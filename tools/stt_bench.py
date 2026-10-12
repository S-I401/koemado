"""音声認識（STT）の比較計測（開発用）。TTS で作った日本語の音声を、各エンジン・モデル・デバイスで認識して、CER と 1 発話あたりの時間を出す。

  <インストール先>/app/.venv/Scripts/python.exe tools/stt_bench.py synth     # TTS（起動中）で test_stt/*.wav を作る（GPU を使う。ffmpeg が必要）
  <インストール先>/app/.venv/Scripts/python.exe tools/stt_bench.py run       # 各構成を起動して認識し、表を出す（run/ の STT とは別のポートで起動する）
  オプション: --only parakeet-gpu,qwen17-gpu  --repeat 3  --dir <WAV のフォルダ>  --hints "仙台、ミオ"

  - 正解は SENTENCES の文（TTS に入れた文そのもの）。TTS の発音の癖で読み間違いがあっても、その分は誤りに数えられる。
  - CER は、空白と句読点を除き、NFKC で正規化した文字列の編集距離 ÷ 正解の長さ。CER* は、さらに漢数字を算用数字にそろえる（七時 = 7時）。
  - 読み CER は、正解と認識結果の両方を pykakasi でひらがなにしてから比べる（かな書きで出すモデルが、漢字の書き分けで不利にならないように。pip install pykakasi が必要。無ければ出さない）。
  - 時間は、クライアント側で測った POST から応答までの秒数（初回の 1 発話は、GPU のウォームアップを含むので別に出す）。
"""
import argparse
import io
import json
import re
import statistics
import subprocess
import sys
import time
import unicodedata
import urllib.request
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import stt_engine  # noqa: E402

# (声, 文)。会話らしい短い文に、固有名詞・数字・英語を含める。声は高さや話し方が違うものを混ぜる
SENTENCES = [
    ("mio_bright", "今日は仙台で、ミオと一緒に散歩をしました。"),
    ("mio_bright", "Hugging Faceからモデルをダウンロードしたいんだけど、どうすればいいかな。"),
    ("mio_bright", "明日の朝七時に起こしてください。"),
    ("mio_bright", "この前買ったパソコンは、メモリが三十二ギガバイトあります。"),
    ("mio_bright", "ありがとう、とても助かりました。"),
    ("mio_bright", "来週の水曜日に、東京駅で友達と待ち合わせをしています。"),
    ("mio_bright", "今夜の晩ご飯は、カレーライスにしようと思います。"),
    ("mio_bright", "それは本当ですか。ちょっと信じられません。"),
    ("mio_bright", "ミオさん、おはようございます。今日の天気はどうですか。"),
    ("mio_bright", "千二百円のお弁当を二つ買って、公園で食べました。"),
    ("mio_bright", "最近、音声認識の精度が上がって、とても便利になりましたね。"),
    ("mio_bright", "Pythonでコードを書いて、ローカルのLLMで動かしています。"),
]

# 構成名 -> (表示名, エンジン, モデル, デバイス)
CONFIGS = {
    "fw-cpu": ("faster-whisper small（CPU）", "faster-whisper", "small", "cpu"),
    "parakeet-gpu": ("speech.cpp parakeet-ja（GPU）", "speechcpp", "parakeet-ja", "gpu"),
    "parakeet-cpu": ("speech.cpp parakeet-ja（CPU）", "speechcpp", "parakeet-ja", "cpu"),
    "qwen06-gpu": ("speech.cpp qwen3-asr-0.6b（GPU）", "speechcpp", "qwen3-asr-0.6b", "gpu"),
    "qwen17-gpu": ("speech.cpp qwen3-asr-1.7b（GPU）", "speechcpp", "qwen3-asr-1.7b", "gpu"),
    "qwen06-cpu": ("speech.cpp qwen3-asr-0.6b（CPU）", "speechcpp", "qwen3-asr-0.6b", "cpu"),
    "qwen17-cpu": ("speech.cpp qwen3-asr-1.7b（CPU）", "speechcpp", "qwen3-asr-1.7b", "cpu"),
    "qwen17-gpu-hint": ("speech.cpp qwen3-asr-1.7b（GPU、固有名詞のヒントあり）", "speechcpp", "qwen3-asr-1.7b", "gpu"),
}


# ---------------------------------------------------------------- CER ----

