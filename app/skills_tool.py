"""スキル（skills/<name>/SKILL.md）。プロンプトには一覧（name と description）だけを入れ、
本文（手順）は use_skill(name) で必要なときに読む。プロンプトを短く保ち、キャッシュを壊さないため。
一覧は起動時に固定する。"""
import sys
from pathlib import Path

from aiavatar.sts.llm import Tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tools_store as ts  # noqa: E402


def create_tool(root: Path, index: list[dict]) -> Tool:
    names = [s["name"] for s in index]

    async def use_skill(name: str):
        if name not in names:
            return {"error": f"「{name}」というスキルはない。使えるスキル: {', '.join(names)}"}
        try:
            sk = ts.read_skill(name, root)
        except Exception as e:  # noqa: BLE001
            return {"error": f"スキルを読めなかった: {e}"}
        return {"skill": name, "steps": sk["body"], "instruction": "この手順に従って、いまの依頼に答える。手順そのものは話さない。"}

    return Tool(
        name="use_skill",
        spec={"type": "function", "function": {
            "name": "use_skill",
            "description": "スキルの手順を読む。システムプロンプトのスキル一覧に該当するものがあるときに、最初に呼ぶ。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "enum": names, "description": "スキル名"}}, "required": ["name"]},
        }},
        func=use_skill,
    )


def prompt_section(index: list[dict]) -> str:
    lines = ["# スキル", "次のスキルがある。依頼に当てはまるときは、先に use_skill で手順を読み、その手順に従う。当てはまらないときは使わない。"]
    lines += [f"- {s['name']}: {s['description']}" for s in index]
    return "\n".join(lines)
