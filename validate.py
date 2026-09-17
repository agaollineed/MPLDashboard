#!/usr/bin/env python3
"""validate.py — Periksa data hasil scraping terhadap sumber independen.

Dua pemeriksaan, keduanya memakai data yang SUDAH ter-cache (tidak ada request
jaringan sama sekali):

  1. Crosstable  — halaman musim Liquipedia memuat tabel silang agregat yang
     dirender terpisah dari daftar laga. Agregat yang kita hitung dari daftar
     laga harus sama persis.

  2. Klasemen    — tabel klasemen Liquipedia (snapshot pekan terakhir) harus
     sama dengan klasemen yang kita turunkan sendiri, baik peringkat, menang-
     kalah, maupun game menang-kalah.

Pemeriksaan kedua lebih kuat dari yang pertama: crosstable memvalidasi skor
antar-pasangan, klasemen memvalidasi agregasi DAN urutan peringkatnya.

Jalankan: python validate.py
"""

from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

import dataset as D
import liquipedia_scraper as L


def berkas_logo(el) -> str | None:
    """Nama berkas logo tim, tanpa path dan tanpa ukuran thumbnail.

    INI kunci join yang dipakai, bukan nama tim. Alasannya: nama tim di
    Liquipedia berubah-ubah antar musim untuk organisasi yang sama
    ("EVOS Glory" di S13 vs "EVOS" di S17, "Bigetron Alpha" vs "Bigetron by
    Vitality"), dan tabel klasemen kadang memakai nama organisasi SEKARANG
    sementara daftar laga memakai nama periode. Berkas logonya justru stabil
    dan identik di semua tempat dalam satu halaman.
    """
    img = el.find("img") if el else None
    if not img or not img.get("src"):
        return None
    src = re.sub(r"/thumb(/.+?\.(?:png|jpg|jpeg|webp))/[^/]+$", r"\1",
                 img["src"], flags=re.I)
    return Path(src).stem.lower()


def peta_logo(soup: BeautifulSoup) -> dict[str, str]:
    """berkas logo DAN nama tim -> kode, dari daftar laga musim itu sendiri."""
    peta = {}
    for sel in soup.select(".brkts-matchlist-opponent"):
        dyn = sel.select_one(".team-name-dynamic")
        if not dyn:
            continue
        kode = D.normal((dyn.get("data-team-shortname") or "").upper())
        stem = berkas_logo(sel)
        if stem:
            peta[stem] = kode
        nama = dyn.get("data-team-name")
        if nama:
            peta[nama] = kode
    return peta


@lru_cache(maxsize=1)
def peta_global() -> dict[str, str]:
    """Peta gabungan dari SEMUA musim yang sudah ter-cache.

    Kenapa perlu: crosstable memakai logo & nama organisasi SEKARANG, sedangkan
    daftar laga memakai yang berlaku pada musim itu. Contohnya di S11 crosstable
    menulis "Geek Fam ID" (logo geek_fam_2019) padahal saat itu timnya bernama
    "Geek Slate" (logo geek_slate); begitu juga S14: "ONIC" vs "Fnatic ONIC".

    Peta lintas-musim menyelesaikannya tanpa tabel alias manual: logo kanonik
    yang tidak dikenal di musim X pasti muncul sebagai logo periode di musim
    lain, jadi kodenya bisa dipelajari dari sana.
    """
    peta: dict[str, str] = {}
    for berkas in sorted(L.CACHE_DIR.glob("*.html")):
        soup = BeautifulSoup(berkas.read_text(encoding="utf-8"), "lxml")
        peta.update(peta_logo(soup))
    return peta


def resolusi(sel, label: str, lokal: dict[str, str]) -> str | None:
    """Tentukan kode tim dari satu sel tabel: coba logo, lalu nama, lalu global."""
    stem = berkas_logo(sel)
    for kunci, peta in ((stem, lokal), (label, lokal),
                        (stem, peta_global()), (label, peta_global())):
        if kunci and kunci in peta:
            return peta[kunci]
    return None


