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


# ---- Hugging Face の GGUF 一覧から選ぶモデルの範囲（モデルブラウザ） ----
# プロファイルごとのファイルサイズ（GiB）の下限・上限・注意の閾値。根拠:
#   上限: 既定モデルの約 1.2〜1.35 倍。それより大きいと、そのプロファイルの最小スペック（min_ram_gib）では
#         モデルがメモリに載りきらず、OS・TTS・STT と取り合って極端に遅くなる。
#         high 28 = VRAM 24GB + RAM 48GB で載る範囲（既定 20.6 の約 1.35 倍）。mid 16 = RAM 32GB の約半分（既定 13.3 の約 1.2 倍）。
#         low 6 = RAM 16GB から OS・TTS・STT を引いた残り約 6GiB（既定 4.8 の約 1.25 倍）。
#   下限: 日本語の会話・ツール呼び出しが不安定になりやすい大きさ。high 8（約 12B の Q4）、mid 4（約 7B の Q4）、low 2（約 3B の Q4）。
#         プロファイルが上がるほど小さいモデルを選ぶ意味が薄いので、下限も上げている。
#   caution_gib: これを超えると「余裕が少ない」と注意を出す（上限の約 88〜90%）。
MODEL_LIMITS = {
    "high": {"min_gib": 8.0, "max_gib": 28.0, "caution_gib": 25.0},
    "mid": {"min_gib": 4.0, "max_gib": 16.0, "caution_gib": 14.0},
    "low": {"min_gib": 2.0, "max_gib": 6.0, "caution_gib": 5.4},
}
# 検出したハードからの絶対的な上限（GiB）= （RAM − OS・STT・アプリ・TTS）+（VRAM − TTS・デスクトップの余白）− KV キャッシュと計算バッファ
# どのプロファイルの範囲より広い値は使わない。数値は実測と概算（TTS: Irodori int8 のピーク VRAM 約 2.7GiB、RAM 約 3GiB）。
HW_OS_RAM_GIB = 4.0
HW_STT_RAM_GIB = 1.5
HW_APP_RAM_GIB = 1.5          # AIAvatarKit 本体・管理プロセス・ブラウザの分
HW_TTS_RAM_GIB = 3.0
HW_RUNTIME_GIB = 1.0          # KV キャッシュ（ctx 8192）と計算バッファ
DISK_MARGIN_GIB = 1.0
# dense（MoE でない）モデルが、LLM に使える VRAM のこの倍率を超えたら「CPU に回る分が多く遅い」と注意する
DENSE_SPILL_RATIO = 1.25

# MoE の --n-cpu-moe を、ファイルサイズと層数から推定する係数（gemma-4-26B-A4B の実測値 moe_layer_mib=390・gpu_fixed_mib=2810 を再現するように校正）。
# 専門家の重みはファイルの約 86%、残り（共通部）+ KV + 計算バッファで約 900MiB。
MOE_EXPERT_SHARE = 0.86
MOE_OVERHEAD_MIB = 900

# 日本語に強いと言われる系列（モデルブラウザの「日本語に強い系列だけ」）。term = 検索語、pattern = リポジトリ名の判定
JA_FAMILIES = [
    {"id": "qwen", "label": "Qwen", "term": "qwen", "pattern": r"qwen"},
    {"id": "gemma", "label": "Gemma", "term": "gemma", "pattern": r"gemma"},
    {"id": "swallow", "label": "Swallow", "term": "swallow", "pattern": r"swallow"},
    {"id": "elyza", "label": "ELYZA", "term": "elyza", "pattern": r"elyza"},
    {"id": "sarashina", "label": "Sarashina", "term": "sarashina", "pattern": r"sarashina"},
    {"id": "plamo", "label": "PLaMo", "term": "plamo", "pattern": r"plamo"},
    {"id": "karakuri", "label": "KARAKURI", "term": "karakuri", "pattern": r"karakuri"},
    {"id": "shisa", "label": "Shisa", "term": "shisa", "pattern": r"shisa"},
    {"id": "calm", "label": "CALM", "term": "calm3", "pattern": r"calm\d"},
    {"id": "llmjp", "label": "LLM-jp", "term": "llm-jp", "pattern": r"llm-jp"},
    {"id": "gptoss", "label": "gpt-oss", "term": "gpt-oss", "pattern": r"gpt-oss"},
]


