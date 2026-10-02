#!/usr/bin/env python3
"""
Reusable transcription step: runs faster-whisper on an audio file and writes:
  - transcript.json / transcript.srt   -- Whisper's own sentence-level segments
                                           (useful for reading/context when
                                           building a manifest)
  - captions.srt                        -- short, punchy re-chunked captions
                                           for burning into the video. Whisper's
                                           raw segments sometimes bundle 2-3
                                           sentences into one block, which reads
                                           as a wall of text on screen; this
                                           re-chunks from word-level timestamps
                                           into a few words at a time instead,
                                           the standard documentary caption style.

USAGE:
    python transcribe_audio.py --audio FILE --out-dir DIR [--model small]
        [--caption-max-words 6] [--caption-max-duration 3.2]
"""

import argparse
import json
from pathlib import Path

from faster_whisper import WhisperModel


def to_srt_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(entries, path):
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{to_srt_timestamp(e['start'])} --> {to_srt_timestamp(e['end'])}")
        lines.append(e["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_short_captions(words, max_words=6, max_chars=34, max_duration=3.2, min_words=2):
    """Re-chunk word-level timestamps into short caption bursts instead of
    whole-sentence blocks: break at sentence-ending punctuation when the
    chunk is already a reasonable length, otherwise cap by word count,
    character count, or duration -- whichever comes first."""
    chunks = []
    current = []

    def flush():
        if current:
            chunks.append({
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": " ".join(w["word"].strip() for w in current),
            })

    for w in words:
        current.append(w)
        text_so_far = " ".join(x["word"].strip() for x in current)
        duration_so_far = current[-1]["end"] - current[0]["start"]
        ends_sentence = w["word"].strip().endswith((".", "!", "?"))
        at_max = (len(current) >= max_words or len(text_so_far) >= max_chars
                  or duration_so_far >= max_duration)
        if (ends_sentence and len(current) >= min_words) or at_max:
            flush()
            current = []
    flush()

    # Don't let a lone trailing word flash by itself -- fold it back in.
    if len(chunks) >= 2 and len(chunks[-1]["text"].split()) == 1:
        last = chunks.pop()
        chunks[-1]["end"] = last["end"]
        chunks[-1]["text"] += " " + last["text"]

    return chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--model", default="small", help="faster-whisper model size (default: small)")
    ap.add_argument("--caption-max-words", type=int, default=6,
                     help="Max words per burned-in caption chunk (default 6).")
    ap.add_argument("--caption-max-duration", type=float, default=3.2,
                     help="Max seconds a single caption chunk stays on screen (default 3.2).")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading faster-whisper model '{args.model}'...")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    print(f"Transcribing {args.audio} ...")
    segments, info = model.transcribe(args.audio, beam_size=5, word_timestamps=True)
    print(f"Detected language: {info.language} (probability {info.language_probability:.2f})")
    print(f"Duration: {info.duration:.1f}s")

    results = []
    all_words = []
    for seg in segments:
        entry = {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()}
        results.append(entry)
        print(f"[{entry['start']:.2f} - {entry['end']:.2f}] {entry['text']}")
        for w in (seg.words or []):
            all_words.append({"word": w.word, "start": w.start, "end": w.end})

    json_path = out_dir / "transcript.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    srt_path = out_dir / "transcript.srt"
    write_srt(results, srt_path)

    captions = build_short_captions(all_words, max_words=args.caption_max_words,
                                     max_duration=args.caption_max_duration)
    captions_path = out_dir / "captions.srt"
    write_srt(captions, captions_path)

    # Raw word-level timestamps, kept separately from the grouped caption
    # chunks above -- lets a downstream step (e.g. build_word_captions.py)
    # build a different caption style later (word-by-word reveal, karaoke
    # highlighting, etc.) without re-running Whisper.
    words_path = out_dir / "words.json"
    with open(words_path, "w", encoding="utf-8") as f:
        json.dump(all_words, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(results)} segments -> {json_path.name}, {srt_path.name}")
    print(f"Saved {len(captions)} short caption chunks -> {captions_path.name}")
    print(f"Saved {len(all_words)} word timestamps -> {words_path.name}")


if __name__ == "__main__":
    main()
