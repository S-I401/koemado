"""ユーザー設定（config/settings.json）とキャラクター（characters/<id>/character.yaml）の読み書き。

- 設定画面（/settings）、configure.bat、start.bat（ctl.py）、run.py が共有する。
- config.json は setup.py が作る低レベルの値（ポート・GPU・導入済みバージョン）。
  config/settings.json はユーザーが変える値（声・LLM・キャラクター）。
- キャラクターの年齢は任意。ただし ESP（拒否の抑制）は、使用中のキャラの年齢が 18 歳以上のときだけ有効にできる（esp_character_block）。
  すべてのキャラに共通する禁止事項は characters/_common.md に書く（キャラ設定では消せない）。
"""
import copy
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import hf_hub
import profiles
import stt_engine
import tts_engine

ROOT = Path(__file__).resolve().parent

REASONING_MODES = {
    "effort": "reasoning_effort=none を送る",
    "template": "chat_template_kwargs {enable_thinking: false} を送る",
    "both": "両方を送る",
    "none": "何も送らない",
}

EXTERNAL_PRESETS = {
    "strata": {
        "label": "Strata",
        "base_url": "http://127.0.0.1:8080/v1",
        "reasoning": "effort", "serialize": True, "tools": True,
        "note": "Strata は別ソフトウェアです（ライセンスは Strata 側）。1 リクエストずつしか処理せず、VRAM 12GB 以上・RAM 48GB 以上・ディスク約 80GB が必要です。起動時に 1〜3 分 PC が重くなります。",
    },
    "lmstudio": {
        "label": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "reasoning": "none", "serialize": True, "tools": True,
        "note": "LM Studio でモデルを読み込み、Developer タブの Local Server を開始してください。",
    },
    "ollama": {
        "label": "Ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "reasoning": "effort", "serialize": True, "tools": True,
        "note": "モデルは ollama pull で用意してください。モデル名は一覧から選べます。",
    },
    "vllm": {
        "label": "vLLM",
        "base_url": "http://127.0.0.1:8000/v1",
        "reasoning": "template", "serialize": True, "tools": True,
        "note": "tool calling を使うには、vLLM の起動時に --enable-auto-tool-choice と --tool-call-parser が必要です。",
    },
    "remote": {
        "label": "別 PC の llama-server など",
        "base_url": "http://192.168.0.10:8080/v1",
        "reasoning": "effort", "serialize": False, "tools": True,
        "note": "別の PC で推論するので、直列化はオフでかまいません。通信は暗号化されません（家庭内ネットワーク向け）。",
    },
    "cloud": {
        "label": "クラウド API（OpenAI 互換）",
        "base_url": "https://api.openai.com/v1",
        "reasoning": "none", "serialize": False, "tools": True,
        "note": "会話の内容が外部のサービスに送られます。API キーが必要です。",
    },
}

