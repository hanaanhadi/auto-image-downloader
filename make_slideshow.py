#!/usr/bin/env python3
"""
Reusable image-slideshow builder: stretches a folder of images across an
audio track's exact duration, applying varied Ken-Burns-style animations per
image and crossfade transitions between them.

USAGE:
    python make_slideshow.py --images-dir DIR --audio FILE --output FILE.mp4
        [--transition-duration 1.0] [--width 1280] [--height 720] [--fps 30]

Requires ffmpeg/ffprobe on PATH.
"""

import argparse
import subprocess
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}


def is_video(path):
    return path.suffix.lower() in VIDEO_EXTS

# Each effect is a (zoom_expr, x_expr, y_expr) triple for the zoompan filter.
# 'on' = output frame index (0-based). Motion uses smoothstep easing
# (ease-in-out) instead of linear steps -- linear motion reads as cheap
# slideshow; eased motion reads as a deliberate camera move, which is the
# single biggest "cinematic feel" upgrade for stills.
def _smooth(p):
    # smoothstep ease-in-out over a 0..1 progress expression p
    return f"(({p})*({p})*(3-2*({p})))"

EFFECTS = [
    # zoom in, centered (eased)
    lambda d: (
        f"1+0.16*{_smooth(f'on/{max(d - 1, 1)}')}",
        "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)",
    ),
    # zoom out, centered (eased)
    lambda d: (
        f"1.16-0.16*{_smooth(f'on/{max(d - 1, 1)}')}",
        "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)",
    ),
    # pan left -> right (fixed zoom, eased)
    lambda d: (
        "1.14",
        f"(iw-iw/zoom)*{_smooth(f'on/{max(d - 1, 1)}')}",
        "ih/2-(ih/zoom/2)",
    ),
    # pan right -> left (fixed zoom, eased)
    lambda d: (
        "1.14",
        f"(iw-iw/zoom)*(1-{_smooth(f'on/{max(d - 1, 1)}')})",
        "ih/2-(ih/zoom/2)",
    ),
    # pan top -> bottom (fixed zoom, eased)
    lambda d: (
        "1.14",
        "iw/2-(iw/zoom/2)",
        f"(ih-ih/zoom)*{_smooth(f'on/{max(d - 1, 1)}')}",
    ),
    # pan bottom -> top (fixed zoom, eased)
    lambda d: (
        "1.14",
        "iw/2-(iw/zoom/2)",
        f"(ih-ih/zoom)*(1-{_smooth(f'on/{max(d - 1, 1)}')})",
    ),
]

# Documentary editing convention: soft blends/dissolves, cycled for variety --
# not hard-edged wipes/slides/circle-opens, which read as "corporate
# slideshow" rather than documentary.
TRANSITIONS = ["fade", "dissolve", "fadeblack", "smoothleft", "smoothright"]


def get_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def list_images(images_dir):
    files = sorted(
        p for p in Path(images_dir).iterdir()
        if p.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS
    )
    if not files:
        raise SystemExit(f"No images found in {images_dir}")
    return [(p, None, {}) for p in files]


