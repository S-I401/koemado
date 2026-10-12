"""音声合成（TTS）のエンジンとモデルの表、使えるかの判定、speech.cpp の起動コマンド（標準ライブラリのみ。
setup.py / ctl.py / supervisor.py / 設定画面 / app/run.py が共有する。speech.cpp の入れ方や Vulkan の判定は stt_engine.py と共通）。

エンジン:
  speechcpp  speech.cpp（MIT）の `speech serve` で動く Irodori-TTS（GGUF）。Vulkan の GPU で動く。Python も PyTorch も不要。既定
  python     従来の Irodori-TTS-Server（Python 3.10 + PyTorch CUDA。int8）。Vulkan が使えない PC のための予備

設定（config/settings.json の "tts"）の engine は "auto" が既定: speech.cpp が使えれば speech.cpp、使えない（vulkan-1.dll が無い・
speech.exe や Irodori の GGUF が未導入）ときは Python 版（導入済みなら）。
speech.cpp の参照音声は、起動時に --add-voice で渡す（tts/voices/ の wav と *.voice.gguf）。起動後に増やした声は、TTS の再起動で使えるようになる。
"""
import time
from pathlib import Path

import stt_engine

# ---- Irodori-TTS v4.1 Small の GGUF（speech.cpp v0.8.4 のカタログと同じ。speech models --json の revision / sha256）----
# type: このアプリが使う重みの種類。サイズは MB でなくバイト。mf = MeanFlow（4 steps で速い）、rf = Rectified Flow（steps 指定。Python 版と同じモデル）
TTS_MODELS = {
    "irodori-tts-mf": {
        "label": "irodori-tts-mf（MeanFlow・速い）", "short": "Irodori-TTS v4.1 Small MF",
        "repo": "sakasegawa/Irodori-TTS-v4.1-Small-MF-GGUF", "revision": "205ab071cb54e46509a84d3a48535df283bc3b0f",
        "files": {
            "f16": {"file": "Irodori-TTS-866M-MF-v4.1-F16.gguf", "size_bytes": 1920321792, "sha256": "e237dc3eefe469b8bba87872beacf12fb7fe00dbdf85767edca8ec04ab910d13"},
            "q8_0": {"file": "Irodori-TTS-866M-MF-v4.1-Q8_0.gguf", "size_bytes": 1207133984, "sha256": "2d161b0bbcd74f906f6b07e03d031075f8ae916db1b8bd4daf16cb37b451086a"},
        },
        "steps_setting": "mf_steps", "default_steps": 4, "upstream": "Aratako/Irodori-TTS-v4.1-Small-MF",
    },
    "irodori-tts": {
        "label": "irodori-tts（Rectified Flow・steps 指定）", "short": "Irodori-TTS v4.1 Small",
        "repo": "sakasegawa/Irodori-TTS-v4.1-Small-GGUF", "revision": "a8d7c01cf5c306d7b495a1db61e027ca4a9c269a",
        "files": {
            "f16": {"file": "Irodori-TTS-859M-v4.1-F16.gguf", "size_bytes": 1905903936, "sha256": "f334932bdf166579bada0a3c8bdabb7199af85682fdcefb6f1cb7e444f5c75b3"},
            "q8_0": {"file": "Irodori-TTS-859M-v4.1-Q8_0.gguf", "size_bytes": 1199474528, "sha256": "293668c68ed2eec670d69c8c30d570410eedd14678acbdb1a80b3d03117c1600"},
        },
        "steps_setting": "num_steps", "default_steps": 24, "upstream": "Aratako/Irodori-TTS-v4.1-Small",
    },
}
DEFAULT_MODEL = "irodori-tts"
DEFAULT_TYPE = "q8_0"
ENGINES = {"auto": "自動（speech.cpp が使えれば speech.cpp、使えなければ Python 版）", "speechcpp": "speech.cpp", "python": "Python 版 Irodori-TTS-Server（従来。PyTorch）"}
VOICE_EXTS = (".wav",)
CAPTION_MAX_CHARS = 200


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
    return {"engine": "auto", "model": DEFAULT_MODEL, "mf_steps": 4}


def resolve(cfg: dict, settings: dict, root) -> dict:
    """TTS の実際の動かし方。engine (speechcpp / python)、model_key、type、fallback（予備に切り替えた理由）、label"""
    root = Path(root)
    t = {**default_tts_settings(), **(settings.get("tts") or {})}
    key = t["model"] if t["model"] in TTS_MODELS else DEFAULT_MODEL
    want = t["engine"] if t["engine"] in ENGINES else "auto"
    fallback = ""
    engine = "speechcpp"
    if want == "python":
        engine = "python"
    else:
        ok, why = stt_engine.speechcpp_usable(root)
        if ok and not model_present(root, key):
            ok, why = False, f"Irodori の GGUF（{model_file(key)['file']}）が未取得です。"
        if not ok:
            if python_installed(root):
                engine, fallback = "python", why + "Python 版の Irodori-TTS-Server で動かします。"
            else:
                fallback = why
    out = {"engine": engine, "model_key": key, "type": DEFAULT_TYPE, "want": want, "fallback": fallback, "model": TTS_MODELS[key]}
    out["label"] = "TTS (Irodori Python 版)" if engine == "python" else f"TTS (speech.cpp {key})"
    return out


def steps_for(settings: dict, key: str) -> int:
    t = {**default_tts_settings(), "num_steps": 24, **(settings.get("tts") or {})}
    return int(t["mf_steps"] if TTS_MODELS[key]["steps_setting"] == "mf_steps" else t["num_steps"])


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
    for name, path in voice_files(root):
        cmd += ["--add-voice", f"{name}={path}"]
    return cmd


def health(cfg: dict, settings: dict, root) -> tuple[str, str | None]:
    port = cfg["ports"]["tts"]
    r = resolve(cfg, settings, root)
    if r["engine"] == "speechcpp":
        return f"http://127.0.0.1:{port}/v1/models", "owned_by"
    return f"http://127.0.0.1:{port}/health", '"loaded":true'


def speech_body(text: str, voice: str, steps: int, seed=None, caption: str | None = None) -> dict:
    """speech.cpp の POST /v1/audio/speech の本文（model は送らない。起動中のモデルを使う）"""
    body = {"input": text, "voice": voice, "response_format": "wav", "steps": int(steps)}
    if seed is not None:
        body["seed"] = int(seed)
    if caption:
        body["instructions"] = caption
    return body
