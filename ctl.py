"""起動・停止・状態確認（start.bat / stop.bat から呼ばれる）と、setup.py と共有する設定の処理。

標準ライブラリだけで動く。設定の正本は config.json（setup.py が生成）。
  python ctl.py start [--no-browser]
  python ctl.py stop
  python ctl.py restart [llm|all]   設定変更のあとの再起動
  python ctl.py status
  python ctl.py _run NAME LOGFILE -- CMD ARGS...   （別ウィンドウ内の中継。内部用）
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import settings_store  # noqa: E402
CONFIG_PATH = ROOT / "config.json"
RUN_DIR = ROOT / "run"
LOG_DIR = ROOT / "logs"

DEFAULT_PORTS = {"llm": 8081, "tts": 8088, "stt": 8002, "app": 8100}
RESERVED_PORTS = {8000, 8080}   # UE5 の MCP など、他のツールが使いがちなポートは避ける
SERVICES = ["llm", "tts", "stt", "app"]    # 起動順
LABEL = {"llm": "LLM (llama-server)", "tts": "TTS (Irodori)", "stt": "STT (faster-whisper)", "app": "AIAvatarKit"}


# ---------------------------------------------------------------- 共通 ----

def load_config(root: Path = ROOT) -> dict:
    return json.loads((root / "config.json").read_text(encoding="utf-8"))


def save_config(cfg: dict, root: Path = ROOT):
    (root / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def port_in_use(port: int) -> bool:
    """127.0.0.1 と 0.0.0.0 のどちらかで他のプロセスが待ち受けていれば True"""
    for host in ("127.0.0.1", "0.0.0.0"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
        except OSError:
            return True
        finally:
            s.close()
    return False


def pick_ports(preferred: dict, taken_by_us: set = frozenset()) -> dict:
    """既定ポートが使用中なら空きを探す。taken_by_us は自分たちのサービスが使っていて良いポート"""
    result, used = {}, set()
    for name in SERVICES:
        port = preferred.get(name, DEFAULT_PORTS[name])
        while port in used or port in RESERVED_PORTS or (port_in_use(port) and port not in taken_by_us):
            port += 1
        result[name] = port
        used.add(port)
    return result


def nvidia_gpu(index: int = 0) -> dict | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--id={index}", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL, creationflags=0x08000000,
        ).stdout.strip()
        total, used, free = [int(x) for x in out.split(",")]
        return {"total_mib": total, "used_mib": used, "free_mib": free}
    except Exception:
        return None


def http_ok(url: str, timeout: float = 3.0, must_contain: str = None) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return False
            return must_contain is None or must_contain in r.read().decode("utf-8", "replace")
    except Exception:
        return False


def llm_resolved(cfg: dict, root: Path = ROOT) -> dict:
    return settings_store.resolve_llm(cfg, settings_store.load_settings(root), root)


def health_url(cfg: dict, name: str) -> tuple[str, str | None]:
    p = cfg["ports"]
    if name == "llm":
        r = llm_resolved(cfg)
        if r["mode"] == "external":
            return r["base_url"] + "/models", None
        return f"http://127.0.0.1:{p['llm']}/health", None
    return {
        "tts": (f"http://127.0.0.1:{p['tts']}/health", '"loaded":true'),
        "stt": (f"http://127.0.0.1:{p['stt']}/health", None),
        "app": (f"http://127.0.0.1:{p['app']}/static/index.html", None),
    }[name]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def check_health(cfg: dict, name: str) -> bool:
    url, needle = health_url(cfg, name)
    try:
        req = urllib.request.Request(url)
        if name == "llm":
            r_ = llm_resolved(cfg)
            if r_["mode"] == "external" and r_["api_key"] != "none":
                req.add_header("Authorization", "Bearer " + r_["api_key"])
        with urllib.request.urlopen(req, timeout=3) as r:
            body = _compact(r.read().decode("utf-8", "replace"))
            return r.status == 200 and (needle is None or _compact(needle) in body)
    except Exception:
        return False


def render_env(cfg: dict) -> str:
    """app/.env: 起動ポートなど低レベルの値だけ。声・LLM・キャラクターは config/settings.json（設定画面）で決まる"""
    p = cfg["ports"]
    return "\n".join([
        "# このファイルは setup.py / start.bat が config.json から作り直します（直接編集しないでください）。",
        "# 声・LLM・キャラクターの設定は http://127.0.0.1:%d/settings か configure.bat で変えられます。" % p["app"],
        f"TTS_BASE_URL=http://127.0.0.1:{p['tts']}/v1",
        f"STT_BASE_URL=http://127.0.0.1:{p['stt']}/v1",
        f"LLM_BASE_URL_BUNDLED=http://127.0.0.1:{p['llm']}/v1",
        f"STT_MODEL={cfg['stt']['model']}",
        "AVATAR_DEBUG=0",
        "",
    ])


def write_env(cfg: dict, root: Path = ROOT):
    (root / "app" / ".env").write_text(render_env(cfg), encoding="utf-8")


def base_env(cfg: dict, root: Path = ROOT) -> dict:
    env = dict(os.environ)
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "HF_HOME": str(root / "hf-home"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TORCH_HOME": str(root / "torch-home"),
        "CUDA_VISIBLE_DEVICES": str(cfg["gpu"]["index"]),
    })
    return env


# ---------------------------------------------------------------- コマンド組み立て ----

def llama_command(cfg: dict, root: Path = ROOT) -> list[str]:
    p = cfg["ports"]
    l = llm_resolved(cfg, root)
    mp = Path(l["model_path"])
    cmd = [
        str(root / "llama.cpp" / "llama-server.exe"),
        "-m", str(mp if mp.is_absolute() else root / mp),
        "--host", "127.0.0.1", "--port", str(p["llm"]),
        "--alias", l["model"],
        "--jinja",
        "-c", str(l["ctx"]),
        "-np", "1",
        "--no-webui",
    ]
    cmd += list(l.get("extra_args", []))
    # fit モード: VRAM に収まる分だけ GPU に載せ、TTS 用の余白（MiB）を残す
    if l.get("fit_target_mib"):
        cmd += ["--fit", "on", "--fit-target", str(int(l["fit_target_mib"]))]
    return cmd


def service_command(cfg: dict, name: str, root: Path = ROOT) -> tuple[list[str], Path, dict]:
    p = cfg["ports"]
    env = base_env(cfg, root)
    if name == "llm":
        return llama_command(cfg, root), root / "llama.cpp", env
    if name == "tts":
        t = cfg["tts"]
        env.update({
            "IRODORI_HOST": "127.0.0.1",
            "IRODORI_PORT": str(p["tts"]),
            "IRODORI_HF_CHECKPOINT": t["hf_checkpoint"],
            "IRODORI_MODEL_NAME": "irodori-tts",
            "IRODORI_MODEL_PRECISION": "bf16",
            "IRODORI_CODEC_PRECISION": "bf16",
            "IRODORI_COMPILE_MODEL": "false",
            "IRODORI_PRELOAD": "true",
            "IRODORI_MODEL_LOAD_TIMEOUT": "600",
            "IRODORI_MAX_CONCURRENT_SYNTHESIS": "1",
            "IRODORI_VOICES_DIR": "voices",
            "IRODORI_ALLOW_NO_REF_VOICE": "true",
            "IRODORI_DEFAULT_RESPONSE_FORMAT": "wav",
            "IRODORI_EMPTY_CACHE_INTERVAL": "1",   # 1 回ごとにキャッシュを返す（VRAM 8GB 級で LLM と同居するため）
        })
        py = root / "tts" / ".venv" / "Scripts" / "python.exe"
        return [str(py), "-m", "irodori_openai_tts", "--host", "127.0.0.1", "--port", str(p["tts"])], root / "tts", env
    if name == "stt":
        env.update({
            "STT_MODEL": cfg["stt"]["model"],
            "STT_DEVICE": "cpu",
            "STT_COMPUTE": "int8",
            "STT_CPU_THREADS": str(cfg["stt"]["cpu_threads"]),
        })
        py = root / "stt" / ".venv" / "Scripts" / "python.exe"
        return [str(py), "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(p["stt"])], root / "stt", env
    if name == "app":
        py = root / "app" / ".venv" / "Scripts" / "python.exe"
        return [str(py), "-m", "uvicorn", "run:app", "--host", "127.0.0.1", "--port", str(p["app"])], root / "app", env
    raise ValueError(name)


# ---------------------------------------------------------------- 起動・停止 ----

def listener_pids(port: int) -> set[int]:
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, errors="replace",
                         stdin=subprocess.DEVNULL, creationflags=0x08000000).stdout or ""
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            pids.add(int(parts[4]))
    return pids


def pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, errors="replace",
                         stdin=subprocess.DEVNULL, creationflags=0x08000000).stdout or ""
    return str(pid) in out


def kill_tree(pid: int):
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, stdin=subprocess.DEVNULL, creationflags=0x08000000)


def pid_file() -> Path:
    return RUN_DIR / "pids.json"


# ---------------------------------------------------------------- Strata（別ソフトウェア。インストール先の strata/ にあるものだけを扱う） ----

STRATA_RESERVE_MIB = 3500       # TTS（ピーク約 2.8GB）用に、Strata の専門家キャッシュが残す VRAM
STRATA_WAIT_SEC = 600
STRATA_VISION_GPU_MIB = 1400      # 画像エンコーダ（GPU）が使う VRAM
ESP_VECTOR = Path('data') / 'experimental-speed-projection' / 'Qwen3.8-Flash-Next-experimental-speed-projection.gguf'
ESP_MODEL_RE = r'^qwen3\.8-flash-next-(q2_0|iq2_xs|iq3_xxs|iq3_s)$'


def strata_dir(root: Path = ROOT) -> Path:
    return root / "strata"


def strata_info(root: Path = ROOT) -> dict | None:
    """インストール先の Strata の設定（strata-*.json）と起動 bat（run-*.bat）を探す。新しいものを優先"""
    d = strata_dir(root)
    if not d.is_dir():
        return None
    jsons = sorted((f for f in d.glob("strata-*.json") if ".bak" not in f.name and not f.stem.endswith("-esp")), key=lambda f: f.stat().st_mtime, reverse=True)
    bats = sorted((f for f in d.glob("run-*.bat") if ".bak" not in f.name and not f.stem.endswith("-esp")), key=lambda f: f.stat().st_mtime, reverse=True)
    info = {"dir": d, "json": None, "port": None, "bat": bats[0] if bats else None}
    for j in jsons:
        try:
            data = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        info["json"], info["port"] = j, data.get("port")
        break
    return info


def ensure_strata_reserve(info: dict, mib: int = STRATA_RESERVE_MIB) -> bool:
    """clone した Strata の設定にだけ、TTS 用の VRAM の余白（--vram-reserve-mib）を入れる。変えたら True"""
    j = info.get("json")
    if not j:
        return False
    data = json.loads(j.read_text(encoding="utf-8"))
    args = data.setdefault("args", [])
    # 画像認識のエンコーダを GPU で動かすときは、その分（約 1.4GB）も足す。Strata のセットアップは GPU でも CPU でも
    # 同じ 700 を書くだけで、エンコーダの分を TTS 用の余白に上乗せはしないため
    if (data.get("vision") or {}).get("gpu"):
        mib += STRATA_VISION_GPU_MIB
    if "--vram-reserve-mib" in args[:-1]:
        i = args.index("--vram-reserve-mib") + 1
        if int(args[i]) >= mib:
            return False
        args[i] = str(mib)
    else:
        args += ["--vram-reserve-mib", str(mib)]
    j.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def esp_applicable(info: dict | None) -> tuple[bool, str]:
    """ESP（拒否の抑制）を使えるか。元の Qwen3.8-Flash-Next のみ（Swift 1.5・Coder・Unsloth 版は対象外）"""
    if not info or not info.get("json"):
        return False, "Strata がインストールされていません（strata-*.json がありません）。"
    try:
        name = json.loads(info["json"].read_text(encoding="utf-8")).get("model_name", "")
    except Exception:
        return False, "Strata の設定ファイルを読めません。"
    if not re.match(ESP_MODEL_RE, name):
        return False, f"このモデル（{name}）では使えません。ベクトルは元の Qwen3.8-Flash-Next 専用です（Swift 1.5・Coder などは対象外）。"
    if not (info["dir"] / ESP_VECTOR).is_file():
        return False, "ベクトルのファイルがありません（strata/data/experimental-speed-projection/）。"
    return True, ""


def esp_json_path(info: dict) -> Path:
    return info["json"].with_name(info["json"].stem + "-esp.json")


def write_esp_json(info: dict) -> Path:
    """Strata のセットアップが書き出すのと同じ形の ESP 用設定（strata-<model>-esp.json）を、元の設定から作る"""
    data = json.loads(info["json"].read_text(encoding="utf-8"))
    args = [a for a in data.get("args", [])]
    for flag, n in (("--control-vector-scaled", 2), ("--control-vector-layer-range", 3), ("--cvec-mode", 2), ("--cvec-dir", 2)):
        while flag in args:
            i = args.index(flag)
            del args[i:i + n]
    args += ["--control-vector-scaled", f"{info['dir'] / ESP_VECTOR}:1.0", "--control-vector-layer-range", "4", "44",
             "--cvec-mode", "project", "--cvec-dir", "per-layer"]
    data["args"] = args
    if data.get("log"):
        data["log"] = re.sub(r"\.log$", "-esp.log", data["log"])
    out = esp_json_path(info)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def strata_launch_bat(info: dict, root: Path = ROOT, esp: bool = False) -> Path:
    """Strata の run-*.bat から、ブラウザを開く --open を外した起動用 bat を作る（esp=True なら ESP 用の設定を使う）"""
    text = info["bat"].read_bytes().decode("utf-8", errors="replace")
    text = text.replace(' "--open"', "").replace(" --open", "")
    if esp:
        cfg_json = write_esp_json(info)
        text = text.replace(info["json"].name, cfg_json.name)
        text = re.sub(r"(?m)^title (.*)$", r"title \1 (experimental speed projection ON)", text, count=1)
    out = root / "run" / "strata-start.bat"
    out.parent.mkdir(exist_ok=True)
    text = text.replace("pause", "rem pause").replace("\r", "")
    out.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    return out


def managed_strata(cfg: dict) -> bool:
    s = settings_store.load_settings(ROOT)
    return s["llm"]["mode"] == "external" and s["llm"]["external"].get("managed") == "strata"


def sync_strata_port(cfg: dict) -> dict | None:
    """Strata の設定ファイルのポートが、こちらの設定と違えば合わせる（設定画面で変えられる）"""
    info = strata_info()
    if not info or not info.get("port"):
        return info
    s = settings_store.load_settings(ROOT)
    from urllib.parse import urlparse
    cur = urlparse(s["llm"]["external"]["base_url"])
    if cur.port != info["port"]:
        base = f"{cur.scheme}://{cur.hostname}:{info['port']}{cur.path or '/v1'}"
        settings_store.update_settings({"llm": {"external": {"base_url": base}}}, ROOT)
        print(f"Strata のポートを strata-*.json に合わせました: {info['port']}")
    return info


def active_services(cfg: dict) -> list[str]:
    """external の LLM は起動も停止もしない（ユーザーが用意するもの）"""
    ext = llm_resolved(cfg)["mode"] == "external"
    return [n for n in SERVICES if not (n == "llm" and ext)]


def read_pids() -> dict:
    try:
        return json.loads(pid_file().read_text())
    except Exception:
        return {}


def stop(cfg: dict = None, quiet: bool = False, only: list[str] = None):
    cfg = cfg or load_config()
    say = (lambda *_: None) if quiet else print
    pids = read_pids()
    names = [n for n in reversed(SERVICES) if (only is None or n in only)]
    ext_llm = llm_resolved(cfg)["mode"] == "external"
    if only is None and "strata" in pids:       # こちらが起動した Strata だけ止める（自分で起動したものは止めない）
        kill_tree(int(pids.pop("strata")))
        say("  Strata: 停止")
    for name in names:
        if name == "llm" and ext_llm:
            continue
        targets = set()
        if name in pids:
            targets.add(int(pids[name]))
        # 中継ウィンドウが先に閉じられていても止められるように、ポートの待ち受けも調べる
        targets |= listener_pids(cfg["ports"][name]) if name in cfg["ports"] else set()
        for pid in targets:
            kill_tree(pid)
        pids.pop(name, None)
        say(f"  {LABEL[name]}: {'停止' if targets else '起動していません'}")
    if pids:
        pid_file().write_text(json.dumps(pids))
    else:
        try:
            pid_file().unlink()
        except FileNotFoundError:
            pass


def wait_external_llm(cfg: dict, limit: int = None, what: str = "外部の LLM サーバー") -> bool:
    r = llm_resolved(cfg)
    limit = limit or cfg.get("external_wait_sec", 120)
    print(f"{what}（{r['base_url']}）の応答を待っています（最大 {limit} 秒）。")
    if what == "外部の LLM サーバー":
        print("  ※ このサーバーは start.bat では起動しません。先に起動しておいてください。")
    started = time.time()
    deadline = started + limit
    last = 0
    while time.time() < deadline:
        if check_health(cfg, "llm"):
            print(f"  LLM: 接続できました（{time.time() - started:.0f} 秒）。")
            return True
        if time.time() - last >= 15:
            last = time.time()
            print(f"  ... 待機中 {time.time() - started:.0f} / {limit} 秒", flush=True)
        time.sleep(2)
    print(f"[エラー] {limit} 秒待ちましたが、{r['base_url']}/models に接続できませんでした。")
    if what.startswith("Strata"):
        print("  ・初回の読み込みは長くかかります。Strata の別ウィンドウのログ（logs/strata.log）を確認し、もう一度 start.bat を実行してください。")
        print("  ・Strata のインストールが終わっていないときは、strata フォルダの START-HERE.bat を実行して完了させてください。")
    else:
        print("  ・推論サーバー（Strata / LM Studio / Ollama など）が起動しているか確認してください。")
        print("  ・URL とポートが合っているか、API キーが必要でないか、設定画面（/settings）か configure.bat で確認してください。")
    return False


def start_managed_strata(cfg: dict) -> bool:
    """インストール先の Strata を起動して、/v1/models が返るまで待つ"""
    info = sync_strata_port(cfg)
    if not info or not info.get("bat"):
        print("[エラー] Strata のインストールが終わっていません（strata/run-*.bat がありません）。")
        print("  strata フォルダの START-HERE.bat を実行して、モデルのダウンロードまで完了させてください。")
        return False
    if check_health(cfg, "llm"):
        print("Strata: すでに起動しています。")
        return True
    old_pid = read_pids().get("strata")
    if old_pid and pid_alive(int(old_pid)):
        print("Strata: 前回起動したものがまだ読み込み中です。そのまま待ちます。")
        return wait_external_llm(cfg, cfg.get("strata_wait_sec", STRATA_WAIT_SEC), what="Strata")
    if ensure_strata_reserve(info):
        print(f"Strata の設定に TTS 用の VRAM の余白を入れました（--vram-reserve-mib {STRATA_RESERVE_MIB}）。")
    esp_on = bool(settings_store.load_settings(ROOT)["llm"].get("esp", {}).get("enabled"))
    if esp_on:
        ok, why = esp_applicable(info)
        if not ok:
            print(f"ESP（拒否の抑制）は使えないため、オフで起動します: {why}")
            esp_on = False
    bat = strata_launch_bat(info, esp=esp_on)
    if esp_on:
        print("ESP（拒否の抑制）をオンにして起動します（experimental speed projection。断らなくなります）。")
    for line in bat.read_text(encoding="utf-8", errors="replace").splitlines():
        if "server.py" in line:
            print("  Strata 起動コマンド:", line.strip())
    log = LOG_DIR / "strata.log"
    runner = [sys.executable, str(ROOT / "ctl.py"), "_run", "Strata", str(log), "--", "cmd", "/c", str(bat)]
    print("Strata を起動します（別ウィンドウ）。")
    print("  ※ 起動中の 1〜3 分（初回はもっと長い）は PC が重くなります。ウィンドウを閉じずに待ってください。")
    proc = subprocess.Popen(runner, cwd=str(info["dir"]), env=base_env(cfg), creationflags=subprocess.CREATE_NEW_CONSOLE)
    pids = read_pids()
    pids["strata"] = proc.pid
    pid_file().write_text(json.dumps(pids))
    return wait_external_llm(cfg, cfg.get("strata_wait_sec", STRATA_WAIT_SEC), what="Strata")


def start(cfg: dict, open_browser: bool = True, only: list[str] = None) -> int:
    RUN_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    names = [n for n in active_services(cfg) if (only is None or n in only)]
    running = [n for n in names if check_health(cfg, n)]
    if running:
        print("すでに起動しているサービスがあります:", ", ".join(LABEL[n] for n in running))
        print("一度 stop.bat で止めてから、もう一度 start.bat を実行してください。")
        return 1

    # 設定したポートを他のプログラムが使い始めていたら、空きに付け替える
    new_ports = pick_ports(cfg["ports"], taken_by_us={cfg["ports"][n] for n in SERVICES if n not in names and check_health(cfg, n)})
    if new_ports != cfg["ports"] and only is None:
        print("使用中のポートがあったため、付け替えました:", cfg["ports"], "->", new_ports)
        cfg["ports"] = new_ports
        save_config(cfg)
    write_env(cfg)

    pids = read_pids()
    for name in SERVICES:
        if name not in names:
            if name == "llm" and only is None and llm_resolved(cfg)["mode"] == "external":
                ok = start_managed_strata(cfg) if managed_strata(cfg) else wait_external_llm(cfg)
                if not ok:
                    stop(cfg, quiet=True, only=names)
                    return 1
            continue
        cmd, cwd, env = service_command(cfg, name)
        if not Path(cmd[0]).exists():
            print(f"[エラー] {cmd[0]} が見つかりません。setup.bat を実行してください。")
            if name == "llm":
                print("  （外部の LLM サーバーを使う場合は、設定画面で方式を external にしてください）")
            stop(cfg, quiet=True, only=names)
            return 1
        if name == "llm":
            mp = Path(cmd[cmd.index("-m") + 1])
            if not mp.exists():
                print(f"[エラー] LLM のモデルファイルがありません: {mp}")
                print("  設定画面（/settings）の LLM でダウンロードするか、setup.bat を実行してください。")
                return 1
        log = LOG_DIR / f"{name}.log"
        runner = [sys.executable, str(ROOT / "ctl.py"), "_run", LABEL[name], str(log), "--"] + cmd
        print(f"{LABEL[name]} を起動します（別ウィンドウ）...")
        proc = subprocess.Popen(runner, cwd=str(cwd), env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
        pids = read_pids()      # Strata の起動で書いた分を消さないように、毎回読み直す
        pids[name] = proc.pid
        pid_file().write_text(json.dumps(pids))
        deadline = time.time() + cfg.get("start_timeout_sec", {}).get(name, 600)
        started = time.time()
        while not check_health(cfg, name):
            if proc.poll() is not None:
                print(f"[エラー] {LABEL[name]} が終了しました。ログ: {log}")
                stop(cfg, quiet=True, only=names)
                return 1
            if time.time() > deadline:
                print(f"[エラー] {LABEL[name]} が時間内に起動しませんでした。ログ: {log}")
                stop(cfg, quiet=True, only=names)
                return 1
            time.sleep(1.5)
        print(f"  {LABEL[name]}: 準備完了（{time.time() - started:.0f} 秒）")

    url = f"http://127.0.0.1:{cfg['ports']['app']}/static/index.html"
    print()
    print("すべて起動しました。")
    print(f"  ブラウザ画面: {url}")
    print(f"  設定画面    : http://127.0.0.1:{cfg['ports']['app']}/settings")
    print(f"  管理画面    : http://127.0.0.1:{cfg['ports']['app']}/admin")
    print("  止めるときは stop.bat を実行してください。")
    if open_browser:
        webbrowser.open(url)
    return 0


def restart(cfg: dict, target: str) -> int:
    """設定を変えたあとの再起動。target: llm / all"""
    if target == "llm":
        if managed_strata(cfg):                  # Strata は LLM だけ再起動できないので、全体を再起動する
            target = "all"
        else:
            stop(cfg, quiet=True, only=["llm"])
            return start(cfg, open_browser=False, only=["llm"])
    stop(cfg, quiet=True)
    time.sleep(2)
    return start(cfg, open_browser=False)


def download(url: str, dest: Path, label: str = None, headers: dict = None, progress=None):
    """進捗表示つき・再開可能のダウンロード（setup.py と設定画面が共有する）"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    label = label or dest.name
    done = part.stat().st_size if part.exists() else 0
    hdrs = {"User-Agent": "koemado"}
    hdrs.update(headers or {})
    if done:
        hdrs["Range"] = f"bytes={done}-"
    req = urllib.request.Request(url, headers=hdrs)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416:   # すでに全部ある
            part.replace(dest)
            return
        raise
    total = int(resp.headers.get("Content-Length", 0)) + (done if resp.status == 206 else 0)
    if resp.status != 206:
        done = 0
    last = 0
    with open(part, "ab" if done else "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
            elif time.time() - last > 1.0:
                last = time.time()
                pct = f"{done * 100 / total:5.1f}%" if total else ""
                print(f"\r  {label}: {done / 1e6:8.1f} / {total / 1e6:.1f} MB {pct}", end="", flush=True)
    if not progress:
        print(f"\r  {label}: {done / 1e6:8.1f} MB 完了{' ' * 20}")
    part.replace(dest)


def run_relay(name: str, log: str, cmd: list[str]) -> int:
    """別ウィンドウの中身: 子プロセスの出力を画面とログに流す"""
    try:
        os.system(f"title {name}")
    except Exception:
        pass
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    with open(log, "wb") as f:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for raw in iter(proc.stdout.readline, b""):
            f.write(raw)
            f.flush()
            try:
                sys.stdout.write(raw.decode("utf-8", "replace"))
                sys.stdout.flush()
            except Exception:
                pass
    code = proc.wait()
    print(f"\n[{name}] 終了しました（コード {code}）。このウィンドウは閉じて構いません。")
    if code not in (0, 1, -1):
        time.sleep(30)
    return code


def status(cfg: dict):
    gpu = nvidia_gpu(cfg["gpu"]["index"])
    for name in SERVICES:
        ok = check_health(cfg, name)
        print(f"  {LABEL[name]:<24} port {cfg['ports'][name]}  {'起動中' if ok else '停止'}")
    if gpu:
        print(f"  GPU メモリ: {gpu['used_mib']} / {gpu['total_mib']} MiB 使用中")


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "_run":
        sep = argv.index("--")
        return run_relay(argv[2], argv[3], argv[sep + 1:])
    if not CONFIG_PATH.exists():
        print("config.json がありません。先に setup.bat を実行してください。")
        return 1
    cfg = load_config()
    if cmd == "start":
        only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else None
        return start(cfg, open_browser="--no-browser" not in argv and only is None, only=only)
    if cmd == "restart":
        return restart(cfg, argv[2] if len(argv) > 2 else "all")
    if cmd == "stop":
        print("停止します...")
        stop(cfg)
        return 0
    if cmd == "status":
        status(cfg)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
