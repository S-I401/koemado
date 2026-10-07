"""koemado の常駐管理プロセス（supervisor）。

黒いウィンドウを出さずに、LLM・TTS・STT・AIAvatarKit を起動して見守る。
  - 子プロセスは CREATE_NO_WINDOW で起動し、出力を logs/<service>.log（5MB でローテーション）とメモリに取り込む
  - ログの API キー・トークンらしい文字列は伏せ字にする
  - 異常終了したら画面とログに出し、自動で再起動する（5 分に 3 回まで）。超えたら止めて知らせる
  - 子プロセスは Job Object に入れる（supervisor が落ちたら OS が全部止める）
  - 管理画面（ログ・状態・不具合報告 zip）は supervisor 自身が持つ（127.0.0.1 の別ポート。既定 8099）
  - タスクトレイのアイコン（pystray）。使えない環境でも本体は動き続ける

  python.exe supervisor.py launch   すでに動いていればブラウザを開くだけ。無ければ起動する（koemado.vbs が、ウィンドウを出さずに呼ぶ）
  python.exe supervisor.py run      常駐本体（launch が CREATE_NO_WINDOW で起動する）
（venv の pythonw.exe は、実際には base の python.exe（コンソール版）を起動するため使わない）
"""
import asyncio
import collections
import ctypes
import io
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import ctl  # noqa: E402
import settings_store as ss  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
LOG_DIR = ROOT / "logs"
RUN_DIR = ROOT / "run"
INFO_FILE = RUN_DIR / "supervisor.json"
DEFAULT_CONSOLE_PORT = 8099
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_KEEP = 4                                  # logs/<service>.log.1 〜 .4
RING_PER_SERVICE = 3000
RING_ALL = 12000
ALL_SERVICES = ["llm", "tts", "stt", "app"]
LABEL = dict(ctl.LABEL)
CONSOLE_HEADER = "x-console-request"


def restart_policy() -> tuple[int, int]:
    """（回数, 秒）。既定は 5 分に 3 回まで。テスト用に環境変数で変えられる"""
    return int(os.environ.get("KOEMADO_RESTART_LIMIT", "3")), int(os.environ.get("KOEMADO_RESTART_WINDOW", "300"))


# ------------------------------------------------------------------ 伏せ字 ----

MASK = "●●●●●●●●"
_SECRET_PATTERNS = [
    (re.compile(r"(?i)(bearer(?:\s|%20|\+)+)[A-Za-z0-9._~+/=\-%]{8,}"), r"\1" + MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), MASK),
    # Hugging Face のトークン（hf_ + 英数字。大文字か数字を含むものだけ。hf_hub_download のような名前は伏せない）と、組織用（api_org_）
    (re.compile(r"\bhf_(?=[A-Za-z0-9]*[A-Z0-9])[A-Za-z0-9]{8,}"), MASK),
    (re.compile(r"\bapi_org_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"(?i)((?:api[_\-]?key|apikey|access[_\-]?token|auth[_\-]?token|(?<![A-Za-z_])token|secret|password|passwd|authorization|x-api-key)[\"']?\s*[:=]\s*[\"']?)(?!Bearer\b)[^\s\"',;&}]{6,}"), r"\1" + MASK),
]
_PATH_USER = re.compile(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\s\"'<>|]+")


class Redactor:
    """設定に入っているキー（LLM・外部エージェント・MCP の env / headers の値）そのものと、キーらしい形を伏せる"""

    def __init__(self):
        self._lit: list[str] = []
        self._stamp = None
        self._lock = threading.Lock()

    def _files(self):
        return [ss.settings_path(ROOT), ROOT / "config" / "mcp.json"]

    def refresh(self):
        stamp = tuple((p.stat().st_mtime_ns if p.exists() else 0) for p in self._files())
        if stamp == self._stamp:
            return
        lits = set()
        try:
            s = ss.load_settings(ROOT)
            for v in (s["llm"]["external"].get("api_key"), s["tools"]["agent"].get("api_key"), (s.get("hf") or {}).get("token")):
                if v:
                    lits.add(v)
        except Exception:
            pass
        for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
            if len(os.environ.get(name, "")) >= 8:
                lits.add(os.environ[name])
        try:
            data = json.loads((ROOT / "config" / "mcp.json").read_text(encoding="utf-8-sig"))
            for sv in (data.get("mcpServers") or {}).values():
                for k in ("env", "headers"):
                    for v in (sv.get(k) or {}).values():
                        lits.add(str(v))
                        m = re.match(r"(?i)bearer\s+(.+)", str(v))
                        if m:
                            lits.add(m.group(1))
        except Exception:
            pass
        with self._lock:
            from urllib.parse import quote, quote_plus
            lits |= {quote(x, safe="") for x in lits} | {quote_plus(x) for x in lits}      # URL に入った形も伏せる
            self._lit = sorted((x for x in lits if len(x) >= 6), key=len, reverse=True)
            self._stamp = stamp

    def __call__(self, text: str) -> str:
        self.refresh()
        for lit in self._lit:
            if lit in text:
                text = text.replace(lit, MASK)
        for pat, rep in _SECRET_PATTERNS:
            text = pat.sub(rep, text)
        return text

    @staticmethod
    def scrub_paths(text: str) -> str:
        """不具合報告用: 個人のパス（ユーザー名・インストール先）を伏せる"""
        root = str(ROOT)
        for variant in {root, root.replace("\\", "/"), root.replace("\\", "\\\\")}:
            text = re.sub(re.escape(variant), "<koemado>", text, flags=re.I)
        return _PATH_USER.sub(r"\1<user>", text)


REDACT = Redactor()


# ------------------------------------------------------------------ ログ ----

_POLL_NOISE = re.compile(r'"GET (/health|/static/index\.html) HTTP/1\.1" 200')
_LVL_LLAMA = re.compile(r"^\d+\.\d+\.\d+\.\d+ ([WEID]) ")
_LVL_ERR = re.compile(r"\b(ERROR|CRITICAL|FATAL|Traceback|Exception|Error:)\b|\[エラー\]|異常終了")
_LVL_WARN = re.compile(r"\b(WARN|WARNING)\b|警告")


def detect_level(text: str) -> str:
    m = _LVL_LLAMA.match(text)
    if m:
        return {"E": "ERROR", "W": "WARNING"}.get(m.group(1), "INFO")
    if _LVL_ERR.search(text):
        return "ERROR"
    if _LVL_WARN.search(text):
        return "WARNING"
    return "INFO"


class RotatingFile:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "ab")

    def write(self, line: str):
        data = (line + "\n").encode("utf-8", "replace")
        if self.f.tell() + len(data) > LOG_MAX_BYTES:
            self.rotate()
        self.f.write(data)
        self.f.flush()

    def rotate(self):
        self.f.close()
        for i in range(LOG_KEEP, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i - 1}") if i > 1 else self.path
            dst = self.path.with_name(f"{self.path.name}.{i}")
            if src.exists():
                try:
                    dst.unlink(missing_ok=True)
                    src.replace(dst)
                except OSError:
                    pass
        self.f = open(self.path, "ab")

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


