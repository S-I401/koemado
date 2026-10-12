"""感情を Irodori の絵文字で声に乗せる（標準ライブラリのみ。app/run.py と設定画面が共有する）。

Irodori v4.1 は、入力文に絵文字を入れると、話し方・感情を制御できる（Aratako/Irodori-TTS-v4.1-Small の EMOJI_ANNOTATIONS.md）。
絵文字は TTS への入力にだけ入れる。会話画面の字幕・デスクトップのキャラの吹き出し・会話の記録（SQLite）・記憶には出さない。

方式 A: LLM が文の先頭に出す表情タグ（[face:joy] など。AIAvatarKit が styled_text に持つ）を、対応表で絵文字にして、TTS への入力の先頭に付ける。
方式 B: LLM が話し方の絵文字を文中に入れる（システムプロンプトに、使ってよい一覧を入れる）。使ってよいもの以外は、TTS に渡す前に取り除く。
LLM が文中に絵文字を入れた文には、方式 A の絵文字は付けない。
"""
import re

# ---- 方式 B: 使ってよい絵文字（話し方・感情・間に限る）と、LLM に見せる短い意味 ----
ALLOWED = {
    "🤭": "くすくす笑う", "😏": "からかう・甘える", "🥺": "声を震わせる・自信なさげ", "🫶": "優しく", "😭": "泣く・悲しむ", "😪": "眠そう・気だるげ",
    "⏸️": "間・沈黙", "😰": "慌てる・緊張", "😆": "喜ぶ", "😠": "怒る・拗ねる", "😲": "驚く", "😟": "心配する", "🫣": "照れる・恥ずかしがる",
    "🙄": "呆れる", "😊": "楽しげ・嬉しそう", "😎": "得意げ", "👌": "相槌・頷き", "🙏": "懇願する", "😌": "安堵・満足", "🤔": "疑問・考え込む",
    "😮‍💨": "ため息・吐息",
}
# 使わせない絵文字（出力に含まれていても、TTS に渡す前に取り除く。ALLOWED 以外はすべて取り除くので、これは明示と確認用）
FORBIDDEN = ["🥵", "👅", "💋", "🌬️", "🥤", "😴", "📢", "📞", "🤐", "👃", "🥴", "🎵", "📖", "👂", "😱", "💥", "⏩", "🐢", "🤧", "😒", "🥱", "😖", "😮", "💪"]

# ---- 方式 A: 表情タグ → 絵文字（値は候補のリスト。強さ 1 は先頭の 1 つ、強さ 2 は先頭の 2 つ（1 つしかなければ同じものを重ねる））----
DEFAULT_FACE_MAP = {
    "joy": ["😊"], "fun": ["😆"], "angry": ["😠"], "sorrow": ["🥺", "😭"], "surprised": ["😲"], "neutral": [],
}
FACE_NAMES = list(DEFAULT_FACE_MAP)
DEFAULT_CONFIG = {
    "face_emoji": {"enabled": True, "strength": 1, "map": DEFAULT_FACE_MAP},     # 方式 A
    "inline_emoji": {"enabled": True},                                          # 方式 B
}

_VS16, _ZWJ = "️", "‍"
# 絵文字 1 つ分（ZWJ でつないだ並び、肌の色、VS16 つき）。♪・☆・★ などの記号は、VS16 が付かない限り絵文字として扱わない
_BASE = ("[\U0001F000-\U0001FAFF⏩-⏺✅✨❌❓-❕❗❤⭐⭕⚡☔☕☺☹❄✊-✍]"
         "|[©®‼⁉™ℹ↔-↪⌚-⌛⌨⏏Ⓜ▪-◾☀-➿⤴-⤵⬅-⬇⬛-⬜〰〽㊗㊙](?=️)")
EMOJI_RE = re.compile(f"(?:{_BASE})️?[\U0001F3FB-\U0001F3FF]?(?:‍(?:{_BASE})️?[\U0001F3FB-\U0001F3FF]?)*")
FACE_RE = re.compile(r"\[face:(\w+)\]")


def _key(e: str) -> str:
    return e.replace(_VS16, "")