def cek_crosstable(soup: BeautifulSoup, laga: pd.DataFrame) -> tuple[int, int, list[str]]:
    ct = soup.select_one("table.crosstable")
    if ct is None:
        return 0, 0, []

    h2h = D.head_to_head(laga)
    peta = peta_logo(soup)

    baris = ct.find_all("tr")
    urut = []          # kode tim per baris, diresolusi lewat berkas logo
    label = []         # nama tampilan, untuk pesan kesalahan
    for tr in baris:
        sel = tr.find_all(["td", "th"])
        span = sel[0].select_one("[data-highlighting-class]") if sel else None
        if span:
            nama = span["data-highlighting-class"]
            urut.append(resolusi(sel[0], nama, peta))
            label.append(nama)

    n = len(urut)
    ok = beda = 0
    catatan = []
    for i, tr in enumerate(baris[:n]):
        sel = tr.find_all(["td", "th"])
        for j, c in enumerate(sel[-n:]):
            if i == j:
                continue
            m = re.match(r"^(\d+)-(\d+)\s", L.teks(c))
            if not m:
                continue
            lp = (int(m.group(1)), int(m.group(2)))
            a, b = urut[i], urut[j]
            if a is None or b is None:
                beda += 1
                catatan.append(f"tim tidak dikenali: {label[i]} / {label[j]}")
                continue
            pertemuan = h2h.get((a, b), [])
            kita = (sum(x for x, _ in pertemuan), sum(y for _, y in pertemuan))
            if lp == kita:
                ok += 1
            else:
                beda += 1
                catatan.append(f"{label[i]} vs {label[j]}: LP {lp[0]}-{lp[1]} "
                               f"!= kita {kita[0]}-{kita[1]}")
    return ok, beda, catatan


def tabel_klasemen(soup: BeautifulSoup):
    """Cari tabel klasemen lewat NAMA KOLOM-nya, bukan urutannya.

    Pelajaran yang terulang: `find_all("table")[0]` pecah begitu halamannya
    menambah tabel di atas. S10 punya tiga tabel dan klasemennya ada di indeks
    1 (didahului tabel daftar laga), sementara S11-S17 di indeks 0. Dicari lewat
    header, semuanya jalan.
    """
    for t in soup.find_all("table"):
        for tr in t.find_all("tr")[:3]:
            kolom = {L.teks(c).lower() for c in tr.find_all(["th", "td"])}
            if {"#", "team", "match", "game"} <= kolom:
                return t
    return None


def cek_klasemen(soup: BeautifulSoup, laga: pd.DataFrame,
                 season: int) -> tuple[int, int, list[str]]:
    kita = D.klasemen(laga)
    jml = len(kita)
    if not jml:
        return 0, 0, []

    t = tabel_klasemen(soup)
    if t is None:
        return 0, 1, ["tabel klasemen tidak ditemukan"]
    rows = t.find_all("tr")[-jml:]
    peta = peta_logo(soup)

    ok = beda = 0
    catatan = []
    for i, tr in enumerate(rows):
        sel = tr.find_all(["th", "td"])
        teks = [L.teks(c) for c in sel]
        # Kolomnya tidak selalu di posisi yang sama (S12 menyelipkan kolom %),
        # jadi cari berdasarkan POLA "X-Y", bukan indeks.
        pasang = [tuple(map(int, s.split("-"))) for s in teks
                  if re.fullmatch(r"\d+-\d+", s)]
        if len(pasang) < 2:
            continue
        (m_w, m_l), (g_w, g_l) = pasang[0], pasang[1]

        a = sel[1].find("a") if len(sel) > 1 else None
        nama = (a.get("title") if a else re.sub(r"[▲▼]\d*", "", teks[1])).strip()
        # Resolusi lewat logo; kalau baris klasemen tidak memuat gambar,
        # kembalikan ke nama kanonik + riwayat ganti nama.
        kode = resolusi(sel[1] if len(sel) > 1 else None, nama, peta)
        if kode is not None:
            # Tabel klasemen memakai nama organisasi SEKARANG; terjemahkan ke
            # kode periode (mis. "Team Liquid ID" di S13 sebenarnya AURA Fire).
            kode = D.kode_periode(kode, season)

        b = kita.iloc[i]
        sama = (b.menang == m_w and b.kalah == m_l
                and b.game_menang == g_w and b.game_kalah == g_l)
        if sama and (kode is None or b.kode == kode):
            ok += 1
        else:
            beda += 1
            catatan.append(f"#{i+1} LP {nama} {m_w}-{m_l} {g_w}-{g_l} | "
                           f"kita {b.kode} {b.menang}-{b.kalah} "
                           f"{b.game_menang}-{b.game_kalah}")
    return ok, beda, catatan


