#!/usr/bin/env python3
"""build_site.py — Ubah CSV hasil scraping jadi satu halaman web statis.

    mpl_scraper.py --scrape        \\
                                    >-- data/*.csv --> build_site.py --> site/index.html
    liquipedia_scraper.py --scrape /

Generator ini tidak pernah menyentuh jaringan. Semua angka di halaman berasal
dari data/, dan klasemen serta head-to-head DIHITUNG dari daftar laga (lihat
dataset.py) — bukan di-scrape terpisah. Jadi tiga tab di satu musim dijamin
saling konsisten, dan satu jalur kode melayani sembilan musim.

Jalankan:  python build_site.py
"""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from dataset import (TIM, URUT_ROLE, awards, hasil_playoff, head_to_head,
                     hero_relasi, hero_stats, juara, juara_pemain, klasemen,
                     mvp_resmi, pemain_id, pickban_per_laga, picks_bans,
                     playoff, roster, semua_laga, stat_pemain, stat_tim,
                     statistik_tim)

DATA_DIR = Path("data")
SITE_DIR = Path("site")

HARI = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday"]
BULAN = ["", "January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"]


def e(x) -> str:
    """Escape HTML. Selalu dipakai untuk apa pun yang berasal dari data."""
    return html.escape(str(x if x is not None else ""))


@lru_cache(maxsize=None)
def data_uri(berkas: str) -> str:
    """Baca logo dari site/assets/ dan ubah jadi data URI base64."""
    path = SITE_DIR / "assets" / berkas
    if not path.exists():
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def logo(kode: str, cls: str = "logo") -> str:
    """Satu logo tim, sebagai <span> ber-background, bukan <img>.

    Logo yang sama muncul ratusan kali (9 musim x klasemen + 616 kartu laga +
    9 matriks). Kalau data URI base64-nya ditempel di tiap <img src="...">,
    berkasnya membengkak jadi puluhan MB. Ditaruh SEKALI di CSS lalu dirujuk
    lewat nama class, ukurannya tetap ratusan KB.
    """
    nama, berkas = TIM.get(kode, (kode, ""))
    if not berkas or not data_uri(berkas):
        return f'<span class="{cls} logo-kosong">{e(kode)}</span>'
    return (f'<span class="{cls} lg-{kode.lower()}" role="img" '
            f'aria-label="{e(nama)}" title="{e(nama)}"></span>')


# Warna garis tiap tim DIAMBIL DARI LOGONYA — disampel sekali dengan pembaca
# PNG kecil (warna paling sering muncul yang cukup pekat), bukan ditebak dari
# ingatan soal warna merek. Nilai mentah hasil sampelnya ditulis di komentar;
# yang dipakai versi yang sudah diangkat supaya terbaca di atas latar gelap —
# semuanya >= 3:1 terhadap --bg, beberapa jauh di atas itu.
#
# Empat tim MPL memakai merah/oranye di dunia nyata, jadi beberapa pasangan
# tetap mirip betapa pun digeser. Karena itu warna di sini cuma alat
# PENELUSURAN; identitasnya dibawa logo di ujung kanan tiap garis, tidak
# pernah oleh warna saja.
WARNA_TIM = {
    "ONIC": "#eef1f8",   # siluet hitam; di situs dibalik jadi putih
    "RRQ":  "#f5a623",   # sampel #f4aa34
    "NAVI": "#f7e34a",   # sampel #feec00, diredam sedikit
    "DEWA": "#c7b15a",   # sampel #e5bb62, ke olive supaya menjauh dari RRQ
    "BTR":  "#ff4d4d",   # sampel #e90303, diangkat
    "GEEK": "#ff8360",   # sampel #ef1b23, dihangatkan supaya menjauh dari BTR
    "AE":   "#cf5b7d",   # sampel #ae1322, ke rose supaya menjauh dari BTR/GEEK
    "AURA": "#ff6a2b",   # sampel #ef4023, diangkat
    "EVOS": "#5cb8e8",   # sampel #456b8e, diangkat
    "TLID": "#3f7dff",   # logonya navy pekat, diangkat
    "RBL":  "#8a93c9",   # sampel #12335f, diangkat
}

# Role -> berkas ikonnya di site/assets/
IKON_BERKAS = {
    "EXP": "role-exp.png", "Jungle": "role-jungle.png", "Mid": "role-mid.png",
    "Gold": "role-gold.png", "Roam": "role-roam.png", "Flex": "role-flex.png",
}


def css_logo() -> str:
    aturan = []
    for kode, (_, berkas) in TIM.items():
        src = data_uri(berkas)
        if src:
            aturan.append(f'.lg-{kode.lower()}{{background-image:url("{src}")}}')
    src_mpl = data_uri("mpl.webp")
    if src_mpl:
        aturan.append(f'.lg-mpl{{background-image:url("{src_mpl}")}}')
    # Ikon role ikut ditanam sekali di CSS, bukan diulang per pemain — pola
    # yang sama seperti logo tim, dan alasannya sama: ada ratusan pemain.
    for role, berkas in IKON_BERKAS.items():
        src = data_uri(berkas)
        if src:
            aturan.append(f'.ir-{role.lower()}{{background-image:url("{src}")}}')
    for kode, warna in WARNA_TIM.items():
        aturan.append(f'.wt-{kode.lower()}{{--c:{warna}}}')
    return "\n".join(aturan)


def tanggal_panjang(iso: str) -> str:
    """'2026-08-14' -> 'Friday, 14 August 2026'."""
    try:
        d = pd.Timestamp(iso)
    except (ValueError, TypeError):
        return str(iso)
    return f"{HARI[d.weekday()]}, {d.day} {BULAN[d.month]} {d.year}"


# ---------------------------------------------------------------------------
# BAGIAN 1 — KLASEMEN
# ---------------------------------------------------------------------------

def bagian_klasemen(kl: pd.DataFrame) -> str:
    if kl.empty:
        return '<p class="catatan">No data yet.</p>'

    # Kolom "Sisa" hanya berguna di musim berjalan. Di musim yang sudah selesai
    # isinya strip semua, jadi kolomnya dihilangkan sekalian.
    ada_sisa = bool(kl.belum_main.sum())

    baris = []
    for r in kl.itertuples(index=False):
        net = int(r.selisih_game)
        sisa = (f'<td class="sisa">{int(r.belum_main) or "&ndash;"}</td>'
                if ada_sisa else "")
        baris.append(f"""
        <tr>
          <td class="rank">{int(r.peringkat)}</td>
          <td class="sel-tim">{logo(r.kode)}<div><b>{e(r.nama)}</b>
              <span class="kode">{e(r.kode)}</span></div></td>
          <td>{int(r.main)}</td>
          <td class="menang">{int(r.menang)}</td>
          <td class="kalah">{int(r.kalah)}</td>
          <td>{int(r.game_menang)} - {int(r.game_kalah)}</td>
          <td class="{'plus' if net > 0 else 'minus' if net < 0 else ''}">{net:+d}</td>
          {sisa}
        </tr>""")

    kol_sisa = "<th>Left</th>" if ada_sisa else ""
    return f"""
    <table class="tabel">
      <thead><tr>
        <th>#</th><th class="kiri">Team</th><th>Played</th>
        <th>W</th><th>L</th><th>Game W-L</th><th>Net</th>{kol_sisa}
      </tr></thead>
      <tbody>{''.join(baris)}</tbody>
    </table>"""


# ---------------------------------------------------------------------------
# BAGIAN 2 — JADWAL / HASIL
# ---------------------------------------------------------------------------

# Tinggi satu baris peringkat. HARUS sama dengan --baris di CSS: garis
# digambar di SVG dengan satuan piksel, titiknya elemen HTML yang diletakkan
# lewat calc(), dan keduanya hanya bertemu kalau angkanya satu.
BUMP_BARIS = 28


def klasemen_mingguan(laga: pd.DataFrame) -> list[tuple[int, dict]]:
    """[(pekan, {kode: (peringkat, menang, kalah)})] setelah tiap pekan.

    Peringkatnya memakai klasemen() yang sama dengan tab Klasemen — bukan
    hitungan kedua — jadi kolom terakhir grafik ini dijamin sama persis dengan
    tabel klasemen di tab sebelahnya.
    """
    selesai = laga[(laga.status == "selesai") & laga.week.notna()]
    if selesai.empty:
        return []
    hasil = []
    for w in sorted(int(x) for x in selesai.week.unique()):
        kl = klasemen(selesai[selesai.week <= w])
        hasil.append((w, {r.kode: (int(r.peringkat), int(r.menang), int(r.kalah))
                          for r in kl.itertuples(index=False)}))
    return hasil


def bagian_bump(laga: pd.DataFrame, season: int) -> str:
    """Grafik peringkat pekan demi pekan: peringkat di kiri, logo di kanan.

    Lebarnya mengikuti halaman, bukan dipatok piksel: SVG-nya dipasang
    preserveAspectRatio="none" sehingga sumbu X ikut melar, dan garisnya
    memakai vector-effect="non-scaling-stroke" supaya tebalnya tidak ikut
    melar. Titiknya elemen HTML, bukan <circle>, karena lingkaran di dalam
    SVG yang diregangkan akan jadi lonjong.
    """
    pekan = klasemen_mingguan(laga)
    if len(pekan) < 2:
        return ""

    akhir = pekan[-1][1]
    tim = sorted(akhir, key=lambda k: akhir[k][0])
    n = len(pekan)

    def fx(i: int) -> float:                 # satuan viewBox, 0..1000
        return 1000 * (i + 0.5) / n

    def fy(rank: int) -> float:              # satuan viewBox = piksel
        return BUMP_BARIS * (rank - 0.5)

    def kiri(i: int) -> str:                 # posisi CSS untuk elemen HTML
        return (f"calc(var(--kiri) + (100% - var(--kiri) - var(--kanan)) "
                f"* {(i + 0.5) / n:.5f})")

    def atas(v: float) -> str:
        return f"calc(var(--atas) + var(--baris) * {v:g})"

    jalur, titik = [], []
    for kode in tim:
        ttk = [(i, k[kode]) for i, (_, k) in enumerate(pekan) if kode in k]
        if len(ttk) < 2:
            continue
        wt = f"wt-{kode.lower()}"
        # Kurva kubik dengan kedua titik kendali di tengah-tengah secara
        # mendatar: bentuk S yang lazim untuk grafik peringkat, dan jauh lebih
        # mudah diikuti daripada garis patah ketika sembilan jalur bersilangan.
        d = f"M{fx(ttk[0][0]):.1f},{fy(ttk[0][1][0]):.1f}"
        for (i0, v0), (i1, v1) in zip(ttk, ttk[1:]):
            x0, y0, x1, y1 = fx(i0), fy(v0[0]), fx(i1), fy(v1[0])
            xm = (x0 + x1) / 2
            d += (f"C{xm:.1f},{y0:.1f} {xm:.1f},{y1:.1f} "
                  f"{x1:.1f},{y1:.1f}")
        jalur.append(f'<path class="pukul {wt}" data-tim="{e(kode)}" d="{d}"/>'
                     f'<path class="lintas {wt}" data-tim="{e(kode)}" d="{d}"/>')

        nama = TIM.get(kode, (kode,))[0]
        for i, (rank, m, kl) in ttk:
            titik.append(
                f'<span class="bump-d {wt}" data-tim="{e(kode)}" '
                f'style="left:{kiri(i)};top:{atas(rank - 0.5)}" '
                f'title="{e(nama)} &#183; W{pekan[i][0]}: #{rank} '
                f'({m}&#8211;{kl})"></span>')

    pita = "".join(f'<div class="bump-pita" style="top:{atas(i)}"></div>'
                   for i in range(len(tim)) if i % 2)
    lbl_pekan = "".join(f'<span class="bump-pekan" style="left:{kiri(i)}">W{w}</span>'
                        for i, (w, _) in enumerate(pekan))
    lbl_rank = "".join(f'<span class="bump-rank" style="top:{atas(i)}">{i + 1}</span>'
                       for i in range(len(tim)))
    lbl_tim = "".join(
        f'<span class="bump-tim wt-{kode.lower()}" data-tim="{e(kode)}" '
        f'style="top:{atas(akhir[kode][0] - 1)}">{logo(kode, "logo kecil")}'
        f'<span class="kode">{e(kode)}</span></span>' for kode in tim)

    return f"""
    <h3>Standings by week</h3>
    <p class="catatan">Where every team stood after each week &mdash; same
      numbers as the Standings tab. Point at a team to trace it; the last
      column is live.</p>
    <div class="bump" style="--n:{len(tim)}" role="img"
         aria-label="Week-by-week standings for Season {season}; the Standings
         tab shows the same data as a table">
      {pita}{lbl_pekan}{lbl_rank}
      <svg class="bump-svg" viewBox="0 0 1000 {len(tim) * BUMP_BARIS}"
           preserveAspectRatio="none" aria-hidden="true" focusable="false">
        {''.join(jalur)}
      </svg>
      {''.join(titik)}{lbl_tim}
    </div>"""


