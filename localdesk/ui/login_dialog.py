"""EVE SSO login inside the app: the redirect to the callback URL is caught before it leaves the
embedded browser, so the window simply closes when the login is done."""
from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QProgressBar, QVBoxLayout

from .. import paths, sso
from .widgets import button, muted

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    AVAILABLE = True
except ImportError:  # QtWebEngine missing: the app falls back to logging in through the web browser
    AVAILABLE = False

_profile = None
_interceptor = None


def profile():
    """One persistent browser profile, so EVE SSO remembers your account between logins."""
    global _profile, _interceptor
    if _profile is None:
        _profile = QWebEngineProfile("eve-sso")
        store = paths.data_dir() / "login-browser"
        _profile.setPersistentStoragePath(str(store))
        _profile.setCachePath(str(store / "cache"))
        _profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        _interceptor = _Interceptor(_profile)
        _profile.setUrlRequestInterceptor(_interceptor)
    return _profile


if AVAILABLE:
    class _Interceptor(QWebEngineUrlRequestInterceptor):
        """Blocks the request to the localhost callback and hands its URL to the open dialog."""
        caught = Signal(str)
        prefix = None

        def interceptRequest(self, info):
            url = info.requestUrl().toString()
            if self.prefix and url.startswith(self.prefix):
                info.block(True)
                self.caught.emit(url)


class LoginDialog(QDialog):
    def __init__(self, parent, request: sso.LoginRequest):
        super().__init__(parent)
        self.request = request
        self.params = None
        self.setWindowTitle("Log in with EVE Online")
        self.resize(560, 800)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 8)
        bar.addWidget(muted("Pick the character to add. This window closes by itself when you're done.", wrap=True), 1)
        switch = button("Use a different EVE account", "fa5s.exchange-alt",
                        tip="Forget the remembered EVE account and log in with another one")
        switch.clicked.connect(self._switch_account)
        bar.addWidget(switch)
        lay.addLayout(bar)
        self.progress = QProgressBar()
        self.progress.setMaximumHeight(3)
        self.progress.setTextVisible(False)
        lay.addWidget(self.progress)

        prof = profile()
        self.interceptor = _interceptor
        self.interceptor.prefix = request.redirect_uri
        self.interceptor.caught.connect(self._caught)
        self.page = QWebEnginePage(prof, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.view.loadProgress.connect(self.progress.setValue)
        self.view.loadFinished.connect(lambda _: self.progress.setVisible(False))
        self.view.loadStarted.connect(lambda: self.progress.setVisible(True))
        # Fallback in case a navigation slips past the interceptor
        self.view.urlChanged.connect(lambda url: self._caught(url.toString()))
        lay.addWidget(self.view, 1)
        self.view.load(QUrl(request.url))

    def _caught(self, url):
        if self.params is not None or not url.startswith(self.request.redirect_uri):
            return
        self.params = sso.callback_params(url)
        self.accept()

    def _switch_account(self):
        profile().cookieStore().deleteAllCookies()
        self.view.load(QUrl(self.request.url))

    def done(self, result):
        if self.page is None:  # already closed
            return
        try:
            self.interceptor.caught.disconnect(self._caught)
        except (RuntimeError, TypeError):
            pass
        self.interceptor.prefix = None
        self.view.setPage(None)
        self.page.deleteLater()
        self.page = None
        super().done(result)


def shutdown():
    """Delete the login browser profile before Qt shuts down. Leaving it to Python's own teardown can
    crash QtWebEngine on exit (seen on Windows)."""
    global _profile, _interceptor
    if _profile is None:
        return
    try:
        import shiboken6
        _profile.setUrlRequestInterceptor(None)
        shiboken6.delete(_profile)
    except Exception:
        pass
    _profile = _interceptor = None
