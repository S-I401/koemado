"""音声合成（TTS）のエンジンとモデルの表、使えるかの判定、speech.cpp の起動コマンド（標準ライブラリのみ。
setup.py / ctl.py / supervisor.py / 設定画面 / app/run.py が共有する。speech.cpp の入れ方や Vulkan の判定は stt_engine.py と共通）。

エンジン:
  python     Irodori-TTS-Server（Python 3.10 + PyTorch CUDA。int8）。既定（声がいちばんミオらしいと、試聴で決めた）
  speechcpp  speech.cpp（MIT）の `speech serve`。Vulkan の GPU で動く。速く、VRAM が少なく、Python も PyTorch も不要。選べる
             動かせるモデル: Irodori-TTS（Small・Small MF・v4 Large。参照音声の声）と Qwen3-TTS（CustomVoice 0.6B・1.7B。固定の 9 話者）

設定（config/settings.json の "tts"）の engine は "auto" が既定: Python 版が導入済みなら Python 版、未導入なら speech.cpp（使えるとき）。
speechcpp を選んだときに使えない（vulkan-1.dll が無い・speech.exe や GGUF が未導入）なら Python 版（導入済みなら）。
Qwen3-TTS と Irodori-TTS v4 Large は speech.cpp でしか動かない（speechcpp_only）ので、engine が auto でも speech.cpp で動かす。
声には 2 種類ある（voice_kind）:
  reference  参照音声（tts/voices/ の wav と *.voice.gguf）。speech.cpp には起動時に --add-voice で渡す。起動後に増やした声は、TTS の再起動で使える
  speakers   モデルが持つ名前つきの話者（Qwen3-TTS の 9 人）。参照音声での声の複製はできない（--add-voice もしない）
"""
import time
from pathlib import Path

import stt_engine

