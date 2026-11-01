"""First-run setup: the EVE developer application's Client ID and callback port."""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from .. import credentials, sso

INSTRUCTIONS = """<p>EVE LocalDesk talks to CCP's ESI API directly from your computer. CCP needs to know which app
is asking, so you register a free “application” once (about 2 minutes). No server or secret key is needed.</p>
<ol>
<li>Open <a href="https://developers.eveonline.com/applications">developers.eveonline.com/applications</a>
and log in with your EVE account.</li>
<li>Create a new application. Connection type: <b>Authentication &amp; API Access</b>.</li>
<li>Tick <b>all</b> the scopes listed below.</li>
<li>Callback URL: <b>{callback}</b></li>
<li>Copy the <b>Client ID</b> into the box below. You can ignore the Secret Key.</li>
</ol>"""


class SetupDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("EVE LocalDesk setup")
        self.resize(640, 640)
        lay = QVBoxLayout(self)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setOpenExternalLinks(True)
        lay.addWidget(self.text)
        form = QFormLayout()
        self.client_id = QLineEdit(credentials.client_id(ctx.db) or "")
        self.client_id.setPlaceholderText("32-character Client ID from the developers site")
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(ctx.db.get_setting("callback_port", str(sso.DEFAULT_PORT))))
        self.port.valueChanged.connect(self._update_text)
        self.in_app = QCheckBox("Log in inside the app (the login window closes by itself)")
        self.in_app.setToolTip("Untick to log in through your normal web browser instead.")
        self.in_app.setChecked(ctx.db.get_setting("login_mode", "app") == "app")
        form.addRow("Client ID", self.client_id)
        form.addRow("Callback port", self.port)
        form.addRow("", self.in_app)
        self.updates = QCheckBox("Check for a new version when the app starts")
        self.updates.setChecked(ctx.db.get_setting("check_updates", "1") == "1")
        form.addRow("", self.updates)
        lay.addLayout(form)
        file_note = QLabel(f"Alternatively, put the Client ID in <code>{credentials.FILE_NAME}</code> next to the "
                           f"app or in its data folder; that file overrides this setting.")
        file_note.setWordWrap(True)
        lay.addWidget(file_note)
        lay.addWidget(QLabel("Scopes to tick on your application:"))
        scopes = QPlainTextEdit("\n".join(sso.SCOPES))
        scopes.setReadOnly(True)
        lay.addWidget(scopes, 1)
        open_site = QPushButton("Open the EVE developers site")
        open_site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://developers.eveonline.com/applications")))
        lay.addWidget(open_site)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._update_text()

    def _update_text(self):
        self.text.setText(INSTRUCTIONS.format(callback=sso.callback_url(self.port.value())))

    def _save(self):
        self.ctx.db.set_setting("client_id", self.client_id.text().strip())
        self.ctx.db.set_setting("callback_port", str(self.port.value()))
        self.ctx.db.set_setting("login_mode", "app" if self.in_app.isChecked() else "browser")
        self.ctx.db.set_setting("check_updates", "1" if self.updates.isChecked() else "0")
        self.accept()
