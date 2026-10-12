"""感情の絵文字の通しテスト（WebSocket。マイク不要）。表情タグつきの返答で、字幕・吹き出しに出る文（WS のメッセージ）に絵文字が無いこと、
会話の記録（aiavatar.db の performance_records）に絵文字が無いことを確かめ、TTS への入力は app のログ（KOEMADO_LOG_TTS_INPUT=1 で起動したとき）から読む。

  KOEMADO_LOG_TTS_INPUT=1 で supervisor を起動してから:
  <インストール先>/app/.venv/Scripts/python.exe tools/e2e_emotion.py "うれしいことがあったよ！" --repeat 2
"""
import argparse
import asyncio
import base64
import json
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import voice_emotion as ve  # noqa: E402


def port() -> int:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["ports"]["app"]


async def one(text: str, timeout: float) -> dict:
    sid = str(uuid.uuid4())
    res = {"chunks": [], "final": None, "wavs": 0}
    async with websockets.connect(f"ws://127.0.0.1:{port()}/ws", max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "session_id": sid, "user_id": "emo"}))
        await ws.send(json.dumps({"type": "invoke", "session_id": sid, "user_id": "emo", "text": text}, ensure_ascii=False))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if m.get("type") == "chunk":
                res["chunks"].append({"text": m.get("text"), "voice_text": m.get("voice_text"), "face": (m.get("avatar_control_request") or {}).get("face_name")})
                if m.get("audio_data") and base64.b64decode(m["audio_data"])[:4] == b"RIFF":
                    res["wavs"] += 1
            elif m.get("type") == "final":
                res["final"] = {"text": m.get("text"), "voice_text": m.get("voice_text")}
                return res


def tts_inputs(since: float) -> list[dict]:
    out = []
    log = ROOT / "logs" / "app.log"
    if not log.exists():
        return out
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
        m = re.search(r"TTS_INPUT (\{.*\})", line)
        if m:
            try:
                out.append(json.loads(m.group(1)))
            except Exception:
                pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="今日、いいことがあったんだ！")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=180)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    bad = 0
    for i in range(args.repeat):
        before = len(tts_inputs(0))
        r = asyncio.run(one(args.text, args.timeout))
        time.sleep(1.0)
        ins = tts_inputs(0)[before:] if before < 400 else tts_inputs(0)[-len(r["chunks"]):]
        print(f"--- {i + 1} 回目 ---")
        for c in r["chunks"]:
            has = bool(ve.EMOJI_RE.search((c["text"] or "") + (c["voice_text"] or "")))
            bad += has
            print(f"WS chunk  face={c['face']} text={c['text']!r} voice_text={c['voice_text']!r} 絵文字あり={has}")
        fin = r["final"] or {}
        has = bool(ve.EMOJI_RE.search((fin.get("text") or "") + (fin.get("voice_text") or "")))
        bad += has
        print(f"WS final  text={fin.get('text')!r} 絵文字あり={has}  / 音声 {r['wavs']} 個")
        for x in ins:
            print(f"TTS 入力  {x['tts']!r}   （LLM の出力 voice_text={x['raw']!r}）")
    db = sqlite3.connect(f"file:{ROOT / 'app' / 'aiavatar.db'}?mode=ro", uri=True)
    rows = db.execute("select response_text, response_voice_text from performance_records order by rowid desc limit ?", (max(3, args.repeat),)).fetchall()
    for t, v in rows:
        has = bool(ve.EMOJI_RE.search((t or "") + (v or "")))
        bad += has
        print(f"記録(SQLite) response_text={t!r} 絵文字あり={has}")
    print("結果:", "絵文字は字幕・記録に出ていません" if not bad else f"絵文字が {bad} か所に出ています")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
