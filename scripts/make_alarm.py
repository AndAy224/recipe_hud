"""Generate the alarm sound (three ascending beeps, ~1.6s loop) as WAV files
for the extension and the launcher fallback. Stdlib only; run once, commit
the outputs."""

import math
import struct
import wave
from pathlib import Path

RATE = 22050
REPO = Path(__file__).resolve().parents[1]
OUTPUTS = [
    REPO / "extension" / "assets" / "alarm.wav",
    REPO / "frontend" / "shared" / "alarm.wav",
]


def tone(freq: float, duration: float, volume: float = 0.55) -> list[float]:
    n = int(RATE * duration)
    fade = int(RATE * 0.012)
    samples = []
    for i in range(n):
        env = min(1.0, i / fade, (n - i) / fade)
        samples.append(volume * env * math.sin(2 * math.pi * freq * i / RATE))
    return samples


def silence(duration: float) -> list[float]:
    return [0.0] * int(RATE * duration)


def main() -> None:
    pattern = (
        tone(880, 0.14) + silence(0.09)
        + tone(1100, 0.14) + silence(0.09)
        + tone(1320, 0.2) + silence(0.9)
    )
    frames = b"".join(struct.pack("<h", int(s * 32767)) for s in pattern)
    for path in OUTPUTS:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(frames)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