def bagian_jadwal(laga: pd.DataFrame, season: int) -> str:
    weeks = sorted(laga.week.dropna().unique())

    tombol = [f'<button class="tab-week aktif" data-week="all">All</button>']
    tombol += [f'<button class="tab-week" data-week="{int(w)}">W{int(w)}</button>'
               for w in weeks]

    blok = []
    for w in weeks:
        pekan = laga[laga.week == w]
        selesai = int((pekan.status == "selesai").sum())

        kartu = []
        for tanggal, grup in pekan.groupby("tanggal", dropna=False):
            kartu.append(f'<div class="tanggal">{e(tanggal_panjang(tanggal))}</div>')
            for m in grup.itertuples(index=False):
                main = m.status == "selesai"
                s1, s2 = int(m.skor1), int(m.skor2)
                m1 = "juara" if main and s1 > s2 else ""
                m2 = "juara" if main and s2 > s1 else ""
                skor = (f'<span class="angka {m1}">{s1}</span>'
                        f'<span class="pisah">-</span>'
                        f'<span class="angka {m2}">{s2}</span>') if main else \
                       '<span class="vs">VS</span>'
                jam = f'<div class="jam">{e(m.jam)}</div>' if pd.notna(m.jam) else ""
                pb = blok_pickban(season, "reguler", m.timestamp) if main else ""
                kartu.append(f"""
          <div class="laga {'belum' if not main else ''}">
            <div class="sisi kanan {m1}">{e(m.nama1)} {logo(m.kode1, 'logo kecil')}</div>
            <div class="tengah"><div class="skor">{skor}</div>{jam}</div>
            <div class="sisi kiri {m2}">{logo(m.kode2, 'logo kecil')} {e(m.nama2)}</div>
          </div>{pb}""")

        blok.append(f"""
      <div class="blok-week" data-week="{int(w)}">
        <h3>Week {int(w)} <span class="catatan">{selesai}/{len(pekan)} played</span></h3>
        <div class="daftar-laga">{''.join(kartu)}</div>
      </div>""")

    return (bagian_bump(laga, season)
            + '<h3>Matches</h3>'
            + f'<div class="tab-bar">{"".join(tombol)}</div>{"".join(blok)}')


# ---------------------------------------------------------------------------
# BAGIAN 3 — MATRIKS HEAD-TO-HEAD
# ---------------------------------------------------------------------------

def bagian_h2h(laga: pd.DataFrame) -> str:
    h2h = head_to_head(laga)
    urutan = sorted(set(laga.kode1) | set(laga.kode2))

    kepala = "".join(f'<th>{logo(k, "logo kecil")}</th>' for k in urutan)
    baris = []

    for baris_tim in urutan:
        sel = []
        for kolom_tim in urutan:
            if baris_tim == kolom_tim:
                sel.append('<td class="diagonal"></td>')
                continue

            pertemuan = h2h.get((baris_tim, kolom_tim), [])
            if not pertemuan:
                sel.append('<td class="kosong"><span class="strip">&ndash;</span></td>')
                continue

            menang = sum(a for a, _ in pertemuan)
            kalah = sum(b for _, b in pertemuan)
            warna = "menang" if menang > kalah else "kalah" if menang < kalah else "seri"
            rinci = ", ".join(f"{a}-{b}" for a, b in pertemuan)
            sel.append(f"""<td class="sel {warna}">
              <div class="agg">{menang}-{kalah}</div>
              <div class="rinci">{e(rinci)}</div></td>""")

        baris.append(f'<tr><th class="sisi-kiri">{logo(baris_tim, "logo kecil")}</th>'
                     f'{"".join(sel)}</tr>')

    return f"""
    <p class="catatan">Each row: total game wins against the team in the column.</p>
    <div class="gulir">
      <table class="matriks">
        <thead><tr><th class="pojok"></th>{kepala}</tr></thead>
        <tbody>{''.join(baris)}</tbody>
        <tfoot><tr><th class="pojok"></th>{kepala}</tr></tfoot>
      </table>
    </div>"""


# ---------------------------------------------------------------------------
# BAGIAN 4 — PLAYOFF
# ---------------------------------------------------------------------------

def bagian_playoff(po: pd.DataFrame, berjalan: bool) -> str:
    if po.empty:
        # Bedakan dua sebab kosong yang artinya sangat berbeda.
        alasan = ("Regular season still in progress &mdash; playoffs not played yet."
                  if berjalan else "No playoff data collected for this season.")
        return f'<p class="catatan">{alasan}</p>'

    # Nama yang dipakai di sini adalah nama PERIODE dari Liquipedia, bukan nama
    # registry. Untuk arsip historis itu yang benar: di S13 timnya betul-betul
    # bertanding sebagai "Liquid Aura", dan di S11 sebagai "Geek Slate".
    # Logonya tetap memakai kode yang sudah dinormalkan, supaya tim yang lolos
    # playoff bisa ditelusuri ke klasemen musim yang sama.
    beda_nama = sorted({
        (getattr(m, f"periode{i}"), getattr(m, f"nama{i}"))
        for m in po.itertuples(index=False) for i in (1, 2)
        if getattr(m, f"periode{i}") != getattr(m, f"nama{i}")
    })
    nota = ""
    if beda_nama:
        daftar = "; ".join(f"<b>{e(per)}</b> = {e(reg)}" for per, reg in beda_nama)
        nota = f'<p class="catatan">Names at the time: {daftar}.</p>' 

    kartu = []
    ronde_kini = None
    for m in po.itertuples(index=False):
        if m.ronde != ronde_kini:
            ronde_kini = m.ronde
            kartu.append(f'<div class="ronde">{e(m.ronde)}</div>')

        s1, s2 = int(m.skor1), int(m.skor2)
        m1 = "juara" if s1 > s2 else ""
        m2 = "juara" if s2 > s1 else ""
        fmt = f'<div class="jam">{e(m.format)}</div>' if pd.notna(m.format) else ""
        tgl = (f'<div class="jam">{e(tanggal_panjang(m.tanggal))}</div>'
               if pd.notna(m.tanggal) else "")
        final = "grand" if "grand final" in str(m.ronde).lower() else ""

        pb = blok_pickban(m.season, "playoff", m.timestamp)
        kartu.append(f"""
      <div class="laga {final}">
        <div class="sisi kanan {m1}">{e(m.periode1)} {logo(m.kode1, 'logo kecil')}</div>
        <div class="tengah">
          <div class="skor"><span class="angka {m1}">{s1}</span>
            <span class="pisah">-</span><span class="angka {m2}">{s2}</span></div>
          {fmt}{tgl}
        </div>
        <div class="sisi kiri {m2}">{logo(m.kode2, 'logo kecil')} {e(m.periode2)}</div>
      </div>{pb}""")

    return f'{nota}<div class="daftar-laga">{"".join(kartu)}</div>'


# ---------------------------------------------------------------------------
# BAGIAN 5 — MVP
# ---------------------------------------------------------------------------

# Urutan tampil penghargaan musim. Yang tidak ada di daftar ini tetap
# ditampilkan, di bawah, sesuai urutan aslinya di Liquipedia.
URUT_AWARD = [
    "Finals MVP", "Regular Season MVP", "Rookie Of The Season", "Rising Star",
    "Most Improved Player", "Dream Team", "First Team Winner",
    "Second Team Winner", "Best Coach", "Best Coaching Team",
    "Best Talent ID", "Best Talent EN",
]


def _orang(r) -> str:
    """Satu penerima award: logo tim (kalau ada) + nama."""
    lg = logo(r.kode, "logo kecil") if pd.notna(r.kode) else ""
    return f'<span class="penerima">{lg}{e(r.pemain)}</span>'


def bagian_award(season: int) -> str:
    aw = awards()
    if aw.empty or not (aw.season == season).any():
        return ('<p class="catatan">No award data for this season. The current '
                'season comes from id-mpl.com, which has no award table.</p>')

    sub = aw[aw.season == season]

    def jenis(label: str) -> str:
        if re.match(r"Week \d+ MVP", label):
            return "mvp"
        if re.match(r"Week \d+ Best Rookie", label):
            return "rookie"
        if re.match(r"Team of [Tt]he Week", label):
            return "totw"
        return "musim"

    def pekan(label: str):
        m = re.search(r"(\d+)", label)
        return int(m.group(1)) if m else 0

    # --- 1. Penghargaan musim -------------------------------------------
    musim_lbl = [l for l in sub.award.dropna().unique() if jenis(l) == "musim"]
    musim_lbl.sort(key=lambda l: (URUT_AWARD.index(l) if l in URUT_AWARD
                                  else len(URUT_AWARD)))
    baris = []
    for label in musim_lbl:
        grup = sub[sub.award == label]
        orang = " ".join(_orang(r) for r in grup.itertuples(index=False))
        hadiah = next((r.hadiah for r in grup.itertuples(index=False)
                       if pd.notna(r.hadiah)), None)
        sorot = ' class="sorot"' if label in ("Finals MVP", "Regular Season MVP") else ''
        baris.append(f'<tr{sorot}><td class="kiri lbl">{e(label)}</td>'
                     f'<td class="kiri">{orang}</td>'
                     f'<td class="sisa">{e(hadiah) if hadiah else "&ndash;"}</td></tr>')

    # Musim berjalan belum punya penghargaan akhir (semuanya masih TBD di
    # Liquipedia, dan baris TBD memang tidak disimpan). Jangan tampilkan judul
    # dengan tabel kosong di bawahnya.
    blok_musim = f"""
    <h3>Season Awards</h3>
    <table class="tabel award">
      <thead><tr><th class="kiri">Award</th><th class="kiri">Winner</th>
      <th>Prize</th></tr></thead>
      <tbody>{''.join(baris)}</tbody>
    </table>""" if baris else ""

    # --- 2. Penghargaan mingguan ----------------------------------------
    mvp_w = {pekan(r.award): r for r in sub.itertuples(index=False)
             if jenis(r.award or "") == "mvp"}
    rook_w = {pekan(r.award): r for r in sub.itertuples(index=False)
              if jenis(r.award or "") == "rookie"}
    blok_mingguan = ""
    if mvp_w or rook_w:
        semua_pekan = sorted(set(mvp_w) | set(rook_w))
        br = []
        for w in semua_pekan:
            m = _orang(mvp_w[w]) if w in mvp_w else '<span class="strip">&ndash;</span>'
            r = _orang(rook_w[w]) if w in rook_w else '<span class="strip">&ndash;</span>'
            br.append(f'<tr><td class="lbl">Week {w}</td>'
                      f'<td class="kiri">{m}</td><td class="kiri">{r}</td></tr>')
        blok_mingguan = f"""
    <h3>Weekly Awards</h3>
    <table class="tabel award">
      <thead><tr><th>Week</th><th class="kiri">MVP</th>
      <th class="kiri">Best Rookie</th></tr></thead>
      <tbody>{''.join(br)}</tbody>
    </table>"""

    # --- 3. Team of The Week --------------------------------------------
    totw = sub[sub.award.map(lambda l: jenis(l or "") == "totw")]
    blok_totw = ""
    if not totw.empty:
        per_pekan = {}
        for r in totw.itertuples(index=False):
            per_pekan.setdefault(pekan(r.award), []).append(r)
        kartu = []
        for w in sorted(per_pekan):
            isi = " ".join(_orang(r) for r in per_pekan[w])
            kartu.append(f'<tr><td class="lbl">Week {w}</td>'
                         f'<td class="kiri">{isi}</td></tr>')
        blok_totw = f"""
    <h3>Team of The Week</h3>
    <table class="tabel award">
      <thead><tr><th>Week</th><th class="kiri">Players</th></tr></thead>
      <tbody>{''.join(kartu)}</tbody>
    </table>"""

    return blok_musim + blok_mingguan + blok_totw


