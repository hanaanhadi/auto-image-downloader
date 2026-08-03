#!/usr/bin/env python3
"""
Parallel image/scrape downloader — reuses download_assets.py's logic but
processes the remaining scrape/image URLs concurrently instead of one at a
time. Meant to run alongside (or after) the sequential run for the video
items, to speed up the image-heavy back half of an asset list.

USAGE:
    python download_assets_parallel.py <asset_list.txt> [--topic TOPIC] [--output DIR] [--min-size KB] [--workers N] [--skip N]
"""

import argparse
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import dns_override
dns_override.install()

import download_assets as base


def main():
    parser = argparse.ArgumentParser(
        description="Concurrently process remaining asset URLs (images/scrape pages).")
    parser.add_argument("asset_file")
    parser.add_argument("--topic", default=None)
    parser.add_argument("--output", default="./downloads")
    parser.add_argument("--min-size", type=int, default=20)
    parser.add_argument("--workers", type=int, default=6,
                        help="Number of URLs to process concurrently (default 6).")
    parser.add_argument("--skip", type=int, default=0,
                        help="Skip the first N URLs in the list (e.g. ones already "
                             "handled by a sequential run).")
    args = parser.parse_args()

    asset_file = Path(args.asset_file)
    if not asset_file.exists():
        print(f"Asset file not found: {asset_file}")
        sys.exit(1)

    topic = args.topic or asset_file.stem
    topic_root = Path(args.output) / topic
    topic_root.mkdir(parents=True, exist_ok=True)

    urls = base.load_asset_list(asset_file)
    remaining = urls[args.skip:]

    print(f"\n{'=' * 60}")
    print(f"Topic:      {topic}")
    print(f"Output:     {topic_root.resolve()}")
    print(f"Total URLs: {len(urls)}  |  Processing (parallel): {len(remaining)}  |  Workers: {args.workers}")
    print(f"{'=' * 60}")

    report = [None] * len(remaining)

    def worker(idx_url):
        idx, url = idx_url
        try:
            result = base.process_url(url, topic_root, min_kb=args.min_size)
        except Exception as e:
            result = {"url": url, "result": f"error: {e}", "path": None}
        return idx, result

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(worker, (i, u)) for i, u in enumerate(remaining)]
        done = 0
        for fut in as_completed(futures):
            idx, result = fut.result()
            report[idx] = result
            done += 1
            print(f"\n[{done}/{len(remaining)} complete] {result['url']} -> {result['result']}")

    report_path = topic_root / "download_report_parallel.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Parallel Download Report — topic: {topic}\n{'=' * 60}\n\n")
        for i, r in enumerate(report, 1):
            f.write(f"[{i}] {r['url']}\n")
            f.write(f"    Result: {r['result']}\n")
            if r.get("path"):
                f.write(f"    Path:   {r['path']}\n")
            f.write("\n")

    print(f"\n{'=' * 60}")
    print(f"Done. Files saved under: {topic_root.resolve()}")
    print(f"Report: {report_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