def cek_playoff(season: int, po: pd.DataFrame) -> tuple[int, int, list[str]]:
    """Cocokkan tabel hasil playoff dengan bracket di halaman yang sama.

    Prinsipnya sama dengan crosstable di regular season: halaman memuat dua
    penyajian berbeda atas data yang sama, dan keduanya harus sepakat.
    """
    if po.empty:
        return 0, 0, []

    berkas = L.CACHE_DIR / (L.JUDUL_PLAYOFF.format(n=season).replace("/", "_") + ".html")
    if not berkas.exists():
        return 0, 1, ["halaman playoff belum ter-cache"]

    soup = BeautifulSoup(berkas.read_text(encoding="utf-8"), "lxml")
    laga = [{"kode1": D.kode_periode(r.kode1, season), "skor1": int(r.skor1),
             "kode2": D.kode_periode(r.kode2, season), "skor2": int(r.skor2)}
            for r in po.itertuples(index=False)]

    # Bracket memakai kode mentah; normalkan supaya sebanding.
    dari_bracket = []
    for m in soup.select(".brkts-match"):
        entri = m.select(".brkts-opponent-entry")
        if len(entri) < 2:
            continue
        pasang = []
        for ent in entri[:2]:
            dyn = ent.select_one("[data-team-shortname]")
            skor = ent.select_one(".brkts-opponent-score-inner")
            angka = re.findall(r"\d+", L.teks(skor) if skor else "")
            # Pakai kode_periode, bukan normal(), supaya sebanding dengan sisi
            # tabel: di S13 bracket menulis TLID ("Liquid Aura") sedangkan
            # tabel sudah dipetakan ke AURA — identitas yang sama.
            pasang.append((D.kode_periode(
                (dyn.get("data-team-shortname") or "").upper(), season)
                if dyn else None,
                int(angka[0]) if angka else None))
        if all(k and v is not None for k, v in pasang):
            dari_bracket.append(tuple(sorted(pasang)))

    sisa = list(dari_bracket)
    ok = 0
    catatan = []
    for m in laga:
        item = tuple(sorted(((m["kode1"], m["skor1"]), (m["kode2"], m["skor2"]))))
        if item in sisa:
            sisa.remove(item)
            ok += 1
        else:
            catatan.append(f"playoff tidak ada di bracket: "
                           f"{m['kode1']} {m['skor1']}-{m['skor2']} {m['kode2']}")
    return ok, len(catatan), catatan


def cek_mvp(season: int) -> tuple[int, int, list[str]]:
    """Cocokkan poin Match MVP hitungan sendiri dengan tabel MVP resmi.

    Hanya S10 dan S11 yang punya tabel ini di Liquipedia. Justru karena itu
    pemeriksaannya berharga: kalau cara menghitung kita terbukti benar di dua
    musim yang punya pembanding, angka untuk enam musim sisanya (yang tidak
    punya tabel) jadi bisa dipercaya.

    Dijoinkan lewat TAUTAN halaman pemain, bukan namanya. Nama tampilan beda
    antar halaman Liquipedia ("VYN" vs "Vyn", "HAIZZ" vs "Haiz", "Super KENN"
    vs "Kenn") — menyamakan lewat nama menghasilkan 33 selisih palsu.
    """
    st = D.mvp_standing()
    if st.empty or not (st.season == season).any():
        return 0, 0, []

    laga = D.mvp_laga()
    laga = laga[(laga.season == season) & laga.link.notna()]
    kita = (laga.groupby(["link", "week"]).size() * D.POIN_MATCH_MVP).to_dict()

    pekan = st[(st.season == season) & st.week.notna() & st.link.notna()]
    ok = beda = 0
    catatan = []
    for r in pekan.itertuples(index=False):
        k = kita.get((r.link, r.week), 0)
        if k == r.poin_match:
            ok += 1
        else:
            beda += 1
            catatan.append(f"MVP {r.pemain} W{int(r.week)}: "
                           f"resmi={r.poin_match:.0f} kita={k}")

    # Weekly MVP: sumbernya tabel "Weekly Awards" di halaman utama musim,
    # pembandingnya kolom W di tabel MVP /Statistics. Dua halaman berbeda.
    wk = D.weekly_mvp()
    if not wk.empty and (wk.season == season).any():
        dari_aw = {(r.link, r.week) for r in
                   wk[wk.season == season].itertuples(index=False)}
        dari_st = {(r.link, int(r.week)) for r in pekan.itertuples(index=False)
                   if r.poin_weekly > 0}
        ok += len(dari_aw & dari_st)
        for l, w in sorted(dari_aw ^ dari_st):
            beda += 1
            catatan.append(f"weekly MVP W{w} ({l}) hanya ada di satu sumber")

    # Atribusi tim: tabel resmi memuat tim tiap pemain, inferensi kita memakai
    # "MVP selalu dari tim pemenang". Keduanya harus sepakat.
    nama2kode = {}
    semua = D.semua_laga()
    for r in semua[semua.season == season].itertuples(index=False):
        nama2kode[r.nama1] = r.kode1
        nama2kode[r.nama2] = r.kode2
    infer = laga.groupby("link").kode.agg(lambda x: set(x)).to_dict()
    for r in st[(st.season == season) & st.total.notna()].itertuples(index=False):
        tim_infer = infer.get(r.link)
        if not tim_infer:
            continue
        kode_resmi = nama2kode.get(r.tim)
        if kode_resmi and tim_infer == {kode_resmi}:
            ok += 1
        elif kode_resmi:
            beda += 1
            catatan.append(f"tim MVP {r.pemain}: resmi={kode_resmi} "
                           f"infer={sorted(tim_infer)}")
    return ok, beda, catatan