# ---------------------------------------------------------------------------
# BAGIAN 6 — ROSTER
# ---------------------------------------------------------------------------

IKON_ROLE = {"EXP": "EXP", "Jungle": "JGL", "Mid": "MID",
             "Gold": "GOLD", "Roam": "ROAM", "Flex": "FLEX"}


def ikon_role(role: str) -> str:
    """Ikon role sebagai <span> ber-background, dengan label teks di sampingnya."""
    if not role or pd.isna(role):
        return ""
    label = IKON_ROLE.get(role, role)
    kelas = f"ir-{role.lower()}" if data_uri(IKON_BERKAS.get(role, "")) else ""
    ikon = f'<span class="ikon-role {kelas}" aria-hidden="true"></span>' if kelas else ""
    return f'{ikon}<span class="nama-role">{e(label)}</span>' 


def bagian_roster(season: int) -> str:
    """Kartu roster per tim: lima pemain inti, sisanya diringkas di kaki kartu.

    Versi sebelumnya menaruh Subs/Former/Staff sebagai baris tabel tersendiri
    lengkap dengan lencana role per orang — informasinya benar tapi ramai.
    Sekarang yang menonjol hanya lima pemain inti; selebihnya jadi satu baris
    teks kecil.
    """
    rs = roster()
    if rs.empty or not (rs.season == season).any():
        return '<p class="catatan">No roster data for this season.</p>'

    sub = rs[rs.season == season]
    kartu = []

    for kode, grup_tim in sub.groupby("kode", sort=False):
        nama_periode = grup_tim.tim.iloc[0]

        inti = (grup_tim[grup_tim.grup.str.lower() == "main"]
                .set_index("role").reindex(URUT_ROLE).reset_index())
        baris = []
        for r in inti.itertuples(index=False):
            if pd.isna(r.pemain):
                continue
            negara = f' title="{e(r.negara)}"' if pd.notna(r.negara) else ""
            baris.append(f'<tr><td class="role">{ikon_role(r.role)}</td>'
                         f'<td class="kiri"><span{negara}>{e(r.pemain)}</span></td></tr>')

        kaki = []
        for label, kunci in (("Subs", "subs"), ("Former", "former"),
                             ("Staff", "staff")):
            bagian = grup_tim[grup_tim.grup.str.lower() == kunci]
            if bagian.empty:
                continue
            nama = ", ".join(
                e(r.pemain) + (' <span class="tanda">DNP</span>'
                               if str(r.catatan) == "DNP" else "")
                for r in bagian.itertuples(index=False))
            kaki.append(f'<div><span class="label">{label}</span>{nama}</div>')

        kartu.append(f"""
      <div class="kartu-tim">
        <div class="kepala-tim">{logo(kode)}<div><b>{e(TIM.get(kode, (kode,))[0])}</b>
          <span class="kode">{e(nama_periode)}</span></div></div>
        <table class="tabel roster"><tbody>{''.join(baris)}</tbody></table>
        {f'<div class="kaki-tim">{"".join(kaki)}</div>' if kaki else ''}
      </div>""")

    return f'<div class="grid-tim">{"".join(kartu)}</div>'


# ---------------------------------------------------------------------------
# PICK & BAN
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _peta_pickban() -> dict:
    return pickban_per_laga(picks_bans())


def blok_pickban(season: int, stage: str, timestamp) -> str:
    """Rincian hero pick & ban satu laga, disembunyikan di balik <details>.

    Dipakai <details> supaya tidak membanjiri tampilan: satu laga bisa berisi
    sampai tujuh game x dua tim x sepuluh hero. Yang ingin melihat tinggal
    membuka; yang tidak, jadwalnya tetap ringkas.
    """
    if pd.isna(timestamp):
        return ""
    baris = _peta_pickban().get((int(season), stage, int(timestamp)))
    if not baris:
        return ""

    per_game: dict = {}
    for r in baris:
        per_game.setdefault(int(r.game), []).append(r)

    isi = []
    for game in sorted(per_game):
        tim = per_game[game]
        durasi = next((t.durasi for t in tim if pd.notna(t.durasi)), None)
        sisi = []
        for t in tim:
            pick = ", ".join(e(getattr(t, f"pick{i}")) for i in range(1, 6)
                             if pd.notna(getattr(t, f"pick{i}")))
            ban = ", ".join(e(getattr(t, f"ban{i}")) for i in range(1, 6)
                            if pd.notna(getattr(t, f"ban{i}")))
            sisi.append(
                f'<tr><td class="pb-tim">{logo(t.kode, "logo kecil")}'
                f'{e(t.kode)}</td>'
                f'<td class="pb-pick">{pick or "&ndash;"}</td>'
                f'<td class="pb-ban">{ban or "&ndash;"}</td></tr>')
        judul = f"Game {game}" + (f' &middot; {e(durasi)}' if durasi else "")
        isi.append(f'<div class="pb-judul">{judul}</div>'
                   f'<table class="pb-tabel"><tbody>{"".join(sisi)}</tbody></table>')

    return (f'<details class="pb"><summary>Pick &amp; Ban '
            f'<span class="catatan">{len(per_game)} games</span></summary>'
            f'<div class="pb-isi">{"".join(isi)}</div></details>')


# ---------------------------------------------------------------------------
# BAGIAN 7 — STATISTIK
# ---------------------------------------------------------------------------

def _daftar_hero(pasangan) -> str:
    """Rangkaian 'Hero xN' yang ringkas, untuk kolom top pick/ban."""
    if not pasangan:
        return '<span class="strip">&ndash;</span>'
    return " ".join(f'<span class="hero-chip">{e(h)}'
                    f'<span class="hero-n">{n}</span></span>' for h, n in pasangan)


def bagian_statistik(season: int) -> str:
    st = statistik_tim(season)
    hs = hero_stats()
    hs = hs[hs.season == season] if not hs.empty else hs

    if st.empty and hs.empty:
        return '<p class="catatan">No stats for this season.</p>'

    bagian = []

    # --- tabel tim ---
    if not st.empty:
        ada_pb = any(r.top_pick or r.top_ban for r in st.itertuples(index=False))
        baris = []
        for r in st.itertuples(index=False):
            kol_pb = (f'<td class="kiri">{_daftar_hero(r.top_pick)}</td>'
                      f'<td class="kiri">{_daftar_hero(r.top_ban)}</td>') if ada_pb else ""
            baris.append(f"""
        <tr>
          <td class="sel-logo">{logo(r.kode)}</td>
          <td>{r.menang}-{r.kalah}</td>
          <td><b>{r.win_rate}%</b></td>
          <td class="sisa">{r.game_win_rate}%</td>
          {kol_pb}
        </tr>""")

        kepala_pb = ('<th class="kiri">Top 5 Picks</th>'
                     '<th class="kiri">Top 5 Bans</th>') if ada_pb else ""
        catatan = ('<p class="catatan">Win rate from matches played. Picks and '
                   'bans cover the regular season through the final.</p>'
                   if ada_pb else
                   '<p class="catatan">Win rate from matches played. No pick/ban '
                   'data for this season.</p>')
        bagian.append(f"""
    <h3>By Team</h3>{catatan}
    <table class="tabel statistik">
      <thead><tr><th>Team</th><th>W-L</th><th>Win Rate</th>
      <th>Game WR</th>{kepala_pb}</tr></thead>
      <tbody>{''.join(baris)}</tbody>
    </table>""")

    # --- tabel hero ---
    if not hs.empty:
        rel = hero_relasi()
        rel = rel[rel.season == season] if not rel.empty else rel
        relasi_hero: dict = {}
        for r in rel.itertuples(index=False):
            relasi_hero.setdefault((r.hero, r.jenis), []).append(r)

        urut = hs.sort_values(["picks", "bans"], ascending=False)
        baris = []
        for i, r in enumerate(urut.itertuples(index=False), 1):
            if not (r.picks or r.bans):
                continue
            wr = f"{r.win_rate:.1f}%" if pd.notna(r.win_rate) else "&ndash;"
            kelas = ("plus" if pd.notna(r.win_rate) and r.win_rate >= 55
                     else "minus" if pd.notna(r.win_rate) and r.win_rate <= 45 else "")

            def sel_relasi(jenis, naik):
                """Satu sel: yang paling menonjol, lalu kelimanya bila diklik.

                Untuk rekan diurutkan dari win rate TERTINGGI (rekan terbaik),
                untuk lawan dari TERENDAH (lawan terberat).
                """
                daftar = sorted(
                    relasi_hero.get((r.hero, jenis), []),
                    key=lambda x: (x.win_rate if pd.notna(x.win_rate) else 999),
                    reverse=not naik)
                if not daftar:
                    return '<span class="strip">&ndash;</span>'
                isi = "".join(
                    f'<tr><td class="kiri">{e(x.hero_lain)}</td>'
                    f'<td class="sisa">{int(x.jumlah)}x</td>'
                    f'<td class="menang">{int(x.menang)}</td>'
                    f'<td class="kalah">{int(x.kalah)}</td>'
                    f'<td class="{"plus" if x.win_rate >= 55 else "minus" if x.win_rate <= 45 else ""}">'
                    f'{x.win_rate:.1f}%</td></tr>' for x in daftar)
                atas = daftar[0]
                return (f'<details class="pb"><summary>{e(atas.hero_lain)} '
                        f'<span class="catatan">{atas.win_rate:.0f}%</span></summary>'
                        f'<table class="pb-tabel lawan"><tbody>{isi}</tbody></table></details>')

            sel_rekan = sel_relasi("rekan", naik=False)
            sel_lawan = sel_relasi("lawan", naik=True)

            baris.append(f"""
        <tr>
          <td class="rank">{i}</td>
          <td class="kiri"><b>{e(r.hero)}</b></td>
          <td>{int(r.picks) if pd.notna(r.picks) else 0}</td>
          <td class="sisa">{int(r.bans) if pd.notna(r.bans) else 0}</td>
          <td class="menang">{int(r.menang) if pd.notna(r.menang) else 0}</td>
          <td class="kalah">{int(r.kalah) if pd.notna(r.kalah) else 0}</td>
          <td class="{kelas}">{wr}</td>
          <td class="kiri lawan-sel">{sel_rekan}</td>
          <td class="kiri lawan-sel">{sel_lawan}</td>
        </tr>""")
        bagian.append(f"""
    <h3>By Hero <span class="catatan">{len(baris)} heroes</span></h3>
    <p class="catatan">Last two columns: the teammate with the highest win
      rate and the opponent with the lowest. Click for the top five.</p>
    <div class="gulir">
    <table class="tabel hero">
      <thead><tr><th>#</th><th class="kiri">Hero</th><th>Picks</th><th>Bans</th>
      <th>W</th><th>L</th><th>WR</th>
      <th class="kiri">Best Teammate</th>
      <th class="kiri">Toughest Opponent</th></tr></thead>
      <tbody>{''.join(baris)}</tbody>
    </table></div>""")

    return "".join(bagian)


