import json
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parents[1]
settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))

audio = settings.get("audio", {})
device = audio.get("input_device", None)
samplerate = audio.get("sample_rate_hz", 16000)
channels = audio.get("channels", 1)

print("Mic level test")
print("  input_device:", device)
print("  samplerate:", samplerate)
print("  channels:", channels)
print("Speak normally. You should see RMS jump when you talk.\n")

last_print = 0.0

def callback(indata, frames, time_info, status):
    global last_print
    if status:
        # Important: shows underruns / device issues
        print("STATUS:", status)

    # indata is float32 in [-1, 1] from sounddevice by default
    x = indata[:, 0]
    rms = float(np.sqrt(np.mean(x * x)))

    now = time.time()
    if now - last_print > 0.25:
        last_print = now
        bar = "#" * min(60, int(rms * 200))
        print(f"rms={rms:.6f} {bar}")

with sd.InputStream(device=device, samplerate=samplerate, channels=channels, callback=callback):
    while True:
        time.sleep(1)
