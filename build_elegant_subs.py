#!/usr/bin/env python3
"""
Documentary-style burned-in subtitles: converts a short-chunk SRT (e.g.
captions.srt from transcribe_audio.py) into a styled ASS file tuned for
long-form history documentaries -- minimal, elegant, readable, never shouty.

Style: clean sans-serif, moderate size, bottom-center, thin dark outline +
soft shadow, generous bottom margin so it never collides with the image.
No karaoke bouncing, no highlight colors -- the archival imagery stays the
star. For TikTok-style word-by-word reveal, see build_word_captions.py.

USAGE:
    python build_elegant_subs.py --srt captions.srt --output elegant.ass
        [--fontsize 54] [--margin-v 70] [--font "DejaVu Sans"]
"""

import argparse
import re
from pathlib import Path


def parse_srt(path):
    text = Path(path).read_text(encoding="utf-8")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*"
                     r"(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        g = list(map(int, m.groups()))
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        cues.append((s, e, " ".join(lines[2:])))
    return cues


def ass_time(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Doc,{font},{fs},&H00FFFFFF,&H000019FF,&H80000000,&H80000000,0,0,0,0,100,100,0.5,0,1,2.2,0.6,2,40,40,{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--srt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--fontsize", type=int, default=54)
    ap.add_argument("--margin-v", type=int, default=70,
                    help="Distance from bottom edge in px at 1080p (default 70).")
    ap.add_argument("--font", default="DejaVu Sans")
    args = ap.parse_args()

    cues = parse_srt(args.srt)
    if not cues:
        raise SystemExit(f"No cues parsed from {args.srt}")

    out = [HEADER.format(font=args.font, fs=args.fontsize, mv=args.margin_v)]
    for s, e, t in cues:
        # gentle 120ms fade in/out so captions never pop harshly
        text = t.replace("\n", "\\N")
        out.append(
            f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Doc,,0,0,0,,"
            f"{{\\fad(120,120)}}{text}"
        )
    Path(args.output).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {len(cues)} styled cues -> {args.output}")


if __name__ == "__main__":
    main()