_ALLOWED_KEYS = {_key(e) for e in ALLOWED}


def merged_config(settings: dict | None, char: dict | None = None) -> dict:
    """設定（settings["voice_emotion"]）と、キャラクターの上書き（character.yaml の voice_emotion: {face: bool, inline: bool}）を合わせる"""
    src = (settings or {}).get("voice_emotion") or {}
    fe = {**DEFAULT_CONFIG["face_emoji"], **(src.get("face_emoji") or {})}
    fe["map"] = {**DEFAULT_FACE_MAP, **(fe.get("map") or {})}
    ie = {**DEFAULT_CONFIG["inline_emoji"], **(src.get("inline_emoji") or {})}
    ov = (char or {}).get("voice_emotion") or {}
    if isinstance(ov, dict):
        if ov.get("face") is not None:
            fe["enabled"] = bool(ov["face"])
        if ov.get("inline") is not None:
            ie["enabled"] = bool(ov["inline"])
    return {"face_emoji": fe, "inline_emoji": ie}


def normalize_map(raw: dict) -> dict:
    """対応表を整える（値は絵文字のリスト。文字列なら 1 つのリストにする）。絵文字でないものは捨てる"""
    out = {}
    for face, v in (raw or {}).items():
        if not re.fullmatch(r"\w{1,20}", str(face)):
            continue
        items = re.split(r"[\s,、]+", v) if isinstance(v, str) else list(v or [])
        keep = []
        for it in items:
            it = str(it).strip()
            if it and EMOJI_RE.fullmatch(it):
                keep.append(it)
        out[str(face)] = keep[:4]
    return out


def strip_all(text: str | None) -> str | None:
    """画面・記録・記憶に出す文から、絵文字をすべて取り除く"""
    if not text:
        return text
    return EMOJI_RE.sub("", text)


def keep_allowed(text: str) -> str:
    """TTS に渡す文: 使ってよい絵文字（ALLOWED）以外の絵文字を取り除く（使ってよいものは残す）"""
    return EMOJI_RE.sub(lambda m: m.group(0) if _key(m.group(0)) in _ALLOWED_KEYS else "", text)


def has_allowed(text: str) -> bool:
    return any(_key(m.group(0)) in _ALLOWED_KEYS for m in EMOJI_RE.finditer(text))


def parse_face(styled_text: str | None) -> str | None:
    m = FACE_RE.search(styled_text or "")
    return m.group(1) if m else None


def face_prefix(face: str | None, cfg: dict) -> str:
    fe = cfg["face_emoji"]
    if not fe.get("enabled") or not face:
        return ""
    cands = (fe.get("map") or {}).get(face) or []
    if not cands:
        return ""
    n = 2 if int(fe.get("strength") or 1) >= 2 else 1
    picked = cands[:n] if len(cands) >= n else cands * n
    return "".join(picked[:n])


def tts_text(text: str, style_info: dict | None, cfg: dict) -> str:
    """TTS に渡す文を作る。方式 B が切れているときは、絵文字をすべて取り除く。
    使ってよい絵文字を含む文には、方式 A の絵文字を付けない"""
    if not cfg["inline_emoji"].get("enabled"):
        text = strip_all(text) or ""
    else:
        text = keep_allowed(text or "")
    if has_allowed(text):
        return text
    prefix = face_prefix(parse_face((style_info or {}).get("styled_text")), cfg)
    return prefix + text if prefix else text


def prompt_section() -> str:
    """方式 B: システムプロンプトに入れる、使ってよい絵文字の一覧"""
    items = " ".join(f"{e}（{m}）" for e, m in ALLOWED.items())
    return ("# 話し方の絵文字\n"
            "- 声の話し方・感情・間を表すためだけに、次の絵文字を文中に入れてよい。画面には出ず、声にだけ反映される。\n"
            f"- 使ってよいもの：{items}\n"
            "- 1 文に 0〜2 個まで。必要なときだけ使い、多用しない。表情タグ（[face:...]）とは別に、声の調子を変えたい所に入れる。\n"
            "- この一覧にない絵文字は使わない。")
