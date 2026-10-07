# /// script
# requires-python = ">=3.12"
# dependencies = ["huggingface_hub>=0.34"]
# ///
"""koemado セットアップ。

PC のスペック（GPU / VRAM / RAM / 空き容量 / ポート）を調べ、合うモデルを選んで、
必要なものをすべてインストール先フォルダの中に導入する。途中で止まっても、
もう一度実行すれば続きから再開する（ダウンロード済みのものは取り直さない）。

  setup.bat                                  通常の導入
  setup.bat --dry-run                        調べて計画を表示するだけ（何も入れない）
  setup.bat --profile mid --yes              プロファイルを指定し、確認なしで進める
  setup.bat --install-dir D:\\aiavatar       インストール先（既定: このフォルダ）
  setup.bat --dry-run --simulate "vram=8,ram=32,disk=60"   別の PC を想定した判定（テスト用）
"""

import argparse
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))
import ctl  # noqa: E402  （起動・設定の共通処理）
import settings_store  # noqa: E402  （ユーザー設定・キャラクター）

# =====================================================================
# 設定データ: モデルやバージョンの差し替えはこの 1 か所を直す
# =====================================================================

GIB = 1024 ** 3

# ---- 固定するバージョン（動作確認したもの） ----
PINNED = {
    # llama.cpp: 動作確認したリリースタグ。アセットは名前のパターンで選ぶ（CUDA 12 系の x64 ビルド）
    "llama_tag": "b11443",
    "llama_asset_patterns": [
        r"^llama-{tag}-bin-win-cuda-12\.\d+-x64\.zip$",
        r"^cudart-llama-bin-win-cuda-12\.\d+-x64\.zip$",
    ],
    # Irodori-TTS-Server: 動作確認したコミット（uv.lock も同じコミットのものを使う）
    "tts_repo": "https://github.com/Aratako/Irodori-TTS-Server.git",
    "tts_commit": "61012c760f22f7b4a6c21c5c5f8f9e148120b6f9",
    # 公式の int8 量子化チェックポイント（MIT）
    "tts_hf_checkpoint": "Aratako/Irodori-TTS-v4.1-Small-Quantized/int8-weight-only",
    # AIAvatarKit: ブラウザ UI（examples/websocket/html）を、この版のタグから取る
    "aiavatar_ui_tarball": "https://github.com/uezo/aiavatarkit/archive/refs/tags/v0.9.1.tar.gz",
    "aiavatar_ui_prefix": "aiavatarkit-0.9.1/examples/websocket/html",
    # git が無い PC 向けの持ち運べる Git（uv が git 依存の取得に使う）
    "mingit_url": "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.2/MinGit-2.47.1.2-64-bit.zip",
    "stt_model": "small",
    "python": "3.12",
    "tts_python": "3.10",   # Irodori-TTS-Server の uv.lock は 3.10/3.11 の wheel を前提にしている（sentencepiece 0.1.99 は 3.12 の wheel が無い）
}

# ---- 動作要件 ----
# PyTorch cu128 は CUDA 12.8 用。NVIDIA のドライバ要件（Windows）は 570.65 以上。
MIN_DRIVER = 570
MIN_COMPUTE_CAPABILITY = 7.5          # Turing（RTX 20 系）以降。bf16 と torchao の int8 が使える世代
RAM_VRAM_TOLERANCE = 0.93             # 搭載 32GB でも OS 側の表示は 31.x GiB になるので、少し余裕を持って判定する

import profiles  # noqa: E402
from profiles import (PROFILES, PROFILE_ORDER, TTS_VRAM_MIB, DESKTOP_HEADROOM_MIB, llm_settings as _llm_settings)  # noqa: E402

# ---- 容量の見積もり（GB。インストール先のドライブに必要な分） ----
# 環境の構築中は、ダウンロードのキャッシュと展開後のファイルが両方ある（キャッシュは終わりに消す）
SIZE_LABELS = {
    "llama.cpp": "llama.cpp（LLM エンジン）",
    "tts_env": "TTS の実行環境（PyTorch CUDA 12.8 など）",
    "tts_models": "TTS のモデル（Irodori int8・codec）",
    "stt_env": "STT の実行環境",
    "stt_model": "STT のモデル（Whisper small）",
    "app_env": "AIAvatarKit の実行環境",
    "ui": "ブラウザ UI",
}
# (ダウンロード量, ディスク使用量) の概算 GiB。ディスクは展開後で、導入中のキャッシュ分を含む
SIZES_GB = {
    "llama.cpp": (0.64, 1.9),    # zip 2 本 → 展開後 1.2GB（zip は展開後に消す）
    "tts_env": (3.2, 6.0),       # PyTorch cu128 + 依存（展開後の実測 5.2GB）+ Python 3.10
    "tts_models": (1.3, 1.4),
    "stt_env": (0.1, 0.3),
    "stt_model": (0.5, 0.5),
    "app_env": (0.4, 1.2),       # Python 3.12 + fastmcp・ddgs などを含む
    "ui": (0.04, 0.05),
}
DISK_MARGIN_GB = 3.0

STATE_FILE = "config.json"

# ---- 既定のキャラクターの声（voices/ に同梱。Voice Design で作った成人女性の声） ----
DEFAULT_VOICE = "mio_a"
DEFAULT_TTS_NUM_STEPS = 24
DEFAULT_TTS_SEED = 42

# =====================================================================


def say(*a, **k):
    print(*a, **k, flush=True)


def die(msg: str, code: int = 1):
    say(f"\n[中止] {msg}")
    sys.exit(code)


# ---------------------------------------------------------------- ハード検出 ----

def ram_gib() -> tuple[float, float]:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullTotalPhys / GIB, st.ullAvailPhys / GIB