def hw_cap_gib(vram_mib: float, ram_gib: float) -> float:
    """このハードで載せられるモデルの上限（GiB）"""
    ram_part = ram_gib - HW_OS_RAM_GIB - HW_STT_RAM_GIB - HW_APP_RAM_GIB - HW_TTS_RAM_GIB
    vram_part = (vram_mib - TTS_VRAM_MIB - DESKTOP_HEADROOM_MIB) / 1024
    return max(0.0, ram_part) + max(0.0, vram_part) - HW_RUNTIME_GIB


def llm_vram_gib(vram_mib: float) -> float:
    """TTS とデスクトップの分を除いた、LLM に使える VRAM（GiB）"""
    return max(0.0, (vram_mib - TTS_VRAM_MIB - DESKTOP_HEADROOM_MIB) / 1024)


def model_range(profile: str, vram_mib: float, ram_gib: float, disk_free_gib: float, existing_gib: float = 0.0) -> dict:
    """選べるファイルサイズの範囲。min/max = 実効の範囲、profile_* = プロファイルの表の値、max_by = 上限を決めたもの"""
    lim = MODEL_LIMITS.get(profile) or MODEL_LIMITS["mid"]
    hw = hw_cap_gib(vram_mib, ram_gib)
    disk = max(0.0, disk_free_gib + existing_gib - DISK_MARGIN_GIB)
    mx, by = min([(lim["max_gib"], "profile"), (hw, "hardware"), (disk, "disk")], key=lambda c: c[0])
    return {"profile": profile, "min_gib": lim["min_gib"], "max_gib": round(mx, 2), "max_by": by,
            "caution_gib": min(lim["caution_gib"], mx), "profile_min_gib": lim["min_gib"], "profile_max_gib": lim["max_gib"],
            "hw_cap_gib": round(hw, 2), "disk_cap_gib": round(disk, 2), "llm_vram_gib": round(llm_vram_gib(vram_mib), 2)}


def assess_model(rng: dict, size_gib: float, kind: str = "dense", moe_layers: int | None = None, mode: str = "fit") -> dict:
    """1 つの候補（合計サイズ）を範囲と比べる。status: ok / too_big / too_small。注意（cautions）は ok のときだけ付ける。
    kind: dense / moe。mode = 導入プロファイルの llm_mode（moe_layers なら --n-cpu-moe を計算する）"""
    reasons, cautions = [], []
    status = "ok"
    if size_gib > rng["max_gib"]:
        status = "too_big"
        why = {"profile": f"プロファイル {rng['profile']} の範囲", "hardware": "このPCのメモリと VRAM", "disk": "インストール先の空き容量"}[rng["max_by"]]
        reasons.append(f"大きすぎます：このPCの上限は {rng['max_gib']:.0f}GiB（{why}）")
    elif size_gib < rng["min_gib"]:
        status = "too_small"
        reasons.append(f"小さすぎます：日本語の会話やツール呼び出しが不安定になりやすい（下限 {rng['min_gib']:.0f}GiB）")
    if status == "ok":
        if size_gib > rng["caution_gib"]:
            cautions.append({"level": "warn", "text": f"上限（{rng['max_gib']:.0f}GiB）に近く、メモリの余裕が少なくなります。他のアプリを閉じて使ってください。"})
        if kind == "dense" and size_gib > rng["llm_vram_gib"] * DENSE_SPILL_RATIO:
            cautions.append({"level": "warn", "text": f"MoE ではない（dense）モデルで、LLM に使える VRAM（TTS の余白を除いて約 {rng['llm_vram_gib']:.1f}GiB）を大きく超えます。"
                                                     "超えた分は CPU で処理するため、応答がかなり遅くなります。"})
        if kind == "moe":
            if mode == "moe_layers" and moe_layers:
                cautions.append({"level": "info", "text": f"MoE モデルです。mid と同じ方式で、専門家の重みを CPU に置く層数（--n-cpu-moe）を VRAM から計算します（全 {moe_layers} 層。サイズからの推定値）。"})
            elif mode == "moe_layers":
                cautions.append({"level": "warn", "text": "MoE モデルのようですが、層数を読み取れなかったため --n-cpu-moe の計算を適用できません。--fit に任せるので、速度は保証できません。"})
            else:
                cautions.append({"level": "info", "text": "MoE モデルです。このプロファイルでは、--fit に VRAM への載せ方を任せます。"})
    return {"status": status, "reasons": reasons, "cautions": cautions}