# ---------------------------------------------------------------------------
# CSS & JS
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# BAGIAN 8 & 9 — SEMUA PEMAIN / SEMUA TIM (lintas musim)
# ---------------------------------------------------------------------------
#
# Dua bagian ini sengaja berada DI LUAR panel musim. Isinya memang lintas
# musim, dan angka KDA/objektifnya cuma ada untuk musim berjalan — kalau
# dipasang sebagai tab di dalam tiap musim, delapan dari sembilan panel akan
# menampilkan tabel kosong.
#
# Penyaring musim bekerja dengan MENUKAR BARIS, bukan menghitung ulang di
# browser. Tiap entitas dirender dua kali: sekali sebagai rekap seluruh musim
# (data-ruang="all") dan sekali untuk tiap musim yang dia ikuti
# (data-ruang="13"). Jadi waktu disaring ke S13, kolom Tim, Role, Gelar, dan
# Award benar-benar menampilkan angka S13 — bukan angka seumur karier yang
# kebetulan barisnya lolos saringan.


def _kotak_cari(target: str, petunjuk: str, jumlah: int, satuan: str,
                musim: list[int]) -> str:
    """Penyaring musim + kotak pencarian untuk satu tabel.

    Pencariannya membaca atribut `data-cari` tiap baris — string siap-pakai
    yang sudah dirakit di sini, bukan hasil membaca ulang isi sel di browser.
    Dengan begitu logo tim (yang tidak punya teks) tetap ikut tercari lewat
    kode dan nama panjang timnya.
    """
    tombol = ['<button class="tab-musim aktif" data-ruang="all">All seasons</button>']
    tombol += [f'<button class="tab-musim" data-ruang="{m}">S{m}</button>'
               for m in musim]
    return (
        # Penyaring musim dan kotak cari dibungkus satu wadah supaya
        # bisa dibuat menempel di atas layar sebagai satu kesatuan.
        f'<div class="alat">'
        f'<div class="tab-bar" data-target="{target}">{"".join(tombol)}</div>'
        f'<div class="cari-bar">'
        f'<input type="search" class="cari" data-target="{target}" '
        f'placeholder="{e(petunjuk)}" aria-label="{e(petunjuk)}" '
        f'autocomplete="off" spellcheck="false">'
        f'<span class="cari-info" data-target="{target}" data-satuan="{e(satuan)}">'
        f'{jumlah} {e(satuan)}</span>'
        f'</div></div>')


def _rentang_musim(musim: list[int]) -> str:
    """[10,11,12] -> 'S10–S12'; [10,12] -> 'S10, S12' (yang bolong tetap kelihatan)."""
    if not musim:
        return "&mdash;"
    m = sorted(musim)
    bagian, awal, akhir = [], m[0], m[0]
    for x in m[1:]:
        if x == akhir + 1:
            akhir = x
        else:
            bagian.append((awal, akhir))
            awal = akhir = x
    bagian.append((awal, akhir))
    return ", ".join(f"S{a}" if a == b else f"S{a}&ndash;S{b}" for a, b in bagian)


def _angka(v, fmt="{:.0f}") -> str:
    return "<span class='sisa'>&mdash;</span>" if v is None or pd.isna(v) \
        else fmt.format(v)


def _bintang(n: int, judul: str) -> str:
    return (f'<span class="gelar" title="{e(judul)}">{"&#9733;" * int(n)}</span>'
            if n else "<span class='sisa'>&mdash;</span>")


# --- pemain ----------------------------------------------------------------

def data_semua_pemain() -> pd.DataFrame:
    """Baris rekap + baris per musim untuk tiap pemain.

    Kuncinya `pemain_id` (slug halaman Liquipedia), bukan nama tampilan —
    alasan yang sama seperti di seluruh proyek ini: nama pemain berubah ejaan
    antar halaman dan antar musim, slug-nya tidak.
    """
    rs = roster()
    if rs.empty:
        return pd.DataFrame()

    r = rs[rs.grup.isin(["Main", "Subs"]) & rs.kode.notna()].copy()
    r["pid"] = [pemain_id(l, n) for l, n in zip(r.link, r.pemain)]
    r = r[r.pid.notna()]

    # Award perorangan (MVP, Team of the Week, dsb) — dipisah dari gelar juara.
    aw = awards()
    n_award: dict[tuple[str, int], int] = {}
    if not aw.empty:
        for l, n, s in zip(aw.link, aw.pemain, aw.season):
            pid = pemain_id(l, n)
            if pid:
                n_award[(pid, int(s))] = n_award.get((pid, int(s)), 0) + 1

    # Gelar juara: milik regu, bukan perorangan.
    jp = juara_pemain()
    gelar = {(pemain_id(l, n), int(s)) for s, l, n in
             zip(jp.season, jp.link, jp.pemain)} if not jp.empty else set()

    sp = stat_pemain()
    stat = {}
    if not sp.empty:
        for b in sp.itertuples(index=False):
            stat[(pemain_id(b.link, b.nama_resmi) or b.pemain,
                  int(b.season))] = b

    baris = []
    for pid, g in r.groupby("pid", sort=False):
        g = g.sort_values("season")
        akhir = g.iloc[-1]
        musim = sorted(set(int(x) for x in g.season))
        peran = [x for x in URUT_ROLE if x in set(g.role.dropna())]
        negara = next((x for x in reversed(list(g.negara)) if isinstance(x, str)), None)
        n_gelar = sum(1 for m in musim if (pid, m) in gelar)

        # --- baris rekap seluruh karier
        s = stat.get((pid, max(musim)))
        baris.append({
            "ruang": "all", "pid": pid, "nama": akhir.pemain, "kode": akhir.kode,
            "tim_semua": sorted(set(g.kode)), "role": peran[0] if peran else None,
            "role_semua": peran, "negara": negara, "musim": musim,
            "n_musim": len(musim), "musim_akhir": musim[-1], "grup": None,
            "gelar": n_gelar, "award": sum(n_award.get((pid, m), 0) for m in musim),
            "main": getattr(s, "main", None), "kill": getattr(s, "kill", None),
            "mati": getattr(s, "mati", None), "assist": getattr(s, "assist", None),
            "kda": getattr(s, "kda", None),
            "partisipasi": getattr(s, "partisipasi", None),
        })

        # --- satu baris untuk tiap musim yang dia ikuti
        for m, gm in g.groupby("season"):
            gm = gm.iloc[0]
            s = stat.get((pid, int(m)))
            peran_m = [x for x in URUT_ROLE if x in set(gm_role for gm_role
                                                        in g[g.season == m].role.dropna())]
            baris.append({
                "ruang": str(int(m)), "pid": pid, "nama": gm.pemain,
                "kode": gm.kode, "tim_semua": [gm.kode],
                "role": peran_m[0] if peran_m else None, "role_semua": peran_m,
                "negara": gm.negara if isinstance(gm.negara, str) else negara,
                "musim": [int(m)], "n_musim": 1, "musim_akhir": int(m),
                "grup": gm.grup,
                "gelar": 1 if (pid, int(m)) in gelar else 0,
                "award": n_award.get((pid, int(m)), 0),
                "main": getattr(s, "main", None), "kill": getattr(s, "kill", None),
                "mati": getattr(s, "mati", None), "assist": getattr(s, "assist", None),
                "kda": getattr(s, "kda", None),
                "partisipasi": getattr(s, "partisipasi", None),
            })

    # Pemain yang punya statistik tapi belum tercatat di roster Liquipedia.
    # Daftar ini dibangun dari roster, jadi tanpa langkah ini mereka hilang
    # sama sekali — padahal mereka benar-benar bermain. Untuk daftar yang
    # namanya "Semua Pemain", itu tidak boleh.
    sudah = {(b["pid"], b["musim"][0]) for b in baris if b["ruang"] != "all"}
    tambahan: dict[str, list] = {}
    for (pid, m), b in stat.items():
        if (pid, m) in sudah:
            continue
        rekor = {
            "ruang": str(m), "pid": pid, "nama": b.pemain, "kode": b.kode,
            "tim_semua": [b.kode], "role": None, "role_semua": [],
            # NaN itu truthy — tanpa isinstance, pemain tanpa data negara
            # akan tampil bernegara "nan".
            "negara": b.negara if isinstance(b.negara, str) else None,
            "musim": [m], "n_musim": 1, "musim_akhir": m,
            "grup": None, "gelar": 0, "award": n_award.get((pid, m), 0),
            "main": b.main, "kill": getattr(b, "kill"), "mati": b.mati,
            "assist": b.assist, "kda": b.kda, "partisipasi": b.partisipasi,
        }
        baris.append(rekor)
        tambahan.setdefault(pid, []).append(rekor)

    for pid, daftar in tambahan.items():
        pertama = daftar[0]
        baris.append({**pertama, "ruang": "all",
                      "musim": sorted(d["musim"][0] for d in daftar),
                      "n_musim": len(daftar),
                      "award": sum(d["award"] for d in daftar)})

    df = pd.DataFrame(baris)
    return df.sort_values(["ruang", "gelar", "musim_akhir", "n_musim", "nama"],
                          ascending=[True, False, False, False, True]).reset_index(drop=True)


def bagian_semua_pemain(musim_tersedia: list[int]) -> str:
    df = data_semua_pemain()
    if df.empty:
        return '<p class="catatan">No roster data yet.</p>'

    rekap = df[df.ruang == "all"]
    kepala = ("<tr><th></th><th class='kiri'>Player</th><th class='kiri'>Role</th>"
              "<th class='kiri'>Seasons</th><th>Titles</th><th>Awards</th>"
              "<th>GP</th><th>K</th><th>D</th><th>A</th><th>KDA</th><th>KP</th></tr>")

    baris = []
    for r in df.itertuples(index=False):
        nama_tim = TIM.get(r.kode, (r.kode,))[0]
        cari = " ".join(str(x) for x in (
            r.nama, r.pid, r.kode, nama_tim, r.role or "",
            r.negara if isinstance(r.negara, str) else "",
            " ".join(r.role_semua), " ".join(r.tim_semua),
            " ".join(f"S{m}" for m in r.musim))).lower()

        lain = [t for t in r.tim_semua if t != r.kode]
        tanda = (f'<span class="tanda" title="Also played for: '
                 f'{e(", ".join(lain))}">+{len(lain)}</span>') if lain else ""
        if r.grup == "Subs":
            tanda += '<span class="tanda cadangan" title="Substitute">sub</span>'
        neg = (f'<span class="negara">{e(r.negara)}</span>'
               if isinstance(r.negara, str) and r.negara else "")
        judul_gelar = (f"{r.gelar} titles" if r.ruang == "all"
                       else "Champion this season")

        baris.append(
            f'<tr data-ruang="{r.ruang}" data-cari="{e(cari)}">'
            f'<td class="rank"></td>'
            f'<td class="kiri"><span class="sel-tim">{logo(r.kode, "logo kecil")}'
            f'<span><b>{e(r.nama)}</b>{neg}{tanda}'
            f'<span class="kode">{e(r.kode)}</span></span></span></td>'
            f'<td class="kiri sel-role">{ikon_role(r.role) if r.role else ""}</td>'
            f'<td class="kiri nowrap">{_rentang_musim(r.musim)}'
            + (f'<span class="kode">{r.n_musim} seasons</span>'
               if r.ruang == "all" else "")
            + f'</td>'
            f'<td>{_bintang(r.gelar, judul_gelar)}</td>'
            f'<td>{r.award if r.award else "<span class=\'sisa\'>&mdash;</span>"}</td>'
            f'<td>{_angka(r.main)}</td><td>{_angka(r.kill)}</td>'
            f'<td>{_angka(r.mati)}</td><td>{_angka(r.assist)}</td>'
            f'<td><b>{_angka(r.kda, "{:.2f}")}</b></td>'
            f'<td>{_angka(r.partisipasi, "{:.0f}%")}</td></tr>')

    return (
        f'<p class="catatan">{len(rekap)} players across nine seasons. '
        f'<b>Titles</b> belong to the squad, so subs count; <b>Awards</b> are '
        f'individual. GP&ndash;KP cover the current season only. '
        f'Pick a season to narrow it; click a column to sort.</p>'
        + _kotak_cari("pemain", "Search name, team, role, or country…",
                      len(rekap), "players", musim_tersedia)
        + f'<div class="gulir"><table class="tabel daftar" id="tabel-pemain">'
          f'<thead>{kepala}</thead><tbody>{"".join(baris)}</tbody></table></div>')


