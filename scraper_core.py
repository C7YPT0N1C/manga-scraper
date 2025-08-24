#!/usr/bin/env python3
import subprocess
import os

def run_nhentai(gallery_id, useragent, cookie, use_tor=False, output_dir="downloads"):
    """
    Run RicterZ/nhentai with the given params.
    """
    # Ensure output folder exists
    os.makedirs(output_dir, exist_ok=True)

    cmd = ["nhentai", str(gallery_id),
           "--useragent", useragent,
           "--cookie", cookie,
           "--output", output_dir]

    if use_tor:
        cmd = ["torsocks"] + cmd  # funnel through Tor

    print(f"[DEBUG] Running command: {' '.join(cmd)}")

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        print(f"[ERROR] Failed to fetch {gallery_id}: {result.stderr.strip()}")
        return False
    else:
        print(f"[+] Successfully downloaded gallery {gallery_id}")
        return True