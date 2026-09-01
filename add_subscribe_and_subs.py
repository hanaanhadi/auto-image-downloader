#!/usr/bin/env python3
"""
Reusable post-processing step: burns in SRT captions and adds an animated
SUBSCRIBE pop-up (slides in from the top-right corner, holds, slides out) at
one or more timestamps. Works on any finished video, independent of how it
was assembled (hand-built EDL, make_slideshow.py output, etc).

USAGE:
    python add_subscribe_and_subs.py --video IN.mp4 --output OUT.mp4 --srt captions.srt
        [--caption-fontsize 20] [--subscribe-duration 10] [--subscribe-times 180,560,1020,1480]
        [--subscribe-count 4]  (used instead of --subscribe-times to auto-space N pop-ups)

Requires ffmpeg/ffprobe on PATH.
"""

import argparse
import os
import subprocess
import tempfile
import shutil
from pathlib import Path

SLIDE_IN = 0.35
SLIDE_OUT = 0.45


def get_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def get_resolution(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    # Some ffmpeg builds emit a trailing comma on csv=p=0 output (e.g.
    # "1920,1080,") -- filter out the resulting empty field instead of
    # assuming exactly two comma-separated values.
    w, h = [v for v in out.stdout.strip().split(",") if v][:2]
    return int(w), int(h)


def build_button_png(width, height, fontsize, out_path):
    """Transparent red-pill SUBSCRIBE button, sized for the given canvas."""
    border = max(int(round(fontsize * 0.65)), 8)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d=1",
        "-vf",
        "format=rgba,colorkey=0x000000:0.01:0.0,"
        f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='SUBSCRIBE':"
        f"fontsize={fontsize}:fontcolor=white:box=1:boxcolor=red@0.92:"
        f"boxborderw={border}:x=(w-text_w)/2:y=(h-text_h)/2",
        "-frames:v", "1", "-update", "1", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def progress_expr(T, dur):
    local = f"(t-{T})"
    grow = f"({local}/{SLIDE_IN})"
    hold = "1.0"
    shrink = f"(({dur}-{local})/{SLIDE_OUT})"
    return (
        f"if(lt({local},0),0,"
        f"if(lt({local},{SLIDE_IN}),{grow},"
        f"if(lt({local},{dur}-{SLIDE_OUT}),{hold},"
        f"if(lt({local},{dur}),{shrink},0))))"
    )


def escape_filter_path(path):
    """Escape an absolute Windows path for embedding inside a single-quoted
    ffmpeg filter option value (colons and backslashes are filter-graph
    syntax). A literal apostrophe -- e.g. from an episode folder name like
    "Bastogne's Ghost Battery" -- can't be escaped with a backslash inside
    a single-quoted value; ffmpeg's own quoting rules require closing the
    quote, inserting a backslash-escaped literal quote, then reopening it,
    same as POSIX shell single-quote escaping."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")


TITLE_FADE_IN = 0.6
TITLE_FADE_OUT = 0.8


def title_alpha_expr(duration):
    return (
        f"if(lt(t,{TITLE_FADE_IN}),t/{TITLE_FADE_IN},"
        f"if(lt(t,{duration}-{TITLE_FADE_OUT}),1,"
        f"if(lt(t,{duration}),({duration}-t)/{TITLE_FADE_OUT},0)))"
    )


def escape_text(text):
    """Escape text for embedding as a drawtext 'text' value."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def build_filter_complex(srt_path, times, dur, top_margin, right_margin,
                          caption_fontsize, video_height, video_width,
                          title_text=None, title_duration=4.5):
    n = len(times)
    chain = ""
    if n > 0:
        split_labels = "".join(f"[btn{i+1}]" for i in range(n))
        chain += f"[1:v]split={n}{split_labels};"

    if srt_path is not None:
        outline = round(caption_fontsize * 0.14, 1)
        shadow = round(caption_fontsize * 0.06, 1)
        margin_v = round(video_height * 0.05)
        style = (
            f"FontName=Arial,Bold=-1,FontSize={caption_fontsize},PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,BorderStyle=1,Outline={outline},Shadow={shadow},"
            f"Alignment=2,MarginV={margin_v}"
        )
        srt_escaped = escape_filter_path(srt_path)
        chain += f"[0:v]subtitles='{srt_escaped}':force_style='{style}'[subbed];"
        prev = "subbed"
    else:
        prev = "0:v"

    if title_text:
        title_fontsize = round(video_height * 0.07)
        alpha = title_alpha_expr(title_duration)
        enable = f"between(t,0,{title_duration})"
        # Static full-frame dim (fixed enable window, no per-frame position
        # math) behind the title so it reads over busy footage, then the
        # title itself fades in/out on top.
        chain += (
            f"[{prev}]drawbox=x=0:y=0:w=iw:h=ih:color=black@0.35:t=fill:"
            f"enable='{enable}'[dimmed];"
        )
        chain += (
            f"[dimmed]drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
            f"text='{escape_text(title_text)}':fontsize={title_fontsize}:"
            f"fontcolor=white:borderw=3:bordercolor=black@0.8:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:alpha='{alpha}':"
            f"enable='{enable}'[titled];"
        )
        prev = "titled"

    for i, T in enumerate(times):
        p = progress_expr(T, dur)
        y_expr = f"-(h+10)+({top_margin}+h+10)*({p})"
        x_expr = f"W-w-{right_margin}"
        enable = f"between(t,{T},{T + dur})"
        out_label = f"v{i+1}"
        chain += (
            f"[{prev}][btn{i+1}]overlay=x='{x_expr}':y='{y_expr}':"
            f"enable='{enable}':shortest=1[{out_label}];"
        )
        prev = out_label

    return chain.rstrip(";"), prev


