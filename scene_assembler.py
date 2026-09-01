#!/usr/bin/env python3
"""
Scene-based documentary assembler: reads a JSON list of scenes (mixed types
-- Ken Burns stills, real video clips, kinetic typography, stat counters,
title cards, split screens), renders each to its exact duration, applies
per-scene color grade / grain overlay / lower-third, then chains them
together with PER-SCENE transition types (crossfade / dipblack / hardcut /
wipeleft), matching a scripted production package rather than
make_slideshow.py's uniform manifest model.

SCENE JSON FORMAT (list of objects):
{
  "id": "scene01",
  "duration": 8.0,
  "type": "video" | "image" | "kinetic" | "counter" | "title" | "split",
  "transition_in": "crossfade" | "dipblack" | "hardcut" | "wipeleft",
  "grade": "sepia" | "warm" | null,
  "grain": "path/to/grain.mp4" | null,
  "grain_opacity": 0.3,
  "lower_third": ["LINE ONE", "LINE TWO"] | null,
  ... type-specific fields (see build_scene) ...
}

USAGE:
    python scene_assembler.py --scenes scenes.json --audio narration.m4a
        --output final.mp4 [--width 1920] [--height 1080] [--fps 24]
        [--work-dir render/scenes] [--batch-size 8]

Requires ffmpeg/ffprobe on PATH.
"""

import argparse
import json
import subprocess
from pathlib import Path

import scene_effects as fx
from make_slideshow import render_image_clip, render_video_clip, is_video

TRANSITION_MAP = {
    "crossfade": "fade",
    "dipblack": "fadeblack",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "hardcut": "fade",  # rendered with a near-zero duration below
}


def get_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip().split(",")[0])


def compute_frame_counts(durations, transitions, fps, trans_seconds):
    """Additive transition-padding, same model as make_slideshow.py: every
    scene except the very first is rendered `trans_frames` LONGER than its
    nominal on-screen duration, so the xfade chain has real extra content to
    blend from instead of truncating the intended visible span. Returns the
    list of RENDER frame counts (what each scene file must actually contain).

    IMPORTANT: this padding is ALWAYS the full trans_frames, even for
    "hardcut" transitions -- it must match build_xfade_chain's render_pad
    exactly (that's what makes the offsets correct), and it must NOT be
    shrunk to hardcut's near-zero visual duration. Confirmed on this
    project: using only a 1-frame pad for hardcut left zero slack for the
    offset math, and a small (2-frame) discrepancy between the theoretical
    and actual xfade output length silently stalled the ENTIRE downstream
    chain -- every scene after the hardcut transition vanished from the
    output despite the command exiting 0. hardcut's near-instant look is
    achieved separately, via a short xfade `duration=` in build_xfade_chain,
    not by starving the render padding."""
    trans_frames = int(round(trans_seconds * fps))
    counts = []
    for i, d in enumerate(durations):
        base = int(round(d * fps))
        if i == 0:
            counts.append(base)
        else:
            counts.append(base + trans_frames)
    return counts


def _done(path):
    """Resume support: every render/composite step in this module writes to
    a stable, content-addressed filename (scene id / batch index), so a
    prior run's output sitting on disk at full size IS the correct result --
    skip re-rendering it rather than redoing potentially hours of work after
    a stop/restart. Only a zero-byte or missing file counts as not done."""
    return path.exists() and path.stat().st_size > 0