def physical_cores() -> int:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return int(out)
    except Exception:
        return max(1, (os.cpu_count() or 2) // 2)


def cpu_name() -> str:
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return "不明"


def detect_gpus() -> list[dict]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return []
    if out.returncode != 0:
        return []
    gpus = []
    for line in out.stdout.strip().splitlines():
        idx, name, total, free, driver, cc = [x.strip() for x in line.split(",")]
        gpus.append({"index": int(idx), "name": name, "vram_mib": int(total), "vram_free_mib": int(free),
                     "driver": driver, "driver_major": int(driver.split(".")[0]), "cc": float(cc)})
    return gpus


def parse_simulate(text: str | None) -> dict:
    sim = {}
    if not text:
        return sim
    for part in text.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        sim[k.strip().lower()] = v.strip()
    return sim


def detect_hardware(install_dir: Path, sim: dict) -> dict:
    gpus = detect_gpus()
    hw = {"simulated": sorted(sim)}
    if gpus:
        g = max(gpus, key=lambda x: x["vram_mib"])
        hw["gpu"] = dict(g)
    else:
        hw["gpu"] = None
    total, avail = ram_gib()
    probe = install_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    disk_free = shutil.disk_usage(probe).free / GIB
    hw.update({
        "ram_total_gib": total, "ram_free_gib": avail,
        "cores": physical_cores(), "cpu": cpu_name(),
        "disk_free_gib": disk_free, "drive": probe.drive or str(probe),
    })
    # 模擬入力で上書き（テスト用）
    if sim:
        if hw["gpu"] is None and ("vram" in sim):
            hw["gpu"] = {"index": 0, "name": "(模擬 GPU)", "vram_mib": 0, "vram_free_mib": 0,
                         "driver": "999.0", "driver_major": 999, "cc": 8.9}
        if hw["gpu"] is not None:
            if "vram" in sim:
                hw["gpu"]["vram_mib"] = int(float(sim["vram"]) * 1024)
                hw["gpu"]["vram_free_mib"] = hw["gpu"]["vram_mib"]
            if "vram_free" in sim:
                hw["gpu"]["vram_free_mib"] = int(float(sim["vram_free"]) * 1024)
            if "driver" in sim:
                hw["gpu"]["driver_major"] = int(float(sim["driver"]))
                hw["gpu"]["driver"] = sim["driver"]
            if "cc" in sim:
                hw["gpu"]["cc"] = float(sim["cc"])
        if "ram" in sim:
            hw["ram_total_gib"] = float(sim["ram"])
            hw["ram_free_gib"] = float(sim["ram"]) * 0.7
        if "disk" in sim:
            hw["disk_free_gib"] = float(sim["disk"])
        if "cores" in sim:
            hw["cores"] = int(sim["cores"])
    return hw


def print_hardware(hw: dict):
    g = hw["gpu"]
    say("=== 検出した環境 " + ("（模擬入力あり: " + ", ".join(hw["simulated"]) + "）" if hw["simulated"] else "") + " ===")
    if g:
        say(f"  GPU        : {g['name']}  VRAM {g['vram_mib'] / 1024:.1f} GiB（空き {g['vram_free_mib'] / 1024:.1f} GiB）")
        say(f"  ドライバ   : {g['driver']}   Compute Capability: {g['cc']}")
    else:
        say("  GPU        : NVIDIA GPU が見つかりません")
    say(f"  CPU        : {hw['cpu']}（物理コア {hw['cores']}）")
    say(f"  メモリ     : {hw['ram_total_gib']:.1f} GiB（空き {hw['ram_free_gib']:.1f} GiB）")
    say(f"  空き容量   : {hw['disk_free_gib']:.1f} GiB（{hw['drive']}）")


def check_requirements(hw: dict, external: bool = False):
    g = hw["gpu"]
    if not g:
        die("NVIDIA GPU が見つかりません（nvidia-smi が動きません）。\n"
            "  このソフトは音声合成（TTS）に NVIDIA GPU を使います。CPU だけでは 1 文の生成に十数秒かかり、会話になりません。\n"
            "  GPU があるのに出る場合は、NVIDIA ドライバを入れ直してください。")
    if g["driver_major"] < MIN_DRIVER:
        die(f"NVIDIA ドライバが古すぎます（{g['driver']}）。{MIN_DRIVER} 以上が必要です。\n"
            "  PyTorch（CUDA 12.8 版）の要件です。https://www.nvidia.com/Download/index.aspx から最新のドライバに更新して、もう一度実行してください。\n"
            "  （このスクリプトはドライバを更新しません）")
    if g["cc"] < MIN_COMPUTE_CAPABILITY:
        die(f"GPU（{g['name']}、Compute Capability {g['cc']}）が古すぎます。{MIN_COMPUTE_CAPABILITY} 以上（RTX 20 シリーズ以降）が必要です。")
    if external:
        if g["vram_mib"] / 1024 < 4 * RAM_VRAM_TOLERANCE or hw["ram_total_gib"] < 8 * RAM_VRAM_TOLERANCE:
            die("外部の LLM を使う場合でも、TTS 用に VRAM 4GB 以上・メモリ 8GB 以上が必要です。")


# ---------------------------------------------------------------- プロファイル選択 ----

def meets(hw: dict, name: str) -> bool:
    p = PROFILES[name]
    vram_gib = hw["gpu"]["vram_mib"] / 1024
    return vram_gib >= p["min_vram_gib"] * RAM_VRAM_TOLERANCE and hw["ram_total_gib"] >= p["min_ram_gib"] * RAM_VRAM_TOLERANCE


def component_state(install: Path, name: str) -> bool:
    """その部品がすでに導入済みか（容量の見積もりから差し引く）"""
    return {
        "llama.cpp": (install / "llama.cpp" / "llama-server.exe").exists(),
        "tts_env": (install / "tts" / ".venv" / ".setup_ok").exists(),
        "tts_models": (install / "hf-home" / ".tts_prefetched").exists(),
        "stt_env": (install / "stt" / ".venv" / ".setup_ok").exists(),
        "stt_model": (install / "hf-home" / ".stt_prefetched").exists(),
        "app_env": (install / "app" / ".venv" / ".setup_ok").exists(),
        "ui": (install / "app" / "html" / ".portable_overlay").exists(),
    }[name]


def model_present(install: Path, name: str) -> bool:
    m = PROFILES[name]["model"]
    f = install / "models" / m["file"]
    return f.exists() and f.stat().st_size >= m["size_bytes"]


def _item(label: str, dl: float, disk: float, done: bool = False, partial_gb: float = 0.0) -> dict:
    """done: 導入済み。partial_gb: 取りかけのダウンロード済みの量（残りから差し引く）"""
    if done:
        return {"label": label, "dl": 0.0, "disk": 0.0, "done": True, "partial": 0.0}
    got = min(partial_gb, dl)
    return {"label": label, "dl": dl - got, "disk": max(0.0, disk - got), "done": False, "partial": got}


def model_partial_gb(install: Path, size_bytes: int) -> float:
    d = install / "models" / ".cache" / "huggingface" / "download"
    best = 0
    if d.is_dir():
        for f in d.glob("*.incomplete"):
            best = max(best, f.stat().st_size)
    return min(best, size_bytes) / GIB


def _components(install: Path, skip: tuple = ()) -> list[dict]:
    out = []
    for key, (dl, disk) in SIZES_GB.items():
        if key in skip:
            continue
        out.append(_item(SIZE_LABELS[key], dl, disk, component_state(install, key)))
    return out


def _finish_plan(items: list[dict]) -> dict:
    dl = sum(i["dl"] for i in items)
    disk = sum(i["disk"] for i in items)
    return {"items": items, "download_gb": dl, "disk_gb": disk, "need_gb": disk + DISK_MARGIN_GB}


def plan_for(install: Path, name: str) -> dict:
    m = PROFILES[name]["model"]
    gb = m["size_bytes"] / GIB
    items = [_item(f"LLM モデル {m['file']}", gb, gb, model_present(install, name), model_partial_gb(install, m["size_bytes"]))]
    items += _components(install)
    plan = _finish_plan(items)
    plan["model_gb"] = gb
    return plan


def plan_external(install: Path) -> dict:
    """外部の推論サーバーを使うとき: llama.cpp と GGUF は不要"""
    return _finish_plan(_components(install, skip=("llama.cpp",)))


def strata_installer_args(install: Path, port: int, vision: str) -> list[str]:
    """Strata のインストーラー（START-HERE.bat）に渡す引数。モデルの選択は Strata 側の画面に任せる"""
    reserve = ctl.STRATA_RESERVE_MIB + (ctl.STRATA_VISION_GPU_MIB if vision == "gpu" else 0)
    return ["--setup", "--port", str(port), "--data-dir", str(install / "strata-data"),
            "--vram-reserve-mib", str(reserve), "--vision", vision or "no"]


def strata_installed(install: Path) -> bool:
    info = ctl.strata_info(install)
    return bool(info and info.get("bat"))


def strata_partial_gb(install: Path) -> float:
    d = install / "strata-data"
    total = 0
    if d.is_dir():
        for f in d.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    return total / GIB


def plan_strata(install: Path, vision: str = "no") -> dict:
    """Strata を入れるとき: GGUF と llama.cpp は不要。Strata 本体とモデルは Strata のインストーラーが取得する"""
    st = profiles.STRATA
    items = [_item("Strata（別途、Strata のインストーラーで約 70GB）", st["download_gb"], st["disk_gb"],
                   strata_installed(install), strata_partial_gb(install))]
    if vision in ("gpu", "cpu") and not strata_installed(install):
        items.append(_item("Strata の画像認識（エンコーダ約 0.9GB + 補助ツール）", 1.0, 1.2))
    items += _components(install, skip=("llama.cpp",))
    return _finish_plan(items)


def strata_eligible(hw: dict, profile: str | None, install: Path) -> bool:
    """Strata の要件（VRAM 12GB 以上・RAM 48GB 以上・ディスクは Strata 込みで足りる）を満たすとき。プロファイルは問わない"""
    st = profiles.STRATA
    if hw["gpu"]["vram_mib"] / 1024 < st["min_vram_gib"] * RAM_VRAM_TOLERANCE or hw["ram_total_gib"] < st["min_ram_gib"] * RAM_VRAM_TOLERANCE:
        return False
    return hw["disk_free_gib"] >= plan_strata(install)["need_gb"]


def choose_profile(hw: dict, install: Path, forced: str | None) -> tuple[str, list[str]]:
    notes = []
    if forced:
        if not meets(hw, forced):
            p = PROFILES[forced]
            notes.append(f"指定された {forced} は推奨要件（VRAM {p['min_vram_gib']}GB 以上・メモリ {p['min_ram_gib']}GB 以上）に届いていません。動かない可能性があります。")
        plan = plan_for(install, forced)
        if hw["disk_free_gib"] < plan["need_gb"]:
            die(f"空き容量が足りません。{forced} には約 {plan['need_gb']:.0f} GiB 必要ですが、{hw['drive']} の空きは {hw['disk_free_gib']:.1f} GiB です。")
        return forced, notes

    natural = next((n for n in PROFILE_ORDER if meets(hw, n)), None)
    if natural is None:
        low = PROFILES["low"]
        die(f"この PC では動かせません。最低でも VRAM {low['min_vram_gib']}GB・メモリ {low['min_ram_gib']}GB が必要です。\n"
            f"  検出: VRAM {hw['gpu']['vram_mib'] / 1024:.1f} GiB / メモリ {hw['ram_total_gib']:.1f} GiB")
    start = PROFILE_ORDER.index(natural)
    for name in PROFILE_ORDER[start:]:
        plan = plan_for(install, name)
        if hw["disk_free_gib"] >= plan["need_gb"]:
            if name != natural:
                notes.append(f"スペック上は {natural} ですが、空き容量が足りないため {name} に下げました。")
            return name, notes
        notes.append(f"{name}: 約 {plan['need_gb']:.0f} GiB 必要 > 空き {hw['disk_free_gib']:.1f} GiB")
    low_plan = plan_for(install, "low")
    die(f"空き容量が足りません。最小の構成（low）でも約 {low_plan['need_gb']:.0f} GiB 必要です（{hw['drive']} の空き: {hw['disk_free_gib']:.1f} GiB）。\n"
        "  別のドライブを --install-dir で指定するか、空きを増やしてください。")


def llm_settings(name: str, hw: dict) -> dict:
    return _llm_settings(name, hw["gpu"]["vram_mib"])


def show_plan(hw: dict, name: str, notes: list[str], plan: dict, llm: dict, install: Path, ports: dict, args=None):
    if name in ("external", "strata"):
        say("\n=== 選んだ構成 ===")
        if name == "strata":
            say("  LLM : Strata（Qwen3.8-Flash-Next）を導入して使う。llama.cpp と同梱のモデルは入れません")
        else:
            say(f"  LLM : 外部の推論サーバーを使う（{args.llm_url}）。llama.cpp とモデルは入れません")
            if settings_store.is_cloud_url(args.llm_url):
                say("  ※ 会話の内容が外部のサービスに送られます。")
        say("  TTS : Irodori-TTS v4.1 Small（int8）／ STT: faster-whisper small（CPU）")
        for n in notes:
            say(f"  ※ {n}")
        _show_items(hw, plan, install, ports)
        return
    p = PROFILES[name]
    say("\n=== 選んだプロファイル ===")
    say(f"  {name}: {p['label']}")
    say(f"  LLM : {p['model']['repo']} / {p['model']['file']}（{p['model']['size_bytes'] / GIB:.1f} GiB）")
    if p["llm_mode"] == "moe_layers":
        say(f"        専門家（MoE）のうち {llm['info']['n_cpu_moe']}/{p['moe_layers']} 層を CPU 側に置く（LLM の VRAM 見込み 約 {llm['info']['llm_vram_estimate_mib'] / 1024:.1f} GiB）")
    else:
        say(f"        VRAM に収まる分だけ GPU に載せる（TTS 用に約 {llm['fit_target_mib'] / 1024:.1f} GiB の余白を残す）")
    say("  TTS : Irodori-TTS v4.1 Small（int8）／ STT: faster-whisper small（CPU）")
    for n in notes:
        say(f"  ※ {n}")
    g = hw["gpu"]
    if g["vram_free_mib"] < (TTS_VRAM_MIB + 2500):
        say(f"  ※ GPU の空きメモリが {g['vram_free_mib'] / 1024:.1f} GiB しかありません。他のアプリ（ゲームや 3D ソフト等）を閉じると安定します。")
    _show_items(hw, plan, install, ports)


def _minutes(gb: float, mbps: float) -> str:
    """回線の実効速度を公称の 80% として、ダウンロードだけの概算時間"""
    m = gb * 1024 * 8 / (mbps * 0.8) / 60
    if m < 1:
        return "1 分未満"
    if m >= 120:
        return f"約 {m / 60:.1f} 時間"
    return f"約 {m:.0f} 分"


def _show_items(hw: dict, plan: dict, install: Path, ports: dict):
    resumed = any(i["partial"] > 0 for i in plan["items"]) or any(i["done"] for i in plan["items"])
    say("\n=== インストール内容（" + ("途中まで済んでいる分を差し引いた、残りの量" if resumed else "これから必要な分") + "） ===")
    say(f"  {'項目':<46}{'ダウンロード':>12}{'ディスク':>10}")
    for i in plan["items"]:
        if i["done"]:
            say(f"  {i['label']:<44}  導入済み")
        else:
            extra = f"（取得済み {i['partial']:.1f} GiB を差し引き）" if i["partial"] > 0 else ""
            say(f"  {i['label']:<44}{i['dl']:>8.1f} GiB{i['disk']:>7.1f} GiB {extra}")
    free = hw["disk_free_gib"]
    say(f"  {'ダウンロード量の合計':<44}{plan['download_gb']:>8.1f} GiB")
    say(f"  {'ディスク使用量の合計（展開後。余裕 ' + format(DISK_MARGIN_GB, '.0f') + ' GiB を除く）':<40}{'':>8}{plan['disk_gb']:>7.1f} GiB")
    say(f"  インストール先の空き容量: {free:.1f} GiB（{hw['drive']}）→ 導入後の残り 約 {free - plan['need_gb']:.1f} GiB（余裕 {DISK_MARGIN_GB:.0f} GiB を含めて計算）")
    dl = plan["download_gb"]
    say(f"  ダウンロードの目安: 100Mbps で{_minutes(dl, 100)}、1Gbps で{_minutes(dl, 1000)}"
        "（回線の実効速度を公称の 80% として計算。展開・環境構築の時間は別にかかります）")
    say(f"  インストール先: {install}")
    say(f"  ポート: LLM {ports['llm']} / TTS {ports['tts']} / STT {ports['stt']} / アプリ {ports['app']}（すべて 127.0.0.1 のみ）")


def strata_box(hw: dict, install: Path, profile: str | None = None):
    st = profiles.STRATA
    line = "=" * 70
    vram = hw["gpu"]["vram_mib"] / 1024
    pname = f"{profile}: {PROFILES[profile]['label']}" if profile in PROFILES else "同梱"
    better = {"high": "同梱の high（Qwen3.6-35B-A3B）より返答の質が上がります", "mid": "同梱の mid（Gemma 4 26B-A4B）より大幅に高性能です",
              "low": "同梱の low（Gemma 4 E4B）より大幅に高性能です"}.get(profile, "同梱の LLM より大幅に高性能です")
    say("\n" + line)
    say("  おすすめ: この PC なら Strata（Qwen3.8-Flash-Next）が動きます")
    say(f"  {better}。こちらを推奨します。")
    say(line)
    say(f"  ・ダウンロード量: 約 {st['download_gb']} GB（ディスクは約 {st['disk_gb']} GB 必要）。初回の導入は時間がかかります")
    if vram < 20:
        say(f"  ・この PC の VRAM は {vram:.0f}GB です。VRAM が小さいほど専門家のキャッシュが小さくなり、速度が下がります。")
        say("    目安（Strata の README、RTX 5070 12GB・RAM 64GB の実測）: IQ2_XS で約 79 tokens/s、IQ3_S で約 53 tokens/s")
    say("  ・Strata は 1 リクエストずつしか処理しません（会話が重なると順番待ちになります）")
    say("  ・起動時に 1〜3 分、PC が重くなります（モデルを RAM に読み込むため。初回はさらに長い）")
    say("  ・Strata は別のソフトウェアです（ライセンスは Strata 側。MIT + モデルごとのライセンス）")
    say(f"  ・要件: VRAM {st['min_vram_gib']}GB 以上 / RAM {st['min_ram_gib']}GB 以上 / ディスク約 {st['disk_gb']}GB。RAM 32GB では Coder 版（コード向け）しか載らず、日本語の雑談に弱いので案内しません")
    say("  ・Strata が VRAM を埋めすぎないよう、TTS 用に約 3.5GB の余白を設定します")
    say("")
    say("  [1] Strata を導入して使う（推奨）")
    say("      → インストール先の strata\\ に取得し、Strata のインストーラーを別ウィンドウで起動します。")
    say("        モデルの選択とダウンロードは Strata 側の画面で行います。")
    say("  [2] Strata はもう入っている（URL を入力して使う）")
    say(f"  [3] 同梱の LLM（{pname}）で続ける")
    say(line)


# ---------------------------------------------------------------- ダウンロード部品 ----

download = ctl.download


def run(cmd, cwd=None, env=None, check=True, quiet=False) -> subprocess.CompletedProcess:
    shown = " ".join(str(c) for c in cmd)
    say(f"  $ {shown[:200]}")
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env)
    if check and proc.returncode != 0:
        die(f"コマンドが失敗しました（コード {proc.returncode}）: {shown[:200]}")
    return proc