DEFAULT_SETTINGS = {
    "version": 1,
    "llm": {
        "mode": "bundled",            # bundled / external
        "profile": "",                # 空 = 導入時のプロファイル。high / mid / low / custom
        # hf: repo + file（分割なら files に全パーツ。先頭が -m に渡るファイル）。size_bytes / kind(dense|moe) / moe_layers はモデルブラウザが入れる
        "custom": {"source": "hf", "repo": "", "file": "", "path": "", "files": [], "size_bytes": 0, "kind": "", "moe_layers": 0},
        "previous": None,             # モデルブラウザで切り替える前の {profile, custom}。「元のモデルに戻す」用
        "ctx": 0,                     # 0 = プロファイルの既定
        "temperature": 0.8,
        "thinking": False,            # True = モデルの既定どおり思考させる（遅くなる）
        "external": {
            "preset": "",
            "base_url": "",
            "api_key": "",
            "model": "",
            "reasoning": "effort",
            "tools": True,
            "serialize": True,
            "managed": "",                # "strata" = インストール先の strata/ を start.bat が起動する
        },
        # Strata の experimental speed projection（拒否の抑制）。名前と違って高速化ではない。既定はオフ
        "esp": {"enabled": False},
        # 画像認識。enabled = アプリが画像を受け付けて LLM に渡す。store_images = 会話の記録に画像を残す（既定は残さない）
        "vision": {"enabled": False, "store_images": False},
    },
    # 音声合成。engine: auto（speech.cpp が使えれば speech.cpp、使えなければ Python 版）/ speechcpp / python。
    # model: irodori-tts-mf（MeanFlow。mf_steps = 4 が標準で速い）/ irodori-tts（Rectified Flow。num_steps で品質と速さを選ぶ。Python 版と同じモデル）。
    # num_steps は Python 版と irodori-tts（rf）の steps。モデル・エンジンの変更は TTS の再起動で反映、声と steps と seed は次の発話から
    "tts": {"voice": "mio_bright", "num_steps": 24, "seed": 42, "engine": "auto", "model": tts_engine.DEFAULT_MODEL, "mf_steps": 4},
    # 音声認識。engine: auto（speech.cpp が使えれば speech.cpp、使えなければ faster-whisper）/ speechcpp / faster-whisper。
    # model: parakeet-ja / qwen3-asr-0.6b / qwen3-asr-1.7b（speech.cpp のとき）。device: auto（プロファイルの既定）/ gpu / cpu。
    # hints: よく使う言葉（固有名詞など。qwen3-asr のときだけ prompt として渡す）。モデル・デバイス・エンジンの変更は STT の再起動で反映、hints は次の発話から
    "stt": {"engine": "auto", "model": stt_engine.DEFAULT_MODEL, "device": "auto", "hints": ""},
    # Hugging Face のトークン（任意。config/settings.json にだけ保存。画面・ログ・報告には出さない）
    "hf": {"token": ""},
    "character": "mio",
    # 会話画面のアバターの動き。none / subtle（控えめ。既定）/ large（大きめ）。画像 1 枚・シルエットのキャラ向けの軽いアニメーション
    "avatar": {"motion": "subtle"},
    # 表示方法。mode: mascot（デスクトップのキャラ。既定）/ browser（ブラウザのタブ）/ both
    "display": {"mode": "mascot"},
    # デスクトップのキャラ。size: small / medium / large。x, y = ウィンドウの下辺の中央（画面の物理ピクセル。null = 右下）。
    # talk: hold（キャラを押している間だけ聞く）/ hotkey（ショートカットキーで 1 回聞く）/ always（常に聞く）
    "mascot": {"size": "medium", "x": None, "y": None, "always_on_top": True, "talk": "hold", "hotkey": "ctrl+alt+space",
               "interrupt": False, "volume": 1.0, "bubble_seconds": 8},
    "weather_location": "東京",
    # ツール。変更は「保存して再起動」で反映する（ツールの定義は起動時に固定し、LLM の会話キャッシュを守る）
    "tools": {
        "web_search": True,            # web_search / web_fetch（DuckDuckGo。検索語は外部に送られる）
        "weather": True,
        "memory": True,
        "mcp": True,                   # config/mcp.json のサーバーを使う
        "skills": True,                # skills/ のスキルを使う
        "skills_disabled": [],         # 個別にオフにしたスキル名
        # 外部エージェント（既定オフ）。kind: hermes / openclaw
        "agent": {"enabled": False, "kind": "hermes", "base_url": "http://127.0.0.1:8642/v1", "api_key": "", "timeout": 600},
    },
}

DISPLAY_MODES = {"mascot": "デスクトップのキャラ", "browser": "ブラウザ（従来どおり）", "both": "両方"}
MASCOT_SIZES = {"small": "小", "medium": "中", "large": "大"}
MASCOT_TALK = {"hold": "キャラを押している間だけ聞く", "hotkey": "ショートカットキー", "always": "常に聞く"}
HOTKEY_RE = re.compile(r"^(?:(?:ctrl|alt|shift|win)\+){1,3}(?:[a-z0-9]|f(?:[1-9]|1[0-9]|2[0-4])|space|enter|tab|esc|home|end|insert|delete)$")

MOTION_LEVELS = {"none": "なし", "subtle": "控えめ", "large": "大きめ"}
MOUTH_SHAPES = ["closed", "half", "open", "e", "u"]
EXPRESSION_NAMES = ["neutral", "joy", "angry", "sorrow", "fun", "surprised", "eyes_closed"]

AGENT_KINDS = {"hermes": "Hermes Agent", "openclaw": "OpenClaw"}


# ------------------------------------------------------------------ 読み書き ----

def settings_path(root: Path = ROOT) -> Path:
    return root / "config" / "settings.json"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_cache = {}