# --- tim -------------------------------------------------------------------

def data_semua_tim() -> pd.DataFrame:
    """Baris rekap + baris per musim untuk tiap tim."""
    laga = semua_laga()
    if laga.empty:
        return pd.DataFrame()

    po_semua = playoff()
    champ = juara(po_semua)
    hp = hasil_playoff(po_semua)
    nasib = {(int(b.season), b.kode): b for b in hp.itertuples(index=False)} \
        if not hp.empty else {}

    st = stat_tim()
    objektif = {(int(b.season), b.kode): b for b in st.itertuples(index=False)} \
        if not st.empty else {}

    per_musim: dict[str, list] = {}
    baris = []
    for s in sorted(laga.season.unique()):
        s = int(s)
        for r in klasemen(laga[laga.season == s]).itertuples(index=False):
            o = objektif.get((s, r.kode))
            n = nasib.get((s, r.kode))
            rekor = {
                "ruang": str(s), "kode": r.kode, "nama": TIM.get(r.kode, (r.kode,))[0],
                "musim": [s], "n_musim": 1, "main": int(r.main),
                "menang": int(r.menang), "kalah": int(r.kalah),
                "win_rate": round(100 * r.menang / r.main, 1) if r.main else None,
                "peringkat": int(r.peringkat), "gelar": 1 if champ.get(s) == r.kode else 0,
                "lolos": 1 if n is not None else 0, "n_musim_lolos": 1 if n else 0,
                "playoff_teks": (n.hasil if n is not None else None),
                "playoff_urut": (n.peringkat_playoff if n is not None else None),
                "kill": getattr(o, "kill", None), "mati": getattr(o, "mati", None),
                "assist": getattr(o, "assist", None), "gold": getattr(o, "gold", None),
                "damage": getattr(o, "damage", None), "lord": getattr(o, "lord", None),
                "turtle": getattr(o, "turtle", None), "tower": getattr(o, "tower", None),
            }
            baris.append(rekor)
            per_musim.setdefault(r.kode, []).append(rekor)

    # --- baris rekap seluruh musim
    for kode, daftar in per_musim.items():
        main = sum(d["main"] for d in daftar)
        lolos = sum(d["lolos"] for d in daftar)
        o = objektif.get((max(d["musim"][0] for d in daftar), kode))
        # Musim yang playoff-nya belum dimainkan tidak boleh dihitung sebagai
        # "gagal lolos" — penyebutnya hanya musim yang playoff-nya sudah ada.
        musim_ada_po = [d for d in daftar if not hp.empty
                        and (hp.season == d["musim"][0]).any()]
        baris.append({
            "ruang": "all", "kode": kode, "nama": TIM.get(kode, (kode,))[0],
            "musim": sorted(d["musim"][0] for d in daftar), "n_musim": len(daftar),
            "main": main, "menang": sum(d["menang"] for d in daftar),
            "kalah": sum(d["kalah"] for d in daftar),
            "win_rate": round(100 * sum(d["menang"] for d in daftar) / main, 1)
            if main else None,
            "peringkat": min(d["peringkat"] for d in daftar),
            "gelar": sum(d["gelar"] for d in daftar),
            "lolos": lolos, "n_musim_lolos": len(musim_ada_po),
            "playoff_teks": f"{lolos}/{len(musim_ada_po)}" if musim_ada_po else None,
            "playoff_urut": lolos,
            "kill": getattr(o, "kill", None), "mati": getattr(o, "mati", None),
            "assist": getattr(o, "assist", None), "gold": getattr(o, "gold", None),
            "damage": getattr(o, "damage", None), "lord": getattr(o, "lord", None),
            "turtle": getattr(o, "turtle", None), "tower": getattr(o, "tower", None),
        })

    df = pd.DataFrame(baris)
    # Baris rekap diurut berdasar gelar lalu win rate; baris satu musim mengikuti
    # KLASEMEN musim itu, supaya urutannya sama dengan tab Klasemen dan tidak
    # terbaca seperti peringkat playoff yang bukan-bukan.
    df["_urut"] = [0 if b.ruang == "all" else b.peringkat
                   for b in df.itertuples(index=False)]
    return df.sort_values(["ruang", "_urut", "gelar", "win_rate"],
                          ascending=[True, True, False, False]
                          ).drop(columns=["_urut"]).reset_index(drop=True)


def bagian_semua_tim(musim_tersedia: list[int]) -> str:
    df = data_semua_tim()
    if df.empty:
        return '<p class="catatan">No match data yet.</p>'

    rekap = df[df.ruang == "all"]
    kepala = ("<tr><th></th><th class='kiri'>Team</th><th class='kiri'>Seasons</th>"
              "<th class='kiri'>Playoffs</th><th>Titles</th>"
              "<th>Played</th><th>W</th><th>L</th><th>WR</th>"
              "<th>Kill</th><th>Death</th><th>Assist</th><th>Gold</th>"
              "<th>Damage</th><th>Lord</th><th>Turtle</th><th>Tower</th></tr>")

    baris = []
    for r in df.itertuples(index=False):
        cari = " ".join(str(x) for x in (
            r.kode, r.nama, r.playoff_teks or "",
            # Kata kuncinya sengaja tidak saling memuat: "not qualified"
            # akan ikut terjaring waktu orang mengetik "qualified".
            "made playoffs" if r.lolos else "missed playoffs",
            " ".join(f"S{m}" for m in r.musim))).lower()

        # pandas mengubah None jadi NaN begitu nilainya masuk kolom bersama
        # string, jadi `is None` tidak pernah kena dan selnya tercetak "nan".
        if r.playoff_teks is None or pd.isna(r.playoff_teks):
            po_sel = '<span class="sisa">&mdash;</span>'
        elif r.ruang == "all":
            po_sel = (f'<b>{e(r.playoff_teks)}</b>'
                      f'<span class="kode">seasons qualified</span>')
        else:
            kelas = "juara" if r.playoff_teks == "Champion" else ""
            po_sel = (f'<span class="lencana {kelas}">{e(r.playoff_teks)}</span>')

        sub = (f'{e(r.kode)} &middot; best finish #{r.peringkat}'
               if r.ruang == "all" else f'{e(r.kode)} &middot; rank #{r.peringkat}')
        judul_gelar = (f"{r.gelar} titles" if r.ruang == "all"
                       else "Champion this season")

        baris.append(
            f'<tr data-ruang="{r.ruang}" data-cari="{e(cari)}">'
            f'<td class="rank"></td>'
            f'<td class="kiri"><span class="sel-tim">{logo(r.kode, "logo kecil")}'
            f'<span><b>{e(r.nama)}</b><span class="kode">{sub}</span></span></span></td>'
            f'<td class="kiri nowrap">{_rentang_musim(r.musim)}'
            + (f'<span class="kode">{r.n_musim} seasons</span>'
               if r.ruang == "all" else "")
            + f'</td>'
            f'<td class="kiri nowrap">{po_sel}</td>'
            f'<td>{_bintang(r.gelar, judul_gelar)}</td>'
            f'<td>{r.main}</td>'
            f'<td class="menang">{r.menang}</td><td class="kalah">{r.kalah}</td>'
            f'<td><b>{_angka(r.win_rate, "{:.1f}%")}</b></td>'
            f'<td>{_angka(r.kill, "{:,.0f}")}</td><td>{_angka(r.mati, "{:,.0f}")}</td>'
            f'<td>{_angka(r.assist, "{:,.0f}")}</td><td>{_angka(r.gold, "{:,.0f}")}</td>'
            f'<td>{_angka(r.damage, "{:,.0f}")}</td><td>{_angka(r.lord, "{:,.0f}")}</td>'
            f'<td>{_angka(r.turtle, "{:,.0f}")}</td>'
            f'<td>{_angka(r.tower, "{:,.0f}")}</td></tr>')

    return (
        f'<p class="catatan">{len(rekap)} teams across S10&ndash;S18. '
        f'<b>Playoffs</b> counts seasons qualified out of the seasons whose '
        f'playoffs have been played; pick one season to see how far a team went. '
        f'Played&ndash;WR come from every regular-season match; Kill&ndash;Tower '
        f'cover the current season only.</p>'
        + _kotak_cari("tim", "Search team name or code…", len(rekap), "teams",
                      musim_tersedia)
        + f'<div class="gulir"><table class="tabel daftar" id="tabel-tim">'
          f'<thead>{kepala}</thead><tbody>{"".join(baris)}</tbody></table></div>')