class Installer:
    def __init__(self, install: Path, cfg: dict, hw: dict):
        self.root = install
        self.cfg = cfg
        self.hw = hw
        self.uv = None
        self.env = None

    # ---- 準備 ----
    def prepare_env(self):
        r = self.root
        self.env = ctl.base_env(self.cfg, r)
        self.env.update({
            "UV_CACHE_DIR": str(r / ".uv" / "cache"),
            "UV_PYTHON_INSTALL_DIR": str(r / ".uv" / "python"),
            "UV_PYTHON_PREFERENCE": "only-managed",   # PC にある別の Python を使わず、uv の Python をインストール先に入れる
            "UV_HTTP_TIMEOUT": "300",
        })
        self.env.pop("UV_PROJECT_ENVIRONMENT", None)
        uv = shutil.which("uv")
        local = SRC_DIR / "tools" / "uv.exe"
        self.uv = str(local) if local.exists() else uv
        if not self.uv:
            die("uv が見つかりません。setup.bat から実行してください。")
        # git: 無ければ持ち運べる MinGit を入れる
        if not shutil.which("git"):
            mingit = r / ".tools" / "mingit"
            if not (mingit / "cmd" / "git.exe").exists():
                say("\n[準備] git が見つからないので、持ち運べる Git（MinGit）を入れます。")
                zpath = r / ".tools" / "mingit.zip"
                download(PINNED["mingit_url"], zpath, "MinGit")
                with zipfile.ZipFile(zpath) as z:
                    z.extractall(mingit)
                zpath.unlink()
            self.env["PATH"] = str(mingit / "cmd") + os.pathsep + self.env["PATH"]

    def uv_run(self, args, cwd=None):
        return run([self.uv] + args, cwd=cwd, env=self.env)

    # ---- ファイルの配置（インストール先がこのフォルダ以外のとき） ----
    def deploy_files(self):
        if self.root == SRC_DIR:
            return
        say("\n[配置] プログラムのファイルをインストール先にコピーします。")
        ignore = shutil.ignore_patterns(".venv", "__pycache__", ".env", ".env.local", "data", "html", "*.db", "*.db-*",
                                        "recorded_voices", "logs")
        for name in ["ctl.py", "profiles.py", "settings_store.py", "configure.py", "setup.py", "start.bat", "stop.bat",
                     "setup.bat", "configure.bat", "README.md", "LICENSE"]:
            if (SRC_DIR / name).exists():
                shutil.copy2(SRC_DIR / name, self.root / name)
        for d in ["app", "stt", "voices", "html-overrides", "tools"]:
            shutil.copytree(SRC_DIR / d, self.root / d, dirs_exist_ok=True, ignore=ignore)
        # キャラクターはユーザーが編集するので、無いファイルだけコピーする
        for f in (SRC_DIR / "characters").rglob("*"):
            if f.is_file():
                dst = self.root / "characters" / f.relative_to(SRC_DIR / "characters")
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)

    # ---- llama.cpp ----
    def install_llama(self):
        dest = self.root / "llama.cpp"
        if (dest / "llama-server.exe").exists():
            say("\n[llama.cpp] 導入済み")
            return
        tag = PINNED["llama_tag"]
        say(f"\n[llama.cpp] {tag}（CUDA 12 ビルド）を入れます。")
        req = urllib.request.Request(f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{tag}",
                                     headers={"User-Agent": "koemado"})
        assets = json.load(urllib.request.urlopen(req, timeout=60))["assets"]
        dl = self.root / ".downloads"
        for pattern in PINNED["llama_asset_patterns"]:
            rx = re.compile(pattern.format(tag=tag))
            hit = sorted(a for a in assets if rx.match(a["name"]))
            if not hit:
                die(f"llama.cpp {tag} に {pattern} に合うファイルがありません。")
            asset = hit[-1]
            zpath = dl / asset["name"]
            download(asset["browser_download_url"], zpath)
            with zipfile.ZipFile(zpath) as z:
                z.extractall(dest)
            zpath.unlink()
        if not (dest / "llama-server.exe").exists():
            die("llama-server.exe が展開されませんでした。")

    # ---- GGUF ----
    def install_model(self, name: str):
        from huggingface_hub import hf_hub_download
        m = PROFILES[name]["model"]
        if model_present(self.root, name):
            say("\n[LLM モデル] 導入済み")
            return
        say(f"\n[LLM モデル] {m['repo']} / {m['file']}（{m['size_bytes'] / GIB:.1f} GiB）をダウンロードします。")
        os.environ["HF_HOME"] = str(self.root / "hf-home")
        hf_hub_download(m["repo"], m["file"], local_dir=str(self.root / "models"))
        if not model_present(self.root, name):
            die("モデルのダウンロードが完了していません。もう一度 setup.bat を実行してください（続きから再開します）。")

    # ---- TTS ----
    def install_tts(self):
        tts = self.root / "tts"
        commit = PINNED["tts_commit"]
        marker = tts / ".venv" / ".setup_ok"
        if marker.exists() and marker.read_text().strip() == commit:
            say("\n[TTS] 導入済み")
        else:
            say("\n[TTS] Irodori-TTS-Server を取得して環境を作ります（PyTorch が大きいので時間がかかります）。")
            if not (tts / ".git").exists():
                run(["git", "clone", PINNED["tts_repo"], tts], env=self.env)
            run(["git", "fetch", "origin"], cwd=tts, env=self.env, check=False)
            run(["git", "checkout", "--quiet", commit], cwd=tts, env=self.env)
            self.uv_run(["sync", "--frozen", "--extra", "cu128", "--python", PINNED["tts_python"]], cwd=tts)
            marker.write_text(commit)
        # 声（参照音声）
        (tts / "voices").mkdir(exist_ok=True)
        for f in (SRC_DIR / "voices").iterdir():
            if f.is_file() and f.suffix.lower() in settings_store.AUDIO_EXTS:
                shutil.copy2(f, tts / "voices" / f.name)
        shipped = SRC_DIR / "voices" / "voices_meta.json"
        if shipped.exists() and not settings_store.voices_meta_path(self.root).exists():
            settings_store.voices_meta_path(self.root).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(shipped, settings_store.voices_meta_path(self.root))
        # 重みのダウンロードと動作確認（サーバーを一度起動して、読み込めたら止める）
        pre = self.root / "hf-home" / ".tts_prefetched"
        if not pre.exists():
            say("\n[TTS] モデルの重みを取得し、GPU で読み込めるか確認します。")
            port = ctl.pick_ports({"llm": 1, "tts": 18088, "stt": 1, "app": 1})["tts"]
            cmd, cwd, env = ctl.service_command({**self.cfg, "ports": {**self.cfg["ports"], "tts": port}}, "tts", self.root)
            env["HF_HUB_DISABLE_PROGRESS_BARS"] = "0"
            proc = subprocess.Popen(cmd, cwd=cwd, env=env)
            try:
                deadline = time.time() + 1800
                while time.time() < deadline:
                    if proc.poll() is not None:
                        die("TTS サーバーが起動中に終了しました。上のメッセージを確認してください（VRAM 不足・ドライバの可能性があります）。")
                    if ctl.http_ok(f"http://127.0.0.1:{port}/health", must_contain='"loaded":true') or \
                            ctl.http_ok(f"http://127.0.0.1:{port}/health", must_contain='"loaded": true'):
                        break
                    time.sleep(3)
                else:
                    die("TTS の読み込みが時間内に終わりませんでした。")
            finally:
                ctl.kill_tree(proc.pid)
            pre.write_text("ok")
            say("  TTS の読み込みを確認しました。")

    # ---- STT ----
    def install_stt(self):
        stt = self.root / "stt"
        req = stt / "requirements.txt"
        digest = hashlib.sha256(req.read_bytes()).hexdigest()
        marker = stt / ".venv" / ".setup_ok"
        if not (marker.exists() and marker.read_text().strip() == digest):
            say("\n[STT] faster-whisper の環境を作ります。")
            self.uv_run(["venv", stt / ".venv", "--python", PINNED["python"], "--allow-existing"])
            self.uv_run(["pip", "install", "--python", stt / ".venv" / "Scripts" / "python.exe", "-r", req])
            marker.write_text(digest)
        else:
            say("\n[STT] 導入済み")
        pre = self.root / "hf-home" / ".stt_prefetched"
        if not pre.exists():
            say(f"[STT] Whisper {PINNED['stt_model']} のモデルを取得します。")
            code = ("from faster_whisper import WhisperModel; "
                    f"WhisperModel('{PINNED['stt_model']}', device='cpu', compute_type='int8'); print('ok')")
            run([stt / ".venv" / "Scripts" / "python.exe", "-c", code], env=self.env)
            pre.write_text("ok")

    # ---- AIAvatarKit ----
    def install_app(self):
        app = self.root / "app"
        req = app / "requirements.txt"
        digest = hashlib.sha256(req.read_bytes()).hexdigest()
        marker = app / ".venv" / ".setup_ok"
        py = app / ".venv" / "Scripts" / "python.exe"
        if not (marker.exists() and marker.read_text().strip() == digest):
            say("\n[AIAvatarKit] 環境を作ります。")
            self.uv_run(["venv", app / ".venv", "--python", PINNED["python"], "--allow-existing"])
            self.uv_run(["pip", "install", "--python", py, "-r", req])
            marker.write_text(digest)
        else:
            say("\n[AIAvatarKit] 導入済み")
        # silero-vad は初回に GitHub から torch.hub で取得し、信頼確認で止まる。先に確認済みの状態で保存しておく
        hub = self.root / "torch-home" / "hub" / "snakers4_silero-vad_master"
        if not hub.exists():
            say("[AIAvatarKit] 音声区間検出（silero-vad）のモデルを取得します。")
            code = ("import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', onnx=True, "
                    "force_onnx_cpu=True, trust_repo=True); print('ok')")
            run([py, "-c", code], env=self.env)
        # ブラウザ UI
        html = app / "html"
        overlay_mark = html / ".portable_overlay"
        if not overlay_mark.exists():
            say("[AIAvatarKit] ブラウザ UI を取得します。")
            tarball = self.root / ".downloads" / "aiavatarkit-ui.tar.gz"
            download(PINNED["aiavatar_ui_tarball"], tarball, "AIAvatarKit UI")
            tmp = self.root / ".downloads" / "ui-extract"
            shutil.rmtree(tmp, ignore_errors=True)
            prefix = PINNED["aiavatar_ui_prefix"]
            with tarfile.open(tarball) as t:
                members = [m for m in t.getmembers() if m.name == prefix or m.name.startswith(prefix + "/")]
                if not members:
                    die("UI のアーカイブに html/ が見つかりません。")
                t.extractall(tmp, members=members)
            shutil.rmtree(html, ignore_errors=True)
            shutil.copytree(tmp / prefix, html)
            shutil.rmtree(tmp, ignore_errors=True)
            tarball.unlink()
            # 同梱のプレースホルダ画像で、サンプルのキャラクター画像を差し替える
            ov = self.root / "html-overrides"
            if ov.exists():
                shutil.copytree(ov, html, dirs_exist_ok=True)
            # 立ち絵: 画像は設定画面で選んだキャラクターのものを /character-image/ から配信する
            index = html / "index.html"
            text = index.read_text(encoding="utf-8")
            text = re.sub(r"images/(neutral|joy|angry|sorrow|fun|surprised|eyes_closed)\.png", r"../character-image/\1", text)
            if "image-panel.js" not in text:
                text = text.replace("</body>", '    <script src="image-panel.js"></script>\n</body>', 1)
            index.write_text(text, encoding="utf-8")
            overlay_mark.write_text("ok")

    # ---- Strata（別ソフトウェア。インストール先の strata\\ に取得し、Strata 自身のインストーラーを起動する） ----
    def clone_strata(self):
        st = profiles.STRATA
        d = self.root / "strata"
        if not (d / ".git").exists():
            say("\n[Strata] Strata を取得します（別ソフトウェア。ライセンスは Strata 側）。")
            run(["git", "clone", st["repo"], d], env=self.env)
        run(["git", "fetch", "origin"], cwd=d, env=self.env, check=False)
        run(["git", "checkout", "--quiet", st["commit"]], cwd=d, env=self.env)

    def launch_strata_installer(self, port: int):
        """Strata の START-HERE.bat を別ウィンドウで起動する。モデルの選択とダウンロードは Strata 側の画面で行う"""
        d = self.root / "strata"
        if ctl.strata_info(self.root) and ctl.strata_info(self.root).get("bat"):
            say("\n[Strata] 導入済み（起動は start.bat が行います）")
            return
        data = self.root / "strata-data"
        vision = getattr(self, "vision", "no") or "no"
        cmd = ["cmd", "/c", str(d / "START-HERE.bat")] + strata_installer_args(self.root, port, vision)
        say("\n[Strata] Strata のインストーラーを別ウィンドウで起動します。")
        say("  ・モデルの選択（RAM に合わせた推奨が出ます。迷ったら Enter）とダウンロード（約 70GB）は、そのウィンドウで行います。")
        say("  ・ダウンロードが終わるとモデルが起動します（PC が 1〜3 分重くなります）。そのあとは start.bat から使えます。")
        say(f"  ・ポートは {port}、モデルの保存先は {data} です。画像認識: {vision}")
        say("  ・Strata のモデルがダウンロードできる RAM に合っているかは、Strata の画面の推奨に従ってください。")
        env = dict(self.env)
        subprocess.Popen(cmd, cwd=str(d), env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)

    # ---- 設定の書き出し ----
    def write_config(self, settings: dict):
        ctl.save_config(self.cfg, self.root)
        ctl.write_env(self.cfg, self.root)
        settings_store.save_settings(settings, self.root)

    def cleanup(self):
        """ダウンロードのキャッシュを消して容量を戻す（再実行時に必要になれば取り直す）"""
        for d in [self.root / ".downloads", self.root / ".uv" / "cache"]:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- main ----

