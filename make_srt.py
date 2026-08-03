import json
from pathlib import Path

ROOT = Path(__file__).parent
data = json.loads((ROOT / "transcript.json").read_text(encoding="utf-8"))


def ts(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


lines = []
for i, seg in enumerate(data, 1):
    lines.append(str(i))
    lines.append(f"{ts(seg['start'])} --> {ts(seg['end'])}")
    lines.append(seg["text"])
    lines.append("")

(ROOT / "transcript.srt").write_text("\n".join(lines), encoding="utf-8")
print(f"Wrote transcript.srt with {len(data)} cues")
