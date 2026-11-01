"""Main window, background task runner and app entry point."""
import os
import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, credentials, paths, sso
from ..db import Database
from ..esi import ESI
from ..model import DataView
from ..sync import Syncer
from . import theme
from .widgets import avatar_placeholder, button, fill_char_combo, round_pixmap, set_prefs_store

SYNC_INTERVAL_MS = 15 * 60 * 1000


class WorkerSignals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)


class Worker(QRunnable):
    """Runs fn(progress) on the thread pool; results come back to the GUI thread via signals."""

    def __init__(self, fn):
        super().__init__()
        self.setAutoDelete(False)
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self):
        try:
            try:
                result = self.fn(self.signals.progress.emit)
            except Exception as e:
                traceback.print_exc()
                self.signals.failed.emit(str(e) or e.__class__.__name__)
            else:
                self.signals.done.emit(result)
        except RuntimeError:
            pass  # the window was closed while this task was still running


class Context:
    """What every page gets: storage, ESI access and a way to run background work."""

    def __init__(self, db, esi, syncer, view):
        self.db, self.esi, self.syncer, self.view = db, esi, syncer, view
        self.window = None
        self.universe = None  # map data, loaded in the background at startup

    def run(self, fn, on_done=None, on_error=None, label=None):
        self.window.run_task(fn, on_done, on_error, label)

    def log(self, message):
        self.window.log(message)

    @property
    def current_char(self):
        return self.window.current_char()

    def characters(self):
        return self.db.list_characters()


