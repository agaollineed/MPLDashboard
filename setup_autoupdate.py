#!/usr/bin/env python3
"""setup_autoupdate.py — pasang/cabut penjadwal otomatis (launchd macOS).

Kenapa launchd, bukan cron atau `while True: sleep(300)`?
  - cron di macOS sudah lama tidak dianjurkan dan tidak sadar sleep/wake.
  - Skrip loop sendiri mati begitu Terminal ditutup atau Mac di-restart.
  - launchd menghidupkan ulang job-nya sendiri setelah login/bangun tidur,
    dan mengeksekusi interval yang terlewat sekali saja saat bangun.

Pakai:
    python setup_autoupdate.py --install     # pasang & nyalakan
    python setup_autoupdate.py --status      # lihat status job
    python setup_autoupdate.py --uninstall   # cabut & hentikan
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

LABEL = "com.hanoraga.mpl-autoupdate"
PROYEK = Path(__file__).resolve().parent
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

# Tiap 5 menit. Nilai ini yang menentukan seberapa cepat skor muncul di situs;
# lihat catatan di README soal kenapa 5 menit, bukan 1 jam.
INTERVAL_DETIK = 300


def plist_xml() -> str:
    python = PROYEK / ".venv" / "bin" / "python"
    skrip = PROYEK / "auto_update.py"
    log_dir = PROYEK / "logs"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{skrip}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{PROYEK}</string>

    <key>StartInterval</key>
    <integer>{INTERVAL_DETIK}</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{log_dir}/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/launchd.err.log</string>
</dict>
</plist>
"""


def uid() -> str:
    return subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()


def jalankan(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def install() -> int:
    (PROYEK / "logs").mkdir(exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)

    # Cabut dulu kalau sudah pernah dipasang, supaya versi lama tidak nyangkut.
    jalankan("launchctl", "bootout", f"gui/{uid()}/{LABEL}")

    PLIST.write_text(plist_xml(), encoding="utf-8")
    print(f"  [tulis] {PLIST}")

    hasil = jalankan("launchctl", "bootstrap", f"gui/{uid()}", str(PLIST))
    if hasil.returncode != 0:
        print(f"  [gagal] {hasil.stderr.strip()}", file=sys.stderr)
        return 1

    print(f"  [aktif] {LABEL} — tiap {INTERVAL_DETIK // 60} menit")
    return status()


def uninstall() -> int:
    hasil = jalankan("launchctl", "bootout", f"gui/{uid()}/{LABEL}")
    print("  [henti]", "ok" if hasil.returncode == 0 else hasil.stderr.strip())
    if PLIST.exists():
        PLIST.unlink()
        print(f"  [hapus] {PLIST}")
    return 0


def status() -> int:
    hasil = jalankan("launchctl", "print", f"gui/{uid()}/{LABEL}")
    if hasil.returncode != 0:
        print("  [status] tidak terpasang")
        return 0
    for baris in hasil.stdout.splitlines():
        if any(k in baris for k in ("state =", "last exit code", "runs =",
                                    "pid =", "path =")):
            print("  [status]", baris.strip())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Penjadwal otomatis MPL scraper")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    if args.status:
        return status()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
