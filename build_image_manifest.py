#!/usr/bin/env python3
"""Builds an ordered image manifest for make_slideshow.py, placing the 6
real archival photos contextually across the narrative and reusing them at
roughly 20-35s intervals (documentary-style, since only 6 unique photos
exist for a 28-minute piece)."""

from pathlib import Path

ROOT = Path(__file__).parent
SCRAPED = ROOT / "downloads/port_chicago_50_assets/scraped/www.history.navy.mil_browse-by-topic_wars-conflicts-and-operations_world-war-ii_"

P1 = SCRAPED / "1559234892965.jpg"   # damaged barracks
P2 = SCRAPED / "1559247980008.jpg"   # loading ship at dock
P3 = SCRAPED / "1559248125295.jpg"   # loading railcar via ramp
P4 = SCRAPED / "1559580307441.jpg"   # ship/tonnage diagram
P5 = SCRAPED / "1559579003914.jpg"   # wreckage w/ buried car
P6 = SCRAPED / "1559579098838.jpg"   # wreckage debris field wide

AUDIO_DURATION = 1703.68  # seconds, from earlier transcription

# (segment_duration_seconds, rotation_list) -- narrative order matches the
# transcript's actual structure (hook / setup / explosion / cleanup /
# mutiny / trial / cover-up-appeal / legacy / closing).
SEGMENTS = [
    (69.84,  [P6, P1]),                      # hook: explosion & deaths
    (205.36, [P2, P3]),                      # setup: segregated navy, loading work
    (205.60, [P4, P5, P6]),                  # the explosion itself
    (140.32, [P1, P6, P5]),                  # cleanup aftermath
    (170.88, [P3, P2, P6]),                  # the mutiny refusal
    (479.60, [P4, P2, P3, P6, P1, P5]),      # the trial
    (237.92, [P2, P4, P6]),                  # cover-up / NAACP / appeal
    (112.08, [P6, P1]),                      # legacy / 2024 exoneration
    (82.08,  [P4, P6]),                      # closing / CTA
]

TARGET_SLOT_SECONDS = 27.0


def main():
    total = sum(d for d, _ in SEGMENTS)
    print(f"Segment total: {total:.2f}s vs audio {AUDIO_DURATION}s")

    manifest = []
    for seg_dur, rotation in SEGMENTS:
        n_slots = max(round(seg_dur / TARGET_SLOT_SECONDS), 1)
        for i in range(n_slots):
            manifest.append(rotation[i % len(rotation)])

    print(f"Total slots: {len(manifest)}  (avg {AUDIO_DURATION/len(manifest):.1f}s/slot)")

    out_path = ROOT / "slideshow_manifest.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        for img in manifest:
            f.write(str(img.resolve()) + "\n")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
