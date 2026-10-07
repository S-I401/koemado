"""koemado のデスクトップのキャラ（マスコット）。

枠なし・背景透明・常に最前面のウィンドウに、会話画面と同じ仕組み（mascot.html）を表示する。
  - 表示は PySide6 の Qt WebEngine（Chromium 同梱。WebView2 などの別途のランタイムは不要）。背景は本当の透明（半透明も正しく混ざる）。
  - 透明な部分はクリックが下のウィンドウに通る（キャラ・吹き出し・入力欄の形だけをクリックできる領域にする。ページが形を知らせる）。
  - マイクの許可（getUserMedia）は、127.0.0.1 のページに限って自動で許可する。
  - 移動・大きさ・位置の保存と復元、右クリックのメニュー、ショートカットキー、トレイからの表示／隠す。
  - supervisor が起動・監視する（ログは管理画面の「キャラ」）。起動直後は、アプリの準備ができるまで「起動中…」の吹き出しを出す。

  python.exe mascot.py            通常（supervisor が呼ぶ）
  python.exe mascot.py --test-page URL   指定の URL を開く（動作確認用）
終了コード: 0 = 終了 / 3 = 使えない（PySide6 が無いなど。supervisor がブラウザ版にする）
"""
import argparse
import concurrent.futures
import ctypes
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import display_env  # noqa: E402
import settings_store as ss  # noqa: E402

RUN_DIR = ROOT / "run"
INFO_FILE = RUN_DIR / "mascot.json"
SUPERVISOR_FILE = RUN_DIR / "supervisor.json"
SIZES = {"small": 200, "medium": 320, "large": 460}
ZONE_H = 190
EXIT_UNAVAILABLE = 3
DEBUG = os.environ.get("KOEMADO_MASCOT_DEBUG") == "1"       # テスト用: /eval で、ページの JS を外から実行できる


def log(msg: str):
    print(msg, flush=True)


# ------------------------------------------------------------------ supervisor との連絡 ----

def console_port() -> int | None:
    try:
        return int(json.loads(SUPERVISOR_FILE.read_text(encoding="utf-8"))["port"])
    except Exception:
        return None


def sup_get(path: str, timeout: float = 3.0):
    port = console_port()
    if not port:
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def sup_post(path: str, body: dict | None = None, timeout: float = 5.0) -> bool:
    port = console_port()
    if not port:
        return False
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=json.dumps(body or {}).encode(),
                                 headers={"X-Console-Request": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


# ------------------------------------------------------------------ ショートカットキー ----

MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
NAMED = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E}


def parse_hotkey(spec: str) -> tuple[int, int]:
    parts = spec.lower().replace(" ", "").split("+")
    mods = 0
    for p in parts[:-1]:
        mods |= MODS[p]
    k = parts[-1]
    if k in NAMED:
        vk = NAMED[k]
    elif re.fullmatch(r"f(\d+)", k):
        vk = 0x70 + int(k[1:]) - 1
    else:
        vk = ord(k.upper())
    return mods, vk


class Hotkey:
    """RegisterHotKey のスレッド。押されたら callback()。登録できなければ on_fail(理由)"""

    def __init__(self, callback, on_fail):
        self.callback, self.on_fail = callback, on_fail
        self.thread = None
        self.tid = None
        self.spec = None
        self.lock = threading.Lock()

    def start(self, spec: str):
        with self.lock:
            self._stop()
            self.spec = spec
            done = threading.Event()
            self.thread = threading.Thread(target=self._loop, args=(spec, done), daemon=True, name="hotkey")
            self.thread.start()
            done.wait(2)

    def stop(self):
        with self.lock:
            self._stop()

    def _stop(self):
        if self.thread and self.tid:
            ctypes.windll.user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)    # WM_QUIT
            self.thread.join(1)
        self.thread, self.tid, self.spec = None, None, None

    def _loop(self, spec, done):
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        self.tid = k.GetCurrentThreadId()
        try:
            mods, vk = parse_hotkey(spec)
            if not u.RegisterHotKey(None, 1, mods | 0x4000, vk):       # MOD_NOREPEAT
                self.on_fail(f"ショートカットキー {spec} を登録できませんでした（ほかのアプリが使っています）。設定画面で別のキーにしてください。")
                done.set()
                return
            log(f"ショートカットキー {spec} を登録しました")
            done.set()
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:      # WM_HOTKEY
                    try:
                        self.callback()
                    except Exception as e:  # noqa: BLE001
                        log(f"ショートカットキーの処理でエラー: {e}")
            u.UnregisterHotKey(None, 1)
        except Exception as e:  # noqa: BLE001
            self.on_fail(f"ショートカットキーを登録できませんでした: {e}")
            done.set()