CSS = """
:root{
  --bg:#0e1015; --panel:#191c24; --garis:#2c2f3b; --garis-halus:#22252f;
  --teks:#eceef4; --redup:#9ba0b0; --samar:#717687;
  --sorot:#232735;               /* baris yang sedang ditunjuk kursor */
  --zebra:rgba(255,255,255,.021);
  --kepala:#171a23;              /* latar judul kolom yang menempel di atas */
  --hijau:#4ade80; --merah:#f87171; --emas:#deb13c; --aksen:#e01e3c;
}
*{box-sizing:border-box}
html{background:var(--bg)}
/* tabular-nums menyamakan lebar tiap digit. Tanpa itu "1" lebih sempit dari
   "8" dan kolom angka jadi bergerigi — paling terasa di tabel statistik yang
   isinya memang angka semua. */
body{margin:0;background:transparent;color:var(--teks);position:relative;
     font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     font-variant-numeric:tabular-nums}
/* Latar dibuat dari dua lapisan, dan pembagiannya disengaja.

   ::after ikut layar dan isinya cuma gradien tegak yang sangat pendek
   (#13151d ke #0b0c11, beda sekitar delapan nilai RGB). Gunanya supaya tidak
   ada bagian halaman yang benar-benar hitam polos; karena rentangnya sependek
   itu, kontras teks di satu layar praktis tidak berubah.

   ::before yang berwarna justru TIDAK ikut layar — tingginya dipatok 760px
   dari puncak halaman, jadi begitu kamu menggulir masuk ke area tabel dia
   hilang sama sekali. Tidak ada gradien yang lewat di belakang angka. */
body::after{content:"";position:fixed;inset:0;z-index:-2;pointer-events:none;
  background:linear-gradient(180deg,#13151d 0,#0e1015 40%,#0b0c11 100%)}
body::before{content:"";position:absolute;inset:0 0 auto;height:760px;z-index:-1;
  pointer-events:none;background:
    radial-gradient(880px 480px at 1% -15%, rgba(224,30,60,.26), transparent 62%),
    radial-gradient(800px 450px at 100% -9%, rgba(74,108,196,.22), transparent 60%),
    linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,0) 86%)}
.bungkus{max-width:1160px;margin:0 auto;padding-block:28px;
         padding-left:20px;padding-right:20px}

/* --- header --- */
header{display:flex;align-items:center;gap:14px;margin-bottom:22px}
header .lg-mpl{width:44px;height:44px;flex:none;display:block;
  background-size:contain;background-repeat:no-repeat;background-position:center}
header h1{margin:0;font-size:17px;font-weight:600;letter-spacing:.2px}
header .catatan{margin:1px 0 0}
.catatan{color:var(--redup);font-size:12px}
.catatan a{color:var(--redup)}
.strip{color:var(--samar)}

/* --- pemilih musim --- */
.bar-musim{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:24px;
  border-bottom:1px solid var(--garis);padding-bottom:0}
.bar-musim button{background:none;color:var(--redup);border:none;
  border-bottom:2px solid transparent;padding:8px 13px;font-size:13px;
  cursor:pointer;font-family:inherit;margin-bottom:-1px}
.bar-musim button:hover{color:var(--teks)}
.bar-musim button.aktif{color:var(--teks);border-bottom-color:var(--aksen);font-weight:600}
.bar-musim .kini{display:none}

/* --- kepala musim: satu strip fakta --- */
.kepala-musim{margin-bottom:20px}
.kepala-musim h2{margin:0 0 10px;font-size:20px;font-weight:600}
.kepala-musim .kini{font-size:11px;color:var(--emas);font-weight:400;
  border:1px solid #5c4a1a;border-radius:3px;padding:1px 6px;vertical-align:middle}
.strip-fakta{display:flex;gap:28px;flex-wrap:wrap}
.fakta{display:flex;flex-direction:column;gap:2px}
.fakta .label{font-size:10px;letter-spacing:.7px;text-transform:uppercase;
  color:var(--samar)}
.fakta .isi{display:flex;align-items:center;gap:7px;font-weight:600}

/* --- tab bagian --- */
nav.bagian{display:flex;gap:4px;margin-bottom:18px;flex-wrap:wrap}
nav.bagian button{background:none;color:var(--redup);border:1px solid transparent;
  border-radius:5px;padding:6px 12px;font-size:13px;cursor:pointer;font-family:inherit}
nav.bagian button:hover{color:var(--teks);background:var(--panel)}
nav.bagian button.aktif{color:var(--teks);background:var(--panel);
  border-color:var(--garis)}
.tab-bar{display:flex;gap:3px;flex-wrap:wrap;margin-bottom:16px}
.tab-week{background:none;color:var(--redup);border:1px solid transparent;
  border-radius:5px;padding:5px 10px;font-size:12px;cursor:pointer;font-family:inherit}
.tab-week:hover{color:var(--teks)}
.tab-week.aktif{color:var(--teks);background:var(--panel);border-color:var(--garis)}
h3{font-size:13px;margin:26px 0 10px;font-weight:600;color:var(--redup);
   letter-spacing:.3px}

/* --- tabel --- */
.tabel{width:100%;border-collapse:collapse}
.tabel th,.tabel td{padding:9px 10px;text-align:center;
  border-bottom:1px solid var(--garis-halus)}
/* Judul kolom menempel di atas layar selama tabelnya masih terlihat. Di
   "Semua Pemain" tabelnya setinggi belasan ribu piksel — tanpa ini nama
   kolomnya hilang setelah beberapa baris dan sisa angkanya jadi tebakan.
   Garis bawahnya dari box-shadow, bukan border: border pada sel sticky di
   tabel border-collapse ikut tergulir dan menghilang. */
.tabel thead th{font-size:10px;letter-spacing:.7px;text-transform:uppercase;
  color:var(--redup);font-weight:600;border-bottom:none;
  position:sticky;top:0;z-index:3;background:var(--kepala);
  box-shadow:inset 0 -1px 0 var(--garis),0 12px 16px -14px rgba(0,0,0,.9)}
/* Selang-seling hanya untuk tabel yang panjang atau lebar. Di daftar lintas
   musim kelasnya dipasang dari JS, bukan :nth-child, karena baris yang
   tersaring tetap ada di DOM — dengan :nth-child polanya jadi bolong-bolong
   begitu pencarian dipakai. */
.tabel.daftar tbody tr.genap{background:var(--zebra)}
.tabel.statistik tbody tr:nth-child(even),
.tabel.hero tbody tr:nth-child(even){background:var(--zebra)}
.tabel tbody tr:hover,.tabel.daftar tbody tr.genap:hover,
.tabel.statistik tbody tr:nth-child(even):hover,
.tabel.hero tbody tr:nth-child(even):hover{background:var(--sorot)}
.tabel .kiri,.tabel .sel-tim{text-align:left}
.tabel .rank{color:var(--samar);width:34px}
.tabel .sisa{color:var(--samar)}
.sel-tim{display:flex;align-items:center;gap:9px}
.sel-tim .kode{display:block;color:var(--samar);font-size:11px;font-weight:400}
.menang,.plus{color:var(--hijau)}
.kalah,.minus{color:var(--merah)}
.logo{width:24px;height:24px;flex:none;display:inline-block;
  background-size:contain;background-repeat:no-repeat;background-position:center}
.logo.kecil{width:19px;height:19px}
.logo-kosong{font-size:10px;color:var(--samar);background:none;width:auto}
/* Sumber hanya menyediakan ONIC versi siluet hitam; dibalik agar kebaca. */
.lg-onic{filter:invert(1)}

/* --- klasemen pekan demi pekan --- */
/* Lebarnya ikut halaman. Caranya: SVG-nya dipasang tepat di atas area plot
   dengan preserveAspectRatio="none", jadi sumbu X-nya melar mengikuti lebar
   wadah — dan vector-effect:non-scaling-stroke yang menjaga tebal garisnya
   tetap 2px alih-alih ikut melar. Titiknya elemen HTML yang diletakkan lewat
   calc(), bukan <circle>, karena lingkaran di dalam SVG yang diregangkan akan
   jadi lonjong. Angka --baris di sini HARUS sama dengan BUMP_BARIS di Python. */
.bump{--kiri:24px;--kanan:78px;--atas:20px;--baris:28px;
  position:relative;width:100%;font-size:10px;margin-bottom:6px;
  height:calc(var(--atas) + var(--baris) * var(--n))}
.bump-pita{position:absolute;left:0;right:0;height:var(--baris);
  background:var(--zebra);border-radius:7px}
.bump-svg{position:absolute;left:var(--kiri);right:var(--kanan);
  top:var(--atas);height:calc(var(--baris) * var(--n))}
.bump .lintas{fill:none;stroke:var(--c);stroke-width:2;stroke-linecap:round;
  vector-effect:non-scaling-stroke;opacity:.9;transition:opacity .13s}
/* Garis 2px terlalu tipis untuk dibidik kursor; jalur kembar tak terlihat ini
   yang menangkap hover-nya, dan pointer-events:stroke membuat hanya pita
   garisnya yang peka — bukan seluruh kotak grafik. */
.bump .pukul{fill:none;stroke:transparent;stroke-width:20;
  vector-effect:non-scaling-stroke;pointer-events:stroke}
/* Cincin selatar dipakai supaya titik yang bertumpuk tetap terbaca sebagai
   dua titik, bukan satu gumpalan. */
.bump-d{position:absolute;width:9px;height:9px;margin:-4.5px 0 0 -4.5px;
  border-radius:50%;background:var(--c);box-shadow:0 0 0 2px var(--bg);
  transition:opacity .13s}
.bump-pekan{position:absolute;top:0;transform:translateX(-50%);
  color:var(--samar);letter-spacing:.4px}
.bump-rank{position:absolute;left:0;width:15px;height:var(--baris);
  display:flex;align-items:center;justify-content:flex-end;color:var(--samar)}
/* Logo + kode di ujung kanan adalah label langsung tiap garis; keping warna
   kecil di depannya yang mengikat garis ke timnya. */
.bump-tim{position:absolute;right:0;height:var(--baris);display:flex;
  align-items:center;gap:6px;transition:opacity .13s}
.bump-tim::before{content:"";width:3px;height:15px;border-radius:2px;
  background:var(--c);flex:none}
.bump-tim .kode{color:var(--redup);font-size:11px;font-weight:600}
/* Menyorot satu tim = meredupkan sisanya. */
.bump.menyorot [data-tim]{opacity:.12}
.bump.menyorot [data-tim].sorot{opacity:1}
.bump.menyorot .lintas.sorot{stroke-width:3}
@media(max-width:640px){
  .bump{--kanan:34px;--baris:26px}
  .bump-tim .kode{display:none}
}

/* --- jadwal --- */
.tanggal{margin:18px 0 6px;font-size:11px;color:var(--samar);
  text-transform:uppercase;letter-spacing:.6px}
.laga{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:14px;
  padding:9px 4px;border-bottom:1px solid var(--garis-halus)}
.laga.belum{color:var(--redup)}
.sisi{display:flex;align-items:center;gap:9px;color:var(--redup);min-width:0}
.sisi.kanan{justify-content:flex-end;text-align:right}
.sisi.juara{color:var(--teks);font-weight:600}
.tengah{text-align:center;min-width:80px}
.skor{font-size:16px;font-weight:700;letter-spacing:.5px}
.angka{color:var(--samar)}.angka.juara{color:var(--hijau)}
.pisah{color:var(--samar);margin:0 4px;font-weight:400}
.vs{font-size:12px;color:var(--samar);letter-spacing:1px}
.jam{font-size:11px;color:var(--samar);margin-top:1px}
.ronde{margin:22px 0 6px;font-size:11px;color:var(--emas);font-weight:600;
  text-transform:uppercase;letter-spacing:.7px}
.laga.grand{background:#1d1a0b}

/* --- matriks head-to-head --- */
.gulir{overflow-x:auto}
/* Judul kolom cuma bisa menempel kalau pembungkusnya BUKAN area gulir.
   overflow-x:auto membuat overflow-y ikut bernilai "auto", jadi .gulir
   menjadi kotak gulir tersendiri — dan sticky lalu mengacu ke kotak itu,
   yang tingginya persis setinggi isinya. Akibatnya judul kolom bukannya
   menempel di atas layar, tapi terdorong turun sejauh nilai top-nya dan
   menimpa baris di tengah tabel. Jadi di dalam .gulir sticky dimatikan
   dulu, dan dihidupkan lagi hanya kalau tabelnya memang muat — kelas
   "muat" dipasang dari JS setelah diukur, lihat ukurGulir(). */
.gulir .tabel thead th{position:static}
.gulir.muat{overflow-x:visible}
.gulir.muat .tabel thead th{position:sticky}
.matriks{border-collapse:collapse;margin:0 auto}
.matriks th,.matriks td{border:1px solid var(--garis);text-align:center}
.matriks th{padding:6px;width:74px}
.matriks .pojok{border-color:transparent}
.matriks td{padding:6px 4px;min-width:74px;vertical-align:middle}
.matriks .agg{font-size:14px;font-weight:700;line-height:1.2}
.matriks .rinci{font-size:10px;opacity:.7}
.sel.menang{background:#15381f;color:#d6f5e0}
.sel.kalah{background:#41161a;color:#f7d7d9}
.sel.seri{background:#332e12;color:#f0e7c4}
.diagonal{background:var(--panel)}
.kosong{background:none}

/* --- award --- */
.tabel.award td{vertical-align:middle}
.tabel.award .lbl{color:var(--redup);white-space:nowrap}
.penerima{display:inline-flex;align-items:center;gap:5px;margin-right:13px;
  white-space:nowrap;color:var(--teks)}
.penerima .logo.kecil{width:17px;height:17px}

/* --- roster --- */
.grid-tim{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(290px,1fr))}
.kartu-tim{background:var(--panel);border:1px solid var(--garis);
  border-radius:7px;overflow:hidden}
.kepala-tim{display:flex;align-items:center;gap:9px;padding:9px 12px;
  border-bottom:1px solid var(--garis)}
.kepala-tim .kode{display:block;color:var(--samar);font-size:11px;font-weight:400}
.tabel.roster td{padding:6px 12px;border-bottom:1px solid var(--garis-halus)}
.tabel.roster tr:last-child td{border-bottom:none}
.tabel.roster tr:hover{background:none}
.role{width:70px;font-size:10px;letter-spacing:.5px;color:var(--samar);
  text-transform:uppercase;font-weight:600;vertical-align:middle;text-align:left;
  white-space:nowrap}
.ikon-role{display:inline-block;width:15px;height:15px;vertical-align:-3px;
  margin-right:6px;background-size:contain;background-repeat:no-repeat;
  background-position:center}
.nama-role{vertical-align:middle}
.kaki-tim{padding:9px 12px;border-top:1px solid var(--garis-halus);
  font-size:11px;color:var(--redup);display:flex;flex-direction:column;gap:3px}
.kaki-tim .label{display:inline-block;min-width:66px;color:var(--samar);
  font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.tanda{display:inline-block;font-size:9px;color:var(--emas);border:1px solid #5c4a1a;
  border-radius:3px;padding:0 4px;margin-left:5px;vertical-align:middle}
.negara{font-size:10px;color:var(--samar);margin-left:5px}

/* --- statistik --- */
/* Tanpa kolom logo, tabelnya muat di lebar halaman — tidak perlu digeser.
   Yang dijaga cuma kolom nama tim dan angka supaya tidak membungkus; chip
   hero memang boleh turun baris. */
.tabel.statistik td{vertical-align:middle}
/* Tim diwakili logonya saja — nama lengkap ("Alter Ego Esports") memakan
   ruang yang lebih berguna untuk chip hero. Nama tetap ada sebagai tooltip
   pada logonya. */
.tabel.statistik .sel-logo{width:44px;text-align:center;padding:9px 6px}
.tabel.statistik .sel-logo .logo{width:26px;height:26px}
.tabel.statistik td:nth-child(2),
.tabel.statistik td:nth-child(3),
.tabel.statistik td:nth-child(4){white-space:nowrap}
.tabel.statistik th:nth-child(n+5),
.tabel.statistik td:nth-child(n+5){width:33%}
.hero-chip{display:inline-flex;align-items:center;gap:4px;background:var(--panel);
  border:1px solid var(--garis);border-radius:4px;padding:1px 6px;margin:2px 3px 2px 0;
  font-size:11px;white-space:nowrap}
.hero-n{color:var(--samar);font-size:10px}
/* Sembilan kolom tidak muat di lebar halaman. Dibiarkan melebar lalu
   bergulir di dalam .gulir — halaman induknya tetap tidak bergeser. */
.tabel.hero{min-width:720px}
.tabel.hero th,.tabel.hero td{white-space:nowrap}
.lawan-sel{min-width:135px}
.lawan-sel .pb{margin:0}
.pb-tabel.lawan td{padding:3px 7px;font-size:11px;white-space:nowrap}
.pb-tabel.lawan{width:auto}

/* --- pick & ban --- */
.pb{margin:-4px 0 8px;font-size:12px}
.pb summary{cursor:pointer;color:var(--samar);font-size:11px;padding:3px 4px;
  list-style:none;display:inline-flex;align-items:center;gap:6px}
.pb summary::-webkit-details-marker{display:none}
.pb summary::before{content:"+";font-weight:700;color:var(--samar)}
.pb[open] summary::before{content:"−"}
.pb summary:hover{color:var(--teks)}
.pb-isi{padding:4px 4px 10px}
.pb-judul{font-size:10px;color:var(--samar);text-transform:uppercase;
  letter-spacing:.6px;margin:8px 0 3px}
.pb-tabel{width:100%;border-collapse:collapse}
.pb-tabel td{padding:4px 8px;border-bottom:1px solid var(--garis-halus);
  vertical-align:top}
.pb-tim{width:66px;white-space:nowrap;color:var(--redup);font-size:11px}
.pb-tim .logo.kecil{width:15px;height:15px;vertical-align:-3px;margin-right:5px}
.pb-pick{color:var(--teks)}
.pb-ban{color:var(--samar);text-decoration:line-through;
  text-decoration-color:rgba(248,113,113,.45)}

/* --- daftar lintas musim: semua pemain / semua tim --- */
.pemisah-bar{width:1px;align-self:center;height:16px;background:var(--garis);
  margin:0 6px}
.panel-lintas .kepala-musim{margin-bottom:14px}
/* Penyaring musim dan kotak cari ikut menempel di atas layar. Daftar
   pemain tingginya belasan ribu piksel; tanpa ini kamu harus menggulir
   balik ke puncak halaman hanya untuk mengetik satu kata.
   Latarnya memakai backdrop-filter, bukan warna pekat, supaya baris yang
   lewat di bawahnya kabur total tapi batangnya tetap menyatu dengan
   gradien di kepala halaman — warna pekat akan tampak sebagai kotak
   asing waktu halaman belum digulir. */
.panel-lintas .alat{position:sticky;top:0;z-index:5;padding:10px 0 6px;
  background:linear-gradient(180deg,rgba(19,21,29,.58) 0%,rgba(14,16,21,.22) 74%,transparent 100%);
  backdrop-filter:blur(12px) saturate(1.2);
  -webkit-backdrop-filter:blur(12px) saturate(1.2);
  box-shadow:0 12px 18px -18px rgba(0,0,0,.82)}
/* Tinggi .alat berubah kalau tombol musimnya membungkus, jadi diukur di
   browser dan dituliskan ke --tinggi-alat (lihat JS). */
.panel-lintas .tabel thead th{top:var(--tinggi-alat,0px)}
.cari-bar{display:flex;align-items:center;gap:12px;margin:0 0 10px}
.cari{flex:1;min-width:0;max-width:420px;background:var(--panel);
  border:1px solid var(--garis);border-radius:6px;padding:8px 11px;
  color:var(--teks);font:inherit;font-size:13px}
.cari::placeholder{color:var(--samar)}
.cari:focus{outline:none;border-color:var(--aksen)}
.cari::-webkit-search-cancel-button{filter:grayscale(1) opacity(.5);cursor:pointer}
.cari-info{font-size:11px;color:var(--samar);white-space:nowrap}
.cari-info.nihil{color:var(--merah)}
/* Enam belas kolom jelas tidak muat; dibiarkan melebar lalu bergulir di dalam
   .gulir supaya halaman induknya tetap tidak bergeser ke samping. */
.tabel.daftar{min-width:860px}
.tabel.daftar th,.tabel.daftar td{white-space:nowrap}
.tabel.daftar td{vertical-align:middle}
.tabel.daftar .sel-tim{gap:8px}
.tabel.daftar .sel-tim b{font-weight:600}
.tabel.daftar .kode{font-size:10px}
/* .kode di kolom Musim tidak berada di dalam .sel-tim, jadi tidak ikut
   aturan display:block-nya dan akan menempel ke teks sebelumnya. */
.tabel.daftar td.nowrap .kode{display:block;margin-top:1px}
.tabel.daftar .sel-role{color:var(--redup);font-size:12px}
.tabel.daftar .rank{color:var(--samar)}
.tabel.daftar .gelar{color:var(--emas);letter-spacing:1px}
/* Lencana hasil playoff waktu satu musim dipilih. */
.lencana{display:inline-block;font-size:11px;padding:1px 7px;border-radius:3px;
  border:1px solid var(--garis);color:var(--redup);background:var(--panel)}
.lencana.juara{color:var(--emas);border-color:#6b5620;background:#1d1a0b;
  font-weight:600}
.tanda.cadangan{color:var(--redup);border-color:var(--garis)}
/* Penyaring musim memakai tampilan yang sama dengan penyaring pekan di Jadwal,
   supaya dua penyaring di satu situs tidak terlihat seperti dua mekanisme. */
.tab-musim{background:none;color:var(--redup);border:1px solid transparent;
  border-radius:5px;padding:5px 10px;font-size:12px;cursor:pointer;
  font-family:inherit}
.tab-musim:hover{color:var(--teks)}
.tab-musim.aktif{color:var(--teks);background:var(--panel);border-color:var(--garis)}
.tab-bar[data-target]{margin-bottom:10px}
/* Judul kolom bisa diklik untuk mengurutkan. JANGAN tambahkan position di
   sini: selektornya lebih spesifik daripada ".tabel thead th", jadi nilai
   apa pun di sini akan mengalahkan position:sticky dan judul kolomnya
   berhenti menempel. Panah urut di ::after cuma teks sebaris, tidak butuh
   position:relative. */
.tabel.daftar thead th{cursor:pointer;user-select:none}
.tabel.daftar thead th:first-child{cursor:default}
.tabel.daftar thead th:hover{color:var(--redup)}
.tabel.daftar thead th.urut::after{content:"↓";margin-left:4px;color:var(--aksen)}
.tabel.daftar thead th.urut.naik::after{content:"↑"}
.tabel.daftar tr.sembunyi{display:none}
@media(max-width:640px){
  .cari{max-width:none}
  .cari-bar{flex-direction:column;align-items:stretch;gap:6px}
}

footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--garis)}
footer .buatan{margin:10px 0 0;color:var(--samar)}
footer .buatan b{color:var(--redup);font-weight:600}

@media(max-width:640px){
  .bungkus{padding-left:14px;padding-right:14px}
  .laga{gap:8px;font-size:12px}
  .sisi{gap:6px}
  .strip-fakta{gap:18px}
  .tabel th,.tabel td{padding:8px 4px;font-size:13px}
  /* Kode tim di bawah nama jadi redundan waktu ruang sempit — nama lengkapnya
     sudah ada di baris yang sama, jadi disembunyikan agar kolom angka lega. */
  .sel-tim .kode{display:none}
  .grid-tim{grid-template-columns:1fr}
}
"""