def cek_roster(season: int) -> tuple[int, int, list[str]]:
    """Periksa bentuk roster: tiap tim harus punya 5 pemain inti, satu per role.

    Berbeda dari pemeriksaan lain, di sini tidak ada tabel pembanding di
    Liquipedia — yang diuji adalah INVARIAN strukturnya. MPL memainkan format
    5 lawan 5 dengan satu pemain per role, jadi roster inti yang tidak berisi
    tepat {EXP, Jungle, Mid, Gold, Roam} berarti parser salah baca ikon role
    atau salah memisahkan bagian Main dari Subs.
    """
    rs = D.roster()
    if rs.empty or not (rs.season == season).any():
        return 0, 0, []

    harus = set(D.URUT_ROLE)
    sub = rs[(rs.season == season) & (rs.grup.str.lower() == "main")]

    ok = beda = 0
    catatan = []
    for (tim,), grup in sub.groupby(["tim"]):
        roles = list(grup.role.dropna())
        if len(grup) == 5 and set(roles) == harus and len(roles) == 5:
            ok += 1
        else:
            beda += 1
            catatan.append(f"roster {tim}: {len(grup)} pemain, role={sorted(roles)}")

    # Tiap tim yang main di musim itu harus punya roster, dan sebaliknya.
    laga = D.semua_laga()
    tim_laga = set(laga[laga.season == season].kode1) | set(laga[laga.season == season].kode2)
    tim_roster = set(rs[rs.season == season].kode.dropna())
    for hilang in sorted(tim_laga - tim_roster):
        beda += 1
        catatan.append(f"tim {hilang} main tapi tidak punya roster")
    for asing in sorted(tim_roster - tim_laga):
        beda += 1
        catatan.append(f"tim {asing} punya roster tapi tidak main")

    return ok, beda, catatan


