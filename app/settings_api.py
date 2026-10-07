"""設定画面（/settings）の API とページ。127.0.0.1 からのアクセスだけを受け付ける。

保存先は <インストール先>/config/settings.json と characters/<id>/ 。
声・キャラクター・temperature は次の発話から反映され、LLM の接続先やモデルは再起動で反映される。
"""
import asyncio
import base64
import ctypes
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ctl  # noqa: E402
import profiles  # noqa: E402
import settings_store as ss  # noqa: E402

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
EXPRESSIONS = ["neutral", "joy", "angry", "sorrow", "fun", "surprised", "eyes_closed"]
PREVIEW_TEXT = "こんにちは。今日はゆっくりお話ししましょう。最近あった楽しいことを、ぜひ聞かせてくださいね。"
DESIGN_TEXT = PREVIEW_TEXT
MAX_UPLOAD = 30 * 1024 * 1024
MAX_IMAGE = 10 * 1024 * 1024


# ------------------------------------------------------------------ 127.0.0.1 限定 ----

class LocalOnlyMiddleware:
    """ループバックからの接続で、Host / Origin もループバックのものだけを通す（DNS リバインディングと、
    他のサイトからの不正なリクエストを防ぐ）。HTTP と WebSocket の両方に適用する。"""

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _host_ok(value: str) -> bool:
        if not value:
            return False
        value = value.strip()
        if value.startswith("["):
            host = value[1:value.find("]")]
        else:
            host = value.split(":")[0]
        return host.lower() in LOOPBACK

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = (scope.get("client") or ("", 0))[0]
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            origin = headers.get("origin")
            ok = client in LOOPBACK and self._host_ok(headers.get("host", ""))
            if ok and origin and origin != "null":
                ok = self._host_ok(urlparse(origin).netloc)
            if not ok:
                if scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                    await send({"type": "http.response.body", "body": "このサービスは 127.0.0.1 からのみ利用できます。".encode()})
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


def guard_write(request: Request):
    """書き込み系: 設定画面の JS だけが付けるヘッダが必要（他のページからの送信を弾く）"""
    if request.headers.get("x-settings-request") != "1":
        raise HTTPException(403, "不正なリクエストです。")


# ------------------------------------------------------------------ 共通の部品 ----

def cfg() -> dict:
    return ctl.load_config(ROOT)


def tts_base() -> str:
    return f"http://127.0.0.1:{cfg()['ports']['tts']}/v1"


def ram_gib() -> float:
    class M(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                    ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    m = M()
    m.dwLength = ctypes.sizeof(M)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullTotalPhys / profiles.GIB


def hardware() -> dict:
    c = cfg()
    probe = ROOT
    return {
        "gpu": c["gpu"]["name"], "vram_gib": c["gpu"]["vram_mib"] / 1024, "simulated": c["gpu"].get("simulated", False),
        "ram_gib": ram_gib(), "disk_free_gib": shutil.disk_usage(probe).free / profiles.GIB,
    }


def model_warnings(size_bytes: int | None, existing_bytes: int = 0) -> list[dict]:
    """モデルのサイズを、この PC のスペックと比べる"""
    hw = hardware()
    out = []
    if not size_bytes:
        return [{"level": "info", "text": "サイズを取得できませんでした。"}]
    gb = size_bytes / profiles.GIB
    need = max(0.0, gb - existing_bytes / profiles.GIB)
    if need and hw["disk_free_gib"] < need + 1.0:
        out.append({"level": "error", "text": f"容量が足りません: ダウンロードに約 {need:.1f} GiB 必要ですが、空きは {hw['disk_free_gib']:.1f} GiB です。"})
    if gb > hw["ram_gib"] * 0.85 + hw["vram_gib"] * 0.5:
        out.append({"level": "error", "text": f"重すぎます: モデル {gb:.1f} GiB に対して、メモリ {hw['ram_gib']:.0f} GiB・VRAM {hw['vram_gib']:.0f} GiB では載りません（または極端に遅くなります）。"})
    elif gb > hw["ram_gib"] * 0.6 + hw["vram_gib"] * 0.5:
        out.append({"level": "warn", "text": f"重めです: モデル {gb:.1f} GiB は、メモリ {hw['ram_gib']:.0f} GiB・VRAM {hw['vram_gib']:.0f} GiB では余裕がありません。他のアプリを閉じて使ってください。"})
    if gb > hw["vram_gib"] - 2.5 and gb < hw["ram_gib"] * 0.6 + hw["vram_gib"] * 0.5:
        out.append({"level": "info", "text": "VRAM に収まらない分は CPU で処理するため、応答が遅くなります（MoE モデルなら比較的軽く済みます）。"})
    return out


def remote_size(repo: str, file: str) -> int | None:
    url = f"https://huggingface.co/{repo}/resolve/main/{file}"
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "aiavatar-portable"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except Exception:
        return None


async def tts_synth(text: str, voice: str, num_steps: int, caption: str | None = None, seed: int | None = None) -> bytes:
    irodori = {"num_steps": num_steps}
    if caption:
        irodori["caption"] = caption
    if seed is not None:
        irodori["seed"] = seed
    body = {"model": "irodori-tts", "input": text, "voice": voice, "response_format": "wav", "irodori": irodori}
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(tts_base() + "/audio/speech", json=body)
    if r.status_code != 200:
        raise HTTPException(502, f"TTS がエラーを返しました: {r.text[:200]}")
    return r.content


def spawn_detached(args: list[str]):
    """アプリ自身が止まっても続く子プロセスを作る（親子関係を切るため PowerShell を一度挟む）"""
    quoted = ",".join("'" + a.replace("'", "''") + "'" for a in args[1:])
    cmd = f"Start-Process -FilePath '{args[0]}' -ArgumentList {quoted} -WindowStyle Hidden -WorkingDirectory '{ROOT}'"
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], check=False, creationflags=0x08000000)


