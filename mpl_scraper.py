#!/usr/bin/env python3
"""
mpl_scraper.py — Scraper data MPL ID (https://id-mpl.com)

Studi kasus web scraping untuk situs yang di-render di server (SSR):
seluruh data sudah ada di HTML mentah, jadi cukup `requests` + `BeautifulSoup`,
tidak perlu Selenium/Playwright.

Cara pakai:
    python mpl_scraper.py --inspect                # lihat tabel apa saja yang ada
    python mpl_scraper.py --scrape                 # ambil semua & simpan ke CSV
    python mpl_scraper.py --scrape --no-cache      # paksa fetch ulang dari server
    python mpl_scraper.py --scrape --out hasil/    # ganti folder output

Dependensi:
    pip install requests beautifulsoup4 lxml pandas
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# KONFIGURASI
# ---------------------------------------------------------------------------

BASE_URL = "https://id-mpl.com"

PAGES = {
    "home": "/",
    "statistics": "/statistics",
    "teams": "/teams",
    "schedule": "/schedule",
}

# Identifikasi diri dengan jujur. Jangan menyamar jadi browser biasa untuk
# menghindari blokir — itu cara yang buruk dan gampang bikin IP kena ban.
HEADERS = {
    "User-Agent": (
        "MPLResearchBot/1.0 (proyek belajar web scraping; "
        "kontak: your-email@example.com)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 20      # detik
MAX_RETRY = 3
DELAY_RANGE = (1.5, 3.0)  # jeda acak antar-request, biar tidak membanjiri server

CACHE_DIR = Path("cache")
OUT_DIR = Path("data")

# Simpanan header Last-Modified per halaman, dipakai untuk GET bersyarat.
CACHE_META = CACHE_DIR / "_http_meta.json"


# ---------------------------------------------------------------------------
# LAPIS 1 — PENGAMBILAN (fetching)
# ---------------------------------------------------------------------------

def fetch(path: str, use_cache: bool = True) -> str:
    """Ambil satu halaman. Hasilnya di-cache ke disk.

    Cache itu wajib waktu belajar/debugging: kamu akan menjalankan parser
    puluhan kali, dan tidak ada alasan memukul server orang puluhan kali
    untuk halaman yang sama.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = path.strip("/").replace("/", "_") or "index"
    cache_file = CACHE_DIR / f"{slug}.html"

    if use_cache and cache_file.exists():
        print(f"  [cache] {path}")
        return cache_file.read_text(encoding="utf-8")

    url = BASE_URL + path
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRY + 1):
        try:
            print(f"  [http ] GET {url} (percobaan {attempt}/{MAX_RETRY})")
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            html = resp.text
            cache_file.write_text(html, encoding="utf-8")
            time.sleep(random.uniform(*DELAY_RANGE))
            return html
        except requests.RequestException as exc:
            last_error = exc
            # Backoff eksponensial: 2s, 4s, 8s.
            wait = 2 ** attempt
            print(f"  [warn ] gagal: {exc} — tunggu {wait}s", file=sys.stderr)
            time.sleep(wait)

    raise RuntimeError(f"Gagal mengambil {url} setelah {MAX_RETRY} percobaan") from last_error


