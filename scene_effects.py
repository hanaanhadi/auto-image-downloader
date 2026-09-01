#!/usr/bin/env python3
"""
Scene-effect renderers for scripted, per-scene documentary edits (as opposed
to make_slideshow.py's uniform manifest/Ken-Burns/crossfade model). Each
function renders ONE finished, self-contained MP4 clip of an exact duration,
meant to be chained together afterward (see scene_assembler.py).

Effects implemented here:
  - kinetic_typography : phrases appear one at a time (word-by-word style)
  - stat_counter        : a number counts up to a final value, with a label
  - split_screen        : two videos side by side with a dividing line
  - lower_third         : location/date stamp burned onto existing footage
  - title_card          : full-screen title + subtitle on a solid background

Requires ffmpeg on PATH. A bold font is assumed at IMPACT_FONT (falls back to
Arial Bold if not found -- set FONT_PATH to override).
"""

import subprocess
from pathlib import Path

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\impact.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]
FONT_PATH = next((f for f in FONT_CANDIDATES if Path(f).exists()), FONT_CANDIDATES[-1])

# Production package's Step 2 exact recipe: desaturated warm sepia, dark
# vignette. Used on most scenes; WARM_GRADE is the lighter "positive" variant
# the package calls for on scenes meant to read less bleakly (e.g. Bates,
# 1997 recognition ceremony).
SEPIA_GRADE = (
    "eq=saturation=0.4:contrast=1.3:brightness=-0.05,"
    "colorbalance=rs=0.08:gs=0.04:bs=-0.04:rm=0.06:gm=0.02:bm=-0.03,"
    "vignette=PI/4:mode=backward"
)
WARM_GRADE = (
    "eq=saturation=0.65:contrast=1.15:brightness=-0.02,"
    "colorbalance=rs=0.05:gs=0.03:bs=-0.02:rm=0.03:gm=0.01:bm=-0.01,"
    "vignette=PI/5:mode=backward"
)