JS = """
// Dua tingkat tab: musim (S10..S18) dan bagian (Klasemen/Jadwal/H2H).
// Bagian yang dipilih sengaja dipertahankan saat ganti musim — kalau kamu
// sedang membandingkan head-to-head antar musim, tidak perlu klik dua kali.
var musimAktif = document.querySelector('.bar-musim button.aktif').dataset.musim;
var bagianAktif = 'klasemen';

function render() {
  document.querySelectorAll('.bar-musim button').forEach(function (b) {
    b.classList.toggle('aktif', b.dataset.musim === musimAktif);
  });
  document.querySelectorAll('.panel-musim').forEach(function (p) {
    p.hidden = p.dataset.musim !== musimAktif;
  });
  document.querySelectorAll('nav.bagian button').forEach(function (b) {
    b.classList.toggle('aktif', b.dataset.bagian === bagianAktif);
  });
  document.querySelectorAll('section[data-bagian]').forEach(function (s) {
    s.hidden = s.dataset.bagian !== bagianAktif;
  });
  ukurGulir();
}

// Apakah satu tabel butuh gulir samping itu urusan lebar ISI tabelnya, bukan
// lebar layar. Versi sebelumnya menebak lewat media query, dan tebakannya
// salah: tabel "Semua Tim" yang 17 kolom lebih lebar daripada halaman pada
// lebar layar mana pun, jadi begitu gulirnya dimatikan dia meluber dan
// HALAMANNYA yang ikut bergeser ke samping. Jadi diukur saja — dan diukur
// ulang tiap kali panelnya ditampilkan atau jendelanya diubah ukurannya,
// karena wadah yang sedang hidden lebarnya nol.
function ukurGulir() {
  document.querySelectorAll('.gulir').forEach(function (g) {
    var t = g.querySelector('table');
    g.classList.toggle('muat', !!t && g.clientWidth > 0
                                && t.scrollWidth <= g.clientWidth);
  });
}
window.addEventListener('resize', ukurGulir);

document.querySelectorAll('.bar-musim button').forEach(function (btn) {
  btn.addEventListener('click', function () { musimAktif = btn.dataset.musim; render(); });
});
document.querySelectorAll('nav.bagian button').forEach(function (btn) {
  btn.addEventListener('click', function () { bagianAktif = btn.dataset.bagian; render(); });
});

// Penyaring pekan, per panel musim.
document.querySelectorAll('.tab-week').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var panel = btn.closest('.panel-musim');
    var pilih = btn.dataset.week;
    panel.querySelectorAll('.tab-week').forEach(function (b) {
      b.classList.toggle('aktif', b === btn);
    });
    panel.querySelectorAll('.blok-week').forEach(function (blok) {
      blok.hidden = !(pilih === 'all' || blok.dataset.week === pilih);
    });
  });
});

// --- Klasemen pekan demi pekan: sorot satu tim ------------------------------
// Yang harus ikut menyala adalah polyline-nya DAN sembilan logonya. Pasangan
// itu tidak bisa dijangkau dari satu selector :hover, jadi kelasnya dipasang
// dari sini.
document.querySelectorAll('.bump').forEach(function (b) {
  function sorot(kode) {
    b.classList.toggle('menyorot', !!kode);
    b.querySelectorAll('[data-tim]').forEach(function (el) {
      el.classList.toggle('sorot', el.dataset.tim === kode);
    });
  }
  b.addEventListener('mouseover', function (ev) {
    var el = ev.target.closest('[data-tim]');
    sorot(el ? el.dataset.tim : null);
  });
  b.addEventListener('mouseleave', function () { sorot(null); });
});

// --- Daftar lintas musim: saring musim, cari, urutkan -----------------------
// Penyaring musim dan kotak pencarian menyaring tabel yang SAMA, jadi keduanya
// dihitung sekali jalan di terapkan(). Kalau masing-masing memasang kelas
// "sembunyi" sendiri-sendiri, menghapus satu saringan akan ikut memunculkan
// baris yang seharusnya masih disembunyikan saringan yang lain.
var saring = {};

function terapkan(target) {
  var tabel = document.getElementById('tabel-' + target);
  if (!tabel) return;
  var st = saring[target] || (saring[target] = { ruang: 'all', kata: [] });
  var info = document.querySelector('.cari-info[data-target="' + target + '"]');
  var n = 0;

  tabel.querySelectorAll('tbody tr').forEach(function (tr) {
    var teks = tr.dataset.cari || '';
    var tampil = tr.dataset.ruang === st.ruang && st.kata.every(function (k) {
      return teks.indexOf(k) !== -1;
    });
    tr.classList.toggle('sembunyi', !tampil);
    // Nomor di kolom pertama adalah POSISI BARIS, bukan peringkat yang melekat
    // pada tim/pemainnya — jadi ditulis ulang tiap kali daftarnya berubah.
    if (tampil) {
      n++;
      var sel = tr.querySelector('.rank');
      if (sel) sel.textContent = n;
    }
    // Selang-seling ikut dihitung dari posisi tampil, bukan dari posisi
    // di DOM: baris yang tersaring masih ada di sana, jadi :nth-child
    // akan melubangi polanya begitu pencarian dipakai.
    tr.classList.toggle('genap', tampil && n % 2 === 0);
  });

  if (info) {
    info.textContent = n + ' ' + info.dataset.satuan;
    info.classList.toggle('nihil', n === 0);
  }
}

document.querySelectorAll('.tab-bar[data-target] .tab-musim').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var bar = btn.closest('.tab-bar');
    var target = bar.dataset.target;
    bar.querySelectorAll('.tab-musim').forEach(function (b) {
      b.classList.toggle('aktif', b === btn);
    });
    (saring[target] || (saring[target] = { ruang: 'all', kata: [] })).ruang =
      btn.dataset.ruang;
    terapkan(target);
  });
});

document.querySelectorAll('.cari').forEach(function (kotak) {
  kotak.addEventListener('input', function () {
    var target = kotak.dataset.target;
    var q = kotak.value.trim().toLowerCase();
    // Semua kata harus cocok, urutannya bebas: "onic jungle" tetap ketemu.
    (saring[target] || (saring[target] = { ruang: 'all', kata: [] })).kata =
      q ? q.split(/\\s+/) : [];
    terapkan(target);
  });
});

// Urutkan berdasarkan kolom. Angka dibandingkan sebagai angka (termasuk yang
// berformat "1,066,148" dan "81.2%"); sel kosong selalu dibuang ke bawah, naik
// maupun turun, supaya tanda "—" tidak pernah mengisi baris teratas.
//
// Yang diurutkan hanya baris yang sedang tampil. Baris musim lain ikut hadir di
// DOM tapi isinya beda bentuk ("Juara" vs "8/9" di kolom Playoff), jadi kalau
// semuanya diadu dalam satu perbandingan hasilnya campur aduk tanpa guna.
document.querySelectorAll('table.daftar thead th').forEach(function (th) {
  // Indeks kolom HARUS dihitung relatif terhadap barisnya sendiri. Memakai
  // indeks dari querySelectorAll itu salah: selector-nya menjangkau kedua
  // tabel, jadi kolom pertama tabel kedua bernomor 12, bukan 0 — dan
  // children[12] tidak ada, sehingga tabel kedua diam saja waktu diklik.
  var i = Array.prototype.indexOf.call(th.parentNode.children, th);
  if (i === 0) return;

  th.addEventListener('click', function () {
    var tabel = th.closest('table');
    var tbody = tabel.querySelector('tbody');
    var naik = th.classList.contains('urut') && !th.classList.contains('naik');
    tabel.querySelectorAll('thead th').forEach(function (x) {
      x.classList.remove('urut', 'naik');
    });
    th.classList.add('urut');
    if (naik) th.classList.add('naik');

    var semua = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
    var tampil = semua.filter(function (tr) { return !tr.classList.contains('sembunyi'); });
    var lain = semua.filter(function (tr) { return tr.classList.contains('sembunyi'); });

    tampil.sort(function (a, b) {
      var sa = a.children[i], sb = b.children[i];
      var ta = sa ? sa.textContent.trim() : '', tb = sb ? sb.textContent.trim() : '';
      var ka = ta === '\\u2014' || ta === '', kb = tb === '\\u2014' || tb === '';
      if (ka !== kb) return ka ? 1 : -1;        // kosong selalu di bawah
      if (ka && kb) return 0;
      var na = parseFloat(ta.replace(/[,%\\s]/g, ''));
      var nb = parseFloat(tb.replace(/[,%\\s]/g, ''));
      var angka = !isNaN(na) && !isNaN(nb);
      var c = angka ? na - nb : ta.localeCompare(tb, 'en');
      return naik ? c : -c;
    });

    tampil.concat(lain).forEach(function (tr) { tbody.appendChild(tr); });
    terapkan(tabel.id.replace(/^tabel-/, ''));
  });
});

document.querySelectorAll('.cari').forEach(function (k) { terapkan(k.dataset.target); });

// Judul kolom menempel tepat di bawah batang penyaring, bukan di puncak
// layar. Tingginya tidak bisa ditulis mati di CSS karena tombol musim
// membungkus ke dua baris di layar sempit — jadi diukur, dan diukur ulang
// setiap kali berubah (termasuk saat panelnya baru ditampilkan).
document.querySelectorAll('.panel-lintas .alat').forEach(function (alat) {
  var panel = alat.closest('.panel-lintas');
  var ukur = function () {
    panel.style.setProperty('--tinggi-alat', alat.offsetHeight + 'px');
  };
  if (window.ResizeObserver) new ResizeObserver(ukur).observe(alat);
  ukur();
});

render();
"""


