"""Web 検索（DuckDuckGo。API キー不要）とページ本文の取得。

- web_search(query): 上位数件のタイトル・URL・要約を返す。検索語は外部（DuckDuckGo）に送られる。
- web_fetch(url): ページ本文をテキストで返す。http / https のみ。ループバック・プライベート・リンクローカルなど、
  この PC や社内ネットワークの宛先は、リダイレクト先も含めて拒否する。サイズと時間に上限がある。
"""
import asyncio
import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

from aiavatar.sts.llm import Tool

logger = logging.getLogger(__name__)

MAX_RESULTS = 5
SNIPPET_CHARS = 160
FETCH_TIMEOUT = 10.0          # 秒（接続・読み込みごと）
FETCH_DEADLINE = 15.0         # 秒（全体）
MAX_BYTES = 1_000_000         # 本文として読む上限
MAX_TEXT_CHARS = 4000         # LLM に返す文字数の上限
MAX_REDIRECTS = 4
BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "dt", "dd", "blockquote", "pre"}
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class BlockedURL(ValueError):
    pass


def _ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip.sixtofour:
            ip = ip.sixtofour
    return not ip.is_global or ip.is_multicast


def check_url(url: str) -> tuple[str, list[str]]:
    """URL を検査する。許可なら (url, 解決した IP のリスト)。拒否なら BlockedURL（理由は日本語）。"""
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https"):
        raise BlockedURL("http / https の URL だけ取得できます。")
    host = u.hostname
    if not host:
        raise BlockedURL("URL にホスト名がありません。")
    if u.username or u.password:
        raise BlockedURL("認証情報つきの URL は取得できません。")
    low = host.lower().rstrip(".")
    if low == "localhost" or low.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")):
        raise BlockedURL("この PC や社内ネットワークの宛先は取得できません。")
    try:
        infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise BlockedURL("ホスト名を解決できませんでした。")
    ips = sorted({i[4][0] for i in infos})
    if not ips:
        raise BlockedURL("ホスト名を解決できませんでした。")
    for ip in ips:
        if _ip_blocked(ipaddress.ip_address(ip.split("%")[0])):
            raise BlockedURL("この PC や社内ネットワークの宛先は取得できません。")
    return u.geturl(), ips


