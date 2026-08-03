#!/usr/bin/env python3
"""
Reusable, fully-automatic image manifest builder for make_slideshow.py.
Cycles through whatever images exist in a directory (round-robin, in
filename order) to fill the audio's duration at a target average slot
length -- no content/context awareness (that requires reading the script,
which is a manual/assisted step, not this script's job).

USAGE:
    python auto_manifest.py --images-dir DIR --audio FILE --output manifest.txt
        [--target-slot-seconds 27]
"""

import argparse
import subprocess
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def get_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--images-dir", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--target-slot-seconds", type=float, default=27.0)
    args = ap.parse_args()

    images = sorted(
        p for p in Path(args.images_dir).iterdir()
        if p.suffix.lower() in IMAGE_EXTS
    )
    if not images:
        raise SystemExit(f"No images found in {args.images_dir}")

    audio_dur = get_duration(args.audio)
    n_slots = max(round(audio_dur / args.target_slot_seconds), 1)

    manifest = [images[i % len(images)] for i in range(n_slots)]

    print(f"{len(images)} unique image(s), audio {audio_dur:.1f}s -> "
          f"{n_slots} slots (avg {audio_dur/n_slots:.1f}s each)")

    with open(args.output, "w", encoding="utf-8") as f:
        for img in manifest:
            f.write(str(img.resolve()) + "\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
