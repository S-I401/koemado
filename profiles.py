"""LLM プロファイル表と、llama-server の引数の決め方（標準ライブラリのみ。setup.py / ctl.py / 設定画面が共有する）。

モデルの差し替えはこの表を直す。
"""
import math

GIB = 1024 ** 3

# llm_mode:
#   "moe_layers": 専門家（MoE）の重みを CPU に置く層数を VRAM から計算する（--n-cpu-moe）。VRAM 8GB 級向け
#   "fit"      : llama.cpp の --fit に任せ、--fit-target で TTS 用の余白を残す
PROFILES = {
    "high": {
        "label": "高性能（Qwen3.6-35B-A3B）",
        "min_vram_gib": 20, "min_ram_gib": 48,
        "model": {"repo": "unsloth/Qwen3.6-35B-A3B-GGUF", "file": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf", "size_bytes": 22134528992},
        "ctx": 8192,
        "llm_mode": "fit", "fit_margin_mib": 3300,
        "extra_args": ["-fa", "on"],
    },
    "mid": {
        "label": "標準（Gemma 4 26B-A4B）",
        "min_vram_gib": 8, "min_ram_gib": 32,
        "model": {"repo": "unsloth/gemma-4-26B-A4B-it-qat-GGUF", "file": "gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf", "size_bytes": 14249047104},
        "ctx": 8192,
        "llm_mode": "moe_layers",
        # 実測値（ctx 8192）: 密な重み + KV + 計算バッファ = 約 2.8GB、専門家 1 層ぶん = 約 0.39GB、全 30 層
        "moe_layers": 30, "gpu_fixed_mib": 2810, "moe_layer_mib": 390,
        "extra_args": ["-fa", "on"],
    },
    "low": {
        "label": "軽量（Gemma 4 E4B）",
        "min_vram_gib": 6, "min_ram_gib": 16,
        "model": {"repo": "google/gemma-4-E4B-it-qat-q4_0-gguf", "file": "gemma-4-E4B_q4_0-it.gguf", "size_bytes": 5154941280},
        "ctx": 8192,
        "llm_mode": "fit", "fit_margin_mib": 3300,
        "extra_args": ["-fa", "on"],
    },
}
PROFILE_ORDER = ["high", "mid", "low"]

# TTS（Irodori int8 + codec）が VRAM に載せる量と、Windows のデスクトップ用の余白（MiB）
TTS_VRAM_MIB = 2800          # 実測: Irodori int8 のピークは約 2.7GB（待機時 1.9GB）
DESKTOP_HEADROOM_MIB = 1500
# 任意の GGUF を指定したときの --fit の余白
CUSTOM_FIT_MARGIN_MIB = 3300   # --fit-target は「空き VRAM から残す量」。TTS のピーク + 余裕


def llm_settings(name: str, vram_mib: int) -> dict:
    """プロファイルと VRAM から、llama-server の引数を決める"""
    p = PROFILES[name]
    args = list(p["extra_args"])
    if p["llm_mode"] == "moe_layers":
        budget = vram_mib - TTS_VRAM_MIB - DESKTOP_HEADROOM_MIB
        gpu_layers = max(0, math.floor((budget - p["gpu_fixed_mib"]) / p["moe_layer_mib"]))
        gpu_layers = min(gpu_layers, p["moe_layers"])
        n_cpu_moe = p["moe_layers"] - gpu_layers
        args += ["-ngl", "99", "--n-cpu-moe", str(n_cpu_moe), "--fit", "off"]
        info = {"llm_vram_estimate_mib": p["gpu_fixed_mib"] + gpu_layers * p["moe_layer_mib"], "n_cpu_moe": n_cpu_moe}
        return {"args": args, "fit_target_mib": None, "info": info}
    return {"args": args, "fit_target_mib": p["fit_margin_mib"], "info": {"fit_margin_mib": p["fit_margin_mib"]}}


def custom_llm_settings() -> dict:
    return {"args": ["-fa", "on"], "fit_target_mib": CUSTOM_FIT_MARGIN_MIB, "info": {"fit_margin_mib": CUSTOM_FIT_MARGIN_MIB}}


# ---- Strata（別ソフトウェア。MIT。Qwen3.8-Flash-Next を普通のゲーミング PC で動かす）----
# 要件は Strata の README と docs/MODELS.md から: VRAM 12GB 以上、RAM 48GB 以上（IQ2_XS 以上の全エキスパート版）、
# ディスク約 80GB。RAM 32GB では Coder 版（半分の専門家を削ったコード向け）しか載らず、日本語の雑談に弱いので案内しない。
STRATA = {
    "repo": "https://github.com/Niko1221/Strata.git",
    "commit": "82f46a8c8f475f001ad76d92f58f4a4f8ffb0253",
    "min_vram_gib": 12,
    "min_ram_gib": 48,
    "disk_gb": 80,
    "download_gb": 70,
    "default_port": 8080,
}