_KD = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KU = {"十": 10, "百": 100, "千": 1000}


def _kanji_num(s: str) -> str:
    total, cur = 0, 0
    for ch in s:
        if ch in _KD:
            cur = cur * 10 + _KD[ch]
        else:
            total += (cur or 1) * _KU[ch]
            cur = 0
    return str(total + cur)


def unify_numbers(text: str) -> str:
    return re.sub(r"[〇一二三四五六七八九十百千]+", lambda m: _kanji_num(m.group(0)), text)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(ch for ch in t if not (ch.isspace() or unicodedata.category(ch)[0] in "PS"))


def to_reading(text: str) -> str | None:
    """漢字をひらがなの読みにそろえる（pykakasi があるときだけ。無ければ None）。パラキートのように、かな書きで出力するモデルを、表記ではなく読みで比べるため"""
    try:
        import pykakasi
    except ImportError:
        return None
    t = unify_numbers(normalize(text))
    return "".join(x["hira"] for x in pykakasi.kakasi().convert(t))


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str, numbers: bool = False) -> float:
    r, h = normalize(ref), normalize(hyp)
    if numbers:
        r, h = unify_numbers(r), unify_numbers(h)
    return edit_distance(r, h) / max(1, len(r))


# ---------------------------------------------------------------- HTTP ----

def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    for k, (name, data, ctype) in files.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode("utf-8"))
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def transcribe(port: int, wav: bytes, prompt: str = "", model: str = "") -> tuple[str, float]:
    fields = {"language": "ja"}
    if prompt:
        fields["prompt"] = prompt
    if model:
        fields["model"] = model
    body, ctype = multipart(fields, {"file": ("voice.wav", wav, "audio/wav")})
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/audio/transcriptions", data=body, headers={"Content-Type": ctype})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        text = json.loads(r.read().decode("utf-8"))["text"]
    return text, time.perf_counter() - t0


def wav_info(data: bytes) -> tuple[float, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnframes() / w.getframerate(), w.getframerate()


# ---------------------------------------------------------------- synth ----

def cmd_synth(args):
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    url = f"http://127.0.0.1:{cfg['ports']['tts']}/v1/audio/speech"
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, (voice, text) in enumerate(SENTENCES, 1):
        body = json.dumps({"model": "irodori-tts", "input": text, "voice": voice, "response_format": "wav",
                           "irodori": {"num_steps": 24, "seed": 42}}).encode("utf-8")
        t0 = time.perf_counter()
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=300) as r:
            data = r.read()
        dur, rate = wav_info(data)
        name = f"s{i:02d}_{voice}.wav"
        (out / "raw").mkdir(exist_ok=True)
        (out / "raw" / name).write_bytes(data)
        # AIAvatarKit のブラウザ / キャラが送る形（16kHz・モノラル・16bit）にそろえる（ffmpeg で正しくリサンプルする）
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / "raw" / name), "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(out / name)], check=True)
        manifest.append({"file": name, "voice": voice, "text": text, "seconds": round(dur, 2), "rate": rate})
        print(f"{name}: {dur:.1f}s {rate}Hz（合成 {time.perf_counter() - t0:.1f}s） {text}")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- run ----