class MainWindow(QMainWindow):
    data_changed = Signal()
    char_changed = Signal(object)

    def __init__(self, ctx: Context):
        super().__init__()
        from .pages import PAGES  # imported here so pages can import this module's helpers
        ctx.window = self
        self.ctx = ctx
        set_prefs_store(ctx.db.get_setting, ctx.db.set_setting)
        self.pool = QThreadPool.globalInstance()
        self._workers = set()
        self._syncing = False
        self._login_cancel = None
        self.setWindowTitle(f"EVE LocalDesk {__version__}")
        self.resize(1440, 880)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar(PAGES))
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._build_header())
        content = QWidget()
        content.setObjectName("content")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 16, 16, 16)
        self.stack = QStackedWidget()
        cl.addWidget(self.stack)
        rl.addWidget(content, 1)
        root.addWidget(right, 1)
        self.setCentralWidget(central)

        self.pages = {}
        section = None
        for cls in PAGES:
            if cls.section != section:
                section = cls.section
                header = QListWidgetItem(section.upper())
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                f = header.font()
                f.setPointSizeF(f.pointSizeF() * 0.8)
                f.setBold(True)
                header.setFont(f)
                header.setForeground(QColor("#6c757d"))
                header.setSizeHint(QSize(0, 34))
                self.nav.addItem(header)
            page = cls(ctx)
            self.pages[cls.title] = page
            self.stack.addWidget(page)
            item = QListWidgetItem(theme.icon(cls.icon), cls.title)
            item.setData(Qt.ItemDataRole.UserRole, cls.title)
            self.nav.addItem(item)
            self.data_changed.connect(page.refresh_view)
            self.char_changed.connect(page.on_char_changed)
        self.nav.currentItemChanged.connect(self._nav_changed)
        from .alerts_page import app_icon
        self.setWindowIcon(app_icon())
        self.show_page(PAGES[0].title)
        self.char_changed.connect(self._update_user_panel)
        self.char_changed.connect(self._remember_character)

        self.busy_label = QLabel()
        self.cancel_login_btn = QPushButton("Cancel login")
        self.cancel_login_btn.setVisible(False)
        self.cancel_login_btn.clicked.connect(lambda: self._login_cancel and self._login_cancel.set())
        self.statusBar().addPermanentWidget(self.cancel_login_btn)
        self.statusBar().addPermanentWidget(self.busy_label)

        self.timer = QTimer(self)
        self.timer.setInterval(SYNC_INTERVAL_MS)
        self.timer.timeout.connect(self.sync_all)
        self.reload_characters()
        if "--selftest" in sys.argv:
            QTimer.singleShot(1500, self._selftest)
        else:
            QTimer.singleShot(0, self._startup)

    def _build_sidebar(self, pages):
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        brand = QWidget()
        brand.setObjectName("brand")
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(16, 12, 16, 12)
        logo = QLabel()
        from .alerts_page import app_icon
        logo.setPixmap(app_icon().pixmap(30, 30))
        name = QLabel("EVE <b>LocalDesk</b>")
        name.setObjectName("brandText")
        bl.addWidget(logo)
        bl.addWidget(name, 1)
        lay.addWidget(brand)
        panel = QWidget()
        panel.setObjectName("userPanel")
        pl = QHBoxLayout(panel)
        pl.setContentsMargins(16, 10, 16, 10)
        self.user_portrait = QLabel()
        self.user_portrait.setFixedSize(34, 34)
        self.user_name = QLabel("No character")
        self.user_name.setObjectName("userName")
        pl.addWidget(self.user_portrait)
        pl.addWidget(self.user_name, 1)
        lay.addWidget(panel)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self.nav, 1)
        version = QLabel(f"v{__version__} · runs locally")
        version.setObjectName("version")
        version.setContentsMargins(16, 8, 16, 10)
        lay.addWidget(version)
        return side

    def _build_header(self):
        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 10, 16, 10)
        hl.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        self.breadcrumb = QLabel()
        self.breadcrumb.setObjectName("breadcrumb")
        titles.addWidget(self.page_title)
        titles.addWidget(self.breadcrumb)
        hl.addLayout(titles)
        hl.addStretch(1)
        self.char_combo = QComboBox()
        self.char_combo.setMinimumWidth(220)
        self.char_combo.currentIndexChanged.connect(lambda _: self.char_changed.emit(self.current_char()))
        hl.addWidget(self.char_combo)
        add = button("Add character", "fa5s.user-plus", "primary")
        add.clicked.connect(self.add_character)
        remove = button("", "fa5s.user-minus", "flat", "Remove the selected character")
        remove.clicked.connect(self.remove_character)
        refresh = button("Refresh", "fa5s.sync-alt", tip="Refresh all characters now")
        refresh.clicked.connect(lambda: self.sync_all())
        dark = self.ctx.db.get_setting("theme", "light") == "dark"
        self.theme_btn = button("", "fa5s.sun" if dark else "fa5s.moon", "flat", "Switch light / dark theme")
        self.theme_btn.clicked.connect(self.toggle_theme)
        setup = button("", "fa5s.cog", "flat", "Setup (EVE application Client ID)")
        setup.clicked.connect(self.open_setup)
        for w in (add, remove, refresh, self.theme_btn, setup):
            hl.addWidget(w)
        return header

    def _nav_changed(self, item, _previous=None):
        title = item.data(Qt.ItemDataRole.UserRole) if item else None
        if title not in self.pages:
            return
        page = self.pages[title]
        self.stack.setCurrentWidget(page)
        self.page_title.setText(title)
        self.breadcrumb.setText(f"Home  /  {page.section}  /  {title}")

    def nav_item(self, title):
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == title:
                return self.nav.item(i)
        return None

    def _update_user_panel(self, cid):
        self.user_name.setText(self.char_combo.currentText() if cid else "No character")
        path = paths.portraits_dir() / f"{cid}.jpg"
        pix = QPixmap(str(path)) if cid and path.exists() else QPixmap()
        self.user_portrait.setPixmap(round_pixmap(pix, 34) if not pix.isNull() else avatar_placeholder(34))

    def toggle_theme(self):
        name = "dark" if self.ctx.db.get_setting("theme", "light") == "light" else "light"
        self.ctx.db.set_setting("theme", name)
        theme.apply(QApplication.instance(), name)
        self.theme_btn.setIcon(theme.icon("fa5s.sun" if name == "dark" else "fa5s.moon", "#6c757d"))

    def load_universe(self, download=False):
        """Load the EVE map (downloading it once if needed) in the background."""
        from .. import universe
        path = paths.data_dir() / "universe.json.gz"

        def job(progress):
            if path.exists() and not download:
                return universe.load(path)
            progress("Downloading EVE map data (one time, about 2 MB)…")
            try:
                u = universe.Universe.from_fuzzwork(self.ctx.esi.http, progress)
            except Exception as e:
                self.log(f"Map download from Fuzzwork failed ({e}); building it from ESI instead. This takes a "
                         f"few minutes.")
                u = universe.Universe.from_esi(self.ctx.esi, progress)
            universe.save(u, path)
            return u

        def done(u):
            self.ctx.universe = u
            self.log(f"Map data ready: {len(u.systems):,} systems, {len(u.regions)} regions")
            self.data_changed.emit()

        self.run_task(job, done, lambda e: self.log(f"Could not load map data: {e}"))

    def _selftest(self):
        """Used by the build pipeline: prove a packaged build starts and has its parts. Exit code 0 = OK."""
        from . import login_dialog
        problems = []
        if theme.icon("fa5s.route").isNull():
            problems.append("icon font (qtawesome) missing")
        if self.windowIcon().isNull():
            problems.append("app icon missing")
        if len(self.pages) < 11:
            problems.append("pages missing")

        def finish(extra=None):
            if extra:
                problems.append(extra)
            write_selftest("OK" if not problems else "FAILED: " + "; ".join(problems))
            code = 1 if problems else 0
            if "--normal-exit" in sys.argv:
                # also exercise the regular shutdown path (used by CI to spot crashes on exit)
                self._selftest_view.deleteLater() if hasattr(self, "_selftest_view") else None
                QTimer.singleShot(300, lambda: QApplication.instance().exit(code))
            else:
                hard_exit(code)

        if not login_dialog.AVAILABLE:
            return finish("QtWebEngine (in-app login) missing")
        # Render a page with the login window's browser profile, to prove the embedded browser's helper
        # process is bundled and that the profile shuts down cleanly.
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self._selftest_view = view = QWebEngineView()
        view.setPage(QWebEnginePage(login_dialog.profile(), view))
        done = []

        def loaded(ok):
            if not done:
                done.append(ok)
                finish(None if ok else "embedded browser could not render a page")

        view.loadFinished.connect(loaded)
        view.setHtml("<p>selftest</p>")
        QTimer.singleShot(20000, lambda: loaded(False))

    def _startup(self):
        if not credentials.client_id(self.ctx.db):
            self.open_setup()
        self.load_universe()
        self.data_changed.emit()
        self.sync_all()
        self.timer.start()

    # --- helpers used by pages -------------------------------------------
    def current_char(self):
        return self.char_combo.currentData()

    def select_char(self, character_id):
        idx = self.char_combo.findData(character_id)
        if idx >= 0:
            self.char_combo.setCurrentIndex(idx)

    def show_page(self, title):
        self.nav.setCurrentItem(self.nav_item(title))

    def log(self, message):
        self.pages["Activity log"].append(message)

    def reload_characters(self, select=None):
        last = self.ctx.db.get_setting("last_character")
        keep = select or self.char_combo.currentData() or (int(last) if last else None)
        fill_char_combo(self.char_combo, self.ctx.db.list_characters(), keep=keep)
        self.char_changed.emit(self.current_char())

    def _remember_character(self, cid):
        if cid:
            self.ctx.db.set_setting("last_character", str(cid))

    # --- background tasks -------------------------------------------------
    def run_task(self, fn, on_done=None, on_error=None, label=None):
        worker = Worker(fn)
        self._workers.add(worker)
        if label:
            self.statusBar().showMessage(label)

        def finished():
            self._workers.discard(worker)
            self._update_busy()

        def done(result):
            finished()
            if label:
                self.statusBar().clearMessage()
            if on_done:
                on_done(result)

        def failed(error):
            finished()
            self.log(f"Error{f' ({label})' if label else ''}: {error}")
            if on_error:
                on_error(error)
            else:
                self.statusBar().clearMessage()
                QMessageBox.warning(self, "Something went wrong", error)

        worker.signals.progress.connect(self.statusBar().showMessage)
        worker.signals.done.connect(done)
        worker.signals.failed.connect(failed)
        self._update_busy()
        self.pool.start(worker)

    def _update_busy(self):
        n = len(self._workers)
        self.busy_label.setText(f"⏳ {n} task(s) running" if n else "")

    # --- actions ----------------------------------------------------------
    def sync_all(self, force=False):
        chars = self.ctx.db.list_characters()
        if self._syncing or not chars:
            return
        self._syncing = True

        def job(progress):
            errors = {}
            progress("Updating market prices and skill data…")
            try:
                self.ctx.syncer.sync_public()
            except Exception as e:
                errors["Public data"] = [str(e)]
            for ch in chars:
                errs = self.ctx.syncer.sync_character(ch["character_id"], force=force, progress=progress)
                if errs:
                    errors[ch["name"]] = errs
            return errors

        def done(errors):
            self._syncing = False
            self.data_changed.emit()
            self.pages["Alerts"].notify_new()
            for name, errs in errors.items():
                for e in errs:
                    self.log(f"{name}: {e}")
            self.statusBar().showMessage(
                "Sync finished with errors — see Activity log" if errors
                else f"Synced {len(chars)} character(s) at {time.strftime('%H:%M')}", 10000)

        def failed(_):
            self._syncing = False

        self.run_task(job, done, failed, "Syncing…")

    def add_character(self):
        client_id = credentials.client_id(self.ctx.db)
        if not client_id:
            self.open_setup()
            return
        port = credentials.callback_port(self.ctx.db, sso.DEFAULT_PORT)
        from . import login_dialog
        if login_dialog.AVAILABLE and self.ctx.db.get_setting("login_mode", "app") == "app":
            self._login_in_app(client_id, port, login_dialog)
        else:
            self._login_in_browser(client_id, port)

    def _login_in_app(self, client_id, port, login_dialog):
        request = sso.prepare_login(client_id, port)
        dlg = login_dialog.LoginDialog(self, request)
        if dlg.exec() != login_dialog.LoginDialog.DialogCode.Accepted or not dlg.params:
            return
        params = dlg.params

        def job(progress):
            progress("Finishing EVE login…")
            tokens, info = sso.finish_login(client_id, request, params)
            self.ctx.db.upsert_character(info, tokens)
            return info

        self.run_task(job, self._character_added, lambda e: QMessageBox.warning(self, "Login failed", e))

    def _login_in_browser(self, client_id, port):
        self._login_cancel = threading.Event()
        cancel = self._login_cancel
        self.cancel_login_btn.setVisible(True)

        def job(progress):
            progress("Finish logging in to EVE in your web browser…")
            tokens, info = sso.login(client_id, port, cancel=cancel)
            self.ctx.db.upsert_character(info, tokens)
            return info

        def done(info):
            self.cancel_login_btn.setVisible(False)
            self._character_added(info)

        def failed(error):
            self.cancel_login_btn.setVisible(False)
            if error != "Login cancelled":
                QMessageBox.warning(self, "Login failed", error)

        self.run_task(job, done, failed)

    def _character_added(self, info):
        self.reload_characters(select=info["character_id"])
        self.log(f"Added {info['name']}")
        missing = sorted(set(sso.SCOPES) - set(info["scopes"]))
        if missing:
            self.log(f"{info['name']} is missing scopes (those features are disabled): {', '.join(missing)}")
        self.sync_all()

    def remove_character(self):
        cid = self.current_char()
        if not cid:
            return
        name = self.char_combo.currentText()
        if QMessageBox.question(self, "Remove character",
                                f"Remove {name} and all of its locally stored data?") \
                == QMessageBox.StandardButton.Yes:
            self.ctx.db.remove_character(cid)
            self.reload_characters()
            self.data_changed.emit()

    def open_setup(self):
        from .setup_dialog import SetupDialog
        SetupDialog(self.ctx, self).exec()


