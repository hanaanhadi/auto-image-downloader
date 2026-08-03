#!/usr/bin/env python3
"""
Reusable background-music mixing step: loops/trims a music track to match
the narration's exact duration, then mixes it in at low volume with sidechain
ducking -- the music automatically dips whenever narration is present and
comes back up in the gaps, the standard documentary/podcast technique --
rather than just playing quietly underneath the whole time.

USAGE:
    python mix_music.py --narration FILE --music FILE --output FILE
        [--music-volume -22] [--duck-ratio 10] [--no-duck]

Requires ffmpeg/ffprobe on PATH.
"""

import argparse
import subprocess
from pathlib import Path


def get_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--narration", required=True)
    ap.add_argument("--music", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--music-volume", type=float, default=-22.0,
                     help="Music level in dB relative to its source (default -22, i.e. quiet).")
    ap.add_argument("--duck-ratio", type=float, default=10.0,
                     help="Sidechain compression ratio ducking music under narration (default 10). "
                          "Higher = music dips more when narration speaks.")
    ap.add_argument("--no-duck", action="store_true",
                     help="Mix music at a constant low level instead of ducking under narration.")
    args = ap.parse_args()

    narration = Path(args.narration).resolve()
    music = Path(args.music).resolve()
    out = Path(args.output).resolve()

    duration = get_duration(narration)
    print(f"Narration duration: {duration:.2f}s")
    print(f"Music level: {args.music_volume} dB" + (" (no ducking)" if args.no_duck else " (sidechain-ducked under narration)"))

    if args.no_duck:
        filt = (
            f"[1:a]volume={args.music_volume}dB[music_vol];"
            f"[0:a][music_vol]amix=inputs=2:duration=first:weights=1 1:normalize=0[out]"
        )
    else:
        filt = (
            f"[1:a]volume={args.music_volume}dB[music_vol];"
            f"[music_vol][0:a]sidechaincompress=threshold=0.05:ratio={args.duck_ratio}:"
            f"attack=20:release=400:makeup=1[music_ducked];"
            f"[0:a][music_ducked]amix=inputs=2:duration=first:weights=1 1:normalize=0[out]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(narration),
        "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", filt,
        "-map", "[out]",
        "-t", f"{duration:.3f}",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ]
    print(" ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)
    print("Done ->", out)


if __name__ == "__main__":
    main()