def cek_pickban(season: int) -> tuple[int, int, list[str]]:
    """Cocokkan pick & ban hasil parsing dengan tabel hero di halaman Statistics.

    Dua hal diuji sekaligus:

    1. JUMLAH  — total pick dan ban tiap hero harus sama dengan kolom di tabel
       hero Liquipedia. Tabel itu mencakup regular season + playoff sekaligus,
       jadi hitungan kita juga digabung.
    2. PEMILIK — tabel "Played By Teams" memberi rincian pick per tim untuk tiap
       hero. Ini yang membuktikan atribusi tim kita benar. Penting, karena
       kelas warna sisi (blue/red) BERTUKAR antar game, jadi yang kita pakai
       adalah posisi kiri/kanan; kalau anggapan itu salah, rincian per tim
       akan langsung meleset.
    """
    berkas = L.CACHE_DIR / (L.JUDUL_STATS.format(n=season).replace("/", "_") + ".html")
    pb = D.picks_bans()
    if not berkas.exists() or pb.empty or not (pb.season == season).any():
        return 0, 0, []

    soup = BeautifulSoup(berkas.read_text(encoding="utf-8"), "lxml")
    tabel = soup.find_all("table")
    if not tabel:
        return 0, 0, []

    # Cari tabel hero lewat KOLOMNYA, bukan indeksnya. S10 dan S11 menaruh
    # tabel MVP standing lebih dulu, jadi tabel hero ada di indeks 1 —
    # mengambil tabel[0] di situ menghasilkan nama pemain, bukan hero.
    t_hero = None
    for t in tabel:
        baris0 = t.find("tr")
        if not baris0:
            continue
        kolom = {L.teks(c).lower() for c in baris0.find_all(["th", "td"])}
        if {"picks", "bans"} <= kolom:
            t_hero = t
            break
    if t_hero is None:
        return 0, 1, ["tabel hero tidak ditemukan di halaman Statistics"]

    # Baris hero: kolomnya banyak dan diawali nomor urut.
    hero_urut, total_resmi, gagal_baca = [], {}, []
    for tr in t_hero.find_all("tr"):
        sel = tr.find_all(["th", "td"])
        if len(sel) >= 15 and L.teks(sel[0]).isdigit():
            nama = L.teks(sel[1])
            hero_urut.append(nama)
            # Indeks kolom mengikuti header dua lapis tabel itu:
            #   0 no | 1 Hero | 2-6 Picks(sum,W,L,WR,%T)
            #   7-10 Blue | 11-14 Red | 15-16 Bans(sum,%T) | 17-18 Picks&Bans
            # sel[15] = jumlah ban. Sempat salah memakai sel[16] (itu persen),
            # int() gagal, lalu except menelan errornya — akibatnya total pick
            # dan ban tidak pernah benar-benar dibandingkan. Sekarang
            # kegagalan parsing dilaporkan, bukan didiamkan.
            try:
                total_resmi[nama] = (int(L.teks(sel[2])), int(L.teks(sel[15])))
            except (ValueError, IndexError):
                gagal_baca.append(nama)

    sub = pb[pb.season == season]
    kol_p = [f"pick{i}" for i in range(1, 6)]
    kol_b = [f"ban{i}" for i in range(1, 6)]
    hit_p = sub[kol_p].stack().value_counts().to_dict()
    hit_b = sub[kol_b].stack().value_counts().to_dict()

    ok = beda = 0
    catatan = []
    if gagal_baca:
        beda += len(gagal_baca)
        catatan.append(f"{len(gagal_baca)} baris hero gagal dibaca "
                       f"(mis. {', '.join(gagal_baca[:3])})")
    if not total_resmi:
        return 0, 1, ["NOL hero terbaca dari tabel — pemeriksa tidak jalan"]
    for hero, (n_pick, n_ban) in total_resmi.items():
        if hit_p.get(hero, 0) == n_pick:
            ok += 1
        else:
            beda += 1
            catatan.append(f"pick {hero}: resmi={n_pick} kita={hit_p.get(hero, 0)}")
        if hit_b.get(hero, 0) == n_ban:
            ok += 1
        else:
            beda += 1
            catatan.append(f"ban {hero}: resmi={n_ban} kita={hit_b.get(hero, 0)}")

    # --- pemilik hero, lewat tabel "Played By Teams" ---
    pbt = [t for t in tabel
           if t.find("tr") and "played by team" in L.teks(t.find("tr")).lower()]

    # Kedua sisi disamakan pada level ALIAS KODE saja (normal), bukan level
    # rebrand antar-musim (kode_periode). Itu persis cara Liquipedia
    # mengelompokkan di tabel ini, dan ketiga kasusnya sudah diperiksa:
    #
    #   GFID -> GEEK (S10)  : tabel memakai GEEK, daftar laga memakai GFID
    #   FNOC -> ONIC (S13-14): tabel memakai ONIC, daftar laga memakai FNOC
    #   Liquid Aura         : TIDAK digabung ke AURA — Liquipedia memang
    #                         mencatatnya sebagai entitas tersendiri, jadi
    #                         kita pun tidak boleh menggabungnya di sini.
    mentah = pd.read_csv(L.OUT_DIR / "picks_bans.csv")
    mentah = mentah[mentah.season == season]
    per_tim: dict = {}
    for r in mentah.itertuples(index=False):
        kode = D.normal(r.kode)
        for i in range(1, 6):
            h = getattr(r, f"pick{i}")
            if isinstance(h, str):
                per_tim[(h, kode)] = per_tim.get((h, kode), 0) + 1

    for hero, t in zip(hero_urut, pbt):
        for tr in t.find_all("tr"):
            sel = tr.find_all(["th", "td"])
            if len(sel) < 6 or not L.teks(sel[0]).isdigit():
                continue
            tim = D.normal(L.teks(sel[1]).upper())
            try:
                n = int(L.teks(sel[2]))
            except ValueError:
                continue
            if per_tim.get((hero, tim), 0) == n:
                ok += 1
            else:
                beda += 1
                catatan.append(f"pick {hero}/{tim}: resmi={n} "
                               f"kita={per_tim.get((hero, tim), 0)}")
    return ok, beda, catatan