def start_server(key: str, port: int):
    name, engine, model, device = CONFIGS[key]
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    cfg = {**cfg, "ports": {**cfg["ports"], "stt": port}}
    if engine == "faster-whisper":
        import os
        env = dict(os.environ, STT_MODEL=cfg["stt"]["model"], STT_DEVICE="cpu", STT_COMPUTE="int8", STT_CPU_THREADS=str(cfg["stt"]["cpu_threads"]), PYTHONUTF8="1",
                   HF_HOME=str(ROOT / "hf-home"))
        cmd = [str(ROOT / "stt" / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)]
        proc = subprocess.Popen(cmd, cwd=str(ROOT / "stt"), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        health = f"http://127.0.0.1:{port}/health"
    else:
        r = {"model_key": model, "device_name": "cpu"}
        if device == "gpu":
            devs, _ = stt_engine.devices(ROOT)
            g = stt_engine.pick_gpu(devs or [], cfg.get("gpu", {}).get("name", ""))
            r["device_name"] = g["name"]
        cmd = stt_engine.speechcpp_command(cfg, r, ROOT)
        proc = subprocess.Popen(cmd, cwd=str(stt_engine.speech_dir(ROOT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        health = f"http://127.0.0.1:{port}/v1/models"
    t0 = time.time()
    while time.time() - t0 < 600:
        if proc.poll() is not None:
            raise RuntimeError(f"{name}: 起動中に終了しました")
        try:
            with urllib.request.urlopen(health, timeout=2) as r:
                if r.status == 200:
                    return proc, time.time() - t0
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError(f"{name}: 起動しませんでした")


def stop(proc):
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=0x08000000)


def cmd_run(args):
    d = Path(args.dir)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    wavs = [(m, (d / m["file"]).read_bytes()) for m in manifest]
    keys = args.only.split(",") if args.only else [k for k in CONFIGS if not k.endswith("hint")]
    rows, details = [], {}
    for key in keys:
        name, engine, model, device = CONFIGS[key]
        if engine == "speechcpp" and not stt_engine.model_present(ROOT, model):
            print(f"[{key}] モデル未取得のためスキップ")
            continue
        port = args.port
        print(f"[{key}] 起動中…", flush=True)
        proc, boot = start_server(key, port)
        try:
            prompt = args.hints if key.endswith("hint") else ""
            first, times, cers, cers2, cers3, texts = None, [], [], [], [], []
            for rep in range(args.repeat + 1):          # 0 回目は、ウォームアップとして別に数える
                for m, wav in wavs:
                    text, sec = transcribe(port, wav, prompt, "" if engine == "speechcpp" else model)
                    if rep == 0:
                        if m is wavs[0][0]:
                            first = sec
                        texts.append(text)
                        cers.append(cer(m["text"], text))
                        cers2.append(cer(m["text"], text, numbers=True))
                        rr, rh = to_reading(m["text"]), to_reading(text)
                        if rr is not None:
                            cers3.append(edit_distance(rr, rh) / max(1, len(rr)))
                    else:
                        times.append(sec)
            details[key] = [{"file": m["file"], "ref": m["text"], "hyp": t, "cer": round(c, 3)} for (m, _), t, c in zip(wavs, texts, cers)]
            rows.append({"key": key, "name": name, "boot_sec": round(boot, 1), "first_sec": round(first, 2),
                         "mean_sec": round(statistics.mean(times), 3) if times else None, "median_sec": round(statistics.median(times), 3) if times else None,
                         "cer": round(statistics.mean(cers), 4), "cer_num": round(statistics.mean(cers2), 4), "cer_kana": round(statistics.mean(cers3), 4) if cers3 else None, "exact": sum(1 for c in cers2 if c == 0), "n": len(wavs)})
            print(f"[{key}] 起動 {boot:.1f}s / 初回 {first:.2f}s / 平均 {rows[-1]['mean_sec']}s / CER {rows[-1]['cer']:.3f} / CER* {rows[-1]['cer_num']:.3f}", flush=True)
        finally:
            stop(proc)
            time.sleep(2)
    Path(args.out).write_text(json.dumps({"rows": rows, "details": details}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n| 構成 | CER | CER* | 読み CER | 完全一致 | 1 発話あたり（平均） | 初回 | 起動 |\n|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['name']} | {r['cer'] * 100:.1f}% | {r['cer_num'] * 100:.1f}% | {'-' if r['cer_kana'] is None else format(r['cer_kana'] * 100, '.1f') + '%'} | {r['exact']}/{r['n']} | {r['mean_sec']} 秒 | {r['first_sec']} 秒 | {r['boot_sec']} 秒 |")
    print("\n--- 認識結果 ---")
    for i, (m, _) in enumerate(wavs):
        print(f"\n{m['file']}  正解: {m['text']}")
        for key in details:
            print(f"   {key:<16} {details[key][i]['hyp']}  ({details[key][i]['cer'] * 100:.0f}%)")


def main():
    ap = argparse.ArgumentParser(description="STT の比較計測")
    ap.add_argument("cmd", choices=["synth", "run"])
    ap.add_argument("--dir", default=str(ROOT / "test_stt"))
    ap.add_argument("--only", default="")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--port", type=int, default=18300)
    ap.add_argument("--hints", default="仙台、ミオ、Hugging Face、Python、LLM")
    ap.add_argument("--out", default=str(ROOT / "test_stt" / "result.json"))
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"synth": cmd_synth, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    main()
