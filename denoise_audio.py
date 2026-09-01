#!/usr/bin/env python3
"""
Reusable audio cleanup step: reduces background noise (ffmpeg's FFT
denoiser), then loudness-normalizes the result to the original level PLUS a
boost (default +6 LUFS), capped at a loud-but-clean ceiling -- quiet source
narration comes out clearly audible instead of just as quiet as the original,
while a true-peak ceiling keeps it from clipping or sounding over-compressed.

USAGE:
    python denoise_audio.py --input FILE --output FILE [--noise-reduction 12]
        [--volume-boost 6] [--max-loudness -14]
"""

import argparse
import json
import re
import subprocess
from pathlib import Path


def measure_loudness(path):
    """First-pass loudnorm analysis: returns (integrated_lufs, true_peak, lra, threshold)."""
    cmd = [
        "ffmpeg", "-i", str(path), "-af", "loudnorm=print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr, re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not parse loudnorm measurement for {path}:\n{result.stderr[-1000:]}")
    data = json.loads(match.group(0))
    return float(data["input_i"]), float(data["input_tp"]), float(data["input_lra"]), float(data["input_thresh"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--noise-reduction", type=float, default=12.0,
                     help="afftdn noise-reduction amount in dB, 0-97 (default 12, moderate).")
    ap.add_argument("--volume-boost", type=float, default=6.0,
                     help="Loudness added on top of the original level, in LUFS (default 6). "
                          "Set to 0 to just match the original level with no boost.")
    ap.add_argument("--max-loudness", type=float, default=-14.0,
                     help="Loudness ceiling in LUFS regardless of boost (default -14, loud but "
                          "clean for spoken narration). Prevents over-compression on sources "
                          "that were already loud to begin with.")
    args = ap.parse_args()

    src = Path(args.input)
    out = Path(args.output)
    tmp_denoised = out.parent / f"_tmp_denoised_{out.stem}.wav"

    print(f"Measuring original loudness of {src.name} ...")
    orig_i, orig_tp, orig_lra, _ = measure_loudness(src)
    print(f"Original: integrated={orig_i:.1f} LUFS, true_peak={orig_tp:.1f} dBTP, LRA={orig_lra:.1f}")

    print(f"Denoising (afftdn, nr={args.noise_reduction}) ...")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(src),
        "-af", f"afftdn=nr={args.noise_reduction}:nf=-25",
        "-ar", "44100", str(tmp_denoised),
    ], check=True)

    target_i = min(orig_i + args.volume_boost, args.max_loudness)
    target_tp = min(orig_tp, -1.5)  # clipping-safe ceiling regardless of source peak
    print(f"Boosting loudness to {target_i:.1f} LUFS "
          f"(original {orig_i:.1f} + {args.volume_boost:.1f} dB, capped at {args.max_loudness:.1f}) ...")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(tmp_denoised),
        "-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={orig_lra}:print_format=summary",
        "-ar", "44100", "-c:a", "aac", "-b:a", "192k", str(out),
    ], check=True)

    tmp_denoised.unlink(missing_ok=True)

    print(f"Verifying output loudness ...")
    final_i, final_tp, _, _ = measure_loudness(out)
    print(f"Output: integrated={final_i:.1f} LUFS (target was {target_i:.1f}), true_peak={final_tp:.1f} dBTP")
    print(f"Done -> {out}")


if __name__ == "__main__":
    main()
