#!/usr/bin/env python3
"""build.py — Bangun basis data SQLite dari dataset/*.csv.

    dataset/*.csv  ──►  schema.sql + views.sql  ──►  database/mpl.db

Skrip ini selalu membangun ULANG dari nol. Basis datanya adalah HASIL, bukan
sumber: kalau ada yang perlu diubah, yang disunting adalah scraper, dataset.py,
atau schema.sql — lalu jalankan ini lagi. Dengan begitu tidak pernah ada
perubahan yang cuma hidup di dalam berkas .db dan hilang jejaknya.

Jalankan:
    python database/build.py            # bangun + periksa
    python database/build.py --periksa  # periksa saja, tanpa membangun ulang
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
PROYEK = HERE.parent
SUMBER = PROYEK / "dataset"
DB = HERE / "mpl.db"

VERSI_SKEMA = "1.0.0"

# Urutan penting: tabel induk harus terisi sebelum anaknya, kalau tidak
# penegakan kunci asing menolak barisnya.
URUTAN = ["role", "tim", "pemain", "hero", "musim", "tim_musim", "roster",
          "pemain_musim", "laga", "game", "pick_ban", "hero_musim",
          "hero_relasi", "award"]

# `role` tidak punya CSV sendiri — isinya ditetapkan di sini karena memang
# kamus, bukan hasil scraping. Urutannya mengikuti posisi di peta.
ROLE = [("EXP", 1), ("Jungle", 2), ("Mid", 3), ("Gold", 4), ("Roam", 5),
        ("Flex", 6)]

# Dua sumber menulis role berbeda; disatukan ke bentuk kanonik di atas.
PETA_ROLE = {"exp": "EXP", "jungle": "Jungle", "jgl": "Jungle", "mid": "Mid",
             "gold": "Gold", "goldlane": "Gold", "roam": "Roam", "flex": "Flex"}


def ke_int(v):
    """Ubah nilai apa pun jadi INTEGER SQLite, atau None.

    Perlu lebih dari int(): CSV menyimpan boolean sebagai teks 'True'/'False',
    dan kolom integer yang punya sel kosong terbaca pandas sebagai float.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return None
    if isinstance(v, (bool,)):
        return int(v)
    if isinstance(v, str):
        t = v.strip()
        if t.lower() in ("true", "false"):
            return int(t.lower() == "true")
        if t == "":
            return None
        return int(float(t))
    return int(v)


def ke_real(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return float(v)


def ke_teks(v):
    if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)):
        return None
    return str(v)


PENGUBAH = {"INTEGER": ke_int, "INT": ke_int, "REAL": ke_real, "TEXT": ke_teks}


def baca(nama: str) -> pd.DataFrame:
    berkas = SUMBER / f"{nama}.csv"
    if not berkas.exists():
        raise SystemExit(f"Tidak ada {berkas}. Jalankan build_dataset.py dulu.")
    return pd.read_csv(berkas)


def sumber_tabel(nama: str) -> pd.DataFrame:
    """Ambil isi satu tabel, sudah dinormalkan seperlunya."""
    if nama == "role":
        return pd.DataFrame(ROLE, columns=["role", "urutan"])

    df = baca(nama)
    if "role" in df.columns:
        # Normalkan ejaan role supaya roster dan statistik pemain bisa di-JOIN.
        df["role"] = [PETA_ROLE.get(str(r).strip().lower())
                      if pd.notna(r) else None for r in df.role]
    return df


def bangun(conn: sqlite3.Connection) -> dict[str, int]:
    conn.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))

    jumlah: dict[str, int] = {}
    for nama in URUTAN:
        df = sumber_tabel(nama)
        kolom_db = [(r[1], r[2].upper()) for r in
                    conn.execute(f"PRAGMA table_info({nama})")]

        hilang = [k for k, _ in kolom_db if k not in df.columns]
        if hilang:
            raise SystemExit(f"{nama}: kolom {hilang} tidak ada di CSV")

        ubah = [PENGUBAH.get(t, ke_teks) for _, t in kolom_db]
        nilai = [tuple(f(v) for f, v in zip(ubah, baris))
                 for baris in df[[k for k, _ in kolom_db]].itertuples(index=False)]

        conn.executemany(
            f"INSERT INTO {nama} VALUES ({','.join('?' * len(kolom_db))})", nilai)
        jumlah[nama] = len(nilai)
        print(f"  [{nama:14}] {len(nilai):>6} baris")

    conn.executescript((views.read_text(encoding="utf-8")
                        if (views := HERE / "views.sql").exists() else ""))
    return jumlah


