"""AIAvatarKit 本体: LLM / TTS / STT / ツール / 記憶 / 設定画面を組み立てる。

- 低レベルの値（ポート）は同じフォルダの .env、導入時の値は ../config.json
- 声・LLM・キャラクターは ../config/settings.json（設定画面 /settings か configure.bat で変える）
  声・キャラクター・temperature は、次の発話から反映される
"""

import os
import sqlite3
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from aiavatar.adapter.websocket.server import AIAvatarWebSocketServer
from aiavatar.admin import setup_admin_panel
from aiavatar.sts.llm.chatgpt import ChatGPTService
from aiavatar.sts.stt.openai import OpenAISpeechRecognizer
from aiavatar.sts.tts import SpeechSynthesizerDummy, create_instant_synthesizer

BASE_DIR = Path(__file__).parent
ROOT = BASE_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)
load_dotenv(BASE_DIR / ".env")

import ctl  # noqa: E402
import settings_store as ss  # noqa: E402
from avatar_memory import SCHEMA, AvatarMemory  # noqa: E402
from settings_api import LocalOnlyMiddleware, make_router  # noqa: E402
from weather_tool import WeatherTool  # noqa: E402


def flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


CFG = ctl.load_config(ROOT)


def settings() -> dict:
    return ss.load_settings(ROOT)   # ファイルが変わったときだけ読み直す


def current_character() -> dict:
    return ss.active_character(settings(), ROOT)


# ---- LLM ----
class BufferedChatGPTService(ChatGPTService):
    """LLM の出力を最後まで受け取ってから流す（直列化）。

    AIAvatarKit は文ごとに TTS を待つが、その間も LLM は生成を続けるので、同じ GPU では
    LLM と TTS が VRAM と演算を取り合って両方遅くなる（VRAM 8GB クラスでは特に顕著）。
    別 PC やクラウドの LLM なら競合しないので、設定で切れる。
    """

    # 返事のあとにこれらのツールを呼ぶと、モデルが同じ反応をもう一度話しがちなので、2 回目は捨てる
    SILENT_AFTER_TOOLS = {"remember"}
    serialize = True

    def apply_runtime(self):
        """設定画面で変えた値を、次のリクエストから反映する"""
        r = ss.resolve_llm(CFG, settings(), ROOT)
        self.temperature = r["temperature"]
        self.reasoning_effort = r["reasoning_effort"]
        self.extra_body = r["extra_body"]
        self.serialize = r["serialize"]

    async def update_context(self, context_id, user_id, messages, response_text):
        """会話の記録（SQLite）に画像を残さない（既定）。残すかどうかは設定画面の「画像を会話の記録に残す」"""
        if not ss.resolve_llm(CFG, settings(), ROOT).get("store_images"):
            for m in messages:
                if isinstance(m.get("content"), list):
                    m["content"] = [({"type": "text", "text": "（画像を送信しました）"} if part.get("type") == "image_url" else part)
                                    for part in m["content"]]
        await super().update_context(context_id, user_id, messages, response_text)

    async def chat_stream(self, *args, **kwargs):
        self.apply_runtime()
        if not self.serialize:
            async for chunk in super().chat_stream(*args, **kwargs):
                yield chunk
            return
        chunks = [chunk async for chunk in super().chat_stream(*args, **kwargs)]
        spoken = False
        mute = False
        for chunk in chunks:
            if chunk.tool_call:
                if spoken and chunk.tool_call.name in self.SILENT_AFTER_TOOLS:
                    mute = True
                yield chunk
                continue
            if mute and chunk.text:
                continue
            if chunk.voice_text:
                spoken = True
            yield chunk


R = ss.resolve_llm(CFG, settings(), ROOT)
llm = BufferedChatGPTService(
    openai_api_key=R["api_key"],
    base_url=R["base_url"],
    model=R["model"] or "local",
    temperature=R["temperature"],
    reasoning_effort=R["reasoning_effort"],
    extra_body=R["extra_body"],
    enable_tool_filtering=False,   # OpenAI 固有の tool_choice 形式を送らない
    system_prompt="",
    debug=flag("AVATAR_DEBUG", "0"),
)
llm.serialize = R["serialize"]


