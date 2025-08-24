#!/usr/bin/env python3
import argparse
from scraper_core import run_nhentai

def main():
    parser = argparse.ArgumentParser(description="nhentai scraper wrapper for RicterZ/nhentai")
    parser.add_argument("--start", type=int, required=True, help="Start gallery ID")
    parser.add_argument("--end", type=int, help="End gallery ID (inclusive)")
    parser.add_argument("--useragent", required=True, help="User agent string")
    parser.add_argument("--cookie", required=True, help="Cookie string from nhentai.net")
    parser.add_argument("--output", default="downloads", help="Output directory")
    parser.add_argument("--use-tor", action="store_true", help="Route requests via Tor (torsocks)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    end = args.end if args.end else args.start
    for gallery_id in range(args.start, end + 1):
        if args.verbose:
            print(f"[*] Starting {gallery_id}...")
        run_nhentai(
            gallery_id=gallery_id,
            useragent=args.useragent,
            cookie=args.cookie,
            use_tor=args.use_tor,
            output_dir=args.output
        )

if __name__ == "__main__":
    main()