# ---- GGUF（speech.cpp v0.11.0 のカタログと同じ。speech models --json の revision / sha256）----
# type: このアプリが使う重みの種類。サイズは MB でなくバイト。mf = MeanFlow（4 steps で速い）、rf = Rectified Flow（steps 指定。Python 版と同じモデル）
# voice_kind: reference（参照音声）/ speakers（モデルが持つ名前つきの話者。speakers に一覧、default_speaker に既定）
# fields: POST /v1/audio/speech に送ってよい項目（speech.cpp は知らない項目を unsupported_parameter（400）で拒否する）。
#   steps = ステップ数、seed、instructions = 声・話し方の指示（Irodori のキャプション / Qwen3-TTS 1.7B の instruct）
# emotion: 感情の乗せ方。emoji（文に絵文字を入れる。Irodori）/ instructions（instructions に話し方を書く。Qwen3-TTS 1.7B）/ none（なし。Qwen3-TTS 0.6B）
# vram_mib: speech.cpp の docs の RTX 2080（Vulkan）での VRAM（Q8_0）を MiB にして丸めた見積もり（このアプリでの実測ではない）
TTS_MODELS = {
    "irodori-tts-mf": {
        "label": "irodori-tts-mf（MeanFlow・速い）", "short": "Irodori-TTS v4.1 Small MF",
        "repo": "sakasegawa/Irodori-TTS-v4.1-Small-MF-GGUF", "revision": "9db7da1bec3d1069ef8e29732e0497be60c7b047",
        "files": {
            "f16": {"file": "Irodori-TTS-866M-MF-v4.1-F16.gguf", "size_bytes": 1920321792, "sha256": "e237dc3eefe469b8bba87872beacf12fb7fe00dbdf85767edca8ec04ab910d13"},
            "q8_0": {"file": "Irodori-TTS-866M-MF-v4.1-Q8_0.gguf", "size_bytes": 1207133984, "sha256": "2d161b0bbcd74f906f6b07e03d031075f8ae916db1b8bd4daf16cb37b451086a"},
        },
        "steps_setting": "mf_steps", "default_steps": 4, "upstream": "Aratako/Irodori-TTS-v4.1-Small-MF",
        "license": "MIT", "credit": "Aratako", "voice_kind": "reference", "speechcpp_only": False,
        "fields": ("steps", "seed", "instructions"), "emotion": "emoji", "vram_mib": 1600,
        "note": "速い（steps 4）。読み戻しの誤りは少し多い",
    },
    "irodori-tts": {
        "label": "irodori-tts（Rectified Flow・steps 指定）", "short": "Irodori-TTS v4.1 Small",
        "repo": "sakasegawa/Irodori-TTS-v4.1-Small-GGUF", "revision": "66fe2cde1973fd44e7202fbe36df722103958188",
        "files": {
            "f16": {"file": "Irodori-TTS-859M-v4.1-F16.gguf", "size_bytes": 1905903936, "sha256": "f334932bdf166579bada0a3c8bdabb7199af85682fdcefb6f1cb7e444f5c75b3"},
            "q8_0": {"file": "Irodori-TTS-859M-v4.1-Q8_0.gguf", "size_bytes": 1199474528, "sha256": "293668c68ed2eec670d69c8c30d570410eedd14678acbdb1a80b3d03117c1600"},
        },
        "steps_setting": "num_steps", "default_steps": 24, "upstream": "Aratako/Irodori-TTS-v4.1-Small",
        "license": "MIT", "credit": "Aratako", "voice_kind": "reference", "speechcpp_only": False,
        "fields": ("steps", "seed", "instructions"), "emotion": "emoji", "vram_mib": 1700,
        "note": "steps で品質と速さを選ぶ（下の num_steps。Python 版と同じモデル）",
    },
    # Irodori-TTS v4 Large（3.4B）。Small の約 4 倍の大きさで、言葉で描いた声や参照音声により忠実に従う代わりに、2〜4 倍遅く、漢字の読みはやや弱い。
    # steps は Small（rf）と同じ（既定 40、このアプリは num_steps = 24）。参照音声は Small と同じ使い方（speech.cpp の docs）。
    # ライセンスは Gemma Terms of Use + Irodori の倫理的な制限（商用利用は可。Gemma の Prohibited Use Policy を利用者にも守らせる）。自動では選ばない
    "irodori-tts-large": {
        "label": "irodori-tts-large（v4 Large 3.4B・高品質・遅い・VRAM 約 4.5GB）", "short": "Irodori-TTS v4 Large",
        "repo": "sakasegawa/Irodori-TTS-v4-Large-GGUF", "revision": "79cae5947a97e941c62912749e1387c8b35ec16b",
        "files": {
            "q8_0": {"file": "Irodori-TTS-3.4B-v4-Q8_0.gguf", "size_bytes": 3875154272, "sha256": "836a2deda5115e4400d25674612ff30d704a358d0b16cf96a3eb510dc416bc4e"},
        },
        "steps_setting": "num_steps", "default_steps": 24, "upstream": "Aratako/Irodori-TTS-v4-Large",
        "license": "Gemma Terms of Use + Irodori の倫理的な制限", "credit": "Aratako（Google の T5Gemma 2 由来）",
        "license_note": ("商用利用は可。Gemma Terms of Use（https://ai.google.dev/gemma/terms）に従い、Gemma Prohibited Use Policy"
                         "（https://ai.google.dev/gemma/prohibited_use_policy）を、このアプリの利用者にも守らせてください。"
                         "Irodori の倫理的な制限（本人の同意のない声のなりすまし・偽情報の禁止）も同じです。"),
        "voice_kind": "reference", "speechcpp_only": True,
        "fields": ("steps", "seed", "instructions"), "emotion": "emoji", "vram_mib": 4500,
        "note": "品質重視。Small の 2〜4 倍遅く、VRAM を約 4.5GB 使う。自動では選ばれない",
    },
    # Qwen3-TTS-12Hz CustomVoice（Apache-2.0）。日本語を含む 10 言語、固定の 9 話者。参照音声での声の複製はできない（--add-voice もしない）。
    # steps は受け付けない（送ると unsupported_parameter）。instructions は 1.7B だけ（0.6B に送ると 400）。出力は 24kHz
    "qwen3-tts-0.6b": {
        "label": "qwen3-tts-0.6b（固定の 9 話者・軽い）", "short": "Qwen3-TTS 0.6B",
        "repo": "sakasegawa/Qwen3-TTS-12Hz-0.6B-CustomVoice-GGUF", "revision": "fd9df9364613a92a86b4137074bc281204823033",
        "files": {
            "q8_0": {"file": "Qwen3-TTS-12Hz-0.6B-CustomVoice-Q8_0.gguf", "size_bytes": 1213534464, "sha256": "f606ea3981aa42762b16db9a94826d2d560eb60f0b102001618f40d5a2f78cc6"},
        },
        "steps_setting": None, "default_steps": None, "upstream": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "license": "Apache-2.0", "credit": "Alibaba Qwen", "voice_kind": "speakers", "speechcpp_only": True,
        "fields": ("seed",), "emotion": "none", "vram_mib": 1400,
        "note": "固定の 9 話者（日本語は ono_anna）。参照音声の声は使えない。感情の指示は受け付けない",
    },
    "qwen3-tts-1.7b": {
        "label": "qwen3-tts-1.7b（固定の 9 話者・感情の指示可）", "short": "Qwen3-TTS 1.7B",
        "repo": "sakasegawa/Qwen3-TTS-12Hz-1.7B-CustomVoice-GGUF", "revision": "a72e6c6575d67652488cdffc1c863215a9899978",
        "files": {
            "q8_0": {"file": "Qwen3-TTS-12Hz-1.7B-CustomVoice-Q8_0.gguf", "size_bytes": 2287780352, "sha256": "1bc0ef69547c003507b6536639a6080c8856382f4d0963b15d4475528330592d"},
        },
        "steps_setting": None, "default_steps": None, "upstream": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "license": "Apache-2.0", "credit": "Alibaba Qwen", "voice_kind": "speakers", "speechcpp_only": True,
        "fields": ("seed", "instructions"), "emotion": "instructions", "vram_mib": 2500,
        "note": "固定の 9 話者（日本語は ono_anna）。参照音声の声は使えない。話し方の指示（instructions）で感情を乗せられる",
    },
}
# Qwen3-TTS の 9 話者（GGUF の speech.voices と同じ。speech.cpp の docs の models.md）。lang: 話者の母語、desc: 声の特徴（GGUF の speech.voice_descriptions を日本語にした）。
# 日本語の文は、どの話者でも読める（language=ja）が、母語が日本語なのは ono_anna だけ
QWEN3_SPEAKERS = {
    "ono_anna": {"lang": "ja", "gender": "female", "desc": "日本語・女性。元気で軽やかな声（日本語の既定）"},
    "aiden": {"lang": "en", "gender": "male", "desc": "英語・男性。明るいアメリカ英語の声"},
    "ryan": {"lang": "en", "gender": "male", "desc": "英語・男性。リズムのあるダイナミックな声"},
    "serena": {"lang": "zh", "gender": "female", "desc": "中国語・女性。温かく穏やかな若い声"},
    "vivian": {"lang": "zh", "gender": "female", "desc": "中国語・女性。明るい若い声"},
    "dylan": {"lang": "zh", "gender": "male", "desc": "中国語（北京）・男性。若々しい声"},
    "eric": {"lang": "zh", "gender": "male", "desc": "中国語（四川）・男性。活発な声"},
    "uncle_fu": {"lang": "zh", "gender": "male", "desc": "中国語・男性。落ち着いた低い声"},
    "sohee": {"lang": "ko", "gender": "female", "desc": "韓国語・女性。温かい声"},
}
for _k in ("qwen3-tts-0.6b", "qwen3-tts-1.7b"):
    TTS_MODELS[_k]["speakers"] = QWEN3_SPEAKERS
    TTS_MODELS[_k]["default_speaker"] = "ono_anna"