def load_settings(root: Path = ROOT) -> dict:
    p = settings_path(root)
    try:
        mtime = p.stat().st_mtime_ns
    except FileNotFoundError:
        return copy.deepcopy(DEFAULT_SETTINGS)
    hit = _cache.get(str(p))
    if hit and hit[0] == mtime:
        return copy.deepcopy(hit[1])
    data = _merge(DEFAULT_SETTINGS, json.loads(p.read_text(encoding="utf-8-sig")))      # メモ帳で保存した BOM 付きでも読める
    _cache[str(p)] = (mtime, data)
    return copy.deepcopy(data)


def save_settings(settings: dict, root: Path = ROOT):
    p = settings_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def update_settings(patch: dict, root: Path = ROOT) -> dict:
    """部分更新。api_key が空文字や伏せ字のときは、今の値を残す"""
    cur = load_settings(root)
    patch = copy.deepcopy(patch)
    ext = (patch.get("llm") or {}).get("external")
    if ext is not None and (ext.get("api_key") in (None, "") or set(ext.get("api_key", "")) <= {"●", "*"}):
        ext.pop("api_key", None)
    hf = patch.get("hf")
    if hf is not None:
        tok = hf.get("token")
        if hf.pop("clear_token", False):
            hf["token"] = ""
        elif tok in (None, "") or set(tok) <= {"●", "*"}:
            hf.pop("token", None)
        elif not hf_hub.valid_token(tok.strip()):
            raise ValueError("Hugging Face のトークンの形式が違います（hf_ で始まる英数字です）。")
        else:
            hf["token"] = tok.strip()
    ag = (patch.get("tools") or {}).get("agent")
    if ag is not None and "api_key" in ag and (ag.get("api_key") in (None, "") or set(ag.get("api_key", "")) <= {"●", "*"}):
        if not ag.pop("clear_api_key", False):
            ag.pop("api_key", None)
        else:
            ag["api_key"] = ""
    if ag is not None:
        ag.pop("clear_api_key", None)
    new = _merge(cur, patch)
    validate_settings(new)
    if new["llm"].get("esp", {}).get("enabled"):
        touched = (patch.get("llm") or {}).get("esp", {}).get("enabled") is True or "character" in patch
        if touched:
            why = esp_character_block(_character_for(new, root))
            if why:
                raise EspCharacterError(why)
    save_settings(new, root)
    return new


