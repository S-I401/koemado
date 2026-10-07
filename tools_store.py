"""ツール関連の設定（MCP・スキル）の読み書き。標準ライブラリだけで動く。

- config/mcp.json : Claude Desktop と同じ形式 {"mcpServers": {name: {command, args, env} | {url, headers}}}
                    各サーバーに enabled と allowed_tools（省略 = すべて許可）を足せる
- skills/<name>/SKILL.md : frontmatter（name, description）+ 本文（手順）
- 有効・無効の状態は config/settings.json の tools 節（skills_disabled など）
設定画面（settings_api）、configure.py、起動時の run.py が共有する。
"""
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,39}$")
MAX_SKILL_BYTES = 20000


# ------------------------------------------------------------------ MCP ----

def mcp_path(root: Path = ROOT) -> Path:
    return root / "config" / "mcp.json"


def valid_server_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def load_mcp(root: Path = ROOT) -> dict:
    """mcp.json を読む。無ければ空。壊れていれば ValueError（アプリは落とさず、呼び出し側で扱う）"""
    p = mcp_path(root)
    if not p.exists():
        return {"mcpServers": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ValueError(f"config/mcp.json が JSON として読めません: {e}")
    return normalize_mcp(data)


def normalize_mcp(data) -> dict:
    """検証して正規化する。Claude Desktop の設定（mcpServers だけを含むもの）もそのまま通す。
    問題があれば ValueError（日本語の理由）。"""
    if not isinstance(data, dict):
        raise ValueError("最上位は {\"mcpServers\": {...}} の形にしてください。")
    servers = data.get("mcpServers")
    if servers is None and data and all(isinstance(v, dict) for v in data.values()) and any(("command" in v or "url" in v) for v in data.values()):
        servers = data   # サーバー定義だけを貼り付けた場合も受け付ける
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise ValueError("mcpServers はサーバー名をキーにしたオブジェクトにしてください。")
    out = {}
    for name, s in servers.items():
        if not valid_server_name(name):
            raise ValueError(f"サーバー名「{name}」は半角英数字・_・- で 40 文字以内にしてください（ツール名の一部になります）。")
        if not isinstance(s, dict):
            raise ValueError(f"「{name}」の定義がオブジェクトではありません。")
        has_cmd, has_url = bool(s.get("command")), bool(s.get("url"))
        if has_cmd == has_url:
            raise ValueError(f"「{name}」には command（stdio）か url（HTTP）のどちらか一方を指定してください。")
        item = {}
        if has_cmd:
            if not isinstance(s["command"], str):
                raise ValueError(f"「{name}」の command は文字列にしてください。")
            args = s.get("args") or []
            if not isinstance(args, list) or not all(isinstance(a, (str, int, float)) for a in args):
                raise ValueError(f"「{name}」の args は文字列の配列にしてください。")
            env = s.get("env") or {}
            if not isinstance(env, dict):
                raise ValueError(f"「{name}」の env はオブジェクトにしてください。")
            item.update(command=s["command"], args=[str(a) for a in args], env={str(k): str(v) for k, v in env.items()})
            if s.get("cwd"):
                item["cwd"] = str(s["cwd"])
        else:
            url = str(s["url"])
            if not re.match(r"^https?://", url):
                raise ValueError(f"「{name}」の url は http:// か https:// で始めてください。")
            headers = s.get("headers") or {}
            if not isinstance(headers, dict):
                raise ValueError(f"「{name}」の headers はオブジェクトにしてください。")
            item.update(url=url, headers={str(k): str(v) for k, v in headers.items()})
        item["enabled"] = bool(s.get("enabled", True))
        allowed = s.get("allowed_tools")
        if allowed is not None:
            if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
                raise ValueError(f"「{name}」の allowed_tools は文字列の配列にしてください（省略するとすべて許可）。")
            item["allowed_tools"] = list(allowed)
        out[name] = item
    return {"mcpServers": out}


def save_mcp(data: dict, root: Path = ROOT) -> dict:
    clean = normalize_mcp(data)
    p = mcp_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return clean


def mask_secrets(data: dict) -> dict:
    """画面向け: env / headers の値を伏せる（キーや Authorization を含みうるため）。元の値を残す側で ●●●● を戻す"""
    out = json.loads(json.dumps(data))
    for s in out.get("mcpServers", {}).values():
        for k in ("env", "headers"):
            if s.get(k):
                s[k] = {kk: MASK for kk in s[k]}
    return out


MASK = "●●●●●●●●"


def unmask_secrets(new: dict, root: Path = ROOT) -> dict:
    """画面から戻ってきた JSON の ●●●● を、保存済みの値に戻す"""
    try:
        old = load_mcp(root)["mcpServers"]
    except ValueError:
        old = {}
    servers = new.get("mcpServers") if isinstance(new, dict) and isinstance(new.get("mcpServers"), dict) else None
    if servers is None:
        return new
    for name, s in servers.items():
        if not isinstance(s, dict):
            continue
        for k in ("env", "headers"):
            if isinstance(s.get(k), dict):
                for kk, vv in list(s[k].items()):
                    if vv == MASK:
                        prev = old.get(name, {}).get(k, {}).get(kk)
                        if prev is None:
                            raise ValueError(f"「{name}」の {k}.{kk} は伏せ字のままです。値を入力し直してください。")
                        s[k][kk] = prev
    return new


# ------------------------------------------------------------------ スキル ----

def skills_dir(root: Path = ROOT) -> Path:
    return root / "skills"


def parse_skill(text: str) -> tuple[dict, str]:
    """--- で囲まれた frontmatter（name: / description: の単純な行）と本文に分ける"""
    text = text.lstrip("﻿")
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$", line)
        if mm:
            v = mm.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            meta[mm.group(1)] = v
    return meta, m.group(2).strip()


def render_skill(name: str, description: str, body: str) -> str:
    description = " ".join(description.split())
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body.strip()}\n"


