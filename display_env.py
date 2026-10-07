"""表示方法（デスクトップのキャラ / ブラウザ / 両方）の判定。

- 設定は config/settings.json の display.mode（mascot / browser / both）。既定は mascot。
- デスクトップのキャラは PySide6（Qt WebEngine。Chromium 同梱なので WebView2 などのランタイムは要らない）が必要。
  入っていない・Windows 10 1809 より古い・起動に失敗する PC では、設定にかかわらずブラウザ版にする。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODES = {"mascot": "デスクトップのキャラ", "browser": "ブラウザ（従来どおり）", "both": "両方"}
MIN_WINDOWS_BUILD = 17763        # Windows 10 1809（Qt 6 の下限）


def qt_installed(root: Path = ROOT) -> bool:
    """app の venv に、PySide6 の Qt WebEngine が入っているか"""
    pkg = root / "app" / ".venv" / "Lib" / "site-packages" / "PySide6"
    return (pkg / "QtWebEngineCore.pyd").exists() and (pkg / "QtWebEngineWidgets.pyd").exists()


def mascot_available(root: Path = ROOT) -> tuple[bool, str]:
    """(使えるか, 使えない理由)"""
    if os.environ.get("KOEMADO_MASCOT_FORCE_UNAVAILABLE") == "1":       # テスト用: 使えない PC のふりをする
        return False, "（テスト用の指定で、デスクトップのキャラを使えないことにしています）"
    if sys.platform != "win32":
        return False, "デスクトップのキャラは Windows 専用です。"
    if sys.getwindowsversion().build < MIN_WINDOWS_BUILD:
        return False, "Windows 10 1809 より古い OS では、デスクトップのキャラを使えません。"
    if not qt_installed(root):
        return False, "デスクトップのキャラの部品（PySide6 / Qt WebEngine）が入っていません。setup.bat を実行し直してください。"
    return True, ""


def effective_mode(settings: dict, root: Path = ROOT) -> tuple[str, str]:
    """(実際に使う表示方法, ブラウザ版にした理由)。理由は設定どおりのときは空"""
    mode = (settings.get("display") or {}).get("mode", "mascot")
    if mode not in MODES:
        mode = "mascot"
    if mode == "browser":
        return "browser", ""
    ok, why = mascot_available(root)
    if ok:
        return mode, ""
    return "browser", why