# ------------------------------------------------------------------ 起動中の画面 ----

BOOT_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:transparent;overflow:hidden;user-select:none;font-family:'Yu Gothic UI','Meiryo UI',sans-serif}
#root{position:fixed;inset:0;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;cursor:grab}
#bubble{max-width:290px;box-sizing:border-box;background:#fff;color:#222;border:2px solid #3b6fe0;border-radius:14px;padding:8px 14px;font-size:14px;line-height:1.5;margin-bottom:10px;white-space:pre-wrap;overflow-wrap:anywhere}
#bubble.err{border-color:#e5533b}
svg{display:block}
</style></head><body><div id="root"><div id="bubble">起動中…</div>
<svg id="icon" width="120" height="120" viewBox="0 0 120 120"><circle cx="60" cy="60" r="56" fill="#3b6fe0"/><rect x="22" y="26" width="76" height="62" rx="14" fill="#f4f6fa"/>
<line x1="60" y1="26" x2="60" y2="88" stroke="#3b6fe0" stroke-width="3"/><line x1="22" y1="57" x2="98" y2="57" stroke="#3b6fe0" stroke-width="3"/><circle cx="60" cy="57" r="9" fill="#e05a3b"/></svg>
</div><script>
var py=null;
function rects(){var o=[];['bubble','icon'].forEach(function(id){var r=document.getElementById(id).getBoundingClientRect();o.push([Math.floor(r.left)-4,Math.floor(r.top)-4,Math.ceil(r.width)+8,Math.ceil(r.height)+8])});return o}
function pub(){if(py)py.set_regions(JSON.stringify(rects()))}
function setStatus(t,err){var b=document.getElementById('bubble');b.textContent=t;b.className=err?'err':'';pub()}
window.__kbReady.then(function(){py=window.__kb;pub();setInterval(pub,1000)});
var r=document.getElementById('root');
r.addEventListener('pointerdown',function(e){if(e.button!==0||!py)return;var x=e.clientX,y=e.clientY;
 function mv(ev){if(Math.hypot(ev.clientX-x,ev.clientY-y)>6){r.removeEventListener('pointermove',mv);py.drag()}}
 r.addEventListener('pointermove',mv);r.addEventListener('pointerup',function(){r.removeEventListener('pointermove',mv)},{once:true})});