def validate_settings(s: dict):
    t = s["tts"]
    t["num_steps"] = int(t["num_steps"])
    if not 4 <= t["num_steps"] <= 64:
        raise ValueError("num_steps は 4〜64 の範囲で指定してください。")
    if t.get("seed") in ("", None):
        t["seed"] = None
    else:
        t["seed"] = int(t["seed"])
    stt = s["stt"]
    if stt.get("engine") not in stt_engine.ENGINES:
        raise ValueError("音声認識のエンジンは auto / speechcpp / faster-whisper のどれかです。")
    if stt.get("model") not in stt_engine.MODELS:
        raise ValueError("音声認識のモデルは " + " / ".join(stt_engine.MODELS) + " のどれかです。")
    if stt.get("device") not in stt_engine.DEVICES:
        raise ValueError("音声認識のデバイスは auto / gpu / cpu のどれかです。")
    stt["hints"] = str(stt.get("hints") or "").strip()
    if len(stt["hints"]) > stt_engine.HINT_MAX_CHARS:
        raise ValueError(f"よく使う言葉は {stt_engine.HINT_MAX_CHARS} 文字以内にしてください。")
    if t.get("engine") not in tts_engine.ENGINES:
        raise ValueError("音声合成のエンジンは auto / speechcpp / python のどれかです。")
    if t.get("model") not in tts_engine.TTS_MODELS:
        raise ValueError("音声合成のモデルは " + " / ".join(tts_engine.TTS_MODELS) + " のどれかです。")
    t["mf_steps"] = int(t.get("mf_steps") or 4)
    if not 1 <= t["mf_steps"] <= 16:
        raise ValueError("mf_steps は 1〜16 の範囲で指定してください。")
    llm = s["llm"]
    llm["temperature"] = float(llm["temperature"])
    if not 0 <= llm["temperature"] <= 2:
        raise ValueError("temperature は 0〜2 の範囲で指定してください。")
    llm["ctx"] = int(llm.get("ctx") or 0)
    if llm["ctx"] and not 1024 <= llm["ctx"] <= 131072:
        raise ValueError("コンテキスト長は 1024〜131072 で指定してください。")
    if llm["mode"] not in ("bundled", "external"):
        raise ValueError("LLM の方式は bundled か external です。")
    c = llm["custom"]
    if c.get("kind") not in ("", "dense", "moe"):
        raise ValueError("モデルの種類が不正です。")
    c["size_bytes"] = int(c.get("size_bytes") or 0)
    c["moe_layers"] = int(c.get("moe_layers") or 0)
    if not isinstance(c.get("files"), list) or not all(isinstance(f, str) and hf_hub.GGUF_FILE_RE.match(f) and ".." not in f for f in c["files"]):
        raise ValueError("ファイル名が不正です。")
    if s["avatar"].get("motion") not in MOTION_LEVELS:
        raise ValueError("アバターの動きは none / subtle / large のどれかです。")
    if s["display"].get("mode") not in DISPLAY_MODES:
        raise ValueError("表示方法は mascot / browser / both のどれかです。")
    m = s["mascot"]
    if m.get("size") not in MASCOT_SIZES:
        raise ValueError("キャラの大きさは small / medium / large のどれかです。")
    if m.get("talk") not in MASCOT_TALK:
        raise ValueError("話しかけ方は hold / hotkey / always のどれかです。")
    m["hotkey"] = str(m.get("hotkey") or "").strip().lower().replace(" ", "")
    if not HOTKEY_RE.match(m["hotkey"]):
        raise ValueError("ショートカットキーは ctrl+alt+space のように、Ctrl / Alt / Shift / Win のどれかと 1 つのキーで指定してください。")
    for k in ("x", "y"):
        m[k] = None if m.get(k) in (None, "") else int(m[k])
    m["always_on_top"] = bool(m.get("always_on_top"))
    m["interrupt"] = bool(m.get("interrupt"))
    m["volume"] = float(m.get("volume", 1.0))
    if not 0 <= m["volume"] <= 1:
        raise ValueError("キャラの音量は 0〜1 で指定してください。")
    m["bubble_seconds"] = int(m.get("bubble_seconds") or 8)
    if not 2 <= m["bubble_seconds"] <= 60:
        raise ValueError("吹き出しを出す秒数は 2〜60 で指定してください。")
    tl = s["tools"]
    for k in ("web_search", "weather", "memory", "mcp", "skills"):
        tl[k] = bool(tl.get(k))
    if not isinstance(tl.get("skills_disabled"), list):
        tl["skills_disabled"] = []
    ag = tl["agent"]
    ag["enabled"] = bool(ag.get("enabled"))
    if ag.get("kind") not in AGENT_KINDS:
        raise ValueError("外部エージェントの種類は hermes か openclaw です。")
    ag["timeout"] = int(ag.get("timeout") or 600)
    if not 10 <= ag["timeout"] <= 3600:
        raise ValueError("外部エージェントのタイムアウトは 10〜3600 秒で指定してください。")
    if ag["enabled"]:
        u = urlparse(ag.get("base_url", ""))
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError("外部エージェントの base_url は http(s)://host:port/v1 の形式で指定してください。")
    if llm["mode"] == "external":
        ext = llm["external"]
        u = urlparse(ext["base_url"])
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError("外部 LLM の base_url は http(s)://host:port/v1 の形式で指定してください。")
        if ext["reasoning"] not in REASONING_MODES:
            raise ValueError("思考を止める方法が不正です。")


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def is_cloud_url(url: str) -> bool:
    """家庭内（ループバック・プライベート IP）以外 = 会話が外部に送られる"""
    host = (urlparse(url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1") or host.endswith(".local"):
        return False
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)\.(\d+)$", host)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return not (a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or a == 127 or (a == 169 and b == 254))
    return True


def masked(s: dict) -> dict:
    """画面・ログ向け: API キーを伏せる"""
    s = copy.deepcopy(s)
    ext = s["llm"]["external"]
    ext["api_key_set"] = bool(ext.get("api_key"))
    ext["api_key"] = ("●●●●●●●●" if ext.get("api_key") else "")
    ag = s["tools"]["agent"]
    ag["api_key_set"] = bool(ag.get("api_key"))
    ag["api_key"] = ("●●●●●●●●" if ag.get("api_key") else "")
    hf = s.setdefault("hf", {})
    hf["token_set"] = bool(hf.get("token"))
    hf["token"] = ("●●●●●●●●" if hf.get("token") else "")
    return s


def hf_token(root: Path = ROOT) -> str:
    """HF のトークン（環境変数 HF_TOKEN が優先。無ければ設定済みのもの）。送信先は hf_hub が huggingface.co だけに限る"""
    import os
    env = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    if hf_hub.valid_token(env):
        return env
    try:
        return load_settings(root).get("hf", {}).get("token") or ""
    except Exception:
        return ""


# ------------------------------------------------------------------ LLM の実効設定 ----

def hf_model_rel_path(repo: str, file: str, root: Path = ROOT) -> str:
    """HF からダウンロードしたモデルの置き場所（インストール先からの相対）。リポジトリごとのフォルダに分ける
    （別のリポジトリに同じファイル名があっても混ざらない）。以前の置き場所 models/<ファイル名> に既にあればそちらを使う"""
    legacy = Path("models") / Path(file).name
    new = Path("models") / repo.replace("/", "--") / file
    if not (root / new).exists() and (root / legacy).exists():
        return str(legacy)
    return str(new)


def resolve_llm(cfg: dict, settings: dict, root: Path = ROOT) -> dict:
    """config.json（導入時の値）と settings.json から、実際に使う LLM 設定を作る"""
    llm = settings["llm"]
    temperature = llm["temperature"]
    if llm["mode"] == "external":
        ext = llm["external"]
        effort, template = {"effort": ("none", False), "template": (None, True),
                            "both": ("none", True), "none": (None, False)}[ext["reasoning"]]
        extra = {"chat_template_kwargs": {"enable_thinking": False}} if template else {}
        if ext.get("preset") == "strata":
            # Strata は、ベクトルを読み込んで起動したときだけ ESP を持つ。読み込み済みなら、リクエストごとに
            # true / false で切り替えられる（false = 素のモデルと同じ出力）。オフのとき（キャラクターが 18 歳以上と確認できないときも）false を送り、勝手に効かないようにする
            extra["experimental_speed_projection"] = esp_effective(settings, root)
        return {
            "mode": "external",
            "base_url": ext["base_url"].rstrip("/"),
            "api_key": ext.get("api_key") or "none",
            "model": ext.get("model") or "",
            "temperature": temperature,
            "reasoning_effort": effort,
            "extra_body": extra or None,
            "vision": bool(llm.get("vision", {}).get("enabled")),
            "store_images": bool(llm.get("vision", {}).get("store_images")),
            "tools": bool(ext.get("tools", True)),
            "serialize": bool(ext.get("serialize", True)),
        }
    profile = llm.get("profile") or cfg.get("profile") or "mid"
    vram = cfg["gpu"]["vram_mib"]
    if profile == "custom":
        c = llm["custom"]
        if c.get("source") == "local" and c.get("path"):
            model_path = c["path"]
        else:
            first = (c.get("files") or [c.get("file", "")])[0]
            model_path = hf_model_rel_path(c.get("repo", ""), first, root)
        mode = profiles.PROFILES.get(cfg.get("profile"), {}).get("llm_mode", "fit")
        built = profiles.custom_llm_settings(vram, c.get("size_bytes") or None, c.get("kind") or "", c.get("moe_layers") or None, mode)
        ram = cfg.get("ram_total_gib")
        ctx = profiles.custom_ctx(c.get("size_bytes") or None, profiles.hw_cap_gib(vram, ram) if ram else None)
    else:
        p = profiles.PROFILES[profile]
        model_path = str(Path("models") / p["model"]["file"])
        built = profiles.llm_settings(profile, vram)
        ctx = p["ctx"]
    think = llm.get("thinking", False)
    return {
        "mode": "bundled",
        "profile": profile,
        "base_url": f"http://127.0.0.1:{cfg['ports']['llm']}/v1",
        "api_key": "none",
        "model": "local",
        "temperature": temperature,
        "reasoning_effort": None if think else "none",
        "extra_body": None if think else {"chat_template_kwargs": {"enable_thinking": False}},
        "tools": True,
        "serialize": True,
        "vision": False,
        "store_images": False,
        "model_path": model_path,
        "ctx": llm.get("ctx") or ctx,
        "extra_args": built["args"],
        "fit_target_mib": built["fit_target_mib"],
    }


# ------------------------------------------------------------------ 声 ----

AUDIO_EXTS = (".wav", ".flac", ".mp3", ".ogg", ".opus")
REJECTED_EXTS = (".m4a", ".aac", ".webm")


def voices_dir(root: Path = ROOT) -> Path:
    return root / "tts" / "voices"


def voices_meta_path(root: Path = ROOT) -> Path:
    return root / "config" / "voices_meta.json"


def load_voices_meta(root: Path = ROOT) -> dict:
    try:
        return json.loads(voices_meta_path(root).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_voices_meta(meta: dict, root: Path = ROOT):
    p = voices_meta_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def list_voices(root: Path = ROOT) -> list[dict]:
    meta = load_voices_meta(root)
    out = []
    d = voices_dir(root)
    if d.exists():
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                out.append({"id": f.stem, "file": f.name, "size": f.stat().st_size, **meta.get(f.stem, {})})
    return out


def valid_voice_id(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", name or ""))


# ------------------------------------------------------------------ キャラクター ----

CHAR_FIELDS = ["name", "age", "first_person", "second_person", "speech_style", "personality",
               "likes", "dislikes", "background", "relationship", "examples", "voice"]


_KANJI_NUM = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _kanji_to_int(s: str):
    if not s:
        return None
    if s == "十":
        return 10
    if "十" in s:
        head, _, tail = s.partition("十")
        h = _KANJI_NUM.get(head, 1) if head else 1
        t = _KANJI_NUM.get(tail, 0) if tail else 0
        return h * 10 + t
    n = 0
    for ch in s:
        if ch not in _KANJI_NUM:
            return None
        n = n * 10 + _KANJI_NUM[ch]
    return n


def parse_age(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    m = re.fullmatch(r"(\d{1,4})(?:\s*歳)?", text)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"([〇一二三四五六七八九十]{1,4})\s*歳?", text)
    if m:
        return _kanji_to_int(m.group(1))
    return None


def validate_character(data: dict) -> list[str]:
    """保存してよいか調べる。問題があれば理由のリストを返す（空なら OK）。年齢は任意（入れるなら数字）。"""
    errors = []
    if not str(data.get("name") or "").strip():
        errors.append("名前は必須です。")
    if data.get("age") not in (None, "") and parse_age(data.get("age")) is None:
        errors.append("年齢は数字で入力してください（例: 28）。空欄でもかまいません。")
    return errors


# ---- ESP（拒否の抑制）とキャラクターの年齢 ----

ESP_AGE_MIN = 18


class EspCharacterError(ValueError):
    """ESP がオンのまま、18 歳以上と確認できないキャラクターを使おうとした"""


def esp_character_block(char: dict | None) -> str:
    """ESP を使えない理由（使えるなら空文字）。年齢が 18 歳以上と設定されているキャラクターだけ使える"""
    if not char:
        return "使用中のキャラクターを読み込めないため、年齢を確認できません。"
    age = parse_age(char.get("age"))
    name = char.get("name") or char.get("id") or "このキャラクター"
    if age is None:
        return f"「{name}」は年齢が未設定です。拒否の抑制（ESP）は、年齢が 18 歳以上と設定されたキャラクターでだけ使えます。"
    if age < ESP_AGE_MIN:
        return f"「{name}」は {age} 歳です。拒否の抑制（ESP）は、年齢が 18 歳以上のキャラクターでだけ使えます。"
    return ""


def _character_for(settings: dict, root: Path) -> dict | None:
    try:
        return active_character(settings, root)
    except Exception:
        return None


def esp_effective(settings: dict, root: Path = ROOT) -> bool:
    """実際に ESP を有効にするか。設定がオンでも、キャラクターが条件を満たさなければ無効"""
    if not settings.get("llm", {}).get("esp", {}).get("enabled"):
        return False
    return not esp_character_block(_character_for(settings, root))


def characters_dir(root: Path = ROOT) -> Path:
    return root / "characters"


def valid_char_id(cid: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_\-]{0,30}", cid or ""))


def _yaml():
    import yaml
    return yaml


def load_character(cid: str, root: Path = ROOT) -> dict:
    f = characters_dir(root) / cid / "character.yaml"
    data = _yaml().safe_load(f.read_text(encoding="utf-8")) or {}
    data["id"] = cid
    return data


def list_characters(root: Path = ROOT) -> list[dict]:
    out = []
    d = characters_dir(root)
    if not d.exists():
        return out
    for sub in sorted(d.iterdir()):
        if sub.is_dir() and (sub / "character.yaml").exists() and not sub.name.startswith("_"):
            try:
                c = load_character(sub.name, root)
                out.append({"id": sub.name, "name": c.get("name"), "age": c.get("age"), "voice": c.get("voice"),
                            "has_image": (sub / "image.png").exists(),
                            "esp_block": esp_character_block(c)})
            except Exception as e:
                out.append({"id": sub.name, "name": f"(読み込み失敗: {e})", "error": True})
    return out


def save_character(cid: str, data: dict, root: Path = ROOT) -> Path:
    if not valid_char_id(cid):
        raise ValueError("キャラクター ID は半角の英小文字・数字・_・- で、31 文字以内にしてください。")
    errors = validate_character(data)
    if errors:
        raise ValueError(" / ".join(errors))
    cur = load_settings(root)
    if cur["llm"].get("esp", {}).get("enabled") and cid == (cur.get("character") or "mio"):
        why = esp_character_block({"id": cid, "name": data.get("name"), "age": data.get("age")})
        if why:
            raise EspCharacterError(why + " 先に ESP をオフにしてください。")
    clean = {}
    for k in CHAR_FIELDS:
        v = data.get(k)
        if k == "age":
            v = parse_age(v)
        if k == "examples":
            v = [str(x).strip() for x in (v or []) if str(x).strip()]
        if v not in (None, "", []):
            clean[k] = v
    folder = characters_dir(root) / cid
    folder.mkdir(parents=True, exist_ok=True)
    text = _yaml().safe_dump(clean, allow_unicode=True, sort_keys=False, default_flow_style=False)
    (folder / "character.yaml").write_text(text, encoding="utf-8")
    return folder


def _drop_sections(text: str, titles: set[str]) -> str:
    """_common.md から、「# 見出し」ごとの節を取り除く（使わないツールの説明を LLM に見せないため）"""
    parts = re.split(r"(?m)^(?=# )", text)
    keep = [p for p in parts if not (p.startswith("# ") and p[2:].split("\n", 1)[0].strip() in titles)]
    return "".join(keep).rstrip() + "\n"


def compose_system_prompt(char: dict, root: Path = ROOT, tools: dict | None = None) -> str:
    """character.yaml から system_prompt を作る。内容は設定を変えない限り同じ文字列になる（プロンプトキャッシュ向け）。
    tools: 使えるツールの on/off（{"weather": bool, "memory": bool}）。オフのツールの節は入れない。"""
    common = (characters_dir(root) / "_common.md").read_text(encoding="utf-8")
    if tools is not None:
        drop = {t for t, k in (("天気", "weather"), ("記憶", "memory")) if not tools.get(k)}
        if drop:
            common = _drop_sections(common, drop)
    age = parse_age(char.get("age"))
    lines = [f"あなたは「{char.get('name', '')}」という名前のキャラクターです。"
             "ユーザーと音声で雑談する相手として話します。返答は音声で読み上げられます。", "", "# キャラクター設定"]
    if age is not None:
        lines.append(f"- 年齢: {age} 歳")
    rows = [("一人称", "first_person"), ("ユーザーの呼び方", "second_person"), ("口調", "speech_style"),
            ("性格", "personality"), ("好きなもの", "likes"), ("苦手なもの", "dislikes"), ("ユーザーとの関係", "relationship")]
    for label, key in rows:
        if char.get(key):
            lines.append(f"- {label}: {str(char[key]).strip()}")
    if char.get("background"):
        lines += ["", "# 生い立ち・経歴", str(char["background"]).strip()]
    if char.get("examples"):
        lines += ["", "# 話し方の例"] + [f"- {e}" for e in char["examples"]]
    return "\n".join(lines) + "\n\n" + common


def active_character(settings: dict, root: Path = ROOT) -> dict:
    cid = settings.get("character") or "mio"
    try:
        return load_character(cid, root)
    except Exception:
        chars = list_characters(root)
        ok = [c for c in chars if not c.get("error")]
        if not ok:
            raise RuntimeError("characters/ にキャラクターがありません。")
        return load_character(ok[0]["id"], root)


def effective_voice(settings: dict, char: dict | None) -> str:
    return (char or {}).get("voice") or settings["tts"]["voice"]


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