DEFAULT_MODEL = "irodori-tts"
DEFAULT_TYPE = "q8_0"
ENGINES = {"auto": "自動（Python 版を優先。未導入なら speech.cpp）", "speechcpp": "speech.cpp（Vulkan。速く VRAM が少ない）", "python": "Python 版 Irodori-TTS-Server（既定。PyTorch）"}
VOICE_EXTS = (".wav",)
CAPTION_MAX_CHARS = 200
DEFAULT_SPEAKER = "ono_anna"
# プロファイルの LLM の余白（profiles.TTS_VRAM_MIB）が見込んでいる TTS の VRAM。これを超えるモデルは、LLM の余白をその分だけ増やす
PROFILE_TTS_VRAM_MIB = 2800


def model_file(key: str, typ: str | None = None) -> dict:
    return TTS_MODELS[key]["files"][typ or DEFAULT_TYPE]


def models_dir(root) -> Path:
    return Path(root) / "models" / "tts"


def model_path(root, key: str, typ: str | None = None) -> Path:
    return models_dir(root) / model_file(key, typ)["file"]


def model_present(root, key: str, typ: str | None = None) -> bool:
    p = model_path(root, key, typ)
    return p.is_file() and p.stat().st_size == model_file(key, typ)["size_bytes"]


def python_installed(root) -> bool:
    return (Path(root) / "tts" / ".venv" / ".setup_ok").exists()


