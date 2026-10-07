"""モデルの選択（設定画面のモデルブラウザと、setup.py の対話モードが共有する。標準ライブラリだけ）。

- inspect_repo: リポジトリの GGUF を『1 つのモデル』に束ね、種類（dense / MoE）・サイズ・範囲との比較・注意を付ける
- pick_interactive / ask_token / resolve_spec: setup.py の対話モードと --model 用
"""
import getpass
import os
import re
from pathlib import Path

import hf_hub
import profiles


def inspect_repo(repo: str, token: str, rng: dict, mode: str, root: Path | None = None) -> dict:
    """設定画面の /hf/files の中身。失敗は hf_hub.HFError"""
    files = hf_hub.list_files(repo, token)
    try:
        info = hf_hub.repo_info(repo, token)
    except hf_hub.HFError:
        info = {"id": repo, "downloads": 0, "likes": 0, "updated": "", "license": "", "gated": False}
    groups, aux = hf_hub.group_gguf(files)
    if not groups:
        raise hf_hub.HFError("not_found", "このリポジトリには、使える GGUF（本体）がありません。", 404)
    probe = next((g for g in groups if g["complete"]), groups[0])
    meta = hf_hub.read_gguf_meta(repo, probe["files"][0], token)
    if meta and meta.get("block_count") is not None:
        kind = "moe" if (meta.get("expert_count") or 0) > 1 else "dense"
        source = "metadata"
    else:
        kind = "moe" if hf_hub.guess_moe_from_name(repo) else "dense"
        source = "name"
    layers = (meta or {}).get("block_count") if kind == "moe" else None
    access = hf_hub.probe_access(repo, probe["files"][0], token) if info.get("gated") else None
    by_path = {f["path"]: f for f in files}
    out = []
    for g in groups:
        gib = g["size"] / profiles.GIB
        a = profiles.assess_model(rng, gib, kind, layers, mode)
        if not g["complete"]:
            a = {"status": "unavailable", "reasons": ["分割ファイルの一部がリポジトリにありません"], "cautions": []}
        installed = bool(root) and all((Path(root) / "models" / repo.replace("/", "--") / p).exists() for p in g["files"])
        out.append({**g, "size_gib": gib, "installed": installed, **a, "items": [by_path[p] for p in g["files"]]})
    return {"repo": repo, "info": info, "kind": kind, "kind_source": source, "moe_layers": layers, "meta": meta, "access": access,
            "page": f"https://huggingface.co/{repo}", "range": rng, "mode": mode, "groups": out, "aux_count": len(aux)}


def choice_from(data: dict, g: dict) -> dict:
    """setup.py と settings.json に渡す、選んだモデルの情報"""
    return {"repo": data["repo"], "key": g["key"], "quant": g["quant"], "files": [dict(x) for x in g["items"]],
            "size_bytes": g["size"], "kind": data["kind"], "moe_layers": int(data.get("moe_layers") or 0), "page": data["page"]}


def custom_settings(choice: dict) -> dict:
    """config/settings.json の llm.custom"""
    files = sorted(f["path"] for f in choice["files"])
    return {"source": "hf", "repo": choice["repo"], "file": files[0], "path": "", "files": files,
            "size_bytes": choice["size_bytes"], "kind": choice["kind"], "moe_layers": choice["moe_layers"]}


def resolve_spec(spec: str, token: str, rng: dict, mode: str) -> dict:
    """`repo:file`（--model）から選択を作る。範囲外・見つからないときは ValueError"""
    repo, _, file = spec.partition(":")
    if not hf_hub.REPO_RE.match(repo) or not file:
        raise ValueError("--model は <owner/name>:<ファイル名.gguf> の形で指定してください。")
    try:
        data = inspect_repo(repo, token, rng, mode)
    except hf_hub.HFError as e:
        raise ValueError(f"{repo}: {e.message}")
    g = next((x for x in data["groups"] if file in x["files"] or file == x["key"] or any(Path(p).name == Path(file).name for p in x["files"])), None)
    if g is None:
        raise ValueError(f"{repo} に {file} が見つかりません。")
    if g["status"] != "ok":
        raise ValueError(f"{repo}:{file} は選べません。{(g['reasons'] or ['このモデルは使えません。'])[0]}")
    return choice_from(data, g)


# ---------------------------------------------------------------- 対話 ----

def _ask(prompt: str) -> str | None:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None


def ask_token(say, env_ok: bool = False, allow_save: bool = True) -> tuple[str, bool]:
    """トークンを決める。戻り値: (トークン, 設定に保存するか)。取り消しは ("", False)。
    環境変数 HF_TOKEN があれば、使ってよいか聞く（env_ok=True なら聞かずに使う）。入力は getpass で、画面に表示しない"""
    env = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    if env and hf_hub.valid_token(env):
        use = env_ok
        if not env_ok:
            a = _ask("環境変数 HF_TOKEN のトークンを使いますか？ [Y/n]: ")
            use = a is not None and a.lower() not in ("n", "no")
        if use:
            r = hf_hub.whoami(env)
            if r.get("valid"):
                say(f"  トークンは有効です。ユーザー名: {r['name']}（環境変数のトークンは、この実行だけで使い、設定には保存しません）")
                return env, False
            say("  環境変数のトークンは使えませんでした: " + (r.get("error") or ""))
    elif env_ok:
        say("  ※ 環境変数 HF_TOKEN が無い、または形式が違います。")
    while True:
        try:
            tok = getpass.getpass("Hugging Face のトークン（入力は表示されません。空で取り消し）: ").strip()
        except (EOFError, KeyboardInterrupt):
            return "", False
        if not tok:
            return "", False
        r = hf_hub.whoami(tok)
        if r.get("valid"):
            say(f"  有効です。ユーザー名: {r['name']}" + (f"（権限: {r['role']}）" if r.get("role") else ""))
            save = False
            if allow_save:
                a = _ask("  このトークンを config/settings.json に保存しますか？（設定画面でも使えます） [Y/n]: ")
                save = a is not None and a.lower() not in ("n", "no")
            return tok, save
        if r.get("valid") is False:
            say("  ✘ " + (r.get("error") or "無効なトークンです。") + " もう一度入力してください。")
        else:
            say("  ✘ 確認できませんでした: " + (r.get("error") or "") + "（ネットワークを確認して、もう一度入力してください）")