def load_manifest(manifest_path):
    """One image/video path per line (blank lines / #comments ignored).
    Repeats allowed -- lets a limited pool be reused across a long timeline.
    Pipe-separated fields after the path:
      - a bare number sets the weight/duration in seconds, e.g. 'foo.jpg|45'
        to hold foo.jpg for ~45s regardless of how many other entries there
        are.
      - key=value fields set per-clip options -- currently for video clips
        only: 'start=SS' seeks that many seconds into the source before
        rendering (pick a specific moment out of a long reel instead of
        always using the start), 'crop_bottom=FRAC' crops that fraction off
        the bottom of the frame before compositing (strips a burned-in
        watermark/timecode band some archival transfers carry).
      e.g. 'reel.mp4|18|start=245|crop_bottom=0.15'
    Returns a list of (Path, weight_or_None, options_dict) tuples."""
    base = Path(manifest_path).parent
    entries = []
    for line in Path(manifest_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        path_str = parts[0].strip()
        weight = None
        options = {}
        for field in parts[1:]:
            field = field.strip()
            if "=" in field:
                key, _, value = field.partition("=")
                options[key.strip()] = float(value.strip())
            else:
                weight = float(field)
        p = Path(path_str)
        if not p.is_absolute():
            p = base / p
        if not p.exists():
            raise SystemExit(f"Manifest references missing file: {p}")
        entries.append((p, weight, options))
    if not entries:
        raise SystemExit(f"Manifest {manifest_path} has no entries")
    return entries


# Subtle, uniform "documentary" grade applied to every clip -- slightly
# desaturated, faint warm tone, a touch more contrast, plus a gentle S-curve
# for a filmic highlight rolloff. Ties together a mixed pool of full-color
# modern photos and B&W archival scans into one consistent look, instead of
# jarring swings between them.
COLOR_GRADE = (
    "eq=saturation=0.88:contrast=1.06:brightness=0.01,"
    "colorbalance=rs=0.04:gs=0.00:bs=-0.05:rm=0.03:gm=0.00:bm=-0.04:rh=0.02:gh=0.00:bh=-0.03,"
    "curves=m='0/0 0.45/0.47 1/1'"
)

# Cinematic finish -- applied after the grade, on by default:
# a soft vignette to focus the eye, and fine temporal film grain to kill the
# sterile digital look. Both are subtle; disable with --no-vignette /
# --no-film-grain if a clip needs to stay perfectly clean.
def cinematic_finish(vignette=True, film_grain=True):
    parts = []
    if vignette:
        parts.append("vignette=a=PI/4.6")
    if film_grain:
        parts.append("noise=alls=6:allf=t")
    return ",".join(parts)


def render_image_clip(image_path, frames, effect_idx, width, height, fps, out_path,
                      color_grade=True, vignette=True, film_grain=True):
    z_expr, x_expr, y_expr = EFFECTS[effect_idx % len(EFFECTS)](frames)
    w2, h2 = width * 2, height * 2
    # Portrait/text-caption images (common for hero graphics) would lose
    # faces or captions to a naive center-crop against a 16:9 canvas. Instead:
    # fill the frame with a blurred, darkened copy of the same image, then
    # composite the FULL (uncropped) image on top, scaled to fit -- nothing
    # is ever cut off, and there's no harsh letterboxing.
    vf = (
        f"split=2[bg][fg];"
        f"[bg]scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
        f"gblur=sigma=30,eq=brightness=-0.15[bg2];"
        f"[fg]scale={w2}:{h2}:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={fps},"
        f"setsar=1"
    )
    if color_grade:
        vf += "," + COLOR_GRADE
    finish = cinematic_finish(vignette=vignette, film_grain=film_grain)
    if finish:
        vf += "," + finish
    # Generous -t so the loop always has enough source frames; -frames:v pins
    # the exact output frame count so piece durations are frame-accurate.
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path), "-t", f"{frames / fps + 2:.3f}",
        "-vf", vf, "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def render_video_clip(video_path, frames, width, height, fps, out_path, color_grade=True,
                      start_offset=0.0, crop_bottom_frac=0.0,
                      vignette=True, film_grain=True):
    """Real footage already has its own motion, but a static crop still reads
    as "inserted stock clip" rather than an intentional shot -- so add a slow,
    steady 6% reframing zoom on top (gentler than the stills' Ken Burns, since
    it's layering onto existing motion, not creating motion from nothing).
    zoompan can't be used here (it's built for stepping through still frames,
    not real video), so the zoom is done with a time-varying crop instead,
    using eval=frame so the crop window is re-evaluated every frame from the
    real elapsed presentation time (t) rather than once at filter init.
    -stream_loop -1 covers clips shorter than the requested duration;
    -frames:v (backed by a generous -t cap, same safeguard pattern as the
    image renderer) pins the exact output length. start_offset seeks into a
    long source reel to pick a specific moment; crop_bottom_frac strips a
    burned-in watermark/timecode band some archival transfers carry."""
    w2, h2 = width * 2, height * 2
    duration = frames / fps
    zoom = (
        f"crop=w='iw*(1-0.06*min(t,{duration})/{duration})':"
        f"h='ih*(1-0.06*min(t,{duration})/{duration})':"
        f"x='(iw-ow)/2':y='(ih-oh)/2'"
    )
    vf = ""
    if crop_bottom_frac > 0:
        vf += f"crop=iw:ih*{1 - crop_bottom_frac}:0:0,"
    vf += (
        f"split=2[bg][fg];"
        f"[bg]scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
        f"gblur=sigma=30,eq=brightness=-0.15[bg2];"
        f"[fg]scale={w2}:{h2}:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,"
        f"{zoom},"
        f"scale={width}:{height},fps={fps},setsar=1"
    )
    if color_grade:
        vf += "," + COLOR_GRADE
    finish = cinematic_finish(vignette=vignette, film_grain=film_grain)
    if finish:
        vf += "," + finish
    cmd = [
        "ffmpeg", "-y", "-stream_loop", "-1",
    ]
    if start_offset > 0:
        cmd += ["-ss", f"{start_offset:.3f}"]
    cmd += [
        "-i", str(video_path),
        "-t", f"{frames / fps + 2:.3f}",
        "-vf", vf, "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def compute_clip_frame_counts(weights, total_frames, trans_frames, fps):
    """Per-clip render frame counts. Each clip i>0 overlaps the PREVIOUS clip
    by trans_frames (consumed by the crossfade), so its own "solo" on-screen
    span in the final timeline is (clip_frames[i] - trans_frames); clip 0 has
    no incoming overlap, so its solo span is its full clip_frames.

    If explicit weights (seconds) are given, each clip's solo span is set to
    exactly that many frames -- trans_frames is ADDED on top per clip (not
    redistributed via a global rescale), so explicit weights are honored
    exactly instead of being uniformly inflated/distorted by the transition
    overlap accounting. Un-weighted (None) entries fall back to an equal
    share of whatever's left after weighted entries are subtracted out.
    """
    n = len(weights)
    if all(w is None for w in weights):
        solo_frames = [total_frames // n] * n
        solo_frames[-1] += total_frames - sum(solo_frames)
    else:
        solo_frames = [int(round(w * fps)) if w is not None else None for w in weights]
        weighted_total = sum(f for f in solo_frames if f is not None)
        n_unweighted = sum(1 for f in solo_frames if f is None)
        remaining = total_frames - weighted_total
        if n_unweighted > 0:
            share = max(remaining // n_unweighted, 1)
            solo_frames = [f if f is not None else share for f in solo_frames]
            # Absorb any rounding leftover into the largest entry.
            drift = total_frames - sum(solo_frames)
        else:
            drift = remaining
        if drift != 0:
            idx = max(range(n), key=lambda i: solo_frames[i])
            solo_frames[idx] += drift

    counts = [solo_frames[0]] + [f + trans_frames for f in solo_frames[1:]]

    if n > 1 and min(counts) <= trans_frames:
        raise SystemExit(
            f"Transition duration is too long for this many images/weights over "
            f"this audio length: the shortest clip would be ~{min(counts) / fps:.2f}s, "
            f"which isn't longer than the {trans_frames / fps:.2f}s transition. "
            f"Use --transition-duration with a smaller value, use fewer images, "
            f"or give short entries a larger explicit weight."
        )
    return counts


def build_xfade_chain(n, clip_frames, trans_frames, fps):
    """Chain N inputs [0:v]..[n-1:v] with xfade transitions using frame-exact
    offsets. Returns (filter_complex_string, final_label)."""
    if n == 1:
        return None, "0:v"

    chain = []
    prev = "0:v"
    cumulative = clip_frames[0]
    for i in range(1, n):
        transition = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
        offset_frames = cumulative - trans_frames
        out_label = f"x{i}"
        chain.append(
            f"[{prev}][{i}:v]xfade=transition={transition}:"
            f"duration={trans_frames / fps:.6f}:offset={offset_frames / fps:.6f}[{out_label}]"
        )
        prev = out_label
        cumulative += clip_frames[i] - trans_frames

    return ";".join(chain), prev


def get_frame_count(path, fps):
    return int(round(get_duration(path) * fps))


def composite_video_chain(inputs, clip_frames, trans_frames, fps, out_path, work_dir, chain_name,
                           audio_path=None, duration=None):
    """Xfade-chain `inputs` (video files) together into out_path. Video-only
    unless audio_path is given, in which case it's muxed in and the output is
    capped to `duration`."""
    n = len(inputs)
    filter_complex, final_label = build_xfade_chain(n, clip_frames, trans_frames, fps)

    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    if audio_path:
        cmd += ["-i", str(audio_path)]
        audio_input_idx = n

    if filter_complex:
        script_path = work_dir / f"{chain_name}_filter_complex.txt"
        script_path.write_text(filter_complex, encoding="utf-8")
        cmd += ["-filter_complex_script", str(script_path), "-map", f"[{final_label}]"]
    else:
        cmd += ["-map", "0:v"]

    if audio_path:
        cmd += ["-map", f"{audio_input_idx}:a", "-t", f"{duration:.3f}"]

    # Pieces/batches can carry slightly different color-range/matrix tags,
    # which makes xfade silently upconvert the blend to yuv444p if the
    # output format isn't pinned -- force standard yuv420p explicitly.
    # Audio is loudness-normalized to YouTube's -14 LUFS target so narration
    # sits at a consistent level across episodes and devices.
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p"]
    if audio_path:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"] if audio_path else ["-an"]
    cmd += [str(out_path)]

    print(f"\nCompositing {chain_name}...")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--images-dir",
                     help="Directory of images (one use each, sorted by filename).")
    ap.add_argument("--manifest",
                     help="Text file, one image path per line, repeats allowed -- "
                          "use instead of --images-dir for explicit ordering/reuse.")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--transition-duration", type=float, default=5.0)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--work-dir", default=None,
                     help="Where to put per-image temp clips (default: <output_dir>/slideshow_pieces)")
    ap.add_argument("--no-color-grade", action="store_true",
                     help="Skip the unified documentary color grade (subtle desaturation + warm tone).")
    ap.add_argument("--no-vignette", action="store_true",
                     help="Skip the soft cinematic vignette.")
    ap.add_argument("--no-film-grain", action="store_true",
                     help="Skip the subtle film grain finish.")
    ap.add_argument("--batch-size", type=int, default=10,
                     help="Composite pieces in batches of this size, chaining the batch outputs "
                          "together at the end, instead of one giant crossfade chain (default 10). "
                          "A single flat chain gets dramatically slower as input count grows "
                          "(many simultaneous open decoders) -- batching keeps each chain small.")
    args = ap.parse_args()

    if not args.images_dir and not args.manifest:
        raise SystemExit("Provide either --images-dir or --manifest.")
    entries = load_manifest(args.manifest) if args.manifest else list_images(args.images_dir)
    images = [p for p, _, _ in entries]
    weights = [w for _, w, _ in entries]
    options = [o for _, _, o in entries]
    n = len(images)
    audio_dur = get_duration(args.audio)
    fps = args.fps
    trans_frames = int(round(args.transition_duration * fps)) if n > 1 else 0
    total_frames = int(round(audio_dur * fps))

    clip_frames = compute_clip_frame_counts(weights, total_frames, trans_frames, fps)
    print(f"{n} image(s), audio {audio_dur:.2f}s ({total_frames} frames @ {fps}fps) -> "
          f"clip lengths (frames): {clip_frames}, transition {trans_frames} frames")

    out_path = Path(args.output)
    work_dir = Path(args.work_dir) if args.work_dir else out_path.parent / "slideshow_pieces"
    work_dir.mkdir(parents=True, exist_ok=True)

    piece_paths = []
    for i, img in enumerate(images):
        piece = work_dir / f"piece_{i:03d}.mp4"
        piece_paths.append(piece)
        if is_video(img):
            start_offset = options[i].get("start", 0.0)
            crop_bottom = options[i].get("crop_bottom", 0.0)
            print(f"[{i+1}/{n}] rendering {img.name} (video clip, start={start_offset}s, "
                  f"{clip_frames[i]} frames) -> {piece.name}")
            render_video_clip(img, clip_frames[i], args.width, args.height, fps, piece,
                               color_grade=not args.no_color_grade,
                               start_offset=start_offset, crop_bottom_frac=crop_bottom,
                               vignette=not args.no_vignette,
                               film_grain=not args.no_film_grain)
        else:
            print(f"[{i+1}/{n}] rendering {img.name} (effect {i % len(EFFECTS)}, "
                  f"{clip_frames[i]} frames) -> {piece.name}")
            render_image_clip(img, clip_frames[i], i, args.width, args.height, fps, piece,
                               color_grade=not args.no_color_grade,
                               vignette=not args.no_vignette,
                               film_grain=not args.no_film_grain)

    if n <= args.batch_size:
        composite_video_chain(piece_paths, clip_frames, trans_frames, fps, out_path, work_dir,
                               "final", audio_path=args.audio, duration=audio_dur)
    else:
        # A single flat chain gets dramatically slower as input count grows --
        # many simultaneous open decoders, not just more filter math. Composite
        # in small batches first (each batch's own internal xfade chain), then
        # chain the batch OUTPUTS together at the top level. The additive
        # transition-padding already baked into clip_frames by
        # compute_clip_frame_counts (every piece except the global first one
        # is padded +trans_frames) is exactly the padding a batch boundary
        # needs too -- so batches can be sliced straight out of the flat
        # clip_frames list with no separate math, and each batch's rendered
        # output naturally already has the right amount of extra tail content
        # for the top-level crossfade into the next batch.
        batch_paths, batch_frame_counts = [], []
        for b, start in enumerate(range(0, n, args.batch_size)):
            batch_pieces = piece_paths[start:start + args.batch_size]
            batch_clip_frames = clip_frames[start:start + args.batch_size]
            batch_out = work_dir / f"batch_{b:03d}.mp4"
            print(f"\n=== Batch {b} ({len(batch_pieces)} pieces) ===")
            composite_video_chain(batch_pieces, batch_clip_frames, trans_frames, fps, batch_out,
                                   work_dir, f"batch_{b:03d}")
            batch_paths.append(batch_out)
            batch_frame_counts.append(get_frame_count(batch_out, fps))

        print(f"\n=== Final: chaining {len(batch_paths)} batches together ===")
        composite_video_chain(batch_paths, batch_frame_counts, trans_frames, fps, out_path,
                               work_dir, "final", audio_path=args.audio, duration=audio_dur)

    print(f"\nDone -> {out_path}")


if __name__ == "__main__":
    main()