def voices_dir(root) -> Path:
    return Path(root) / "tts" / "voices"


def voice_files(root) -> list[tuple[str, Path]]:
    """speech.cpp に --add-voice で渡す声（名前 = ファイル名の拡張子を除いたもの。*.voice.gguf は .voice も除く）"""
    d = voices_dir(root)
    out = []
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if not f.is_file():
                continue
            low = f.name.lower()
            if low.endswith(".voice.gguf"):
                out.append((f.name[:-len(".voice.gguf")], f))
            elif f.suffix.lower() in VOICE_EXTS:
                out.append((f.stem, f))
    return out


def default_tts_settings() -> dict:
    return {"engine": "auto", "model": DEFAULT_MODEL, "mf_steps": 4, "speaker": DEFAULT_SPEAKER}


def resolve(cfg: dict, settings: dict, root) -> dict:
    """TTS の実際の動かし方。engine (speechcpp / python)、model_key、type、fallback（予備に切り替えた理由）、label、
    voice_kind（reference / speakers。実際に動かすエンジンとモデルでの声の種類。Python 版は常に reference）、speakers（speakers のときの話者の表）"""
    root = Path(root)
    t = {**default_tts_settings(), **(settings.get("tts") or {})}
    key = t["model"] if t["model"] in TTS_MODELS else DEFAULT_MODEL
    want = t["engine"] if t["engine"] in ENGINES else "auto"
    fallback = ""
    only = TTS_MODELS[key]["speechcpp_only"]
    if only and want == "python":
        key, only = DEFAULT_MODEL, False       # Python 版では動かせない（設定画面は、この組み合わせを保存させない）。Python 版は、モデルを使わない
    elif only:
        want = "speechcpp"                     # speech.cpp 専用のモデルは、エンジンが「自動」でも speech.cpp で動かす
    if want in ("python", "auto"):
        # 既定は Python 版（int8）。未導入のときだけ speech.cpp を使う
        engine = "python"
        if not python_installed(root):
            ok, why = stt_engine.speechcpp_usable(root)
            if ok and model_present(root, key):
                engine, fallback = "speechcpp", "Python 版の Irodori-TTS-Server が導入されていないので、speech.cpp で動かします。"
    else:
        engine = "speechcpp"
        ok, why = stt_engine.speechcpp_usable(root)
        if ok and not model_present(root, key):
            ok, why = False, f"{TTS_MODELS[key]['short']} の GGUF（{model_file(key)['file']}）が未取得です。"
        if not ok:
            if python_installed(root):
                engine, fallback = "python", why + "Python 版の Irodori-TTS-Server で動かします。"
                if only:
                    key = DEFAULT_MODEL
            else:
                fallback = why
    m = TTS_MODELS[key]
    kind = m["voice_kind"] if engine == "speechcpp" else "reference"
    out = {"engine": engine, "model_key": key, "type": DEFAULT_TYPE, "want": want, "fallback": fallback, "model": m,
           "voice_kind": kind, "speakers": m.get("speakers") if kind == "speakers" else None}
    out["label"] = "TTS (Irodori Python 版)" if engine == "python" else f"TTS (speech.cpp {key})"
    return out


