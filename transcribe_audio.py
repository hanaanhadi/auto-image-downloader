#!/usr/bin/env python3
"""
Reusable transcription step: runs faster-whisper on an audio file and writes
both transcript.json (structured, with timestamps) and transcript.srt
(subtitle format) into an output directory.

USAGE:
    python transcribe_audio.py --audio FILE --out-dir DIR [--model small]
"""

import argparse
import json
from pathlib import Path

from faster_whisper import WhisperModel


def to_srt_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--model", default="small", help="faster-whisper model size (default: small)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading faster-whisper model '{args.model}'...")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    print(f"Transcribing {args.audio} ...")
    segments, info = model.transcribe(args.audio, beam_size=5, word_timestamps=False)
    print(f"Detected language: {info.language} (probability {info.language_probability:.2f})")
    print(f"Duration: {info.duration:.1f}s")

    results = []
    for seg in segments:
        entry = {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()}
        results.append(entry)
        print(f"[{entry['start']:.2f} - {entry['end']:.2f}] {entry['text']}")

    json_path = out_dir / "transcript.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    srt_lines = []
    for i, seg in enumerate(results, 1):
        srt_lines.append(str(i))
        srt_lines.append(f"{to_srt_timestamp(seg['start'])} --> {to_srt_timestamp(seg['end'])}")
        srt_lines.append(seg["text"])
        srt_lines.append("")
    srt_path = out_dir / "transcript.srt"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

    print(f"\nSaved {len(results)} segments -> {json_path.name}, {srt_path.name}")


if __name__ == "__main__":
    main()
