"""WebSocket 経由のテキスト入力テスト（マイク不要）。

返答のテキスト・表情/モーションの指定・ツール呼び出し・音声（WAV）の受信と、
入力から最初の音声が届くまでの時間を確認する。

  <インストール先>/app/.venv/Scripts/python.exe tools/e2e_test.py "明日の天気は？" --repeat 3
"""
import argparse
import asyncio
import base64
import json
import re
import sys
import time
import uuid
from pathlib import Path

import websockets


def load_port() -> int:
    cfg = Path(__file__).resolve().parent.parent / "config.json"
    if cfg.exists():
        return json.loads(cfg.read_text(encoding="utf-8"))["ports"]["app"]
    return 8100


async def one(url: str, text: str, outdir: Path, idx: int, timeout: float, user_id: str, image: str = None) -> dict:
    session_id = str(uuid.uuid4())
    res = {"text": "", "controls": [], "tools": [], "audio": 0, "first_audio": None, "total": None, "wav_ok": True}
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "session_id": session_id, "user_id": user_id}))
        t0 = time.perf_counter()
        msg = {"type": "invoke", "session_id": session_id, "user_id": user_id, "text": text}
        if image:   # 画像（data URL に変換して files で送る。会話画面の「画像を送る」パネルと同じ）
            import mimetypes
            mime = mimetypes.guess_type(image)[0] or "image/png"
            msg["files"] = [{"url": f"data:{mime};base64," + base64.b64encode(Path(image).read_bytes()).decode()}]
        await ws.send(json.dumps(msg, ensure_ascii=False))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            msg = json.loads(raw)
            kind = msg.get("type")
            now = time.perf_counter() - t0
            if kind == "chunk":
                if msg.get("text"):
                    res["text"] += msg["text"]
                if msg.get("avatar_control_request"):
                    res["controls"].append(msg["avatar_control_request"])
                if msg.get("audio_data"):
                    wav = base64.b64decode(msg["audio_data"])
                    res["audio"] += 1
                    if res["first_audio"] is None:
                        res["first_audio"] = now
                    if not wav.startswith(b"RIFF"):
                        res["wav_ok"] = False
                    (outdir / f"run{idx}_{res['audio']:02d}.wav").write_bytes(wav)
            elif kind == "tool_call":
                tc = (msg.get("metadata") or {}).get("tool_call") or {}
                res["tools"].append(tc.get("name") if isinstance(tc, dict) else str(tc))
            elif kind == "final":
                res["total"] = now
                if not res["text"]:
                    res["text"] = msg.get("text") or ""
                break
            elif kind == "error":
                res["error"] = msg
                break
    return res


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="こんにちは。今日はどんな一日でしたか？")
    ap.add_argument("--port", type=int, default=load_port())
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--out", default="test_out")
    ap.add_argument("--user", default="tester")
    ap.add_argument("--image", help="画像ファイルを一緒に送る（画像認識のテスト）")
    args = ap.parse_args()
    outdir = Path(args.out)
    outdir.mkdir(exist_ok=True)
    url = f"ws://127.0.0.1:{args.port}/ws"
    results = []
    for i in range(1, args.repeat + 1):
        r = await one(url, args.text, outdir, i, args.timeout, args.user, args.image)
        results.append(r)
        fa = f"{r['first_audio']:.2f}s" if r["first_audio"] is not None else "なし"
        print(f"[{i}] 最初の音声まで {fa} / 全体 {r['total'] and round(r['total'], 2)}s / 音声 {r['audio']} 個 / ツール {r['tools']}")
        print(f"     テキスト: {r['text']}")
        print(f"     制御    : {[c for c in r['controls'] if c.get('face_name') or c.get('animation_name')]}")
    texts = {re.sub(r"\s+", "", r["text"]) for r in results}
    print(f"\n異なる返答: {len(texts)} / {len(results)}")
    if any(re.search(r"(?i)thinking|<think>|\*\s*User|Role:", r["text"]) for r in results):
        print("警告: 思考らしい文字列が混ざっています")
    return 0


sys.exit(asyncio.run(main()))
