import sys
import json
from faster_whisper import WhisperModel

audio_path = "port chicago audio.mp3"
model = WhisperModel("small", device="cpu", compute_type="int8")

segments, info = model.transcribe(audio_path, beam_size=5, word_timestamps=False)

print(f"Detected language: {info.language} (probability {info.language_probability:.2f})")
print(f"Duration: {info.duration:.1f}s")

results = []
for seg in segments:
    entry = {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()}
    results.append(entry)
    print(f"[{entry['start']:.2f} - {entry['end']:.2f}] {entry['text']}")

with open("transcript.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print(f"\nSaved {len(results)} segments to transcript.json")