def hard_exit(code):
    """End the process without running library teardown. On Windows even os._exit lets every DLL run
    its detach code, and QtWebEngine's crashes there (access violation); TerminateProcess skips it.
    Only called once everything is saved and flushed."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # declare the types: an undeclared 64-bit HANDLE gets truncated and the call silently fails
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.GetCurrentProcess.argtypes = []
            kernel32.TerminateProcess.restype = wintypes.BOOL
            kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
        except Exception:
            pass
    os._exit(code)


def ensure_output_streams():
    """A windowed Windows build has no console, so sys.stdout/sys.stderr are None and anything that
    prints (tracebacks from background tasks included) would itself fail. Send that output to
    <data dir>/localdesk.log instead, which is also handy when reporting a problem."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log = open(paths.data_dir() / "localdesk.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
    print(f"--- EVE LocalDesk {__version__} started {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)


def write_selftest(result):
    """Self-test result on stdout and in <data dir>/selftest.log (a windowed Windows build has no stdout)."""
    line = f"SELFTEST {result}"
    print(line, flush=True)
    try:
        (paths.data_dir() / "selftest.log").write_text(line + "\n", encoding="utf-8")
    except OSError:
        pass


def build_context():
    db = Database(paths.db_path())
    esi = ESI(db)
    syncer = Syncer(db, esi)
    return Context(db, esi, syncer, DataView(db, syncer))


def main():
    ensure_output_streams()
    if "--selftest" in sys.argv:
        try:
            _main()
        except BaseException as e:
            if isinstance(e, SystemExit):
                raise
            write_selftest("CRASHED:\n" + traceback.format_exc())
            hard_exit(2)
    else:
        _main()


def _main():
    if sys.platform == "win32":
        # Own taskbar entry and icon instead of being grouped under python.exe
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EVELocalDesk.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("EVE LocalDesk")
    app.setApplicationVersion(__version__)
    from .alerts_page import app_icon
    app.setWindowIcon(app_icon())
    ctx = build_context()
    theme.apply(app, ctx.db.get_setting("theme", "light"))
    window = MainWindow(ctx)
    window.show()
    code = app.exec()
    # Shut the embedded browser down in a known order, then exit straight away: Python's own teardown
    # can crash QtWebEngine on Windows, and background network tasks needn't finish (all data is
    # already committed to the database as it arrives).
    from . import login_dialog
    login_dialog.shutdown()
    window.deleteLater()
    hard_exit(code)
