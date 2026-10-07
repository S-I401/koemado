"""Hugging Face からのモデル取得を `hf download`（huggingface_hub の CLI + hf_xet）にそろえる（標準ライブラリだけ）。

setup.py（初回の導入）と設定画面（supervisor 配下のアプリ）が、同じ hf.exe（<インストール先>\\tools\\hf-venv）を使う。
- トークンは引数ではなく環境変数 HF_TOKEN で子プロセスにだけ渡す（引数はプロセス一覧に出るため）。HF_HUB_DISABLE_TELEMETRY=1 も付ける。
- 進捗は、--local-dir の .cache/huggingface/download にできる途中ファイル（*.incomplete）のサイズで出す。
- キャンセルはプロセスの停止。`hf download` は、もう一度実行すると続きから再開する。
- 遅いとき（既定: 60 秒の平均が 1MB/s 未満）は hf download を止めて、16 並列の Range 取得に切り替える。
  ファイルの SHA256（HF の LFS の oid）で検証する。トークンは huggingface.co だけに付ける（hf_hub.open_url の安全策）。
"""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import hf_hub

HF_PINS = ["huggingface_hub[hf_xet]==2.1.1", "hf-xet==1.7.0"]
CREATE_NO_WINDOW = 0x08000000
INCOMPLETE_RE = re.compile(r"\.([0-9a-f]{40,64})\.[0-9A-Za-z]+\.incomplete$")
CHUNK = 32 * 1024 * 1024
WORKERS = 16


def slow_limits() -> tuple[float, float]:
    """(ウィンドウ秒, しきい値 bytes/秒)。テスト用に環境変数で変えられる"""
    try:
        w = float(os.environ.get("KOEMADO_HF_SLOW_WINDOW_SEC", "60"))
    except ValueError:
        w = 60.0
    try:
        b = float(os.environ.get("KOEMADO_HF_SLOW_BPS", "1000000"))
    except ValueError:
        b = 1e6
    return w, b


def hf_exe(root) -> Path:
    return Path(root) / "tools" / "hf-venv" / "Scripts" / "hf.exe"


def ensure_hf_cli(root, uv: str | None = None, env: dict | None = None, python: str | None = None, log=print) -> Path:
    """tools\\hf-venv に hf.exe（バージョン固定）を入れる。無ければ作る"""
    root = Path(root)
    exe = hf_exe(root)
    if exe.exists():
        return exe
    venv = exe.parent.parent
    log("  Hugging Face の CLI（hf、hf_xet 入り）を入れます…")
    uv = uv or next((str(p) for p in (root / "tools" / "uv.exe",) if p.exists()), None) or shutil.which("uv")
    kw = {"env": env, "capture_output": True, "text": True, "errors": "replace", "creationflags": CREATE_NO_WINDOW, "stdin": subprocess.DEVNULL}
    steps = []
    if uv:
        py = venv / "Scripts" / "python.exe"
        steps = [[uv, "venv", str(venv), "--allow-existing"] + (["--python", python] if python else []), [uv, "pip", "install", "--python", str(py)] + HF_PINS]
    else:
        py = venv / "Scripts" / "python.exe"
        steps = [[sys.executable, "-m", "venv", str(venv)], [str(py), "-m", "pip", "install", "-q"] + HF_PINS]
    for cmd in steps:
        r = subprocess.run(cmd, **kw)
        if r.returncode != 0:
            raise RuntimeError("hf の導入に失敗しました: " + (r.stderr or r.stdout or "")[-300:])
    if not exe.exists():
        raise RuntimeError("hf.exe が見つかりません。")
    return exe


def hf_env(token: str = "", base: dict | None = None) -> dict:
    env = dict(base if base is not None else os.environ)
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["PYTHONUTF8"] = "1"
    env.pop("HF_TOKEN", None)
    env.pop("HUGGING_FACE_HUB_TOKEN", None)
    if token:
        env["HF_TOKEN"] = token
    return env


def redact(line: str, token: str = "") -> str:
    if token:
        line = line.replace(token, "●●●●●●●●")
    return re.sub(r"hf_[A-Za-z0-9]{8,}", "●●●●●●●●", line)


