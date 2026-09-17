#!/usr/bin/env python3
"""build_dataset.py — Susun CSV mentah jadi dataset relasional ternormalisasi.

Berkas di data/ itu hasil scraping: bentuknya mengikuti halaman sumbernya,
bukan mengikuti struktur datanya. Skrip ini menatanya ulang jadi tabel-tabel
yang saling terhubung lewat kunci, sehingga bisa dipakai untuk analisis atau
dimuat ke basis data.

    python build_dataset.py            # tulis ke dataset/
    python build_dataset.py --periksa  # tulis + uji keterkaitan antar tabel

Bentuknya:

    DIMENSI                     FAKTA
    tim ──────────┐             tim_musim   (season, kode)
    musim ────────┼──────────►  roster      (season, kode, pemain_id)
    pemain ───────┤             laga        (laga_id)
    hero ─────────┘             game        (laga_id, game)
                                pick_ban    (laga_id, game, kode, jenis, urutan)
                                hero_musim  (season, hero)
                                hero_relasi (season, hero, jenis, hero_lain)
                                award       (season, award, pemain_id)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

import dataset as D
from dataset import pemain_id

OUT = Path("dataset")


# ---------------------------------------------------------------------------
# UTILITAS
# ---------------------------------------------------------------------------

def tulis(df: pd.DataFrame, nama: str, kunci: list[str]) -> pd.DataFrame:
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / f"{nama}.csv", index=False, encoding="utf-8-sig")
    print(f"  [{nama:12}] {len(df):>5} baris x {len(df.columns):>2} kolom"
          f"   PK: ({', '.join(kunci)})")
    return df


# ---------------------------------------------------------------------------
# DIMENSI
# ---------------------------------------------------------------------------

def dim_tim() -> pd.DataFrame:
    return pd.DataFrame(
        [{"kode": k, "nama": n, "logo": b} for k, (n, b) in D.TIM.items()]
    ).sort_values("kode").reset_index(drop=True)


def dim_hero(hs: pd.DataFrame, pb: pd.DataFrame) -> pd.DataFrame:
    nama = set(hs.hero.dropna()) if not hs.empty else set()
    if not pb.empty:
        for k in [f"pick{i}" for i in range(1, 6)] + [f"ban{i}" for i in range(1, 6)]:
            nama |= set(pb[k].dropna())
    return pd.DataFrame({"hero": sorted(nama)})


def dim_pemain(awards: pd.DataFrame, laga: pd.DataFrame) -> pd.DataFrame:
    """Satu baris per pemain, dikumpulkan dari roster, award, dan MVP laga.

    Sengaja memakai roster MENTAH (D.roster()) yang masih punya kolom `link`,
    bukan tabel roster hasil olahan yang link-nya sudah diganti pemain_id.
    """
    baris = {}

    for r in D.roster().itertuples(index=False):
        pid = pemain_id(r.link, r.pemain)
        if pid:
            baris.setdefault(pid, {"pemain_id": pid, "nama": r.pemain,
                                   "negara": r.negara})

    for r in awards.itertuples(index=False):
        pid = pemain_id(r.link, r.pemain)
        if pid:
            baris.setdefault(pid, {"pemain_id": pid, "nama": r.pemain,
                                   "negara": None})

    for r in laga.itertuples(index=False):
        pid = pemain_id(getattr(r, "mvp_link", None), getattr(r, "mvp", None))
        if pid:
            baris.setdefault(pid, {"pemain_id": pid, "nama": r.mvp,
                                   "negara": None})

    return pd.DataFrame(baris.values()).sort_values("pemain_id").reset_index(drop=True)


def dim_musim(laga: pd.DataFrame, tm: pd.DataFrame) -> pd.DataFrame:
    champ = D.juara(D.playoff())
    resmi = D.mvp_resmi()
    baris = []
    for s in sorted(laga.season.unique()):
        sub = laga[laga.season == s]
        mvp = resmi.get(int(s), {})
        baris.append({
            "season": int(s),
            "sumber": sub.sumber.iloc[0],
            "jumlah_tim": int((tm.season == s).sum()),
            "laga_reguler": int((sub.stage == "reguler").sum()),
            "laga_playoff": int((sub.stage == "playoff").sum()),
            "juara_kode": champ.get(int(s)),
            "mvp_pemain_id": pemain_id(mvp.get("link"), mvp.get("pemain")),
        })
    return pd.DataFrame(baris)


# ---------------------------------------------------------------------------
# FAKTA
# ---------------------------------------------------------------------------

def fakta_laga() -> pd.DataFrame:
    """Satu tabel laga untuk regular season DAN playoff, dengan kunci sendiri."""
    reg = D.semua_laga().copy()
    reg["stage"] = "reguler"
    reg["ronde"] = None
    reg["format"] = None

    hm = Path("data/history_matches.csv")
    if hm.exists():
        raw = pd.read_csv(hm)[["season", "timestamp", "mvp", "mvp_link"]]
        reg = reg.merge(raw, on=["season", "timestamp"], how="left")
    else:
        reg["mvp"] = reg["mvp_link"] = None

    po = D.playoff().copy()
    po["stage"] = "playoff"
    po["week"] = None
    po["status"] = "selesai"
    po["sumber"] = "liquipedia"
    po["mvp"] = po["mvp_link"] = None

    kol = ["season", "stage", "week", "ronde", "tanggal", "jam", "timestamp",
           "kode1", "kode2", "skor1", "skor2", "status", "format",
           "mvp", "mvp_link", "sumber"]
    df = pd.concat([reg.reindex(columns=kol), po.reindex(columns=kol)],
                   ignore_index=True)
    df = df.sort_values(["season", "stage", "tanggal", "jam"],
                        na_position="last").reset_index(drop=True)

    # Kunci laga dibuat sendiri: S17R001 / S17P003. Timestamp tidak dipakai
    # sebagai kunci karena musim berjalan (dari id-mpl.com) tidak punya.
    urut = df.groupby(["season", "stage"]).cumcount() + 1
    df.insert(0, "laga_id", [f"S{int(s)}{'R' if st == 'reguler' else 'P'}{i:03d}"
                             for s, st, i in zip(df.season, df.stage, urut)])
    df["mvp_pemain_id"] = [pemain_id(l, n) for l, n in zip(df.mvp_link, df.mvp)]
    return df.drop(columns=["mvp_link"])


def fakta_tim_musim() -> pd.DataFrame:
    laga = D.semua_laga()
    baris = []
    for s in sorted(laga.season.unique()):
        st = D.statistik_tim(int(s))
        kl = D.klasemen(laga[laga.season == s]).set_index("kode")
        for r in st.itertuples(index=False):
            k = kl.loc[r.kode]
            baris.append({
                "season": int(s), "kode": r.kode,
                "peringkat": int(k.peringkat), "main": r.main,
                "menang": r.menang, "kalah": r.kalah, "win_rate": r.win_rate,
                "game_menang": r.game_menang, "game_kalah": r.game_kalah,
                "game_win_rate": r.game_win_rate,
                "belum_main": int(k.belum_main),
            })

    tm = pd.DataFrame(baris)

    # Kill/gold/objektif punya grain yang SAMA dengan klasemen (satu baris per
    # tim per musim), jadi tempatnya memang di tabel ini — bukan tabel baru
    # yang berelasi satu-ke-satu. Kolomnya kosong untuk musim yang datanya
    # cuma dari Liquipedia; itu memang batas sumbernya, bukan kegagalan parse.
    st = D.stat_tim()
    if not st.empty:
        tm = tm.merge(st.drop(columns=["nama_sumber"]),
                      on=["season", "kode"], how="left")

    # Nasib playoff juga bergrain (season, kode), jadi ikut ke tabel ini.
    # `lolos_playoff` dibedakan dari kosong: False berarti ikut musimnya tapi
    # tidak lolos, kosong berarti playoff musim itu memang belum dimainkan.
    hp = D.hasil_playoff()
    if not hp.empty:
        tm = tm.merge(hp[["season", "kode", "hasil", "peringkat_playoff"]]
                      .rename(columns={"hasil": "hasil_playoff"}),
                      on=["season", "kode"], how="left")
        ada_po = set(hp.season)
        tm["lolos_playoff"] = [
            (b.hasil_playoff == b.hasil_playoff) if b.season in ada_po else None
            for b in tm.itertuples(index=False)]
    return tm


def fakta_roster() -> pd.DataFrame:
    """Keanggotaan regu per musim, lengkap dengan penanda juara.

    `juara` sengaja tinggal di sini, bukan di tabel award: gelar juara melekat
    pada REGU (cadangan ikut terhitung), sementara award berisi penghargaan
    perorangan seperti MVP dan Team of the Week. Menggabungkan keduanya akan
    membuat hitungan "berapa penghargaan yang dia dapat" jadi salah.
    """
    rs = D.roster()
    rs = rs[rs.kode.notna()].copy()
    rs["pemain_id"] = [pemain_id(l, n) for l, n in zip(rs.link, rs.pemain)]

    champ = D.juara(D.playoff())
    rs["juara"] = [bool(champ.get(int(s)) == k) and g in ("Main", "Subs")
                   for s, k, g in zip(rs.season, rs.kode, rs.grup)]
    return rs[["season", "kode", "pemain_id", "grup", "role", "negara",
               "catatan", "tim", "juara"]].rename(columns={"tim": "nama_periode"})


def fakta_pemain_musim() -> pd.DataFrame:
    """Statistik per pemain per musim (baru ada untuk musim berjalan).

    Grain-nya (season, kode, pemain). `pemain_id` sengaja boleh kosong:
    beberapa pemain sudah main menurut situs resmi MPL tapi belum tercatat di
    roster Liquipedia, jadi belum punya halaman. Memaksa mereka punya id
    justru akan memalsukan keterkaitan yang sebetulnya belum ada.
    """
    sp = D.stat_pemain()
    if sp.empty:
        return pd.DataFrame()

    sp = sp.copy()
    sp["pemain_id"] = [pemain_id(l, n) for l, n in zip(sp.link, sp.nama_resmi)]
    return sp[["season", "kode", "pemain", "pemain_id", "role", "main",
               "kill", "kill_rata", "mati", "mati_rata", "assist",
               "assist_rata", "kda", "partisipasi"]]


def fakta_game_dan_pickban(laga: pd.DataFrame):
    pb = D.picks_bans()
    if pb.empty:
        return pd.DataFrame(), pd.DataFrame()

    kunci = {(int(r.season), r.stage, int(r.timestamp)): r.laga_id
             for r in laga.itertuples(index=False) if pd.notna(r.timestamp)}
    pb = pb[pb.timestamp.notna()].copy()
    pb["laga_id"] = [kunci.get((int(s), st, int(t)))
                     for s, st, t in zip(pb.season, pb.stage, pb.timestamp)]
    pb = pb[pb.laga_id.notna()]

    game = (pb.groupby(["laga_id", "game"], as_index=False)
              .agg(durasi=("durasi", "first")))

    panjang = []
    for r in pb.itertuples(index=False):
        for jenis, awalan in (("pick", "pick"), ("ban", "ban")):
            for i in range(1, 6):
                hero = getattr(r, f"{awalan}{i}")
                if isinstance(hero, str) and hero:
                    panjang.append({"laga_id": r.laga_id, "game": int(r.game),
                                    "kode": r.kode, "jenis": jenis,
                                    "urutan": i, "hero": hero})
    return game, pd.DataFrame(panjang)


def fakta_award() -> pd.DataFrame:
    aw = D.awards().copy()
    aw["pemain_id"] = [pemain_id(l, n) for l, n in zip(aw.link, aw.pemain)]
    aw["kategori"] = [
        "weekly_mvp" if re.match(r"Week \d+ MVP", a or "") else
        "weekly_rookie" if re.match(r"Week \d+ Best Rookie", a or "") else
        "team_of_week" if re.match(r"Team of [Tt]he Week", a or "") else "musim"
        for a in aw.award]
    aw["week"] = [int(m.group(1)) if (m := re.search(r"(\d+)", a or "")) and
                  re.match(r"(Week|Team of)", a or "") else None
                  for a in aw.award]
    return aw[["season", "award", "kategori", "week", "pemain_id", "kode",
               "hadiah"]].rename(columns={"kode": "kode_tim"})


# ---------------------------------------------------------------------------
# PEMERIKSAAN KETERKAITAN
# ---------------------------------------------------------------------------

def periksa(t: dict) -> int:
    """Uji setiap kunci asing benar-benar menunjuk baris yang ada."""
    aturan = [
        ("tim_musim.kode", t["tim_musim"].kode, t["tim"].kode),
        ("tim_musim.season", t["tim_musim"].season, t["musim"].season),
        ("roster.kode", t["roster"].kode, t["tim"].kode),
        ("roster.pemain_id", t["roster"].pemain_id, t["pemain"].pemain_id),
        ("pemain_musim.kode", t["pemain_musim"].kode, t["tim"].kode),
        ("pemain_musim.season", t["pemain_musim"].season, t["musim"].season),
        ("pemain_musim.pemain_id", t["pemain_musim"].pemain_id,
         t["pemain"].pemain_id),
        ("laga.kode1", t["laga"].kode1, t["tim"].kode),
        ("laga.kode2", t["laga"].kode2, t["tim"].kode),
        ("laga.season", t["laga"].season, t["musim"].season),
        ("laga.mvp_pemain_id", t["laga"].mvp_pemain_id, t["pemain"].pemain_id),
        ("game.laga_id", t["game"].laga_id, t["laga"].laga_id),
        ("pick_ban.laga_id", t["pick_ban"].laga_id, t["laga"].laga_id),
        ("pick_ban.kode", t["pick_ban"].kode, t["tim"].kode),
        ("pick_ban.hero", t["pick_ban"].hero, t["hero"].hero),
        ("hero_musim.hero", t["hero_musim"].hero, t["hero"].hero),
        ("hero_relasi.hero", t["hero_relasi"].hero, t["hero"].hero),
        ("hero_relasi.hero_lain", t["hero_relasi"].hero_lain, t["hero"].hero),
        ("hero_relasi.season", t["hero_relasi"].season, t["musim"].season),
        ("hero_musim.season", t["hero_musim"].season, t["musim"].season),
        ("award.pemain_id", t["award"].pemain_id, t["pemain"].pemain_id),
        ("award.kode_tim", t["award"].kode_tim, t["tim"].kode),
        ("musim.juara_kode", t["musim"].juara_kode, t["tim"].kode),
        ("musim.mvp_pemain_id", t["musim"].mvp_pemain_id, t["pemain"].pemain_id),
    ]

    print(f"\n{'KUNCI ASING':26}{'DIPERIKSA':>10}{'YATIM':>7}")
    total = 0
    for nama, anak, induk in aturan:
        nilai = anak.dropna()
        yatim = sorted(set(nilai) - set(induk.dropna()))
        total += len(yatim)
        tanda = "OK" if not yatim else f"{len(yatim)} YATIM"
        print(f"  {nama:24}{len(nilai):>10}{tanda:>7}")
        for y in yatim[:3]:
            print(f"      -> tidak ada induknya: {y!r}")

    # Kunci utama harus unik
    print()
    pk = [("tim", ["kode"]), ("musim", ["season"]), ("pemain", ["pemain_id"]),
          ("hero", ["hero"]), ("tim_musim", ["season", "kode"]),
          ("laga", ["laga_id"]), ("game", ["laga_id", "game"]),
          ("hero_musim", ["season", "hero"]),
          ("roster", ["season", "kode", "pemain_id", "grup"]),
          ("pemain_musim", ["season", "kode", "pemain"]),
          ("hero_relasi", ["season", "hero", "jenis", "hero_lain"]),
          ("pick_ban", ["laga_id", "game", "kode", "jenis", "urutan"])]
    for nama, kunci in pk:
        n_dup = int(t[nama].duplicated(subset=kunci).sum())
        total += n_dup
        print(f"  PK {nama:22}{'unik' if not n_dup else f'{n_dup} DUPLIKAT':>14}")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="Bangun dataset relasional")
    ap.add_argument("--periksa", action="store_true",
                    help="uji keterkaitan antar tabel setelah menulis")
    args = ap.parse_args()

    print("Menulis dataset/ ...\n")
    laga = fakta_laga()
    tm = fakta_tim_musim()
    roster = fakta_roster()
    aw_mentah = D.awards()
    aw = fakta_award()
    hs = D.hero_stats()
    pb_mentah = D.picks_bans()
    game, pick_ban = fakta_game_dan_pickban(laga)

    t = {}
    t["tim"] = tulis(dim_tim(), "tim", ["kode"])
    t["musim"] = tulis(dim_musim(laga, tm), "musim", ["season"])
    t["pemain"] = tulis(dim_pemain(aw_mentah, laga), "pemain", ["pemain_id"])
    t["hero"] = tulis(dim_hero(hs, pb_mentah), "hero", ["hero"])
    t["tim_musim"] = tulis(tm, "tim_musim", ["season", "kode"])
    t["roster"] = tulis(roster, "roster",
                        ["season", "kode", "pemain_id", "grup"])
    t["pemain_musim"] = tulis(fakta_pemain_musim(), "pemain_musim",
                              ["season", "kode", "pemain"])
    t["laga"] = tulis(laga, "laga", ["laga_id"])
    t["game"] = tulis(game, "game", ["laga_id", "game"])
    t["pick_ban"] = tulis(pick_ban, "pick_ban",
                          ["laga_id", "game", "kode", "jenis", "urutan"])
    t["hero_musim"] = tulis(hs, "hero_musim", ["season", "hero"])
    t["hero_relasi"] = tulis(D.hero_relasi(), "hero_relasi",
                             ["season", "hero", "jenis", "hero_lain"])
    t["award"] = tulis(aw, "award", ["season", "award", "pemain_id"])

    if args.periksa:
        n = periksa(t)
        print("\nSEMUA KETERKAITAN VALID" if n == 0 else f"\nADA {n} MASALAH")
        return 0 if n == 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
