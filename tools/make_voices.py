"""Irodori の Voice Design（キャプション + 固定シード）で、成人女性の参照音声を作る。

1. 候補（キャプション × シード）ごとに、別々の 5 文を合成する
2. 文ごとの基本周波数（F0）の中央値を測り、文どうしのばらつき（半音）を安定度の指標にする
3. キャプションごとに最も安定したシードを選び、参照音声 voices/<名前>.wav として保存する
4. 保存した参照音声で声をクローンして 5 文を作り直し、クローン後の安定度も測る
5. 結果を voices/voices_report.json に記録する（キャプション・シード・指標。リポジトリには含めない作業用の記録）

実在の人物の声は使わない。TTS サーバー（setup 済みの環境）が起動している状態で実行する。
  <インストール先>\tts\.venv\Scripts\python.exe tools\make_voices.py --port 8088 --out voice_work
"""
import argparse
import json
import statistics
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

SENTENCES = [
    "こんにちは。今日はどんな一日でしたか？ゆっくり聞かせてくださいね。",
    "なるほど、それは大変でしたね。でも、ここまでよく頑張りましたよ。",
    "明日は晴れるみたいです。洗濯物がよく乾きそうで、ちょっと嬉しいですね。",
    "そうですね、私は温かいお茶を飲みながら、のんびり過ごすのが好きです。",
    "もしよかったら、今度おすすめの映画を一緒に探してみませんか？",
]
REF_SENTENCE = "こんにちは。今日はゆっくりお話ししましょう。最近あった楽しいことを、ぜひ聞かせてくださいね。"

CAPTIONS = {
    "a": "明るく落ち着いた20代後半の女性の声。柔らかく自然な話し方。",
    "b": "落ち着いた低めの20代後半の女性の声。穏やかで聞き取りやすい話し方。",
    "c": "はきはきとした明るい20代後半の女性の声。親しみやすく自然な話し方。",
    "d": "柔らかくて優しい20代後半の女性の声。少しゆっくりめで丁寧な話し方。",
}
SEEDS = [11, 23, 42]
STEPS = 32


def synth(base: str, text: str, voice: str, caption: str | None, seed: int, steps: int = STEPS) -> bytes:
    irodori = {"num_steps": steps, "seed": seed}
    if caption:
        irodori["caption"] = caption
    body = {"model": "irodori-tts", "input": text, "voice": voice, "response_format": "wav", "irodori": irodori}
    req = urllib.request.Request(base + "/v1/audio/speech", json.dumps(body).encode(), {"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=300).read()


def median_f0(path: Path) -> tuple[float, float]:
    import librosa
    y, sr = sf.read(path)
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = y.astype(np.float32)
    f0, voiced, _ = librosa.pyin(y, fmin=70, fmax=450, sr=sr, frame_length=2048)
    f0 = f0[~np.isnan(f0)]
    ratio = len(f0) / max(1, len(y) / sr * sr / 512)
    return (float(np.median(f0)) if len(f0) else 0.0), float(ratio)


def stability(files: list[Path]) -> dict:
    meds = [median_f0(f)[0] for f in files]
    meds = [m for m in meds if m > 0]
    semis = [12 * np.log2(m / 100.0) for m in meds]
    return {
        "median_f0_hz": round(float(np.median(meds)), 1),
        "per_sentence_f0_hz": [round(m, 1) for m in meds],
        "std_semitones": round(float(np.std(semis)), 2),
        "range_semitones": round(float(max(semis) - min(semis)), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--out", default="voice_work")
    ap.add_argument("--voices-dir", default="voices")
    ap.add_argument("--prefix", default="mio")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    out = Path(args.out)
    vdir = Path(args.voices_dir)
    out.mkdir(exist_ok=True)
    vdir.mkdir(exist_ok=True)

    report = {"sentences": SENTENCES, "ref_sentence": REF_SENTENCE, "steps": STEPS, "candidates": [], "voices": {}}
    best = {}
    for key, caption in CAPTIONS.items():
        for seed in SEEDS:
            files = []
            for i, text in enumerate(SENTENCES):
                f = out / f"cand_{key}_s{seed}_{i}.wav"
                f.write_bytes(synth(base, text, "none", caption, seed))
                files.append(f)
            st = stability(files)
            row = {"key": key, "caption": caption, "seed": seed, **st}
            report["candidates"].append(row)
            print(f"cand {key} seed {seed}: F0 {st['median_f0_hz']} Hz, ばらつき {st['std_semitones']} 半音（範囲 {st['range_semitones']}）", flush=True)
            if key not in best or st["std_semitones"] < best[key]["std_semitones"]:
                best[key] = row

    # 各キャプションの最良シードを参照音声にする
    for key, row in best.items():
        name = f"{args.prefix}_{key}"
        ref = vdir / f"{name}.wav"
        ref.write_bytes(synth(base, REF_SENTENCE, "none", row["caption"], row["seed"]))
        dur = sf.info(ref).duration
        clones = []
        for i, text in enumerate(SENTENCES):
            f = out / f"clone_{name}_{i}.wav"
            f.write_bytes(synth(base, text, name, None, 42, steps=24))
            clones.append(f)
        cst = stability(clones)
        report["voices"][name] = {"caption": row["caption"], "seed": row["seed"], "design_std_semitones": row["std_semitones"],
                                  "ref_duration_sec": round(dur, 1), "clone": cst}
        print(f"voice {name}: 参照 {dur:.1f}s, 設計時 {row['std_semitones']} 半音 -> クローン後 {cst['std_semitones']} 半音, F0 {cst['median_f0_hz']} Hz", flush=True)

    (vdir / "voices_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", vdir / "voices_report.json")


if __name__ == "__main__":
    main()
