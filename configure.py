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
import profiles  # noqa: E402
import settings_store as ss  # noqa: E402


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
        print(f"  キャラクター: {char.get('name')}（{char['id']}、{char.get('age')} 歳）  声: {ss.effective_voice(s, char)}")
    except Exception as e:
        print(f"  キャラクター: 読み込めません（{e}）")
    print(f"  num_steps: {s['tts']['num_steps']}  seed: {s['tts']['seed']}")
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
    if kind:
        print("※ LLM の方式・モデル・接続先を変えたら、start.bat を再実行（または stop.bat のあと start.bat）してください。")


def menu_character():
    chars = [c for c in ss.list_characters(ROOT) if not c.get("error")]
    s = ss.load_settings(ROOT)
    act = pick("操作", [("switch", "キャラクターを切り替える"), ("new", "新しいキャラクターを作る（成人のみ）"), ("edit", "いまのキャラクターを編集する")])
    if act == "switch":
        cid = pick("キャラクター", [(c["id"], f"{c['name']}（{c['id']}、{c['age']} 歳）") for c in chars])
        if cid:
            _apply({"character": cid})
            print("切り替えました。次の会話から反映されます。")
    elif act in ("new", "edit"):
        print("\n成人キャラクターのみ作れます。18 歳未満や、未成年を示す設定（少女・中学生など）は保存できません。")
        base = ss.active_character(s, ROOT) if act == "edit" else {}
        cid = base.get("id") or ask("ID（半角英小文字・数字・_・-）")
        data = {}
        for key, label in [("name", "名前"), ("age", "年齢（18 以上）"), ("first_person", "一人称"), ("second_person", "ユーザーの呼び方"),
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
        choice = pick("設定する項目", [("voice", "声"), ("llm", "LLM"), ("char", "キャラクター")])
        if choice == "voice":
            menu_voice()
        elif choice == "llm":
            menu_llm()
        elif choice == "char":
            menu_character()
        else:
            return


if __name__ == "__main__":
    main()
