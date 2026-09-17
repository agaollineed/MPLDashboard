#!/usr/bin/env python3
"""download_logos.py — Unduh logo tim & logo MPL ke site/assets/.

Kenapa diunduh, bukan di-hotlink langsung ke URL aslinya? Dua alasan:
1. Web-nya jadi berdiri sendiri — tetap jalan walau offline atau kalau CDN
   sumbernya (wsrv.nl / imagekit) berubah/mati.
2. Tidak menumpang bandwidth server orang tiap kali halaman dibuka.

Jalankan sekali saja:  python download_logos.py
"""

from pathlib import Path

import requests

from mpl_scraper import HEADERS, REQUEST_TIMEOUT

ASSET_DIR = Path("site/assets")

# Versi hi-res, diambil dari halaman /teams (bukan yang 64px di halaman jadwal).
IK = "https://ik.imagekit.io/nloe8dhf7w/mplid/s14/teams"

LOGOS = {
    "ae":   f"{IK}/ae-256.png",
    "btr":  f"{IK}/btr_vit.png",
    "dewa": f"{IK}/dewa-united-500.png",
    "evos": f"{IK}/evos-500.png",
    "geek": f"{IK}/geek-500.png",
    "navi": f"{IK}/NAVI-2.png",
    "onic": f"{IK}/onic-b-256.png",
    "rrq":  f"{IK}/rrq-500.png",
    "tlid": f"{IK}/TLID-Primary500x500.png",
    "mpl":  "https://id-mpl.com/images/s14/logo/LOGO_MPL-ID-NEW-2024-400.webp",

    # Tim yang cuma ada di musim lampau, jadi tidak tersedia di id-mpl.com.
    # Diambil dari berkas media Liquipedia (bukan halaman HTML — itu dilarang
    # ToS mereka), sekali saja lalu di-cache selamanya.
    "aura": "https://liquipedia.net/commons/images/8/89/AURA_Esports_allmode.png",
    "rbl":  "https://liquipedia.net/commons/images/0/0a/Rebellion_Esports_allmode.png",

    # Ikon role. Diambil dari berkas media Liquipedia, sekali lalu di-cache.
    "role-exp":    "https://liquipedia.net/commons/images/4/40/Mobile_Legends_EXP_Lane.png",
    "role-jungle": "https://liquipedia.net/commons/images/7/70/Mobile_Legends_Jungle.png",
    "role-mid":    "https://liquipedia.net/commons/images/d/d3/Mobile_Legends_Mid_Lane.png",
    "role-gold":   "https://liquipedia.net/commons/images/f/fd/Mobile_Legends_Gold_Lane.png",
    "role-roam":   "https://liquipedia.net/commons/images/2/27/Mobile_Legends_Roamer.png",
    "role-flex":   "https://liquipedia.net/commons/images/3/35/Fill_icon_Mobile_Legends.png",
}


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)

    for nama, url in LOGOS.items():
        target = ASSET_DIR / f"{nama}{Path(url).suffix.split('?')[0]}"
        if target.exists():
            print(f"  [ada   ] {target}")
            continue
        try:
            from liquipedia_scraper import USER_AGENT
            h = dict(HEADERS)
            if "liquipedia.net" in url:
                h["User-Agent"] = USER_AGENT
            resp = requests.get(url, headers=h, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            target.write_bytes(resp.content)
            print(f"  [unduh ] {target}  ({len(resp.content):,} byte)")
        except requests.RequestException as exc:
            print(f"  [gagal ] {nama}: {exc}")


if __name__ == "__main__":
    main()