def build_scene(scene, width, height, fps, work_dir, render_frames):
    """Render one scene (base content only, no grade/grain/lower-third yet)
    to work_dir/<id>_base.mp4 and return the path. `render_frames` is the
    padded length from compute_frame_counts, not the scene's raw nominal
    duration -- text/generated effects just hold their resting state a
    little longer to fill it; video/image pieces get a bit more content."""
    out = work_dir / f"{scene['id']}_base.mp4"
    t = scene["type"]
    frames = render_frames
    dur = frames / fps

    if _done(out):
        return out

    if t == "video":
        render_video_clip(
            Path(scene["asset"]), frames, width, height, fps, out,
            color_grade=False,  # scene-level grade applied afterward, uniformly
            start_offset=scene.get("start", 0.0),
            crop_bottom_frac=scene.get("crop_bottom", 0.0),
        )
    elif t == "image":
        render_image_clip(
            Path(scene["asset"]), frames, scene.get("effect_idx", 0),
            width, height, fps, out, color_grade=False,
        )
    elif t == "kinetic":
        fx.kinetic_typography(
            [tuple(p) for p in scene["phrases"]], width, height, fps, out,
            duration=dur, bg_color=scene.get("bg_color", "black"),
            fontsize=scene.get("fontsize", 80), gap=scene.get("gap", 0.5),
            hold=scene.get("hold", 1.5), attribution=scene.get("attribution"),
        )
    elif t == "counter":
        fx.stat_counter(
            scene["number"], scene["label"], width, height, fps, out,
            duration=dur, bg_color=scene.get("bg_color", "black"),
        )
    elif t == "title":
        fx.title_card(
            scene["title"], scene.get("subtitle", ""), width, height, fps, out,
            duration=dur, bg_color=scene.get("bg_color", "0x1a1a1a"),
        )
    elif t == "split":
        fx.split_screen(
            Path(scene["left"]), Path(scene["right"]), width, height, fps, out,
            duration=dur, left_caption=scene.get("left_caption"),
            right_caption=scene.get("right_caption"),
        )
    else:
        raise ValueError(f"Unknown scene type: {t}")
    return out


def post_process(scene, base_path, work_dir):
    """Apply grade -> grain -> lower_third in sequence, each optional."""
    current = base_path
    grade = scene.get("grade")
    if grade:
        graded = work_dir / f"{scene['id']}_graded.mp4"
        if not _done(graded):
            recipe = fx.WARM_GRADE if grade == "warm" else fx.SEPIA_GRADE
            fx.apply_color_grade(current, graded, grade=recipe)
        current = graded

    grain = scene.get("grain")
    if grain:
        grained = work_dir / f"{scene['id']}_grained.mp4"
        if not _done(grained):
            fx.apply_grain_overlay(current, Path(grain), grained,
                                    opacity=scene.get("grain_opacity", 0.3))
        current = grained

    lt = scene.get("lower_third")
    if lt:
        stamped = work_dir / f"{scene['id']}_lt.mp4"
        if not _done(stamped):
            fx.lower_third(current, lt[0], lt[1] if len(lt) > 1 else None, stamped)
        current = stamped

    return current


