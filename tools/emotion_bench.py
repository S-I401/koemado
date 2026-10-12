"""感情の絵文字（方式 A / B）の効き目の比較（開発用）。Python 版 Irodori（既定の TTS）で、同じ 6 文を「感情なし / 方式 A / 方式 A + B」で合成し、
F0・話速・音量・読み戻し CER を表にして、試聴ページを作る。

  <インストール先>/tts/.venv/Scripts/python.exe tools/emotion_bench.py run [--out <フォルダ>] [--engine python|speechcpp]
  <インストール先>/tts/.venv/Scripts/python.exe tools/emotion_bench.py report --out <フォルダ>

  - サービス（supervisor）は止めておくこと。TTS を別のポートで起動する。GPU の空きが 4GB 以上あるときだけ動かす。
  - 方式 A + B の文は、方式 B の絵文字を、LLM が入れたものとして手で入れてある（実際の LLM の出力ではない）。
    方式 B の絵文字を含む文には、方式 A の絵文字は付かない（voice_emotion.tts_text の規則）。
  - F0 の「変化」は、1 文の中の F0 の標準偏差（半音）と範囲。話速は、絵文字を除いた文字数 ÷ 発話の長さ（無音の端を除く）。
    音量は、50ms 窓の RMS（dB）の平均と標準偏差。読み戻し CER は、絵文字なしの文と、qwen3-asr-1.7b の読み戻しの比較。
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import ctl  # noqa: E402
import stt_engine  # noqa: E402
import tts_bench as tb  # noqa: E402
import voice_emotion as ve  # noqa: E402

# (表情, 文, 方式 B の絵文字を入れた文)
SENTENCES = [
    ("joy", "今日はいい天気だね、一緒にお散歩に行こうよ。", "今日はいい天気だね😊、一緒にお散歩に行こうよ。"),
    ("fun", "やったあ、テストで満点だったよ。", "やったあ、テストで満点だったよ。"),
    ("angry", "もう、約束してたのに、どうして来てくれなかったの。", "もう😠、約束してたのに、どうして来てくれなかったの。"),
    ("sorrow", "ずっと大切にしてたのに、なくしちゃったんだ。", "ずっと大切にしてたのに🥺、なくしちゃったんだ。"),
    ("surprised", "えっ、本当に、あの人がここに来るの。", "えっ😲、本当に、あの人がここに来るの。"),
    ("neutral", "明日の天気は晴れで、気温は二十度くらいです。", "明日の天気は晴れで、気温は二十度くらいです。"),
]
VARIANTS = ["none", "A", "A2", "AB"]
SEEDS = [1, 2, 3, 4, 5]          # 乱数の揺れに埋もれないよう、5 つの seed の平均を見る（試聴ページは seed 1）


def variant_text(kind: str, face: str, plain: str, inline: str, cfg: dict) -> str:
    """TTS に渡す文（voice_emotion.tts_text と同じ規則）"""
    if kind == "none":
        return plain
    if kind == "A2":      # 強さ 2（絵文字を 2 個）
        cfg = {**cfg, "face_emoji": {**cfg["face_emoji"], "strength": 2}}
    src = plain if kind != "AB" else inline
    return ve.tts_text(src, {"styled_text": f"[face:{face}]{src}"}, cfg)


def audio_stats(path: Path, chars: int) -> dict:
    import librosa
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(path)
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = y.astype(np.float32)
    f0, voiced, _ = librosa.pyin(y, fmin=70, fmax=450, sr=sr, frame_length=2048)
    f0 = f0[~np.isnan(f0)]
    semis = 12 * np.log2(f0 / 100.0) if len(f0) else np.array([0.0])
    win = int(sr * 0.05)
    rms = np.array([np.sqrt(np.mean(y[i:i + win] ** 2) + 1e-12) for i in range(0, len(y) - win, win)])
    db = 20 * np.log10(rms)
    active = db > (db.max() - 40)                      # 無音（最大から 40dB 以下）を除く
    idx = np.where(active)[0]
    speech_sec = (idx[-1] - idx[0] + 1) * 0.05 if len(idx) else len(y) / sr
    return {"f0_median": round(float(np.median(f0)), 1) if len(f0) else 0, "f0_std": round(float(np.std(semis)), 2), "f0_range": round(float(semis.max() - semis.min()), 2),
            "cps": round(chars / speech_sec, 2), "seconds": round(len(y) / sr, 2), "speech_seconds": round(speech_sec, 2),
            "db_mean": round(float(db[active].mean()), 1), "db_std": round(float(db[active].std()), 2)}


def cmd_run(args):
    cfg = ctl.load_config(ROOT)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mcfg = ve.merged_config({})
    key = "py" if args.engine == "python" else "sp-rf24-q8"
    proc, boot = tb.start_server(key, cfg)
    res = {"engine": args.engine, "rows": []}
    try:
        url, _ = tb.synth_body("python" if args.engine == "python" else "speechcpp", "x", 24)
        kind = "python" if args.engine == "python" else "speechcpp"
        tb.post(*tb.synth_body(kind, "ウォームアップです。", 24))
        for i, (face, plain, inline) in enumerate(SENTENCES, 1):
            row = {"face": face, "plain": plain, "variants": {}}
            for v in VARIANTS:
                text = variant_text(v, face, plain, inline, mcfg)
                chars = len(ve.strip_all(plain if v != "AB" else inline).replace("、", "").replace("。", ""))
                runs = []
                for k, seed in enumerate(SEEDS):
                    url, body = tb.synth_body(kind, text, 24, seed=seed)
                    t0 = time.perf_counter()
                    wav = tb.post(url, body)
                    sec = time.perf_counter() - t0
                    d = out / v
                    d.mkdir(exist_ok=True)
                    p = d / (f"s{i}.wav" if k == 0 else f"s{i}_seed{seed}.wav")
                    p.write_bytes(wav)
                    runs.append({"gen_sec": round(sec, 2), "path": p.name, **audio_stats(p, chars)})
                keys = ["f0_median", "f0_std", "f0_range", "cps", "seconds", "speech_seconds", "db_mean", "db_std", "gen_sec"]
                row["variants"][v] = {"tts_text": text, "runs": runs, **{k2: round(statistics.mean(r2[k2] for r2 in runs), 2) for k2 in keys}}
                x = row["variants"][v]
                print(f"s{i} {face:9} {v:4} {text}  F0 {x['f0_median']}±{x['f0_std']} {x['cps']}字/秒 {x['db_mean']}dB", flush=True)
            res["rows"].append(row)
    finally:
        tb.kill(proc)
    time.sleep(2)
    # 読み戻し
    import stt_bench
    cfg2 = {**cfg, "ports": {**cfg["ports"], "stt": tb.PORT + 1}}
    devs, _ = stt_engine.devices(ROOT)
    r = {"model_key": "qwen3-asr-1.7b", "device_name": stt_engine.pick_gpu(devs, cfg["gpu"]["name"])["name"]}
    asr = subprocess.Popen(stt_engine.speechcpp_command(cfg2, r, ROOT), cwd=str(stt_engine.speech_dir(ROOT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{tb.PORT + 1}/v1/models", timeout=2)
                break
            except Exception:
                time.sleep(1)
        for i, row in enumerate(res["rows"], 1):
            for v in VARIANTS:
                ref = row["plain"] if v != "AB" else ve.strip_all(SENTENCES[i - 1][2])
                cers = []
                for k, run in enumerate(row["variants"][v]["runs"]):
                    p16 = out / v / ("tmp16.wav")
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / v / run["path"]), "-ar", "16000", "-ac", "1", str(p16)], check=True)
                    text, _ = stt_bench.transcribe(tb.PORT + 1, p16.read_bytes())
                    run["readback"] = text
                    cers.append(stt_bench.cer(ref, text, numbers=True))
                row["variants"][v]["readback"] = row["variants"][v]["runs"][0]["readback"]
                row["variants"][v]["cer"] = round(statistics.mean(cers), 3)
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(asr.pid)], capture_output=True, creationflags=0x08000000)
    (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd_report(args)


def cmd_report(args):
    out = Path(args.out)
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    names = {"none": "感情なし", "A": "方式 A（表情 → 絵文字）", "A2": "方式 A・強（絵文字 2 個）", "AB": "方式 A + B（LLM の絵文字も）"}
    lines = ["| 表情 | 方式 | TTS への入力 | F0 中央値 | F0 変化（標準偏差 / 範囲、半音） | 話速（字/秒） | 音量 平均 / 変化（dB） | 読み戻し CER |", "|---|---|---|---|---|---|---|---|"]
    for row in res["rows"]:
        for v in VARIANTS:
            x = row["variants"][v]
            lines.append(f"| {row['face']} | {names[v]} | {x['tts_text']} | {x['f0_median']}Hz | {x['f0_std']} / {x['f0_range']} | {x['cps']} | {x['db_mean']} / {x['db_std']} | {x['cer'] * 100:.0f}% |")
    lines.append("")
    lines.append("| 方式 | F0 中央値（平均） | F0 標準偏差（平均） | 話速（平均） | 音量の変化（平均） | CER（平均） |")
    lines.append("|---|---|---|---|---|---|")
    for v in VARIANTS:
        xs = [r["variants"][v] for r in res["rows"]]
        m = lambda k: statistics.mean(x[k] for x in xs)  # noqa: E731
        lines.append(f"| {names[v]} | {m('f0_median'):.0f}Hz | {m('f0_std'):.2f} | {m('cps'):.2f} | {m('db_std'):.2f} | {m('cer') * 100:.1f}% |")
    (out / "table.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    parts = ['<!doctype html><meta charset=utf-8><title>感情の絵文字の比較</title><style>body{font-family:sans-serif;max-width:980px;margin:20px auto}.c{border:1px solid #ccc;padding:10px;margin:10px 0}audio{width:100%;margin:2px 0}small{color:#555}</style>'
             f"<h1>感情を絵文字で声に乗せる（{res['engine']}、mio_bright、24 steps。数値は seed 1〜5 の平均、音声は seed 1）</h1><p>各文を、感情なし / 方式 A / 方式 A + B で並べています。音の判断はお任せします。</p>"]
    for i, row in enumerate(res["rows"], 1):
        parts.append(f"<div class=c><h2>{i}. {row['face']}: {row['plain']}</h2>")
        for v in VARIANTS:
            x = row["variants"][v]
            parts.append(f"<div><b>{names[v]}</b>　<small>入力: {x['tts_text']} / F0 {x['f0_median']}Hz（変化 {x['f0_std']}半音） / {x['cps']}字/秒 / {x['db_mean']}dB（変化 {x['db_std']}） / 読み戻し: {x['readback']}（CER {x['cer'] * 100:.0f}%）</small>"
                         f"<audio controls src='{v}/s{i}.wav'></audio></div>")
        parts.append("</div>")
    (out / "listen.html").write_text("\n".join(parts), encoding="utf-8")
    print("保存:", out / "listen.html")


def main():
    ap = argparse.ArgumentParser(description="感情の絵文字の比較")
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--out", default=str(ROOT / "test_emotion"))
    ap.add_argument("--engine", choices=["python", "speechcpp"], default="python")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
