#!/usr/bin/env python3
"""
Reusable audio cleanup step: reduces background noise (ffmpeg's FFT
denoiser) and then loudness-matches the result back to the ORIGINAL file's
measured integrated loudness, so the output sounds cleaner without sounding
quieter or louder than the source.

USAGE:
    python denoise_audio.py --input FILE --output FILE [--noise-reduction 12]
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

    print("Matching loudness back to the original level ...")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(tmp_denoised),
        "-af", f"loudnorm=I={orig_i}:TP={orig_tp}:LRA={orig_lra}:print_format=summary",
        "-ar", "44100", "-c:a", "aac", "-b:a", "192k", str(out),
    ], check=True)

    tmp_denoised.unlink(missing_ok=True)

    print(f"Verifying output loudness ...")
    final_i, final_tp, _, _ = measure_loudness(out)
    print(f"Output: integrated={final_i:.1f} LUFS (target was {orig_i:.1f}), true_peak={final_tp:.1f} dBTP")
    print(f"Done -> {out}")


if __name__ == "__main__":
    main()