def apply_color_grade(in_path, out_path, grade=SEPIA_GRADE):
    cmd = [
        "ffmpeg", "-y", "-i", str(in_path), "-vf", grade,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        "-c:a", "copy", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def _probe_frame_count(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries", "stream=nb_read_frames",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    v = out.stdout.strip().split(",")[0]
    return int(v) if v.isdigit() else 150  # fallback if counting fails


def _probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip().split(",")[0])


def _probe_resolution(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = [v for v in out.stdout.strip().split(",") if v][:2]
    return int(w), int(h)


def apply_grain_overlay(in_path, grain_path, out_path, opacity=0.3):
    """Composites a looping film-grain clip on top at low opacity (package's
    Step 3). `in_path` should already be the exact target duration; the
    grain loops to cover it regardless of the grain clip's own length.
    loop's `size` must match the grain clip's actual frame count -- setting
    it far larger (e.g. a fixed 9999) makes the filter wait to buffer frames
    that don't exist, which hangs instead of erroring. `-shortest` alone is
    NOT a reliable stop condition on a filter graph with an infinite loop=-1
    input (confirmed: it ran to 10+ minutes on a 9s source) -- an explicit
    -t duration cap is the real safeguard, same pattern as every other
    looped-input render in this project."""
    duration = _probe_duration(in_path)
    grain_frames = _probe_frame_count(grain_path)
    main_w, main_h = _probe_resolution(in_path)
    # blend requires both inputs at identical resolution -- grain clips are
    # usually a small native size (e.g. 640x360), so scale to match explicitly.
    filt = (
        f"[1:v]loop=loop=-1:size={grain_frames},setpts=PTS-STARTPTS,"
        f"scale={main_w}:{main_h}[grain];"
        f"[0:v][grain]blend=all_mode=overlay:all_opacity={opacity}[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(in_path), "-i", str(grain_path),
        "-filter_complex", filt, "-map", "[out]", "-map", "0:a?",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        "-c:a", "copy", "-shortest", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def _esc(path_or_text, is_path=False):
    """Escape a string for embedding inside an ffmpeg filter option."""
    s = str(path_or_text)
    if is_path:
        s = s.replace("\\", "/")
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")


def kinetic_typography(phrases, width, height, fps, out_path, duration=None,
                        bg_color="black", fontsize=80, gap=0.5, hold=1.5,
                        attribution=None):
    """Phrases appear one at a time, each staying on screen once revealed
    (word-by-word/phrase-by-phrase reveal, matching the production package's
    Step 5). `phrases` is a list of (text, fontcolor) tuples, stacked
    vertically, centered. Each phrase reveals `gap` seconds after the
    previous one; the whole thing holds for `hold` seconds after the last
    phrase appears unless `duration` is given explicitly."""
    n = len(phrases)
    reveal_times = [0.3 + i * gap for i in range(n)]
    total_duration = duration or (reveal_times[-1] + hold)

    line_height = fontsize * 1.3
    total_text_height = line_height * n
    start_y = f"(h-{total_text_height})/2"

    filters = []
    for i, (text, color) in enumerate(phrases):
        y = f"{start_y}+{i * line_height}"
        filters.append(
            f"drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(text)}':"
            f"fontsize={fontsize}:fontcolor={color}:x=(w-text_w)/2:y={y}:"
            f"enable='gte(t,{reveal_times[i]:.2f})'"
        )
    if attribution:
        filters.append(
            f"drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(attribution)}':"
            f"fontsize={int(fontsize * 0.45)}:fontcolor=gray:x=(w-text_w)/2:"
            f"y={start_y}+{n * line_height}+20:enable='gte(t,{reveal_times[-1] + 0.4:.2f})'"
        )

    vf = ",".join(filters)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={bg_color}:s={width}x{height}:d={total_duration:.3f}",
        "-vf", vf, "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "18", "-an", str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return total_duration


def stat_counter(number, label, width, height, fps, out_path, duration=4.0,
                  bg_color="black", numcolor="white", labelcolor="0xCC0000",
                  fontsize=160, label_fontsize=40, count_fraction=0.6):
    """A number counts up from 0 to `number` over the first `count_fraction`
    of the clip, then holds; a label sits below it (package's Step 8)."""
    count_duration = max(duration * count_fraction, 0.1)
    rate = number / count_duration
    text_expr = f"%{{eif\\:min(floor(t*{rate:.4f})\\,{number})\\:d}}"
    vf = (
        f"drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{text_expr}':"
        f"fontsize={fontsize}:fontcolor={numcolor}:x=(w-text_w)/2:y=h/2-100,"
        f"drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(label)}':"
        f"fontsize={label_fontsize}:fontcolor={labelcolor}:x=(w-text_w)/2:y=h/2+60"
    )
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={bg_color}:s={width}x{height}:d={duration:.3f}",
        "-vf", vf, "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "18", "-an", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def split_screen(left_path, right_path, width, height, fps, out_path, duration,
                  divider_color="red", divider_thickness=4,
                  left_caption=None, right_caption=None, fontsize=36):
    """Two clips/images side by side with a dividing line (package's Step 9).
    Inputs are looped/trimmed to `duration`; images work via -loop 1."""
    def input_args(path):
        p = Path(path)
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
            return ["-loop", "1", "-i", str(p)]
        return ["-stream_loop", "-1", "-i", str(p)]

    half_w = width // 2
    vf = (
        f"[0:v]scale={half_w}:{height}:force_original_aspect_ratio=increase,"
        f"crop={half_w}:{height}[left];"
        f"[1:v]scale={width - half_w}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width - half_w}:{height}[right];"
        f"[left][right]hstack=inputs=2[stacked];"
        f"[stacked]drawbox=x={half_w - divider_thickness // 2}:y=0:"
        f"w={divider_thickness}:h={height}:color={divider_color}:t=fill[boxed]"
    )
    label = "boxed"
    if left_caption:
        vf += (
            f";[{label}]drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(left_caption)}':"
            f"fontsize={fontsize}:fontcolor=white:borderw=2:bordercolor=black:"
            f"x={half_w // 2}-text_w/2:y=h-100[c1]"
        )
        label = "c1"
    if right_caption:
        vf += (
            f";[{label}]drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(right_caption)}':"
            f"fontsize={fontsize}:fontcolor=white:borderw=2:bordercolor=black:"
            f"x={half_w}+{(width - half_w) // 2}-text_w/2:y=h-100[c2]"
        )
        label = "c2"

    cmd = ["ffmpeg", "-y"]
    cmd += input_args(left_path)
    cmd += input_args(right_path)
    cmd += [
        "-t", f"{duration:.3f}", "-filter_complex", vf, "-map", f"[{label}]",
        "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        "-an", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def lower_third(video_path, line1, line2, out_path, start=1.0, hold=4.0,
                 fontsize1=28, fontsize2=20, box_color="0xCC0000"):
    """Burns a location/date stamp onto existing footage (package's Step 7),
    re-encoding the whole clip. `video_path` should already be the exact
    target duration."""
    end = start + hold
    # drawbox can't reference a separate drawtext filter's text_w (no shared
    # eval context between filter instances) -- estimate box width from
    # character count instead of relying on that.
    box_w = int(len(line1) * fontsize1 * 0.62) + 40
    vf = (
        f"drawbox=x=50:y=h-95:w={box_w}:h=40:color={box_color}@0.75:t=fill:"
        f"enable='between(t,{start},{end})',"
        f"drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(line1)}':"
        f"fontsize={fontsize1}:fontcolor=white:borderw=1:bordercolor=black:"
        f"x=60:y=h-80:enable='between(t,{start},{end})'"
    )
    if line2:
        vf += (
            f",drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(line2)}':"
            f"fontsize={fontsize2}:fontcolor=white:x=60:y=h-50:"
            f"enable='between(t,{start},{end})'"
        )
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path), "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        "-c:a", "copy", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def title_card(title, subtitle, width, height, fps, out_path, duration=4.0,
               bg_color="0x1a1a1a", title_fontsize=64, subtitle_fontsize=32,
               subtitle_color="0xCC0000", fade=0.5):
    """Full-screen section title card (package's Step 7 first example)."""
    on_start, on_end = fade, duration - fade
    vf = (
        f"drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(title)}':"
        f"fontsize={title_fontsize}:fontcolor=white:x=(w-text_w)/2:y=h/2-40:"
        f"enable='between(t,{on_start},{on_end})'"
    )
    if subtitle:
        vf += (
            f",drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(subtitle)}':"
            f"fontsize={subtitle_fontsize}:fontcolor={subtitle_color}:x=(w-text_w)/2:y=h/2+40:"
            f"enable='between(t,{on_start},{on_end})'"
        )
    vf += f",fade=t=in:st=0:d={fade},fade=t=out:st={duration - fade}:d={fade}"
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={bg_color}:s={width}x{height}:d={duration:.3f}",
        "-vf", vf, "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "18", "-an", str(out_path),
    ]
    subprocess.run(cmd, check=True)
