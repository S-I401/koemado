"""MCP の動作確認用の小さなサーバー（fastmcp）。サイコロを振る・今の時刻を返す・足し算の 3 つ。

  stdio（mcp.json から起動）: {"mcpServers": {"demo": {"command": "python", "args": ["tools/mcp_test_server.py"]}}}
  HTTP（streamable-http）   : app\.venv\Scripts\python.exe tools\mcp_test_server.py --http 8765
                              → {"mcpServers": {"demo": {"url": "http://127.0.0.1:8765/mcp"}}}
"""
import random
import sys
from datetime import datetime

from fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool
def roll_dice(sides: int = 6, count: int = 1) -> dict:
    """サイコロを振る。sides 面のサイコロを count 個。"""
    rolls = [random.randint(1, max(2, sides)) for _ in range(max(1, min(count, 20)))]
    return {"rolls": rolls, "total": sum(rolls)}


@mcp.tool
def current_time() -> str:
    """今の日付と時刻（この PC の時計）を返す。"""
    return datetime.now().strftime("%Y年%m月%d日 %H時%M分")


@mcp.tool
def add_numbers(a: float, b: float) -> float:
    """2 つの数を足す。"""
    return a + b


if __name__ == "__main__":
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1])
        mcp.run(transport="streamable-http", host="127.0.0.1", port=port)
    else:
        mcp.run()