# ------------------------------------------------------------------ LLM ダウンロード ----

DOWNLOAD = {"status": "idle"}
_download_lock = threading.Lock()


def _start_download(repo: str, file: str):
    dest = ROOT / "models" / Path(file).name

    def work():
        try:
            def progress(done, total):
                DOWNLOAD.update(done=done, total=total, updated=time.time())
            ctl.download(f"https://huggingface.co/{repo}/resolve/main/{file}", dest, progress=progress)
            DOWNLOAD.update(status="done", file=dest.name)
        except Exception as e:  # noqa: BLE001
            DOWNLOAD.update(status="error", error=str(e))

    DOWNLOAD.clear()
    DOWNLOAD.update(status="running", repo=repo, file=dest.name, done=0, total=0, started=time.time())
    threading.Thread(target=work, daemon=True).start()


async def services_status(c: dict) -> dict:
    """各サービスの稼働状況。自分自身（app）は応答できている時点で起動中。ブロッキングの確認は別スレッドで行う"""
    loop = asyncio.get_running_loop()
    names = [n for n in ctl.SERVICES if n != "app"]
    results = await asyncio.gather(*[loop.run_in_executor(None, ctl.check_health, c, n) for n in names])
    out = dict(zip(names, results))
    out["app"] = True
    return {n: out[n] for n in ctl.SERVICES}


# ------------------------------------------------------------------ ルーター ----

