#!/usr/bin/env python3
"""
Reusable module: cut short-form (TikTok / YouTube Shorts / Reels) vertical
clips out of a finished long-form episode. Meant to run once a long video
(episode) is fully assembled -- give it the finished master file and a short
list of hand-picked high-engagement segments (with a scroll-stopping opening
hook line), and it produces ready-to-upload 9:16 vertical MP4s with:

  - a blurred-fill vertical reformat (not a stretched or naively cropped 16:9)
  - a slow reframing zoom for motion energy on otherwise-static shots
  - single-word, pop-animated captions (the professional short-form style --
    NOT burned-in sentence/phrase blocks), word-accurate because they come
    from a real word-level transcription of each cut clip's own audio, not
    from re-slicing the episode's sentence-level .srt
  - a bold hook-text card over the opening ~2.5s to stop the scroll
  - clean fade in/out at the cut points, since these are arbitrary
    mid-episode cuts, not authored scene boundaries

Segment picking (which moments are surprising / emotional / self-contained)
is an editorial judgment call and is NOT automated here -- pass in the
segments you've chosen via SHORT SPECS.

USAGE (as a library):
    from make_shorts import create_short, ShortSpec
    create_short(
        source_video=Path("episodes/general patton/patton_final.mp4"),
        spec=ShortSpec(start=1007.12, end=1071.84,
                       hook_lines=["NOVEMBER 19, 1944", "ONE TANK VS THE GERMAN ARMY"],
                       out_name="short1_sacrifice"),
        out_dir=Path("episodes/general patton/shorts"),
    )

USAGE (CLI, batch from a JSON spec file -- see SPEC JSON FORMAT below):
    python make_shorts.py --source patton_final.mp4 \
        --specs shorts_spec.json --out-dir episodes/general patton/shorts

SPEC JSON FORMAT (list of objects):
[
  {"start": 1007.12, "end": 1071.84, "out_name": "short1_sacrifice",
   "hook_lines": ["NOVEMBER 19, 1944", "ONE TANK VS THE GERMAN ARMY"]},
  ...
]

Requires ffmpeg (with libass for the subtitles filter) on PATH, and
faster-whisper installed (for the per-clip word-level transcription).
"""

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from scene_effects import FONT_PATH, _esc

WIDTH, HEIGHT, FPS = 1080, 1920, 24
HOOK_SHOW_SECONDS = 2.6
EDGE_FADE = 0.35
ZOOM_AMOUNT = 0.06          # slow 6% reframing zoom over the clip, same amount
                             # already proven on real footage in make_slideshow.py
CAPTION_Y_FRAC = 0.68       # vertical position of the word captions (below the
                             # video band, above the very bottom safe zone)
CAPTION_BASE_FONTSIZE = 100
MIN_WORD_DISPLAY = 0.14

_WHISPER_MODELS = {}


@dataclass
class ShortSpec:
    start: float           # seconds into the source video
    end: float              # seconds into the source video
    out_name: str
    hook_lines: list = field(default_factory=list)  # 1-3 short punchy lines