def cek_matchup(season: int) -> tuple[int, int, list[str]]:
    """Uji simetri rekor hero-dengan-hero, dengan aturan berbeda per jenis.

    Ini bukan satu aturan yang dipukul rata:

      rekan  — dua hero berada di TIM YANG SAMA, jadi mereka menang dan kalah
               bersama. Rekornya harus IDENTIK dua arah: (n, W, L) == (n, W, L).
      lawan  — dua hero BERHADAPAN, jadi menang salah satu adalah kalah yang
               lain. Rekornya harus BERKEBALIKAN: (n, W, L) <-> (n, L, W).

    Tidak ada tabel pembanding untuk data ini, jadi konsistensi internalnya
    sendiri yang diuji — dan itu cukup tajam: tertukarnya kolom W/L, atau
    tertukarnya sub-tabel "Played With" dengan "Played Against", akan langsung
    melanggar salah satu aturan.

    Liquipedia hanya menampilkan lima teratas per kategori, jadi sebagian
    pasangan cuma muncul satu arah. Itu bukan kesalahan, jadi tidak dihitung.
    """
    rel = D.hero_relasi()
    if rel.empty or not (rel.season == season).any():
        return 0, 0, []

    ok = beda = 0
    catatan = []
    for jenis in ("rekan", "lawan"):
        sub = rel[(rel.season == season) & (rel.jenis == jenis)]
        peta = {(r.hero, r.hero_lain): (r.jumlah, r.menang, r.kalah)
                for r in sub.itertuples(index=False)}
        for (a, b), (n, w, l) in peta.items():
            balik = peta.get((b, a))
            if balik is None:
                continue
            harus = (n, w, l) if jenis == "rekan" else (n, l, w)
            if balik == harus:
                ok += 1
            else:
                beda += 1
                catatan.append(f"{jenis} {a}/{b}: {n}/{w}/{l} tapi {b}/{a}: {balik}")
    return ok, beda, catatan


def cek_playoff_gelar(season: int, po: pd.DataFrame) -> tuple[int, int, list[str]]:
    """Lolos playoff dan gelar juara, diadu dengan daftar laga playoff.

    Tiga hal yang diperiksa:

      1. tim yang ditandai lolos = persis tim yang muncul di laga playoff
      2. tim berperingkat playoff #1 = juara yang dihitung terpisah oleh juara()
      3. pemain bergelar juara = seluruh anggota roster tim juara musim itu

    Nomor 2 bukan basa-basi: peringkat playoff diturunkan dari laga TERAKHIR
    tiap tim, sedangkan juara() diturunkan dari pemenang Grand Final. Dua jalur
    yang berbeda, jadi kalau keduanya sepakat, penurunan rondenya benar.
    """
    if po.empty:
        return 0, 0, []

    n_ok = beda = 0
    catatan: list[str] = []

    hp = D.hasil_playoff(po)
    hp = hp[hp.season == season]

    # --- 1. himpunan tim harus sama persis
    dari_laga = set(po.kode1) | set(po.kode2)
    ditandai = set(hp.kode)
    if dari_laga == ditandai:
        n_ok += len(dari_laga)
    else:
        for k in sorted(dari_laga - ditandai):
            beda += 1
            catatan.append(f"{k}: main di playoff tapi tidak ditandai lolos")
        for k in sorted(ditandai - dari_laga):
            beda += 1
            catatan.append(f"{k}: ditandai lolos tapi tidak ada di laga playoff")

    # --- 2. peringkat playoff #1 harus sama dengan juara()
    puncak = sorted(hp[hp.peringkat_playoff == 1].kode)
    resmi = D.juara(po).get(season)
    if len(puncak) == 1 and puncak[0] == resmi:
        n_ok += 1
    else:
        beda += 1
        catatan.append(f"peringkat playoff #1 {puncak} tidak sama dengan "
                       f"juara Grand Final {resmi!r}")

    # --- 3. pemain bergelar = anggota roster tim juara
    jp = D.juara_pemain()
    jp = jp[jp.season == season]
    rs = D.roster()
    regu = rs[(rs.season == season) & (rs.kode == resmi)
              & rs.grup.isin(["Main", "Subs"])]
    if len(regu) == len(jp) and set(regu.pemain) == set(jp.pemain):
        n_ok += len(jp)
    else:
        beda += 1
        catatan.append(f"pemain bergelar {len(jp)} tidak sama dengan roster "
                       f"{resmi} ({len(regu)} orang)")

    return n_ok, beda, catatan


