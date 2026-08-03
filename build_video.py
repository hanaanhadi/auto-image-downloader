#!/usr/bin/env python3
"""
Builds the Port Chicago 50 video: a hand-mapped edit-decision-list syncing
downloaded video/photo assets to the narration transcript, rendered with
ffmpeg (per-piece encode -> concat -> mux against the original audio).
"""

import subprocess
import os
from pathlib import Path

ROOT = Path(__file__).parent
MEDIA = ROOT / "downloads/port_chicago_50_assets/media"
SCRAPED = ROOT / "downloads/port_chicago_50_assets/scraped/www.history.navy.mil_browse-by-topic_wars-conflicts-and-operations_world-war-ii_"
RENDER = ROOT / "render"
RENDER.mkdir(exist_ok=True)

AUDIO = ROOT / "port chicago audio.mp3"
OUTPUT = ROOT / "Port_Chicago_50_final.mp4"

W, H, FPS = 1280, 720, 30

V1 = MEDIA / "NPC-4814.mp4"                                            # 1944 day-after film
V2 = MEDIA / "NPC-5193.mp4"                                            # 1944 damaged-town film
V3 = MEDIA / "66df21f5-beca-4f24-bb4e-f5884ffc3340720p.mp4"            # modern memorial B-roll
V4 = MEDIA / "17c1804f-df2f-4d9f-809e-90f2226eaddc720p.mp4"            # modern memorial B-roll 2
V5 = MEDIA / "4e490974-9c9a-4b03-895f-55e022278f98720p.mp4"            # 2024 80th anniversary ceremony

P1 = SCRAPED / "1559234892965.jpg"   # damaged barracks
P2 = SCRAPED / "1559247980008.jpg"   # loading ship at dock
P3 = SCRAPED / "1559248125295.jpg"   # loading railcar via ramp
P4 = SCRAPED / "1559580307441.jpg"   # ship/tonnage diagram
P5 = SCRAPED / "1559579003914.jpg"   # wreckage w/ buried car
P6 = SCRAPED / "1559579098838.jpg"   # wreckage debris field wide

# (type, source, in_point, duration)
PIECES = [
    ("video", V2, 0.00, 14.80),
    ("photo", P6, None, 10.00),
    ("video", V2, 14.80, 15.20),
    ("photo", P1, None, 10.00),
    ("video", V2, 30.00, 18.80),
    ("video", V1, 0.00, 49.52),
    ("photo", P2, None, 11.52),
    ("video", V1, 49.52, 33.92),
    ("photo", P3, None, 12.64),
    ("video", V1, 83.44, 98.80),
    ("video", V2, 48.80, 7.28),
    ("photo", P4, None, 16.72),
    ("video", V2, 56.08, 75.60),
    ("photo", P5, None, 13.76),
    ("video", V2, 131.68, 92.24),
    ("video", V1, 182.24, 140.32),
    ("video", V4, 0.00, 170.88),
    ("video", V3, 0.00, 456.52),
    ("video", V4, 170.88, 23.08),
    ("video", V4, 193.96, 237.92),
    ("video", V5, 0.00, 112.08),
    ("video", V5, 112.08, 53.59),
    ("video", V1, 322.56, 28.49),
]


def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def render_video_piece(src, in_point, duration, out_path):
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS}")
    run([
        "ffmpeg", "-y", "-ss", f"{in_point:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
        "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", str(out_path),
    ])


def render_photo_piece(src, duration, out_path):
    frames = int(round(duration * FPS))
    vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
          f"crop={W*2}:{H*2},"
          f"zoompan=z='min(zoom+0.0006,1.15)':d={frames}:s={W}x{H}:fps={FPS},setsar=1")
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(src), "-t", f"{duration:.3f}",
        "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", str(out_path),
    ])


def main():
    total = sum(p[3] for p in PIECES)
    print(f"Total pieces: {len(PIECES)}  |  Total duration: {total:.2f}s")

    piece_files = []
    for i, (kind, src, in_point, duration) in enumerate(PIECES, 1):
        out_path = RENDER / f"piece_{i:03d}.mp4"
        piece_files.append(out_path)
        print(f"\n[{i}/{len(PIECES)}] {kind} {src.name} dur={duration:.2f}s")
        if out_path.exists() and out_path.stat().st_size > 0:
            print("  (already rendered, skipping)")
            continue
        if kind == "video":
            render_video_piece(src, in_point, duration, out_path)
        else:
            render_photo_piece(src, duration, out_path)

    list_path = RENDER / "concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for p in piece_files:
            f.write(f"file '{p.resolve()}'\n")

    silent_path = RENDER / "silent_full.mp4"
    print("\nConcatenating pieces...")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
         "-c", "copy", str(silent_path)])

    print("\nMuxing with audio...")
    run(["ffmpeg", "-y", "-i", str(silent_path), "-i", str(AUDIO),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", str(OUTPUT)])

    print(f"\nDone: {OUTPUT}")


if __name__ == "__main__":
    main()
