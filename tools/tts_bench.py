"""TTS の比較計測（開発用）。同じ文を、今の Python 版 Irodori と speech.cpp の Irodori-TTS（mf / rf）で合成して、
時間・VRAM・F0（声がミオらしく保たれているか）・読み戻し CER（speech.cpp の qwen3-asr-1.7b）を測り、試聴ページを作る。

  <インストール先>/tts/.venv/Scripts/python.exe tools/tts_bench.py run [--only py,sp-mf,sp-rf16,sp-rf24] [--out <フォルダ>]
  （tts/.venv の Python を使うのは、F0 の計算に librosa が要るため。Python 版 Irodori の環境があるときだけ動かせる）

  - 各構成を、別のポートで起動して、1 文ずつ合成する（先に 1 回ウォームアップ）。サービス（supervisor）は止めておくこと。
  - 時間は、リクエストから WAV が全部届くまで（アプリは WAV を全部受け取ってから再生する）。speech.cpp は、PCM のストリームで
    「最初の音が届くまで」も測る。
  - VRAM は、Windows の GPU Process Memory（プロセスごとの専有メモリ）を 0.5 秒ごとに読んだ最大。
  - F0 は tools/make_voices.py の stability と同じ（pyin。文ごとの F0 の中央値の、中央値とばらつき（半音））。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import ctl  # noqa: E402
import stt_engine  # noqa: E402

SENTENCES = [
    "あっ、こんにちは！今日も来てくれたんだね、うれしいな。",
    "うんうん、わかった。それでそれで？",
    "やったあ！すごいすごい、ほんとに？ありがとう！",
    "もう、知らない。ずっと待ってたのに、ちょっとだけ寂しかったんだからね。",
    "今日の仙台は晴れて、とっても気持ちよさそうですよ。",
    "明日の朝七時に起こしてくださいね。お願いします。",
    "最近、音声認識の精度が上がって、とても便利になりましたね。",
    "Pythonでコードを書いて、ローカルのLLMで動かしています。",
]
VOICE = "mio_bright"
SEED = 42
PORT = 18401

# 構成 -> (表示名, 種類, モデルのキー（speech.cpp）, steps, 型)
CONFIGS = {
    "py": ("Python 版 Irodori（int8、24 steps）", "python", None, 24, None),
    "sp-mf": ("speech.cpp irodori-tts-mf（F16、4 steps）", "speechcpp", "irodori-tts-mf", 4, "f16"),
    "sp-mf-q8": ("speech.cpp irodori-tts-mf（Q8_0、4 steps）", "speechcpp", "irodori-tts-mf", 4, "q8_0"),
    "sp-rf16": ("speech.cpp irodori-tts（F16、16 steps）", "speechcpp", "irodori-tts", 16, "f16"),
    "sp-rf24": ("speech.cpp irodori-tts（F16、24 steps）", "speechcpp", "irodori-tts", 24, "f16"),
    "sp-rf16-q8": ("speech.cpp irodori-tts（Q8_0、16 steps）", "speechcpp", "irodori-tts", 16, "q8_0"),
    "sp-rf24-q8": ("speech.cpp irodori-tts（Q8_0、24 steps）", "speechcpp", "irodori-tts", 24, "q8_0"),
}


def post(url: str, body: dict, timeout=300) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def first_byte(url: str, body: dict) -> float:
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        r.read(1)
        t = time.perf_counter() - t0
        r.read()
    return t


class Gpu:
    """GPU Process Memory（プロセスごとの専有メモリ）を 0.5 秒ごとに CSV へ書くサンプラー（PowerShell）"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.stop = self.path.with_suffix(".stop")

    def start(self):
        self.stop.unlink(missing_ok=True)
        ps1 = Path(__file__).with_name("gpu_sampler.ps1")
        subprocess.Popen(["powershell", "-NoProfile", "-File", str(ps1), "-Out", str(self.path), "-Stop", str(self.stop)], creationflags=0x08000000)
        time.sleep(4)

    def end(self):
        self.stop.write_text("x")

    def mib(self, pid: int) -> float:
        pids = {pid} | children(pid)
        peak = {}
        for line in self.path.read_text(encoding="ascii", errors="ignore").splitlines()[1:]:
            if "," in line:
                a, b = line.split(",")
                if a.isdigit() and int(a) in pids:
                    peak[int(a)] = max(peak.get(int(a), 0), int(b))
        return sum(peak.values()) / 2 ** 20


def children(pid: int) -> set:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process | Where-Object {{ $_.ParentProcessId -eq {pid} }}).ProcessId"],
                         capture_output=True, text=True).stdout.split()
    res = {int(x) for x in out if x.isdigit()}
    for c in list(res):
        res |= children(c)
    return res


