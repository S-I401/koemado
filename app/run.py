"""AIAvatarKit 本体: LLM / TTS / STT / ツール / 記憶 / 設定画面を組み立てる。

- 低レベルの値（ポート）は同じフォルダの .env、導入時の値は ../config.json
- 声・LLM・キャラクターは ../config/settings.json（設定画面 /settings か configure.bat で変える）
  声・キャラクター・temperature は、次の発話から反映される
"""

import asyncio
import contextlib
import logging
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
import stt_engine  # noqa: E402
import tools_store as ts  # noqa: E402
from avatar_memory import SCHEMA, AvatarMemory  # noqa: E402
from settings_api import RUNTIME, LocalOnlyMiddleware, make_router  # noqa: E402
from weather_tool import WeatherTool  # noqa: E402

logger = logging.getLogger("avatar")


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

# ---- ツール: 起動時に固定する（会話の途中で定義が変わると、LLM の会話キャッシュが効かなくなる）。
#      設定画面の「ツール」タブの変更は、「保存して再起動」で反映する ----
TOOLS = settings()["tools"]
USE_TOOLS = bool(R["tools"])        # LLM が tool calling を使えるか（外部サーバーで「使わない」にしたときは全部オフ）
USE_WEATHER = USE_TOOLS and TOOLS["weather"]
USE_MEMORY = USE_TOOLS and TOOLS["memory"]
USE_WEB = USE_TOOLS and TOOLS["web_search"]
USE_AGENT = USE_TOOLS and TOOLS["agent"]["enabled"]
skill_index = ts.skill_index(ROOT, TOOLS["skills_disabled"]) if (USE_TOOLS and TOOLS["skills"]) else []
tool_prompt_sections: list[str] = []

if USE_WEATHER:
    llm.add_tool(WeatherTool(default_location=settings().get("weather_location", "東京")).create_tool())
if USE_MEMORY:
    for tool in avatar_memory.create_tools():
        llm.add_tool(tool)
if USE_WEB:
    import web_tools
    for tool in web_tools.create_tools():
        llm.add_tool(tool)
    tool_prompt_sections.append(
        "# Web 検索\n"
        "- 最新の情報や、自分の知識では分からないことを聞かれたときだけ web_search を使う。雑談や、知っていることには使わない。\n"
        "- 結果の本文が必要なときだけ web_fetch でページを読む。\n"
        "- 調べた結果は、話し言葉で短く要約して伝える。URL は読み上げない。出典はサイト名で言う。"
    )
if skill_index:
    import skills_tool
    llm.add_tool(skills_tool.create_tool(ROOT, skill_index))
    tool_prompt_sections.append(skills_tool.prompt_section(skill_index))
if USE_AGENT:
    import agent_tool
    for tool in agent_tool.create_tools(TOOLS["agent"], lambda: aiavatar_app, debug=flag("AVATAR_DEBUG", "0")):
        llm.add_tool(tool)
    tool_prompt_sections.append(
        "# 作業エージェント\n"
        "- 天気と簡単な検索は、自分のツール（get_weather, web_search）で済ませる。\n"
        "- ファイル操作やコード実行、長い調査など、時間のかかる作業だけを ask_agent で頼む。頼んだら「頼みました」と一言伝え、結果は通知で届く。\n"
        "- 作業の進み具合を聞かれたら check_agent_tasks で確認する。"
    )
TOOL_PROMPT = "\n\n".join(tool_prompt_sections)    # 起動中は変わらない文字列


# キャラクターの設定と記憶は会話（context_id）の最初に固定されるので、会話中は LLM の会話キャッシュが効く
@llm.get_system_prompt
async def get_system_prompt(context_id, user_id, system_prompt_params):
    char = current_character()
    base = ss.compose_system_prompt(char, ROOT, tools={"weather": USE_WEATHER, "memory": USE_MEMORY})
    parts = [base]
    if TOOL_PROMPT:
        parts.append(TOOL_PROMPT)
    if USE_MEMORY:
        parts.append("# 記憶\n" + avatar_memory.prompt_section(user_id, context_id))
    return "\n\n".join(parts)


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

# ---- STT: ローカルの OpenAI 互換サーバー（speech.cpp の `speech serve`、または従来の faster-whisper）----
class LocalSpeechRecognizer(OpenAISpeechRecognizer):
    """OpenAISpeechRecognizer に、prompt（キャラ名と、設定画面に登録した「よく使う言葉」）を送れるようにしたもの。

    AIAvatarKit 0.9.1 の OpenAISpeechRecognizer は model / language / file しか送らないので、transcribe を差し替える。
    - speech.cpp は、model を省略するか、起動中のモデルの id と同じにする必要がある（別の id は 404）。
      設定画面でモデルを切り替えたあとも合うように、model は送らない（起動中のモデルを使う）。
    - prompt を受け付けるのは qwen3-asr だけ（parakeet は拒否する）ので、そのときだけ送る。
    - 設定は毎回読む（hints の変更は次の発話から反映される。エンジン・モデル・デバイスは STT の再起動で反映される）。
    """

    def _form(self) -> dict:
        form = {}
        try:
            st = {**stt_engine.default_stt_settings(), **(settings().get("stt") or {})}
            r = stt_engine.resolve(CFG, settings(), ROOT, probe=False)
            if r["engine"] == "speechcpp":
                if r["model"]["hint"]:
                    try:
                        name = current_character().get("name", "")
                    except Exception:
                        name = ""
                    prompt = stt_engine.build_prompt(name, st["hints"])
                    if prompt:
                        form["prompt"] = prompt
            else:
                form["model"] = CFG.get("stt", {}).get("model", "small")
        except Exception:
            logger.exception("STT の設定を読めませんでした（既定で認識します）")
        return form

    async def transcribe(self, data: bytes) -> str:
        if len(data) < self.min_data_length:
            return None
        form_data = self._form()
        if self.language and not self.alternative_languages:
            form_data["language"] = self.language.split("-")[0] if "-" in self.language else self.language
        files = {"file": ("voice.wav", self.to_wave_file(data), "audio/wav")}
        resp = await self.http_request_with_retry(
            method="POST", url=f"{self.base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.openai_api_key}"}, data=form_data, files=files)
        try:
            return resp.json()["text"]
        except Exception:
            return None