def moe_estimate(size_bytes: int, block_count: int) -> dict:
    """MoE の、GPU に載せる固定分（MiB）と専門家 1 層ぶん（MiB）を、ファイルサイズと層数から推定する"""
    size_mib = size_bytes / 1048576
    experts = size_mib * MOE_EXPERT_SHARE
    return {"moe_layers": block_count, "moe_layer_mib": experts / block_count, "gpu_fixed_mib": size_mib - experts + MOE_OVERHEAD_MIB}


def custom_llm_settings(vram_mib: int | None = None, size_bytes: int | None = None, kind: str = "", moe_layers: int | None = None,
                        mode: str = "fit") -> dict:
    """任意の GGUF の llama-server 引数。既存のプロファイルと同じ方式で決める:
    - MoE で、導入プロファイルが moe_layers 方式（mid）で、層数が分かるとき: 専門家を CPU に置く層数を VRAM から計算（--n-cpu-moe）
    - それ以外: --fit に任せ、TTS 用の余白を残す
    --jinja は llama_command が常に付ける。"""
    if kind == "moe" and mode == "moe_layers" and moe_layers and size_bytes and vram_mib:
        est = moe_estimate(size_bytes, moe_layers)
        budget = vram_mib - TTS_VRAM_MIB - DESKTOP_HEADROOM_MIB
        gpu_layers = max(0, min(est["moe_layers"], math.floor((budget - est["gpu_fixed_mib"]) / est["moe_layer_mib"])))
        n_cpu_moe = est["moe_layers"] - gpu_layers
        info = {"llm_vram_estimate_mib": int(est["gpu_fixed_mib"] + gpu_layers * est["moe_layer_mib"]), "n_cpu_moe": n_cpu_moe, "estimated": True}
        return {"args": ["-fa", "on", "-ngl", "99", "--n-cpu-moe", str(n_cpu_moe), "--fit", "off"], "fit_target_mib": None, "info": info}
    return {"args": ["-fa", "on"], "fit_target_mib": CUSTOM_FIT_MARGIN_MIB, "info": {"fit_margin_mib": CUSTOM_FIT_MARGIN_MIB}}


def custom_ctx(size_bytes: int | None, hw_cap: float | None) -> int:
    """任意のモデルのコンテキスト長: 既定 8192。ハードの上限の 85% を超える大きいモデルは 4096 に下げる（KV キャッシュの分を残す）"""
    if size_bytes and hw_cap and size_bytes / GIB > hw_cap * 0.85:
        return 4096
    return 8192


# ---- Strata（別ソフトウェア。MIT。Qwen3.8-Flash-Next を普通のゲーミング PC で動かす）----
# 要件は Strata の README と docs/MODELS.md から: VRAM 12GB 以上、RAM 48GB 以上（IQ2_XS 以上の全エキスパート版）、
# ディスク約 80GB（プロファイルは問わない。mid でも案内する）。RAM 32GB では Coder 版（半分の専門家を削ったコード向け）しか載らず、日本語の雑談に弱いので案内しない。
STRATA = {
    "repo": "https://github.com/Niko1221/Strata.git",
    "commit": "82f46a8c8f475f001ad76d92f58f4a4f8ffb0253",
    "min_vram_gib": 12,
    "min_ram_gib": 48,
    "disk_gb": 80,
    "download_gb": 70,
    "default_port": 8080,
}
