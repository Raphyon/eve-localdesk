"""EVE mail: read, compose and reply."""
import html
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox,
                               QPlainTextEdit, QSplitter, QTextBrowser)

from ..model import fmt_time, parse_time
from ..sync import S_MAIL, S_ORGANIZE_MAIL, S_SEND_MAIL
from .widgets import USER_ROLE, Card, Page, SortItem, button, fill_table, make_table, muted


def clean_mail_html(body):
    """EVE mail bodies use <font size/color> with ARGB colours meant for a dark client; drop those
    attributes and turn showinfo: links into plain text."""
    body = re.sub(r'\s(?:color|size)="[^"]*"', "", body or "")
    body = re.sub(r"<a href=\"(?:showinfo|killReport|fitting)[^\"]*\">(.*?)</a>", r"\1", body, flags=re.S)
    return body.replace("\n", "<br>")


def mail_to_text(body):
    text = re.sub(r"<br\s*/?>", "\n", body or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


class ComposeDialog(QDialog):
    def __init__(self, parent, sender_name, to="", subject="", body=""):
        super().__init__(parent)
        self.setWindowTitle(f"New mail from {sender_name}")
        self.resize(640, 480)
        form = QFormLayout(self)
        self.to = QLineEdit(to)
        self.to.setPlaceholderText("Character, corporation, alliance or mailing list names, comma-separated")
        self.subject = QLineEdit(subject)
        self.body = QPlainTextEdit(body)
        form.addRow("To", self.to)
        form.addRow("Subject", self.subject)
        form.addRow(self.body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Send", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        names = [n.strip() for n in self.to.text().split(",") if n.strip()]
        body = html.escape(self.body.toPlainText()).replace("\n", "<br>")
        return names, self.subject.text().strip(), body


class MailPage(Page):
    title = "Mail"
    icon = "fa5s.envelope"
    section = "Character"

    def __init__(self, ctx):
        super().__init__(ctx)
        card = Card("Mailbox", accent="primary", margins=0)
        self.status = muted(wrap=False)
        self.label_filter = QComboBox()
        self.label_filter.setMinimumWidth(150)
        self.label_filter.currentIndexChanged.connect(lambda _: self._fill())
        fetch = button("", "fa5s.sync-alt", tip="Check for new mail")
        fetch.clicked.connect(self._fetch)
        compose = button("Compose", "fa5s.pen", "primary")
        compose.clicked.connect(lambda: self._compose())
        self.reply_btn = button("Reply", "fa5s.reply")
        self.reply_btn.clicked.connect(self._reply)
        self.reply_btn.setEnabled(False)
        for w in (self.status, self.label_filter, fetch, self.reply_btn, compose):
            card.add_tool(w)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = make_table(["Received", "From", "To", "Subject"], stretch=3)
        self.table.itemSelectionChanged.connect(self._selected)
        self.viewer = QTextBrowser()
        self.viewer.setOpenLinks(False)
        self.viewer.setStyleSheet("border: none; border-top: 1px solid palette(mid); padding: 12px;")
        split.addWidget(self.table)
        split.addWidget(self.viewer)
        split.setSizes([360, 340])
        card.add(split, 1)
        self.vbox().addWidget(card)
        self._current = None

    def refresh_view(self):
        cid = self.ctx.current_char
        labels = (self.ctx.view.get(cid, "mail_labels") or {}).get("labels", []) if cid else []
        keep = self.label_filter.currentData()
        self.label_filter.blockSignals(True)
        self.label_filter.clear()
        self.label_filter.addItem("All folders", None)
        for lb in labels:
            unread = f" ({lb['unread_count']})" if lb.get("unread_count") else ""
            self.label_filter.addItem(lb["name"] + unread, lb["label_id"])
        idx = self.label_filter.findData(keep)
        self.label_filter.setCurrentIndex(max(idx, 0))
        self.label_filter.blockSignals(False)
        if cid and not self.ctx.syncer.has_scope(cid, S_MAIL):
            self.status.setText("This character didn't grant mail access.")
        else:
            self.status.setText("")
        self._fill()

    def _fill(self):
        cid = self.ctx.current_char
        v = self.ctx.view
        mails = v.get(cid, "mail") or [] if cid else []
        label = self.label_filter.currentData()
        rows = []
        for m in mails:
            if label is not None and label not in m.get("labels", []):
                continue
            ts = parse_time(m.get("timestamp"))
            unread = not m.get("is_read", False)
            to = ", ".join(v.name(r["recipient_id"]) for r in m.get("recipients", [])[:3])
            if len(m.get("recipients", [])) > 3:
                to += ", …"
            rows.append([SortItem(fmt_time(ts), ts, data=m["mail_id"], bold=unread),
                         SortItem(v.name(m.get("from")), bold=unread),
                         SortItem(to),
                         SortItem(m.get("subject", ""), bold=unread)])
        self.table.blockSignals(True)
        fill_table(self.table, rows)
        self.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.table.blockSignals(False)

    def _header(self, mail_id):
        for m in self.ctx.view.get(self.ctx.current_char, "mail") or []:
            if m["mail_id"] == mail_id:
                return m
        return None

    def _selected(self):
        row = self.table.currentRow()
        cid = self.ctx.current_char
        if row < 0 or not cid:
            return
        mail_id = self.table.item(row, 0).data(USER_ROLE)
        header = self._header(mail_id)
        self.reply_btn.setEnabled(True)
        cached = self.ctx.db.cache_get(cid, f"mailbody:{mail_id}")
        if cached:
            self._show(header, cached)
        else:
            self.viewer.setHtml("<i>Loading…</i>")
            self.ctx.run(lambda p: self.ctx.syncer.mail_body(cid, mail_id),
                         lambda body: self._show(header, body) if self.ctx.current_char == cid else None)
        if header and not header.get("is_read") and self.ctx.syncer.has_scope(cid, S_ORGANIZE_MAIL):
            self.ctx.run(lambda p: self.ctx.syncer.mark_mail_read(cid, mail_id), lambda _: self._mark_read_row(row),
                         on_error=lambda e: None)

    def _mark_read_row(self, row):
        if row < self.table.rowCount():
            for c in (0, 1, 3):
                item = self.table.item(row, c)
                f = item.font()
                f.setBold(False)
                item.setFont(f)

    def _show(self, header, body):
        v = self.ctx.view
        self._current = (header, body)
        recipients = ", ".join(v.name(r["recipient_id"]) for r in body.get("recipients", []))
        self.viewer.setHtml(
            f"<h3>{html.escape(body.get('subject', ''))}</h3>"
            f"<b>From:</b> {html.escape(v.name(body.get('from')))}<br>"
            f"<b>To:</b> {html.escape(recipients)}<br>"
            f"<b>Date:</b> {fmt_time(parse_time(body.get('timestamp')))}<hr>"
            f"{clean_mail_html(body.get('body', ''))}")

    def _compose(self, to="", subject="", body=""):
        cid = self.ctx.current_char
        if not cid:
            return
        if not self.ctx.syncer.has_scope(cid, S_SEND_MAIL):
            QMessageBox.information(self, "Missing permission", "This character didn't grant esi-mail.send_mail.v1.")
            return
        dlg = ComposeDialog(self, self.ctx.window.char_combo.currentText(), to, subject, body)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        names, subj, html_body = dlg.values()
        self.ctx.run(lambda p: self.ctx.syncer.send_mail(cid, names, subj, html_body),
                     lambda _: (self.ctx.log(f"Mail “{subj}” sent"), self.status.setText("Mail sent ✓")),
                     label="Sending mail…")

    def _reply(self):
        if not self._current:
            return
        header, body = self._current
        subject = body.get("subject", "")
        quoted = "\n".join("> " + line for line in mail_to_text(body.get("body", "")).splitlines())
        self._compose(self.ctx.view.name(body.get("from")),
                      subject if subject.lower().startswith("re:") else f"Re: {subject}", f"\n\n{quoted}")

    def _fetch(self):
        cid = self.ctx.current_char
        if not cid:
            return
        def job(p):
            self.ctx.syncer.fetch_mail(cid, force=True)
            self.ctx.syncer.fetch(cid, "mail_labels", f"/characters/{cid}/mail/labels/", force=True)
            self.ctx.syncer.resolve_for_character(cid)
        self.ctx.run(job, lambda _: self.ctx.window.data_changed.emit(), label="Checking mail…")