def classify(text: str, has_token: bool, repo: str) -> dict:
    t = text.lower()
    if "gated" in t or "403" in t or "restricted" in t or "access to model" in t:
        return {"error_kind": "gated", "error": "HF のページで利用条件に同意してください（同意すると、このモデルをダウンロードできます）。", "link": f"https://huggingface.co/{repo}"}
    if "401" in t or "unauthorized" in t or "invalid user token" in t or "repository not found" in t:
        if has_token:
            return {"error_kind": "invalid_token", "error": "Hugging Face に拒否されました。トークンが無効、または期限切れの可能性があります。", "link": "https://huggingface.co/settings/tokens"}
        return {"error_kind": "token_required", "error": "このモデルのダウンロードには、Hugging Face のトークンの登録が必要です。", "link": "https://huggingface.co/settings/tokens"}
    tail = " / ".join(x for x in text.strip().splitlines()[-3:])[:300]
    return {"error_kind": "other", "error": "ダウンロードに失敗しました。もう一度押すと続きから再開します。" + (f"（{tail}）" if tail else "")}


def incomplete_map(local_dir: Path) -> dict:
    """local_dir/.cache/huggingface/download の途中ファイルを、LFS の sha256 ごとの最大サイズにまとめる（sha が読めないものは ""）"""
    out: dict[str, int] = {}
    d = Path(local_dir) / ".cache" / "huggingface" / "download"
    if d.is_dir():
        for f in d.glob("*.incomplete"):
            m = INCOMPLETE_RE.search(f.name)
            key = m.group(1) if m else ""
            try:
                out[key] = max(out.get(key, 0), f.stat().st_size)
            except OSError:
                pass
    return out


def done_bytes(local_dir: Path, files: list[dict]) -> int:
    inc = incomplete_map(local_dir)
    total, unknown_left, matched = 0, 0, set()
    for f in files:
        p = Path(local_dir) / f["path"]
        size = int(f.get("size") or 0)
        if p.exists() and (not size or p.stat().st_size == size):
            total += p.stat().st_size
        elif f.get("sha256") in inc:
            matched.add(f["sha256"])
            total += min(inc[f["sha256"]], size or inc[f["sha256"]])
        else:
            unknown_left += size or (1 << 62)
    # sha256 で対応づけられなかった途中ファイル（etag が sha256 でないもの）は、残りのファイルの分としてまとめて足す
    rest = sum(v for k, v in inc.items() if k not in matched)
    return total + min(rest, unknown_left)


class Cancelled(Exception):
    pass