# ---- 長期記憶: キャラクターごとに別の DB ----
class CharacterMemory(AvatarMemory):
    """アバター自身の記憶（感情・出来事）。いま選ばれているキャラクターの DB を使う。DB は空で始まる。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.prompt_memories = 8
        self.mood_window = 24 * 3600
        self.max_snapshots = 100
        self._snapshots = {}
        self._ready = set()
        self.db_path = str(self.data_dir)

    def _cid(self) -> str:
        return current_character()["id"]

    def _connect(self) -> sqlite3.Connection:
        cid = self._cid()
        con = sqlite3.connect(self.data_dir / f"memory_{cid}.db", timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        if cid not in self._ready:
            con.executescript(SCHEMA)
            self._ready.add(cid)
        return con

    def prompt_section(self, user_id: str, context_id: str) -> str:
        return super().prompt_section(user_id, f"{self._cid()}:{context_id}")


avatar_memory = CharacterMemory(BASE_DIR / "data")

if R["tools"]:
    llm.add_tool(WeatherTool(default_location=settings().get("weather_location", "東京")).create_tool())
    for tool in avatar_memory.create_tools():
        llm.add_tool(tool)


# キャラクターの設定と記憶は会話（context_id）の最初に固定されるので、会話中は LLM の会話キャッシュが効く
@llm.get_system_prompt
async def get_system_prompt(context_id, user_id, system_prompt_params):
    char = current_character()
    base = ss.compose_system_prompt(char, ROOT)
    if not R["tools"]:
        return base
    return base + "\n\n# 記憶\n" + avatar_memory.prompt_section(user_id, context_id)


# ---- TTS: Irodori-TTS-Server。声と num_steps は毎回設定を読むので、再起動なしで切り替わる ----
def make_tts_request(text: str, style_info: dict = None, language: str = None) -> httpx.Request:
    s = settings()
    try:
        voice = ss.effective_voice(s, current_character())
    except Exception:
        voice = s["tts"]["voice"]
    irodori = {"num_steps": int(s["tts"]["num_steps"])}
    if s["tts"].get("seed") is not None:
        irodori["seed"] = int(s["tts"]["seed"])
    body = {"model": "irodori-tts", "input": text, "voice": voice, "response_format": "wav", "irodori": irodori}
    return httpx.Request("POST", os.environ["TTS_BASE_URL"].rstrip("/") + "/audio/speech", json=body)


tts = create_instant_synthesizer(request_maker=make_tts_request, timeout=120)

# ---- STT: ローカルの OpenAI 互換サーバー ----
stt = OpenAISpeechRecognizer(
    openai_api_key="none",
    base_url=os.environ["STT_BASE_URL"],
    model=os.getenv("STT_MODEL", "small"),
    language="ja",
    timeout=60,
)

aiavatar_app = AIAvatarWebSocketServer(
    llm=llm,
    tts=tts,
    stt=stt,
    voice_recorder_enabled=flag("VOICE_RECORDER", "0"),   # 既定ではマイク音声を保存しない
    debug=flag("AVATAR_DEBUG", "0"),
)


# 計測の記録（aiavatar.db）にも画像の中身（data URL）を残さない
_record = aiavatar_app.sts.performance_recorder.record


def _record_without_images(performance):
    if not ss.resolve_llm(CFG, settings(), ROOT).get("store_images") and performance.request_files:
        performance.request_files = "[画像は保存しません]" if "image" in performance.request_files or "data:" in performance.request_files else performance.request_files
    return _record(performance)


aiavatar_app.sts.performance_recorder.record = _record_without_images


# 応答ごとに表情を感情ログへ記録する（LLM は呼ばない）
@aiavatar_app.sts.on_finish
async def log_emotion(request, response):
    if request.text and not request.text.startswith("$") and R["tools"]:
        avatar_memory.log_turn(request.user_id, request.context_id, request.text, response.text)


app = FastAPI()
app.include_router(aiavatar_app.get_websocket_router())  # 既定パスは /ws
app.include_router(make_router(BASE_DIR / "settings_ui" / "index.html"))   # /settings と /character-image/

# 付属のブラウザ UI（setup.py が html/ に配置する）
if not (BASE_DIR / "html").exists():
    raise SystemExit("html/ がありません。setup.bat を再実行してください。")
app.mount("/static", StaticFiles(directory="html"), name="static")

# 管理画面（/admin）：計測・ログ・設定変更
setup_admin_panel(app, adapter=aiavatar_app)

# 127.0.0.1 以外からの接続、ループバック以外の Host / Origin を弾く（HTTP・WebSocket 共通）
app.add_middleware(LocalOnlyMiddleware)
