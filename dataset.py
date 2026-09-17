#!/usr/bin/env python3
"""dataset.py — Satukan data dua sumber jadi satu tabel laga.

Masalahnya: musim berjalan (S18) datang dari id-mpl.com, arsip S10-S17 datang
dari Liquipedia, dan bentuk kolomnya beda. Daripada menulis dua jalur logika di
generator situs, semuanya dinormalkan di sini menjadi SATU bentuk:

    season week tanggal jam kode1 nama1 skor1 skor2 kode2 nama2 status sumber

Setelah itu klasemen dan head-to-head bisa dihitung dengan fungsi yang sama
persis untuk musim mana pun — kode yang sudah tervalidasi terhadap klasemen
resmi id-mpl.com, dipakai ulang apa adanya untuk 8 musim arsip.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")

KOLOM = ["season", "week", "tanggal", "jam", "timestamp", "kode1", "nama1",
         "skor1", "skor2", "kode2", "nama2", "status", "sumber"]

# ---------------------------------------------------------------------------
# REGISTRY TIM — satu sumber kebenaran untuk nama & logo
# ---------------------------------------------------------------------------

# Liquipedia kadang memakai kode berbeda untuk organisasi yang SAMA. Ketahuan
# karena nama panjang dan berkas logonya identik:
#   GFID (S10) "Geek Fam ID" -> berkas logo sama persis dengan GEEK
#   FNOC (S14) "ONIC"        -> rebrand Fnatic ONIC, tetap ONIC
# Tanpa disamakan, satu tim akan muncul dua kali di tabel lintas musim.
ALIAS = {
    "GFID": "GEEK",
    "FNOC": "ONIC",
}

# kode -> (nama tampil, berkas logo di site/assets/)
TIM = {
    "AE":   ("Alter Ego Esports",  "ae.png"),
    "AURA": ("AURA Fire",          "aura.png"),
    "BTR":  ("Bigetron by VIT",    "btr.png"),
    "DEWA": ("Dewa United",        "dewa.png"),
    "EVOS": ("EVOS",               "evos.png"),
    "GEEK": ("Geek Fam ID",        "geek.png"),
    "NAVI": ("NAVI",               "navi.png"),
    "ONIC": ("ONIC",               "onic.png"),
    "RBL":  ("Rebellion Esports",  "rbl.png"),
    "RRQ":  ("RRQ Hoshi",          "rrq.png"),
    "TLID": ("Team Liquid ID",     "tlid.png"),
}


# Liquipedia memakai nama ORGANISASI SAAT INI di tabel klasemen, tapi nama
# PERIODE di daftar laga. Ketahuan waktu memvalidasi S13: baris peringkat 4
# menaut ke /mobilelegends/Team_Liquid_ID padahal daftar laganya menulis
# "AURA Fire" — angkanya identik (8-8, 21-18), jadi memang organisasi yang sama.
# AURA Fire diakuisisi Team Liquid setelah S13.
#
# Kita SENGAJA tidak menggabungkan keduanya: di tampilan per musim, nama dan
# logo yang benar adalah nama periode itu. AURA Fire main di S10-S13 sebagai
# AURA Fire, bukan sebagai Team Liquid.
# Rantai rebrand: AURA Fire -> Liquid Aura -> Team Liquid ID.
# Buktinya ada di halaman S13 sendiri, tiga tempat berbeda:
#   crosstable reguler -> "AURA Fire"       (/AURA_Fire)
#   tabel klasemen     -> "Team Liquid ID"  (/Team_Liquid_ID), angka identik
#   halaman playoff    -> "Liquid Aura"     (/Liquid_Aura)
# Jadi rebrand-nya terjadi DI TENGAH musim S13: reguler dimainkan sebagai AURA
# Fire, playoff sebagai Liquid Aura. Dipetakan ke satu kode per musim supaya
# tim yang lolos playoff tetap bisa ditelusuri ke klasemennya — tanpa ini,
# tab Playoff S13 memunculkan tim yang tidak ada di klasemen musim itu.
RIWAYAT_NAMA = {
    "TLID": [(10, 13, "AURA")],
}


def kode_periode(kode: str, season: int) -> str:
    """Kode yang benar untuk musim tertentu, mengingat riwayat ganti nama."""
    kode = normal(kode)
    for awal, akhir, lama in RIWAYAT_NAMA.get(kode, []):
        if awal <= season <= akhir:
            return lama
    return kode


def normal(kode: str) -> str:
    """Samakan kode yang merujuk organisasi yang sama."""
    return ALIAS.get((kode or "").upper(), (kode or "").upper())


def nama_tim(kode: str) -> str:
    return TIM.get(normal(kode), (kode,))[0]



def _dari_id_mpl() -> pd.DataFrame:
    """Musim berjalan. Di sini tim1/tim2 sudah berupa kode (AE, BTR, ...)."""
    berkas = DATA_DIR / "schedule.csv"
    if not berkas.exists():
        return pd.DataFrame(columns=KOLOM)

    sc = pd.read_csv(berkas)
    sc = sc.rename(columns={"tim1": "kode1", "tim2": "kode2"})
    sc["season"] = 18
    sc["timestamp"] = None
    sc["nama1"] = sc.kode1.map(nama_tim)
    sc["nama2"] = sc.kode2.map(nama_tim)
    sc["sumber"] = "id-mpl.com"
    return sc.reindex(columns=KOLOM)


def _dari_liquipedia() -> pd.DataFrame:
    """Arsip. Punya timestamp Unix yang presisi; tanggal/jam diturunkan darinya."""
    berkas = DATA_DIR / "history_matches.csv"
    if not berkas.exists():
        return pd.DataFrame(columns=KOLOM)

    hm = pd.read_csv(berkas)
    hm = hm.rename(columns={"tim1": "nama1", "tim2": "nama2"})

    def bagian(ts, fmt):
        if pd.isna(ts):
            return None
        return datetime.fromtimestamp(int(ts)).strftime(fmt)

    hm["tanggal"] = hm.timestamp.map(lambda t: bagian(t, "%Y-%m-%d"))
    hm["jam"] = hm.timestamp.map(lambda t: bagian(t, "%H:%M"))
    hm["sumber"] = "liquipedia"
    return hm.reindex(columns=KOLOM)


def semua_laga() -> pd.DataFrame:
    """Gabungan seluruh musim, urut dari yang terlama."""
    df = pd.concat([_dari_liquipedia(), _dari_id_mpl()], ignore_index=True)
    if df.empty:
        return df
    for sisi in ("1", "2"):
        df[f"kode{sisi}"] = df[f"kode{sisi}"].map(normal)
        df[f"nama{sisi}"] = df[f"kode{sisi}"].map(nama_tim)
    df = df.sort_values(["season", "week", "tanggal", "jam"], na_position="last")
    return df.reset_index(drop=True)


def klasemen(laga: pd.DataFrame) -> pd.DataFrame:
    """Klasemen satu musim, dihitung dari hasil laga.

    Sengaja DIHITUNG, bukan di-scrape terpisah. Liquipedia memang menyediakan
    tabel klasemennya sendiri, tapi menurunkannya dari daftar laga berarti satu
    jalur kode untuk semua musim — dan angkanya dijamin konsisten dengan jadwal
    dan head-to-head yang ditampilkan di halaman yang sama.
    """
    if laga.empty:
        return pd.DataFrame()

    rekap: dict[str, dict] = {}

    def slot(kode: str, nama: str) -> dict:
        r = rekap.setdefault(kode, {
            "kode": kode, "nama": nama, "main": 0, "menang": 0, "kalah": 0,
            "draw": 0, "belum_main": 0, "game_menang": 0, "game_kalah": 0,
        })
        return r

    for row in laga.itertuples(index=False):
        a = slot(row.kode1, row.nama1)
        b = slot(row.kode2, row.nama2)

        if row.status != "selesai":
            a["belum_main"] += 1
            b["belum_main"] += 1
            continue

        for me, s_saya, s_lawan in ((a, row.skor1, row.skor2),
                                    (b, row.skor2, row.skor1)):
            me["main"] += 1
            me["game_menang"] += int(s_saya)
            me["game_kalah"] += int(s_lawan)
            if s_saya > s_lawan:
                me["menang"] += 1
            elif s_saya < s_lawan:
                me["kalah"] += 1
            else:
                me["draw"] += 1

    df = pd.DataFrame(rekap.values())
    df["selisih_game"] = df.game_menang - df.game_kalah
    df = df.sort_values(["menang", "selisih_game", "game_menang"],
                        ascending=False).reset_index(drop=True)
    df.insert(0, "peringkat", range(1, len(df) + 1))
    return df


# ---------------------------------------------------------------------------
# PLAYOFF
# ---------------------------------------------------------------------------

KOLOM_PO = ["season", "tanggal", "jam", "timestamp", "ronde", "kode1", "nama1",
            "skor1", "skor2", "kode2", "nama2", "format", "pemenang",
            "periode1", "periode2"]


def playoff() -> pd.DataFrame:
    """Laga playoff semua musim, kode tim sudah dinormalkan.

    Urutannya sengaja dibiarkan kronologis (sesuai timestamp), karena itulah
    urutan bracket yang sebenarnya dimainkan: babak atas dulu, lalu bawah,
    ditutup Grand Final.
    """
    berkas = DATA_DIR / "playoff_matches.csv"
    if not berkas.exists():
        return pd.DataFrame(columns=KOLOM_PO)

    po = pd.read_csv(berkas)
    po = po.rename(columns={"tim1": "nama1", "tim2": "nama2"})

    def bagian(ts, fmt):
        if pd.isna(ts):
            return None
        return datetime.fromtimestamp(int(ts)).strftime(fmt)

    po["tanggal"] = po.timestamp.map(lambda t: bagian(t, "%Y-%m-%d"))
    po["jam"] = po.timestamp.map(lambda t: bagian(t, "%H:%M"))

    for sisi in ("1", "2"):
        # Simpan nama sebagaimana tertulis di Liquipedia SEBELUM dinormalkan —
        # itu nama yang benar-benar dipakai tim pada saat itu ("Liquid Aura").
        po[f"periode{sisi}"] = po[f"nama{sisi}"]
        po[f"kode{sisi}"] = [kode_periode(k, s) for k, s
                             in zip(po[f"kode{sisi}"], po.season)]
        po[f"nama{sisi}"] = po[f"kode{sisi}"].map(nama_tim)
    po["pemenang"] = [kode_periode(k, s) if pd.notna(k) else None
                      for k, s in zip(po.pemenang, po.season)]

    po = po.sort_values(["season", "timestamp"], na_position="last")
    return po.reindex(columns=KOLOM_PO).reset_index(drop=True)


def juara(po: pd.DataFrame) -> dict[int, str]:
    """Musim -> kode juara, diambil dari pemenang Grand Final."""
    hasil = {}
    if po.empty:
        return hasil
    gf = po[po.ronde.str.contains("grand final", case=False, na=False)]
    for musim, grup in gf.groupby("season"):
        menang = grup.iloc[-1].pemenang       # kalau ada bracket reset, ambil yang terakhir
        if pd.notna(menang):
            hasil[int(musim)] = menang
    return hasil


# Nama ronde ditulis berbeda-beda antar musim ("Upper Bracket Semifinals" di
# satu musim, "Upper Bracket SF (Bo5)" di musim lain), jadi dinormalkan dulu
# sebelum dipakai sebagai kunci urutan.
URUT_RONDE = ["Play-In", "UB Quarterfinal", "LB Semifinal", "UB Semifinal",
              "UB Final", "LB Final", "Grand Final"]


def ronde_pendek(ronde: str) -> str:
    """'Upper Bracket SF (Bo5)' -> 'UB Semifinal'."""
    r = re.sub(r"\s*\(Bo\d\)\s*$", "", str(ronde)).strip()
    r = r.replace("Upper Bracket", "UB").replace("Lower Bracket", "LB")
    r = r.replace("Quarterfinals", "Quarterfinal").replace("Semifinals", "Semifinal")
    r = re.sub(r"\bSF\b", "Semifinal", r)
    return "Play-In" if r.lower() == "play-in" else r


def hasil_playoff(po: pd.DataFrame | None = None) -> pd.DataFrame:
    """Nasib tiap tim di playoff: sampai mana, dan peringkat akhirnya.

    Ditentukan dari laga TERAKHIR yang dimainkan tim itu (menurut timestamp),
    lalu dilihat menang atau kalah. Kriteria "tidak pernah kalah" tidak bisa
    dipakai di bracket gugur ganda: juara S17 (BTR) kalah di UB Semifinal,
    turun ke lower bracket, dan tetap keluar sebagai juara.

    Peringkat memakai peringkat kompetisi — dua tim yang tersingkir di ronde
    yang sama berbagi angka yang sama, tidak dipaksa berurutan.
    """
    if po is None:
        po = playoff()
    if po.empty:
        return pd.DataFrame(columns=["season", "kode", "ronde_akhir", "hasil",
                                     "peringkat_playoff", "laga"])

    baris = []
    for s, g in po.groupby("season"):
        g = g.sort_values("timestamp")
        for kode in sorted(set(g.kode1) | set(g.kode2)):
            sub = g[(g.kode1 == kode) | (g.kode2 == kode)]
            akhir = sub.iloc[-1]
            ronde = ronde_pendek(akhir.ronde)
            menang = akhir.pemenang == kode
            if "Grand Final" in ronde:
                hasil = "Champion" if menang else "Runner-up"
            else:
                hasil = ronde
            baris.append({"season": int(s), "kode": kode, "ronde_akhir": ronde,
                          "menang_akhir": bool(menang), "hasil": hasil,
                          "laga": len(sub)})

    df = pd.DataFrame(baris)

    # Semakin jauh rondenya, semakin tinggi peringkatnya; juara di atas
    # runner-up meski ronde terakhirnya sama.
    def skor(b):
        dasar = URUT_RONDE.index(b.ronde_akhir) if b.ronde_akhir in URUT_RONDE else -1
        return dasar * 2 + (1 if b.menang_akhir else 0)

    df["_skor"] = [skor(b) for b in df.itertuples(index=False)]
    df["peringkat_playoff"] = (df.groupby("season")._skor
                               .rank(method="min", ascending=False).astype(int))
    return df.drop(columns=["_skor"]).sort_values(
        ["season", "peringkat_playoff", "kode"]).reset_index(drop=True)


def juara_pemain() -> pd.DataFrame:
    """Pemain yang timnya jadi juara musim itu — gelar, bukan penghargaan.

    Dasarnya keanggotaan roster (inti maupun cadangan), bukan siapa yang turun
    bermain: gelar juara melekat pada regunya. Ini sengaja dipisah dari tabel
    award, yang isinya penghargaan perorangan seperti MVP dan Team of the Week.
    """
    rs = roster()
    if rs.empty:
        return pd.DataFrame(columns=["season", "kode", "pemain", "link"])

    champ = juara(playoff())
    r = rs[rs.grup.isin(["Main", "Subs"]) & rs.kode.notna()]
    pilih = [champ.get(int(s)) == k for s, k in zip(r.season, r.kode)]
    return r[pilih][["season", "kode", "pemain", "link", "grup"]].reset_index(drop=True)


def head_to_head(laga: pd.DataFrame) -> dict[tuple[str, str], list[tuple[int, int]]]:
    """Semua pertemuan, dicatat DUA arah — kunci (baris, kolom) selalu dari
    sudut pandang tim baris, supaya matriksnya simetris otomatis."""
    h2h: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for m in laga[laga.status == "selesai"].itertuples(index=False):
        h2h.setdefault((m.kode1, m.kode2), []).append((int(m.skor1), int(m.skor2)))
        h2h.setdefault((m.kode2, m.kode1), []).append((int(m.skor2), int(m.skor1)))
    return h2h


def picks_bans() -> pd.DataFrame:
    """Hero pick & ban tiap game, satu baris per (game, tim).

    Hanya tersedia untuk musim yang datanya dari Liquipedia (S10-S17).
    Musim berjalan diambil dari id-mpl.com, yang tidak memuat pick/ban.
    """
    berkas = DATA_DIR / "picks_bans.csv"
    if not berkas.exists():
        return pd.DataFrame()

    pb = pd.read_csv(berkas)
    pb["kode"] = [kode_periode(k, s) for k, s in zip(pb.kode, pb.season)]
    return pb


def pickban_per_laga(pb: pd.DataFrame) -> dict:
    """Kelompokkan pick/ban berdasarkan (season, stage, timestamp).

    Timestamp dipakai sebagai kunci, bukan pasangan nama tim + pekan: dua tim
    bisa bertemu lebih dari sekali dalam satu pekan, dan timestamp itu unik
    per laga.
    """
    hasil: dict = {}
    if pb.empty:
        return hasil
    for r in pb.itertuples(index=False):
        if pd.isna(r.timestamp):
            continue
        hasil.setdefault((int(r.season), r.stage, int(r.timestamp)), []).append(r)
    return hasil


# ---------------------------------------------------------------------------
# STATISTIK
# ---------------------------------------------------------------------------

def hero_stats() -> pd.DataFrame:
    """Statistik hero per musim: picks, bans, menang, kalah, win rate.

    Dua sumber, disatukan ke bentuk yang sama:
      S10-S17 : tabel hero halaman /Statistics Liquipedia
      S18     : halaman /statistics id-mpl.com

    Kolom menang/kalah datang dari sumbernya, bukan dihitung sendiri: data
    pick/ban yang kita punya mencatat hero per game, tapi TIDAK mencatat
    pemenang tiap game — popup laga hanya memuat skor laganya.
    """
    bagian = []

    lq = DATA_DIR / "hero_season_stats.csv"
    if lq.exists():
        bagian.append(pd.read_csv(lq))

    idm = DATA_DIR / "hero_stats.csv"
    if idm.exists():
        h = pd.read_csv(idm)
        h = h.rename(columns={"pick": "picks", "ban": "bans",
                              "win": "menang", "win_rate": "win_rate"})
        h["season"] = 18
        h["kalah"] = h.picks - h.menang
        h["pick_ban"] = h.picks + h.bans
        bagian.append(h.reindex(columns=["season", "hero", "picks", "menang",
                                         "kalah", "win_rate", "bans", "pick_ban"]))

    if not bagian:
        return pd.DataFrame()
    df = pd.concat(bagian, ignore_index=True)
    df = df[df.picks.notna() | df.bans.notna()].copy()

    # Dua sumber memakai kesepakatan berbeda untuk hero yang tidak pernah
    # dipick: Liquipedia mengosongkan win rate-nya, id-mpl menulis 0.0.
    # Yang benar adalah kosong — win rate dari nol pertandingan itu tidak
    # terdefinisi, bukan nol persen. Kalau dibiarkan 0.0, hero yang tidak
    # pernah dipick akan menumpuk di dasar peringkat "win rate terburuk" dan
    # menarik turun setiap AVG() yang menyentuh kolom ini.
    df.loc[df.picks == 0, "win_rate"] = pd.NA
    return df


def hero_relasi() -> pd.DataFrame:
    """Rekor tiap hero bersama (`rekan`) dan melawan (`lawan`) hero lain.

    Bedanya bukan cuma label. Dua hero SATU TIM menang dan kalah bersama, jadi
    angka W/L pasangan rekan identik dua arah. Dua hero BERHADAPAN saling
    berkebalikan. Keduanya diuji di validate.py dengan aturan yang berbeda.

    Hanya LIMA teratas per kategori yang disediakan Liquipedia, jadi ini bukan
    matriks lengkap. W/L dari sudut pandang kolom `hero`.
    """
    berkas = DATA_DIR / "hero_relasi.csv"
    if not berkas.exists():
        return pd.DataFrame()
    return pd.read_csv(berkas)


def statistik_tim(season: int) -> pd.DataFrame:
    """Per tim dalam satu musim: win rate, lalu 5 hero paling sering
    dipick dan 5 paling sering di-ban.

    Win rate dihitung dari laga yang SUDAH dimainkan saja, supaya musim
    berjalan tidak terlihat buruk hanya karena sisa jadwalnya belum jalan.
    Pick/ban mencakup regular season sampai final (playoff ikut).
    """
    laga = semua_laga()
    kl = klasemen(laga[laga.season == season])
    if kl.empty:
        return pd.DataFrame()

    pb = picks_bans()
    sub = pb[pb.season == season] if not pb.empty else pb

    kol_p = [f"pick{i}" for i in range(1, 6)]
    kol_b = [f"ban{i}" for i in range(1, 6)]

    def teratas(df, kolom, n=5):
        if df.empty:
            return []
        hit = df[kolom].stack().value_counts()
        return [(h, int(j)) for h, j in hit.head(n).items()]

    baris = []
    for r in kl.itertuples(index=False):
        main = int(r.menang) + int(r.kalah)
        tim_pb = sub[sub.kode == r.kode] if not sub.empty else sub
        baris.append({
            "kode": r.kode, "nama": r.nama,
            "main": main, "menang": int(r.menang), "kalah": int(r.kalah),
            "win_rate": round(r.menang / main * 100, 1) if main else None,
            "game_menang": int(r.game_menang), "game_kalah": int(r.game_kalah),
            "game_win_rate": (round(r.game_menang / (r.game_menang + r.game_kalah) * 100, 1)
                              if (r.game_menang + r.game_kalah) else None),
            "top_pick": teratas(tim_pb, kol_p),
            "top_ban": teratas(tim_pb, kol_b),
        })

    return pd.DataFrame(baris)


# ---------------------------------------------------------------------------
# AWARDS (halaman utama musim)
# ---------------------------------------------------------------------------

def peta_tim():
    """Fungsi pencari kode tim dari nama, untuk halaman yang memakai nama bebas.

    Nama tim di Liquipedia berbeda-beda tergantung halamannya — ada EMPAT
    varian untuk organisasi yang sama ("Bigetron Alpha" / "Bigetron by
    Vitality" / "Bigetron Esports", "Geek Slate" / "Geek Fam ID" / "Geek Fam").
    Jadi pencocokan dibuat bertingkat:

        1. per musim (paling tepat),
        2. lintas-musim (nama yang tak dikenal di musim X biasanya muncul
           sebagai nama periode di musim lain),
        3. bentuk sederhana (huruf kecil, akhiran "ID"/"Esports" dibuang).

    Sengaja tidak dibuat lebih longgar dari itu, supaya dua organisasi berbeda
    tidak tertukar cuma karena namanya mirip.
    """
    lokal: dict[tuple[int, str], str] = {}
    global_: dict[str, str] = {}

    for nama_berkas in ("history_matches.csv", "playoff_matches.csv"):
        berkas = DATA_DIR / nama_berkas
        if not berkas.exists():
            continue
        raw = pd.read_csv(berkas)
        for r in raw.itertuples(index=False):
            for nama, kode in ((r.tim1, r.kode1), (r.tim2, r.kode2)):
                lokal.setdefault((int(r.season), nama), normal(kode))
                global_.setdefault(nama, normal(kode))

    for kode, (nama_tampil, _) in TIM.items():
        global_.setdefault(nama_tampil, kode)

    def sederhana(nama: str) -> str:
        t = re.sub(r"\s+(id|esports)$", "", nama.strip().lower())
        return re.sub(r"\s+", " ", t)

    sederhana_map: dict[str, str] = {}
    for nama, kode in global_.items():
        sederhana_map.setdefault(sederhana(nama), kode)

    def cari(musim, nama):
        if not isinstance(nama, str) or not nama:
            return None
        kode = (lokal.get((int(musim), nama))
                or global_.get(nama)
                or sederhana_map.get(sederhana(nama)))
        return kode_periode(kode, int(musim)) if kode else None

    return cari


# ---------------------------------------------------------------------------
# STATISTIK PEMAIN & TIM (id-mpl.com, musim berjalan)
# ---------------------------------------------------------------------------

# Angka KDA/gold/damage cuma ada di situs resmi MPL, dan situs itu hanya
# menayangkan musim yang sedang berjalan. Liquipedia tidak punya padanannya.
MUSIM_ID_MPL = 18


def pemain_id(link, nama) -> str | None:
    """Identitas pemain yang stabil.

    Nama tampilan tidak bisa dipakai: Liquipedia menulis pemain yang sama
    dengan ejaan berbeda antar halaman ("VYN"/"Vyn", "Super KENN"/"Kenn").
    Yang stabil adalah slug halamannya. Kalau tidak ada tautan (mis. kaster),
    nama dipakai sebagai cadangan.
    """
    if isinstance(link, str) and link.startswith("/mobilelegends/"):
        slug = link.rsplit("/", 1)[-1]
        if "index.php" in slug:                      # halaman belum dibuat
            m = re.search(r"title=([^&]+)", slug)
            slug = m.group(1) if m else slug
        return slug
    return str(nama) if isinstance(nama, str) and nama else None


def kunci_nama(nama: str) -> str:
    """Bentuk nama yang tahan beda ejaan antar sumber.

    id-mpl dan Liquipedia mengeja nama pemain yang sama dengan cara berbeda:
    kapitalisasi ("MORENOOO" vs "Moreno"), spasi sisipan ("ABOYY" vs
    "A B O Y"), dan huruf akhir yang digandakan ("LUTPI" vs "Lutpiii").
    Jadi: huruf saja, kecilkan, lalu runtuhkan huruf yang berulang.

    Kunci ini SENGAJA hanya dipakai di dalam satu tim. Meruntuhkan huruf
    berulang itu agresif — dua pemain berbeda bisa saja bertabrakan kalau
    dibandingkan lintas liga — tapi di dalam satu regu berisi belasan orang
    risikonya hilang, dan itu dibuktikan ulang oleh validate.py.
    """
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]", "", (nama or "").lower()))


def stat_pemain() -> pd.DataFrame:
    """KDA tiap pemain di musim berjalan, sudah bertim dan tersambung roster.

    Kode timnya datang dari nama berkas logo di halaman sumber, bukan dari
    pencocokan nama — lihat `nama_logo()` di mpl_scraper.py. Yang dicocokkan
    lewat nama hanyalah sambungan ke roster Liquipedia (untuk dapat `link`
    halaman pemain), dan itu pun dibatasi per tim.
    """
    berkas = DATA_DIR / "player_stats.csv"
    if not berkas.exists():
        return pd.DataFrame()

    ps = pd.read_csv(berkas)
    ps = ps.rename(columns={
        "player": "pemain", "team_code": "kode", "lanes": "role",
        "total_games": "main", "total_kills": "kill", "avg_kills": "kill_rata",
        "total_deaths": "mati", "avg_deaths": "mati_rata",
        "total_assists": "assist", "avg_assists": "assist_rata",
        "avg_kda": "kda", "kill_participation": "partisipasi"})
    ps["season"] = MUSIM_ID_MPL
    ps["kode"] = ps.kode.map(normal)

    # Sambungkan ke roster Liquipedia untuk dapat link halaman pemain dan
    # negaranya. Cocok per (tim, kunci nama) — bukan nama mentah.
    rs = roster()
    tautan: dict[tuple[str, str], tuple] = {}
    if not rs.empty:
        r18 = rs[(rs.season == MUSIM_ID_MPL) & rs.grup.isin(["Main", "Subs"])]
        for _, b in r18.iterrows():
            tautan.setdefault((b["kode"], kunci_nama(b["pemain"])),
                              (b["pemain"], b.get("link"), b.get("negara")))

    cocok = [tautan.get((k, kunci_nama(n)), (None, None, None))
             for k, n in zip(ps.kode, ps.pemain)]
    ps["nama_resmi"] = [c[0] for c in cocok]
    ps["link"] = [c[1] for c in cocok]
    ps["negara"] = [c[2] for c in cocok]

    urut = ["season", "kode", "pemain", "nama_resmi", "link", "negara", "role",
            "main", "kill", "kill_rata", "mati", "mati_rata", "assist",
            "assist_rata", "kda", "partisipasi"]
    return ps.reindex(columns=urut).sort_values(
        ["kode", "main"], ascending=[True, False]).reset_index(drop=True)


def stat_tim() -> pd.DataFrame:
    """Statistik objektif tiap tim di musim berjalan.

    Kill/death/assist plus objektif khas MLBB: Lord, Turtle, dan Tower.
    Satu baris per tim, jadi grain-nya sama dengan `klasemen()` dan bisa
    ditempel langsung ke sana.
    """
    berkas = DATA_DIR / "team_stats.csv"
    if not berkas.exists():
        return pd.DataFrame()

    ts = pd.read_csv(berkas)
    ts = ts.rename(columns={"team_code": "kode", "team_name": "nama_sumber",
                            "kills": "kill", "deaths": "mati",
                            "assists": "assist", "tortoise": "turtle"})
    ts["season"] = MUSIM_ID_MPL
    ts["kode"] = ts.kode.map(normal)
    urut = ["season", "kode", "nama_sumber", "kill", "mati", "assist",
            "gold", "damage", "lord", "turtle", "tower"]
    return ts.reindex(columns=urut).reset_index(drop=True)


def awards() -> pd.DataFrame:
    """Semua penghargaan musim: Finals MVP, Regular Season MVP, Weekly MVP, dll.

    Sumbernya halaman utama musim (bukan /Statistics). Ini yang membuat Weekly
    MVP tersedia untuk SEMUA musim, bukan cuma S10-S11.
    """
    berkas = DATA_DIR / "awards.csv"
    if not berkas.exists():
        return pd.DataFrame()

    aw = pd.read_csv(berkas)
    cari = peta_tim()
    aw["kode"] = [cari(s, t) for s, t in zip(aw.season, aw.tim)]
    return aw


def weekly_mvp() -> pd.DataFrame:
    """Baris 'Week N MVP' saja, dengan nomor pekannya."""
    aw = awards()
    if aw.empty:
        return pd.DataFrame()
    w = aw[aw.award.str.match(r"Week \d+ MVP", na=False)].copy()
    w["week"] = w.award.str.extract(r"Week (\d+)").astype(int)
    return w[["season", "week", "pemain", "link", "tim", "kode"]]


def mvp_resmi() -> dict[int, dict]:
    """Musim -> pemenang award 'Regular Season MVP' menurut Liquipedia.

    Ini penghargaan resmi liga, dan TIDAK selalu sama dengan pemuncak papan
    poin 10/5 (lihat catatan di mvp_poin). Keduanya disajikan terpisah.
    """
    aw = awards()
    hasil = {}
    if aw.empty:
        return hasil
    rs = aw[aw.award.str.lower() == "regular season mvp"]
    for r in rs.itertuples(index=False):
        hasil[int(r.season)] = {"pemain": r.pemain, "kode": r.kode,
                                "tim": r.tim, "link": r.link}
    return hasil


def roster() -> pd.DataFrame:
    """Roster pemain tiap tim per musim: inti, cadangan, mantan, dan staf.

    Sumbernya halaman utama musim — sudah ter-cache, jadi tidak ada request
    tambahan. Role datang dari ikon di kartu tim, bukan dari urutan baris.
    """
    berkas = DATA_DIR / "rosters.csv"
    if not berkas.exists():
        return pd.DataFrame()

    rs = pd.read_csv(berkas)
    cari = peta_tim()
    rs["kode"] = [cari(s, t) for s, t in zip(rs.season, rs.tim)]
    return rs


# Urutan role sesuai posisi di peta, bukan alfabetis.
URUT_ROLE = ["EXP", "Jungle", "Mid", "Gold", "Roam"]


# ---------------------------------------------------------------------------
# MVP
# ---------------------------------------------------------------------------

POIN_MATCH_MVP = 10     # tiap Match MVP
POIN_WEEKLY_MVP = 5     # bonus Weekly MVP


def mvp_laga() -> pd.DataFrame:
    """MVP tiap laga regular season, sudah dengan kode tim.

    Dipakai oleh validate.py untuk mencocokkan hitungan Match MVP dengan tabel
    MVP resmi Liquipedia. Situsnya sendiri TIDAK lagi menampilkan papan poin
    10/5 — lihat catatan di README soal kenapa.

    Sumbernya footer popup di halaman Regular Season — bukan halaman
    Statistics. Untungnya begitu: tersedia untuk SEMUA musim S10-S17
    (544/544 laga), sementara tabel MVP di halaman Statistics ternyata cuma
    ada di S10 dan S11, dan itu pun terpotong (S11 hanya memuat 17 dari 25
    pemain yang benar-benar pernah jadi MVP).
    """
    berkas = DATA_DIR / "history_matches.csv"
    if not berkas.exists():
        return pd.DataFrame()

    hm = pd.read_csv(berkas)
    if "mvp" not in hm.columns:
        return pd.DataFrame()

    hm = hm[hm.mvp.notna() & hm.mvp_kode.notna()].copy()
    hm["mvp_kode"] = [kode_periode(k, s) for k, s in zip(hm.mvp_kode, hm.season)]
    return hm[["season", "week", "mvp", "mvp_link", "mvp_kode"]].rename(
        columns={"mvp": "pemain", "mvp_link": "link", "mvp_kode": "kode"})


def mvp_standing() -> pd.DataFrame:
    """Tabel MVP resmi Liquipedia (hanya S10 & S11 yang punya)."""
    berkas = DATA_DIR / "mvp_standing.csv"
    if not berkas.exists():
        return pd.DataFrame()
    return pd.read_csv(berkas)



if __name__ == "__main__":
    df = semua_laga()
    print(f"total {len(df)} laga, {df.season.nunique()} musim\n")
    ring = df.groupby(["season", "sumber"]).agg(
        laga=("status", "size"),
        selesai=("status", lambda s: (s == "selesai").sum()),
        tim=("kode1", lambda _: 0),
    ).reset_index()
    po = playoff()
    champ = juara(po)
    for s in sorted(df.season.unique()):
        sub = df[df.season == s]
        tim = sorted(set(sub.kode1) | set(sub.kode2))
        n_po = int((po.season == s).sum()) if not po.empty else 0
        print(f"  S{s:<3} {len(sub):>3} reguler + {n_po:>2} playoff | {len(tim)} tim | "
              f"juara: {champ.get(s, '-'):5} | {sub.sumber.iloc[0]}")
