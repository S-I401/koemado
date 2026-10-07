"""faster-whisper を OpenAI 互換の /v1/audio/transcriptions で公開する最小サーバー。

環境変数:
  STT_MODEL   モデル名（tiny / base / small / medium / large-v3 など）。既定 small
  STT_DEVICE  cpu / cuda。既定 cpu
  STT_COMPUTE int8 / float16 など。既定 int8
"""

import io
import os
import time
import wave

import numpy as np
from fastapi import FastAPI, File, Form, UploadFile
from faster_whisper import WhisperModel

MODEL_NAME = os.getenv("STT_MODEL", "small")
DEVICE = os.getenv("STT_DEVICE", "cpu")
COMPUTE = os.getenv("STT_COMPUTE", "int8")
CPU_THREADS = int(os.getenv("STT_CPU_THREADS", "8"))

def read_wav(data: bytes) -> np.ndarray:
    """PCM16 WAV を 16kHz モノラル float32 にする（PyAV を経由しない）"""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"16bit PCM WAV only (sample width: {width})")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype(np.float32)
    return audio


model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE, cpu_threads=CPU_THREADS)
app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME, "device": DEVICE}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]}


@app.post("/v1/audio/transcriptions")
def transcriptions(
    file: UploadFile = File(...),
    model_name: str = Form(None, alias="model"),
    language: str = Form("ja"),
):
    # model フィールドは受け取るだけ（起動時に読み込んだモデルを使う）
    started = time.perf_counter()
    segments, _ = model.transcribe(
        read_wav(file.file.read()),
        language=(language or "ja").split("-")[0],
        beam_size=1,
        vad_filter=False,   # VAD は AIAvatarKit 側で済んでいる
        condition_on_previous_text=False,
    )
    text = "".join(s.text for s in segments).strip()
    print(f"[stt] {time.perf_counter() - started:.2f}s: {text}", flush=True)
    return {"text": text}