stt = LocalSpeechRecognizer(
    openai_api_key="none",
    base_url=os.environ["STT_BASE_URL"],
    language="ja",
    timeout=60,
)

aiavatar_app = AIAvatarWebSocketServer(
    llm=llm,
    tts=tts,
    stt=stt,
    voice_recorder_enabled=flag("VOICE_RECORDER", "0"),   # 既定ではマイク音声を保存しない
    use_invoke_queue=USE_AGENT,    # 外部エージェントの完了報告を、発話の後ろに並べるため（連携がオンのときだけ）
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
    if request.text and not request.text.startswith("$") and USE_MEMORY:
        avatar_memory.log_turn(request.user_id, request.context_id, request.text, response.text)


@contextlib.asynccontextmanager
async def lifespan(_app):
    """MCP サーバーへの接続（起動時に一度だけ）。失敗したサーバーは飛ばす"""
    mcp = None
    if USE_TOOLS and TOOLS["mcp"]:
        from mcp_manager import McpManager
        try:
            servers = ts.load_mcp(ROOT)["mcpServers"]
        except ValueError as e:
            logger.warning("MCP の設定を読めませんでした（MCP は使いません）: %s", e)
            RUNTIME["mcp_config_error"] = str(e)
            servers = {}
        mcp = McpManager(ROOT)
        RUNTIME["mcp_status"] = mcp.status
        try:
            await mcp.start(llm, servers, set(llm.tools))
        except Exception:  # noqa: BLE001  何があってもアプリは起動する
            logger.exception("MCP の初期化に失敗しました（MCP は使いません）")
    extra = [n for st in RUNTIME["mcp_status"].values() for n in st.get("tools", [])]
    if extra:    # MCP のツールが 1 つでもあれば、使い方の一文を足す（起動時に一度だけ。以後は変わらない）
        global TOOL_PROMPT
        TOOL_PROMPT = (TOOL_PROMPT + "\n\n" if TOOL_PROMPT else "") + (
            "# 追加のツール\n- 依頼に合うツールがあるときは、推測や作り話で答えず、必ずそのツールを呼ぶ。ツール名を文章に書かない。")
    RUNTIME["tools"] = sorted(llm.tools)
    RUNTIME["skills"] = [s["name"] for s in skill_index]
    yield
    if mcp:
        await mcp.close()


app = FastAPI(lifespan=lifespan)
app.include_router(aiavatar_app.get_websocket_router())  # 既定パスは /ws
app.include_router(make_router(BASE_DIR / "settings_ui" / "index.html"))   # /settings と /character-image/

# 付属のブラウザ UI（setup.py が html/ に配置する）
if not (BASE_DIR / "html").exists():
    raise SystemExit("html/ がありません。setup.bat を再実行してください。")
# 付属 UI を koemado 向けに整える（取得元のサンプルの題名を置き換え、管理画面へのリンクを足す。何度実行しても同じ）
_html = BASE_DIR / "html"
try:
    _t = (_html / "index.html").read_text(encoding="utf-8")
    _t2 = _t.replace("AIAvatarKit WebSocket Example", "koemado")
    if "console-link.js" not in _t2:
        _t2 = _t2.replace("</body>", '    <script src="console-link.js"></script>\n</body>', 1)
    if _t2 != _t:
        (_html / "index.html").write_text(_t2, encoding="utf-8")
    # キャラクターごとの素材（口の差分・表情）と、軽いアニメーション: ImageAvatar を拡張した KoemadoImageAvatar に差し替える
    _t = (_html / "index.html").read_text(encoding="utf-8")
    _t3 = _t
    if "koemado-avatar.js" not in _t3:
        _t3 = _t3.replace('<script src="image-avatar.js"></script>', '<script src="image-avatar.js"></script>\n    <script src="koemado-avatar.js"></script>', 1)
    _t3 = _t3.replace("new ImageAvatar(", "new KoemadoImageAvatar(")
    if _t3 != _t:
        (_html / "index.html").write_text(_t3, encoding="utf-8")
    for _name in ("console-link.js", "koemado-avatar.js", "mascot.html", "mascot.css", "mascot.js", "mascot-silhouette.svg"):
        _src = ROOT / "html-overrides" / _name
        if _src.exists():
            (_html / _name).write_bytes(_src.read_bytes())
except OSError:
    pass
app.mount("/static", StaticFiles(directory="html"), name="static")

# 管理画面（/admin）：計測・ログ・設定変更
setup_admin_panel(app, adapter=aiavatar_app)

# 127.0.0.1 以外からの接続、ループバック以外の Host / Origin を弾く（HTTP・WebSocket 共通）
app.add_middleware(LocalOnlyMiddleware)