def _baca_meta() -> dict:
    try:
        return json.loads(CACHE_META.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def fetch_revalidate(path: str) -> tuple[str, bool]:
    """GET BERSYARAT — kembalikan (html, berubah).

    Ini yang bikin polling tiap 5 menit tetap sopan. Kita kirim header
    `If-Modified-Since` berisi tanggal versi yang sudah kita punya. Kalau
    halamannya belum berubah, server menjawab `304 Not Modified` dengan
    body KOSONG — nol byte, bukan 2,3 MB. Kita pakai salinan cache-nya.

    Halaman /schedule itu 2,3 MB. Tanpa trik ini, polling seharian di hari
    pertandingan berarti menarik ratusan megabyte percuma dari server orang.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = path.strip("/").replace("/", "_") or "index"
    cache_file = CACHE_DIR / f"{slug}.html"

    meta = _baca_meta()
    headers = dict(HEADERS)
    if cache_file.exists() and meta.get(slug, {}).get("last_modified"):
        headers["If-Modified-Since"] = meta[slug]["last_modified"]

    resp = requests.get(BASE_URL + path, headers=headers, timeout=REQUEST_TIMEOUT)

    if resp.status_code == 304 and cache_file.exists():
        return cache_file.read_text(encoding="utf-8"), False

    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    html = resp.text

    # CADANGAN: /statistics dikirim dinamis (Cache-Control: no-cache, tanpa
    # Last-Modified maupun ETag), jadi selalu balas 200 — kalau hanya berpatokan
    # pada status HTTP, halaman itu akan SELALU dianggap "berubah" dan situsnya
    # dibangun ulang tiap 5 menit tanpa guna. Untungnya isinya byte-stable,
    # jadi sidik jari SHA-256 cukup untuk membedakan benar-berubah vs sama saja.
    sidik = hashlib.sha256(html.encode("utf-8")).hexdigest()
    berubah = meta.get(slug, {}).get("sha256") != sidik

    cache_file.write_text(html, encoding="utf-8")
    meta[slug] = {
        "last_modified": resp.headers.get("Last-Modified", ""),
        "sha256": sidik,
    }
    CACHE_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return html, berubah


# ---------------------------------------------------------------------------
# LAPIS 2 — UTILITAS PARSING
# ---------------------------------------------------------------------------

def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def cell_parts(cell: Tag) -> list[str]:
    """Pecah isi satu sel jadi potongan-potongan teks.

    Trik kuncinya: `get_text(separator="|")`. Di HTML, sel seperti

        <td><span>1</span><img><b>TLID</b><span>TEAM LIQUID ID</span></td>

    kalau diambil dengan get_text() biasa jadi "1TLIDTEAM LIQUID ID" — nempel
    semua. Dengan separator, batas antar-elemen tetap kelihatan:
    ["1", "TLID", "TEAM LIQUID ID"].

    Ini jauh lebih tahan banting daripada mengandalkan nama class CSS, karena
    nama class berubah tiap kali situsnya di-redesign.
    """
    raw = cell.get_text(separator="|", strip=True)
    return [p.strip() for p in raw.split("|") if p.strip()]


def cell_text(cell: Tag) -> str:
    return " ".join(cell_parts(cell))


def to_num(text: str):
    """Ubah teks jadi angka, sadar format Indonesia vs Inggris.

    Situs ini mencampur dua format dalam satu halaman:
        "1,066,148" -> pemisah ribuan gaya Inggris  -> 1066148
        "2,60"      -> pemisah desimal gaya Indonesia -> 2.60
        "84%"       -> persen                        -> 84.0

    Aturan pembeda: koma diikuti tepat 3 digit dan berulang = ribuan;
    koma diikuti 1-2 digit = desimal. Kalau tidak cocok pola apa pun,
    kembalikan teks aslinya (jangan dipaksa jadi angka).
    """
    t = (text or "").strip()
    if not t:
        return None
    if t.endswith("%"):
        inner = t[:-1].strip().replace(",", ".")
        try:
            return float(inner)
        except ValueError:
            return t
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+", t):
        return int(t.replace(",", ""))
    if re.fullmatch(r"-?\d+,\d{1,2}", t):
        return float(t.replace(",", "."))
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return float(t)
    return t


def table_headers(table: Tag) -> list[str]:
    """Ambil nama kolom dari baris header pertama."""
    head = table.find("thead")
    row = head.find("tr") if head else table.find("tr")
    if row is None:
        return []
    cells = row.find_all(["th", "td"])
    return [cell_text(c) for c in cells]


def table_body_rows(table: Tag) -> list[list[Tag]]:
    """Ambil baris isi (tanpa baris header)."""
    body = table.find("tbody")
    rows = body.find_all("tr") if body else table.find_all("tr")[1:]
    out = []
    for tr in rows:
        cells = tr.find_all(["td", "th"])
        if cells:
            out.append(cells)
    return out


def find_table(soup: BeautifulSoup, required: set[str]) -> Tag | None:
    """Cari tabel berdasarkan NAMA KOLOM-nya, bukan urutannya.

    Ini inti dari scraper yang awet. `soup.find_all("table")[3]` akan rusak
    begitu situsnya menambah satu tabel di atas. Mencari lewat header
    ("tabel yang punya kolom Hero, Pick, dan Ban") tetap jalan.
    """
    wanted = {w.lower() for w in required}
    for table in soup.find_all("table"):
        cols = {h.lower() for h in table_headers(table)}
        if wanted.issubset(cols):
            return table
    return None


def table_to_df(table: Tag, numeric_from: int = 1) -> pd.DataFrame:
    """Konversi generik <table> -> DataFrame.

    Kolom mulai dari indeks `numeric_from` dicoba dikonversi ke angka.
    Kolom sebelum itu (biasanya nama tim/pemain/hero) dibiarkan teks.
    """
    headers = table_headers(table)
    records = []
    for cells in table_body_rows(table):
        row = []
        for i, cell in enumerate(cells):
            txt = cell_text(cell)
            row.append(to_num(txt) if i >= numeric_from else txt)
        # Samakan panjang baris dengan panjang header.
        if headers:
            row = (row + [None] * len(headers))[: len(headers)]
        records.append(row)
    return pd.DataFrame(records, columns=headers or None)


# ---------------------------------------------------------------------------
# LAPIS 3 — PARSER SPESIFIK
# ---------------------------------------------------------------------------

def nama_logo(cell: Tag) -> str | None:
    """Nama berkas logo tim di dalam satu sel, tanpa path dan tanpa ukuran.

    Dipakai sebagai KUNCI IDENTITAS tim, bukan nama timnya. Tabel pemain di
    halaman statistik hanya menempelkan logo — tidak ada teks kode tim sama
    sekali — sementara nama pemain dieja beda dengan Liquipedia ("MORENOOO"
    vs "Moreno"). Berkas logonya justru sama persis di seluruh halaman.
    """
    img = cell.find("img") if cell else None
    src = img.get("src") if img else None
    if not src:
        return None
    src = src.split("?url=")[-1]                    # lewati proxy gambar
    stem = re.sub(r"\.(png|jpg|jpeg|webp|svg)$", "", src.rsplit("/", 1)[-1], flags=re.I)
    return re.sub(r"-\d+$", "", stem).lower()       # buang akhiran ukuran "-64"


def split_team_cell(parts: list[str]) -> dict:
    """Pecah sel tim jadi peringkat / kode / nama panjang.

    Contoh potongan: ["1", "TLID", "TEAM LIQUID ID"]
                     ["BTR", "BIGETRON BY VIT"]
    """
    rank, code, name = None, None, None
    rest = list(parts)

    if rest and re.fullmatch(r"\d+", rest[0]):
        rank = int(rest.pop(0))

    if rest:
        code = rest.pop(0)
    if rest:
        name = " ".join(rest)

    return {"rank": rank, "team_code": code, "team_name": name or code}


def parse_standings(soup: BeautifulSoup) -> pd.DataFrame:
    """Klasemen regular season (ada di halaman home)."""
    table = find_table(soup, {"team", "match point"})
    if table is None:
        print("  [skip ] tabel klasemen tidak ditemukan", file=sys.stderr)
        return pd.DataFrame()

    headers = table_headers(table)
    records = []
    for cells in table_body_rows(table):
        rec = split_team_cell(cell_parts(cells[0]))
        for header, cell in zip(headers[1:], cells[1:]):
            text = cell_text(cell)
            key = header.lower().replace(" ", "_").replace("-", "_")
            # Kolom seperti "8 - 1" dipecah jadi menang & kalah.
            m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", text)
            if m:
                rec[f"{key}_win"] = int(m.group(1))
                rec[f"{key}_loss"] = int(m.group(2))
            else:
                rec[key] = to_num(text)
        records.append(rec)

    df = pd.DataFrame(records)
    if "rank" in df.columns:
        df = df.sort_values("rank", na_position="last").reset_index(drop=True)
    return df


def parse_team_stats(soup: BeautifulSoup) -> pd.DataFrame:
    table = find_table(soup, {"team", "kills", "lord", "tortoise"})
    if table is None:
        print("  [skip ] tabel team statistics tidak ditemukan", file=sys.stderr)
        return pd.DataFrame()

    headers = table_headers(table)
    records = []
    for cells in table_body_rows(table):
        rec = split_team_cell(cell_parts(cells[0]))
        rec.pop("rank", None)
        rec["logo"] = nama_logo(cells[0])
        for header, cell in zip(headers[1:], cells[1:]):
            rec[header.lower().replace(" ", "_")] = to_num(cell_text(cell))
        records.append(rec)
    return pd.DataFrame(records)


def parse_player_stats(soup: BeautifulSoup,
                       peta_logo: dict | None = None) -> pd.DataFrame:
    """Statistik pemain.

    Catatan: halaman statistik memuat tabel pemain beberapa kali — satu tabel
    gabungan, lalu satu tabel per tim (untuk tampilan tab). find_table()
    mengembalikan yang pertama, yaitu tabel gabungan. Persis yang kita mau.

    Tim pemain TIDAK ditulis sebagai teks di tabel ini, hanya sebagai logo.
    Karena itu `peta_logo` (berkas logo -> kode tim) dibangun lebih dulu dari
    tabel team statistics, yang memuat keduanya sekaligus. Peta ini ikut
    berubah sendiri kalau situsnya mengganti logo, jadi tidak ada daftar
    kode tim yang perlu dirawat manual di sini.
    """
    table = find_table(soup, {"player", "lanes", "avg kda"})
    if table is None:
        print("  [skip ] tabel player statistics tidak ditemukan", file=sys.stderr)
        return pd.DataFrame()

    peta = peta_logo or {}
    headers = table_headers(table)
    records = []
    for cells in table_body_rows(table):
        parts = cell_parts(cells[0])
        berkas = nama_logo(cells[0])
        rec = {"player": parts[-1] if parts else None,
               "team_code": peta.get(berkas),
               "logo": berkas}
        for header, cell in zip(headers[1:], cells[1:]):
            rec[header.lower().replace(" ", "_")] = to_num(cell_text(cell))
        records.append(rec)

    df = pd.DataFrame(records)
    # Buang duplikat kalau-kalau tabel per-tim ikut terjaring.
    if "player" in df.columns:
        df = df.drop_duplicates(subset=["player"], keep="first").reset_index(drop=True)
    return df


def parse_hero_stats(soup: BeautifulSoup) -> pd.DataFrame:
    table = find_table(soup, {"hero", "pick", "ban", "win rate"})
    if table is None:
        print("  [skip ] tabel hero statistics tidak ditemukan", file=sys.stderr)
        return pd.DataFrame()

    headers = table_headers(table)
    records = []
    for cells in table_body_rows(table):
        parts = cell_parts(cells[0])
        rec = {"hero": parts[-1] if parts else None}
        for header, cell in zip(headers[1:], cells[1:]):
            rec[header.lower().replace(" ", "_")] = to_num(cell_text(cell))
        records.append(rec)
    return pd.DataFrame(records)


TICKET_PATTERNS = [
    # mpl-id-s18-w6-d1-onic-vs-geek
    re.compile(r"mpl-id-s(?P<season>\d+)-w(?P<week>\d+)-d(?P<day>\d+)-"
               r"(?P<home>[a-z0-9]+)-vs-(?P<away>[a-z0-9]+)"),
    # mpl-id-s18-week-9-navi-vs-rrq
    re.compile(r"mpl-id-s(?P<season>\d+)-week-(?P<week>\d+)-"
               r"(?P<home>[a-z0-9]+)-vs-(?P<away>[a-z0-9]+)"),
]


def parse_schedule_from_links(soup: BeautifulSoup) -> pd.DataFrame:
    """Jadwal, dibaca dari URL tiket.

    JUJUR SOAL KETERBATASAN: cara ini hanya menangkap pertandingan yang masih
    punya tombol beli tiket. Laga yang sudah SOLD OUT kehilangan link-nya,
    jadi tidak akan muncul di sini. Untuk jadwal lengkap, tulis parser
    berbasis DOM kartu pertandingan — jalankan `--inspect` dulu untuk melihat
    struktur dan nama class-nya.
    """
    records = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "cognitix" not in href and "mpl-id-s" not in href:
            continue
        for pattern in TICKET_PATTERNS:
            m = pattern.search(href)
            if m:
                d = m.groupdict()
                records.append({
                    "season": int(d["season"]),
                    "week": int(d["week"]),
                    "day": int(d.get("day") or 0) or None,
                    "home": d["home"].upper(),
                    "away": d["away"].upper(),
                    "ticket_url": href,
                })
                break

    df = pd.DataFrame(records)
    if not df.empty:
        df = df.drop_duplicates(subset=["season", "week", "home", "away"])
        df = df.sort_values(["week", "day"], na_position="last").reset_index(drop=True)
    return df


BULAN_ID = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10,
    "november": 11, "desember": 12,
}


def parse_tanggal_id(text: str) -> str | None:
    """Ubah "Jumat, 14 Agustus 2026" -> "2026-08-14".

    Kalau polanya tidak cocok, kembalikan None — jangan menebak.
    """
    m = re.search(r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})", text or "")
    if not m:
        return None
    bulan = BULAN_ID.get(m.group(2).lower())
    if bulan is None:
        return None
    return f"{int(m.group(3)):04d}-{bulan:02d}-{int(m.group(1)):02d}"


def parse_schedule(soup: BeautifulSoup) -> pd.DataFrame:
    """Jadwal lengkap week 1-9, dibaca dari DOM kartu pertandingan.

    Ini pengganti parse_schedule_from_links(): halaman /schedule menaruh tiap
    pekan di panel `div#t-week-N`, dan di dalamnya ada dua jenis `.match`:

        <div class="match date ...">Jumat, 14 Agustus 2026</div>   <- judul tanggal
        <div class="match position-relative ...">                  <- kartu laga
            <div class="team team1">...<div class="name">EVOS</div></div>
            <div class="score">2</div>
            <div class="time">15:00 ...</div>
            <div class="score">0</div>
            <div class="team team2">...<div class="name">RRQ</div></div>
        </div>

    Judul tanggal dan kartu laga adalah SAUDARA (sibling), bukan induk-anak,
    jadi tanggalnya harus "diingat" sambil menyusuri panel dari atas ke bawah.

    Laga yang belum dimainkan punya `.score` kosong — itu dicatat sebagai
    skor 0-0 dengan status "belum main", bukan dibuang.
    """
    records = []

    for panel in soup.select('div[id^="t-week-"]'):
        m = re.search(r"t-week-(\d+)", panel.get("id", ""))
        if not m:
            continue
        week = int(m.group(1))
        tanggal = None

        for card in panel.select(".match"):
            classes = card.get("class") or []

            if "date" in classes:
                tanggal = parse_tanggal_id(card.get_text(" ", strip=True))
                continue

            t1 = card.select_one(".team1 .name")
            t2 = card.select_one(".team2 .name")
            if t1 is None or t2 is None:
                continue

            # Ambil hanya .score milik kartu ini (bukan yang nyasar dari popup).
            scores = [s.get_text(strip=True) for s in card.select(".score")][:2]
            scores += [""] * (2 - len(scores))
            s1, s2 = scores

            sudah_main = s1.isdigit() and s2.isdigit()
            skor1 = int(s1) if s1.isdigit() else 0
            skor2 = int(s2) if s2.isdigit() else 0

            jam = card.select_one(".time div")
            jam = jam.get_text(strip=True) if jam else None

            records.append({
                "week": week,
                "tanggal": tanggal,
                "jam": jam if jam and re.fullmatch(r"\d{1,2}[:.]\d{2}", jam) else None,
                "tim1": t1.get_text(strip=True).upper(),
                "skor1": skor1,
                "skor2": skor2,
                "tim2": t2.get_text(strip=True).upper(),
                "status": "selesai" if sudah_main else "belum main",
            })

    df = pd.DataFrame(records)
    if not df.empty:
        df = df.drop_duplicates(subset=["week", "tim1", "tim2", "tanggal", "jam"])
        df = df.sort_values(["week", "tanggal", "jam"], na_position="last")
        df = df.reset_index(drop=True)
    return df


def build_team_records(schedule: pd.DataFrame) -> pd.DataFrame:
    """Rekap menang / kalah / draw tiap tim dari tabel jadwal.

    Laga yang belum dimainkan tidak ikut dihitung, tapi timnya tetap muncul
    dengan angka 0 — jadi tim yang belum pernah main sekalipun tidak hilang
    dari tabel.
    """
    if schedule.empty:
        return pd.DataFrame()

    rekap: dict[str, dict] = {}

    def slot(tim: str) -> dict:
        return rekap.setdefault(tim, {
            "tim": tim, "main": 0, "menang": 0, "kalah": 0, "draw": 0,
            "belum_main": 0, "game_menang": 0, "game_kalah": 0,
        })

    for row in schedule.itertuples(index=False):
        a, b = slot(row.tim1), slot(row.tim2)

        if row.status != "selesai":
            a["belum_main"] += 1
            b["belum_main"] += 1
            continue

        for me, lawan, skor_saya, skor_lawan in (
            (a, b, row.skor1, row.skor2),
            (b, a, row.skor2, row.skor1),
        ):
            me["main"] += 1
            me["game_menang"] += skor_saya
            me["game_kalah"] += skor_lawan
            if skor_saya > skor_lawan:
                me["menang"] += 1
            elif skor_saya < skor_lawan:
                me["kalah"] += 1
            else:
                me["draw"] += 1

    df = pd.DataFrame(rekap.values())
    df["selisih_game"] = df["game_menang"] - df["game_kalah"]
    df = df.sort_values(
        ["menang", "selisih_game", "game_menang"], ascending=False
    ).reset_index(drop=True)
    df.insert(0, "peringkat", range(1, len(df) + 1))
    return df


# ---------------------------------------------------------------------------
# MODE INSPECT
# ---------------------------------------------------------------------------

def inspect_page(name: str, html: str) -> None:
    """Petakan isi halaman sebelum menulis parser.

    Ini langkah yang paling sering dilewati orang, padahal paling hemat waktu:
    lihat dulu ada tabel apa saja dan kolomnya apa, baru tulis parser-nya.
    """
    soup = soup_of(html)
    tables = soup.find_all("table")
    print(f"\n=== {name} ({len(html):,} karakter, {len(tables)} tabel) ===")

    for i, table in enumerate(tables):
        headers = table_headers(table)
        n_rows = len(table_body_rows(table))
        print(f"\n  [tabel {i}] {n_rows} baris")
        print(f"    kolom : {headers}")
        rows = table_body_rows(table)
        if rows:
            sample = [cell_parts(c) for c in rows[0][:3]]
            print(f"    contoh: {sample}")

    if not tables:
        print("  (tidak ada <table>; datanya mungkin dalam <div> — "
              "periksa manual dengan Ctrl+U di browser)")


# ---------------------------------------------------------------------------
# ORKESTRASI
# ---------------------------------------------------------------------------

def save(df: pd.DataFrame, name: str, out_dir: Path) -> None:
    if df.empty:
        print(f"  [kosong] {name} — tidak disimpan")
        return
    path = out_dir / f"{name}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  [simpan] {path}  ({len(df)} baris x {len(df.columns)} kolom)")


def run_scrape(out_dir: Path, use_cache: bool, revalidate: bool = False,
               ringkas: bool = False) -> bool:
    """Ambil -> parse -> simpan. Kembalikan True kalau ada data baru ditulis.

    `revalidate=True` dipakai oleh auto_update.py: kalau ketiga halaman
    menjawab 304 (tidak ada yang berubah), fungsi ini berhenti lebih awal dan
    mengembalikan False — tidak ada parsing, tidak ada tulis-menulis berkas.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n[1/3] Mengambil halaman...")
    if revalidate:
        # Urutannya penting. home (282 KB) dan schedule (2,3 MB) sama-sama
        # mengirim Last-Modified, jadi kalau belum ada yang berubah keduanya
        # dijawab 304 — nol byte. Itu kondisi normal sebagian besar siklus.
        home_html, b1 = fetch_revalidate(PAGES["home"])
        sched_html, b3 = fetch_revalidate(PAGES["schedule"])

        if not (b1 or b3):
            print("  [304  ] home & schedule belum berubah — berhenti")
            return False

        # Baru sekarang /statistics disentuh. Halaman itu 1,4 MB dan tidak
        # punya validator HTTP sama sekali (no-cache, tanpa Last-Modified/ETag),
        # jadi SELALU terunduh penuh. Menariknya tiap 5 menit = ratusan MB
        # sehari, percuma: statistik pemain/hero cuma bergerak kalau ada game
        # yang selesai — dan itu pasti sudah kelihatan lebih dulu di
        # home/schedule. Jadi dia ikut ditarik hanya kalau salah satu berubah.
        stats_html, b2 = fetch_revalidate(PAGES["statistics"])

        ubah = [n for n, b in (("home", b1), ("schedule", b3), ("statistics", b2)) if b]
        print(f"  [baru  ] berubah: {', '.join(ubah)}")
    else:
        home_html = fetch(PAGES["home"], use_cache)
        stats_html = fetch(PAGES["statistics"], use_cache)
        sched_html = fetch(PAGES["schedule"], use_cache)

    home = soup_of(home_html)
    stats = soup_of(stats_html)
    sched = soup_of(sched_html)

    print("\n[2/3] Mem-parsing...")
    schedule = parse_schedule(sched)

    # Tabel tim memuat logo DAN kode tim; tabel pemain cuma logo. Jadi tabel
    # tim diparse duluan untuk dipakai sebagai kamus penerjemah.
    tim_stats = parse_team_stats(stats)
    peta_logo = (dict(zip(tim_stats.logo, tim_stats.team_code))
                 if not tim_stats.empty and "logo" in tim_stats.columns else {})
    results = {
        "standings": parse_standings(home),
        "schedule": schedule,
        "schedule_tickets": parse_schedule_from_links(home),
        "team_records": build_team_records(schedule),
        "team_stats": tim_stats,
        "player_stats": parse_player_stats(stats, peta_logo),
        "hero_stats": parse_hero_stats(stats),
    }

    print("\n[3/3] Menyimpan...")
    for name, df in results.items():
        save(df, name, out_dir)

    # Metadata: kapan diambil dan dari mana. Wajib kalau datanya mau dipakai
    # untuk analisis — tanpa ini kamu tidak tahu snapshot-nya kapan.
    meta = {
        "source": BASE_URL,
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": {k: int(len(v)) for k, v in results.items()},
    }
    (out_dir / "_metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(f"  [simpan] {out_dir / '_metadata.json'}")

    if not ringkas:
        for name, df in results.items():
            if df.empty:
                continue
            if name in ("schedule", "team_records"):
                print(f"\n--- {name} ({len(df)} baris) ---")
                print(df.to_string(index=False))
            else:
                print(f"\n--- {name} (5 baris pertama) ---")
                print(df.head().to_string(index=False))

    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Scraper data MPL ID")
    ap.add_argument("--inspect", action="store_true",
                    help="petakan struktur halaman tanpa menyimpan apa pun")
    ap.add_argument("--scrape", action="store_true",
                    help="ambil data dan simpan ke CSV")
    ap.add_argument("--no-cache", action="store_true",
                    help="abaikan cache, fetch ulang dari server")
    ap.add_argument("--out", default=str(OUT_DIR), help="folder output")
    args = ap.parse_args()

    if not (args.inspect or args.scrape):
        ap.print_help()
        return

    use_cache = not args.no_cache

    if args.inspect:
        for name, path in PAGES.items():
            try:
                inspect_page(name, fetch(path, use_cache))
            except Exception as exc:
                print(f"  [error] {name}: {exc}", file=sys.stderr)

    if args.scrape:
        run_scrape(Path(args.out), use_cache)


if __name__ == "__main__":
    main()