def cek_statistik() -> tuple[int, int, list[str]]:
    """Statistik pemain & tim dari situs resmi MPL.

    Tidak ada sumber kedua untuk diadu, jadi yang dipakai adalah tiga relasi
    yang HARUS berlaku kalau parse-nya benar:

      1. rata-rata = total / jumlah game          (situs menayangkan keduanya)
      2. KDA       = (kill + assist) / mati
      3. total tim = jumlah seluruh pemainnya     (dua tabel yang terpisah)

    Nomor 3 yang paling tajam: tabel tim dan tabel pemain diparse lewat jalur
    kode yang berbeda, dari tabel HTML yang berbeda pula. Kalau penetapan tim
    lewat berkas logo salah untuk satu pemain saja, angkanya langsung pecah.
    """
    sp = D.stat_pemain()
    st = D.stat_tim()
    if sp.empty or st.empty:
        return 0, 0, []

    n_ok = beda = 0
    catatan: list[str] = []

    # --- 1. rata-rata konsisten dengan total (toleransi = pembulatan 2 desimal)
    main = sp[sp.main > 0]
    for tot, rata in (("kill", "kill_rata"), ("mati", "mati_rata"),
                      ("assist", "assist_rata")):
        selisih = (main[tot] / main.main - main[rata]).abs()
        salah = main[selisih > 0.01]
        n_ok += len(main) - len(salah)
        beda += len(salah)
        for b in salah.head(2).itertuples(index=False):
            catatan.append(f"{b.pemain}: {tot} {getattr(b, tot)}/{b.main} "
                           f"tidak sama dengan {rata} {getattr(b, rata)}")

    # --- 2. rumus KDA
    m2 = main[main.mati > 0]
    selisih = ((m2["kill"] + m2.assist) / m2.mati - m2.kda).abs()
    salah = m2[selisih > 0.01]
    n_ok += len(m2) - len(salah)
    beda += len(salah)
    for b in salah.head(2).itertuples(index=False):
        catatan.append(f"{b.pemain}: KDA {b.kda} != (K+A)/D")

    # --- 3. total tim vs jumlah pemainnya
    #
    # Sebelum diadu, daftar pemainnya harus dipastikan LENGKAP dulu. Patokannya:
    # setiap game diisi tepat lima pemain, jadi jumlah `main` seluruh pemain
    # satu tim harus sama dengan 5 x jumlah game tim itu. Kalau kurang, ada
    # pemain yang benar-benar bermain tapi tidak tercantum di tabel pemain —
    # lubang di SUMBER, bukan kesalahan parse. Mengadu total tim dengan daftar
    # yang bolong hanya menghasilkan alarm palsu, jadi tim itu dilewati.
    #
    # Patokan ini sengaja tidak memakai role: pemain cadangan di role yang sama
    # membuat jumlah per-role melebihi jumlah game, sehingga role lain terlihat
    # "kurang" padahal datanya utuh.
    agg = sp.groupby("kode")[["kill", "mati", "assist"]].sum()
    for b in st.itertuples(index=False):
        if b.kode not in agg.index:
            beda += 1
            catatan.append(f"{b.kode}: ada di tabel tim tapi tidak punya pemain")
            continue

        regu = sp[sp.kode == b.kode]
        game = int(regu.main.max()) if len(regu) else 0
        kurang = 5 * game - int(regu.main.sum())
        if kurang:
            catatan.append(f"{b.kode}: daftar pemain di sumber kurang {kurang} "
                           f"game-pemain dari {5 * game} yang seharusnya "
                           f"({game} game x 5) — perbandingan total dilewati")
            continue
        n_ok += 1

        p = agg.loc[b.kode]
        for kol in ("kill", "mati", "assist"):
            if int(getattr(b, kol)) == int(p[kol]):
                n_ok += 1
            else:
                beda += 1
                catatan.append(
                    f"{b.kode}: {kol} tim {int(getattr(b, kol))} vs jumlah "
                    f"pemain {int(p[kol])} (selisih "
                    f"{int(getattr(b, kol)) - int(p[kol])})")

    # --- 4. tiap pemain harus dapat kode tim yang dikenal
    tak_dikenal = sorted(set(sp.kode) - set(D.TIM))
    if tak_dikenal:
        beda += len(tak_dikenal)
        catatan.append(f"kode tim tidak dikenal: {tak_dikenal}")
    else:
        n_ok += len(sp)

    return n_ok, beda, catatan