class LogHub:
    """サービスごとのリングバッファとファイル。全体の連番（seq）で SSE の続きを渡す"""

    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.rings: dict[str, collections.deque] = {}
        self.allring = collections.deque(maxlen=RING_ALL)
        self.files: dict[str, RotatingFile] = {}

    def add(self, svc: str, text: str, level: str | None = None):
        text = REDACT(text.rstrip("\r\n"))
        level = level or detect_level(text)
        now = time.time()
        with self.lock:
            self.seq += 1
            e = {"seq": self.seq, "t": now, "svc": svc, "lvl": level, "text": text}
            self.rings.setdefault(svc, collections.deque(maxlen=RING_PER_SERVICE)).append(e)
            self.allring.append(e)
            fh = self.files.get(svc)
            if fh is None:
                fh = self.files[svc] = RotatingFile(LOG_DIR / f"{svc}.log")
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))} | {text}")

    def get(self, svc: str, since: int = 0, limit: int = 2000) -> list[dict]:
        with self.lock:
            src = self.allring if svc == "all" else self.rings.get(svc, ())
            out = [e for e in src if e["seq"] > since]
        return out[-limit:]

    def file_text(self, svc: str) -> str:
        """ローテーションした世代も古い順に連結（キーは伏せる）"""
        base = LOG_DIR / f"{svc}.log"
        parts = []
        for i in range(LOG_KEEP, 0, -1):
            p = base.with_name(f"{base.name}.{i}")
            if p.exists():
                parts.append(p)
        if base.exists():
            parts.append(base)
        text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in parts)
        return "\n".join(REDACT(line) for line in text.splitlines()) + "\n"


HUB = LogHub()


def log(msg: str, level: str | None = None):
    HUB.add("supervisor", msg, level)


class _StdWriter(io.TextIOBase):
    """pythonw では stdout / stderr が無いので、ctl.py の print などをログに流す"""

    def __init__(self, level=None):
        self.level = level
        self.buf = ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                log(line, self.level)
        return len(s)

    def flush(self):
        pass


# ------------------------------------------------------------------ Job Object ----

class Job:
    """子プロセスを結び付ける。supervisor が落ちる（ハンドルが閉じる）と、OS が全部止める"""

    def __init__(self):
        self.handle = None
        try:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.restype = wintypes.HANDLE
            h = k.CreateJobObjectW(None, None)
            if not h:
                raise OSError(ctypes.get_last_error())

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

            class EXT(ctypes.Structure):
                _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            info = EXT()
            info.Basic.LimitFlags = 0x2000     # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise OSError(ctypes.get_last_error())
            self.k, self.handle = k, h
        except Exception as e:  # noqa: BLE001
            log(f"Job Object を作れませんでした（子プロセスの自動終了は無効）: {e}", "WARNING")

    def add(self, proc: subprocess.Popen):
        if self.handle:
            try:
                self.k.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(proc._handle)))
            except Exception as e:  # noqa: BLE001
                log(f"子プロセスを Job に入れられませんでした: {e}", "WARNING")