def list_skills(root: Path = ROOT, disabled: list | None = None) -> list[dict]:
    disabled = set(disabled or [])
    out = []
    d = skills_dir(root)
    if not d.exists():
        return out
    for sub in sorted(d.iterdir()):
        f = sub / "SKILL.md"
        if not (sub.is_dir() and f.exists()) or sub.name.startswith("_"):
            continue
        item = {"name": sub.name, "enabled": sub.name not in disabled}
        try:
            meta, body = parse_skill(f.read_text(encoding="utf-8"))
            item["description"] = meta.get("description", "")
            item["body_chars"] = len(body)
            if meta.get("name") and meta["name"] != sub.name:
                item["error"] = f"frontmatter の name（{meta['name']}）がフォルダ名と違います。"
            elif not item["description"]:
                item["error"] = "description がありません（一覧に出せません）。"
        except Exception as e:  # noqa: BLE001
            item["description"] = ""
            item["error"] = f"読み込めません: {e}"
        out.append(item)
    return out


def read_skill(name: str, root: Path = ROOT) -> dict:
    if not valid_server_name(name):
        raise ValueError("スキル名は半角英数字・_・- で 40 文字以内にしてください。")
    f = skills_dir(root) / name / "SKILL.md"
    meta, body = parse_skill(f.read_text(encoding="utf-8"))
    return {"name": name, "description": meta.get("description", ""), "body": body}


def save_skill(name: str, description: str, body: str, root: Path = ROOT):
    if not valid_server_name(name):
        raise ValueError("スキル名は半角英数字・_・- で 40 文字以内にしてください（フォルダ名になります）。")
    if not (description or "").strip():
        raise ValueError("description（いつ使うスキルか）を書いてください。一覧に出る一文で、LLM はこれを見て使うか決めます。")
    if not (body or "").strip():
        raise ValueError("本文（手順）を書いてください。")
    text = render_skill(name, description, body)
    if len(text.encode("utf-8")) > MAX_SKILL_BYTES:
        raise ValueError(f"スキルが大きすぎます（{MAX_SKILL_BYTES // 1000}KB まで）。")
    folder = skills_dir(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(text, encoding="utf-8")


def delete_skill(name: str, root: Path = ROOT):
    if not valid_server_name(name):
        raise ValueError("スキル名が不正です。")
    folder = skills_dir(root) / name
    if folder.exists() and (folder / "SKILL.md").exists():
        shutil.rmtree(folder)


def skill_index(root: Path = ROOT, disabled: list | None = None) -> list[dict]:
    """プロンプトに載せる（name, description）。有効で、エラーのないものだけ"""
    return [{"name": s["name"], "description": s["description"]} for s in list_skills(root, disabled)
            if s["enabled"] and not s.get("error")]
