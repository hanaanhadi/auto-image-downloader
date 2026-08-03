#!/usr/bin/env python3
"""
End-to-end episode pipeline orchestrator. Given an audio narration file and
an asset URL list, runs every step sequentially into a dedicated folder for
that episode, printing live progress from each stage.

Pipeline:
  1. Set up episodes/<name>/ folder, copy in the audio
  2. Denoise the audio, loudness-matched back to the original level (denoise_audio.py)
  3. Download assets from the URL list -- images/scrape pages first, then
     videos, to go easier on source sites (download_assets.py)
  -- PAUSES HERE for you to review episodes/<name>/images_pool/: delete
     anything that doesn't belong, add anything you want included. --
  4. Transcribe the (denoised, narration-only) audio (transcribe_audio.py)
     -> transcript.json/.srt
  5. Mix in background music if --music is given (mix_music.py), sidechain-
     ducked under the narration -- done AFTER transcription so Whisper reads
     clean narration, not narration+music
  6. Auto-build an image rotation manifest (auto_manifest.py)
     -- NOTE: this is a simple round-robin, not content-aware placement.
        Content-aware placement (matching images to what's being said) is a
        manual/assisted step -- ask directly in conversation for that.
  7. Build the slideshow (make_slideshow.py) -- documentary color grade +
     soft crossfades by default
  8. Burn in captions + opening title card + SUBSCRIBE pop-up (add_subscribe_and_subs.py)

USAGE:
    First pass (setup, denoise, download), then stops for your review:
        python run_episode.py --episode-name NAME --audio FILE --asset-list FILE
            [--min-size 20] [--noise-reduction 12] [--skip-denoise]

    After reviewing episodes/NAME/images_pool/, continue with:
        python run_episode.py --episode-name NAME --resume
            [--transition-duration 5] [--width 1920] [--height 1080]
            [--caption-fontsize 20] [--subscribe-duration 10] [--target-slot-seconds 27]
            [--title "Episode Title"] [--music FILE] [--music-volume -22]
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
PY = sys.executable

# Without this, stdout is fully buffered (not line-buffered) whenever it's
# piped/redirected rather than an interactive terminal -- meaning our own
# progress prints would sit invisible in a buffer until the whole script
# exits, arriving all at once instead of live. Force line buffering so the
# step banners actually show up as each stage starts, not after the fact.
sys.stdout.reconfigure(line_buffering=True)


def run_step(step_num, total, title, cmd):
    print(f"\n{'=' * 70}")
    print(f"[{step_num}/{total}] {title}")
    print(f"{'=' * 70}")
    print("$ " + " ".join(str(c) for c in cmd))
    start = time.time()
    result = subprocess.run(cmd)
    elapsed = time.time() - start
    if result.returncode != 0:
        print(f"\n!! Step {step_num} ('{title}') failed with exit code {result.returncode} "
              f"after {elapsed:.1f}s. Stopping pipeline.")
        sys.exit(result.returncode)
    print(f"-- Step {step_num} done in {elapsed:.1f}s --")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episode-name", required=True)
    ap.add_argument("--audio", help="Required unless --resume.")
    ap.add_argument("--asset-list", help="Required unless --resume.")
    ap.add_argument("--resume", action="store_true",
                     help="Skip setup/denoise/download and continue from the review "
                          "checkpoint using whatever's currently in images_pool/.")
    ap.add_argument("--min-size", type=int, default=20)
    ap.add_argument("--transition-duration", type=float, default=5.0)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--caption-fontsize", type=int, default=20)
    ap.add_argument("--subscribe-duration", type=float, default=10.0)
    ap.add_argument("--subscribe-count", type=int, default=4)
    ap.add_argument("--target-slot-seconds", type=float, default=27.0)
    ap.add_argument("--noise-reduction", type=float, default=12.0,
                     help="Denoise strength in dB (default 12, moderate). See denoise_audio.py.")
    ap.add_argument("--skip-denoise", action="store_true",
                     help="Use the audio as-is, skipping noise reduction.")
    ap.add_argument("--title", default=None,
                     help="Opening title card text. Omit for no title card.")
    ap.add_argument("--music", default=None,
                     help="Background music track. Omit for narration-only audio.")
    ap.add_argument("--music-volume", type=float, default=-22.0,
                     help="Music level in dB, before ducking (default -22, quiet).")
    ap.add_argument("--no-duck", action="store_true",
                     help="Mix music at a constant level instead of ducking under narration.")
    args = ap.parse_args()

    episode_dir = ROOT / "episodes" / args.episode_name
    episode_dir.mkdir(parents=True, exist_ok=True)
    print(f"Episode folder: {episode_dir}")

    total_steps = 8
    audio_dest = episode_dir / "audio.m4a"
    images_pool_dir = episode_dir / "images_pool"

    if args.resume:
        if not audio_dest.exists():
            raise SystemExit(f"--resume given but {audio_dest} doesn't exist. "
                              f"Run the first pass (--audio/--asset-list) first.")
        if not images_pool_dir.exists() or not any(images_pool_dir.iterdir()):
            raise SystemExit(f"--resume given but {images_pool_dir} is empty. "
                              f"Run the first pass first.")
        collected = sum(1 for _ in images_pool_dir.iterdir())
        print(f"Resuming with {collected} images already in {images_pool_dir}")
    else:
        if not args.audio or not args.asset_list:
            raise SystemExit("--audio and --asset-list are required unless --resume is given.")

        # Step 1: copy audio in
        print(f"\n{'=' * 70}\n[1/{total_steps}] Setting up episode folder\n{'=' * 70}")
        audio_raw = episode_dir / "audio_raw.mp3"
        shutil.copyfile(args.audio, audio_raw)
        print(f"Copied audio -> {audio_raw}")

        # Step 2: denoise, loudness-matched back to the original
        if args.skip_denoise:
            print(f"\n{'=' * 70}\n[2/{total_steps}] Denoising audio (skipped)\n{'=' * 70}")
            shutil.copyfile(audio_raw, audio_dest)
        else:
            run_step(2, total_steps, "Denoising audio (noise reduction + loudness match)", [
                PY, str(ROOT / "denoise_audio.py"),
                "--input", str(audio_raw), "--output", str(audio_dest),
                "--noise-reduction", str(args.noise_reduction),
            ])

        # Step 3: download assets
        downloads_dir = episode_dir / "downloads"
        run_step(3, total_steps, "Downloading assets (images first, then videos)", [
            PY, str(ROOT / "download_assets.py"), str(Path(args.asset_list).resolve()),
            "--topic", args.episode_name, "--output", str(downloads_dir),
            "--min-size", str(args.min_size),
        ])

        # Collect downloaded images into a flat review folder
        topic_root = downloads_dir / args.episode_name
        images_pool_dir.mkdir(exist_ok=True)
        collected = 0
        for img in topic_root.rglob("*"):
            if img.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
                shutil.copyfile(img, images_pool_dir / f"{collected:04d}_{img.name}")
                collected += 1
        if collected == 0:
            print("!! No images were downloaded -- can't build an image-slider video. "
                  "Check the asset list / download report and try again.")
            sys.exit(1)

        print(f"\n{'=' * 70}")
        print(f"PAUSED FOR REVIEW: {collected} images downloaded to:")
        print(f"  {images_pool_dir}")
        print("Delete anything that doesn't belong, add anything you want included.")
        print("When ready, continue with:")
        print(f'  python run_episode.py --episode-name "{args.episode_name}" --resume')
        print(f"{'=' * 70}")
        return

    # Step 4: transcribe (narration-only audio, for clean Whisper accuracy)
    run_step(4, total_steps, "Transcribing audio", [
        PY, str(ROOT / "transcribe_audio.py"),
        "--audio", str(audio_dest), "--out-dir", str(episode_dir),
    ])

    # Step 5: mix in background music (if given), AFTER transcription
    audio_for_video = audio_dest
    if args.music:
        music_mixed = episode_dir / "audio_with_music.m4a"
        cmd = [
            PY, str(ROOT / "mix_music.py"),
            "--narration", str(audio_dest), "--music", str(Path(args.music).resolve()),
            "--output", str(music_mixed), "--music-volume", str(args.music_volume),
        ]
        if args.no_duck:
            cmd.append("--no-duck")
        run_step(5, total_steps, "Mixing background music", cmd)
        audio_for_video = music_mixed
    else:
        print(f"\n{'=' * 70}\n[5/{total_steps}] Mixing background music (skipped, no --music given)\n{'=' * 70}")

    # Step 6: auto-build manifest from the (now human-reviewed) image pool
    print(f"\n{'=' * 70}\n[6/{total_steps}] Building image manifest ({collected} images found)\n{'=' * 70}")
    manifest_path = episode_dir / "slideshow_manifest.txt"
    run_step(6, total_steps, "Building image manifest", [
        PY, str(ROOT / "auto_manifest.py"),
        "--images-dir", str(images_pool_dir), "--audio", str(audio_dest),
        "--output", str(manifest_path),
        "--target-slot-seconds", str(args.target_slot_seconds),
    ])

    # Step 7: build slideshow (uses the music-mixed audio if present, so the
    # final video's soundtrack includes it; manifest timing above still keys
    # off the narration-only file, but durations are identical either way)
    base_video = episode_dir / f"{args.episode_name}_base.mp4"
    pieces_dir = episode_dir / "slideshow_pieces"
    run_step(7, total_steps, "Building slideshow video", [
        PY, str(ROOT / "make_slideshow.py"),
        "--manifest", str(manifest_path), "--audio", str(audio_for_video),
        "--output", str(base_video), "--work-dir", str(pieces_dir),
        "--transition-duration", str(args.transition_duration),
        "--width", str(args.width), "--height", str(args.height),
    ])

    # Step 8: captions + title card + subscribe
    final_video = episode_dir / f"{args.episode_name}_final.mp4"
    cmd = [
        PY, str(ROOT / "add_subscribe_and_subs.py"),
        "--video", str(base_video), "--output", str(final_video),
        "--srt", str(episode_dir / "transcript.srt"),
        "--caption-fontsize", str(args.caption_fontsize),
        "--subscribe-duration", str(args.subscribe_duration),
        "--subscribe-count", str(args.subscribe_count),
    ]
    if args.title:
        cmd += ["--title", args.title]
    run_step(8, total_steps, "Adding captions + title card + subscribe pop-ups", cmd)

    print(f"\n{'=' * 70}")
    print(f"DONE -> {final_video}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