class Job:
    """1 リポジトリの複数ファイルを、1 回の hf download で取得する（分割 GGUF は全パーツをまとめて）。
    status（dict）に、status / done / total / engine / speed / message / error などを書く。run() は呼び出したスレッドで動く"""

    def __init__(self, root, repo: str, files: list[dict], local_dir, token: str = "", status: dict | None = None, log=None,
                 flags: int = 0, uv: str | None = None, base_env: dict | None = None):
        self.root, self.repo, self.local_dir = Path(root), repo, Path(local_dir)
        self.files = [dict(f) for f in files]
        self.token = token
        self.status = status if status is not None else {}
        self.log = log or print
        self.flags = flags
        self.uv = uv
        self.base_env = base_env
        self.cancel_event = threading.Event()
        self.proc = None
        self.total = sum(int(f.get("size") or 0) for f in self.files)

    # ---- 外から ----
    def cancel(self):
        self.cancel_event.set()
        p = self.proc
        if p and p.poll() is None:
            _kill_tree(p.pid)

    def _say(self, line: str):
        self.log(redact(line, self.token))

    def _update(self, **kw):
        self.status.update(kw)

    def pending(self) -> list[dict]:
        out = []
        for f in self.files:
            p = self.local_dir / f["path"]
            size = int(f.get("size") or 0)
            if not (p.exists() and (not size or p.stat().st_size == size)):
                out.append(f)
        return out

    def run(self) -> bool:
        """True = 完了。キャンセルは status="cancelled"、失敗は status="error"（error_kind / error / link）"""
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self._update(status="running", total=self.total, done=done_bytes(self.local_dir, self.files), engine="hf", speed=0, message="")
        try:
            exe = ensure_hf_cli(self.root, self.uv, env=self.base_env, log=self._say)
            if self.pending():
                engine = self._run_hf(exe)
                if engine == "slow":
                    self._run_range()
            bad = [f["path"] for f in self.files if not (self.local_dir / f["path"]).exists()]
            if bad:
                raise RuntimeError("取得できなかったファイル: " + ", ".join(bad))
            self._update(status="done", done=self.total or done_bytes(self.local_dir, self.files), speed=0)
            return True
        except Cancelled:
            self._update(status="cancelled", speed=0)
            return False
        except Exception as e:  # noqa: BLE001
            info = getattr(e, "info", None) or classify(str(e), bool(self.token), self.repo)
            self._update(status="error", speed=0, **info)
            self._say(f"[hf] 失敗: {info.get('error')}")
            return False

    # ---- hf download ----
    def _run_hf(self, exe: Path) -> str:
        paths = [f["path"] for f in self.pending()]
        cmd = [str(exe), "download", self.repo, *paths, "--local-dir", str(self.local_dir), "--max-workers", "8"]
        self._say(f"[hf] hf download {self.repo} ({len(paths)} ファイル) → {self.local_dir}")
        tail: list[str] = []
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env=hf_env(self.token, self.base_env), creationflags=self.flags)

        def reader():
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line.strip():
                    tail.append(line)
                    del tail[:-30]
                    self._say("[hf] " + line)
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        window, thr = slow_limits()
        t0 = time.time()
        samples = [(t0, done_bytes(self.local_dir, self.files))]
        try:
            while self.proc.poll() is None:
                time.sleep(0.7)
                if self.cancel_event.is_set():
                    _kill_tree(self.proc.pid)
                    raise Cancelled()
                now = time.time()
                done = done_bytes(self.local_dir, self.files)
                samples.append((now, done))
                while len(samples) > 2 and samples[1][0] <= now - window:
                    samples.pop(0)
                base = samples[0]
                speed = (done - base[1]) / max(0.5, now - base[0])
                self._update(done=done, speed=int(speed), engine="hf")
                if now - t0 >= window and now - base[0] >= window * 0.9 and speed < thr and (not self.total or done < self.total * 0.995):
                    self._say(f"[hf] 速度が {speed / 1e6:.2f}MB/s（{window:.0f} 秒の平均）としきい値 {thr / 1e6:.2f}MB/s を下回ったため、hf download を止めて並列の Range 取得に切り替えます。")
                    _kill_tree(self.proc.pid)
                    self.proc.wait(timeout=20)
                    return "slow"
        finally:
            t.join(timeout=3)
        if self.cancel_event.is_set():
            raise Cancelled()
        if self.proc.returncode != 0:
            raise RuntimeError("\n".join(tail))
        return "ok"

    # ---- 予備: 16 並列の Range 取得 ----
    def _run_range(self):
        self._update(engine="range", message=f"速度が遅いため、並列（{WORKERS} 本）の Range 取得に切り替えました。", speed=0)
        for f in self.pending():
            self._range_file(f)

    def _range_file(self, f: dict):
        path = f["path"]
        dest = self.local_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = int(f.get("size") or 0) or (hf_hub.head_size(self.repo, path, self.token) or 0)
        if not size:
            raise RuntimeError(f"{path} のサイズを取得できません。")
        part = Path(str(dest) + ".koemado-part")
        meta_p = Path(str(dest) + ".koemado-part.json")
        n = math.ceil(size / CHUNK)
        done_idx: set[int] = set()
        if part.exists() and meta_p.exists():
            try:
                m = json.loads(meta_p.read_text())
                if m.get("size") == size:
                    done_idx = set(m.get("done", []))
            except Exception:
                done_idx = set()
        if not part.exists() or part.stat().st_size != size:
            with open(part, "wb") as fh:
                fh.truncate(size)
            done_idx = set()
        lock = threading.Lock()
        got = {"bytes": sum(min(CHUNK, size - i * CHUNK) for i in done_idx)}
        base_other = done_bytes(self.local_dir, [x for x in self.files if x["path"] != path])
        url = hf_hub.file_url(self.repo, path)
        t0 = time.time()
        last = {"t": t0, "b": got["bytes"]}

        def save_meta():
            meta_p.write_text(json.dumps({"size": size, "done": sorted(done_idx)}))

        def fetch(i: int):
            if self.cancel_event.is_set():
                return
            a, b = i * CHUNK, min(size, (i + 1) * CHUNK) - 1
            for attempt in range(4):
                mine = 0
                try:
                    req = hf_hub.make_request(url, self.token, {"Range": f"bytes={a}-{b}"})
                    with hf_hub.open_url(req, 60) as r, open(part, "r+b") as fh:
                        fh.seek(a)
                        remaining = b - a + 1
                        while remaining > 0:
                            if self.cancel_event.is_set():
                                return
                            buf = r.read(min(1 << 20, remaining))
                            if not buf:
                                raise IOError("short read")
                            fh.write(buf)
                            remaining -= len(buf)
                            mine += len(buf)
                            with lock:
                                got["bytes"] += len(buf)
                    with lock:
                        done_idx.add(i)
                        if len(done_idx) % 4 == 0:
                            save_meta()
                    return
                except Exception as e:  # noqa: BLE001
                    err = e
                    with lock:
                        got["bytes"] -= mine
                    time.sleep(1 + attempt)
            raise RuntimeError(f"Range 取得に失敗しました（{type(err).__name__}）")

        stop = threading.Event()

        def progress():
            while not stop.is_set():
                time.sleep(0.7)
                now = time.time()
                with lock:
                    b = got["bytes"]
                sp = (b - last["b"]) / max(0.1, now - last["t"])
                last.update(t=now, b=b)
                self._update(done=base_other + min(b, size), speed=int(sp), engine="range")
        pt = threading.Thread(target=progress, daemon=True)
        pt.start()
        try:
            todo = [i for i in range(n) if i not in done_idx]
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                list(ex.map(fetch, todo))
        finally:
            stop.set()
            save_meta()
        if self.cancel_event.is_set():
            raise Cancelled()
        if len(done_idx) != n:
            raise RuntimeError("Range 取得が途中で終わりました。")
        self._update(message="SHA256 を検証しています…", speed=0)
        want = (f.get("sha256") or "").lower()
        if want:
            h = hashlib.sha256()
            with open(part, "rb") as fh:
                for blk in iter(lambda: fh.read(8 << 20), b""):
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    h.update(blk)
            if h.hexdigest() != want:
                part.unlink(missing_ok=True)
                meta_p.unlink(missing_ok=True)
                raise RuntimeError(f"SHA256 が一致しません（{path}）。もう一度ダウンロードしてください。")
            self._say(f"[range] {path}: SHA256 が一致しました（{want[:12]}…）")
        elif part.stat().st_size != size:
            raise RuntimeError("サイズが一致しません。")
        part.replace(dest)
        meta_p.unlink(missing_ok=True)
        if want:    # 切り替え前に hf download が残した途中ファイルを消す
            for inc in (self.local_dir / ".cache" / "huggingface" / "download").glob(f"*.{want}.*.incomplete"):
                inc.unlink(missing_ok=True)
        self._update(message=f"並列の Range 取得で {Path(path).name} を取得しました（SHA256 検証{'済み' if want else '（HF にハッシュが無いのでサイズのみ）'}）。")


def _kill_tree(pid: int):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)


def hf_fetch(root, repo: str, include: list[str] | None = None, cache_dir=None, token: str = "", log=print, flags: int = 0,
             uv: str | None = None, base_env: dict | None = None, echo: bool = True):
    """`hf download <repo> [--include ...] --cache-dir <dir>`（TTS・STT のモデルを HF のキャッシュに置く）。失敗したら RuntimeError"""
    exe = ensure_hf_cli(root, uv, env=base_env, log=log)
    cmd = [str(exe), "download", repo]
    for pat in include or []:
        cmd += ["--include", pat]
    if cache_dir:
        cmd += ["--cache-dir", str(cache_dir)]
    log(f"  $ hf download {repo}" + (f" --include {' '.join(include)}" if include else ""))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=hf_env(token, base_env), creationflags=flags)
    tail = []
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", "replace").rstrip()
        if line.strip():
            tail.append(line)
            del tail[:-20]
            if echo:
                log("    " + redact(line, token))
    if proc.wait() != 0:
        raise RuntimeError(f"hf download {repo} に失敗しました: " + redact(" / ".join(tail[-3:]), token))