def build_config(name: str, hw: dict, ports: dict, args) -> dict:
    """config.json: 導入時に決まる低レベルの値。声・LLM・キャラクターは config/settings.json に分ける"""
    return {
        "profile": name,
        "gpu": {"index": hw["gpu"]["index"], "name": hw["gpu"]["name"], "vram_mib": hw["gpu"]["vram_mib"],
                "simulated": bool(hw["simulated"])},
        "ports": ports,
        "tts": {"hf_checkpoint": PINNED["tts_hf_checkpoint"]},
        "stt": {"model": PINNED["stt_model"], "cpu_threads": max(2, min(8, hw["cores"]))},
        "start_timeout_sec": {"llm": 900, "tts": 900, "stt": 300, "app": 180},
        "external_wait_sec": 120,
        "pinned": {k: PINNED[k] for k in ("llama_tag", "tts_commit", "tts_hf_checkpoint")},
    }


def initial_settings(args, name: str) -> dict:
    """config/settings.json の初期値。すでにあれば、指定された項目だけ更新する"""
    s = settings_store.load_settings(Path(args.install_dir).resolve())
    if settings_store.settings_path(Path(args.install_dir).resolve()).exists() and not args.llm_explicit:
        return s   # 再実行では、設定画面で変えた内容を上書きしない
    s["weather_location"] = args.weather_location
    if args.llm == "strata":
        s["llm"]["mode"] = "external"
        ext = s["llm"]["external"]
        pr = settings_store.EXTERNAL_PRESETS["strata"]
        ext.update({"preset": "strata", "base_url": args.llm_url.rstrip("/"), "model": "", "reasoning": pr["reasoning"],
                    "serialize": True, "tools": True, "managed": "strata"})
        s["llm"]["temperature"] = 0.8
        s["llm"]["esp"] = {"enabled": False}
        s["llm"]["vision"] = {"enabled": (args.vision in ("gpu", "cpu")), "store_images": False}
    elif args.llm == "external":
        s["llm"]["mode"] = "external"
        ext = s["llm"]["external"]
        ext["base_url"] = args.llm_url.rstrip("/")
        ext["model"] = args.llm_model or ext.get("model", "")
        if args.llm_api_key:
            ext["api_key"] = args.llm_api_key
        ext["managed"] = ""
        preset = next((k for k, v in settings_store.EXTERNAL_PRESETS.items() if v["base_url"].rstrip("/") == ext["base_url"]), "")
        ext["preset"] = preset
        if preset:
            for k in ("reasoning", "serialize", "tools"):
                ext[k] = settings_store.EXTERNAL_PRESETS[preset][k]
    else:
        s["llm"]["mode"] = "bundled"
        s["llm"]["profile"] = name
    return s


