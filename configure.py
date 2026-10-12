"""設定の対話 CLI（設定画面 /settings が開けないときの予備）。configure.bat から呼ばれる。

声・LLM・キャラクターを、設定画面と同じファイル（config/settings.json, characters/）に書く。
反映: 声・キャラクター・temperature は次の発話から。LLM の方式・モデル・接続先は start.bat の再起動後。
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import hf_hub  # noqa: E402
import hf_dl  # noqa: E402
import profiles  # noqa: E402
import stt_engine  # noqa: E402
import settings_store as ss  # noqa: E402
import tools_store as ts  # noqa: E402


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default != "" else ""
    ans = input(f"{prompt}{suffix}: ").strip()
    return ans or default


def pick(title: str, options: list[tuple[str, str]]) -> str | None:
    print(f"\n{title}")
    for i, (_, label) in enumerate(options, 1):
        print(f"  {i}) {label}")
    ans = input("番号（空で戻る）: ").strip()
    if not ans.isdigit() or not 1 <= int(ans) <= len(options):
        return None
    return options[int(ans) - 1][0]


def show():
    s = ss.load_settings(ROOT)
    llm = ss.masked(s)["llm"]
    print("\n=== 現在の設定 ===")
    try:
        char = ss.active_character(s, ROOT)
        print(f"  キャラクター: {char.get('name')}（{char['id']}、{str(char.get('age')) + ' 歳' if char.get('age') is not None else '年齢未設定'}）  声: {ss.effective_voice(s, char)}")
    except Exception as e:
        print(f"  キャラクター: 読み込めません（{e}）")
    print(f"  表示方法: {ss.DISPLAY_MODES[s['display']['mode']]}")
    print(f"  num_steps: {s['tts']['num_steps']}  seed: {s['tts']['seed']}")
    st = s["stt"]
    print(f"  音声認識: エンジン {stt_engine.ENGINES[st['engine']].split('（')[0]} / モデル {st['model']} / デバイス {stt_engine.DEVICES[st['device']].split('（')[0]}" + (f" / よく使う言葉: {st['hints']}" if st['hints'] else ""))
    t = s["tools"]
    print(f"  Hugging Face トークン: {'登録済み' if s['hf'].get('token') else '未登録（任意）'}")
    onoff = lambda v: "オン" if v else "オフ"  # noqa: E731
    print(f"  ツール: Web検索 {onoff(t['web_search'])} / 天気 {onoff(t['weather'])} / 記憶 {onoff(t['memory'])} / スキル {onoff(t['skills'])} / 外部エージェント {onoff(t['agent']['enabled'])}")
    if llm["mode"] == "bundled":
        print(f"  LLM: 同梱 / プロファイル {llm['profile'] or '(導入時のもの)'} / temperature {llm['temperature']}")
    else:
        e = llm["external"]
        print(f"  LLM: 外部 {e['base_url']}  model={e['model'] or '(自動)'}  API キー: {'設定済み' if e['api_key_set'] else 'なし'}  思考停止: {e['reasoning']}")


def _apply(patch: dict):
    try:
        ss.update_settings(patch, ROOT)
        print("保存しました。")
    except (ValueError, KeyError) as e:
        print("エラー:", e)


def menu_voice():
    s = ss.load_settings(ROOT)
    voices = ss.list_voices(ROOT)
    if not voices:
        print("参照音声がありません（tts/voices/ に wav / flac / mp3 / ogg / opus を置いてください）。")
        return
    print("\n声の追加は、設定画面の「声」タブ（アップロード・Voice Design）か、tts/voices/ へファイルを置いてください。")
    print("※ 本人の同意がない実在人物（声優・有名人など）の声は使わないでください。")
    vid = pick("使う声を選んでください", [(v["id"], f"{v['id']}  {v.get('caption', '')}") for v in voices])
    if vid:
        char = ss.active_character(s, ROOT)
        cid = char.pop("id")
        char["voice"] = vid
        ss.save_character(cid, char, ROOT)
        _apply({"tts": {"voice": vid}})
        print(f"声を {vid} にしました。次の発話から変わります。")
    steps = ask("num_steps（4〜64。小さいほど速い）", str(s["tts"]["num_steps"]))
    try:
        _apply({"tts": {"num_steps": int(steps)}})
    except ValueError as e:
        print("エラー:", e)


def menu_llm():
    s = ss.load_settings(ROOT)
    kind = pick("LLM の方式", [("bundled", "同梱の llama-server"), ("external", "自分で用意した推論サーバー（OpenAI 互換）")])
    if kind == "bundled":
        opts = [(k, f"{k}: {p['label']}  {p['model']['size_bytes'] / profiles.GIB:.1f} GiB"
                    f"{'（ダウンロード済み）' if (ROOT / 'models' / p['model']['file']).exists() else '（未ダウンロード）'}")
                for k, p in profiles.PROFILES.items()]
        prof = pick("プロファイル", opts)
        patch = {"llm": {"mode": "bundled"}}
        if prof:
            patch["llm"]["profile"] = prof
            f = ROOT / "models" / profiles.PROFILES[prof]["model"]["file"]
            if not f.exists():
                print(f"※ モデルが未ダウンロードです。設定画面でダウンロードするか、setup.bat --profile {prof} を実行してください。")
        patch["llm"]["temperature"] = float(ask("temperature", str(s["llm"]["temperature"])))
        _apply(patch)
    elif kind == "external":
        preset = pick("プリセット", [(k, f"{v['label']}  {v['base_url']}") for k, v in ss.EXTERNAL_PRESETS.items()] + [("", "プリセットなし（URL を直接入力）")])
        pr = ss.EXTERNAL_PRESETS.get(preset or "", {})
        ext = s["llm"]["external"]
        url = ask("base_url（/v1 まで）", pr.get("base_url", ext["base_url"]))
        key = ask("API キー（任意。空なら変更なし）", "")
        models = []
        try:
            req = urllib.request.Request(url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key or ext.get('api_key', '')}"})
            models = [m["id"] for m in json.load(urllib.request.urlopen(req, timeout=8)).get("data", [])]
        except Exception as e:
            print(f"モデル一覧を取得できませんでした（{type(e).__name__}）。手入力してください。")
        if models:
            print("  候補:", ", ".join(models[:10]))
        model = ask("モデル名", ext["model"] or (models[0] if models else ""))
        reasoning = pick("思考を止める方法", list(ss.REASONING_MODES.items())) or pr.get("reasoning", ext["reasoning"])
        serialize = ask("LLM→TTS を直列化する？（同じ GPU ならはい、別 PC・クラウドならいいえ）y/n",
                        "y" if pr.get("serialize", ext["serialize"]) else "n").lower().startswith("y")
        tools = ask("ツール呼び出し（天気・記憶）を使う？ y/n", "y" if ext["tools"] else "n").lower().startswith("y")
        if ss.is_cloud_url(url):
            print("※ 会話の内容が外部のサービスに送られます。")
        _apply({"llm": {"mode": "external", "temperature": float(ask("temperature", str(s["llm"]["temperature"]))),
                        "external": {"preset": preset or "", "base_url": url, "api_key": key, "model": model, "reasoning": reasoning,
                                     "serialize": serialize, "tools": tools}}})
    if kind == "external":
        menu_strata_extras()
    if kind:
        print("※ LLM の方式・モデル・接続先を変えたら、start.bat を再実行（または stop.bat のあと start.bat）してください。")


def menu_hf():
    """Hugging Face のトークン（任意）。登録すると、設定画面のモデルブラウザで GGUF の一覧から選べる。ゲート付きモデルのダウンロードにも使う"""
    import getpass
    s = ss.load_settings(ROOT)
    is_set = bool(s["hf"].get("token"))
    print("\nHugging Face のトークン（任意）")
    print("  ・登録すると、設定画面（LLM タブ）で GGUF の一覧から自由にモデルを選べます。ゲート付きモデルのダウンロードにも使います。")
    print("  ・作り方: https://huggingface.co/settings/tokens で「Create new token」→ 種類は Read（読み取り）で足ります。")
    print("  ・トークンは huggingface.co にだけ送ります。config/settings.json に保存し、画面・ログ・不具合報告には出しません。")
    print(f"  ・いまの状態: {'登録済み（値は表示しません）' if is_set else '未登録'}")
    act = pick("操作", [("set", "登録・変更"), ("check", "確認（有効か、ユーザー名）"), ("del", "削除")])
    if act == "set":
        tok = getpass.getpass("トークン（入力は表示されません。hf_ で始まる文字列）: ").strip()
        if tok:
            _apply({"hf": {"token": tok}})
            r = hf_hub.whoami(tok)
            print("  確認:", f"有効です（ユーザー名: {r['name']}）" if r.get("valid") else f"{'無効です' if r.get('valid') is False else '確認できませんでした'}: {r.get('error', '')}")
    elif act == "check":
        tok = s["hf"].get("token") or ""
        if not tok:
            print("トークンが登録されていません。")
        else:
            r = hf_hub.whoami(tok)
            print("  ", f"有効です。ユーザー名: {r['name']}（権限: {r.get('role') or '?'}）" if r.get("valid") else f"{'無効です' if r.get('valid') is False else '確認できませんでした'}: {r.get('error', '')}")
    elif act == "del":
        if is_set and input("登録したトークンを削除しますか？ [y/N]: ").strip().lower() in ("y", "yes"):
            _apply({"hf": {"clear_token": True}})


def menu_strata_extras():
    """Strata の拒否の抑制（ESP）と、外部サーバーの画像認識"""
    s = ss.load_settings(ROOT)
    llm = s["llm"]
    if llm["external"].get("preset") == "strata" or llm["external"].get("managed") == "strata":
        print()
        print("拒否の抑制（Strata の experimental speed projection）")
        print("  ・名前と違って高速化ではありません。モデルが依頼をほとんど断らなくなります（安全上の振る舞いが外れます）。")
        print("  ・速度は 1 トークンあたり 0.2〜0.4% 遅くなる程度。出力の責任は利用者にあります。")
        print("  ・Qwen3.8-Flash-Next 専用（Swift 1.5・Coder 版では使えません）。ライセンスは Qwen Community License 1.0。")
        print("  ・使用中のキャラクターの年齢が 18 歳以上と設定されているときだけ、オンにできます（未設定・18 歳未満では使えません）。")
        print("  ・性的な内容や、未成年が関わる性的な話題には応じない、という共通ルールは、この設定と関係なく有効です。")
        if llm["external"].get("managed") == "strata":
            import ctl
            ok, why = ctl.esp_applicable(ctl.strata_info(ROOT))
            if not ok:
                print("  ・この Strata では使えません:", why)
        cur = bool(llm.get("esp", {}).get("enabled"))
        block = ss.esp_character_block(ss._character_for(s, ROOT))
        if block:
            print("  ・いまは使えません:", block)
            if cur:
                print("    （設定がオンのままなので、オフにします）")
                _apply({"llm": {"esp": {"enabled": False}}})
        ans = "" if block else input(f"拒否の抑制をオンにしますか？（いまは{'オン' if cur else 'オフ'}）y=オン / n=オフ / 空=変更しない: ").strip().lower()
        if ans in ("y", "yes"):
            ok2 = input("上の内容を理解したうえでオンにします。よろしいですか？ [y/N]: ").strip().lower() in ("y", "yes")
            if ok2:
                _apply({"llm": {"esp": {"enabled": True}}})
                print("※ start.bat が起動する Strata は、再起動したときに反映されます。")
        elif ans in ("n", "no"):
            _apply({"llm": {"esp": {"enabled": False}}})
    print()
    cur = bool(llm.get("vision", {}).get("enabled"))
    print("画像認識（会話画面に画像を送るパネルを出す。サーバー側で画像認識が有効な場合のみ使えます）")
    ans = input(f"画像認識をオンにしますか？（いまは{'オン' if cur else 'オフ'}）y=オン / n=オフ / 空=変更しない: ").strip().lower()
    if ans in ("y", "yes", "n", "no"):
        store = False
        if ans in ("y", "yes"):
            store = input("送った画像を会話の記録に残しますか？（既定: 残さない）y/N: ").strip().lower() in ("y", "yes")
        _apply({"llm": {"vision": {"enabled": ans in ("y", "yes"), "store_images": store}}})


def yesno(prompt: str, cur: bool) -> bool | None:
    """y / n / 空（変更しない）"""
    ans = input(f"{prompt}（いまは{'オン' if cur else 'オフ'}）y=オン / n=オフ / 空=変更しない: ").strip().lower()
    if ans in ("y", "yes"):
        return True
    if ans in ("n", "no"):
        return False
    return None


def menu_tools():
    s = ss.load_settings(ROOT)
    t = s["tools"]
    print("\nツールの変更は、アプリの再起動（start.bat の再実行）で反映されます。")
    patch = {}
    print("\nWeb 検索（web_search / web_fetch）: 検索語は外部（DuckDuckGo）に送られます。API キーは不要です。")
    for key, label in (("web_search", "Web 検索"), ("weather", "天気（Open-Meteo に地名を送ります）"), ("memory", "記憶（この PC の中だけ）"), ("skills", "スキル")):
        v = yesno(label, t[key])
        if v is not None:
            patch[key] = v
    # MCP
    print("\nMCP サーバー（config/mcp.json。JSON の編集は設定画面の「ツール」タブで行ってください）")
    print("  ・MCP のツールは、この PC で動くプログラムです。ファイル操作やコマンド実行ができるものもあり、音声の聞き間違いがそのまま実行される可能性があります。信頼できるものだけを有効にしてください。")
    print("  ・ツールが多いと、小さい LLM では呼び出しが不安定になります（目安: low は合計 8 個、mid は 15 個、大きいモデルでも 30 個まで）。")
    try:
        servers = ts.load_mcp(ROOT)["mcpServers"]
    except ValueError as e:
        servers = {}
        print("  config/mcp.json を読めません:", e)
    if not servers:
        print("  （サーバーはありません）")
    changed = False
    for name, c in servers.items():
        print(f"  - {name}: {'有効' if c['enabled'] else '無効'}  {'stdio ' + c['command'] if c.get('command') else 'HTTP ' + c['url']}"
              + (f"  使うツール: {', '.join(c['allowed_tools'])}" if c.get("allowed_tools") is not None else ""))
        v = yesno(f"    「{name}」を有効にする？", c["enabled"])
        if v is not None and v != c["enabled"]:
            c["enabled"] = v
            changed = True
    if changed:
        ts.save_mcp({"mcpServers": servers}, ROOT)
        print("  config/mcp.json を保存しました。")
    # 外部エージェント
    ag = t["agent"]
    print("\n外部エージェント（時間のかかる作業だけを別のエージェントに頼む。既定はオフ）")
    print("  ・Hermes の API は、ターミナルの実行を含む全ツールへのアクセスを与えます。127.0.0.1 で使い、API キーを必ず設定してください。Hermes 側の承認設定は緩めないでください。")
    print("  ・Hermes 本体のインストールはこのアプリの対象外です（https://hermes-agent.nousresearch.com/docs/ ）。")
    v = yesno("外部エージェントを使う", ag["enabled"])
    agp = {}
    if v is not None:
        agp["enabled"] = v
    if v or (v is None and ag["enabled"]):
        kind = pick("種類", list(ss.AGENT_KINDS.items()))
        if kind:
            agp["kind"] = kind
        agp["base_url"] = ask("base_url（/v1 まで）", ag["base_url"])
        key = ask("API キー（空なら変更なし。入力は画面に表示されます）", "")
        if key:
            agp["api_key"] = key
        agp["timeout"] = int(ask("タイムアウト（秒）", str(ag["timeout"])) or 600)
        if not ss.is_local_url(agp["base_url"]):
            print("※ 127.0.0.1 以外の宛先です。通信が暗号化されない相手に会話の内容を送ることになります。")
        if not (key or ag.get("api_key")):
            print("※ API キーが未設定です。キーなしで使うのはおすすめしません。")
    if agp:
        patch["agent"] = agp
    if patch:
        _apply({"tools": patch})
        print("※ 反映するには、start.bat を再実行（または stop.bat のあと start.bat）してください。")


def menu_display():
    import display_env
    s = ss.load_settings(ROOT)
    cur = s["display"]["mode"]
    mode = pick(f"表示方法（いまは「{ss.DISPLAY_MODES[cur]}」）", list(ss.DISPLAY_MODES.items()))
    if not mode:
        return
    patch = {"display": {"mode": mode}}
    ok, why = display_env.mascot_available(ROOT)
    if mode != "browser" and not ok:
        print(f"※ この PC ではデスクトップのキャラを使えないため、起動時はブラウザ版になります: {why}")
    if mode != "browser":
        m = s["mascot"]
        talk = pick(f"キャラへの話しかけ方（いまは「{ss.MASCOT_TALK[m['talk']]}」）", list(ss.MASCOT_TALK.items()))
        mp = {}
        if talk:
            mp["talk"] = talk
        if (talk or m["talk"]) == "hotkey":
            mp["hotkey"] = ask("ショートカットキー（例: ctrl+alt+space）", m["hotkey"])
        size = pick(f"キャラの大きさ（いまは「{ss.MASCOT_SIZES[m['size']]}」）", list(ss.MASCOT_SIZES.items()))
        if size:
            mp["size"] = size
        if mp:
            patch["mascot"] = mp
    _apply(patch)
    print("※ 反映するには、start.bat を再実行（または stop.bat のあと start.bat）してください。")


def menu_stt():
    """音声認識（speech.cpp / faster-whisper）。モデル・デバイス・エンジンを変えたら STT の再起動で反映される"""
    import threading
    import time
    s = ss.load_settings(ROOT)
    st = s["stt"]
    ok, why = stt_engine.speechcpp_usable(ROOT)
    print(f"\n音声認識: speech.cpp は{'使えます' if ok else '使えません（' + why + '）'} / faster-whisper は{'導入済み' if stt_engine.fw_installed(ROOT) else '未導入'}")
    patch = {}
    eng = pick(f"エンジン（いまは「{st['engine']}」）", list(stt_engine.ENGINES.items()))
    if eng:
        patch["engine"] = eng
    if (eng or st["engine"]) != "faster-whisper":
        key = pick(f"speech.cpp のモデル（いまは「{st['model']}」）", [(k, f"{m['label']} ・ {m['size_bytes'] / 1e6:.0f}MB ・ CER {m['cer']} ・ {'取得済み' if stt_engine.model_present(ROOT, k) else '未取得'} ・ {m['license']}") for k, m in stt_engine.MODELS.items()])
        if key:
            patch["model"] = key
        dev = pick(f"デバイス（いまは「{st['device']}」）", list(stt_engine.DEVICES.items()))
        if dev:
            patch["device"] = dev
        m = stt_engine.MODELS[key or st["model"]]
        if m["hint"]:
            h = ask("よく使う言葉（固有名詞のヒント。読点区切り。空欄で変えない）", "")
            if h:
                patch["hints"] = h
        if ok and not stt_engine.model_present(ROOT, key or st["model"]):
            if input(f"{m['file']}（{m['size_bytes'] / 1e6:.0f}MB）を Hugging Face から取得しますか？ [y/N]: ").strip().lower() in ("y", "yes"):
                status = {}
                job = hf_dl.Job(ROOT, m["repo"], [{"path": m["file"], "size": m["size_bytes"], "sha256": m["sha256"]}], stt_engine.models_dir(ROOT),
                                ss.hf_token(ROOT), status=status, log=lambda line: None, flags=hf_dl.CREATE_NO_WINDOW, revision=m["revision"])
                t = threading.Thread(target=job.run, daemon=True)
                t.start()
                while t.is_alive():
                    time.sleep(1)
                    print(f"\r  {status.get('done', 0) / 1e6:.0f} / {m['size_bytes'] / 1e6:.0f} MB", end="", flush=True)
                print()
                if status.get("status") != "done":
                    print("取得できませんでした:", status.get("error"))
                    return
    if patch:
        _apply({"stt": patch})
        print("※ 反映するには STT の再起動が必要です（起動中なら `python ctl.py restart stt`、または start.bat を再実行）。")


def menu_character():
    chars = [c for c in ss.list_characters(ROOT) if not c.get("error")]
    s = ss.load_settings(ROOT)
    act = pick("操作", [("switch", "キャラクターを切り替える"), ("new", "新しいキャラクターを作る"), ("edit", "いまのキャラクターを編集する")])
    if act == "switch":
        cid = pick("キャラクター", [(c["id"], f"{c['name']}（{c['id']}、{str(c['age']) + ' 歳' if c.get('age') is not None else '年齢未設定'}）") for c in chars])
        if cid:
            target = next(c for c in chars if c["id"] == cid)
            patch = {"character": cid}
            if s["llm"].get("esp", {}).get("enabled") and target.get("esp_block"):
                print("\n拒否の抑制（ESP）がオンです。", target["esp_block"])
                ans = input("ESP をオフにして切り替えますか？ y=オフにして切り替える / n=切り替えをやめる [n]: ").strip().lower()
                if ans not in ("y", "yes"):
                    print("切り替えをやめました。")
                    return
                patch["llm"] = {"esp": {"enabled": False}}
            _apply(patch)
            print("切り替えました。次の会話から反映されます。")
    elif act in ("new", "edit"):
        print("\n年齢は任意です（空欄でかまいません）。性的な内容や、未成年が関わる性的な話題には応じない、という共通ルールは、キャラクターの設定では変えられません。")
        print("拒否の抑制（ESP）は、年齢が 18 歳以上と設定されたキャラクターでだけ使えます。")
        base = ss.active_character(s, ROOT) if act == "edit" else {}
        cid = base.get("id") or ask("ID（半角英小文字・数字・_・-）")
        data = {}
        for key, label in [("name", "名前"), ("age", "年齢（任意）"), ("first_person", "一人称"), ("second_person", "ユーザーの呼び方"),
                           ("speech_style", "口調"), ("personality", "性格"), ("likes", "好きなもの"), ("dislikes", "苦手なもの"),
                           ("background", "生い立ち・経歴"), ("relationship", "ユーザーとの関係")]:
            data[key] = ask(label, str(base.get(key, "")))
        ex = ask("話し方の例文（| で区切って複数）", "|".join(base.get("examples", [])))
        data["examples"] = [x for x in ex.split("|") if x.strip()]
        data["voice"] = ask("使う声の ID（空なら既定）", base.get("voice", ""))
        try:
            ss.save_character(cid, data, ROOT)
            print(f"保存しました: characters/{cid}/character.yaml（画像は設定画面から追加できます）")
        except ValueError as e:
            print("保存できません:", e)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    while True:
        show()
        choice = pick("設定する項目", [("voice", "声"), ("llm", "LLM"), ("hf", "Hugging Face のトークン（モデルを一覧から選ぶ）"), ("char", "キャラクター"), ("stt", "音声認識（speech.cpp / faster-whisper）"), ("display", "表示方法（デスクトップのキャラ / ブラウザ）"), ("tools", "ツール（Web検索・MCP・外部エージェント）")])
        if choice == "voice":
            menu_voice()
        elif choice == "llm":
            menu_llm()
        elif choice == "hf":
            menu_hf()
        elif choice == "char":
            menu_character()
        elif choice == "stt":
            menu_stt()
        elif choice == "display":
            menu_display()
        elif choice == "tools":
            menu_tools()
        else:
            return


if __name__ == "__main__":
    main()
