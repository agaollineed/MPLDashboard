#!/usr/bin/env python3
"""auto_update.py — satu siklus pembaruan otomatis. Dipanggil launchd tiap 5 menit.

Yang dikerjakan tiap kali dipanggil:

    1. Lihat jadwal: apakah SEKARANG ada pertandingan berlangsung?
       Kalau tidak -> berhenti tanpa menyentuh jaringan sama sekali.
    2. Kalau ya  -> GET bersyarat ke id-mpl.com (If-Modified-Since).
       Kalau server jawab 304 -> berhenti, nol byte terunduh.
    3. Kalau ada yang berubah -> parse ulang, tulis CSV, bangun ulang situs,
       lalu catat skor apa saja yang berubah ke log.

Tiga lapis rem itu disengaja. Polling tiap 5 menit terdengar agresif, tapi
yang benar-benar sampai ke server orang cuma request kecil di jam pertandingan,
dan hampir semuanya dijawab 304.

Pakai manual:
    python auto_update.py            # satu siklus, hormati jendela jadwal
    python auto_update.py --force    # paksa jalan walau di luar jam main
    python auto_update.py --status   # cuma lapor: sekarang mau ngapain?
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROYEK = Path(__file__).resolve().parent

# launchd menjalankan skrip dengan direktori kerja "/", bukan folder proyek.
# mpl_scraper dan build_site memakai path relatif ("cache/", "data/", "site/"),
# jadi tanpa baris ini berkasnya akan berhamburan ke root filesystem.
os.chdir(PROYEK)
DATA_DIR = PROYEK / "data"
LOG_DIR = PROYEK / "logs"
LOG_FILE = LOG_DIR / "auto_update.log"
STATE_FILE = LOG_DIR / "state.json"

LOG_MAKS = 1_000_000  # byte; di atas ini log dipangkas

# Jendela pantau, dihitung relatif terhadap jadwal hari itu.
SEBELUM = timedelta(minutes=20)              # mulai memantau sebelum laga pertama
SESUDAH = timedelta(hours=3, minutes=30)     # BO3 terakhir + jeda posting hasil

# Kalau seharian tidak ada laga, tetap cek sekali sehari — supaya perubahan
# jadwal (jam digeser, pekan baru dibuka) tetap ketahuan.
JEDA_HARIAN = timedelta(hours=20)


# ---------------------------------------------------------------------------
# LOG
# ---------------------------------------------------------------------------

def _pangkas(berkas: Path) -> None:
    """Potong log yang kegemukan — sisakan separuh terakhir.

    Berlaku juga untuk launchd.out.log: launchd menambahkan stdout terus-menerus
    tanpa pernah memutarnya sendiri. 288 siklus sehari x setahun = berkas raksasa
    kalau dibiarkan.
    """
    try:
        if berkas.exists() and berkas.stat().st_size > LOG_MAKS:
            isi = berkas.read_text(encoding="utf-8", errors="replace").splitlines()
            berkas.write_text("\n".join(isi[len(isi) // 2:]) + "\n", encoding="utf-8")
    except OSError:
        pass  # log bermasalah bukan alasan untuk menggagalkan pembaruan


def log(pesan: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    baris = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {pesan}"
    print(baris)
    for berkas in (LOG_FILE, LOG_DIR / "launchd.out.log", LOG_DIR / "launchd.err.log"):
        _pangkas(berkas)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(baris + "\n")


def baca_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def tulis_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# KAPAN HARUS JALAN?
# ---------------------------------------------------------------------------

def jendela_hari_ini(sekarang: datetime) -> tuple[datetime, datetime] | None:
    """Rentang waktu pantau untuk hari ini — DIHITUNG DARI JADWAL, bukan dipatok.

    Godaannya adalah menulis "tiap Jumat-Minggu jam 14-23". Jangan. Jadwal MPL
    berubah: musim ini ada 3 laga hari KAMIS, dan jam mulainya campur antara
    14:00, 15:00, 17:00, 18:00, dan 20:00. Dengan membaca schedule.csv, jendela
    ini menyesuaikan diri sendiri setiap kali jadwalnya di-scrape ulang.
    """
    berkas = DATA_DIR / "schedule.csv"
    if not berkas.exists():
        return None

    sc = pd.read_csv(berkas)
    hari_ini = sekarang.strftime("%Y-%m-%d")
    laga = sc[(sc.tanggal == hari_ini) & sc.jam.notna()]
    if laga.empty:
        return None

    def gabung(jam: str) -> datetime:
        h, m = str(jam).replace(".", ":").split(":")
        return sekarang.replace(hour=int(h), minute=int(m), second=0, microsecond=0)

    mulai = min(gabung(j) for j in laga.jam)
    akhir = max(gabung(j) for j in laga.jam)
    return mulai - SEBELUM, akhir + SESUDAH


def perlu_jalan(sekarang: datetime, state: dict) -> tuple[bool, str]:
    """Putuskan: siklus ini lanjut ke jaringan, atau berhenti di sini?"""
    jendela = jendela_hari_ini(sekarang)
    if jendela and jendela[0] <= sekarang <= jendela[1]:
        return True, f"jam main ({jendela[0]:%H:%M}-{jendela[1]:%H:%M})"

    terakhir = state.get("cek_terakhir")
    if terakhir:
        selisih = sekarang - datetime.fromisoformat(terakhir)
        if selisih < JEDA_HARIAN:
            sisa = JEDA_HARIAN - selisih
            alasan = "di luar jam main" if not jendela else \
                     f"di luar jendela ({jendela[0]:%H:%M}-{jendela[1]:%H:%M})"
            return False, f"{alasan}, cek harian berikutnya ~{sisa.seconds // 3600}j lagi"

    return True, "cek harian (pantau perubahan jadwal)"


# ---------------------------------------------------------------------------
# APA YANG BERUBAH?
# ---------------------------------------------------------------------------

def potret_skor() -> dict:
    """Ambil potret skor saat ini, untuk dibandingkan setelah scrape."""
    berkas = DATA_DIR / "schedule.csv"
    if not berkas.exists():
        return {}
    sc = pd.read_csv(berkas)
    return {
        (int(r.week), r.tim1, r.tim2): (int(r.skor1), int(r.skor2), r.status)
        for r in sc.itertuples(index=False)
    }


def beda_skor(sebelum: dict, sesudah: dict) -> list[str]:
    """Bandingkan dua potret, hasilkan daftar perubahan yang enak dibaca."""
    pesan = []
    for kunci, baru in sesudah.items():
        lama = sebelum.get(kunci)
        if lama == baru:
            continue
        week, t1, t2 = kunci
        s1, s2, status = baru
        if lama is None:
            pesan.append(f"laga baru: W{week} {t1} vs {t2}")
        elif lama[2] != status and status == "selesai":
            pesan.append(f"SELESAI: W{week} {t1} {s1}-{s2} {t2}")
        else:
            pesan.append(f"skor berubah: W{week} {t1} {s1}-{s2} {t2} ({status})")
    return pesan


# ---------------------------------------------------------------------------
# SIKLUS
# ---------------------------------------------------------------------------

def siklus(paksa: bool) -> int:
    sekarang = datetime.now()
    state = baca_state()

    jalan, alasan = (True, "dipaksa (--force)") if paksa else perlu_jalan(sekarang, state)
    if not jalan:
        log(f"lewat — {alasan}")
        return 0

    log(f"cek — {alasan}")

    # Import di sini, bukan di atas: kalau siklusnya cuma "lewat", kita tidak
    # perlu membayar ongkos memuat pandas-nya scraper & generator situs.
    import build_site
    import mpl_scraper

    sebelum = potret_skor()
    berubah = mpl_scraper.run_scrape(DATA_DIR, use_cache=False,
                                     revalidate=True, ringkas=True)

    state["cek_terakhir"] = sekarang.isoformat(timespec="seconds")

    if not berubah:
        log("  tidak ada perubahan (304)")
        tulis_state(state)
        return 0

    build_site.main()

    perubahan = beda_skor(sebelum, potret_skor())
    for p in perubahan:
        log(f"  >> {p}")
    if not perubahan:
        log("  halaman sumber berubah, tapi skor/jadwal sama")

    state["perubahan_terakhir"] = sekarang.isoformat(timespec="seconds")
    state["perubahan_terakhir_isi"] = perubahan
    tulis_state(state)
    return 0


def tampilkan_status() -> int:
    sekarang = datetime.now()
    state = baca_state()
    jendela = jendela_hari_ini(sekarang)
    jalan, alasan = perlu_jalan(sekarang, state)

    print(f"sekarang         : {sekarang:%Y-%m-%d %H:%M:%S} WIB")
    print(f"jendela hari ini : "
          + (f"{jendela[0]:%H:%M} - {jendela[1]:%H:%M}" if jendela
             else "tidak ada laga hari ini"))
    print(f"siklus berikutnya: {'JALAN' if jalan else 'LEWAT'} — {alasan}")
    print(f"cek terakhir     : {state.get('cek_terakhir', '-')}")
    print(f"perubahan terakhir: {state.get('perubahan_terakhir', '-')}")
    for p in state.get("perubahan_terakhir_isi", [])[:5]:
        print(f"   >> {p}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Siklus pembaruan otomatis MPL")
    ap.add_argument("--force", action="store_true",
                    help="jalan walau di luar jam pertandingan")
    ap.add_argument("--status", action="store_true",
                    help="lapor rencana siklus berikutnya, tanpa menjalankannya")
    args = ap.parse_args()

    if args.status:
        return tampilkan_status()

    try:
        return siklus(args.force)
    except Exception:
        # Jangan pernah keluar dengan kode error: launchd akan menganggapnya
        # crash dan mulai membatasi laju (throttle). Cukup catat, coba lagi
        # 5 menit kemudian.
        log("GAGAL — " + traceback.format_exc().strip().replace("\n", " | ")[:500])
        return 0


if __name__ == "__main__":
    sys.exit(main())