def ask_text(prompt: str, default: str = "") -> str:
    try:
        ans = input(prompt).strip()
    except EOFError:
        return default
    return ans or default


def ask_vision(args) -> str:
    """Strata の画像認識。--yes / --dry-run では聞かず「使わない」"""
    if args.yes or args.dry_run:
        return "no"
    say("\n画像認識（カメラや写真について話せるようにする）を有効にしますか？")
    say("  [1] 使わない（既定）")
    say("  [2] GPU（推奨）: 1 枚 0.1〜0.5 秒。VRAM を約 1.4GB 使う（TTS 用の余白に上乗せします）")
    say("  [3] CPU: 1 枚 10〜30 秒。VRAM を使わない")
    say("  ※ Strata のインストーラー側でも、同じ内容を聞かれることがあります（その場合はこの選択に合わせてください）。")
    ans = ask_text("番号を入力 [1]: ", "1")
    return {"2": "gpu", "3": "cpu"}.get(ans, "no")


def ask_llm_kind(args, offer_strata: bool, hw: dict, install: Path, profile: str | None = None) -> str:
    """LLM の方式を決める。戻り値: bundled / external / strata

    --yes や --dry-run では質問せず、同梱の LLM で続ける（Strata の約 70GB を勝手に入れない）。
    """
    if args.llm:
        return args.llm
    if offer_strata:
        strata_box(hw, install, profile)
        if args.yes or args.dry_run:
            say("  （--yes / --dry-run では質問しません。同梱の LLM で続けます。Strata を使うには --llm strata を付けて実行してください）")
            return "bundled"
        ans = ask_text("番号を入力 [1]: ", "1")
        if ans == "2":
            args.llm_url = ask_text(f"Strata の base_url（/v1 まで） [http://127.0.0.1:{profiles.STRATA['default_port']}/v1]: ",
                                    f"http://127.0.0.1:{profiles.STRATA['default_port']}/v1")
            args.llm_model = ""
            return "external"
        return "strata" if ans == "1" else "bundled"
    if args.yes or args.dry_run:
        return "bundled"
    say("LLM（返答を考えるモデル）をどうしますか？")
    say("  1) 同梱の LLM を使う（llama.cpp とモデルを自動で入れる。おすすめ）")
    say("  2) 自分で用意した推論サーバーを使う（LM Studio / Ollama / vLLM / 別 PC / クラウド API など）")
    ans = ask_text("番号を入力 [1]: ", "1")
    if ans != "2":
        return "bundled"
    say("\nプリセット: " + " / ".join(f"{k}={v['label']}" for k, v in settings_store.EXTERNAL_PRESETS.items()))
    preset = ask_text("プリセット名（空なら URL を直接入力）: ")
    default_url = settings_store.EXTERNAL_PRESETS.get(preset, {}).get("base_url", "")
    url = ask_text(f"base_url（/v1 まで） [{default_url}]: ", default_url)
    if not url:
        die("URL が必要です。")
    args.llm_url = url
    args.llm_model = ask_text("モデル名（空なら後で設定画面で選ぶ）: ")
    if settings_store.is_cloud_url(url):
        say("※ 会話の内容が外部のサービスに送られます。")
        args.llm_api_key = ask_text("API キー（任意）: ")
    return "external"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="koemado セットアップ")
    ap.add_argument("--profile", choices=PROFILE_ORDER, help="プロファイルを指定（既定: PC のスペックから自動）")
    ap.add_argument("--yes", "-y", action="store_true", help="確認せずに進める")
    ap.add_argument("--dry-run", action="store_true", help="検出と計画の表示だけ行い、何も入れない")
    ap.add_argument("--install-dir", default=str(SRC_DIR), help="インストール先（既定: このフォルダ）")
    ap.add_argument("--simulate", help='模擬入力（テスト用）例: "vram=8,ram=32,disk=60,cores=8,driver=555"')
    ap.add_argument("--weather-location", default="東京", help="天気ツールの既定の地名（既定: 東京）")
    ap.add_argument("--llm", choices=["bundled", "external", "strata"], help="bundled=同梱の llama-server（既定） / external=自分で用意した OpenAI 互換サーバー / strata=Strata を導入して使う（VRAM 12GB・RAM 48GB 以上の PC 向け。約 70GB）")
    ap.add_argument("--vision", choices=["gpu", "cpu", "no"], help="--llm strata のときの画像認識: gpu（推奨。1 枚 0.1〜0.5 秒、VRAM 約 1.4GB）/ cpu（1 枚 10〜30 秒、VRAM を使わない）/ no（使わない。既定）")
    ap.add_argument("--llm-url", default="", help="external のときの base_url（/v1 まで）")
    ap.add_argument("--llm-model", default="", help="external のときのモデル名")
    ap.add_argument("--llm-api-key", default="", help="external のときの API キー（任意。config/settings.json に保存される）")
    args = ap.parse_args()

    install = Path(args.install_dir).resolve()
    args.install_dir = str(install)
    sim = parse_simulate(args.simulate)
    say("koemado セットアップ\n")
    hw = detect_hardware(install, sim)
    print_hardware(hw)
    args.llm_explicit = bool(args.llm)
    # 先にプロファイルを決める（Strata の案内は、選ばれたプロファイルが high のときだけ）
    name, notes, llm = None, [], None
    if args.llm in (None, "bundled"):
        check_requirements(hw)
        name, notes = choose_profile(hw, install, args.profile)
    offer = args.llm is None and strata_eligible(hw, name, install)
    kind = ask_llm_kind(args, offer, hw, install, name)
    args.llm_explicit = args.llm_explicit or kind in ("external", "strata")
    args.llm = kind
    external = kind in ("external", "strata")
    if kind == "external" and not args.llm_url:
        die("--llm external には --llm-url（例: http://127.0.0.1:8081/v1）が必要です。")
    if external:
        check_requirements(hw, external=True)

    if kind == "strata":
        if not strata_eligible(hw, None, install) and not args.yes:
            say("※ この PC は Strata の要件（VRAM 12GB・RAM 48GB・ディスク約 80GB）を満たしていません。続けると動かない可能性があります。")
        name, notes = "strata", []
        if args.vision is None:
            args.vision = ask_vision(args)
        reserve = ctl.STRATA_RESERVE_MIB + (ctl.STRATA_VISION_GPU_MIB if args.vision == "gpu" else 0)
        notes.append(f"Strata が専門家のキャッシュで VRAM を埋めないよう、TTS 用に {reserve} MiB の余白を設定します"
                     + ("（TTS 3500 + 画像エンコーダ（GPU）1400）" if args.vision == "gpu" else "（TTS 用）"))
        notes.append("画像認識: " + {"gpu": "GPU（1 枚 0.1〜0.5 秒、VRAM を約 1.4GB 使う）", "cpu": "CPU（1 枚 10〜30 秒、VRAM を使わない）", "no": "使わない"}[args.vision])
        plan = plan_strata(install, args.vision)
        if hw["disk_free_gib"] < plan["need_gb"]:
            die(f"空き容量が足りません。Strata を含めて約 {plan['need_gb']:.0f} GiB 必要ですが、{hw['drive']} の空きは {hw['disk_free_gib']:.1f} GiB です。")
    elif kind == "external":
        name, notes = "external", []
        plan = plan_external(install)
        if hw["disk_free_gib"] < plan["need_gb"]:
            die(f"空き容量が足りません。約 {plan['need_gb']:.0f} GiB 必要ですが、{hw['drive']} の空きは {hw['disk_free_gib']:.1f} GiB です。")
    else:
        plan = plan_for(install, name)
        llm = llm_settings(name, hw)

    # ポート: 前回の設定があれば引き継ぎ、使用中なら空きに付け替える
    old_cfg = None
    if (install / STATE_FILE).exists():
        try:
            old_cfg = ctl.load_config(install)
        except Exception:
            old_cfg = None
    preferred = dict(old_cfg["ports"]) if old_cfg else dict(ctl.DEFAULT_PORTS)
    ours = {preferred[n] for n in ctl.SERVICES if old_cfg and ctl.check_health(old_cfg, n)}
    ports = ctl.pick_ports(preferred, taken_by_us=ours)
    if ports != ctl.DEFAULT_PORTS and not old_cfg:
        notes.append("既定のポートが使用中だったため、空きポートに変更しました: " + ", ".join(f"{k}={v}" for k, v in ports.items()))

    if kind == "strata":
        args.llm_url = f"http://127.0.0.1:{ports['llm']}/v1"
    show_plan(hw, name, notes, plan, llm, install, ports, args)
    if kind == "strata":
        say("  Strata のインストーラーに渡す引数: START-HERE.bat " + " ".join(strata_installer_args(install, ports["llm"], args.vision or "no")))
    if args.dry_run:
        say(f"\n[dry-run] 選ばれた構成: {name}  （何も変更していません）")
        return 0
    if not args.yes:
        # 明示的な同意（y / yes）以外はすべて中止。空の Enter も中止
        try:
            ans = input("\nこの内容でインストールしますか？ [y/N]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            ans = ""
        if ans not in ("y", "yes"):
            say("中止しました（何も変更していません）。")
            return 1

    install.mkdir(parents=True, exist_ok=True)
    cfg = build_config(name, hw, ports, args)
    inst = Installer(install, cfg, hw)
    started = time.time()
    inst.prepare_env()
    inst.deploy_files()
    if not external:
        inst.install_llama()
        inst.install_model(name)
    if kind == "strata":
        inst.clone_strata()
    inst.install_stt()
    inst.install_app()
    inst.install_tts()
    inst.write_config(initial_settings(args, name))
    inst.cleanup()
    if kind == "strata":
        inst.vision = args.vision or "no"
        inst.launch_strata_installer(ports["llm"])
    say(f"\n=== 導入が完了しました（{(time.time() - started) / 60:.1f} 分）===")
    if kind == "strata":
        say("  ※ Strata のインストーラー（別ウィンドウ）が終わってから start.bat を実行してください。")
    say(f"  起動: {install / 'start.bat'} をダブルクリック")
    say(f"  停止: {install / 'stop.bat'}")
    say(f"  設定（声・LLM・キャラクター）: 起動後に http://127.0.0.1:{ports['app']}/settings、または {install / 'configure.bat'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