# ------------------------------------------------------------------ サービス ----

class Service:
    def __init__(self, key: str):
        self.key = key
        self.label = LABEL[key]
        self.port = None
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"        # stopped / starting / ready / no_response / crashed / failed / external
        self.message = ""
        self.started_at = None
        self.ready_at = None
        self.restarts: collections.deque = collections.deque()
        self.total_restarts = 0
        self.stop_requested = False
        self.miss = 0

    def view(self) -> dict:
        now = time.time()
        return {"key": self.key, "label": self.label, "port": self.port, "state": self.state, "message": self.message,
                "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
                "uptime": int(now - self.started_at) if self.started_at and self.state in ("starting", "ready", "no_response") else None,
                "starting_for": int(now - self.started_at) if self.started_at and self.state == "starting" else None,
                "restarts": self.total_restarts}


class Supervisor:
    def __init__(self):
        self.cfg = ctl.load_config()
        self.job = Job()
        self.services = {k: Service(k) for k in ALL_SERVICES}
        self.phase = "idle"           # idle / starting / ready / failed / stopping
        self.phase_msg = ""
        self.lock = threading.RLock()
        self.shutdown_event = threading.Event()
        self.boot_thread: threading.Thread | None = None
        self.generation = 0           # 全体の再起動のたびに増やす（古い起動スレッドを止める）
        self.console_port = self.cfg["ports"].get("console", DEFAULT_CONSOLE_PORT)
        self.started_at = time.time()
        self.tray_ok = False

    # ---- 設定の読み直し ----
    def reload(self):
        self.cfg = ctl.load_config()
        for k, s in self.services.items():
            s.port = self.cfg["ports"].get(k)

    def names(self) -> list[str]:
        return ctl.active_services(self.cfg)

    # ---- 起動 ----
    def command_for(self, key: str):
        """(cmd, cwd, env, label)。外部の LLM サーバー（自分で用意するもの）は None"""
        cfg = self.cfg
        if key == "llm" and ctl.llm_resolved(cfg)["mode"] == "external":
            if not ctl.managed_strata(cfg):
                return None
            info = ctl.sync_strata_port(cfg)
            if not info or not info.get("bat"):
                raise RuntimeError("Strata のインストールが終わっていません（strata/run-*.bat がありません）。strata フォルダの START-HERE.bat を実行して完了させてください。")
            if ctl.check_health(cfg, "llm"):
                return None            # すでに動いている Strata は、そのまま使う
            if ctl.ensure_strata_reserve(info):
                log(f"Strata の設定に TTS 用の VRAM の余白を入れました（--vram-reserve-mib {ctl.STRATA_RESERVE_MIB}）。")
            esp_on = bool(ss.load_settings(ROOT)["llm"].get("esp", {}).get("enabled"))
            if esp_on:
                ok, why = ctl.esp_applicable(info)
                if not ok:
                    log(f"ESP（拒否の抑制）は使えないため、オフで起動します: {why}", "WARNING")
                    esp_on = False
                else:
                    log("ESP（拒否の抑制）をオンにして起動します（experimental speed projection）。")
            bat = ctl.strata_launch_bat(info, esp=esp_on)
            return ["cmd", "/c", str(bat)], info["dir"], ctl.base_env(cfg), "Strata"
        cmd, cwd, env = ctl.service_command(cfg, key)
        if not Path(cmd[0]).exists():
            raise RuntimeError(f"{cmd[0]} が見つかりません。setup.bat を実行してください。")
        if key == "llm":
            mp = Path(cmd[cmd.index("-m") + 1])
            if not mp.exists():
                raise RuntimeError(f"LLM のモデルファイルがありません: {mp}（設定画面の LLM でダウンロードするか、setup.bat を実行してください）")
        return cmd, cwd, env, LABEL[key]

    def spawn(self, s: Service, label: str, cmd, cwd, env):
        s.label = label
        s.stop_requested = False
        s.message = ""
        s.miss = 0
        proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        self.job.add(proc)
        s.proc, s.started_at, s.ready_at, s.state = proc, time.time(), None, "starting"
        log(f"{label} を起動しました（PID {proc.pid}）")
        threading.Thread(target=self._pump, args=(s, proc), daemon=True, name=f"pump-{s.key}").start()

    def _pump(self, s: Service, proc: subprocess.Popen):
        try:
            in_tb = False
            for raw in iter(proc.stdout.readline, b""):
                text = raw.decode("utf-8", "replace")
                if _POLL_NOISE.search(text):       # この管理プロセス自身の死活確認（数秒ごと）は、ログに出さない
                    continue
                level = None
                if text.startswith("Traceback (most recent call last)"):
                    in_tb, level = True, "ERROR"
                elif in_tb:        # トレースバックは、最後の例外の行まで ERROR にそろえる
                    level = "ERROR"
                    if text.strip() and not text.startswith((" ", "\t")):
                        in_tb = False
                HUB.add(s.key, text, level)
        except Exception as e:  # noqa: BLE001
            log(f"{s.label} の出力を読めませんでした: {e}", "WARNING")

    def wait_ready(self, s: Service, gen: int, timeout: float) -> bool:
        """準備完了まで待つ。プロセスが終了・時間切れ・世代が変わったら False"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.shutdown_event.is_set() or gen != self.generation:
                return False
            if s.proc is not None and s.proc.poll() is not None:
                s.state, s.message = "failed", f"{s.label} が起動中に終了しました（コード {s.proc.returncode}）。ログを確認してください。"
                log(s.message, "ERROR")
                return False
            if ctl.check_health(self.cfg, s.key):
                s.state, s.ready_at, s.message = "ready", time.time(), ""
                log(f"{s.label}: 準備完了（{time.time() - (s.started_at or time.time()):.0f} 秒）")
                return True
            time.sleep(1.5)
        s.state, s.message = "failed", f"{s.label} が {int(timeout)} 秒以内に起動しませんでした。ログを確認してください。"
        log(s.message, "ERROR")
        return False

    def boot(self, gen: int):
        """全サービスを順に起動する（ctl.start と同じ順序と待ち方）"""
        try:
            self.reload()
            names = self.names()
            # 他のプログラムがポートを使い始めていたら、空きに付け替える
            new_ports = ctl.pick_ports({k: v for k, v in self.cfg["ports"].items() if k in ctl.SERVICES})
            new_ports = {**self.cfg["ports"], **new_ports}
            if new_ports != self.cfg["ports"]:
                log(f"使用中のポートがあったため、付け替えました: {self.cfg['ports']} -> {new_ports}", "WARNING")
                self.cfg["ports"] = new_ports
                ctl.save_config(self.cfg)
            ctl.write_env(self.cfg)
            self.reload()
            self.phase, self.phase_msg = "starting", ""
            for key in ctl.SERVICES:
                s = self.services[key]
                if gen != self.generation or self.shutdown_event.is_set():
                    return
                if key not in names:
                    # 自分で用意する外部の LLM サーバー: 応答するまで待つ（起動はしない）
                    s.state, s.started_at = "starting", time.time()
                    s.label = "LLM（外部サーバー）"
                    r = ctl.llm_resolved(self.cfg)
                    s.message = f"外部の LLM サーバー（{r['base_url']}）の応答を待っています。先に起動しておいてください。"
                    log(s.message)
                    if self.wait_ready_external(s, gen):
                        s.state, s.message = "external", "外部サーバーに接続しました（このアプリは起動も停止もしません）。"
                        continue
                    self._boot_failed(s)
                    return
                try:
                    spec = self.command_for(key)
                except Exception as e:  # noqa: BLE001
                    s.state, s.message = "failed", str(e)
                    log(s.message, "ERROR")
                    self._boot_failed(s)
                    return
                if spec is None:
                    s.state, s.started_at = "starting", time.time()
                    if self.wait_ready_external(s, gen):
                        s.state, s.message = "external", "すでに起動している Strata を使います。"
                        continue
                    self._boot_failed(s)
                    return
                cmd, cwd, env, label = spec
                self.spawn(s, label, cmd, cwd, env)
                timeout = self.cfg.get("strata_wait_sec", ctl.STRATA_WAIT_SEC) if label == "Strata" else self.cfg.get("start_timeout_sec", {}).get(key, 600)
                if not self.wait_ready(s, gen, timeout):
                    if gen == self.generation and not self.shutdown_event.is_set():
                        self._boot_failed(s)
                    return
            self.phase, self.phase_msg = "ready", ""
            log("すべてのサービスが準備できました。")
        except Exception as e:  # noqa: BLE001
            log(f"起動処理でエラー: {type(e).__name__}: {e}", "ERROR")
            self.phase, self.phase_msg = "failed", f"起動処理でエラーが起きました: {e}"

    def wait_ready_external(self, s: Service, gen: int) -> bool:
        limit = self.cfg.get("strata_wait_sec", ctl.STRATA_WAIT_SEC) if ctl.managed_strata(self.cfg) else self.cfg.get("external_wait_sec", 120)
        deadline = time.time() + limit
        while time.time() < deadline:
            if self.shutdown_event.is_set() or gen != self.generation:
                return False
            if ctl.check_health(self.cfg, s.key):
                return True
            time.sleep(2)
        s.state, s.message = "failed", f"外部の LLM サーバーに {limit} 秒以内に接続できませんでした。起動しているか、URL とポートを確認してください。"
        log(s.message, "ERROR")
        return False

    def _boot_failed(self, s: Service):
        self.phase = "failed"
        self.phase_msg = f"{s.label} が起動できません。ログを確認してください。"
        log(self.phase_msg, "ERROR")

    # ---- 監視（異常終了の検知と自動再起動、応答の確認） ----
    def monitor(self):
        last_health = 0.0
        while not self.shutdown_event.is_set():
            time.sleep(1.5)
            if self.phase in ("stopping", "idle"):
                continue
            for s in self.services.values():
                p = s.proc
                if p is None or s.stop_requested:
                    continue
                code = p.poll()
                if code is not None and s.state in ("ready", "no_response"):
                    self._crashed(s, code)
            if time.time() - last_health > 6:
                last_health = time.time()
                for s in self.services.values():
                    if s.state in ("ready", "no_response") and s.proc and s.proc.poll() is None:
                        ok = ctl.check_health(self.cfg, s.key)
                        s.miss = 0 if ok else s.miss + 1
                        if ok and s.state == "no_response":
                            s.state, s.message = "ready", ""
                        elif s.miss >= 3 and s.state == "ready":
                            s.state, s.message = "no_response", f"{s.label} が応答しません（処理中かもしれません）。続くときはログを確認して再起動してください。"
                            log(s.message, "WARNING")

    def _crashed(self, s: Service, code: int):
        limit, window = restart_policy()
        now = time.time()
        while s.restarts and now - s.restarts[0] > window:
            s.restarts.popleft()
        log(f"{s.label} が異常終了しました（コード {code}）。ログの末尾を確認してください。", "ERROR")
        if len(s.restarts) >= limit:
            s.state = "failed"
            span = f"{window // 60}分" if window >= 60 else f"{window}秒"
            s.message = f"{s.label} が起動できません。ログを確認してください。（{span}に {limit} 回再起動しましたが直りませんでした。自動の再起動は止めました）"
            log(s.message, "ERROR")
            self.phase, self.phase_msg = "failed", s.message
            return
        s.restarts.append(now)
        s.total_restarts += 1
        s.state = "crashed"
        s.message = f"異常終了（コード {code}）。自動で再起動します（{len(s.restarts)}/{limit}）。"
        log(s.message, "WARNING")
        gen = self.generation
        threading.Thread(target=self._relaunch, args=(s, gen), daemon=True, name=f"relaunch-{s.key}").start()

    def _relaunch(self, s: Service, gen: int):
        try:
            time.sleep(2)
            if gen != self.generation or self.shutdown_event.is_set():
                return
            self.reload()
            spec = self.command_for(s.key)
            if spec is None:
                s.state = "external"
                return
            # 親だけが終了して、子がポートを握ったまま残っていることがあるので、片付けてから起動する
            if s.port:
                for pid in ctl.listener_pids(s.port):
                    ctl.kill_tree(pid)
                time.sleep(1)
            cmd, cwd, env, label = spec
            self.spawn(s, label, cmd, cwd, env)
            if not self.wait_ready(s, gen, self.cfg.get("start_timeout_sec", {}).get(s.key, 600)) and s.state == "failed":
                self.phase, self.phase_msg = "failed", f"{s.label} が起動できません。ログを確認してください。"
        except Exception as e:  # noqa: BLE001
            s.state, s.message = "failed", f"{s.label} を再起動できませんでした: {e}"
            log(s.message, "ERROR")

    # ---- 停止・再起動 ----
    def stop_service(self, s: Service):
        s.stop_requested = True
        p = s.proc
        if p is not None and p.poll() is None:
            ctl.kill_tree(p.pid)
            try:
                p.wait(timeout=15)
            except Exception:
                pass
        # 中継が無い（外部から起動された）ものは、ポートの待ち受けも探して止める
        if s.state != "external" and s.port and s.key != "llm":
            for pid in ctl.listener_pids(s.port):
                if not (p and pid == p.pid):
                    ctl.kill_tree(pid)
        if s.state != "external":
            s.state = "stopped"
        s.proc = None

    def stop_all(self):
        self.generation += 1
        self.phase = "stopping"
        for key in reversed(ctl.SERVICES):
            s = self.services[key]
            if s.proc is not None or s.state in ("starting", "ready", "no_response", "crashed"):
                self.stop_service(s)
                log(f"{s.label}: 停止")
        for s in self.services.values():
            if s.state != "failed":
                s.state = "stopped"
            s.message = "" if s.state == "stopped" else s.message

    def start_all(self):
        with self.lock:
            self.generation += 1
            gen = self.generation
            for s in self.services.values():
                s.restarts.clear()
                if s.state in ("failed", "crashed"):
                    s.state, s.message = "stopped", ""
            self.phase, self.phase_msg = "starting", ""
            self.boot_thread = threading.Thread(target=self.boot, args=(gen,), daemon=True, name="boot")
            self.boot_thread.start()

    def restart_all(self):
        def work():
            with self.lock:
                log("全体を再起動します。")
                self.stop_all()
                time.sleep(2)
            self.start_all()
        threading.Thread(target=work, daemon=True, name="restart-all").start()

    def restart_service(self, key: str):
        """1 サービスだけ再起動する。LLM が Strata のときと、全体が起動中のときは、全体を再起動する"""
        if key not in self.services:
            raise KeyError(key)
        if self.phase == "starting" or (key == "llm" and ctl.managed_strata(self.cfg)):
            return self.restart_all()

        def work():
            s = self.services[key]
            gen = self.generation
            log(f"{s.label} を再起動します。")
            self.stop_service(s)
            time.sleep(1)
            s.restarts.clear()
            try:
                self.reload()
                spec = self.command_for(key)
                if spec is None:
                    return
                cmd, cwd, env, label = spec
                self.spawn(s, label, cmd, cwd, env)
                self.wait_ready(s, gen, self.cfg.get("start_timeout_sec", {}).get(key, 600))
                if all(x.state in ("ready", "external") for x in self.services.values()):
                    self.phase = "ready"
            except Exception as e:  # noqa: BLE001
                s.state, s.message = "failed", f"{s.label} を再起動できませんでした: {e}"
                log(s.message, "ERROR")
        threading.Thread(target=work, daemon=True, name=f"restart-{key}").start()

    def shutdown(self):
        """全部止めて、supervisor も終了する"""
        if self.shutdown_event.is_set():
            return
        log("終了します。")
        self.shutdown_event.set()
        try:
            self.stop_all()
        finally:
            try:
                icon = getattr(self, "tray_icon", None)
                if icon:
                    icon.stop()         # トレイに幽霊アイコンを残さない
            except Exception:
                pass
            try:
                INFO_FILE.unlink()
            except OSError:
                pass
            time.sleep(0.5)
            os._exit(0)

    # ---- 状態 ----
    def state(self) -> dict:
        gpu = ctl.nvidia_gpu(self.cfg["gpu"]["index"]) if time.time() - getattr(self, "_gpu_t", 0) > 2 else getattr(self, "_gpu", None)
        if gpu is not None or not hasattr(self, "_gpu_t"):
            self._gpu, self._gpu_t = gpu, time.time()
        ports = self.cfg["ports"]
        return {
            "phase": self.phase, "phase_msg": self.phase_msg,
            "services": [self.services[k].view() for k in ctl.SERVICES],
            "ready": self.phase == "ready" and all(self.services[k].state in ("ready", "external") for k in ctl.SERVICES),
            "ports": ports, "chat_url": f"http://127.0.0.1:{ports['app']}/static/index.html",
            "settings_url": f"http://127.0.0.1:{ports['app']}/settings",
            "gpu": self._gpu, "ram": ram_info(), "uptime": int(time.time() - self.started_at), "tray": self.tray_ok,
            "restart_limit": list(restart_policy()),
        }


def ram_info() -> dict:
    class M(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                    ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    m = M()
    m.dwLength = ctypes.sizeof(M)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {"total_mib": int(m.ullTotalPhys / 2**20), "used_mib": int((m.ullTotalPhys - m.ullAvailPhys) / 2**20)}


# ------------------------------------------------------------------ 不具合報告 ----

def _run_text(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL, errors="replace",
                              creationflags=CREATE_NO_WINDOW).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"(取得できません: {type(e).__name__})"


def env_report(sup: Supervisor) -> str:
    from importlib import metadata

    def ver(pkg):
        try:
            return metadata.version(pkg)
        except Exception:
            return "-"
    cfg = sup.cfg
    try:
        s = ss.load_settings(ROOT)
        llm = s["llm"]
        t = s["tools"]
        settings_line = (f"llm.mode={llm['mode']} profile={llm.get('profile') or '(導入時)'} thinking={llm.get('thinking')} "
                         f"tools: web={t['web_search']} weather={t['weather']} memory={t['memory']} skills={t['skills']} mcp={t['mcp']} agent={t['agent']['enabled']}")
    except Exception as e:  # noqa: BLE001
        settings_line = f"(設定を読めません: {e})"
    gpu = _run_text(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used", "--format=csv,noheader"])
    ram = ram_info()
    lines = [
        f"koemado 不具合報告 {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"OS: {platform.platform()}", f"Python(supervisor): {platform.python_version()}",
        f"GPU: {gpu}", f"RAM: {ram['used_mib']} / {ram['total_mib']} MiB 使用中",
        f"プロファイル(導入時): {cfg.get('profile')}  VRAM(導入時の判定): {cfg.get('gpu', {}).get('vram_mib')} MiB",
        f"固定バージョン: {json.dumps(cfg.get('pinned', {}), ensure_ascii=False)}",
        "パッケージ: " + ", ".join(f"{p}={ver(p)}" for p in ("aiavatar", "fastmcp", "ddgs", "fastapi", "uvicorn", "openai", "torch", "pystray", "pillow")),
        f"設定(要約。キー・URL は含みません): {settings_line}",
        "サービス:",
    ]
    for v in sup.state()["services"]:
        lines.append(f"  {v['key']:<4} {v['label']:<22} state={v['state']:<12} port={v['port']} restarts={v['restarts']} {v['message']}")
    mcp_names = []
    try:
        mcp_names = list(json.loads((ROOT / "config" / "mcp.json").read_text(encoding="utf-8-sig")).get("mcpServers", {}))
    except Exception:
        pass
    lines.append("MCP サーバー名: " + (", ".join(mcp_names) or "なし"))
    return Redactor.scrub_paths(REDACT("\n".join(lines))) + "\n"


def make_report_zip(sup: Supervisor) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("env.txt", env_report(sup))
        names = set(HUB.rings) | {p.name.split(".log")[0] for p in LOG_DIR.glob("*.log*")}
        for svc in sorted(names):
            z.writestr(f"logs/{svc}.log", Redactor.scrub_paths(HUB.file_text(svc)))
        z.writestr("README.txt", "koemado の不具合報告です。API キーなどは伏せ字にしてありますが、送る前に中身を確認してください。\n")
    return buf.getvalue()


# ------------------------------------------------------------------ 管理画面（Web） ----

def make_app(sup: Supervisor):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
    from settings_api import LocalOnlyMiddleware

    app = FastAPI()
    page = ROOT / "console_ui" / "index.html"

    def guard(request: Request):
        if request.headers.get(CONSOLE_HEADER) != "1":
            raise HTTPException(403, "不正なリクエストです。")

    @app.get("/")
    async def index():
        return FileResponse(page, headers={"Cache-Control": "no-store"})

    @app.get("/api/ping")
    async def ping():
        return {"ok": True, "pid": os.getpid(), "ready": sup.phase == "ready"}

    @app.get("/api/state")
    async def state():
        return JSONResponse(await asyncio.get_running_loop().run_in_executor(None, sup.state), headers={"Cache-Control": "no-store"})

    def valid(svc: str):
        if svc not in ("all", "supervisor", *ALL_SERVICES):
            raise HTTPException(404, "サービス名が不正です。")

    @app.get("/api/logs")
    async def logs(service: str = "all", since: int = 0, limit: int = 2000):
        valid(service)
        return JSONResponse({"entries": HUB.get(service, since, min(limit, 5000)), "seq": HUB.seq}, headers={"Cache-Control": "no-store"})

    @app.get("/api/stream")
    async def stream(request: Request, service: str = "all", since: int = 0):
        valid(service)

        async def gen():
            last = since
            idle = 0
            while True:
                if await request.is_disconnected():
                    return
                entries = HUB.get(service, last, 500)
                if entries:
                    last = entries[-1]["seq"]
                    idle = 0
                    yield f"data: {json.dumps(entries, ensure_ascii=False)}\n\n"
                else:
                    idle += 1
                    if idle % 20 == 0:
                        yield ": keep-alive\n\n"
                await asyncio.sleep(0.4)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/logs/download")
    async def download(service: str = "all"):
        valid(service)
        if service == "all":
            names = sorted(set(HUB.rings) | {p.name.split(".log")[0] for p in LOG_DIR.glob("*.log*")})
            text = "\n".join(f"===== {n} =====\n{HUB.file_text(n)}" for n in names)
        else:
            text = HUB.file_text(service)
        return Response(text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="koemado-{service}-{time.strftime("%Y%m%d-%H%M%S")}.log"'})

    @app.get("/api/report")
    async def report():
        data = await asyncio.get_running_loop().run_in_executor(None, make_report_zip, sup)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="koemado-report-{time.strftime("%Y%m%d-%H%M%S")}.zip"'})

    @app.post("/api/restart/{svc}")
    async def restart(svc: str, request: Request):
        guard(request)
        if svc == "all":
            sup.restart_all()
        elif svc in ALL_SERVICES:
            sup.restart_service(svc)
        else:
            raise HTTPException(404, "サービス名が不正です。")
        return {"ok": True}

    @app.post("/api/shutdown")
    async def shutdown(request: Request):
        guard(request)
        threading.Timer(0.5, sup.shutdown).start()
        return {"ok": True}

    app.add_middleware(LocalOnlyMiddleware)
    return app


def serve_console(sup: Supervisor):
    import uvicorn
    app = make_app(sup)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=sup.console_port, log_level="warning", access_log=False)
    uvicorn.Server(cfg).run()


# ------------------------------------------------------------------ タスクトレイ ----

def make_icon_image(size: int = 64):
    """声の窓: 角の丸い窓に、音の波を重ねた図形"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.rounded_rectangle([4 * s, 6 * s, 60 * s, 58 * s], radius=12 * s, fill=(59, 111, 224, 255))
    d.rounded_rectangle([10 * s, 12 * s, 54 * s, 52 * s], radius=7 * s, fill=(244, 246, 250, 255))
    d.line([(32 * s, 12 * s), (32 * s, 52 * s)], fill=(59, 111, 224, 255), width=max(1, int(2 * s)))
    d.line([(10 * s, 32 * s), (54 * s, 32 * s)], fill=(59, 111, 224, 255), width=max(1, int(2 * s)))
    for r, a in ((6, 255), (11, 200), (16, 140)):     # 音の波
        d.arc([(32 - r) * s, (32 - r) * s, (32 + r) * s, (32 + r) * s], start=-50, end=50, fill=(224, 90, 59, a), width=max(2, int(3 * s)))
    d.ellipse([27 * s, 27 * s, 37 * s, 37 * s], fill=(224, 90, 59, 255))
    return img