def main() -> int:
    semua = D.semua_laga()
    if semua.empty:
        print("Belum ada data. Jalankan scraper dulu.", file=sys.stderr)
        return 1

    po_semua = D.playoff()
    champ = D.juara(po_semua)

    print(f"{'MUSIM':7}{'LAGA':>6}{'CROSSTABLE':>14}{'KLASEMEN':>12}"
          f"{'PLAYOFF':>12}{'MVP':>12}{'ROSTER':>11}{'PICK/BAN':>14}"
          f"{'MATCHUP':>13}{'JUARA':>7}   SUMBER")
    total_beda = 0

    for n in sorted(semua.season.unique()):
        laga = semua[semua.season == n]
        sumber = laga.sumber.iloc[0]

        po = po_semua[po_semua.season == n] if not po_semua.empty else po_semua

        if sumber != "liquipedia":
            # Laganya dari id-mpl.com, jadi tidak ada crosstable/klasemen
            # Liquipedia untuk dibandingkan. TAPI roster-nya tetap datang dari
            # Liquipedia, jadi pemeriksaan itu harus tetap jalan — kalau
            # dilewati begitu saja, roster musim berjalan lolos tanpa dicek.
            rs_ok, rs_beda, rs_note = cek_roster(n)
            total_beda += rs_beda
            rs_kol = (f'{rs_ok} tim ' + ('OK' if not rs_beda else f'{rs_beda} BEDA')
                      if rs_ok or rs_beda else '-')
            print(f"S{n:<6}{len(laga):>6}{'-':>14}{'-':>12}{'-':>12}{'-':>12}"
                  f"{rs_kol:>11}{'-':>14}{'-':>13}{champ.get(n, '-'):>7}   {sumber}")
            for c in rs_note:
                print(f"         -> {c}")
            continue

        soup = BeautifulSoup(L.ambil_parse(L.JUDUL.format(n=n)), "lxml")
        ct_ok, ct_beda, ct_note = cek_crosstable(soup, laga)
        kl_ok, kl_beda, kl_note = cek_klasemen(soup, laga, n)
        po_ok, po_beda, po_note = cek_playoff(n, po)
        gl_ok, gl_beda, gl_note = cek_playoff_gelar(n, po)
        po_ok += gl_ok
        po_beda += gl_beda
        po_note += gl_note
        mv_ok, mv_beda, mv_note = cek_mvp(n)
        rs_ok, rs_beda, rs_note = cek_roster(n)
        pb_ok, pb_beda, pb_note = cek_pickban(n)
        mu_ok, mu_beda, mu_note = cek_matchup(n)

        # Nol perbandingan bukan "lulus" — itu berarti pemeriksanya tidak
        # menemukan apa pun untuk dicek, dan justru harus berbunyi.
        for label, n_ok, note in (("crosstable", ct_ok, ct_note),
                                  ("klasemen", kl_ok, kl_note)):
            if n_ok == 0 and not note:
                note.append(f"{label}: NOL perbandingan — pemeriksa tidak "
                            f"menemukan tabelnya, bukan berarti cocok")
                if label == "crosstable":
                    ct_beda += 1
                else:
                    kl_beda += 1

        total_beda += (ct_beda + kl_beda + po_beda + mv_beda + rs_beda
                       + pb_beda + mu_beda)

        po_kol = (f'{po_ok} laga ' + ('OK' if not po_beda else f'{po_beda} BEDA')
                  if po_ok or po_beda else '-')
        mv_kol = (f'{mv_ok} sel ' + ('OK' if not mv_beda else f'{mv_beda} BEDA')
                  if mv_ok or mv_beda else 'tanpa tabel')
        print(f"S{n:<6}{len(laga):>6}"
              f"{f'{ct_ok} sel ' + ('OK' if not ct_beda else f'{ct_beda} BEDA'):>14}"
              f"{f'{kl_ok} baris ' + ('OK' if not kl_beda else f'{kl_beda} BEDA'):>12}"
              f"{po_kol:>12}{mv_kol:>12}"
              f"{f'{rs_ok} tim ' + ('OK' if not rs_beda else f'{rs_beda} BEDA'):>11}"
              f"{f'{pb_ok} sel ' + ('OK' if not pb_beda else f'{pb_beda} BEDA'):>14}"
              f"{f'{mu_ok} pasang ' + ('OK' if not mu_beda else f'{mu_beda} BEDA'):>13}"
              f"{champ.get(n, '-'):>7}   {sumber}")
        for c in (ct_note + kl_note + po_note + mv_note + rs_note
                  + pb_note[:5] + mu_note[:3]):
            print(f"         -> {c}")

    # Statistik KDA/objektif hanya ada untuk musim berjalan, jadi tidak muat
    # sebagai kolom di tabel per musim di atas — dilaporkan terpisah.
    sv_ok, sv_beda, sv_note = cek_statistik()
    if sv_ok or sv_beda:
        total_beda += sv_beda
        print(f"\nSTATISTIK PEMAIN & TIM (musim berjalan)")
        print(f"  {sv_ok} relasi diperiksa  "
              + ("SEMUA KONSISTEN" if not sv_beda else f"{sv_beda} TIDAK KONSISTEN"))
        for c in sv_note:
            print(f"    -> {c}")

    print()
    print("SEMUA COCOK" if total_beda == 0 else f"ADA {total_beda} SELISIH")
    return 0 if total_beda == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