# ---------------------------------------------------------------------------
# PERAKITAN
# ---------------------------------------------------------------------------

def panel_musim(season: int, laga: pd.DataFrame, terbaru: int,
                po: pd.DataFrame, champ: dict, mvps: dict) -> str:
    kl = klasemen(laga)
    selesai = int((laga.status == "selesai").sum())
    berjalan = season == terbaru

    # Satu strip padat menggantikan paragraf ringkasan + beberapa pita terpisah.
    # Sebelumnya info yang sama tersebar di tiga baris; ini muat dalam satu.
    fakta = []
    kode_juara = champ.get(season)
    if kode_juara:
        fakta.append(("Champion", logo(kode_juara, "logo kecil")
                      + e(TIM.get(kode_juara, (kode_juara,))[0])))
    if not kl.empty and selesai == len(laga):
        atas = kl.iloc[0]
        fakta.append(("Regular season top", logo(atas.kode, "logo kecil")
                      + e(atas.nama)))
    mvp = mvps.get(season)
    if mvp:
        fakta.append(("MVP", e(mvp["pemain"])))
    if berjalan:
        fakta.append(("Matches", f"{selesai} of {len(laga)} played"))

    strip = "".join(
        f'<div class="fakta"><span class="label">{lbl}</span>'
        f'<span class="isi">{val}</span></div>' for lbl, val in fakta)

    return f"""
  <div class="panel-musim" data-musim="{season}" hidden>
    <div class="kepala-musim">
      <h2>Season {season}{' <span class="kini">ongoing</span>' if berjalan else ''}</h2>
      <div class="strip-fakta">{strip}</div>
    </div>

    <nav class="bagian">
      <button data-bagian="klasemen">Standings</button>
      <button data-bagian="jadwal">Schedule</button>
      <button data-bagian="h2h">Head-to-Head</button>
      <button data-bagian="playoff">Playoffs</button>
      <button data-bagian="award">Awards</button>
      <button data-bagian="roster">Rosters</button>
      <button data-bagian="statistik">Stats</button>
    </nav>

    <section data-bagian="klasemen" hidden>{bagian_klasemen(kl)}</section>
    <section data-bagian="jadwal" hidden>{bagian_jadwal(laga, season)}</section>
    <section data-bagian="h2h" hidden>{bagian_h2h(laga)}</section>
    <section data-bagian="playoff" hidden>{bagian_playoff(po, berjalan)}</section>
    <section data-bagian="award" hidden>{bagian_award(season)}</section>
    <section data-bagian="roster" hidden>{bagian_roster(season)}</section>
    <section data-bagian="statistik" hidden>{bagian_statistik(season)}</section>
  </div>"""


def main() -> None:
    semua = semua_laga()
    if semua.empty:
        raise SystemExit("Belum ada data. Jalankan scraper dulu.")

    musim = sorted(semua.season.unique())
    terbaru = max(musim)

    try:
        meta = json.loads((DATA_DIR / "_metadata.json").read_text(encoding="utf-8"))
        diambil = meta.get("scraped_at_utc", "")[:10]
    except (OSError, json.JSONDecodeError):
        diambil = ""

    tombol_musim = "".join(
        f'<button data-musim="{m}"{" class=\'aktif\'" if m == terbaru else ""}>'
        f'S{m}{"<span class=\'kini\'>&bull;</span>" if m == terbaru else ""}</button>'
        for m in musim)
    # Dua tampilan lintas musim, dipisah garis supaya tidak terbaca sebagai musim.
    tombol_musim += ('<span class="pemisah-bar" aria-hidden="true"></span>'
                     '<button data-musim="semua-pemain">All Players</button>'
                     '<button data-musim="semua-tim">All Teams</button>')

    po_semua = playoff()
    champ = juara(po_semua)
    mvps = mvp_resmi()
    panel = "".join(
        panel_musim(m, semua[semua.season == m], terbaru,
                    po_semua[po_semua.season == m] if not po_semua.empty
                    else po_semua, champ, mvps)
        for m in musim)
    selesai = int((semua.status == "selesai").sum())

    doc = f"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MPL ID — Standings, Schedule &amp; Head-to-Head (Season {musim[0]}&ndash;{terbaru})</title>
<style>{CSS}
{css_logo()}</style>
</head>
<body>
<div class="bungkus">

  <header>
    <span class="lg-mpl" role="img" aria-label="MPL Indonesia"></span>
    <div>
      <h1>MPL Indonesia &mdash; Regular Season</h1>
      <p class="catatan">
        {len(musim)} seasons (S{musim[0]}&ndash;S{terbaru}) &middot;
        {len(semua)} matches &middot; {selesai} played
        {f'&middot; current season fetched {e(diambil)}' if diambil else ''}
      </p>
    </div>
  </header>

  <div class="bar-musim">{tombol_musim}</div>
  {panel}

  <div class="panel-musim panel-lintas" data-musim="semua-pemain" hidden>
    <div class="kepala-musim"><h2>All Players</h2></div>
    {bagian_semua_pemain(musim)}
  </div>

  <div class="panel-musim panel-lintas" data-musim="semua-tim" hidden>
    <div class="kepala-musim"><h2>All Teams</h2></div>
    {bagian_semua_tim(musim)}
  </div>

  <footer>
    <p class="catatan">
      S{terbaru} data from <a href="https://id-mpl.com">id-mpl.com</a>, the
      official MPL Indonesia site. S{musim[0]}&ndash;S{terbaru - 1} from
      <a href="https://liquipedia.net/mobilelegends/">Liquipedia</a>, licensed
      <a href="https://creativecommons.org/licenses/by-sa/3.0/">CC BY-SA 3.0</a>.
      Mobile Legends: Bang Bang and MPL Indonesia are trademarks of Moonton;
      this is an unofficial, non-commercial archive.
    </p>
    <p class="catatan buatan">Made by <b>@agaollineed</b></p>
  </footer>

</div>
<script>{JS}</script>
</body>
</html>
"""

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    keluaran = SITE_DIR / "index.html"
    keluaran.write_text(doc, encoding="utf-8")
    print(f"  [simpan] {keluaran}  ({len(doc):,} karakter)")
    print(f"           {len(musim)} musim | {len(semua)} laga reguler | "
          f"{len(po_semua)} laga playoff | {len(champ)} juara | {len(mvps)} MVP resmi")


if __name__ == "__main__":
    main()