class TrayActions:
    """メニュー項目が呼ぶ関数（テストからも直接呼べる）"""

    def __init__(self, sup: Supervisor):
        self.sup = sup

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.sup.cfg['ports']['app']}{path}"

    def open_chat(self, *_):
        webbrowser.open(self._url("/static/index.html"))

    def open_settings(self, *_):
        webbrowser.open(self._url("/settings"))

    def open_console(self, *_):
        webbrowser.open(f"http://127.0.0.1:{self.sup.console_port}/")

    def restart(self, *_):
        self.sup.restart_all()

    def quit(self, *_):
        threading.Thread(target=self.sup.shutdown, daemon=True).start()


def run_tray(sup: Supervisor):
    try:
        import pystray
        act = TrayActions(sup)
        menu = pystray.Menu(
            pystray.MenuItem("会話画面を開く", act.open_chat, default=True),
            pystray.MenuItem("設定", act.open_settings),
            pystray.MenuItem("ログ・状態", act.open_console),
            pystray.MenuItem("再起動", act.restart),
            pystray.MenuItem("終了", act.quit),
        )
        icon = pystray.Icon("koemado", make_icon_image(64), "koemado", menu)
        sup.tray_ok = True
        sup.tray_icon = icon
        icon.run()
    except Exception as e:  # noqa: BLE001
        sup.tray_ok = False
        log(f"タスクトレイのアイコンを出せませんでした（本体は動き続けます）: {type(e).__name__}: {e}", "WARNING")


