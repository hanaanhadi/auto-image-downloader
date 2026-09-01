#!/usr/bin/env python3
"""
Topic-based copyright-free media downloader.
==============================================
Give it a topic (e.g. "Charles Jackson French") and it searches free,
usage-rights-cleared sources and downloads matching images/videos into
downloads/<topic>/ -- ranked by each source's own relevance search, not
scraped off random webpages, so everything that comes back is both on-topic
and legally clear to use.

SOURCES (each auto-skipped if not configured -- see below):
  - Wikimedia Commons   images + video, public domain / CC   -- no key needed
  - Openverse           images, CC-licensed                  -- no key needed
  - Internet Archive     video (+ some images), public domain -- no key needed
  - Pexels              images + video, free-to-use license  -- needs a free API key
  - Pixabay             images + video, free-to-use license  -- needs a free API key
  - NARA (National Archives)  images + video, US Gov public domain -- needs a free API key

Internet Archive is the best source here for real historical (e.g. WWII
newsreel/training film) footage that Pexels/Pixabay don't carry -- their
libraries are modern stock content, not archival.

FREE API KEYS (optional, unlocks Pexels/Pixabay/NARA -- each takes under a minute):
    Pexels:  https://www.pexels.com/api/        -> PEXELS_API_KEY
    Pixabay: https://pixabay.com/api/docs/      -> PIXABAY_API_KEY
    NARA:    https://data.nara.gov/             -> NARA_API_KEY (catalog.archives.gov API v2)
    Put them in the .env file in this folder:
        PEXELS_API_KEY=...
        PIXABAY_API_KEY=...
        NARA_API_KEY=...
    Wikimedia Commons, Openverse, and Internet Archive work immediately with
    no key at all. NARA's integration is unverified until tested against a
    real key -- it's written defensively (skips cleanly on any schema
    mismatch) but flag it if results look wrong.

USAGE:
    python search_and_download.py --topic "Charles Jackson French"
        [--images 25] [--videos 5] [--output downloads]
        [--sources wikimedia,openverse,archive_org,pexels,pixabay,nara]

Downloads into downloads/<topic>/images/ and downloads/<topic>/videos/, plus
a sources_report.txt crediting each file's source/author/license -- useful
for a documentary credits card even where attribution isn't legally required.

To feed the results straight into the episode pipeline, copy (or point)
episodes/<name>/images_pool/ at the downloaded images/ folder, then run
run_episode.py --resume as usual.

FIRST-TIME SETUP:
    pip install requests
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

# Windows consoles default to a codepage (e.g. cp1252) that can't encode
# every character that shows up in source titles/tags (accents, foreign
# scripts) -- without this, printing one crashes the whole run partway
# through a batch of downloads.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import requests
except ImportError:
    print("Missing dependency. Install with:\n    pip install requests")
    sys.exit(1)

try:
    import dns_override
    dns_override.install()
except ImportError:
    pass

try:
    # Some Windows setups route TLS validation through the OS trust store
    # (via pip's vendored `truststore`) instead of the standard, actively
    # maintained certifi CA bundle -- that can reject perfectly valid certs
    # on hosts whose chain isn't in the (possibly stale) OS store yet, even
    # though the cert itself is fine. Pin to certifi explicitly to sidestep
    # that rather than disabling verification.
    import certifi
    CA_BUNDLE = certifi.where()
except ImportError:
    CA_BUNDLE = True

ROOT = Path(__file__).parent
USER_AGENT = "TopicMediaDownloader/1.0 (personal documentary project)"
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT = 30
CHUNK_SIZE = 8192


def load_env():
    """Minimal .env loader -- avoids requiring python-dotenv as a dependency."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def get_with_retry(url, retries=10, backoff=1.5, **kwargs):
    """GET with retries -- commons.wikimedia.org in particular resets the
    connection on a large fraction of requests from some networks (flaky,
    not a hard block: retrying the same request shortly after tends to
    succeed), and upload.wikimedia.org occasionally 429s under load."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, **kwargs)
            if r.status_code == 429 and attempt < retries:
                time.sleep(backoff * attempt)
                continue
            return r
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise last_exc


def sanitize_filename(name, max_len=80):
    name = unquote(name or "")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(". ")
    return name[:max_len] or "untitled"


def build_filename(item, index):
    ext = Path(urlparse(item["url"]).path).suffix.split("?")[0]
    if not ext or len(ext) > 5:
        ext = ".jpg" if item["kind"] == "image" else ".mp4"
    # Pixabay/Wikimedia titles are sometimes long comma-separated tag lists
    # ("statue, new york, statue, sculpture, ...") -- keep just the first
    # tag/phrase so filenames stay short and readable.
    title = (item.get("title") or "").split(",")[0]
    stem = sanitize_filename(Path(title).stem, max_len=40)
    return f"{item['source']}_{index:03d}_{stem}{ext}"


def download_file(url, dest_dir, filename):
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    if dest.exists() and dest.stat().st_size > 0:
        print(f"    - already have {filename}")
        return dest
    try:
        with get_with_retry(url, headers=HEADERS, timeout=TIMEOUT, stream=True, verify=CA_BUNDLE) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
        print(f"    + downloaded {filename} ({dest.stat().st_size // 1024} KB)")
        return dest
    except Exception as e:
        print(f"    x failed {url}: {e}")
        if dest.exists():
            dest.unlink()
        return None


# ---------------------------------------------------------------- SOURCES
# Each search_* function returns a list of dicts:
#   {title, url (direct file), page_url (credit link), license, author, kind, source}

def search_wikimedia(topic, limit):
    results = []
    try:
        r = get_with_retry(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "srnamespace": 6,
                "srsearch": topic, "srlimit": min(limit, 50), "format": "json",
            },
            headers=HEADERS, timeout=TIMEOUT, verify=CA_BUNDLE,
        )
        r.raise_for_status()
        titles = [item["title"] for item in r.json().get("query", {}).get("search", [])]
    except Exception as e:
        print(f"  Wikimedia search failed: {e}")
        return results

    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        try:
            r = get_with_retry(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query", "titles": "|".join(batch),
                    "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                    "format": "json",
                },
                headers=HEADERS, timeout=TIMEOUT, verify=CA_BUNDLE,
            )
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})
            for page in pages.values():
                info = (page.get("imageinfo") or [None])[0]
                if not info or not info.get("url"):
                    continue
                mime = info.get("mime", "")
                if mime.startswith("image/"):
                    kind = "image"
                elif mime.startswith("video/"):
                    kind = "video"
                else:
                    continue
                meta = info.get("extmetadata", {})
                title = page.get("title", "").replace("File:", "")
                results.append({
                    "title": title,
                    "url": info["url"],
                    "page_url": info.get("descriptionurl")
                        or f"https://commons.wikimedia.org/wiki/File:{title.replace(' ', '_')}",
                    "license": meta.get("LicenseShortName", {}).get("value", "See page"),
                    "author": re.sub("<[^<]+?>", "", meta.get("Artist", {}).get("value", "Unknown"))[:80],
                    "kind": kind,
                    "source": "wikimedia",
                })
        except Exception as e:
            print(f"  Wikimedia imageinfo batch failed: {e}")
        time.sleep(0.3)
    return results


def search_archive_org(topic, limit):
    """Internet Archive: the best free source here for real historical
    footage (WWII newsreels, training films, etc). Two-step: search for
    items, then fetch each item's file list and pick a reasonably-sized
    derivative video (skipping multi-GB masters and tiny thumbnail clips)."""
    results = []
    try:
        r = get_with_retry(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f"({topic}) AND mediatype:(movies)",
                "fl[]": ["identifier", "title", "description"],
                "rows": min(limit, 25),
                "output": "json",
            },
            headers=HEADERS, timeout=TIMEOUT, verify=CA_BUNDLE,
        )
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
    except Exception as e:
        print(f"  Internet Archive search failed: {e}")
        return results

    for doc in docs:
        identifier = doc.get("identifier")
        if not identifier:
            continue
        try:
            meta = get_with_retry(
                f"https://archive.org/metadata/{identifier}",
                headers=HEADERS, timeout=TIMEOUT, verify=CA_BUNDLE,
            )
            meta.raise_for_status()
            files = meta.json().get("files", [])

            def fsize(f):
                try:
                    return int(f.get("size", 0))
                except (TypeError, ValueError):
                    return 0

            candidates = [
                f for f in files
                if f.get("name", "").lower().endswith((".mp4", ".ogv"))
                and 200_000 < fsize(f) < 500_000_000  # skip thumbnails and multi-GB masters
            ]
            if not candidates:
                continue
            # .ogv derivatives 500-error on archive.org's CDN noticeably more
            # often than .mp4 -- prefer mp4 when both exist, smallest first.
            chosen = min(candidates, key=lambda f: (not f["name"].lower().endswith(".mp4"), fsize(f)))
            title = doc.get("title") or identifier
            results.append({
                "title": title,
                "url": f"https://archive.org/download/{identifier}/{chosen['name']}",
                "page_url": f"https://archive.org/details/{identifier}",
                "license": "Public Domain (Internet Archive)",
                "author": (doc.get("description") or "Internet Archive")[:80],
                "kind": "video",
                "source": "archive_org",
            })
        except Exception as e:
            print(f"  Internet Archive metadata failed for {identifier}: {e}")
        time.sleep(0.2)
    return results


def search_nara(topic, limit, kind):
    """National Archives catalog API v2 -- the source for exact US Signal
    Corps / official WWII photos and film (matches the NAID-style records a
    production guide might reference). Needs a free key from data.nara.gov.
    UNVERIFIED against a live key/schema as of writing -- wrapped
    defensively so any field-name mismatch just yields 0 results rather
    than crashing; flag it if results look wrong once a key is in place."""
    key = os.environ.get("NARA_API_KEY")
    if not key:
        return []
    results = []
    try:
        r = requests.get(
            "https://catalog.archives.gov/api/v2/records/search",
            params={"q": topic, "limit": min(limit * 3, 60)},
            headers={**HEADERS, "x-api-key": key}, timeout=TIMEOUT, verify=CA_BUNDLE,
        )
        r.raise_for_status()
        hits = r.json().get("body", {}).get("hits", {}).get("hits", [])
    except Exception as e:
        print(f"  NARA search failed: {e}")
        return results

    for hit in hits:
        try:
            record = hit.get("_source", {}).get("record", {})
            digital_objects = record.get("digitalObjects") or []
            for obj in digital_objects:
                obj_url = obj.get("objectUrl")
                obj_type = (obj.get("objectType") or "").lower()
                if not obj_url:
                    continue
                if kind == "image" and "image" not in obj_type:
                    continue
                if kind == "video" and not any(t in obj_type for t in ("video", "mpeg", "motion")):
                    continue
                results.append({
                    "title": record.get("title") or f"nara_{record.get('naId', '')}",
                    "url": obj_url,
                    "page_url": f"https://catalog.archives.gov/id/{record.get('naId', '')}",
                    "license": "Public Domain (US Government / NARA)",
                    "author": "National Archives",
                    "kind": kind,
                    "source": "nara",
                })
                break  # one file per record is enough
        except Exception:
            continue
    return results


def search_openverse(topic, limit):
    results = []
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": topic, "page_size": min(limit, 20), "license_type": "commercial"},
            headers=HEADERS, timeout=TIMEOUT, verify=CA_BUNDLE,
        )
        r.raise_for_status()
        for item in r.json().get("results", []):
            if not item.get("url"):
                continue
            results.append({
                "title": item.get("title") or "untitled",
                "url": item["url"],
                "page_url": item.get("foreign_landing_url"),
                "license": f"{item.get('license', '').upper()} {item.get('license_version', '')}".strip(),
                "author": item.get("creator") or "Unknown",
                "kind": "image",
                "source": "openverse",
            })
    except Exception as e:
        print(f"  Openverse search failed: {e}")
    return results


def search_pexels(topic, limit, kind):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    results = []
    try:
        if kind == "image":
            r = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": topic, "per_page": min(max(limit, 1), 80)},
                headers={"Authorization": key}, timeout=TIMEOUT, verify=CA_BUNDLE,
            )
            r.raise_for_status()
            for item in r.json().get("photos", []):
                results.append({
                    "title": item.get("alt") or f"pexels_{item['id']}",
                    "url": item["src"]["original"],
                    "page_url": item["url"],
                    "license": "Pexels License (free to use)",
                    "author": item.get("photographer", "Unknown"),
                    "kind": "image",
                    "source": "pexels",
                })
        else:
            r = requests.get(
                "https://api.pexels.com/videos/search",
                params={"query": topic, "per_page": min(max(limit, 1), 80)},
                headers={"Authorization": key}, timeout=TIMEOUT, verify=CA_BUNDLE,
            )
            r.raise_for_status()
            for item in r.json().get("videos", []):
                files = sorted(item.get("video_files", []),
                                key=lambda f: f.get("width", 0), reverse=True)
                if not files:
                    continue
                results.append({
                    "title": f"pexels_video_{item['id']}",
                    "url": files[0]["link"],
                    "page_url": item.get("url"),
                    "license": "Pexels License (free to use)",
                    "author": item.get("user", {}).get("name", "Unknown"),
                    "kind": "video",
                    "source": "pexels",
                })
    except Exception as e:
        print(f"  Pexels {kind} search failed: {e}")
    return results


def search_pixabay(topic, limit, kind):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return []
    results = []
    try:
        if kind == "image":
            r = requests.get(
                "https://pixabay.com/api/",
                params={"key": key, "q": topic, "image_type": "photo",
                        "per_page": max(min(limit, 200), 3)},
                timeout=TIMEOUT, verify=CA_BUNDLE,
            )
            r.raise_for_status()
            for item in r.json().get("hits", []):
                if not item.get("largeImageURL"):
                    continue
                results.append({
                    "title": item.get("tags") or f"pixabay_{item['id']}",
                    "url": item["largeImageURL"],
                    "page_url": item.get("pageURL"),
                    "license": "Pixabay License (free to use)",
                    "author": item.get("user", "Unknown"),
                    "kind": "image",
                    "source": "pixabay",
                })
        else:
            r = requests.get(
                "https://pixabay.com/api/videos/",
                params={"key": key, "q": topic, "per_page": max(min(limit, 200), 3)},
                timeout=TIMEOUT, verify=CA_BUNDLE,
            )
            r.raise_for_status()
            for item in r.json().get("hits", []):
                videos = item.get("videos", {})
                best = videos.get("large") or videos.get("medium") or videos.get("small")
                if not best or not best.get("url"):
                    continue
                results.append({
                    "title": item.get("tags") or f"pixabay_video_{item['id']}",
                    "url": best["url"],
                    "page_url": item.get("pageURL"),
                    "license": "Pixabay License (free to use)",
                    "author": item.get("user", "Unknown"),
                    "kind": "video",
                    "source": "pixabay",
                })
    except Exception as e:
        print(f"  Pixabay {kind} search failed: {e}")
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic", required=True, help="What to search for, e.g. \"Charles Jackson French\"")
    ap.add_argument("--images", type=int, default=25, help="Max images to download (default 25)")
    ap.add_argument("--videos", type=int, default=5, help="Max video clips to download (default 5)")
    ap.add_argument("--output", default="downloads", help="Root output directory (default: downloads)")
    ap.add_argument("--sources", default="wikimedia,openverse,archive_org,pexels,pixabay,nara",
                     help="Comma-separated sources to use (default: all)")
    args = ap.parse_args()

    load_env()
    sources = {s.strip().lower() for s in args.sources.split(",") if s.strip()}

    topic_slug = sanitize_filename(args.topic, max_len=60)
    topic_root = Path(args.output) / topic_slug
    images_dir = topic_root / "images"
    videos_dir = topic_root / "videos"

    print(f"{'=' * 60}\nTopic: {args.topic}\nOutput: {topic_root.resolve()}\nSources: {', '.join(sorted(sources)) or '(none)'}\n{'=' * 60}")

    candidate_images, candidate_videos = [], []

    if "wikimedia" in sources:
        print("\n[Wikimedia Commons] searching...")
        wiki = search_wikimedia(args.topic, max(args.images, args.videos) * 3)
        candidate_images += [r for r in wiki if r["kind"] == "image"]
        candidate_videos += [r for r in wiki if r["kind"] == "video"]
        print(f"  found {len(wiki)} usable results")

    if "openverse" in sources:
        print("\n[Openverse] searching...")
        ov = search_openverse(args.topic, args.images * 2)
        candidate_images += ov
        print(f"  found {len(ov)} usable results")

    if "archive_org" in sources:
        print("\n[Internet Archive] searching...")
        ia = search_archive_org(args.topic, args.videos * 4)
        candidate_videos += ia
        print(f"  found {len(ia)} usable results")

    if "pexels" in sources:
        if os.environ.get("PEXELS_API_KEY"):
            print("\n[Pexels] searching...")
            px_img = search_pexels(args.topic, args.images, "image")
            px_vid = search_pexels(args.topic, args.videos, "video")
            candidate_images += px_img
            candidate_videos += px_vid
            print(f"  found {len(px_img)} images, {len(px_vid)} videos")
        else:
            print("\n[Pexels] skipped -- no PEXELS_API_KEY in .env")

    if "pixabay" in sources:
        if os.environ.get("PIXABAY_API_KEY"):
            print("\n[Pixabay] searching...")
            pb_img = search_pixabay(args.topic, args.images, "image")
            pb_vid = search_pixabay(args.topic, args.videos, "video")
            candidate_images += pb_img
            candidate_videos += pb_vid
            print(f"  found {len(pb_img)} images, {len(pb_vid)} videos")
        else:
            print("\n[Pixabay] skipped -- no PIXABAY_API_KEY in .env")

    if "nara" in sources:
        if os.environ.get("NARA_API_KEY"):
            print("\n[NARA] searching...")
            nara_img = search_nara(args.topic, args.images, "image")
            nara_vid = search_nara(args.topic, args.videos, "video")
            candidate_images += nara_img
            candidate_videos += nara_vid
            print(f"  found {len(nara_img)} images, {len(nara_vid)} videos")
        else:
            print("\n[NARA] skipped -- no NARA_API_KEY in .env")

    to_download_images = candidate_images[:args.images]
    to_download_videos = candidate_videos[:args.videos]

    print(f"\n{'=' * 60}\nDownloading {len(to_download_images)} images, "
          f"{len(to_download_videos)} videos\n{'=' * 60}")

    report = []
    print("\nImages:")
    for i, item in enumerate(to_download_images, 1):
        filename = build_filename(item, i)
        print(f"  [{i}/{len(to_download_images)}] {item['source']}: {item['title'][:60]}")
        path = download_file(item["url"], images_dir, filename)
        report.append((item, path))

    print("\nVideos:")
    for i, item in enumerate(to_download_videos, 1):
        filename = build_filename(item, i)
        print(f"  [{i}/{len(to_download_videos)}] {item['source']}: {item['title'][:60]}")
        path = download_file(item["url"], videos_dir, filename)
        report.append((item, path))

    report_path = topic_root / "sources_report.txt"
    topic_root.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Sources report -- topic: {args.topic}\n{'=' * 60}\n\n")
        for item, path in report:
            f.write(f"File:    {path.name if path else '(failed)'}\n")
            f.write(f"Source:  {item['source']}\n")
            f.write(f"Title:   {item['title']}\n")
            f.write(f"Author:  {item['author']}\n")
            f.write(f"License: {item['license']}\n")
            f.write(f"Page:    {item.get('page_url', '')}\n\n")

    downloaded = sum(1 for _, p in report if p)
    print(f"\n{'=' * 60}\nDone. {downloaded}/{len(report)} files saved under {topic_root.resolve()}")
    print(f"Report: {report_path}\n{'=' * 60}")


if __name__ == "__main__":
    main()