def start_server(key: str, cfg: dict):
    name, kind, model, steps, typ = CONFIGS[key]
    cfg = {**cfg, "ports": {**cfg["ports"], "tts": PORT}}
    if kind == "python":
        cmd, cwd, env = ctl.service_command(cfg, "tts", ROOT)
        health, needle = f"http://127.0.0.1:{PORT}/health", '"loaded":true'
    else:
        import tts_engine
        r = {"model_key": model, "type": typ, "device_name": stt_engine.pick_gpu(stt_engine.devices(ROOT)[0] or [], cfg["gpu"]["name"])["name"]}
        cmd = tts_engine.speechcpp_command(cfg, r, ROOT)
        cwd, env = stt_engine.speech_dir(ROOT), dict(os.environ)
        health, needle = f"http://127.0.0.1:{PORT}/v1/models", "owned_by"
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    t0 = time.time()
    while time.time() - t0 < 900:
        if proc.poll() is not None:
            raise RuntimeError(f"{name}: 起動中に終了")
        try:
            with urllib.request.urlopen(health, timeout=2) as r:
                if needle in r.read().decode("utf-8", "replace").replace(" ", ""):
                    return proc, time.time() - t0
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError(f"{name}: 起動しない")


def synth_body(kind: str, text: str, steps: int, seed: int = SEED, fmt: str = "wav") -> tuple[str, dict]:
    if kind == "python":
        return f"http://127.0.0.1:{PORT}/v1/audio/speech", {"model": "irodori-tts", "input": text, "voice": VOICE, "response_format": fmt, "irodori": {"num_steps": steps, "seed": seed}}
    return f"http://127.0.0.1:{PORT}/v1/audio/speech", {"input": text, "voice": VOICE, "response_format": fmt, "steps": steps, "seed": seed}


def kill(proc):
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=0x08000000)


def wav_seconds(data: bytes) -> float:
    import io
    import wave
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnframes() / w.getframerate()


