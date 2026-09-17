#!/usr/bin/env python3
"""liquipedia_scraper.py — Data historis MPL ID Season 10-17 dari Liquipedia.

KENAPA MODULNYA TERPISAH DARI mpl_scraper.py?
Karena aturan mainnya beda total. id-mpl.com itu situs biasa yang boleh diambil
HTML-nya dengan sopan. Liquipedia TIDAK:

  1. Scraping HTML langsung DILARANG. Kutipan dari Terms of Use mereka:
     "Automated access to non-API endpoints (ie, generated HTML pages) is not
     permitted." Jadi URL seperti
     https://liquipedia.net/mobilelegends/MPL/Indonesia/Season_17/Regular_Season
     TIDAK BOLEH di-requests.get(). Kita harus lewat MediaWiki API.

  2. action=parse dibatasi 1 request per 30 DETIK (endpoint lain 1 per 2 detik),
     karena parse itu mahal di sisi mereka. 8 musim = ~4 menit. Sekali saja.

  3. Wajib User-Agent yang menyebut identitas + kontak. UA generik seperti
     "python-requests/2.x" kemungkinan besar diblokir.

  4. Wajib cache. "Do not issue repeated requests which return the same data."
     Data musim lampau tidak pernah berubah, jadi cache-nya permanen.

  5. Konten berlisensi CC-BY-SA 3.0 — wajib mencantumkan atribusi ke Liquipedia
     di mana pun datanya dipakai.

Cara pakai:
    python liquipedia_scraper.py --inspect 17      # petakan struktur 1 musim
    python liquipedia_scraper.py --scrape          # ambil Season 10-17
    python liquipedia_scraper.py --scrape --only 15,16
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

API = "https://liquipedia.net/mobilelegends/api.php"

MUSIM = list(range(10, 19))   # S18 sudah ada halamannya di Liquipedia
JUDUL = "MPL/Indonesia/Season_{n}/Regular_Season"
JUDUL_PLAYOFF = "MPL/Indonesia/Season_{n}/Playoffs"
JUDUL_STATS = "MPL/Indonesia/Season_{n}/Statistics"
JUDUL_MUSIM = "MPL/Indonesia/Season_{n}"

CACHE_DIR = Path("cache/liquipedia")
OUT_DIR = Path("data")

# GANTI kontak di bawah dengan email/URL kamu sendiri sebelum dipakai serius.
# Liquipedia mewajibkan UA yang bisa dihubungi; UA generik akan diblokir.
KONTAK = "ganti-dengan-email-kamu@example.com"
USER_AGENT = f"MPLResearchBot/1.0 (proyek belajar web scraping; {KONTAK})"

JEDA_PARSE = 30.0   # detik — batas keras Liquipedia untuk action=parse
TIMEOUT = 30

_sesi: requests.Session | None = None
_terakhir: float = 0.0


def sesi() -> requests.Session:
    """Satu Session dipakai ulang — Liquipedia minta koneksi HTTP di-reuse."""
    global _sesi
    if _sesi is None:
        _sesi = requests.Session()
        _sesi.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",   # diminta eksplisit oleh ToS mereka
        })
    return _sesi


def ambil_parse(judul: str, pakai_cache: bool = True) -> str:
    """Ambil HTML hasil render satu halaman lewat action=parse.

    Ini SATU-SATUNYA pintu yang boleh dipakai. Hasilnya di-cache permanen:
    halaman musim yang sudah selesai tidak akan berubah lagi, jadi tidak ada
    alasan memukul API mereka dua kali untuk data yang sama.
    """
    global _terakhir

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    berkas = CACHE_DIR / (judul.replace("/", "_") + ".html")

    if pakai_cache and berkas.exists():
        print(f"  [cache] {judul}")
        return berkas.read_text(encoding="utf-8")

    # Rem laju: pastikan minimal 30 detik sejak parse terakhir.
    tunggu = JEDA_PARSE - (time.monotonic() - _terakhir)
    if _terakhir and tunggu > 0:
        print(f"  [jeda ] menunggu {tunggu:.0f}s (batas 1 parse / {JEDA_PARSE:.0f}s)")
        time.sleep(tunggu)

    print(f"  [api  ] parse {judul}")
    resp = sesi().get(API, params={
        "action": "parse",
        "page": judul,
        "prop": "text",
        "format": "json",
        "formatversion": "2",
    }, timeout=TIMEOUT)
    _terakhir = time.monotonic()
    resp.raise_for_status()

    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"API menolak {judul}: {data['error'].get('info')}")

    html = data["parse"]["text"]
    berkas.write_text(html, encoding="utf-8")
    return html


# ---------------------------------------------------------------------------
# MODE INSPECT
# ---------------------------------------------------------------------------

def teks(el: Tag) -> str:
    return " ".join(el.get_text(" ", strip=True).split())


def inspect(n: int, pakai_cache: bool = True) -> None:
    html = ambil_parse(JUDUL.format(n=n), pakai_cache)
    soup = BeautifulSoup(html, "lxml")

    print(f"\n=== Season {n} ({len(html):,} karakter) ===")

    tabel = soup.find_all("table")
    print(f"\n-- {len(tabel)} <table> --")
    for i, t in enumerate(tabel[:12]):
        kelas = " ".join(t.get("class") or []) or "(tanpa class)"
        baris = t.find_all("tr")
        kepala = [teks(c) for c in baris[0].find_all(["th", "td"])] if baris else []
        print(f"  [{i}] class={kelas!r}  {len(baris)} baris")
        print(f"      kolom: {kepala[:10]}")

    # Liquipedia membungkus hasil pertandingan dalam struktur khusus, bukan
    # <table> biasa. Petakan juga kelas-kelas yang sering dipakai.
    print("\n-- pola kelas yang relevan --")
    for pola in ("brkts-matchlist", "matchlist", "bracket", "match-row",
                 "wikitable", "prizepooltable", "standings", "toggle-area"):
        n_el = len(soup.select(f'[class*="{pola}"]'))
        if n_el:
            print(f"  {pola:22} {n_el} elemen")


# ---------------------------------------------------------------------------
# PARSER
# ---------------------------------------------------------------------------

def _skor(t: str) -> tuple[int, bool]:
    """Ubah teks skor jadi (angka, sudah_main).

    Liquipedia tidak selalu menulis angka. Yang mungkin muncul:
        "2"   -> laga normal
        "W"   -> menang walkover (lawan mundur)
        "FF"  -> forfeit / kalah WO
        "-"   -> belum dimainkan
    Walkover diperlakukan sebagai 2-0, karena itulah yang dicatat di klasemen.
    """
    t = (t or "").strip().upper()
    if t.isdigit():
        return int(t), True
    if t == "W":
        return 2, True
    if t in ("FF", "L", "0"):
        return 0, True
    return 0, False


def parse_matches(soup: BeautifulSoup, season: int) -> list[dict]:
    """Ambil semua pertandingan satu musim dari blok .brkts-matchlist.

    Tiap pekan adalah satu .brkts-matchlist. Di dalamnya, judul hari
    (.brkts-matchlist-header = "Day 1") dan kartu laga (.brkts-matchlist-match)
    adalah SAUDARA, bukan induk-anak — sama persis polanya seperti tanggal di
    id-mpl.com. Jadi "hari" harus diingat sambil menyusuri dari atas ke bawah.
    """
    records = []

    for blok_pekan in soup.select(".brkts-matchlist"):
        judul = blok_pekan.select_one(".brkts-matchlist-title b")
        m = re.search(r"(\d+)", teks(judul) if judul else "")
        if not m:
            continue
        week = int(m.group(1))
        hari = None

        area = blok_pekan.select_one(".brkts-matchlist-collapse-area") or blok_pekan
        for anak in area.find_all("div", recursive=False):
            kelas = anak.get("class") or []

            if "brkts-matchlist-header" in kelas:
                d = re.search(r"(\d+)", teks(anak))
                hari = int(d.group(1)) if d else None
                continue

            if "brkts-matchlist-match" not in kelas:
                continue

            lawan = anak.select(".brkts-matchlist-opponent")
            skor = anak.select(".brkts-matchlist-score")
            if len(lawan) < 2 or len(skor) < 2:
                continue

            def nama(sel: Tag) -> tuple[str, str]:
                dyn = sel.select_one(".team-name-dynamic")
                panjang = (sel.get("aria-label") or "").strip()
                pendek = (dyn.get("data-team-shortname") if dyn else "") or panjang
                return panjang or pendek, pendek.upper()

            t1, k1 = nama(lawan[0])
            t2, k2 = nama(lawan[1])
            s1, main1 = _skor(teks(skor[0]))
            s2, main2 = _skor(teks(skor[1]))

            # Timestamp Unix ada di popup detail laga — jauh lebih presisi
            # daripada menebak dari teks "March 27, 2026".
            jam_obj = anak.select_one("[data-timestamp]")
            ts = jam_obj.get("data-timestamp") if jam_obj else None
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                ts = None

            # MVP laga ada di footer popup detail. Timnya TIDAK ditulis di situ,
            # tapi bisa disimpulkan: MVP selalu dari tim pemenang. Itu bukan
            # asumsi kosong — diuji ke 544 laga di 8 musim, dan ke-257 pemain
            # berbeda semuanya memetakan ke tepat satu tim, nol konflik.
            el_mvp = anak.select_one(".brkts-popup-mvp")
            mvp = mvp_link = None
            if el_mvp:
                a = el_mvp.find("a")
                mvp = teks(a) if a else teks(el_mvp).replace("MVP:", "").strip()
                # Tautan halaman pemain = kunci identitas yang stabil. Nama
                # tampilannya berbeda antar halaman ("VYN" vs "Vyn", "HAIZZ" vs
                # "Haiz", "Super KENN" vs "Kenn"), jadi menyamakan lewat nama
                # akan salah pasang. Href-nya konsisten.
                mvp_link = a.get("href") if a else None

            selesai = main1 and main2
            mvp_kode = None
            if mvp and selesai and s1 != s2:
                mvp_kode = k1 if s1 > s2 else k2

            records.append({
                "season": season,
                "week": week,
                "day": hari,
                "timestamp": ts,
                "tim1": t1, "kode1": k1,
                "skor1": s1, "skor2": s2,
                "kode2": k2, "tim2": t2,
                "status": "selesai" if selesai else "belum main",
                "mvp": mvp,
                "mvp_link": mvp_link,
                "mvp_kode": mvp_kode,
            })

    return records


def validasi_crosstable(soup: BeautifulSoup, laga: list[dict]) -> tuple[int, int]:
    """Cek hasil parsing terhadap crosstable buatan Liquipedia sendiri.

    Halaman musim memuat dua penyajian data yang sama: daftar pertandingan
    per pekan, dan tabel silang agregat. Keduanya dirender dari sumber berbeda
    di wiki-nya. Kalau parser kita membaca daftar laga dengan benar, agregat
    yang kita hitung HARUS sama persis dengan isi crosstable.

    Ini pemeriksaan gratis yang sangat berharga: kalau suatu musim strukturnya
    beda dan parser diam-diam salah baca, angkanya langsung ketahuan meleset.
    """
    ct = soup.select_one("table.crosstable")
    if ct is None:
        return 0, 0

    h2h: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for m in laga:
        h2h.setdefault((m["tim1"], m["tim2"]), []).append((m["skor1"], m["skor2"]))
        h2h.setdefault((m["tim2"], m["tim1"]), []).append((m["skor2"], m["skor1"]))

    baris = ct.find_all("tr")
    urut = []
    for tr in baris:
        sel = tr.find_all(["td", "th"])
        span = sel[0].select_one("[data-highlighting-class]") if sel else None
        if span:
            urut.append(span["data-highlighting-class"])

    n = len(urut)
    cocok = beda = 0
    for i, tr in enumerate(baris[:n]):
        sel = tr.find_all(["td", "th"])
        for j, c in enumerate(sel[-n:]):
            if i == j:
                continue
            m = re.match(r"^(\d+)-(\d+)\s", teks(c))
            if not m:
                continue
            lp = (int(m.group(1)), int(m.group(2)))
            pertemuan = h2h.get((urut[i], urut[j]), [])
            kita = (sum(a for a, _ in pertemuan), sum(b for _, b in pertemuan))
            if lp == kita:
                cocok += 1
            else:
                beda += 1
                print(f"    [BEDA ] {urut[i]} vs {urut[j]}: "
                      f"liquipedia {lp[0]}-{lp[1]} != kita {kita[0]}-{kita[1]}",
                      file=sys.stderr)
    return cocok, beda


def scrape(musim: list[int], pakai_cache: bool = True) -> None:
    import pandas as pd

    semua: list[dict] = []
    ringkasan = []

    for n in musim:
        soup = BeautifulSoup(ambil_parse(JUDUL.format(n=n), pakai_cache), "lxml")
        laga = parse_matches(soup, n)
        cocok, beda = validasi_crosstable(soup, laga)

        tim = sorted({m["kode1"] for m in laga} | {m["kode2"] for m in laga})
        status = "OK" if beda == 0 and cocok else ("TANPA CROSSTABLE" if not cocok else "BEDA")
        print(f"  [S{n:>2}  ] {len(laga):>3} laga | {len(tim)} tim | "
              f"validasi {cocok} sel {status}")

        semua.extend(laga)
        ringkasan.append({"season": n, "laga": len(laga), "tim": len(tim),
                          "sel_divalidasi": cocok, "sel_beda": beda})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(semua)
    df.to_csv(OUT_DIR / "history_matches.csv", index=False, encoding="utf-8-sig")
    print(f"\n  [simpan] {OUT_DIR / 'history_matches.csv'}  "
          f"({len(df)} baris x {len(df.columns)} kolom)")

    meta = {
        "sumber": "https://liquipedia.net/mobilelegends/",
        "lisensi": "CC-BY-SA 3.0",
        "catatan": "Wajib mencantumkan atribusi ke Liquipedia saat data dipakai.",
        "musim": ringkasan,
    }
    (OUT_DIR / "_liquipedia_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  [simpan] {OUT_DIR / '_liquipedia_meta.json'}")


# ---------------------------------------------------------------------------
# PARSER PLAYOFF
# ---------------------------------------------------------------------------

def _tim_sel(cell: Tag) -> tuple[str, str]:
    """Ambil (kode, nama) dari satu sel tim di tabel hasil playoff.

    Sel itu memuat dua <span class="name">: nama panjang untuk layar lebar dan
    kode pendek untuk layar sempit. Contoh: ["Dewa United", "DEWA"].
    """
    nama_sel = [teks(s) for s in cell.select("span.name")]
    tautan = cell.find("a")
    panjang = (tautan.get("title") if tautan else None) or \
              (nama_sel[0] if nama_sel else "")
    pendek = nama_sel[-1] if len(nama_sel) > 1 else panjang
    return pendek.upper(), panjang


def tabel_hasil(soup: BeautifulSoup) -> Tag | None:
    """Cari tabel hasil lewat NAMA KOLOM, bukan indeks.

    Pelajaran yang sudah dua kali menggigit di proyek ini: halaman Liquipedia
    tidak konsisten soal urutan tabel. Dicari lewat header, aman.
    """
    for t in soup.find_all("table"):
        baris = t.find_all("tr")
        if not baris:
            continue
        kolom = {teks(c).lower() for c in baris[0].find_all(["th", "td"])}
        if {"date", "round", "score"} <= kolom:
            return t
    return None


def parse_playoffs(soup: BeautifulSoup, season: int) -> list[dict]:
    """Semua laga playoff satu musim, dari tabel hasil.

    Dipilih tabel hasil, bukan bracket, karena tabel memuat nama ronde
    ("Upper Bracket Final", "Grand Final") dan timestamp presisi dalam satu
    baris. Bracket-nya tetap dipakai — sebagai pembanding di validasi.
    """
    t = tabel_hasil(soup)
    if t is None:
        return []

    records = []
    for tr in t.find_all("tr")[1:]:
        sel = tr.find_all("td")
        if len(sel) < 5:
            continue

        jam_obj = sel[0].select_one("[data-timestamp]")
        try:
            ts = int(jam_obj["data-timestamp"])
        except (TypeError, KeyError, ValueError):
            ts = None

        ronde = teks(sel[1])
        k1, n1 = _tim_sel(sel[2])
        k2, n2 = _tim_sel(sel[4])

        # Skor ada di <div> pertama sel, bentuknya "0:3" (yang menang di <b>).
        div = sel[3].find("div")
        angka = re.findall(r"\d+", teks(div) if div else teks(sel[3]))
        if len(angka) < 2:
            continue
        s1, s2 = int(angka[0]), int(angka[1])

        abbr = sel[3].find("abbr")
        format_laga = teks(abbr) if abbr else None

        records.append({
            "season": season,
            "timestamp": ts,
            "ronde": ronde,
            "tim1": n1, "kode1": k1,
            "skor1": s1, "skor2": s2,
            "kode2": k2, "tim2": n2,
            "format": format_laga,
            "pemenang": k1 if s1 > s2 else (k2 if s2 > s1 else None),
        })

    return records


def validasi_bracket(soup: BeautifulSoup, laga: list[dict]) -> tuple[int, int]:
    """Cocokkan hasil dari tabel dengan bracket di halaman yang sama.

    Sama semangatnya dengan validasi crosstable di regular season: dua
    penyajian berbeda atas data yang sama harus sepakat. Dicocokkan sebagai
    multiset pasangan (kode, skor) supaya tidak bergantung urutan.
    """
    dari_bracket = []
    for m in soup.select(".brkts-match"):
        entri = m.select(".brkts-opponent-entry")
        if len(entri) < 2:
            continue
        pasang = []
        for ent in entri[:2]:
            dyn = ent.select_one("[data-team-shortname]")
            skor = ent.select_one(".brkts-opponent-score-inner")
            angka = re.findall(r"\d+", teks(skor) if skor else "")
            pasang.append(((dyn.get("data-team-shortname") or "").upper(),
                           int(angka[0]) if angka else None))
        if all(k and s is not None for k, s in pasang):
            dari_bracket.append(tuple(sorted(pasang)))

    dari_tabel = [tuple(sorted(((m["kode1"], m["skor1"]),
                                (m["kode2"], m["skor2"])))) for m in laga]

    sisa = list(dari_bracket)
    cocok = 0
    for item in dari_tabel:
        if item in sisa:
            sisa.remove(item)
            cocok += 1
    return cocok, len(dari_tabel) - cocok


def scrape_playoffs(musim: list[int], pakai_cache: bool = True) -> None:
    import pandas as pd

    semua: list[dict] = []
    for n in musim:
        soup = BeautifulSoup(ambil_parse(JUDUL_PLAYOFF.format(n=n), pakai_cache), "lxml")
        laga = parse_playoffs(soup, n)
        cocok, beda = validasi_bracket(soup, laga)

        final = [m for m in laga if "grand final" in m["ronde"].lower()]
        juara = final[-1]["pemenang"] if final else None

        status = "OK" if beda == 0 and cocok else ("TANPA BRACKET" if not cocok else "BEDA")
        print(f"  [S{n:>2}  ] {len(laga):>2} laga playoff | juara: {juara or '?':6} "
              f"| bracket {cocok} cocok {status}")
        semua.extend(laga)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(semua)
    df.to_csv(OUT_DIR / "playoff_matches.csv", index=False, encoding="utf-8-sig")
    print(f"\n  [simpan] {OUT_DIR / 'playoff_matches.csv'}  "
          f"({len(df)} baris x {len(df.columns)} kolom)")


# ---------------------------------------------------------------------------
# PARSER MVP STANDING
# ---------------------------------------------------------------------------

def tabel_mvp(soup: BeautifulSoup) -> Tag | None:
    """Cari tabel 'Regular Season MVP Standing' lewat kolomnya."""
    for t in soup.find_all("table"):
        baris = t.find_all("tr")
        if not baris:
            continue
        kolom = {teks(c).lower() for c in baris[0].find_all(["th", "td"])}
        if {"player", "total points"} <= kolom:
            return t
    return None


def parse_mvp_standing(soup: BeautifulSoup, season: int) -> list[dict]:
    """Tabel MVP resmi: poin per pekan, dipecah Match MVP (M) dan Weekly MVP (W).

    Bentuk tabelnya dua lapis header:
        No. | Player | Week 1 (colspan 2) | Week 2 (colspan 2) | ... | Total
                     |   M   |   W        |   M   |   W        |
    M = poin Match MVP (10 per laga), W = bonus Weekly MVP (5).

    Sel pemain memuat logo timnya, jadi atribusi pemain->tim datang langsung
    dari Liquipedia — tidak perlu disimpulkan.
    """
    t = tabel_mvp(soup)
    if t is None:
        return []

    baris = t.find_all("tr")
    # Baris header pertama: label pekan (yang ber-colspan 2).
    pekan = [teks(c) for c in baris[0].find_all(["th", "td"])
             if (c.get("colspan") or "") == "2"]

    records = []
    for tr in baris[2:]:
        sel = tr.find_all(["th", "td"])
        if len(sel) < 4:
            continue

        peringkat = teks(sel[0]).rstrip(".")
        sel_pemain = sel[1]
        a_pemain = [a for a in sel_pemain.find_all("a")
                    if teks(a) and not a.find("img")]
        nama = teks(a_pemain[-1]) if a_pemain else teks(sel_pemain)
        link = a_pemain[-1].get("href") if a_pemain else None
        a_tim = sel_pemain.find("a")
        tim = (a_tim.get("title") if a_tim else None)

        nilai = [teks(c) for c in sel[2:-1]]
        total = teks(sel[-1])

        def angka(x):
            return int(x) if x.strip().isdigit() else 0

        # Pasangkan (M, W) per pekan sesuai urutan header.
        for i, label in enumerate(pekan):
            m = angka(nilai[i * 2]) if i * 2 < len(nilai) else 0
            w = angka(nilai[i * 2 + 1]) if i * 2 + 1 < len(nilai) else 0
            if not (m or w):
                continue
            no_pekan = re.search(r"(\d+)", label)
            records.append({
                "season": season,
                "week": int(no_pekan.group(1)) if no_pekan else None,
                "pemain": nama, "link": link,
                "tim": tim,
                "poin_match": m,
                "poin_weekly": w,
            })

        records.append({
            "season": season, "week": None, "pemain": nama, "link": link,
            "tim": tim,
            "poin_match": None, "poin_weekly": None,
            "peringkat": int(peringkat) if peringkat.isdigit() else None,
            "total": angka(total),
        })

    return records


def scrape_mvp(musim: list[int], pakai_cache: bool = True) -> None:
    import pandas as pd

    semua: list[dict] = []
    tanpa_tabel = []

    for n in musim:
        soup = BeautifulSoup(ambil_parse(JUDUL_STATS.format(n=n), pakai_cache), "lxml")
        rec = parse_mvp_standing(soup, n)
        if not rec:
            tanpa_tabel.append(n)
            print(f"  [S{n:>2}  ] tidak ada tabel MVP standing di halaman Statistics")
            continue

        total = [r for r in rec if r.get("total") is not None]
        juara = min(total, key=lambda r: r.get("peringkat") or 99) if total else None
        print(f"  [S{n:>2}  ] {len(total)} pemain | season MVP: "
              f"{juara['pemain'] if juara else '?'} ({juara['tim'] if juara else '?'}, "
              f"{juara['total'] if juara else '?'} poin)")
        semua.extend(rec)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(semua)
    df.to_csv(OUT_DIR / "mvp_standing.csv", index=False, encoding="utf-8-sig")
    print(f"\n  [simpan] {OUT_DIR / 'mvp_standing.csv'}  ({len(df)} baris)")
    if tanpa_tabel:
        print(f"  [catat ] musim tanpa tabel MVP: {tanpa_tabel} "
              f"(poin Match MVP tetap bisa dihitung dari data laga)")


# ---------------------------------------------------------------------------
# PARSER AWARDS (halaman utama musim)
# ---------------------------------------------------------------------------

def _peserta(cell: Tag) -> list[dict]:
    """Ambil daftar peserta dari satu sel Participant.

    Sebagian penghargaan berisi lebih dari satu orang (mis. "First Team
    Winner" = 5 pemain), jadi selalu dikembalikan sebagai daftar.
    Tim diambil dari tautan yang membungkus gambar logo.
    """
    hasil = []
    for a in cell.find_all("a", href=True):
        if a.find("img"):
            continue                      # itu tautan logo tim, bukan pemain
        nama = teks(a)
        if not nama:
            continue
        # Tim = tautan bergambar terdekat di baris/blok yang sama.
        tim = None
        blok = a.find_parent(["span", "div", "td"])
        while blok is not None and tim is None:
            img_a = blok.find("a", href=True)
            for kand in blok.find_all("a", href=True):
                if kand.find("img"):
                    tim = kand.get("title")
                    break
            blok = blok.parent if tim is None and blok.name != "td" else None
        hasil.append({"pemain": nama, "link": a.get("href"), "tim": tim})
    if not hasil:
        t = teks(cell)
        if t:
            hasil.append({"pemain": t, "link": None, "tim": None})
    return hasil


def parse_awards(soup: BeautifulSoup, season: int) -> list[dict]:
    """Semua tabel berbentuk `Award | Participant` di halaman musim.

    Halaman memuat beberapa tabel dengan bentuk sama: penghargaan utama
    (Finals MVP, Regular Season MVP, ...), Weekly MVP, Weekly Best Rookie,
    dan Team of The Week. Dibaca semua, lalu dibedakan lewat label
    penghargaannya — bukan lewat urutan tabel.
    """
    records = []
    for t in soup.find_all("table"):
        baris = t.find_all("tr")
        if not baris:
            continue
        kolom = [teks(c).lower() for c in baris[0].find_all(["th", "td"])]
        if not (len(kolom) >= 2 and kolom[0] == "award"
                and kolom[1] == "participant"):
            continue

        hadiah_idx = next((i for i, k in enumerate(kolom) if "usd" in k), None)
        label = None
        for tr in baris[1:]:
            sel = tr.find_all(["th", "td"])
            if not sel:
                continue
            # Sel penghargaan bisa ber-rowspan (mis. First Team Winner);
            # baris lanjutannya hanya berisi peserta.
            if len(sel) >= 2:
                # Buang penanda catatan kaki wiki, mis. "Week 5 MVP [ 1 ]".
                label = re.sub(r"\s*\[\s*\d+\s*\]\s*$", "", teks(sel[0])) or label
                sel_peserta = sel[1]
                hadiah = (teks(sel[hadiah_idx])
                          if hadiah_idx is not None and hadiah_idx < len(sel) else None)
            else:
                sel_peserta = sel[0]
                hadiah = None

            for orang in _peserta(sel_peserta):
                # Musim yang masih berjalan memuat baris placeholder "TBD"
                # untuk penghargaan yang belum diputuskan. Itu bukan data,
                # jadi tidak ikut disimpan.
                if (orang.get("pemain") or "").strip().upper() == "TBD":
                    continue
                records.append({
                    "season": season,
                    "award": label,
                    **orang,
                    "hadiah": hadiah if hadiah and hadiah != "-" else None,
                })
    return records


def scrape_awards(musim: list[int], pakai_cache: bool = True) -> None:
    import pandas as pd

    semua: list[dict] = []
    for n in musim:
        soup = BeautifulSoup(ambil_parse(JUDUL_MUSIM.format(n=n), pakai_cache), "lxml")
        rec = parse_awards(soup, n)

        mingguan = [r for r in rec if re.match(r"Week \d+ MVP", r["award"] or "")]
        rs = next((r for r in rec
                   if (r["award"] or "").lower() == "regular season mvp"), None)
        fin = next((r for r in rec
                    if (r["award"] or "").lower() == "finals mvp"), None)

        print(f"  [S{n:>2}  ] {len(rec):>3} baris award | Weekly MVP: {len(mingguan)} pekan "
              f"| RS MVP: {rs['pemain'] if rs else '?'} "
              f"| Finals MVP: {fin['pemain'] if fin else '?'}")
        semua.extend(rec)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(semua)
    df.to_csv(OUT_DIR / "awards.csv", index=False, encoding="utf-8-sig")
    print(f"\n  [simpan] {OUT_DIR / 'awards.csv'}  ({len(df)} baris)")


# ---------------------------------------------------------------------------
# PARSER ROSTER
# ---------------------------------------------------------------------------

# Judul role di Liquipedia -> nama pendek yang dipakai di proyek ini.
ROLE = {
    "EXP Lane": "EXP",
    "Jungler": "Jungle",
    "Gold Lane": "Gold",
    "Middle": "Mid",
    "Roamer": "Roam",
}


def parse_roster(soup: BeautifulSoup, season: int) -> list[dict]:
    """Roster tiap tim: pemain inti, cadangan, mantan, dan staf.

    Tiap tim satu `.team-participant-card`. Di dalamnya ada pil pengalih
    (Main / Subs / Former / Staff) dan sejumlah blok isi yang urutannya
    SEJAJAR dengan pil-pil itu — jadi keduanya dipasangkan berdasarkan indeks,
    bukan nama kelas, supaya tetap jalan kalau suatu musim tidak punya
    (misalnya) bagian "Former".

    Role dibaca dari judul gambar ikonnya ("EXP Lane", "Jungler", "Middle",
    "Gold Lane", "Roamer"), bukan dari posisi baris — urutan pemain di kartu
    tidak selalu sama antar tim.
    """
    records = []

    for kartu in soup.select(".team-participant-card"):
        head = kartu.select_one(".team-participant-card__header")
        tautan = head.find("a", title=True) if head else None
        tim = tautan.get("title") if tautan else None
        if not tim:
            continue

        pil = [teks(b) for b in kartu.select(".toggle-area-button")]
        wadah = kartu.select_one(".content-switch-content-container")
        if wadah is None:
            continue

        for i, bagian in enumerate(wadah.find_all("div", recursive=False)):
            grup = pil[i] if i < len(pil) else f"bagian-{i + 1}"

            for m in bagian.select(".team-participant-card__member"):
                ikon = m.select_one(".team-participant-card__member-role-left img")
                role_asli = ikon.get("title") if ikon else None

                nama_el = m.select_one(".team-participant-card__member-name .name a") \
                    or m.select_one(".name a") or m.select_one(".name")
                nama = teks(nama_el)
                if not nama:
                    continue

                bendera = m.select_one(".flag img")
                kanan = m.select_one(".team-participant-card__member-role-right")
                catatan = teks(kanan) if kanan else None

                records.append({
                    "season": season,
                    "tim": tim,
                    "grup": grup,
                    "role": ROLE.get(role_asli, role_asli),
                    "role_asli": role_asli,
                    "pemain": nama,
                    "link": nama_el.get("href") if nama_el.name == "a" else None,
                    "negara": bendera.get("title") if bendera else None,
                    "catatan": catatan or None,
                })

    return records


def scrape_roster(musim: list[int], pakai_cache: bool = True) -> None:
    import pandas as pd

    semua: list[dict] = []
    for n in musim:
        soup = BeautifulSoup(ambil_parse(JUDUL_MUSIM.format(n=n), pakai_cache), "lxml")
        rec = parse_roster(soup, n)
        tim = len({r["tim"] for r in rec})
        inti = [r for r in rec if r["grup"].lower() == "main"]
        grup = sorted({r["grup"] for r in rec})
        print(f"  [S{n:>2}  ] {len(rec):>3} baris | {tim} tim | {len(inti)} pemain inti "
              f"| bagian: {', '.join(grup)}")
        semua.extend(rec)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(semua)
    df.to_csv(OUT_DIR / "rosters.csv", index=False, encoding="utf-8-sig")
    print(f"\n  [simpan] {OUT_DIR / 'rosters.csv'}  ({len(df)} baris x {len(df.columns)} kolom)")


# ---------------------------------------------------------------------------
# PARSER PICK & BAN
# ---------------------------------------------------------------------------

def parse_pickban(popup: Tag, kode1: str, kode2: str) -> list[dict]:
    """Hero pick & ban tiap game, dari popup detail laga.

    Bentuknya dua blok sejajar di kiri dan kanan:

        .brkts-popup-body-grid-row-detail  -> [pick tim kiri] [durasi] [pick tim kanan]
        .brkts-popup-veto-row              -> [ban tim kiri] "Game N" [ban tim kanan]

    PENTING soal sisi: kelas `brkts-popup-side-color--blue/--red` BERTUKAR
    antar game (tim yang di blue di game 1 bisa jadi red di game 3), tapi
    POSISI kiri/kanan tetap mengikuti tim yang sama sepanjang laga. Jadi yang
    dipakai untuk menentukan pemilik hero adalah posisinya, bukan warnanya.
    Ini diverifikasi terhadap tabel "Played By Teams" di halaman Statistics.
    """
    records = []

    picks = popup.select(".brkts-popup-body-grid-row-detail")
    bans = popup.select(".brkts-popup-veto-row")

    def sisi_hero(baris: Tag) -> tuple[list[str], list[str]]:
        """Kembalikan (hero kiri, hero kanan) dari satu baris."""
        blok_isi = []
        for ch in baris.find_all("div", recursive=False):
            hero = [a.get("title") for a in ch.find_all("a", title=True)
                    if a.get("title")]
            blok_isi.append(hero)
        berisi = [b for b in blok_isi if b]
        if len(berisi) < 2:
            return (berisi[0] if berisi else []), []
        return berisi[0], berisi[-1]

    for i in range(max(len(picks), len(bans))):
        game = i + 1

        kiri_p, kanan_p = sisi_hero(picks[i]) if i < len(picks) else ([], [])
        kiri_b, kanan_b = sisi_hero(bans[i]) if i < len(bans) else ([], [])

        durasi = None
        if i < len(picks):
            m = re.search(r"\b(\d{1,2}:\d{2})\b", teks(picks[i]))
            durasi = m.group(1) if m else None

        for kode, pk, bn in ((kode1, kiri_p, kiri_b), (kode2, kanan_p, kanan_b)):
            if not pk and not bn:
                continue
            rec = {"game": game, "durasi": durasi, "kode": kode}
            for j in range(5):
                rec[f"pick{j + 1}"] = pk[j] if j < len(pk) else None
                rec[f"ban{j + 1}"] = bn[j] if j < len(bn) else None
            records.append(rec)

    return records


def scrape_pickban(musim: list[int], pakai_cache: bool = True) -> None:
    """Pick & ban untuk regular season DAN playoff, semua musim.

    Kedua halaman memakai popup detail laga dengan struktur yang sama, jadi
    parser-nya dipakai ulang; yang beda hanya cara menemukan kartu laga dan
    kode timnya.
    """
    import pandas as pd

    semua: list[dict] = []

    for n in musim:
        # --- regular season ---
        soup = BeautifulSoup(ambil_parse(JUDUL.format(n=n), pakai_cache), "lxml")
        n_reg = 0
        for blok_pekan in soup.select(".brkts-matchlist"):
            judul = blok_pekan.select_one(".brkts-matchlist-title b")
            mw = re.search(r"(\d+)", teks(judul) if judul else "")
            week = int(mw.group(1)) if mw else None

            for kartu in blok_pekan.select(".brkts-matchlist-match"):
                lawan = kartu.select(".brkts-matchlist-opponent")
                popup = kartu.select_one(".brkts-popup")
                if len(lawan) < 2 or popup is None:
                    continue

                def kode_dari(sel):
                    dyn = sel.select_one(".team-name-dynamic")
                    return (dyn.get("data-team-shortname") or "").upper() if dyn else None

                k1, k2 = kode_dari(lawan[0]), kode_dari(lawan[1])
                ts = kartu.select_one("[data-timestamp]")
                ts = int(ts["data-timestamp"]) if ts else None

                for r in parse_pickban(popup, k1, k2):
                    semua.append({"season": n, "stage": "reguler", "week": week,
                                  "timestamp": ts, "lawan1": k1, "lawan2": k2, **r})
                    n_reg += 1

        # --- playoff ---
        soup_po = BeautifulSoup(ambil_parse(JUDUL_PLAYOFF.format(n=n), pakai_cache), "lxml")
        n_po = 0
        for kartu in soup_po.select(".brkts-match"):
            entri = kartu.select(".brkts-opponent-entry")
            popup = kartu.select_one(".brkts-popup")
            if len(entri) < 2 or popup is None:
                continue
            kode = []
            for ent in entri[:2]:
                dyn = ent.select_one("[data-team-shortname]")
                kode.append((dyn.get("data-team-shortname") or "").upper() if dyn else None)
            ts = popup.select_one("[data-timestamp]")
            ts = int(ts["data-timestamp"]) if ts else None

            for r in parse_pickban(popup, kode[0], kode[1]):
                semua.append({"season": n, "stage": "playoff", "week": None,
                              "timestamp": ts, "lawan1": kode[0], "lawan2": kode[1], **r})
                n_po += 1

        print(f"  [S{n:>2}  ] {n_reg:>4} baris reguler | {n_po:>3} baris playoff")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(semua)
    df.to_csv(OUT_DIR / "picks_bans.csv", index=False, encoding="utf-8-sig")
    print(f"\n  [simpan] {OUT_DIR / 'picks_bans.csv'}  "
          f"({len(df)} baris x {len(df.columns)} kolom)")


# ---------------------------------------------------------------------------
# PARSER STATISTIK HERO
# ---------------------------------------------------------------------------

def tabel_hero(soup: BeautifulSoup) -> Tag | None:
    """Cari tabel statistik hero lewat kolomnya (Picks + Bans di header atas).

    Bukan lewat indeks: S10 dan S11 menaruh tabel MVP standing lebih dulu,
    sehingga tabel hero di sana ada di posisi kedua.
    """
    for t in soup.find_all("table"):
        baris0 = t.find("tr")
        if not baris0:
            continue
        kolom = {teks(c).lower() for c in baris0.find_all(["th", "td"])}
        if {"picks", "bans"} <= kolom:
            return t
    return None


def parse_hero_stats(soup: BeautifulSoup, season: int) -> list[dict]:
    """Statistik tiap hero satu musim: pick, menang, kalah, win rate, ban.

    Susunan kolomnya mengikuti header dua lapis:

        0 no | 1 Hero | 2-6 Picks(sum, W, L, WR, %T)
        7-10 Blue Side | 11-14 Red Side | 15-16 Bans(sum, %T)
        17-18 Picks & Bans(sum, %T)

    Angka W/L di sini datang langsung dari Liquipedia. Menghitungnya sendiri
    tidak mungkin dari data yang kita punya: pick/ban per game sudah ada, tapi
    pemenang TIAP GAME tidak tercatat di popup laga — yang ada cuma skor laga.
    """
    t = tabel_hero(soup)
    if t is None:
        return []

    def angka(x, pecahan=False):
        x = (x or "").strip().replace("%", "")
        if not x or x == "-":
            return None
        try:
            return float(x) if pecahan else int(x)
        except ValueError:
            return None

    records = []
    for tr in t.find_all("tr"):
        sel = tr.find_all(["th", "td"])
        if len(sel) < 19 or not teks(sel[0]).isdigit():
            continue
        records.append({
            "season": season,
            "hero": teks(sel[1]),
            "picks": angka(teks(sel[2])),
            "menang": angka(teks(sel[3])),
            "kalah": angka(teks(sel[4])),
            "win_rate": angka(teks(sel[5]), pecahan=True),
            "bans": angka(teks(sel[15])),
            "pick_ban": angka(teks(sel[17])),
        })
    return records


def scrape_hero_stats(musim: list[int], pakai_cache: bool = True) -> None:
    import pandas as pd

    semua: list[dict] = []
    for n in musim:
        soup = BeautifulSoup(ambil_parse(JUDUL_STATS.format(n=n), pakai_cache), "lxml")
        rec = parse_hero_stats(soup, n)
        dipakai = [r for r in rec if (r["picks"] or 0) > 0]
        print(f"  [S{n:>2}  ] {len(rec):>3} hero terdaftar | {len(dipakai)} pernah dipick")
        semua.extend(rec)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(semua)
    df.to_csv(OUT_DIR / "hero_season_stats.csv", index=False, encoding="utf-8-sig")
    print(f"\n  [simpan] {OUT_DIR / 'hero_season_stats.csv'}  ({len(df)} baris)")


def parse_hero_relasi(soup: BeautifulSoup, season: int) -> list[dict]:
    """Tabel "Played With" dan "Played Against" tiap hero.

    Keduanya BERSARANG di dalam baris hero pada tabel statistik, bukan berdiri
    sendiri di halaman. Diambil dari dalam barisnya supaya pasti terikat ke
    hero yang benar — memasangkannya berdasarkan urutan tabel akan meleset
    begitu ada hero yang tidak punya sub-tabel.

    Artinya berbeda, dan itu penting waktu memvalidasi:
      rekan  — dua hero SATU TIM, jadi menang/kalahnya sama persis
      lawan  — dua hero BERHADAPAN, jadi menang/kalahnya berkebalikan

    W/L selalu dari sudut pandang hero pemilik baris. Liquipedia hanya
    menampilkan LIMA teratas per kategori, jadi ini bukan matriks lengkap.
    """
    t = tabel_hero(soup)
    if t is None:
        return []

    def angka(x, pecahan=False):
        x = (x or "").strip().replace("%", "")
        try:
            return float(x) if pecahan else int(x)
        except ValueError:
            return None

    JENIS = {"played with": "rekan", "played against": "lawan"}

    records = []
    for tr in t.find_all("tr"):
        sel = tr.find_all(["th", "td"])
        if len(sel) < 19 or not teks(sel[0]).isdigit():
            continue
        hero = teks(sel[1])

        for sub in tr.find_all("table"):
            baris0 = sub.find("tr")
            if not baris0:
                continue
            jenis = JENIS.get(teks(baris0).strip().lower())
            if jenis is None:
                continue
            for r in sub.find_all("tr"):
                kol = r.find_all(["th", "td"])
                if len(kol) < 6 or not teks(kol[0]).isdigit():
                    continue
                records.append({
                    "season": season,
                    "hero": hero,
                    "jenis": jenis,
                    "hero_lain": teks(kol[1]),
                    "peringkat": int(teks(kol[0])),
                    "jumlah": angka(teks(kol[2])),
                    "menang": angka(teks(kol[3])),
                    "kalah": angka(teks(kol[4])),
                    "win_rate": angka(teks(kol[5]), pecahan=True),
                })
    return records


def scrape_hero_relasi(musim: list[int], pakai_cache: bool = True) -> None:
    import pandas as pd

    semua: list[dict] = []
    for n in musim:
        soup = BeautifulSoup(ambil_parse(JUDUL_STATS.format(n=n), pakai_cache), "lxml")
        rec = parse_hero_relasi(soup, n)
        n_rekan = sum(1 for r in rec if r["jenis"] == "rekan")
        n_lawan = sum(1 for r in rec if r["jenis"] == "lawan")
        print(f"  [S{n:>2}  ] {n_rekan:>4} pasangan rekan | {n_lawan:>4} pasangan lawan")
        semua.extend(rec)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(semua)
    df.to_csv(OUT_DIR / "hero_relasi.csv", index=False, encoding="utf-8-sig")
    print(f"\n  [simpan] {OUT_DIR / 'hero_relasi.csv'}  ({len(df)} baris)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Scraper Liquipedia MPL ID")
    ap.add_argument("--inspect", type=int, metavar="MUSIM",
                    help="petakan struktur satu musim, mis. --inspect 17")
    ap.add_argument("--scrape", action="store_true",
                    help="ambil regular season -> data/history_matches.csv")
    ap.add_argument("--playoffs", action="store_true",
                    help="ambil playoff -> data/playoff_matches.csv")
    ap.add_argument("--mvp", action="store_true",
                    help="ambil tabel MVP standing -> data/mvp_standing.csv")
    ap.add_argument("--awards", action="store_true",
                    help="ambil awards & weekly MVP -> data/awards.csv")
    ap.add_argument("--roster", action="store_true",
                    help="ambil roster pemain per tim -> data/rosters.csv")
    ap.add_argument("--pickban", action="store_true",
                    help="ambil hero pick & ban tiap game -> data/picks_bans.csv")
    ap.add_argument("--herostats", action="store_true",
                    help="ambil statistik hero per musim -> data/hero_season_stats.csv")
    ap.add_argument("--relasi", action="store_true",
                    help="ambil rekor hero rekan & lawan -> data/hero_relasi.csv")
    ap.add_argument("--only", metavar="DAFTAR",
                    help="batasi musim, mis. --only 15,16")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    if args.inspect:
        inspect(args.inspect, not args.no_cache)
        return 0

    pilih = [int(x) for x in args.only.split(",")] if args.only else MUSIM

    if args.scrape:
        scrape(pilih, not args.no_cache)
        return 0

    if args.playoffs:
        scrape_playoffs(pilih, not args.no_cache)
        return 0

    if args.mvp:
        scrape_mvp(pilih, not args.no_cache)
        return 0

    if args.awards:
        scrape_awards(pilih, not args.no_cache)
        return 0

    if args.roster:
        scrape_roster(pilih, not args.no_cache)
        return 0

    if args.pickban:
        scrape_pickban(pilih, not args.no_cache)
        return 0

    if args.herostats:
        scrape_hero_stats(pilih, not args.no_cache)
        return 0

    if args.relasi:
        scrape_hero_relasi(pilih, not args.no_cache)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
