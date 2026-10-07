"""MCP サーバーへの接続と、ツールの LLM への登録。

- config/mcp.json（Claude Desktop と同じ形式）のサーバーに、起動時に接続して、ツールを LLM に登録する。
- stdio は fastmcp の汎用 StdioTransport（command + args。Python 専用の StdioMCP は使わない）を MCPBase で包む。
  HTTP は AIAvatarKit の StreamableHttpMCP。
- 接続に失敗したサーバーは飛ばす（警告ログと、設定画面の状態表示）。アプリは落とさない。
- ツールの定義は起動時に固定する（会話の途中で変わると LLM のプロンプトキャッシュが効かなくなるため）。
  変えるときは設定画面の「保存して再起動」。
"""
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

from aiavatar.sts.llm.tools.mcp import MCPBase, StreamableHttpMCP

logger = logging.getLogger("mcp_manager")

CONNECT_TIMEOUT = 60.0     # npx の初回は、パッケージの取得に時間がかかる
TEST_TIMEOUT = 60.0
NODE_COMMANDS = {"npx", "node", "npm", "pnpm", "yarn", "bunx"}
NODE_HINT = "Node.js が見つかりません。npx を使う MCP サーバーには Node.js が必要です（https://nodejs.org/ から入れてください。このアプリは自動では入れません）。"


def _resolve_command(command: str) -> str:
    """実行ファイルを探す。見つからなければ ValueError（日本語の案内つき）"""
    base = Path(command).name.lower()
    stem = base[:-4] if base.endswith((".exe", ".cmd", ".bat")) else base
    found = shutil.which(command)
    if found:
        return command
    if stem in ("python", "python3", "py"):
        return sys.executable    # このアプリの Python（fastmcp が入っている）で代用する
    if stem in NODE_COMMANDS:
        raise ValueError(NODE_HINT)
    if stem in ("uvx", "uv"):
        raise ValueError("uv が見つかりません。uvx を使う MCP サーバーには uv が必要です（https://docs.astral.sh/uv/ ）。")
    raise ValueError(f"コマンド「{command}」が見つかりません。PATH にあるか、フルパスで指定してください。")


def make_transport(name: str, cfg: dict, root: Path):
    from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
    if cfg.get("command"):
        command = _resolve_command(cfg["command"])
        logs = root / "logs"
        logs.mkdir(exist_ok=True)
        return StdioTransport(
            command=command,
            args=list(cfg.get("args") or []),
            env=(cfg.get("env") or None),
            cwd=cfg.get("cwd") or str(root),     # 相対パスの引数は、インストール先を基準にする
            keep_alive=False,
            log_file=logs / f"mcp_{name}.log",
        )
    return StreamableHttpTransport(url=cfg["url"], headers=(cfg.get("headers") or None))


def make_mcp(name: str, cfg: dict, root: Path) -> MCPBase:
    from fastmcp import Client
    transport = make_transport(name, cfg, root)
    if cfg.get("url"):
        return StreamableHttpMCP(client=Client(transport))
    return MCPBase(client=Client(transport))


def explain_error(e: BaseException) -> str:
    """接続エラーを、利用者向けの短い一文にする"""
    if isinstance(e, ValueError):
        return str(e)
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return f"{int(CONNECT_TIMEOUT)} 秒以内に応答がありませんでした（初回の npx はダウンロードに時間がかかることがあります）。"
    if isinstance(e, ExceptionGroup) and e.exceptions:
        return explain_error(e.exceptions[0])
    msg = str(e) or type(e).__name__
    if isinstance(e, FileNotFoundError):
        return f"実行ファイルが見つかりません: {msg}"
    return f"{type(e).__name__}: {msg}"[:300]


async def test_server(name: str, cfg: dict, root: Path) -> dict:
    """接続して、ツール一覧を返す（設定画面の「接続テスト」）。一時的な接続で、終わったら閉じる"""
    try:
        mcp = make_mcp(name, cfg, root)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": explain_error(e)}
    try:
        tools = await asyncio.wait_for(mcp.initialize(), timeout=TEST_TIMEOUT)
        return {"ok": True, "tools": [{"name": t.name, "description": (t.spec["function"].get("description") or "")[:200]} for t in tools]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": explain_error(e)}
    finally:
        await _close(mcp)


async def _close(mcp: MCPBase):
    try:
        await asyncio.wait_for(mcp.close(), timeout=10)
    except Exception as e:  # noqa: BLE001
        logger.debug("MCP close: %s", e)


class McpManager:
    """起動時に一度だけ接続する。status は設定画面が読む（起動時点のもの）。"""

    def __init__(self, root: Path):
        self.root = root
        self.status: dict[str, dict] = {}     # name -> {state, tools, error}
        self._live: dict[str, MCPBase] = {}

    async def start(self, llm, servers: dict, reserved_names: set[str]):
        """enabled なサーバーに並行して接続し、許可されたツールを llm に登録する"""
        names = []
        for name, cfg in servers.items():
            if not cfg.get("enabled", True):
                self.status[name] = {"state": "disabled", "tools": [], "error": ""}
            else:
                names.append(name)
        results = await asyncio.gather(*[self._connect(n, servers[n]) for n in names])
        taken = set(reserved_names)
        for name, tools in zip(names, results):
            if tools is None:
                continue
            allowed = servers[name].get("allowed_tools")
            registered = []
            for t in tools:
                if allowed is not None and t.name not in allowed:
                    continue
                if t.name in taken:    # 同名のツールがあるときは、サーバー名を前につける
                    new = f"{name}__{t.name}"
                    t.name = new
                    t.spec["function"]["name"] = new
                taken.add(t.name)
                llm.add_tool(t)
                registered.append(t.name)
            self.status[name].update(tools=registered, available=[x.name for x in tools])
            logger.info("MCP %s: %d ツールを登録（%s）", name, len(registered), ", ".join(registered))

    async def _connect(self, name: str, cfg: dict):
        try:
            mcp = make_mcp(name, cfg, self.root)
            tools = await asyncio.wait_for(mcp.initialize(), timeout=CONNECT_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            err = explain_error(e)
            logger.warning("MCP サーバー「%s」に接続できませんでした（飛ばします）: %s", name, err)
            self.status[name] = {"state": "failed", "tools": [], "error": err}
            return None
        self._live[name] = mcp
        self.status[name] = {"state": "connected", "tools": [], "error": ""}
        return tools

    async def close(self):
        await asyncio.gather(*[_close(m) for m in self._live.values()], return_exceptions=True)
        self._live.clear()