def _extract_text(raw: bytes, content_type: str) -> tuple[str, str]:
    """(タイトル, 本文) を取り出す"""
    if content_type.startswith("text/plain"):
        text = raw.decode("utf-8", errors="replace")
        return "", re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
    import lxml.html
    try:
        doc = lxml.html.fromstring(raw)
    except Exception:
        return "", raw.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]
    title = " ".join((doc.findtext(".//title") or "").split())
    for bad in doc.xpath("//script|//style|//noscript|//template|//nav|//header|//footer|//aside|//form|//svg|//iframe"):
        try:
            bad.drop_tree()
        except Exception:  # noqa: BLE001  親ごと取り除かれた要素
            pass
    for el in doc.iter():
        if isinstance(el.tag, str) and el.tag.lower() in BLOCK_TAGS:     # 要素の切れ目に改行を入れる（語がくっつかないように）
            el.text = "\n" + (el.text or "")
            el.tail = "\n" + (el.tail or "")
    # 本文らしい要素があればそこだけ使う
    root = next(iter(doc.xpath("//article|//main")), doc)
    text = root.text_content()
    text = re.sub(r"[ \t　\xa0]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return title, text


async def fetch_text(url: str) -> dict:
    """http(s) のページ本文を取得する。上限を超えたら打ち切る。"""
    try:
        return await asyncio.wait_for(_fetch(url), timeout=FETCH_DEADLINE)
    except asyncio.TimeoutError:
        return {"error": "ページの取得に時間がかかりすぎたので打ち切った。"}


async def _fetch(url: str) -> dict:
    loop = asyncio.get_running_loop()
    cur = url
    timeout = httpx.Timeout(FETCH_TIMEOUT)
    # trust_env=False: 環境変数のプロキシ経由で、ここでの宛先検査をすり抜けないようにする
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                cur, ips = await loop.run_in_executor(None, check_url, cur)
            except BlockedURL as e:
                return {"error": f"取得できない宛先: {e}"}
            try:
                async with client.stream("GET", cur) as resp:
                    # 接続した先の IP も確認する（検査のあとに名前解決が変わる攻撃への備え）
                    stream = resp.extensions.get("network_stream")
                    peer = stream.get_extra_info("server_addr") if stream else None
                    if peer and _ip_blocked(ipaddress.ip_address(str(peer[0]).split("%")[0])):
                        return {"error": "取得できない宛先: この PC や社内ネットワークの宛先は取得できません。"}
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            return {"error": "リダイレクト先が不明だった。"}
                        cur = urljoin(cur, loc)
                        continue
                    if resp.status_code >= 400:
                        return {"error": f"ページを取得できなかった（HTTP {resp.status_code}）。"}
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype not in ALLOWED_TYPES:
                        return {"error": f"テキストのページではなかった（{ctype or '種類不明'}）。"}
                    buf = bytearray()
                    async for part in resp.aiter_bytes():
                        buf += part
                        if len(buf) >= MAX_BYTES:
                            break
                    truncated_bytes = len(buf) >= MAX_BYTES
            except httpx.HTTPError as e:
                return {"error": f"ページを取得できなかった（{type(e).__name__}）。"}
            title, text = await loop.run_in_executor(None, _extract_text, bytes(buf), ctype)
            out = {"url": cur, "title": title, "text": text[:MAX_TEXT_CHARS]}
            if len(text) > MAX_TEXT_CHARS or truncated_bytes:
                out["note"] = f"長いページなので、先頭の{MAX_TEXT_CHARS}文字だけ。"
            if not text:
                out["note"] = "本文を取り出せなかった。"
            return out
    return {"error": "リダイレクトが多すぎた。"}


def _search_sync(query: str, max_results: int) -> list[dict]:
    from ddgs import DDGS
    rows = DDGS(timeout=10).text(query, region="jp-jp", safesearch="moderate", max_results=max_results)
    out = []
    for r in rows or []:
        out.append({
            "title": (r.get("title") or "")[:100],
            "url": r.get("href") or r.get("url") or "",
            "snippet": " ".join((r.get("body") or "").split())[:SNIPPET_CHARS],
        })
    return out


async def search(query: str, max_results: int = MAX_RESULTS) -> dict:
    query = (query or "").strip()
    if not query:
        return {"error": "検索語が空だった。"}
    try:
        rows = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, _search_sync, query[:200], max_results), timeout=20)
    except asyncio.TimeoutError:
        return {"error": "検索に時間がかかりすぎた。"}
    except Exception as e:  # noqa: BLE001  ddgs は DDGSException など
        logger.warning("web_search failed: %s: %s", type(e).__name__, e)
        if "No results" in str(e):
            return {"results": [], "note": "該当する結果はなかった。"}
        return {"error": f"検索できなかった（{type(e).__name__}）。ネットワークを確認して。"}
    return {"results": rows} if rows else {"results": [], "note": "該当する結果はなかった。"}


def create_tools() -> list[Tool]:
    async def web_search(query: str):
        return await search(query)

    async def web_fetch(url: str):
        return await fetch_text(url)

    return [
        Tool(
            name="web_search",
            spec={"type": "function", "function": {
                "name": "web_search",
                "description": "Web で検索して、上位の結果（タイトル・URL・要約）を返す。最新の情報や、自分の知識では分からないことを調べるときだけ使う。",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string", "description": "検索語。短い語句で（例: 今日 ニュース 話題）"}},
                    "required": ["query"]},
            }},
            func=web_search,
        ),
        Tool(
            name="web_fetch",
            spec={"type": "function", "function": {
                "name": "web_fetch",
                "description": "web_search で見つけたページの本文をテキストで読む。URL を指定する。この PC や社内ネットワークのアドレスは読めない。",
                "parameters": {"type": "object", "properties": {
                    "url": {"type": "string", "description": "http または https の URL"}},
                    "required": ["url"]},
            }},
            func=web_fetch,
        ),
    ]
