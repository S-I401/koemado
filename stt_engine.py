"""音声認識（STT）のエンジンとモデルの表、使えるかの判定、speech.cpp の起動コマンド（標準ライブラリのみ。
setup.py / ctl.py / supervisor.py / 設定画面 / app/run.py が共有する）。

エンジン:
  speechcpp       speech.cpp（MIT）の `speech serve`。Vulkan の GPU か CPU で動く。Python も CUDA も不要。既定
  faster-whisper  従来の faster-whisper small（CPU）。Vulkan が使えない PC のための予備

設定（config/settings.json の "stt"）の engine は "auto" が既定: speech.cpp が使えれば speech.cpp、
使えない（vulkan-1.dll が無い・speech.exe やモデルが未導入）ときは faster-whisper（導入済みなら）。
モデルや版の差し替えはこの表を直す。
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
GIB = 1024 ** 3

# テスト用: 1 にすると「vulkan-1.dll が見つからない PC」として振る舞う（faster-whisper への切り替えを試すため）
ENV_NO_VULKAN = "KOEMADO_SIMULATE_NO_VULKAN"

# ---- speech.cpp（動作確認した版。公式の install.ps1 は PATH を変えるので使わない）----
SPEECHCPP = {
    "version": "0.8.4",
    "asset": "speech-0.8.4-windows-x64-vulkan.zip",
    "url": "https://github.com/nyosegawa/speech.cpp/releases/download/v0.8.4/speech-0.8.4-windows-x64-vulkan.zip",
    "sums_url": "https://github.com/nyosegawa/speech.cpp/releases/download/v0.8.4/SHA256SUMS",
    # リリースの SHA256SUMS と同じ値（コードにも固定する。SHA256SUMS の改ざんにも気づけるように、両方を照合する）
    "sha256": "c35e75e64b170d69c2249fc32a82ae6e1a16170f2064eeb60cf815d3a5d65001",
    "size_bytes": 30547352,
    "disk_bytes": 93 * 1024 * 1024,        # 展開後（speech.exe + speech.dll）の実測
    "license": "MIT",
    "home": "https://github.com/nyosegawa/speech.cpp",
}

# ---- 認識モデル（speech.cpp の v0.8.4 のカタログと同じ。Q8_0。revision / sha256 は speech models --json の値）----
# id: speech serve の /v1/models が返す id（カタログの名前とは違い、GGUF が持つモデル名）。cer: Common Voice 8.0 日本語の test（4,483 発話）の CER（speech.cpp の docs による。Q8_0）
MODELS = {
    "parakeet-ja": {
        "id": "parakeet-tdt_ctc-0.6b-ja",
        "label": "parakeet-ja（速い・既定）",
        "short": "parakeet-tdt_ctc-0.6b-ja",
        "repo": "sakasegawa/parakeet-tdt_ctc-0.6b-ja-GGUF", "revision": "f7cb5387e1e76385456b7a792054565ecf177417",
        "file": "parakeet-tdt_ctc-0.6B-ja-Q8_0.gguf", "size_bytes": 661012416,
        "sha256": "6ff90c5240f0778dfb44816916eb3c8e634645e120170d6ae43c0f50a11d79d0",
        "hint": False, "cer": "7.9%",
        "license": "CC-BY-4.0", "upstream": "nvidia/parakeet-tdt_ctc-0.6b-ja", "credit": "NVIDIA",
    },
    "qwen3-asr-0.6b": {
        "id": "Qwen3-ASR-0.6B",
        "label": "qwen3-asr-0.6b（軽い）",
        "short": "Qwen3-ASR 0.6B",
        "repo": "sakasegawa/Qwen3-ASR-0.6B-GGUF", "revision": "ee78f8709547f39bea3b924a16c72403826cac92",
        "file": "Qwen3-ASR-0.6B-Q8_0.gguf", "size_bytes": 841502336,
        "sha256": "416e10c15b4a3d9002bd337d18fc450233fdf68502b6e10d1379d2789838afd0",
        "hint": True, "cer": "11.9%",
        "license": "Apache-2.0", "upstream": "Qwen/Qwen3-ASR-0.6B", "credit": "Alibaba Qwen",
    },
    "qwen3-asr-1.7b": {
        "id": "Qwen3-ASR-1.7B",
        "label": "qwen3-asr-1.7b（高精度・固有名詞のヒント可）",
        "short": "Qwen3-ASR 1.7B",
        "repo": "sakasegawa/Qwen3-ASR-1.7B-GGUF", "revision": "81e2423d24073cdeeccb5951df16accc89e075a2",
        "file": "Qwen3-ASR-1.7B-Q8_0.gguf", "size_bytes": 2176109216,
        "sha256": "5f219b78a1d9c3b9e97da27708b36f8a0bc1bfc1650b541c0a6dbaf87c9a62d0",
        "hint": True, "cer": "9.5%",
        "license": "Apache-2.0", "upstream": "Qwen/Qwen3-ASR-1.7B", "credit": "Alibaba Qwen",
    },
}
DEFAULT_MODEL = "parakeet-ja"
ENGINES = {"auto": "自動（speech.cpp が使えれば speech.cpp、使えなければ faster-whisper）",
           "speechcpp": "speech.cpp", "faster-whisper": "faster-whisper（従来。CPU）"}
DEVICES = {"auto": "自動（プロファイルの既定）", "gpu": "GPU（Vulkan）", "cpu": "CPU"}
# STT を GPU に載せたときの VRAM のピーク（MiB）。mid の構成（LLM + TTS + STT をすべて起動。VRAM 8GB の模擬）で、GPU Process Memory の
# 専有メモリを STT のプロセスだけ測った最大値（parakeet 705 / qwen3-asr-0.6b 1041 / qwen3-asr-1.7b 2321）。余裕を少し足して丸めている
STT_VRAM_MIB = {"parakeet-ja": 710, "qwen3-asr-0.6b": 1050, "qwen3-asr-1.7b": 2330}
# デバイスが「自動」のとき GPU を使ってよいモデル（プロファイルごと）。根拠（mid の実測、VRAM 8GB 模擬）:
#   LLM 3518 + TTS 2410 = 5929MiB（STT が CPU）。これに STT を足した合計が 7GB（7168MiB）以下になるものだけを GPU にする
#   （parakeet 6647、qwen3-asr-0.6b 6967 は収まる。qwen3-asr-1.7b は 8281 で 8GB のカードに収まらない）。
#   high は VRAM が 20GB 以上あり、LLM が --fit-target の余白（STT の分を足す）を残すので、どれも GPU でよい。
#   low は VRAM 6GB 級で、LLM + TTS でほぼ埋まるので、CPU（parakeet は CPU でも 0.3〜1 秒）。
#   external / strata（LLM を別に動かす）は、Strata の余白（TTS 用）に収まる parakeet だけ GPU。
# mid: TTS が既定の Python 版のとき、0.6B を GPU にすると合計 7,057MiB で 7GB すれすれなので、自動では parakeet だけ GPU（設定画面で選べるが警告が出る）
AUTO_GPU = {"high": set(STT_VRAM_MIB), "mid": {"parakeet-ja"}, "low": set(), "external": set(STT_VRAM_MIB), "strata": {"parakeet-ja"}}
AUTO_GPU_OTHER = {"parakeet-ja"}


def auto_device(profile: str, model_key: str) -> str:
    return "gpu" if model_key in AUTO_GPU.get(profile, AUTO_GPU_OTHER) else "cpu"


HINT_MAX_CHARS = 400

FW_DEFAULT_MODEL = "small"
FW_REPO = "Systran/faster-whisper-small"


# ---------------------------------------------------------------- パス ----

def speech_dir(root) -> Path:
    return Path(root) / "stt" / "speechcpp"


def speech_exe(root) -> Path:
    return speech_dir(root) / "speech.exe"


def models_dir(root) -> Path:
    return Path(root) / "models" / "stt"


def model_path(root, key: str) -> Path:
    return models_dir(root) / MODELS[key]["file"]


def model_present(root, key: str) -> bool:
    p = model_path(root, key)
    return p.is_file() and p.stat().st_size == MODELS[key]["size_bytes"]


def fw_installed(root) -> bool:
    return (Path(root) / "stt" / ".venv" / ".setup_ok").exists()


def speechcpp_installed(root) -> bool:
    return speech_exe(root).is_file() and (speech_dir(root) / "speech.dll").is_file()


# ---------------------------------------------------------------- Vulkan とデバイス ----

def vulkan_loader_found() -> tuple[bool, str]:
    """Vulkan のローダー（vulkan-1.dll。GPU のドライバが入れる）を読み込めるか"""
    if os.environ.get(ENV_NO_VULKAN) == "1":
        return False, f"vulkan-1.dll を見つけられない PC として動かしています（テスト用の環境変数 {ENV_NO_VULKAN}=1）。"
    if sys.platform != "win32":
        return False, "Windows 以外です。"
    try:
        ctypes.WinDLL("vulkan-1.dll")
        return True, ""
    except OSError:
        return False, "Vulkan のローダー（vulkan-1.dll）が見つかりません。GPU のドライバを入れ直すと入ります。"


_devices_cache: dict = {}


def devices(root, force: bool = False) -> tuple[list[dict] | None, str]:
    """`speech devices --json` の devices。失敗したら (None, 理由)。30 秒は結果を使い回す"""
    key = str(Path(root))
    hit = _devices_cache.get(key)
    if hit and not force and time.time() - hit[0] < 30:
        return hit[1], hit[2]
    exe = speech_exe(root)
    if not exe.is_file():
        return None, "speech.exe がありません。"
    try:
        r = subprocess.run([str(exe), "devices", "--json"], capture_output=True, text=True, timeout=60, errors="replace",
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, cwd=str(exe.parent))
        data = json.loads(r.stdout)
        out, why = list(data.get("devices") or []), ""
    except Exception as e:  # noqa: BLE001
        out, why = None, f"speech devices が失敗しました: {type(e).__name__}: {e}"
    _devices_cache[key] = (time.time(), out, why)
    return out, why


def pick_gpu(devs: list[dict], gpu_name: str = "") -> dict | None:
    """使う GPU。NVIDIA など discrete（kind=gpu）を優先し、config の GPU 名に近いものを先に。無ければ None（CPU）"""
    gpus = [d for d in devs if d.get("kind") == "gpu"]
    if not gpus:
        return None
    want = (gpu_name or "").lower().replace("nvidia", "").replace("geforce", "").strip()
    for d in gpus:
        desc = (d.get("description") or "").lower()
        if want and want in desc:
            return d
    return gpus[0]


def speechcpp_usable(root) -> tuple[bool, str]:
    """speech.cpp を起動できる PC か（vulkan-1.dll と speech.exe と、モデルが 1 つ以上）"""
    ok, why = vulkan_loader_found()
    if not ok:
        return False, why
    if not speechcpp_installed(root):
        return False, "speech.cpp（stt\\speechcpp\\speech.exe）が導入されていません。setup.bat を実行してください。"
    return True, ""


# ---------------------------------------------------------------- 設定の解決 ----

def default_stt_settings() -> dict:
    return {"engine": "auto", "model": DEFAULT_MODEL, "device": "auto", "hints": ""}


def split_hints(text: str) -> list[str]:
    out = []
    for part in (text or "").replace("、", ",").replace("，", ",").replace("\n", ",").replace("\r", ",").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def build_prompt(char_name: str, hints: str) -> str:
    """qwen3-asr の prompt（名前や言葉のヒント。モデルは背景として使い、指示としては扱わない）"""
    terms = []
    for t in [char_name] + split_hints(hints):
        t = (t or "").strip()
        if t and t not in terms:
            terms.append(t)
    return "、".join(terms)[:HINT_MAX_CHARS]


def resolve(cfg: dict, settings: dict, root, probe: bool = True) -> dict:
    """STT の実際の動かし方。戻り値:
    engine (speechcpp / faster-whisper)、model_key、model（MODELS の項目）、device（gpu / cpu）、device_name（speech の --device の値）、
    fallback（予備に切り替えたときの理由。無ければ ""）、label（ログ・管理画面の名前）"""
    root = Path(root)
    st = {**default_stt_settings(), **(settings.get("stt") or {})}
    key = st["model"] if st["model"] in MODELS else DEFAULT_MODEL
    want = st["engine"] if st["engine"] in ENGINES else "auto"
    fallback = ""
    engine = "speechcpp"
    if want == "faster-whisper":
        engine = "faster-whisper"
    else:
        ok, why = speechcpp_usable(root)
        if ok and not model_present(root, key):
            ok, why = False, f"認識モデル（{MODELS[key]['file']}）が未取得です。"
        if not ok:
            if fw_installed(root):
                engine, fallback = "faster-whisper", why + "faster-whisper で動かします。"
            else:
                fallback = why
    out = {"engine": engine, "model_key": key, "model": MODELS[key], "want": want, "fallback": fallback}
    if engine == "faster-whisper":
        out.update(device="cpu", device_name="cpu", label="STT (faster-whisper)")
        return out
    device = st["device"] if st["device"] in ("gpu", "cpu") else auto_device(cfg.get("profile"), key)
    dev_name = "cpu"
    if device == "gpu" and probe:
        devs, _ = devices(root)
        g = pick_gpu(devs or [], (cfg.get("gpu") or {}).get("name", ""))
        if g:
            dev_name = g["name"]
        else:
            device = "cpu"
            out["fallback"] = (out["fallback"] + " " if out["fallback"] else "") + "Vulkan の GPU が見つからないため、CPU で動かします。"
    out.update(device=device, device_name=dev_name, label=f"STT (speech.cpp {key}, {device.upper()})")
    return out


def speechcpp_command(cfg: dict, r: dict, root) -> list[str]:
    root = Path(root)
    threads = int((cfg.get("stt") or {}).get("cpu_threads") or 8)
    return [str(speech_exe(root)), "serve", str(model_path(root, r["model_key"])),
            "--host", "127.0.0.1", "--port", str(cfg["ports"]["stt"]),
            "--device", r["device_name"], "--threads", str(threads), "--language", "ja"]


def health(cfg: dict, settings: dict, root) -> tuple[str, str | None]:
    """(URL, 本文に含まれるべき文字列)。speech.cpp は /v1/models で準備完了を見る（モデルを読み込んでから待ち受ける）"""
    port = cfg["ports"]["stt"]
    r = resolve(cfg, settings, root, probe=False)
    if r["engine"] == "speechcpp":
        return f"http://127.0.0.1:{port}/v1/models", r["model"]["id"]
    return f"http://127.0.0.1:{port}/health", None