def auto_space_times(duration, count, dur):
    """Evenly space up to `count` pop-ups between 8% and 92% of the timeline,
    capping the count so consecutive pop-ups never overlap -- each one stays
    on screen for `dur` seconds, so windows need at least `dur` between starts
    or the next pop-up starts sliding in while the previous is still held on
    screen (looks like one button is frozen while another moves)."""
    if count <= 0:
        return []
    start, end = duration * 0.08, duration * 0.92
    span = end - start
    max_count = max(int(span // dur) + 1, 1)
    if count > max_count:
        print(f"Note: {count} pop-ups requested but only {max_count} fit in "
              f"{span:.1f}s without overlapping (each held {dur}s) -- using {max_count}.")
        count = max_count
    if count == 1:
        return [start]
    step = max(span / (count - 1), dur)
    return [round(start + i * step, 1) for i in range(count)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--srt", default=None, help="Omit or pass --no-captions to skip burned-in captions.")
    ap.add_argument("--no-captions", action="store_true", help="Skip burned-in captions entirely.")
    ap.add_argument("--caption-fontsize", type=int, default=20)
    ap.add_argument("--subscribe-duration", type=float, default=10.0,
                     help="Total on-screen time per pop-up, including slide in/out (default 10s).")
    ap.add_argument("--subscribe-times", default=None,
                     help="Comma-separated timestamps in seconds, e.g. 180,560,1020,1480")
    ap.add_argument("--subscribe-count", type=int, default=4,
                     help="Auto-space this many pop-ups if --subscribe-times isn't given.")
    ap.add_argument("--title", default=None,
                     help="Opening title card text (fades in over the first few seconds, "
                          "then out). Omit for no title card.")
    ap.add_argument("--title-duration", type=float, default=4.5,
                     help="How long the title card is on screen, including fades (default 4.5s).")
    args = ap.parse_args()

    video = Path(args.video).resolve()
    out = Path(args.output).resolve()
    srt = None if (args.no_captions or not args.srt) else Path(args.srt).resolve()
    work_dir = out.parent

    srt_temp_copy = None
    if srt is not None and "'" in str(srt):
        # The subtitles filter parses its `filename` argument with its own
        # private sub-parser (separate from the outer filtergraph parser),
        # which doesn't reliably honor the standard '\'' escape for a
        # literal apostrophe -- it can swallow the quote AND merge the
        # following :force_style=... option into the filename. Simplest
        # robust fix: stage the SRT under a path with no apostrophe.
        fd, temp_name = tempfile.mkstemp(suffix=".srt", prefix="subs_")
        os.close(fd)
        srt_temp_copy = Path(temp_name)
        shutil.copyfile(srt, srt_temp_copy)
        srt = srt_temp_copy

    duration = get_duration(video)
    width, height = get_resolution(video)
    print(f"Source: {duration:.2f}s @ {width}x{height}")

    if args.subscribe_duration < SLIDE_IN + SLIDE_OUT + 0.1:
        raise SystemExit("--subscribe-duration must be longer than the slide in/out time (~0.8s).")

    if args.subscribe_times:
        times = [float(x) for x in args.subscribe_times.split(",")]
    else:
        times = auto_space_times(duration, args.subscribe_count, args.subscribe_duration)
    print(f"Subscribe pop-up times: {times} (each held {args.subscribe_duration}s)")

    # Button sized proportionally to the video canvas (matches the 420x110 @ 1280x720 design).
    button_width = round(width * 0.328)
    button_height = round(height * 0.153)
    button_fontsize = round(button_height * 0.31)
    button_path = work_dir / "button.png"
    work_dir.mkdir(parents=True, exist_ok=True)
    build_button_png(button_width, button_height, button_fontsize, button_path)
    print(f"Button: {button_width}x{button_height}, font {button_fontsize}")

    top_margin = round(height * 0.0417)
    right_margin = round(width * 0.03125)

    chain, final_label = build_filter_complex(
        srt, times, args.subscribe_duration, top_margin, right_margin,
        args.caption_fontsize, height, width,
        title_text=args.title, title_duration=args.title_duration,
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-loop", "1", "-i", str(button_path),
        "-filter_complex", chain,
        "-map", f"[{final_label}]", "-map", "0:a",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-c:a", "copy",
        "-shortest",
        str(out),
    ]
    print(" ".join(str(c) for c in cmd))
    try:
        subprocess.run(cmd, check=True)
    finally:
        if srt_temp_copy is not None:
            srt_temp_copy.unlink(missing_ok=True)
    print("Done ->", out)


if __name__ == "__main__":
    main()