def tulis_meta(conn: sqlite3.Connection, jumlah: dict[str, int]) -> None:
    sidik = hashlib.sha256()
    for f in sorted(SUMBER.glob("*.csv")):
        sidik.update(f.read_bytes())

    meta = {
        "versi_skema": VERSI_SKEMA,
        "dibangun_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sumber": "dataset/*.csv",
        "sidik_sumber_sha256": sidik.hexdigest(),
        "sqlite": sqlite3.sqlite_version,
        "lisensi_data": "Liquipedia CC-BY-SA 3.0; id-mpl.com",
    }
    conn.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
    conn.executemany(
        "INSERT INTO meta_tabel VALUES (?,?,?)",
        [(n, j, "kamus" if n == "role" else f"dataset/{n}.csv")
         for n, j in jumlah.items()])


def periksa(conn: sqlite3.Connection) -> int:
    """Tiga lapis pemeriksaan setelah muat."""
    masalah = 0

    rusak = conn.execute("PRAGMA integrity_check").fetchall()
    ok_integritas = rusak == [("ok",)]
    print(f"  {'OK   ' if ok_integritas else 'GAGAL'} integritas berkas")
    masalah += 0 if ok_integritas else 1

    yatim = conn.execute("PRAGMA foreign_key_check").fetchall()
    print(f"  {'OK   ' if not yatim else 'GAGAL'} kunci asing "
          f"({len(yatim)} baris yatim)")
    for y in yatim[:5]:
        print(f"        {y}")
    masalah += len(yatim)

    # Jumlah baris di basis data harus sama dengan CSV-nya. CHECK yang gagal
    # akan membatalkan INSERT-nya, jadi selisih di sini menangkap baris yang
    # diam-diam tidak masuk.
    beda = 0
    for nama in URUTAN:
        db_n = conn.execute(f"SELECT COUNT(*) FROM {nama}").fetchone()[0]
        csv_n = len(sumber_tabel(nama))
        if db_n != csv_n:
            beda += 1
            print(f"  GAGAL {nama}: {db_n} baris di DB vs {csv_n} di CSV")
    print(f"  {'OK   ' if not beda else 'GAGAL'} jumlah baris cocok dengan CSV "
          f"({len(URUTAN)} tabel)")
    masalah += beda

    # View harus benar-benar bisa dijalankan. View yang salah ketik baru
    # ketahuan saat dipakai, bukan saat dibuat.
    view = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")]
    for v in view:
        try:
            conn.execute(f"SELECT * FROM {v} LIMIT 1").fetchall()
        except sqlite3.Error as e:
            masalah += 1
            print(f"  GAGAL view {v}: {e}")
    print(f"  {'OK   ' if masalah == 0 else '     '} {len(view)} view bisa dijalankan")

    return masalah


def main() -> int:
    ap = argparse.ArgumentParser(description="Bangun basis data SQLite MPL ID")
    ap.add_argument("--periksa", action="store_true",
                    help="periksa basis data yang sudah ada, tanpa membangun ulang")
    args = ap.parse_args()

    if args.periksa:
        if not DB.exists():
            raise SystemExit(f"{DB} belum ada. Jalankan tanpa --periksa dulu.")
        with sqlite3.connect(DB) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            print(f"Memeriksa {DB.name} ...\n")
            n = periksa(conn)
        print("\nBASIS DATA SEHAT" if n == 0 else f"\nADA {n} MASALAH")
        return 0 if n == 0 else 1

    DB.unlink(missing_ok=True)
    print(f"Membangun {DB.name} dari {SUMBER.name}/ ...\n")

    with sqlite3.connect(DB) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        jumlah = bangun(conn)
        tulis_meta(conn, jumlah)
        print()
        n = periksa(conn)
        conn.execute("ANALYZE")

    # VACUUM tidak boleh di dalam transaksi, jadi koneksi terpisah.
    with sqlite3.connect(DB) as conn:
        conn.execute("VACUUM")

    total = sum(jumlah.values())
    print(f"\n  {len(jumlah)} tabel, {total:,} baris, "
          f"{DB.stat().st_size / 1024:,.0f} KB")
    print("\nBASIS DATA SIAP" if n == 0 else f"\nADA {n} MASALAH")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
