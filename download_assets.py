#!/usr/bin/env python3
"""
YouTube Video Asset Downloader
==============================
A generalized bulk-downloader for archival / copyright-free assets. Reads a
plain-text list of URLs, auto-detects whether each URL is a direct media file
or a webpage, and:
  - Downloads direct media (mp4, jpg, png, pdf, etc.) into ./downloads/<topic>/media/
  - Scrapes all images off HTML pages into ./downloads/<topic>/scraped/<pagename>/
  - Auto-derives direct file URLs for known archives (archive.org, Wikimedia Commons)
  - Skips already-downloaded files (safe to re-run)
  - Retries on failure, follows redirects, sanitizes filenames
  - Writes a plain-text report of everything downloaded

USAGE:
    python download_assets.py <asset_list.txt> [--topic TOPIC] [--output DIR] [--min-size KB]

EXAMPLE:
    python download_assets.py port_chicago_50_assets.txt

    Uses the filename as the topic name, downloads into ./downloads/port_chicago_50_assets/

    python download_assets.py my_next_video.txt --topic charles_french

    Downloads into ./downloads/charles_french/

ASSET LIST FORMAT:
    - One URL per line (http:// or https://)
    - Lines starting with # are comments (ignored)
    - Blank lines are ignored
    - Labels are supported: "Photo: https://example.com/file.jpg" — script uses the URL

FIRST-TIME SETUP:
    pip install requests beautifulsoup4 tqdm
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Install them with:")
    print("    pip install requests beautifulsoup4 tqdm")
    sys.exit(1)

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ------------------------------------------------------------------ CONFIG

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT = 60
RETRIES = 3
CHUNK_SIZE = 8192

MEDIA_EXTS = {
    ".mp4", ".mov", ".webm", ".mkv", ".avi",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".tif", ".tiff",
    ".pdf", ".mp3", ".wav", ".ogg",
}

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".ogv"}

MEDIA_CONTENT_TYPES = ("video/", "image/", "application/pdf",
                       "application/octet-stream", "audio/")

ARCHIVE_ORG_PATTERN = re.compile(r"https?://archive\.org/details/([^/?#]+)")
WIKIMEDIA_FILE_PATTERN = re.compile(
    r"https?://commons\.wikimedia\.org/wiki/File:([^?#]+)")


# ------------------------------------------------------------------ HELPERS

def sanitize_filename(name: str, max_len: int = 200) -> str:
    """Make a string safe as a filename on any OS."""
    name = unquote(name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(". ")
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot:
            name = stem[:max_len - len(ext) - 1] + dot + ext
        else:
            name = name[:max_len]
    return name or "unnamed"


def guess_extension_from_content_type(ct: str) -> str:
    mapping = {
        "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
        "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
        "image/webp": ".webp", "image/svg+xml": ".svg", "image/tiff": ".tif",
        "application/pdf": ".pdf",
    }
    return mapping.get(ct.split(";")[0].strip().lower(), "")


def looks_like_media(url: str) -> bool:
    """Fast check: does the URL end in a known media extension?"""
    return any(urlparse(url).path.lower().endswith(ext) for ext in MEDIA_EXTS)


def probe_url(url: str):
    """HEAD-request the URL. Returns (is_media, content_type)."""
    try:
        r = requests.head(url, headers=HEADERS, timeout=TIMEOUT,
                          allow_redirects=True)
        ct = r.headers.get("Content-Type", "").lower()
        is_media = any(ct.startswith(p) for p in MEDIA_CONTENT_TYPES)
        return (is_media, ct)
    except Exception:
        return (False, "")


def rewrite_special_urls(url: str):
    """
    Archives often publish DETAILS pages but the file follows a predictable
    pattern. Return candidate direct URLs to try in order.
    """
    m = ARCHIVE_ORG_PATTERN.match(url)
    if m:
        item = m.group(1)
        return [
            f"https://archive.org/download/{item}/{item}.mp4",
            f"https://archive.org/download/{item}/{item}_512kb.mp4",
            f"https://archive.org/download/{item}/{item}.m4v",
            f"https://archive.org/download/{item}/{item}.ogv",
        ]

    m = WIKIMEDIA_FILE_PATTERN.match(url)
    if m:
        filename = m.group(1)
        # Special:FilePath is a stable redirect to the current file location
        return [f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}"]

    return []


def classify_url(url: str) -> str:
    """
    Cheap, purely local guess at 'video' vs 'image' (no network requests --
    used only to order downloads image-first, so we don't add extra probe
    traffic to the very sites we're trying to go easy on). Anything not
    confidently video (HTML pages to scrape, unknown extensions, etc.)
    defaults to 'image', since that's the larger, more site-hammering
    category we want processed steadily rather than guessed wrong as video.
    """
    if ARCHIVE_ORG_PATTERN.match(url):
        return "video"
    if WIKIMEDIA_FILE_PATTERN.match(url):
        return "image"
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in VIDEO_EXTS):
        return "video"
    return "image"


# ------------------------------------------------------------------ CORE

def download_file(url: str, dest_dir: Path, filename: str = None):
    """Download a file with retries + progress. Skip if already exists."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path) or "file"
    filename = sanitize_filename(filename)

    dest = dest_dir / filename
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ✓ Already exists (skipped): {filename}")
        return dest

    for attempt in range(1, RETRIES + 1):
        try:
            with requests.get(url, headers=HEADERS, timeout=TIMEOUT,
                              stream=True) as r:
                r.raise_for_status()

                # If filename has no extension, try to add one from Content-Type
                if "." not in filename:
                    ext = guess_extension_from_content_type(
                        r.headers.get("Content-Type", ""))
                    if ext:
                        dest = dest_dir / (filename + ext)

                total = int(r.headers.get("Content-Length", 0)) or None
                progress = None
                if HAS_TQDM and total:
                    progress = tqdm(
                        total=total, unit="B", unit_scale=True,
                        unit_divisor=1024,
                        desc=f"  ↓ {dest.name[:40]}", leave=False)

                with open(dest, "wb") as f:
                    for chunk in r.iter_content(CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                            if progress:
                                progress.update(len(chunk))
                if progress:
                    progress.close()

            print(f"  ✓ Downloaded: {dest.name} ({dest.stat().st_size // 1024} KB)")
            return dest
        except Exception as e:
            print(f"  ✗ Attempt {attempt}/{RETRIES} failed: {e}")
            time.sleep(2 * attempt)

    print(f"  ✗ GAVE UP: {url}")
    return None


def scrape_images_from_page(url: str, dest_dir: Path, min_kb: int = 20):
    """Fetch a page, find all <img> tags, download the substantial ones."""
    print(f"  ⚙ Scraping page for images: {url}")
    downloaded = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print(f"  ✗ Could not fetch page: {e}")
        return downloaded

    soup = BeautifulSoup(r.text, "html.parser")
    img_urls = set()
    for tag in soup.find_all("img"):
        src = tag.get("src") or tag.get("data-src") or tag.get("data-lazy-src")
        if not src or src.startswith("data:"):
            continue
        full = urljoin(url, src)
        # Strip thumbnail-size query params (?maxwidth=650, etc.)
        full = re.sub(r"[?&](maxwidth|width|w|thumb|resize)=[^&]*", "", full)
        full = full.rstrip("?&")
        img_urls.add(full)

    print(f"  Found {len(img_urls)} image references.")

    for i, img_url in enumerate(sorted(img_urls), 1):
        try:
            head = requests.head(img_url, headers=HEADERS, timeout=TIMEOUT,
                                 allow_redirects=True)
            size_kb = int(head.headers.get("Content-Length", 0)) / 1024
            ct = head.headers.get("Content-Type", "").lower()
            if not ct.startswith("image/"):
                continue
            if size_kb and size_kb < min_kb:
                continue

            filename = os.path.basename(urlparse(img_url).path) or f"img_{i}"
            path = download_file(img_url, dest_dir, filename)
            if path:
                downloaded.append(path)
        except Exception as e:
            print(f"  ✗ Skipping {img_url}: {e}")

    return downloaded


def process_url(url: str, topic_root: Path, min_kb: int) -> dict:
    print(f"\n➤ {url}")

    # 1) Try rewrites for known archives first
    for candidate in rewrite_special_urls(url):
        is_media, _ = probe_url(candidate)
        if is_media:
            print(f"  → Rewrote to direct file: {candidate}")
            path = download_file(candidate, topic_root / "media")
            return {"url": url, "result": "downloaded", "path": path}

    # 2) Direct media by extension
    if looks_like_media(url):
        path = download_file(url, topic_root / "media")
        return {"url": url, "result": "downloaded", "path": path}

    # 3) Direct media by content-type probe
    is_media, ct = probe_url(url)
    if is_media:
        path = download_file(url, topic_root / "media")
        return {"url": url, "result": "downloaded", "path": path}

    # 4) HTML page → scrape images
    if "text/html" in ct or ct == "":
        slug_source = urlparse(url).netloc + urlparse(url).path.replace("/", "_")
        page_slug = sanitize_filename(slug_source)[:80] or "page"
        scrape_dir = topic_root / "scraped" / page_slug
        images = scrape_images_from_page(url, scrape_dir, min_kb=min_kb)
        return {
            "url": url,
            "result": f"scraped_{len(images)}_images",
            "path": scrape_dir,
        }

    return {"url": url, "result": "skipped_unknown_type", "path": None}


def load_asset_list(path: Path):
    """Read URLs from a text file. Ignore #comments and blank lines."""
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("http://", "https://")):
                urls.append(line)
            else:
                # Support "Label: https://..." format
                match = re.search(r"(https?://\S+)", line)
                if match:
                    urls.append(match.group(1))
    return urls


# ------------------------------------------------------------------ MAIN

def main():
    parser = argparse.ArgumentParser(
        description="Bulk-download archival assets for a video topic.")
    parser.add_argument("asset_file",
                        help="Text file with one URL per line (# = comment).")
    parser.add_argument("--topic", default=None,
                        help="Folder name for this topic "
                             "(default: asset filename stem).")
    parser.add_argument("--output", default="./downloads",
                        help="Root output directory (default: ./downloads).")
    parser.add_argument("--min-size", type=int, default=20,
                        help="Minimum image size in KB when scraping pages "
                             "(default: 20). Increase to skip icons/logos.")
    args = parser.parse_args()

    asset_file = Path(args.asset_file)
    if not asset_file.exists():
        print(f"Asset file not found: {asset_file}")
        sys.exit(1)

    topic = args.topic or asset_file.stem
    topic_root = Path(args.output) / topic
    topic_root.mkdir(parents=True, exist_ok=True)

    urls = load_asset_list(asset_file)

    # Images/scrape-pages first, then videos -- steadier, more predictable
    # load on source sites than an interleaved mix. Classification is local
    # (extension/pattern only, no network requests) so this adds no extra
    # traffic of its own.
    image_urls = [u for u in urls if classify_url(u) == "image"]
    video_urls = [u for u in urls if classify_url(u) == "video"]
    urls = image_urls + video_urls

    print(f"\n{'=' * 60}")
    print(f"Topic:      {topic}")
    print(f"Output:     {topic_root.resolve()}")
    print(f"URLs found: {len(urls)}  ({len(image_urls)} image/page, {len(video_urls)} video -- "
          f"images processed first)")
    print(f"{'=' * 60}")

    report = []
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}]", end=" ")
        try:
            report.append(process_url(url, topic_root, min_kb=args.min_size))
        except KeyboardInterrupt:
            print("\n\nInterrupted by user. Progress saved.")
            break
        except Exception as e:
            print(f"  ✗ Unhandled error: {e}")
            report.append({"url": url, "result": f"error: {e}", "path": None})

    # Write a report
    report_path = topic_root / "download_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Download Report — topic: {topic}\n{'=' * 60}\n\n")
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
