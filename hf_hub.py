"""Hugging Face Hub の小さなクライアント（標準ライブラリだけ。ctl.py / 設定画面 / configure.py / setup.py が共有する）。

トークンの扱い（ここが唯一の送信経路）:
- Authorization ヘッダは、ホスト名が huggingface.co のときだけ付ける。
- リダイレクト先（CDN など）が huggingface.co 以外のときは、ヘッダを外してたどる（署名つき URL にトークンは要らない）。
- 例外メッセージ・戻り値にトークンの値を入れない。
"""
import json
import re
import struct
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://huggingface.co"
UA = "koemado"
TOKEN_RE = re.compile(r"^hf_[A-Za-z0-9]{8,200}$")
REPO_RE = re.compile(r"^[\w.\-]+/[\w.\-]+$")
GGUF_FILE_RE = re.compile(r"^[\w.\-/ ]+\.gguf$")


def valid_token(token: str) -> bool:
    return bool(TOKEN_RE.match(token or ""))


def is_hf_host(url: str) -> bool:
    return (urllib.parse.urlparse(url).hostname or "").lower() == "huggingface.co"


class HFError(Exception):
    """kind: invalid_token / gated / not_found / rate_limit / network / bad_response"""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """リダイレクト先が huggingface.co 以外なら Authorization を外す"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not is_hf_host(newurl):
            for k in list(new.headers):
                if k.lower() == "authorization":
                    del new.headers[k]
            for k in list(new.unredirected_hdrs):
                if k.lower() == "authorization":
                    del new.unredirected_hdrs[k]
        return new


_opener = urllib.request.build_opener(_SafeRedirect)


def make_request(url: str, token: str = "", headers: dict | None = None, method: str = "GET") -> urllib.request.Request:
    h = {"User-Agent": UA}
    h.update(headers or {})
    if token and is_hf_host(url):
        h["Authorization"] = "Bearer " + token
    return urllib.request.Request(url, headers=h, method=method)


def open_url(req: urllib.request.Request, timeout: float = 60):
    """ダウンロードなど、ストリームを読む用途。リダイレクトでトークンを外部に渡さない"""
    return _opener.open(req, timeout=timeout)


def _translate(e: Exception, what: str = "") -> HFError:
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        if code == 401:
            return HFError("invalid_token", "認証に失敗しました。トークンが未登録、無効、または期限切れです（ゲート付きのモデルは、HF のページで利用条件への同意も必要です）。", code)
        if code == 403:
            return HFError("gated", "このモデルは利用条件への同意が必要です。HF のページで同意してください。", code)
        if code == 404:
            return HFError("not_found", "見つかりませんでした（リポジトリ名が違う、または非公開です）。", code)
        if code == 429:
            return HFError("rate_limit", "Hugging Face のリクエスト上限に達しました。しばらく待ってからやり直してください。", code)
        return HFError("network", f"Hugging Face がエラーを返しました（HTTP {code}）。", code)
    if isinstance(e, HFError):
        return e
    return HFError("network", f"Hugging Face に接続できませんでした（{type(e).__name__}）。")


def get_json(url: str, token: str = "", timeout: float = 30):
    """(JSON, レスポンスヘッダ)。失敗は HFError"""
    try:
        with open_url(make_request(url, token), timeout) as r:
            body = r.read()
            return json.loads(body.decode("utf-8")), r.headers
    except HFError:
        raise
    except json.JSONDecodeError:
        raise HFError("bad_response", "Hugging Face の応答を読めませんでした。")
    except Exception as e:  # noqa: BLE001
        raise _translate(e)


def whoami(token: str) -> dict:
    """トークンの確認。{valid, name, role, error}。トークン本体は返さない"""
    if not valid_token(token):
        return {"valid": False, "error": "トークンの形式が違います（hf_ で始まる英数字です）。"}
    try:
        data, _ = get_json(API + "/api/whoami-v2", token, timeout=20)
    except HFError as e:
        if e.kind == "invalid_token":
            return {"valid": False, "error": "無効なトークンです（Hugging Face に拒否されました）。"}
        return {"valid": None, "error": e.message}
    tok = ((data.get("auth") or {}).get("accessToken") or {})
    return {"valid": True, "name": data.get("name") or "", "role": tok.get("role") or "", "token_name": tok.get("displayName") or ""}


# ---------------------------------------------------------------- 検索 ----

SORTS = {"downloads": "downloads", "likes": "likes", "lastModified": "lastModified"}
_EXPAND = ["downloads", "likes", "lastModified", "gated", "tags"]


def _license(tags: list[str]) -> str:
    for t in tags or []:
        if t.startswith("license:"):
            return t[8:]
    return ""


def _brief(m: dict) -> dict:
    tags = m.get("tags") or []
    return {"id": m.get("id") or m.get("modelId"), "downloads": m.get("downloads") or 0, "likes": m.get("likes") or 0,
            "updated": (m.get("lastModified") or "")[:10], "license": _license(tags), "gated": m.get("gated") or False}


def _search_url(q: str, sort: str, limit: int) -> str:
    params = [("filter", "gguf"), ("sort", SORTS.get(sort, "downloads")), ("direction", "-1"), ("limit", str(limit))]
    if q:
        params.insert(0, ("search", q))
    params += [("expand[]", x) for x in _EXPAND]
    return API + "/api/models?" + urllib.parse.urlencode(params)


def family_regex(families: list[dict]) -> re.Pattern:
    return re.compile("|".join(f["pattern"] for f in families), re.I)


def search(q: str, sort: str, page: int, limit: int, token: str, ja_only: bool, families: list[dict]) -> dict:
    """GGUF のあるモデルを探す。ja_only: 日本語に強いと言われる系列だけに絞る（families は profiles.JA_FAMILIES）"""
    q = (q or "").strip()
    sort = sort if sort in SORTS else "downloads"
    page = max(0, min(page, 9))
    want = (page + 1) * limit
    fam_re = family_regex(families) if ja_only else None
    items: list[dict] = []
    if ja_only and not q:
        terms = [f["term"] for f in families]
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(lambda t: get_json(_search_url(t, sort, min(100, want + 5)), token)[0], terms))
        seen = set()
        for res in results:
            for m in res:
                b = _brief(m)
                if b["id"] and b["id"] not in seen and fam_re.search(b["id"]):
                    seen.add(b["id"])
                    items.append(b)
        key = {"downloads": "downloads", "likes": "likes", "lastModified": "updated"}[sort]
        items.sort(key=lambda b: b[key], reverse=True)
        pass
    else:
        fetch = min(100, want * 3 + 1 if fam_re else want + 1)
        data, _ = get_json(_search_url(q, sort, fetch), token)
        for m in data:
            b = _brief(m)
            if b["id"] and (fam_re is None or fam_re.search(b["id"])):
                items.append(b)
    return {"items": items[page * limit:(page + 1) * limit], "page": page, "has_next": len(items) > want}


def repo_info(repo: str, token: str = "") -> dict:
    data, _ = get_json(f"{API}/api/models/{repo}?" + urllib.parse.urlencode([("expand[]", x) for x in _EXPAND + ["gguf"]]), token)
    b = _brief(data)
    g = data.get("gguf") or {}
    b["architecture"] = g.get("architecture") or ""
    b["context_length"] = g.get("context_length") or 0
    return b


# ---------------------------------------------------------------- ファイル一覧 ----

def _next_link(headers) -> str | None:
    link = headers.get("Link") or ""
    m = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
    return m.group(1) if m else None


def list_files(repo: str, token: str = "", max_pages: int = 5) -> list[dict]:
    """リポジトリの全ファイル（[{path, size, sha256}]）。LFS のサイズと sha256（oid）を優先する。sha256 は LFS のファイルだけ"""
    url = f"{API}/api/models/{repo}/tree/main?recursive=true"
    out = []
    for _ in range(max_pages):
        data, headers = get_json(url, token)
        for e in data:
            if e.get("type") == "file":
                out.append({"path": e["path"], "size": int((e.get("lfs") or {}).get("size") or e.get("size") or 0), "sha256": (e.get("lfs") or {}).get("oid") or ""})
        url = _next_link(headers)
        if not url or not is_hf_host(url):
            break
    return out


_SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})(?=\.gguf$)", re.I)
_AUX_RE = re.compile(r"mmproj|imatrix|(^|[/_\-.])mtp([/_\-.]|$)|vocab|draft", re.I)
_QUANT_RE = re.compile(
    r"(?i)(?:^|[-_.])((?:UD-)?(?:IQ\d(?:_[A-Z0-9]+)*|Q\d(?:_[A-Z0-9]+)*|MXFP4(?:_MOE)?|BF16|F16|F32))(?=$|[-_.])")


def quant_of(name: str) -> str:
    base = name.rsplit("/", 1)[-1][:-5] if name.lower().endswith(".gguf") else name
    m = _QUANT_RE.findall(base)
    return m[-1].upper() if m else ""


def group_gguf(files: list[dict]) -> tuple[list[dict], list[dict]]:
    """GGUF を『1 つのモデル』に束ねる。分割（-00001-of-0000N）は合計サイズで 1 件にし、mmproj などの補助は別に返す。
    戻り値: (本体の候補, 補助ファイル)"""
    groups: dict[str, dict] = {}
    aux = []
    for f in files:
        p = f["path"]
        if not p.lower().endswith(".gguf"):
            continue
        if _AUX_RE.search(p):
            aux.append(f)
            continue
        m = _SPLIT_RE.search(p)
        key = _SPLIT_RE.sub("", p) if m else p
        g = groups.setdefault(key, {"key": key, "quant": quant_of(key), "parts": {}, "expected": 1})
        g["parts"][p] = f["size"]
        if m:
            g["expected"] = int(m.group(2))
    out = []
    for g in groups.values():
        paths = sorted(g["parts"])
        complete = len(paths) == g["expected"]
        out.append({"key": g["key"], "quant": g["quant"], "files": paths, "size": sum(g["parts"].values()),
                    "parts": len(paths), "complete": complete})
    out.sort(key=lambda x: (x["size"], x["key"]))
    return out, aux


def file_url(repo: str, path: str, revision: str = "main") -> str:
    return f"{API}/{repo}/resolve/{revision}/{urllib.parse.quote(path)}"


def head_size(repo: str, path: str, token: str = "") -> int | None:
    try:
        with open_url(make_request(file_url(repo, path), token, method="HEAD"), 20) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except Exception:
        return None


def probe_access(repo: str, path: str, token: str = "") -> bool | None:
    """ゲート付きモデルの本体にアクセスできるか（True = できる / False = 利用条件への同意が未了 / None = 判定できない）"""
    try:
        with open_url(make_request(file_url(repo, path), token, {"Range": "bytes=0-0"}), 20):
            return True
    except urllib.error.HTTPError as e:
        return False if e.code in (401, 403) else None
    except Exception:
        return None


# ---------------------------------------------------------------- GGUF のメタデータ（先頭だけ読む） ----

class _Need(Exception):
    pass


class _Buf:
    def __init__(self, data: bytes):
        self.d, self.p = data, 0

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise _Need()
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack("<" + fmt, self.take(size))[0]


_SCALAR = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}


def _read_value(b: _Buf, t: int, keep: bool):
    if t in _SCALAR:
        return b.unpack(_SCALAR[t])
    if t == 8:
        n = b.unpack("Q")
        raw = b.take(n)
        return raw.decode("utf-8", "replace") if keep else None
    if t == 9:
        et = b.unpack("I")
        n = b.unpack("Q")
        if et in _SCALAR:
            b.take(struct.calcsize(_SCALAR[et]) * n)
            return None
        for _ in range(n):
            _read_value(b, et, False)
        return None
    raise ValueError("unknown gguf type")


WANT_SUFFIXES = (".block_count", ".expert_count", ".expert_used_count", ".context_length", ".embedding_length")


def parse_gguf_meta(data: bytes) -> dict:
    """先頭バイトから、アーキテクチャ・層数・専門家の数を読む。足りなければ _Need。tokenizer.* が出たら打ち切る"""
    b = _Buf(data)
    if b.take(4) != b"GGUF":
        raise ValueError("not gguf")
    version = b.unpack("I")
    if version < 2:
        raise ValueError("old gguf")
    b.unpack("Q")
    kv = b.unpack("Q")
    meta = {}
    for _ in range(kv):
        klen = b.unpack("Q")
        key = b.take(klen).decode("utf-8", "replace")
        t = b.unpack("I")
        if key.startswith("tokenizer."):
            break
        keep = key == "general.architecture" or key.endswith(WANT_SUFFIXES)
        v = _read_value(b, t, keep)
        if keep and v is not None:
            meta[key] = v
    arch = meta.get("general.architecture", "")
    out = {"architecture": arch}
    for s in ("block_count", "expert_count", "expert_used_count", "context_length", "embedding_length"):
        v = meta.get(f"{arch}.{s}")
        if isinstance(v, int):
            out[s] = v
    return out


def read_gguf_meta(repo: str, path: str, token: str = "") -> dict | None:
    """GGUF の先頭だけを取得してメタデータを読む。失敗したら None（呼び出し側は名前から推定する）"""
    for size in (256 * 1024, 2 * 1024 * 1024, 8 * 1024 * 1024):
        try:
            req = make_request(file_url(repo, path), token, {"Range": f"bytes=0-{size - 1}"})
            with open_url(req, 30) as r:
                data = r.read(size)
        except Exception:
            return None
        try:
            return parse_gguf_meta(data)
        except _Need:
            continue
        except Exception:
            return None
    return None


# ---------------------------------------------------------------- 種類の推定（名前から） ----

_MOE_NAME_RE = re.compile(r"(?i)(?:\b|[-_])(?:\d+(?:\.\d+)?B-A\d+(?:\.\d+)?B|\d+x\d+B|moe|a\d+b)(?:\b|[-_.])")


def guess_moe_from_name(name: str) -> bool:
    return bool(_MOE_NAME_RE.search(name))