def _fmt_dl(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


def pick_interactive(token: str, rng: dict, mode: str, say, allow_no_token: bool = False) -> dict | None:
    """検索 → リポジトリ → GGUF の一覧 → 選ぶ。戻り値: choice（取り消しは None）。範囲外は選べない"""
    if not token and not allow_no_token:
        say("  トークンが無いので、一覧からは選べません。")
        return None
    page, q = 0, ""
    while True:
        a = _ask("\n検索語（モデル名など。空で人気順、b で戻る）: ")
        if a is None or a.lower() == "b":
            return None
        q, page = a, 0
        while True:
            try:
                r = hf_hub.search(q, "downloads", page, 10, token, False, profiles.JA_FAMILIES)
            except hf_hub.HFError as e:
                say("  ✘ " + e.message)
                break
            items = r["items"]
            if not items:
                say("  見つかりませんでした。")
                break
            say(f"\n  候補（ダウンロード数順。ページ {page + 1}）")
            for i, m in enumerate(items, 1):
                say(f"  {i:>2}) {m['id']}   DL {_fmt_dl(m['downloads'])} / ♥ {_fmt_dl(m['likes'])} / 更新 {m['updated']} / {m['license'] or 'ライセンス不明'}"
                    + (" / ゲート付き" if m["gated"] else ""))
            a = _ask("  番号で選ぶ（n=次のページ、p=前のページ、s=検索し直す、b=戻る）: ")
            if a is None or a.lower() == "b":
                return None
            if a.lower() == "s":
                break
            if a.lower() == "n" and r["has_next"]:
                page += 1
                continue
            if a.lower() == "p" and page > 0:
                page -= 1
                continue
            if not a.isdigit() or not 1 <= int(a) <= len(items):
                continue
            repo = items[int(a) - 1]["id"]
            choice = _pick_file(repo, token, rng, mode, say)
            if choice:
                return choice


def _pick_file(repo: str, token: str, rng: dict, mode: str, say) -> dict | None:
    say(f"\n  {repo} を調べています…（GGUF の先頭を読んで、MoE かどうかも確かめます）")
    try:
        d = inspect_repo(repo, token, rng, mode)
    except hf_hub.HFError as e:
        say("  ✘ " + e.message)
        return None
    i = d["info"]
    layers = d["moe_layers"]
    kind_txt = ("MoE" + (f"（全 {layers} 層）" if layers else "")) if d["kind"] == "moe" else "dense"
    src_txt = "メタデータから" if d["kind_source"] == "metadata" else "名前からの推定"
    say(f"  {repo}: DL {_fmt_dl(i['downloads'])} / ♥ {_fmt_dl(i['likes'])} / 更新 {i['updated']} / ライセンス {i['license'] or '不明'} / ゲート: {'あり' if i['gated'] else 'なし'}"
        f" / 種類: {kind_txt}（{src_txt}）")
    if i["gated"] and d["access"] is False:
        say(f"  ✘ このモデルはゲート付きで、まだ利用条件に同意していません。HF のページで利用条件に同意してください: {d['page']}")
    say(f"  選べるサイズ: {d['range']['min_gib']:.0f}〜{d['range']['max_gib']:.0f} GiB（プロファイル {d['range']['profile']}）")
    for n, g in enumerate(d["groups"], 1):
        mark = "  " if g["status"] == "ok" else "× "
        say(f"  {mark}{n:>2}) {g['quant'] or '?':<12}{g['size_gib']:>7.1f} GiB{'（' + str(g['parts']) + ' 分割）' if g['parts'] > 1 else ''}"
            + ("   範囲内" if g["status"] == "ok" else "   選べません: " + "; ".join(g["reasons"])))
        for c in g["cautions"]:
            say(f"           {'注意: ' if c['level'] == 'warn' else ''}{c['text']}")
    while True:
        a = _ask("  番号で選ぶ（b=戻る）: ")
        if a is None or a.lower() == "b":
            return None
        if not a.isdigit() or not 1 <= int(a) <= len(d["groups"]):
            continue
        g = d["groups"][int(a) - 1]
        if g["status"] != "ok":
            say("  ✘ 選べません。" + "; ".join(g["reasons"]))
            continue
        if i["gated"] and d["access"] is False:
            say(f"  ✘ ゲート付きで未承認です。{d['page']} で利用条件に同意してから、もう一度選んでください。")
            continue
        return choice_from(d, g)


def manual_interactive(token: str, rng: dict, mode: str, say) -> dict | None:
    """リポジトリとファイル名の手入力（トークンは任意）"""
    while True:
        repo = _ask("リポジトリ（owner/name。空で戻る）: ")
        if not repo:
            return None
        file = _ask("ファイル名（.gguf。分割ファイルはどれか 1 つ）: ")
        if not file:
            return None
        try:
            return resolve_spec(f"{repo}:{file}", token, rng, mode)
        except ValueError as e:
            say("  ✘ " + str(e))