def cmd_run(args):
    import statistics
    import make_voices as mv
    cfg = ctl.load_config(ROOT)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    keys = args.only.split(",") if args.only else ["py", "sp-mf", "sp-rf16", "sp-rf24"]
    gpu = Gpu(out / "gpu.csv")
    gpu.start()
    results = {}
    for key in keys:
        name, kind, model, steps, typ = CONFIGS[key]
        print(f"[{key}] 起動中…", flush=True)
        proc, boot = start_server(key, cfg)
        url, body = synth_body(kind, "ウォームアップです。", steps)
        t0 = time.perf_counter()
        post(url, body)
        warm = time.perf_counter() - t0
        d = out / key
        d.mkdir(exist_ok=True)
        rows = []
        for i, text in enumerate(SENTENCES, 1):
            url, body = synth_body(kind, text, steps)
            ts = []
            for rep in range(args.repeat):
                t0 = time.perf_counter()
                wav = post(url, body)
                ts.append(time.perf_counter() - t0)
            (d / f"s{i}.wav").write_bytes(wav)
            fb = None
            if kind == "speechcpp":
                url2, body2 = synth_body(kind, text, steps, fmt="pcm")
                fb = first_byte(url2, body2)
            rows.append({"text": text, "total": statistics.median(ts), "first_byte": fb, "audio_sec": wav_seconds(wav)})
            print(f"  s{i}: total {rows[-1]['total']:.2f}s first {fb and round(fb, 2)} audio {rows[-1]['audio_sec']:.1f}s", flush=True)
        time.sleep(1.5)
        vram = gpu.mib(proc.pid)
        kill(proc)
        time.sleep(2)
        st = mv.stability([d / f"s{i}.wav" for i in range(1, len(SENTENCES) + 1)])
        tot = sum(r["total"] for r in rows)
        aud = sum(r["audio_sec"] for r in rows)
        results[key] = {"name": name, "boot_sec": round(boot, 1), "warmup_sec": round(warm, 2), "rows": rows, "vram_mib": round(vram),
                        "mean_total": round(tot / len(rows), 2), "median_total": round(statistics.median(r["total"] for r in rows), 2), "first_sentence_total": round(rows[0]["total"], 2), "rtf": round(tot / aud, 3),
                        "mean_first_byte": round(statistics.mean(r["first_byte"] for r in rows), 2) if kind == "speechcpp" else None,
                        "f0": st}
        print(f"[{key}] 起動 {boot:.1f}s / 初回 {warm:.2f}s / 1 文 {results[key]['mean_total']}s / RTF {results[key]['rtf']} / VRAM {vram:.0f}MiB / F0 {st['median_f0_hz']}Hz ±{st['std_semitones']}半音", flush=True)
    gpu.end()
    import shutil
    (out / "ref").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "tts" / "voices" / f"{VOICE}.wav", out / "ref" / f"{VOICE}.wav")
    results["_ref"] = {"name": "参照音声（mio_bright）", "f0": mv.stability([ROOT / "tts" / "voices" / f"{VOICE}.wav"])}
    (out / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("保存:", out / "result.json")


def cmd_cer(args):
    """合成した WAV を speech.cpp の qwen3-asr-1.7b で読み戻して CER を出す（result.json に cer を足す）"""
    import stt_bench
    out = Path(args.out)
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    cfg = ctl.load_config(ROOT)
    cfg = {**cfg, "ports": {**cfg["ports"], "stt": PORT + 1}}
    devs, _ = stt_engine.devices(ROOT)
    r = {"model_key": "qwen3-asr-1.7b", "device_name": "cpu" if args.cpu else stt_engine.pick_gpu(devs, cfg["gpu"]["name"])["name"]}
    proc = subprocess.Popen(stt_engine.speechcpp_command(cfg, r, ROOT), cwd=str(stt_engine.speech_dir(ROOT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT + 1}/v1/models", timeout=2)
            break
        except Exception:
            time.sleep(1)
    try:
        for key, v in res.items():
            if key.startswith("_"):
                continue
            cers, hyps = [], []
            for i, row in enumerate(v["rows"], 1):
                wav = (out / key / f"s{i}.wav").read_bytes()
                # 16kHz へ（ffmpeg）
                p16 = out / key / f"s{i}_16k.wav"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / key / f"s{i}.wav"), "-ar", "16000", "-ac", "1", str(p16)], check=True)
                text, _ = stt_bench.transcribe(PORT + 1, p16.read_bytes())
                hyps.append(text)
                cers.append(stt_bench.cer(row["text"], text, numbers=True))
            v["cer"] = round(sum(cers) / len(cers), 4)
            v["hyps"] = hyps
            print(f"[{key}] 読み戻し CER* {v['cer'] * 100:.1f}%", flush=True)
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=0x08000000)
    (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_html(args):
    out = Path(args.out)
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    parts = ['<!doctype html><meta charset=utf-8><title>ミオ TTS 比較</title><style>body{font-family:sans-serif;max-width:980px;margin:20px auto}.c{border:1px solid #ccc;padding:10px;margin:10px 0}audio{width:100%;margin:2px 0}small{color:#555}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:3px 8px}</style><h1>ミオ（mio_bright）の声: TTS 方式の比較</h1>']
    rf = res.get("_ref", {}).get("f0", {})
    parts.append(f"<div class=c><h2>参照音声（mio_bright）</h2><small>F0 {rf.get('median_f0_hz')}Hz（比べる基準）</small><audio controls src='ref/mio_bright.wav'></audio></div>")
    for key, v in res.items():
        if key.startswith("_"):
            continue
        f0 = v["f0"]
        cer = f" / 読み戻し CER* {v['cer'] * 100:.1f}%" if "cer" in v else ""
        parts.append(f"<div class=c><h2>{key}: {v['name']}</h2><small>1 文 {v['mean_total']}s（RTF {v['rtf']}）"
                     + (f" / 最初の音まで {v['mean_first_byte']}s" if v.get("mean_first_byte") else "")
                     + f" / VRAM {v['vram_mib']}MiB / F0 {f0['median_f0_hz']}Hz / ばらつき {f0['std_semitones']}半音{cer}</small>")
        for i, row in enumerate(v["rows"], 1):
            hyp = f"<br><small>読み戻し: {v['hyps'][i - 1]}</small>" if "hyps" in v else ""
            parts.append(f"<div>{i}. {row['text']}（{row['audio_sec']:.1f}s、生成 {row['total']:.2f}s）<audio controls src='{key}/s{i}.wav'></audio>{hyp}</div>")
        parts.append("</div>")
    (out / "listen.html").write_text("\n".join(parts), encoding="utf-8")
    print("保存:", out / "listen.html")


def main():
    ap = argparse.ArgumentParser(description="TTS の比較計測")
    ap.add_argument("cmd", choices=["run", "cer", "html"])
    ap.add_argument("--only", default="")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--cpu", action="store_true", help="cer: 読み戻しの ASR を CPU で動かす")
    ap.add_argument("--out", default=str(ROOT / "test_tts"))
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"run": cmd_run, "cer": cmd_cer, "html": cmd_html}[args.cmd](args)


if __name__ == "__main__":
    main()