# ------------------------------------------------------------------ 起動（launch / run） ----

def read_info() -> dict | None:
    try:
        return json.loads(INFO_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def ping(port: int, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def running_supervisor() -> dict | None:
    """動いている supervisor の情報（{pid, port}）。無ければ None"""
    info = read_info()
    if info and ping(info["port"]):
        return info
    return None


def request(port: int, path: str, method: str = "POST", timeout: float = 10.0):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"{}" if method == "POST" else None,
                                 headers={CONSOLE_HEADER: "1", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def interpreter() -> str:
    p = ROOT / "app" / ".venv" / "Scripts" / "python.exe"
    return str(p if p.exists() else sys.executable)


def launch(open_browser: bool = True) -> int:
    """すでに動いていればブラウザを開くだけ。無ければ supervisor を起動して、管理画面を開く"""
    open_browser = open_browser and os.environ.get("KOEMADO_NO_BROWSER") != "1"     # テスト用: ブラウザを開かない
    info = running_supervisor()
    if info is None:
        RUN_DIR.mkdir(exist_ok=True)
        subprocess.Popen([interpreter(), str(ROOT / "supervisor.py"), "run"], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
        deadline = time.time() + 30
        while time.time() < deadline:
            info = running_supervisor()
            if info:
                break
            time.sleep(0.5)
        if info is None:
            return 1
        base = f"http://127.0.0.1:{info['port']}"
        if open_browser:
            webbrowser.open(base + "/?boot=1")        # 起動中の表示。準備ができたら会話画面へ自動で移る
        return 0
    st = ping(info["port"]) or {}
    base = f"http://127.0.0.1:{info['port']}"
    if open_browser:
        if st.get("ready"):
            try:
                webbrowser.open(json.loads(urllib.request.urlopen(base + "/api/state", timeout=5).read().decode())["chat_url"])
            except Exception:
                webbrowser.open(base + "/")
        else:
            webbrowser.open(base + "/?boot=1")
    return 0


def acquire_mutex():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = wintypes.HANDLE
    name = "Local\\koemado-supervisor-" + re.sub(r"[^A-Za-z0-9]", "_", str(ROOT))[-80:]
    h = k.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:    # ERROR_ALREADY_EXISTS
        return None
    return h


def pick_console_port(cfg: dict) -> int:
    pref = cfg["ports"].get("console", DEFAULT_CONSOLE_PORT)
    port = pref
    others = {v for k, v in cfg["ports"].items() if k != "console"}
    while ctl.port_in_use(port) or port in others:
        port += 1
    if port != cfg["ports"].get("console"):
        cfg["ports"]["console"] = port
        ctl.save_config(cfg)
    return port


def run() -> int:
    LOG_DIR.mkdir(exist_ok=True)
    RUN_DIR.mkdir(exist_ok=True)
    mutex = acquire_mutex()
    if mutex is None:
        return 0            # すでに動いている（二重起動しない）
    sys.stdout = _StdWriter()
    sys.stderr = _StdWriter("ERROR")
    sup = Supervisor()
    sup.console_port = pick_console_port(sup.cfg)
    sup.reload()
    INFO_FILE.write_text(json.dumps({"pid": os.getpid(), "port": sup.console_port, "started": time.time()}), encoding="utf-8")
    log(f"supervisor を起動しました（PID {os.getpid()}、管理画面 http://127.0.0.1:{sup.console_port}/）")
    threading.Thread(target=serve_console, args=(sup,), daemon=True, name="console").start()
    threading.Thread(target=sup.monitor, daemon=True, name="monitor").start()
    if os.environ.get("KOEMADO_NO_TRAY") != "1":
        threading.Thread(target=run_tray, args=(sup,), daemon=True, name="tray").start()
    sup.start_all()
    while True:             # 終了は shutdown() が、子プロセスを止めたあとで os._exit する（ここで抜けない）
        time.sleep(1)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "launch"
    if cmd == "run":
        return run()
    if cmd == "launch":
        return launch(open_browser="--no-browser" not in argv)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