document.addEventListener('contextmenu',function(e){e.preventDefault();if(py)py.menu()});
</script></body></html>"""


def single_instance():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    from ctypes import wintypes
    k.CreateMutexW.restype = wintypes.HANDLE
    name = "Local\\koemado-mascot-" + re.sub(r"[^A-Za-z0-9]", "_", str(ROOT))[-80:]
    h = k.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:
        return None
    return h


# ------------------------------------------------------------------ 本体 ----

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-page", default=None)
    args = ap.parse_args()

    t_start = time.time()
    ok, why = display_env.mascot_available(ROOT)
    if not ok:
        log(f"デスクトップのキャラは使えません: {why}")
        return EXIT_UNAVAILABLE
    mutex = single_instance()
    if mutex is None:
        log("すでに起動しています。")
        return 0

    # Chromium への指定: 話しかける前でも声を出せる（自動再生の許可）／「隠れたウィンドウ」扱いで処理が止まらないようにする
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = ("--autoplay-policy=no-user-gesture-required --disable-renderer-backgrounding "
                                                "--disable-background-timer-throttling --disable-backgrounding-occluded-windows")
    extra = RUN_DIR / "mascot-extra-flags.txt"          # テスト用（KOEMADO_MASCOT_DEBUG=1 のときだけ）: 疑似マイク（--use-fake-device-for-media-stream など）を渡す
    if DEBUG and extra.exists():
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] += " " + extra.read_text(encoding="utf-8").strip()
        log(f"テスト用の Chromium の指定: {extra.read_text(encoding='utf-8').strip()}")
    try:
        from PySide6.QtCore import QFile, QIODevice, QObject, QPoint, QRect, Qt, QTimer, QUrl, Signal, Slot
        from PySide6.QtGui import QAction, QCursor, QGuiApplication, QRegion
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEnginePermission, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QApplication, QMenu
    except Exception as e:  # noqa: BLE001
        log(f"PySide6（Qt WebEngine）を読み込めません: {type(e).__name__}: {e}")
        return EXIT_UNAVAILABLE

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("koemado-mascot")

    class Gui(QObject):
        """他のスレッドから GUI スレッドで関数を動かすための橋"""
        call = Signal(object)

        def __init__(self):
            super().__init__()
            self.call.connect(self._run)

        @Slot(object)
        def _run(self, fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log(f"GUI 処理でエラー: {type(e).__name__}: {e}")

        def run(self, fn):
            self.call.emit(fn)

        def run_wait(self, fn, timeout=30):
            fut = concurrent.futures.Future()

            def go():
                try:
                    fut.set_result(fn())
                except Exception as e:  # noqa: BLE001
                    fut.set_exception(e)
            self.call.emit(go)
            return fut.result(timeout)

    gui = Gui()

    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line, source):
            lv = QWebEnginePage.JavaScriptConsoleMessageLevel
            if level != lv.InfoMessageLevel:
                log(f"[js {'warn' if level == lv.WarningMessageLevel else 'error'}] {message} ({source.rsplit('/', 1)[-1]}:{line})")

        def acceptNavigationRequest(self, url, nav_type, is_main):
            if url.scheme() in ("about", "data", "qrc") or (url.scheme() == "http" and url.host() == "127.0.0.1"):
                return True
            log(f"外部への移動は止めました: {url.toString()[:80]}")
            return False

        def createWindow(self, wtype):
            return None

    class View(QWebEngineView):
        def moveEvent(self, e):
            super().moveEvent(e)
            if not mascot.programmatic and self.isVisible():
                move_timer.start(600)               # 動かし終わってから位置を保存する

        def closeEvent(self, e):
            e.ignore()                              # ×・Alt+F4 では終了せず、トレイへ隠す
            mascot.hide()

    class Bridge(QObject):
        """ページ（JS）から呼ばれる関数（window.__kb）。GUI スレッドで動く"""

        def __init__(self, m):
            super().__init__()
            self.m = m

        @Slot(str)
        def log(self, msg):
            log(f"[page] {msg}")

        @Slot(result=str)
        def get_config(self):
            c = self.m.cfg
            return json.dumps({k: c.get(k) for k in ("size", "talk", "hotkey", "interrupt", "volume", "bubble_seconds")})

        @Slot()
        def ready(self):
            log("キャラのページが開きました。")
            self.m.apply_hotkey()

        @Slot(int, int)
        def resize(self, w, h):
            m = self.m
            m.content = (int(w), int(h))
            m.apply_geometry()

        @Slot(str)
        def set_regions(self, js):
            try:
                regs = [[int(a) for a in r] for r in json.loads(js)]
            except Exception:
                return
            if regs != self.m.regions:
                self.m.regions = regs
                self.m.apply_mask()

        @Slot()
        def drag(self):
            h = self.m.view.windowHandle()
            if h is not None:
                log("移動を始めました")
                h.startSystemMove()                      # OS の移動操作（動かし終わると、moveEvent の後で位置を保存する）

        @Slot()
        def menu(self):
            self.m.show_menu()

        @Slot(str, str)
        def set_pref(self, key, value):
            if key == "volume":
                self.m.pref(key, float(value))

        @Slot()
        def focus_window(self):
            v = self.m.view
            v.activateWindow()
            v.raise_()
            v.setFocus()

        @Slot(str, str)
        def notify_hidden(self, title, text):
            """隠れているときだけ、Windows の通知（トレイのアイコン経由）で知らせる"""
            if self.m.hidden:
                threading.Thread(target=sup_post, args=("/api/notify", {"title": title, "message": text}), daemon=True).start()

    class Mascot(QObject):
        def __init__(self):
            super().__init__()
            self.cfg = {}
            self.cfg_stamp = None
            self.hidden = False
            self.size_name = "medium"
            self.content = (340, SIZES["medium"] + ZONE_H)          # 論理ピクセル（ページが resize で知らせる。最初は、起動中の画面の大きさ）
            self.anchor = None                                      # (x, y) = ウィンドウの下辺の中央（論理ピクセル）
            self.chat_loaded = False
            self.regions = []
            self.token = secrets.token_hex(16)
            self.hotkey = Hotkey(self._on_hotkey, self._hotkey_failed)
            self.programmatic = False
            self.view = None
            self.menu = None
            self.load_cfg()
            self.content = (340, SIZES.get(self.size_name, SIZES["medium"]) + ZONE_H)

        # ---- 設定 ----
        def load_cfg(self):
            try:
                self.cfg = ss.load_settings(ROOT)["mascot"]
            except Exception as e:  # noqa: BLE001
                log(f"設定を読めませんでした（既定の値を使います）: {e}")
                self.cfg = dict(ss.DEFAULT_SETTINGS["mascot"])
            self.size_name = self.cfg.get("size", "medium")

        def pref(self, key, value):
            try:
                ss.update_settings({"mascot": {key: value}}, ROOT)
                self.cfg[key] = value
            except Exception as e:  # noqa: BLE001
                log(f"設定を保存できませんでした（{key}）: {e}")

        # ---- ウィンドウ ----
        def build_view(self):
            RUN_DIR.mkdir(exist_ok=True)
            store = RUN_DIR / "qtweb"
            store.mkdir(exist_ok=True)
            profile = QWebEngineProfile("koemado-mascot", app)
            profile.setPersistentStoragePath(str(store / "data"))
            profile.setCachePath(str(store / "cache"))
            profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
            profile.settings().setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
            self.profile = profile

            v = View()
            v.setWindowTitle("koemado-mascot")
            self.set_flags(v)
            v.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            v.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            v.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            page = Page(profile, v)
            page.setBackgroundColor(Qt.GlobalColor.transparent)
            page.permissionRequested.connect(self.on_permission)
            v.setPage(page)

            # ページから呼べる関数（QWebChannel）。window.__kb に入る
            self.bridge = Bridge(self)
            ch = QWebChannel(page)
            ch.registerObject("bridge", self.bridge)
            page.setWebChannel(ch)
            f = QFile(":/qtwebchannel/qwebchannel.js")
            f.open(QIODevice.OpenModeFlag.ReadOnly)
            src = bytes(f.readAll().data()).decode("utf-8")
            f.close()
            sc = QWebEngineScript()
            sc.setName("koemado-bridge")
            sc.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            sc.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            sc.setRunsOnSubFrames(False)
            sc.setSourceCode(src + "\n;window.__kb=null;window.__kbReady=new Promise(function(res){new QWebChannel(qt.webChannelTransport,function(c){window.__kb=c.objects.bridge;res(true)})});")
            page.scripts().insert(sc)
            self.view = v
            self.page = page
            return v

        def set_flags(self, v):
            fl = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint
            if self.cfg.get("always_on_top", True):
                fl |= Qt.WindowType.WindowStaysOnTopHint
            v.setWindowFlags(fl)

        def on_permission(self, p):
            local = p.origin().host() == "127.0.0.1" and p.origin().scheme() == "http"
            if local and p.permissionType() == QWebEnginePermission.PermissionType.MediaAudioCapture:
                p.grant()                  # 127.0.0.1 のページのマイクだけ自動で許可
            else:
                p.deny()

        def screens_rects(self):
            return [s.availableGeometry() for s in QGuiApplication.screens()]

        def default_anchor(self, w):
            a = QGuiApplication.primaryScreen().availableGeometry()
            return (a.right() - w // 2 - 24, a.bottom() - 4)

        def anchor_visible(self, ax, ay, char_h) -> bool:
            probe = QPoint(int(ax), int(ay - char_h * 0.15))
            return any(r.contains(probe) for r in self.screens_rects())

        def apply_geometry(self, reset=False):
            """ページが知らせた大きさ（content）に合わせて、下辺の中央を固定したまま、窓の大きさと位置を決める"""
            w, h = self.content
            char_h = SIZES.get(self.size_name, SIZES["medium"])
            ax, ay = self.anchor if self.anchor else (self.cfg.get("x"), self.cfg.get("y"))
            if reset or ax is None or ay is None or not self.anchor_visible(ax, ay, char_h):
                if ax is not None and not reset:
                    log("保存した位置が画面の外だったので、見える位置に戻しました。")
                ax, ay = self.default_anchor(w)
            self.anchor = (int(ax), int(ay))
            self.programmatic = True
            self.view.setGeometry(int(ax - w // 2), int(ay - h), int(w), int(h))
            QTimer.singleShot(400, lambda: setattr(self, "programmatic", False))
            self.apply_mask()

        def current_anchor(self):
            g = self.view.geometry()
            return (g.x() + g.width() // 2, g.y() + g.height())

        def save_position(self):
            try:
                self.anchor = tuple(int(x) for x in self.current_anchor())
                ss.update_settings({"mascot": {"x": self.anchor[0], "y": self.anchor[1]}}, ROOT)
                self.cfg["x"], self.cfg["y"] = self.anchor
                log(f"位置を保存しました: {self.anchor}")
            except Exception as e:  # noqa: BLE001
                log(f"位置を保存できませんでした: {e}")

        def apply_mask(self):
            """クリックできる領域（キャラ・吹き出し・入力欄の形）。ここ以外は、見えないだけでなく、クリックも下のウィンドウに通る"""
            if not self.regions:
                self.view.clearMask()
                return
            reg = QRegion()
            for x, y, w, h in self.regions:
                reg = reg.united(QRegion(QRect(int(x), int(y), int(w), int(h))))
            self.view.setMask(reg)

        def show(self):
            self.hidden = False
            self.view.show()
            self.view.raise_()

        def hide(self):
            self.hidden = True
            self.view.hide()

        def set_topmost(self, on):
            self.cfg["always_on_top"] = bool(on)
            g = self.view.geometry()
            self.programmatic = True
            self.set_flags(self.view)
            self.view.setGeometry(g)
            if not self.hidden:
                self.view.show()
            self.apply_mask()
            QTimer.singleShot(400, lambda: setattr(self, "programmatic", False))

        def js(self, code: str, cb=None):
            if cb:
                self.page.runJavaScript(code, cb)
            else:
                self.page.runJavaScript(code)

        def js_state(self, then):
            if not self.chat_loaded:
                then({})
                return

            def cb(r):
                try:
                    then(json.loads(r) if r else {})
                except Exception:
                    then({})
            self.js("JSON.stringify(window.mascot ? window.mascot.state() : {})", cb)

        # ---- メニュー ----
        def show_menu(self):
            self.js_state(self._popup_menu)

        def _popup_menu(self, st: dict):
            m = QMenu()
            m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            on = self.chat_loaded

            def act(text, fn=None, checked=None, enabled=True, parent=None):
                a = QAction(text, m)
                a.setEnabled(enabled)
                if checked is not None:
                    a.setCheckable(True)
                    a.setChecked(bool(checked))
                if fn:
                    a.triggered.connect(lambda *_: fn())
                (parent or m).addAction(a)
                return a
            act("文字で話す", lambda: self.js("mascot.cmd('text')"), enabled=on)
            act("ミュート（マイク）", lambda: self.js("mascot.cmd('mute')"), checked=st.get("muted", False), enabled=on)
            vol = m.addMenu("音量")
            vol.setEnabled(on)
            cur = round(float(st.get("volume", self.cfg.get("volume", 1.0))) * 100)
            nearest = min((0, 25, 50, 75, 100), key=lambda x: abs(x - cur))
            for v in (0, 25, 50, 75, 100):
                act(f"{v}%" if v else "0%（消音）", (lambda v=v: self.js(f"mascot.cmd('volume',{v / 100})")), checked=(v == nearest), parent=vol)
            sz = m.addMenu("大きさ")
            for k, label in (("small", "小"), ("medium", "中"), ("large", "大")):
                act(label, (lambda k=k: self.set_size(k)), checked=(self.size_name == k), parent=sz)
            act("常に最前面", lambda: self.toggle_topmost(), checked=self.cfg.get("always_on_top", True))
            m.addSeparator()
            act("設定を開く", self.open_settings)
            act("ログ・状態を開く", self.open_console)
            m.addSeparator()
            act("隠す（トレイへ）", self.hide)
            act("終了（全体を止める）", self.quit_all)
            self.menu = m
            m.popup(QCursor.pos())

        def set_size(self, name: str):
            self.size_name = name
            self.pref("size", name)
            self.js("mascot.reload()")

        def toggle_topmost(self):
            v = not self.cfg.get("always_on_top", True)
            self.pref("always_on_top", v)
            self.set_topmost(v)

        def open_settings(self):
            def work():
                url = f"http://127.0.0.1:{self.app_port()}/settings"
                log(f"設定画面をブラウザで開きます: {url}")
                webbrowser.open(url)
            threading.Thread(target=work, daemon=True).start()

        def open_console(self):
            p = console_port()
            if p:
                def work():
                    log(f"状態・ログの画面をブラウザで開きます: http://127.0.0.1:{p}/")
                    webbrowser.open(f"http://127.0.0.1:{p}/")
                threading.Thread(target=work, daemon=True).start()

        def quit_all(self):
            log("終了（全体を止める）が選ばれました。")

            def work():
                if not sup_post("/api/shutdown"):
                    log("管理プロセスに連絡できなかったため、キャラだけ終了します。")
                time.sleep(1.0)
                self.exit()
            threading.Thread(target=work, daemon=True).start()

        def exit(self):
            try:
                self.hotkey.stop()
            except Exception:
                pass
            try:
                INFO_FILE.unlink()
            except OSError:
                pass
            os._exit(0)

        def app_port(self) -> int:
            st = sup_get("/api/state")
            if st:
                return st["ports"]["app"]
            try:
                return int(json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["ports"]["app"])
            except Exception:
                return 8100

        # ---- ショートカットキー ----
        def _on_hotkey(self):
            gui.run(lambda: self.js("mascot.hotkey()") if self.chat_loaded else None)

        def _hotkey_failed(self, why: str):
            log(why)
            gui.run(lambda: self.js("mascot.showBubble(%s,'info')" % json.dumps(why, ensure_ascii=False)) if self.chat_loaded else None)

        def apply_hotkey(self):
            want = self.cfg.get("hotkey") if self.cfg.get("talk") == "hotkey" else None
            if want != self.hotkey.spec:
                threading.Thread(target=(lambda: self.hotkey.start(want)) if want else self.hotkey.stop, daemon=True).start()

        # ---- 設定ファイルの監視・画面構成の監視 ----
        def watch(self):
            try:
                stamp = ss.settings_path(ROOT).stat().st_mtime_ns
            except OSError:
                stamp = None
            if stamp != self.cfg_stamp:
                self.cfg_stamp = stamp
                before = dict(self.cfg)
                self.load_cfg()
                if before != self.cfg:
                    self.apply_hotkey()
                    if before.get("always_on_top") != self.cfg.get("always_on_top"):
                        self.set_topmost(self.cfg.get("always_on_top", True))
                    if self.chat_loaded and any(before.get(k) != self.cfg.get(k) for k in ("size", "talk", "interrupt", "volume", "bubble_seconds")):
                        self.js("mascot.reload()")

        def screens_changed(self, *_):
            log("ディスプレイの構成が変わりました。見える位置を確認します。")
            QTimer.singleShot(800, lambda: self.apply_geometry())

        # ---- 起動の流れ ----
        def boot_wait(self):
            """アプリの準備ができるまで「起動中…」を出し、できたらキャラのページへ。失敗したら管理画面を開く（別スレッド）"""
            t0 = time.time()
            failed_shown = False
            while True:
                time.sleep(1.0)
                if self.chat_loaded:
                    return
                st = sup_get("/api/state")
                if st is None:
                    if time.time() - t0 > 60:
                        self.set_status("管理プロセスに接続できません。start.bat からやり直してください。", True)
                        log("管理プロセスに接続できません。")
                        time.sleep(5)
                    continue
                if st.get("ready"):
                    url = st["chat_url"].replace("/static/index.html", "/static/mascot.html")
                    log(f"準備ができました。キャラのページを開きます: {url}")
                    self.chat_loaded = True
                    gui.run(lambda: self.view.load(QUrl(url)))
                    return
                if st.get("phase") == "failed":
                    self.set_status("起動に失敗しました。管理画面（ブラウザ）で原因を確認してください。", True)
                    if not failed_shown:
                        failed_shown = True
                        log("起動に失敗したため、管理画面を開きます。")
                        webbrowser.open(f"http://127.0.0.1:{console_port()}/")
                    continue
                cur = next((s for s in st["services"] if s["state"] == "starting"), None)
                label = f"起動中… {cur['label']}" if cur else "起動中…"
                if cur and cur.get("starting_for") is not None:
                    label += f"（{cur['starting_for']} 秒）"
                if cur and cur["key"] == "llm":
                    label += "\nモデルを読み込んでいます。しばらくお待ちください。"
                self.set_status(label)

        def set_status(self, text, err=False):
            code = "setStatus(%s,%s)" % (json.dumps(text, ensure_ascii=False), "true" if err else "false")
            gui.run(lambda: self.js(code))

        # ---- 操作用の小さな HTTP サーバー（supervisor のトレイから）----
        def serve_control(self):
            me = self

            class H(BaseHTTPRequestHandler):
                def _ok(self, body=b"{}"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)

                def do_GET(self):
                    if self.path == "/status":
                        def get():
                            g = me.view.geometry()
                            return {"hidden": me.hidden, "chat": me.chat_loaded, "pid": os.getpid(), "rect": [g.x(), g.y(), g.width(), g.height()],
                                    "dpr": me.view.devicePixelRatioF(), "anchor": me.anchor, "size": me.size_name, "visible": me.view.isVisible(),
                                    "regions": me.regions}
                        return self._ok(json.dumps(gui.run_wait(get)).encode())
                    self.send_response(404)
                    self.end_headers()

                def do_POST(self):
                    if self.headers.get("X-Mascot-Token") != me.token:
                        self.send_response(403)
                        self.end_headers()
                        return
                    cmd = self.path.strip("/")
                    if cmd == "show":
                        gui.run(me.show)
                    elif cmd == "hide":
                        gui.run(me.hide)
                    elif cmd == "toggle":
                        gui.run(lambda: me.show() if me.hidden else me.hide())
                    elif cmd == "reset_position":
                        gui.run(lambda: me.apply_geometry(reset=True))
                    elif cmd == "eval" and DEBUG:
                        n = int(self.headers.get("Content-Length") or 0)
                        code = self.rfile.read(n).decode("utf-8")
                        fut = concurrent.futures.Future()
                        gui.run(lambda: me.js(code, lambda r: fut.set_result(r)))
                        try:
                            res = fut.result(30)
                        except Exception as e:  # noqa: BLE001
                            res = f"error: {e}"
                        return self._ok(json.dumps({"result": res}, ensure_ascii=False, default=str).encode("utf-8"))
                    else:
                        self.send_response(404)
                        self.end_headers()
                        return
                    self._ok()

                def log_message(self, *a):
                    pass

            srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
            RUN_DIR.mkdir(exist_ok=True)
            INFO_FILE.write_text(json.dumps({"pid": os.getpid(), "port": srv.server_address[1], "token": self.token}), encoding="utf-8")
            threading.Thread(target=srv.serve_forever, daemon=True, name="mascot-control").start()

    mascot = Mascot()
    move_timer = QTimer()
    move_timer.setSingleShot(True)
    move_timer.timeout.connect(lambda: mascot.save_position())
    view = mascot.build_view()
    for s in QGuiApplication.screens():
        s.geometryChanged.connect(mascot.screens_changed)
    app.screenAdded.connect(mascot.screens_changed)
    app.screenRemoved.connect(mascot.screens_changed)

    log(f"ウィンドウを作ります（準備に {time.time() - t_start:.1f} 秒）")
    if args.test_page:
        mascot.chat_loaded = True
        view.load(QUrl(args.test_page))
    else:
        view.setHtml(BOOT_HTML, QUrl("http://127.0.0.1/"))
        threading.Thread(target=mascot.boot_wait, daemon=True, name="boot").start()
    mascot.apply_geometry()
    view.show()
    mascot.serve_control()
    wt = QTimer()
    wt.timeout.connect(mascot.watch)
    wt.start(2000)
    mascot.cfg_stamp = ss.settings_path(ROOT).stat().st_mtime_ns if ss.settings_path(ROOT).exists() else None
    log(f"キャラのウィンドウを出しました（アンカー {mascot.anchor}、画面の倍率 {view.devicePixelRatioF():.2f}）")
    code = app.exec()
    log("ウィンドウが閉じられました。")
    mascot.exit()
    return code


if __name__ == "__main__":
    sys.exit(main())
