"""表示方法（デスクトップのキャラ / ブラウザ / 両方）を切り替える（display.bat・start.bat --mascot などが呼ぶ）。

  display.py mascot|browser|both   設定（config/settings.json）に保存する。起動中なら、その場で反映する
  display.py                       いまの設定を表示して、番号で選ばせる
  オプション: --quiet（メッセージを出さない）  --save-only（保存だけ。起動中でも反映しない）
終了コード: 0 = 保存した / 10 = 保存して、起動中の koemado にその場で反映した / 1 = エラー
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import ctl  # noqa: E402
import display_env  # noqa: E402
import settings_store as ss  # noqa: E402

MODES = ["mascot", "browser", "both"]


def apply_live(mode: str, prev: str) -> bool:
    """起動中の supervisor に、保存済みの表示方法をその場で反映してもらう。起動していなければ False"""
    info = ctl.supervisor_info()
    if not info:
        return False
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/display", method="POST",
                                 data=json.dumps({"mode": mode, "save": False, "prev": prev}).encode(),
                                 headers={"X-Console-Request": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True
    except Exception:
        return False


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    quiet = "--quiet" in argv
    save_only = "--save-only" in argv
    args = [a for a in argv[1:] if not a.startswith("--")]
    say = (lambda *a: None) if quiet else print
    cur = ss.load_settings(ROOT)["display"]["mode"]
    mode = args[0].lower() if args else None
    if mode is None:
        eff, why = display_env.effective_mode(ss.load_settings(ROOT), ROOT)
        print(f"いまの表示方法: {ss.DISPLAY_MODES[cur]}")
        if why:
            print(f"  ※ この PC ではデスクトップのキャラを使えないため、ブラウザ版になります: {why}")
        for i, m in enumerate(MODES, 1):
            print(f"  [{i}] {ss.DISPLAY_MODES[m]}{'（いま）' if m == cur else ''}")
        try:
            ans = input("番号を入力（空で変更しない）: ").strip()
        except EOFError:
            ans = ""
        if not ans.isdigit() or not 1 <= int(ans) <= len(MODES):
            print("変更しませんでした。")
            return 0
        mode = MODES[int(ans) - 1]
    if mode not in MODES:
        print("使い方: display.bat mascot|browser|both")
        return 1
    try:
        ss.update_settings({"display": {"mode": mode}}, ROOT)
    except Exception as e:  # noqa: BLE001
        print(f"保存できませんでした: {e}")
        return 1
    say(f"表示方法を「{ss.DISPLAY_MODES[mode]}」にしました。")
    if mode != "browser":
        ok, why = display_env.mascot_available(ROOT)
        if not ok:
            say(f"  ※ この PC ではデスクトップのキャラを使えないため、起動時はブラウザ版になります: {why}")
    if not save_only and apply_live(mode, cur):
        say("  起動中の koemado に、その場で反映しました。")
        return 10
    if not save_only:
        say("  起動していないので、次に start.bat で起動したときから反映されます。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