def ensure_has_audio(path, work_dir, fps):
    """Text/generated scenes (title/kinetic/counter/split) have -an (no audio
    stream). Give them silent audio so every scene input is uniform for the
    xfade+audio chain later."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    if probe.stdout.strip():
        return path
    out = work_dir / f"{path.stem}_silentaudio.mp4"
    if _done(out):
        return out
    duration = get_duration(path)
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        "-map", "0:v", "-map", "1:a",
        "-t", f"{duration:.3f}", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def build_xfade_chain(clips, transitions, frame_counts, fps, trans_seconds=1.0):
    """Chain clips[0..n-1] with per-clip transition_in (transitions[i] is the
    transition INTO clip i; transitions[0] is ignored). hardcut uses a very
    short crossfade (imperceptible) instead of a structurally different path.
    `frame_counts` must be the RENDER lengths from compute_frame_counts (each
    clip file's actual frame count), not nominal on-screen durations.
    Returns (filter_complex_string, final_video_label, final_audio_label).
    filter_complex_string is None for a single clip -- there's nothing to
    chain, and "-map [0:v]" (bracketed, a filter-graph output pad) is NOT
    the same thing as "-map 0:v" (a plain input stream specifier); passing
    an empty/absent filter script but still bracket-mapping is what caused a
    real failure on this project's last (single-clip) batch. The 4th return
    value is the TOTAL output frame count (not the input frame_counts list)
    -- callers need this to pin an explicit -t on the composite command,
    since xfade's video chain and acrossfade's audio chain can silently
    disagree on exactly when the stream ends (confirmed: composite_chain
    exited 0 but truncated the video stream well short of its target,
    reproducibly, with no -t safeguard in place)."""
    n = len(clips)
    if n == 1:
        return None, "0:v", "0:a", frame_counts[0]

    trans_frames = int(round(trans_seconds * fps))
    hardcut_frames = max(int(round(0.05 * fps)), 1)

    vchain, achain = [], []
    prev_v, prev_a = "0:v", "0:a"
    cumulative = frame_counts[0]
    for i in range(1, n):
        # render_pad is how many extra frames were actually rendered onto
        # this clip (compute_frame_counts always uses the full trans_frames,
        # even for hardcut) -- offset/cumulative MUST use this value, since
        # it reflects real available content. xfade_dur is only the visual
        # blend length passed to the filter's duration= parameter, which can
        # be near-zero for hardcut. Using hardcut's tiny value for BOTH
        # (the old bug) left offset with ~0 slack against the input's real
        # length; any few-frame drift between the theoretical and actual
        # xfade output length then silently stalled everything downstream.
        render_pad = trans_frames
        xfade_dur = hardcut_frames if transitions[i] == "hardcut" else trans_frames
        xfade_type = TRANSITION_MAP.get(transitions[i], "fade")
        offset_frames = cumulative - render_pad
        vout, aout = f"xv{i}", f"xa{i}"
        vchain.append(
            f"[{prev_v}][{i}:v]xfade=transition={xfade_type}:"
            f"duration={xfade_dur / fps:.6f}:offset={offset_frames / fps:.6f}[{vout}]"
        )
        achain.append(
            f"[{prev_a}][{i}:a]acrossfade=d={xfade_dur / fps:.6f}[{aout}]"
        )
        prev_v, prev_a = vout, aout
        cumulative += frame_counts[i] - render_pad

    return ";".join(vchain + achain), prev_v, prev_a, cumulative


def composite_chain(clips, transitions, frame_counts, fps, trans_seconds, out_path, work_dir, name):
    """Xfade+acrossfade-chain `clips` together (video AND audio) into one
    output file. `frame_counts` are each clip's actual render length (from
    compute_frame_counts / probed from a prior batch's real output)."""
    filt, vlabel, alabel, total_frames = build_xfade_chain(clips, transitions, frame_counts, fps, trans_seconds)
    if _done(out_path):
        print(f"\n=== {name} already composited, skipping ({out_path.name}) ===")
        return
    cmd = ["ffmpeg", "-y"]
    for p in clips:
        cmd += ["-i", str(p)]
    if filt is not None:
        script_path = work_dir / f"{name}_filter.txt"
        script_path.write_text(filt, encoding="utf-8")
        cmd += ["-filter_complex_script", str(script_path), "-map", f"[{vlabel}]", "-map", f"[{alabel}]"]
    else:
        cmd += ["-map", vlabel, "-map", alabel]
    # xfade's video chain and acrossfade's audio chain are two independently
    # timed filter graphs sharing one command -- they can silently disagree
    # on exactly when the stream ends. Confirmed on this project: composite_
    # chain exited 0, container-level duration looked plausible, but the
    # video stream itself stopped dead well short of the real target,
    # reproducibly. An explicit -t matching the actual computed total frame
    # count is the real safeguard, same lesson as every other looped/chained
    # render in this codebase.
    cmd += [
        "-t", f"{total_frames / fps:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", str(out_path),
    ]
    print(f"\n=== Compositing {name} ({len(clips)} inputs, target {total_frames / fps:.2f}s) ===")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


def _verify_no_truncation(path, expected_seconds, tolerance=2.0):
    """A composite_chain call can exit 0 while silently truncating the VIDEO
    stream well short of its target (confirmed on this project: exited
    clean, container-level duration looked fine because it reflects the
    longest stream, but ffprobe on the video stream specifically -- and
    actually extracting a frame near the end -- showed it stopped dead at a
    fixed frame count both times, reproducibly, on a 17-input flat chain).
    Never trust exit code or container-level duration alone for one of
    these chains; always check the video stream itself."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    v = out.stdout.strip().split(",")[0]
    actual = float(v) if v else 0.0
    if actual < expected_seconds - tolerance:
        raise RuntimeError(
            f"{path} video stream truncated: got {actual:.2f}s, expected ~{expected_seconds:.2f}s. "
            f"This chain step needs to be re-run (or batched smaller) -- do not trust its exit code."
        )


def chain_level(clips, transitions, frame_counts, fps, trans_seconds, batch_size, work_dir, level):
    """One level of batching: groups `clips` into batches of `batch_size`,
    composites each, and returns (batch_paths, batch_frame_counts) for the
    caller to feed into the next level up (or use directly if small enough)."""
    n = len(clips)
    batch_paths, batch_frame_counts = [], []
    for b, start in enumerate(range(0, n, batch_size)):
        batch_clips = clips[start:start + batch_size]
        batch_transitions = transitions[start:start + batch_size]
        batch_frames = frame_counts[start:start + batch_size]
        batch_out = work_dir / f"L{level}batch_{b:03d}.mp4"
        expected = sum(batch_frames) / fps  # generous upper bound (ignores overlap trim)
        composite_chain(batch_clips, batch_transitions, batch_frames, fps, trans_seconds,
                         batch_out, work_dir, f"L{level}batch_{b:03d}")
        _verify_no_truncation(batch_out, expected * 0.5)  # loose floor -- catches gross truncation, not overlap rounding
        batch_paths.append(batch_out)
        batch_frame_counts.append(int(round(get_duration(batch_out) * fps)))
    return batch_paths, batch_frame_counts


def chain_scenes(processed, transitions, frame_counts, fps, trans_seconds, batch_size, work_dir):
    """Chain all scenes together, batching to avoid the flat-chain slowdown
    (a 48-input flat chain took 3+ hours on a prior episode) AND to avoid a
    separate, confirmed silent-truncation failure mode on a 17-input flat
    chain (exited clean, container duration looked plausible, but the video
    stream itself stopped dead partway through, both times it was tried).
    Batches hierarchically -- if there are still more "clips" than
    batch_size after one round of batching, batch THOSE too, recursing
    until a single level's input count is small enough to chain directly."""
    clips, clip_transitions, counts = processed, transitions, frame_counts
    level = 0
    while len(clips) > batch_size:
        print(f"\n=== Level {level}: batching {len(clips)} inputs into groups of {batch_size} ===")
        new_clips, counts = chain_level(
            clips, clip_transitions, counts, fps, trans_seconds, batch_size, work_dir, level
        )
        # Each new "clip" (a batch output) inherits the transition_in of
        # its first original member for the next level up. Must be derived
        # from the PRE-batching clip_transitions/clips (not yet overwritten).
        clip_transitions = ["crossfade"] + [
            clip_transitions[i * batch_size] for i in range(1, len(new_clips))
        ]
        clips = new_clips
        level += 1

    out_path = work_dir / "assembled_video.mp4"
    print(f"\n=== Final level: chaining {len(clips)} inputs ===")
    composite_chain(clips, clip_transitions, counts, fps, trans_seconds, out_path, work_dir, "final")
    _verify_no_truncation(out_path, sum(counts) / fps * 0.5)
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenes", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--transition-duration", type=float, default=1.0)
    ap.add_argument("--batch-size", type=int, default=8,
                     help="Chain scenes in batches of this size, then chain the batch outputs "
                          "together, instead of one flat chain across all scenes (default 8). "
                          "A flat chain gets dramatically slower as input count grows -- confirmed "
                          "on a prior episode, a 48-input flat chain took 3+ hours to composite.")
    args = ap.parse_args()

    scenes = json.loads(Path(args.scenes).read_text(encoding="utf-8"))
    out_path = Path(args.output)
    work_dir = Path(args.work_dir) if args.work_dir else out_path.parent / "scene_pieces"
    work_dir.mkdir(parents=True, exist_ok=True)

    transitions = [s.get("transition_in", "crossfade") for s in scenes]
    durations = [s["duration"] for s in scenes]
    frame_counts = compute_frame_counts(durations, transitions, args.fps, args.transition_duration)

    processed = []
    for i, scene in enumerate(scenes):
        print(f"\n[{i+1}/{len(scenes)}] {scene['id']} ({scene['type']}, "
              f"{scene['duration']}s nominal, {frame_counts[i]} frames rendered)")
        base = build_scene(scene, args.width, args.height, args.fps, work_dir, frame_counts[i])
        final = post_process(scene, base, work_dir)
        final = ensure_has_audio(final, work_dir, args.fps)
        processed.append(final)

    total_video_out = chain_scenes(processed, transitions, frame_counts, args.fps,
                                    args.transition_duration, args.batch_size, work_dir)

    print("\n=== Muxing narration audio ===")
    narration_dur = get_duration(args.audio)
    cmd2 = [
        "ffmpeg", "-y", "-i", str(total_video_out), "-i", str(args.audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", f"{narration_dur:.3f}", str(out_path),
    ]
    print(" ".join(cmd2))
    subprocess.run(cmd2, check=True)

    print(f"\nDone -> {out_path}")


if __name__ == "__main__":
    main()