def _get_whisper_model(size="small"):
    if size not in _WHISPER_MODELS:
        from faster_whisper import WhisperModel
        print(f"Loading faster-whisper model '{size}'...")
        _WHISPER_MODELS[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _WHISPER_MODELS[size]


def _extract_audio(source_video, start, end, out_path):
    cmd = [
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(source_video), "-vn", "-ac", "1", "-ar", "16000",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def transcribe_words(audio_path, model_size="small"):
    """Real per-word (word, start, end) timestamps for `audio_path`, timed
    relative to that audio file's own start (0.0) -- since callers pass in
    an already-cut clip's audio, these line up with the clip's own timeline
    directly, no shifting needed."""
    model = _get_whisper_model(model_size)
    segments, _ = model.transcribe(str(audio_path), beam_size=5, word_timestamps=True)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            words.append({"word": w.word, "start": w.start, "end": w.end})
    return words


def _clean_word(raw):
    text = raw.strip()
    text = re.sub(r"^[^\w]+|[^\w]+$", "", text)
    return text.upper()


def build_word_bursts(words, min_display=MIN_WORD_DISPLAY):
    """One caption event per spoken word (the professional short-form style
    -- flashing single words in rhythm with speech, not sentence blocks).
    Extends short words up to `min_display` seconds so nothing flickers by
    unreadably, but never into the next word's own start time."""
    bursts = []
    n = len(words)
    for i, w in enumerate(words):
        text = _clean_word(w["word"])
        if not text:
            continue
        start = w["start"]
        end = max(w["end"], start + min_display)
        if i + 1 < n:
            end = min(end, words[i + 1]["start"])
        if end <= start:
            end = start + 0.05
        bursts.append((start, end, text))
    return bursts


def _ass_time(t):
    t = max(t, 0.0)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _fontsize_for(word, base=CAPTION_BASE_FONTSIZE):
    """Long words would overrun the frame width at the base size -- scale
    down proportionally past a reasonable character-count threshold instead
    of letting ASS silently overflow past the frame edges."""
    if len(word) <= 9:
        return base
    return max(int(base * 9 / len(word)), 46)


def write_word_ass(bursts, path, width=WIDTH, height=HEIGHT):
    """Native ASS (not plain SRT) so PlayResX/PlayResY can be declared
    explicitly, matching WIDTHxHEIGHT exactly -- style values then mean
    literal pixels with no libass default-resolution guessing involved
    (that guessing is what silently broke sizing/position earlier in this
    module's development against plain SRT input). Each word pops in with a
    quick scale animation (60% -> 105% -> 100%) via ASS \\t() override tags,
    the standard "karaoke word pop" look; words containing a digit render in
    a gold highlight color to draw the eye to dates/counts/stats."""
    pos_x = width // 2
    pos_y = int(height * CAPTION_Y_FRAC)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Word,Impact,{CAPTION_BASE_FONTSIZE},&H00FFFFFF,&H000000FF,&H00000000,"
        "&H00000000,-1,0,0,0,100,100,0,0,1,6,0,5,20,20,20,1\n"
        f"Style: Highlight,Impact,{CAPTION_BASE_FONTSIZE},&H0000D7FF,&H000000FF,&H00000000,"
        "&H00000000,-1,0,0,0,100,100,0,0,1,6,0,5,20,20,20,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for start, end, text in bursts:
        style = "Highlight" if any(c.isdigit() for c in text) else "Word"
        fs = _fontsize_for(text)
        pop_ms = max(int(min(end - start, 0.3) * 1000 * 0.5), 60)
        override = (
            f"{{\\an5\\pos({pos_x},{pos_y})\\fs{fs}\\fscx60\\fscy60"
            f"\\t(0,{pop_ms},\\fscx105\\fscy105)\\t({pop_ms},{pop_ms + 60},\\fscx100\\fscy100)}}"
        )
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,"
            f"{override}{_ass_escape(text)}\n"
        )
    Path(path).write_text("".join(lines), encoding="utf-8")


def _vertical_reformat_chain(in_label, out_label):
    """16:9 source -> WIDTHxHEIGHT vertical: blurred, cropped, darkened full-
    bleed background of the same footage, with the original frame scaled to
    fit the width and centered on top. Avoids the amateur look of a naive
    center-crop (loses both edges of the frame) or a stretched fill."""
    return (
        f"[{in_label}]split=2[bg{out_label}][fg{out_label}];"
        f"[bg{out_label}]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},gblur=sigma=30,eq=brightness=-0.08[bgblur{out_label}];"
        f"[fg{out_label}]scale={WIDTH}:-2[fgscaled{out_label}];"
        f"[bgblur{out_label}][fgscaled{out_label}]overlay=(W-w)/2:(H-h)/2[{out_label}]"
    )


def _zoom_chain(in_label, out_label, duration, amount=ZOOM_AMOUNT):
    """Slow, steady reframing zoom over the clip's full duration -- the same
    time-varying-crop technique already proven on real video footage
    elsewhere in this codebase (make_slideshow.py's render_video_clip),
    reused here because a static shot reads as flat/inserted in short-form,
    where viewers expect constant motion. Applied to the vertical composite
    BEFORE captions are drawn, so the burned-in text stays crisp and static
    rather than zooming with the footage."""
    return (
        f"[{in_label}]crop=w='iw*(1-{amount}*min(t,{duration:.3f})/{duration:.3f})':"
        f"h='ih*(1-{amount}*min(t,{duration:.3f})/{duration:.3f})':"
        f"x='(iw-ow)/2':y='(ih-oh)/2',scale={WIDTH}:{HEIGHT}[{out_label}]"
    )


def _hook_fontsize_for(text, base=76, width=WIDTH, max_width_frac=0.90, box_pad=40):
    """Long hook lines would overrun the vertical frame's width at the base
    size (Impact is bold/wide) -- scale down proportionally by character
    count past a safe threshold instead of letting drawtext silently clip
    text off both edges (x=(w-text_w)/2 goes negative once text_w > w).
    box_pad accounts for the boxborderw padding around the text, which
    isn't included in drawtext's own text_w-based centering."""
    max_width = width * max_width_frac - box_pad
    est_width = len(text) * base * 0.48
    if est_width <= max_width:
        return base
    return max(int(base * max_width / est_width), 36)


def _hook_text_filters(hook_lines, fontsize=76):
    """Bold, boxed, stacked hook lines shown for the first HOOK_SHOW_SECONDS
    -- matches the project's existing kinetic_typography/title_card look
    (same FONT_PATH, same drawtext-per-line stacking convention)."""
    if not hook_lines:
        return ""
    line_height = fontsize * 1.25
    start_y = "(h*0.14)"
    filters = []
    for i, text in enumerate(hook_lines):
        y = f"{start_y}+{i}*{line_height:.0f}"
        fs = _hook_fontsize_for(text, fontsize)
        filters.append(
            f"drawtext=fontfile='{_esc(FONT_PATH, True)}':text='{_esc(text)}':"
            f"fontsize={fs}:fontcolor=white:borderw=4:bordercolor=black:"
            f"box=1:boxcolor=black@0.45:boxborderw=18:"
            f"x=(w-text_w)/2:y={y}:"
            f"enable='between(t,0,{HOOK_SHOW_SECONDS})'"
        )
    return ",".join(filters)


def create_short(source_video, spec, out_dir, work_dir=None, whisper_model="small"):
    """Cut, vertically reformat, zoom, caption (real word-level, pop-
    animated), and hook-overlay one short from `source_video` per `spec` (a
    ShortSpec). Writes <out_dir>/<out_name>.mp4 and returns its path."""
    source_video = Path(source_video)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(work_dir) if work_dir else out_dir / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    duration = spec.end - spec.start

    audio_slice = work_dir / f"{spec.out_name}_audio.wav"
    _extract_audio(source_video, spec.start, spec.end, audio_slice)
    words = transcribe_words(audio_slice, whisper_model)
    bursts = build_word_bursts(words)
    ass_out = work_dir / f"{spec.out_name}.ass"
    write_word_ass(bursts, ass_out)
    print(f"  {len(bursts)} word captions transcribed for '{spec.out_name}'")

    vf_chain = _vertical_reformat_chain("0:v", "vert")
    zoom_chain = _zoom_chain("vert", "zoomed", duration)
    hook_chain = _hook_text_filters(spec.hook_lines)
    ass_escaped = _esc(ass_out, True)

    filt = f"{vf_chain};{zoom_chain};[zoomed]subtitles=filename='{ass_escaped}'[capped]"
    if hook_chain:
        filt += f";[capped]{hook_chain}[hooked]"
        final_label = "hooked"
    else:
        final_label = "capped"
    filt += (
        f";[{final_label}]fade=t=in:st=0:d={EDGE_FADE},"
        f"fade=t=out:st={duration - EDGE_FADE:.3f}:d={EDGE_FADE}[out]"
    )

    out_path = out_dir / f"{spec.out_name}.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{spec.start:.3f}", "-to", f"{spec.end:.3f}",
        "-i", str(source_video),
        "-filter_complex", filt,
        "-map", "[out]", "-map", "0:a",
        "-af", f"afade=t=in:st=0:d={EDGE_FADE},afade=t=out:st={duration - EDGE_FADE:.3f}:d={EDGE_FADE}",
        "-avoid_negative_ts", "make_zero",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    print(f"\n=== Creating short '{spec.out_name}' ({spec.start:.2f}s-{spec.end:.2f}s, {duration:.1f}s) ===")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    return out_path


def create_shorts(source_video, specs, out_dir, whisper_model="small"):
    return [create_short(source_video, spec, out_dir, whisper_model=whisper_model) for spec in specs]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True)
    ap.add_argument("--specs", required=True, help="Path to a JSON file (see SPEC JSON FORMAT in the module docstring)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--whisper-model", default="small")
    args = ap.parse_args()

    raw_specs = json.loads(Path(args.specs).read_text(encoding="utf-8"))
    specs = [ShortSpec(start=s["start"], end=s["end"], out_name=s["out_name"],
                        hook_lines=s.get("hook_lines", [])) for s in raw_specs]
    paths = create_shorts(args.source, specs, args.out_dir, whisper_model=args.whisper_model)
    for p in paths:
        print(f"Done -> {p}")


if __name__ == "__main__":
    main()