def make_router(page_path: Path) -> APIRouter:
    r = APIRouter()

    @r.get("/settings")
    async def page():
        return FileResponse(page_path, headers={"Cache-Control": "no-store"})

    # ---- 状態 ----
    @r.get("/settings/api/state")
    async def state():
        s = ss.load_settings(ROOT)
        c = cfg()
        chars = ss.list_characters(ROOT)
        try:
            active = ss.active_character(s, ROOT)
        except Exception:
            active = {}
        resolved = ss.resolve_llm(c, s, ROOT)
        model_file = Path(resolved["model_path"]) if resolved["mode"] == "bundled" else None
        return {
            "settings": ss.masked(s),
            "hardware": hardware(),
            "installed_profile": c["profile"],
            "llama_installed": (ROOT / "llama.cpp" / "llama-server.exe").exists(),
            "active_voice": ss.effective_voice(s, active),
            "active_character": active.get("id"),
            "voices": ss.list_voices(ROOT),
            "characters": chars,
            "profiles": [
                {"id": pid, "label": p["label"], "repo": p["model"]["repo"], "file": p["model"]["file"],
                 "size_gib": p["model"]["size_bytes"] / profiles.GIB,
                 "installed": (ROOT / "models" / p["model"]["file"]).exists(),
                 "warnings": model_warnings(p["model"]["size_bytes"], (ROOT / "models" / p["model"]["file"]).stat().st_size if (ROOT / "models" / p["model"]["file"]).exists() else 0),
                 "min_vram_gib": p["min_vram_gib"], "min_ram_gib": p["min_ram_gib"]}
                for pid, p in profiles.PROFILES.items()
            ],
            "presets": ss.EXTERNAL_PRESETS,
            "reasoning_modes": ss.REASONING_MODES,
            "llm_resolved": {k: v for k, v in resolved.items() if k not in ("api_key",)},
            "model_file_exists": (model_file is not None and (model_file if model_file.is_absolute() else ROOT / model_file).exists()),
            "cloud": resolved["mode"] == "external" and ss.is_cloud_url(resolved["base_url"]),
            "services": await services_status(c),
            "ports": c["ports"],
        }

    @r.put("/settings/api/settings")
    async def put_settings(request: Request):
        guard_write(request)
        patch = await request.json()
        try:
            new = ss.update_settings(patch, ROOT)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e))
        return ss.masked(new)

    # ---- 声 ----
    @r.get("/settings/api/voices/preview")
    async def voice_preview(voice: str, num_steps: int = 0, text: str = ""):
        if not ss.valid_voice_id(voice) and voice != "none":
            raise HTTPException(400, "声の ID が不正です。")
        steps = num_steps or ss.load_settings(ROOT)["tts"]["num_steps"]
        wav = await tts_synth((text or PREVIEW_TEXT)[:200], voice, int(steps), seed=ss.load_settings(ROOT)["tts"]["seed"])
        return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @r.post("/settings/api/voices/upload")
    async def voice_upload(request: Request, file: UploadFile = File(...), name: str = Form(...), consent: str = Form("")):
        guard_write(request)
        if consent != "yes":
            raise HTTPException(400, "実在の人物の声は、本人の同意がある場合だけ使えます。同意の確認にチェックしてください。")
        if not ss.valid_voice_id(name):
            raise HTTPException(400, "声の ID は半角の英数字・_・- で 40 文字以内にしてください。")
        ext = Path(file.filename or "").suffix.lower()
        if ext in ss.REJECTED_EXTS:
            raise HTTPException(400, f"{ext} はこの環境では読み込めません。wav / flac / mp3 / ogg / opus に変換してください。")
        if ext not in ss.AUDIO_EXTS:
            raise HTTPException(400, "アップロードできる形式は wav / flac / mp3 / ogg / opus です。")
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(400, "ファイルが大きすぎます（30MB まで）。")
        d = ss.voices_dir(ROOT)
        d.mkdir(parents=True, exist_ok=True)
        if any(f.stem == name for f in d.iterdir() if f.is_file()):
            raise HTTPException(400, f"「{name}」はすでにあります。別の ID にしてください。")
        dest = d / f"{name}{ext}"
        dest.write_bytes(data)
        try:  # 実際に読み込めて合成できるか確認する
            await tts_synth("テストです。", name, 8)
        except HTTPException as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"この音声は参照音声として使えませんでした（{e.detail}）")
        meta = ss.load_voices_meta(ROOT)
        meta[name] = {"source": "upload", "original_name": file.filename, "consent_confirmed": True, "created": ss.now()}
        ss.save_voices_meta(meta, ROOT)
        return {"ok": True, "id": name}

    @r.post("/settings/api/voices/design")
    async def voice_design(request: Request):
        guard_write(request)
        body = await request.json()
        caption = (body.get("caption") or "").strip()
        if not caption:
            raise HTTPException(400, "キャプションを入力してください。")
        seed = int(body.get("seed") if body.get("seed") not in (None, "") else 42)
        steps = int(body.get("num_steps") or 32)
        wav = await tts_synth(DESIGN_TEXT, "none", steps, caption=caption, seed=seed)
        token = uuid.uuid4().hex[:12]
        tmp = ROOT / "app" / "data" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / f"design_{token}.wav").write_bytes(wav)
        (tmp / f"design_{token}.json").write_text(json.dumps({"caption": caption, "seed": seed, "num_steps": steps}, ensure_ascii=False), encoding="utf-8")
        return {"token": token, "audio": "data:audio/wav;base64," + base64.b64encode(wav).decode()}

    @r.post("/settings/api/voices/design/save")
    async def voice_design_save(request: Request):
        guard_write(request)
        body = await request.json()
        token, name = str(body.get("token", "")), body.get("name", "")
        if not re.fullmatch(r"[0-9a-f]{12}", token):
            raise HTTPException(400, "不正なトークンです。")
        if not ss.valid_voice_id(name):
            raise HTTPException(400, "声の ID は半角の英数字・_・- で 40 文字以内にしてください。")
        tmp = ROOT / "app" / "data" / "tmp"
        src = tmp / f"design_{token}.wav"
        if not src.exists():
            raise HTTPException(404, "候補が見つかりません。もう一度作ってください。")
        dest = ss.voices_dir(ROOT) / f"{name}.wav"
        if dest.exists():
            raise HTTPException(400, f"「{name}」はすでにあります。別の ID にしてください。")
        shutil.copy2(src, dest)
        info = json.loads((tmp / f"design_{token}.json").read_text(encoding="utf-8"))
        meta = ss.load_voices_meta(ROOT)
        meta[name] = {"source": "voice-design", **info, "created": ss.now(), "note": "Irodori の Voice Design で作った声（実在の人物の声ではない）"}
        ss.save_voices_meta(meta, ROOT)
        return {"ok": True, "id": name}

    @r.delete("/settings/api/voices/{voice_id}")
    async def voice_delete(voice_id: str, request: Request):
        guard_write(request)
        meta = ss.load_voices_meta(ROOT)
        if meta.get(voice_id, {}).get("source") not in ("upload", "voice-design"):
            raise HTTPException(400, "同梱の声は削除できません。")
        for f in ss.voices_dir(ROOT).glob(f"{voice_id}.*"):
            f.unlink()
        meta.pop(voice_id, None)
        ss.save_voices_meta(meta, ROOT)
        return {"ok": True}

    # ---- LLM ----
    @r.post("/settings/api/llm/check")
    async def llm_check(request: Request):
        guard_write(request)
        b = await request.json()
        if b.get("source") == "local":
            path = Path(b.get("path", ""))
            if path.suffix.lower() != ".gguf" or not path.is_file():
                raise HTTPException(400, ".gguf のファイルが見つかりません。")
            return {"size_gib": path.stat().st_size / profiles.GIB, "installed": True, "warnings": model_warnings(path.stat().st_size, path.stat().st_size)}
        repo, file = b.get("repo", ""), b.get("file", "")
        if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo) or not re.fullmatch(r"[\w.\-/]+\.gguf", file):
            raise HTTPException(400, "リポジトリは owner/name、ファイルは .gguf の名前で指定してください。")
        size = await asyncio.get_running_loop().run_in_executor(None, remote_size, repo, file)
        if size is None:
            raise HTTPException(404, "Hugging Face でファイルが見つかりませんでした（非公開・ゲート付きのモデルは未対応です）。")
        local = ROOT / "models" / Path(file).name
        have = local.stat().st_size if local.exists() else 0
        return {"size_gib": size / profiles.GIB, "installed": have >= size, "warnings": model_warnings(size, have)}

    @r.post("/settings/api/llm/download")
    async def llm_download(request: Request):
        guard_write(request)
        b = await request.json()
        repo, file = b.get("repo", ""), b.get("file", "")
        if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo) or not re.fullmatch(r"[\w.\-/]+\.gguf", file):
            raise HTTPException(400, "リポジトリまたはファイル名が不正です。")
        if b.get("confirmed") is not True:
            raise HTTPException(400, "ダウンロードの確認が必要です。")
        with _download_lock:
            if DOWNLOAD.get("status") == "running":
                raise HTTPException(409, "ダウンロード中です。")
            _start_download(repo, file)
        return {"ok": True}

    @r.get("/settings/api/llm/download/status")
    async def llm_download_status():
        return dict(DOWNLOAD)

    @r.post("/settings/api/llm/models")
    async def llm_models(request: Request):
        """外部サーバーの /v1/models から、使えるモデル名の候補を取る"""
        guard_write(request)
        b = await request.json()
        url, key = b.get("base_url", "").rstrip("/"), b.get("api_key") or ss.load_settings(ROOT)["llm"]["external"].get("api_key") or ""
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "base_url が不正です。")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url + "/models", headers={"Authorization": f"Bearer {key}"} if key else {})
            resp.raise_for_status()
            ids = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"モデル一覧を取得できませんでした（{type(e).__name__}）。モデル名は手入力してください。")
        return {"models": ids}

    @r.post("/settings/api/llm/test")
    async def llm_test(request: Request):
        """接続テスト: /models、短い chat、思考の混入、ツール呼び出し"""
        guard_write(request)
        b = await request.json()
        s = ss.load_settings(ROOT)
        ext = s["llm"]["external"]
        if b.get("mode", s["llm"]["mode"]) == "bundled":
            res = ss.resolve_llm(cfg(), s, ROOT)
            base, key, model = res["base_url"], "", res["model"]
            effort, extra = res["reasoning_effort"], res["extra_body"]
        else:
            base = (b.get("base_url") or ext["base_url"]).rstrip("/")
            key = b.get("api_key") or ext.get("api_key") or ""
            model = b.get("model") if b.get("model") is not None else ext["model"]
            reasoning = b.get("reasoning") or ext["reasoning"]
            effort, tmpl = {"effort": ("none", False), "template": (None, True), "both": ("none", True), "none": (None, False)}[reasoning]
            extra = {"chat_template_kwargs": {"enable_thinking": False}} if tmpl else None
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        temperature = float(b.get("temperature", s["llm"]["temperature"]))
        report = {"base_url": base, "steps": []}

        def step(name, ok, detail, ms=None):
            report["steps"].append({"name": name, "ok": ok, "detail": detail, "ms": ms})

        async with httpx.AsyncClient(timeout=120) as client:
            t = time.perf_counter()
            try:
                resp = await client.get(base + "/models", headers=headers)
                resp.raise_for_status()
                ids = [m.get("id") for m in resp.json().get("data", [])]
                step("接続（/v1/models）", True, f"モデル {len(ids)} 件: " + ", ".join(ids[:5]), int((time.perf_counter() - t) * 1000))
                if not model and ids:
                    model = ids[0]
            except Exception as e:  # noqa: BLE001
                step("接続（/v1/models）", False, f"{type(e).__name__}: {e}"[:200])
                return report

            body = {"model": model, "messages": [{"role": "user", "content": "短く挨拶してください。"}], "temperature": temperature, "max_tokens": 300}
            if effort:
                body["reasoning_effort"] = effort
            if extra:
                body.update(extra)
            t = time.perf_counter()
            try:
                resp = await client.post(base + "/chat/completions", json=body, headers=headers)
                ms = int((time.perf_counter() - t) * 1000)
                resp.raise_for_status()
                msg = resp.json()["choices"][0]["message"]
                content = (msg.get("content") or "").strip()
                reasoning_text = msg.get("reasoning_content") or msg.get("reasoning") or ""
                leaked = bool(reasoning_text) or bool(re.search(r"<think>|</think>|<\|channel\|>|^\s*\*\s*User", content))
                step("短い応答（chat/completions）", bool(content), f"「{content[:60]}」", ms)
                step("思考が混ざっていないか", not leaked,
                     "思考は出ていません。" if not leaked else "思考らしい出力があります。『思考を止める方法』を変えてみてください。")
            except Exception as e:  # noqa: BLE001
                detail = f"{type(e).__name__}: {e}"
                if isinstance(e, httpx.HTTPStatusError):
                    detail = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                step("短い応答（chat/completions）", False, detail[:300])
                return report

            tool_body = {
                "model": model, "temperature": 0, "max_tokens": 200,
                "messages": [{"role": "user", "content": "東京の今日の天気を get_weather で調べてください。"}],
                "tools": [{"type": "function", "function": {"name": "get_weather", "description": "天気を調べる",
                           "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}}],
            }
            if effort:
                tool_body["reasoning_effort"] = effort
            if extra:
                tool_body.update(extra)
            t = time.perf_counter()
            try:
                resp = await client.post(base + "/chat/completions", json=tool_body, headers=headers)
                ms = int((time.perf_counter() - t) * 1000)
                resp.raise_for_status()
                calls = resp.json()["choices"][0]["message"].get("tool_calls") or []
                step("ツール呼び出し", bool(calls), (f"{calls[0]['function']['name']}({calls[0]['function']['arguments'][:60]}) が呼ばれました。" if calls
                     else "ツールが呼ばれませんでした。天気や記憶のツールが使えない可能性があります（モデルか、サーバーの tool calling 設定を確認）。"), ms)
            except Exception as e:  # noqa: BLE001
                step("ツール呼び出し", False, f"{type(e).__name__}: {e}"[:300])
        return report

    # ---- キャラクター ----
    @r.get("/settings/api/characters/{cid}")
    async def get_character(cid: str):
        if not ss.valid_char_id(cid):
            raise HTTPException(400, "ID が不正です。")
        try:
            data = ss.load_character(cid, ROOT)
        except FileNotFoundError:
            raise HTTPException(404, "キャラクターが見つかりません。")
        folder = ss.characters_dir(ROOT) / cid
        data["images"] = sorted(f.stem for f in folder.glob("image*.png"))
        return data

    @r.put("/settings/api/characters/{cid}")
    async def put_character(cid: str, request: Request):
        guard_write(request)
        body = await request.json()
        try:
            ss.save_character(cid, body, ROOT)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "id": cid}

    @r.delete("/settings/api/characters/{cid}")
    async def delete_character(cid: str, request: Request):
        guard_write(request)
        if not ss.valid_char_id(cid):
            raise HTTPException(400, "ID が不正です。")
        if cid == ss.load_settings(ROOT).get("character"):
            raise HTTPException(400, "使用中のキャラクターは削除できません。先に別のキャラクターに切り替えてください。")
        shutil.rmtree(ss.characters_dir(ROOT) / cid, ignore_errors=True)
        return {"ok": True}

    @r.post("/settings/api/characters/{cid}/image")
    async def upload_image(cid: str, request: Request, file: UploadFile = File(...), state: str = Form("")):
        guard_write(request)
        if not ss.valid_char_id(cid) or not (ss.characters_dir(ROOT) / cid).exists():
            raise HTTPException(404, "キャラクターが見つかりません。")
        if state and state not in EXPRESSIONS:
            raise HTTPException(400, "表情の名前が不正です。")
        data = await file.read()
        if len(data) > MAX_IMAGE:
            raise HTTPException(400, "画像が大きすぎます（10MB まで）。")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(400, "PNG 画像だけアップロードできます（透過 PNG がおすすめです）。")
        name = f"image_{state}.png" if state else "image.png"
        (ss.characters_dir(ROOT) / cid / name).write_bytes(data)
        return {"ok": True, "file": name}

    @r.delete("/settings/api/characters/{cid}/image")
    async def delete_image(cid: str, request: Request, state: str = ""):
        guard_write(request)
        if not ss.valid_char_id(cid) or (state and state not in EXPRESSIONS):
            raise HTTPException(400, "不正です。")
        (ss.characters_dir(ROOT) / cid / (f"image_{state}.png" if state else "image.png")).unlink(missing_ok=True)
        return {"ok": True}

    # ---- 再起動 ----
    @r.post("/settings/api/restart")
    async def restart(request: Request):
        guard_write(request)
        body = await request.json()
        target = body.get("target", "all")
        if target not in ("llm", "all"):
            raise HTTPException(400, "target は llm か all です。")
        py = str(ROOT / "app" / ".venv" / "Scripts" / "python.exe")
        spawn_detached([py, str(ROOT / "ctl.py"), "restart", target])
        return {"ok": True, "message": "再起動しています。完了まで数十秒〜数分かかります。"}

    # ---- 立ち絵 ----
    @r.get("/character-image/{state}")
    async def character_image(state: str):
        s = ss.load_settings(ROOT)
        try:
            cid = ss.active_character(s, ROOT)["id"]
        except Exception:
            cid = None
        headers = {"Cache-Control": "no-store"}
        if state not in EXPRESSIONS:
            raise HTTPException(404)
        if cid:
            folder = ss.characters_dir(ROOT) / cid
            for name in (f"image_{state}.png", "image.png"):
                if (folder / name).exists():
                    return FileResponse(folder / name, media_type="image/png", headers=headers)
        return FileResponse(Path(__file__).parent / "placeholder" / "silhouette.svg", media_type="image/svg+xml", headers=headers)

    return r
