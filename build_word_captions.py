#!/usr/bin/env python3
"""
Reusable caption-style step: turns Whisper's word-level timestamps
(words.json, from transcribe_audio.py) into an SRT where each cue is a
SINGLE word -- the "TikTok-style" word-by-word reveal, instead of the
multi-word phrase chunks in captions.srt.

Each word's cue runs from its own start to the next word's start (so there's
no dead gap/flicker between words), except across a real pause in speech --
there the cue just holds for the word's own (start,end) plus a small tail,
so a caption doesn't linger stretched across silence.

USAGE:
    python build_word_captions.py --words words.json --output captions_words.srt
        [--max-gap 0.6] [--tail-pad 0.15]
"""

import argparse
import json
from pathlib import Path


def to_srt_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build(words, max_gap=0.6, tail_pad=0.15):
    cues = []
    for i, w in enumerate(words):
        text = w["word"].strip()
        if not text:
            continue
        start = w["start"]
        natural_end = w["end"] + tail_pad
        if i + 1 < len(words):
            next_start = words[i + 1]["start"]
            gap = next_start - w["end"]
            end = next_start if gap <= max_gap else min(natural_end, next_start)
        else:
            end = natural_end
        end = max(end, start + 0.08)  # floor so a rushed word still flashes visibly
        cues.append({"start": start, "end": end, "text": text})
    return cues


def write_srt(cues, path):
    lines = []
    for i, c in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{to_srt_timestamp(c['start'])} --> {to_srt_timestamp(c['end'])}")
        lines.append(c["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--words", required=True, help="words.json from transcribe_audio.py")
    ap.add_argument("--output", required=True)
    ap.add_argument("--max-gap", type=float, default=0.6,
                     help="Extend a word's cue to the next word's start only if the silence "
                          "between them is under this many seconds (default 0.6).")
    ap.add_argument("--tail-pad", type=float, default=0.15,
                     help="Extra seconds held after a word's own end when it's followed by a "
                          "real pause (default 0.15).")
    args = ap.parse_args()

    words = json.loads(Path(args.words).read_text(encoding="utf-8"))
    cues = build(words, max_gap=args.max_gap, tail_pad=args.tail_pad)
    out_path = Path(args.output)
    write_srt(cues, out_path)
    print(f"Wrote {len(cues)} word-cues -> {out_path}")


if __name__ == "__main__":
    main()