def pick_speaker(r: dict, *wanted) -> str:
    """speakers のモデルで使う話者。wanted（キャラクターの voice、設定の speaker の順）のうち、そのモデルにいる最初のもの。
    参照音声の声（mio_bright など）は話者ではないので、飛ばして既定（日本語の ono_anna）にする"""
    names = r.get("speakers") or {}
    for w in wanted:
        if w and w in names:
            return w
    return r["model"].get("default_speaker") or DEFAULT_SPEAKER


def steps_for(settings: dict, key: str) -> int | None:
    """そのモデルの steps。steps を受け付けないモデル（Qwen3-TTS）は None"""
    setting = TTS_MODELS[key]["steps_setting"]
    if setting is None:
        return None
    t = {**default_tts_settings(), "num_steps": 24, **(settings.get("tts") or {})}
    return int(t["mf_steps"] if setting == "mf_steps" else t["num_steps"])


def emotion_mode(r: dict) -> str:
    """感情の乗せ方。emoji（Irodori / Python 版）/ instructions（Qwen3-TTS 1.7B）/ none（Qwen3-TTS 0.6B）"""
    if r["engine"] == "python":
        return "emoji"
    return r["model"].get("emotion", "emoji")


def vram_extra_mib(r: dict) -> int:
    """LLM の余白（profiles.TTS_VRAM_MIB）に足す、この TTS の分。speech.cpp で、余白の見込み（Python 版 Irodori 2.8GB）を超えるモデルだけ"""
    if r["engine"] != "speechcpp":
        return 0
    return max(0, int(r["model"].get("vram_mib") or 0) - PROFILE_TTS_VRAM_MIB)


def device_name(cfg: dict, root) -> str:
    """speech.cpp の --device の値。NVIDIA などの GPU（Vulkan）。見つからなければ cpu"""
    devs, _ = stt_engine.devices(root)
    g = stt_engine.pick_gpu(devs or [], (cfg.get("gpu") or {}).get("name", ""))
    return g["name"] if g else "cpu"


def speechcpp_command(cfg: dict, r: dict, root) -> list[str]:
    root = Path(root)
    cmd = [str(stt_engine.speech_exe(root)), "serve", str(model_path(root, r["model_key"], r.get("type"))),
           "--host", "127.0.0.1", "--port", str(cfg["ports"]["tts"]), "--device", r["device_name"],
           "--threads", str(int((cfg.get("stt") or {}).get("cpu_threads") or 8)), "--language", "ja"]
    if r["model"]["voice_kind"] == "reference":       # 固定の話者のモデル（Qwen3-TTS）は、参照音声を受け付けない
        for name, path in voice_files(root):
            cmd += ["--add-voice", f"{name}={path}"]
    return cmd


def health(cfg: dict, settings: dict, root) -> tuple[str, str | None]:
    port = cfg["ports"]["tts"]
    r = resolve(cfg, settings, root)
    if r["engine"] == "speechcpp":
        return f"http://127.0.0.1:{port}/v1/models", "owned_by"
    return f"http://127.0.0.1:{port}/health", '"loaded":true'


def speech_body(text: str, voice: str, steps: int | None, seed=None, caption: str | None = None, model_key: str | None = None) -> dict:
    """speech.cpp の POST /v1/audio/speech の本文（model は送らない。起動中のモデルを使う）。
    speech.cpp は知らない項目を 400（unsupported_parameter）で拒否するので、モデルが受け付ける項目（fields）だけを入れる。
    model_key を省くと、Irodori と同じ（steps・seed・instructions）"""
    fields = TTS_MODELS[model_key]["fields"] if model_key else ("steps", "seed", "instructions")
    body = {"input": text, "voice": voice, "response_format": "wav"}
    if "steps" in fields and steps is not None:
        body["steps"] = int(steps)
    if "seed" in fields and seed is not None:
        body["seed"] = int(seed)
    if "instructions" in fields and caption:
        body["instructions"] = caption
    return body
