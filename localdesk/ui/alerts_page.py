"""Alerts page plus desktop (tray) notifications for new alerts."""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QCheckBox, QLabel, QSystemTrayIcon

from .. import alerts
from ..model import fmt_time
from .theme import ACCENT
from .widgets import Card, Page, SortItem, fill_table, make_table, muted

LEVEL_COLORS = {"critical": ACCENT["danger"], "warning": ACCENT["orange"], "info": ACCENT["info"]}


def app_icon():
    """The app icon (localdesk/assets/icon.png), or a drawn fallback."""
    path = Path(__file__).resolve().parent.parent / "assets" / "icon.png"
    if path.is_file():
        return QIcon(str(path))
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#3d8fd1"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(QColor("#15171a"))
    p.drawEllipse(20, 20, 24, 24)
    p.end()
    return QIcon(pix)


class AlertsPage(Page):
    title = "Alerts"

    icon = "fa5s.bell"
    section = "Overview"

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = self.vbox()
        card = Card("Alerts", accent="danger", margins=0)
        self.summary = QLabel()
        self.notify = QCheckBox("Desktop notifications")
        self.notify.setChecked(ctx.db.get_setting("notify", "1") == "1")
        self.notify.toggled.connect(lambda on: ctx.db.set_setting("notify", "1" if on else "0"))
        card.add_tool(self.summary)
        card.add_tool(self.notify)
        self.table = make_table(["Level", "Character", "Alert", "When"], stretch=2)
        card.add(self.table, 1)
        lay.addWidget(card, 1)
        lay.addWidget(muted("Checks: skill queues, unread mail, PI extractors, finished industry jobs, contracts "
                            "assigned to you, jump fatigue, structure fuel and moon chunks, expired logins."))
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip("EVE LocalDesk")
            self.tray.activated.connect(lambda *_: (ctx.window.showNormal(), ctx.window.activateWindow()))
            self.tray.show()
        self._seen = set(json.loads(ctx.db.get_setting("seen_alerts", "[]")))
        self._items = []

    def refresh_view(self):
        self._items = alerts.compute(self.ctx.db, self.ctx.syncer, self.ctx.view)
        names = {c["character_id"]: c["name"] for c in self.ctx.characters()}
        order = {"critical": 0, "warning": 1, "info": 2}
        fill_table(self.table, [[SortItem(a["level"].title(), order[a["level"]], badge=LEVEL_COLORS[a["level"]]),
                                 names.get(a["character"], ""), a["text"],
                                 SortItem(fmt_time(a["when"]) if a["when"] else "", a["when"] or 0)]
                                for a in self._items])
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        counts = {lvl: sum(1 for a in self._items if a["level"] == lvl) for lvl in order}
        self.summary.setText(f"<b style='color:{LEVEL_COLORS['critical']}'>{counts['critical']}</b> critical · "
                             f"<b style='color:{LEVEL_COLORS['warning']}'>{counts['warning']}</b> warnings · "
                             f"<b>{counts['info']}</b> info &nbsp;")
        item = self.ctx.window.nav_item(self.title) if hasattr(self.ctx.window, "nav") else None
        if item is not None:
            urgent = counts["critical"] + counts["warning"]
            item.setText(f"Alerts  ({urgent})" if urgent else "Alerts")

    def notify_new(self):
        """Call after a sync: pops a desktop notification for alerts not seen before."""
        self.refresh_view()
        new = [a for a in self._items if a["id"] not in self._seen]
        self._seen = {a["id"] for a in self._items}
        self.ctx.db.set_setting("seen_alerts", json.dumps(sorted(self._seen)))
        if new and self.tray and self.notify.isChecked():
            names = {c["character_id"]: c["name"] for c in self.ctx.characters()}
            body = "\n".join(f"{names.get(a['character'], '')}: {a['text']}" for a in new[:5])
            if len(new) > 5:
                body += f"\n…and {len(new) - 5} more"
            self.tray.showMessage(f"EVE LocalDesk: {len(new)} new alert(s)", body,
                                  QSystemTrayIcon.MessageIcon.Warning, 10000)
