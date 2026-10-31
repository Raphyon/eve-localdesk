"""Dotlan-style top-down map of New Eden: security-coloured systems, gates, region names, and overlays
for routes (gate routes as thick lines, capital jumps as blue arcs), jump bridges and characters.

To stay readable, system names are placed without overlapping (busier systems first) and only once
zoomed in, region names fade out as system names appear, and the base map dims under a route."""
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem,
                               QGraphicsScene, QGraphicsView)

from ..universe import LY
from .widgets import sec_color

PX_PER_LY = 20.0
LABEL_ZOOM = 1.1      # system names start appearing at this zoom
REGION_FADE = (1.1, 3.0)  # region names fade out between these zoom levels
DIMMED = 0.3          # base map opacity while a route is shown
DIMMED_LABELS = 0.5   # and the opacity of the names around it
SMALL_DOTS_BELOW = 0.7  # zoom under which system dots shrink
LABEL_GAP = (5, 3)    # extra horizontal / vertical space kept between names (pixels)
ROUTE_COLOR, JUMP_COLOR, BRIDGE_COLOR, WAYPOINT_COLOR, CHAR_COLOR = (
    "#fd7e14", "#4d5dff", "#17a2b8", "#1fa31f", "#1fa31f")
IGNORE = QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations


def _pen(color, width, style=Qt.PenStyle.SolidLine):
    pen = QPen(QColor(color), width, style)
    pen.setCosmetic(True)  # width in screen pixels regardless of zoom
    return pen


def _dark():
    return QApplication.palette().base().color().lightness() < 128


class Label(QGraphicsItem):
    """A system name with a soft background, drawn at a fixed screen size beside its system."""

    def __init__(self, text, font, color, background, offset=(6, -8), centered=False):
        super().__init__()
        self.setFlag(IGNORE)
        self.text, self.font, self.color, self.background = text, font, QColor(color), QColor(background)
        fm = QFontMetricsF(font)
        w, h = fm.horizontalAdvance(text) + 8, fm.height() + 2
        self.rect = QRectF(-w / 2, -h / 2, w, h) if centered else QRectF(offset[0], offset[1], w, h)

    def boundingRect(self):
        return self.rect

    def paint(self, painter, option, widget=None):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.background)
        painter.drawRoundedRect(self.rect, 3, 3)
        painter.setPen(self.color)
        painter.setFont(self.font)
        painter.drawText(self.rect, Qt.AlignmentFlag.AlignCenter, self.text)


class StarMap(QGraphicsView):
    system_menu = Signal(int, object)  # system id, global position (right-click)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setMinimumHeight(260)
        self.universe = None
        self._labels = {}        # sid -> Label (base map)
        self._priority = []      # sids, most important first
        self._region_labels = []
        self._base = []          # items dimmed under a route
        self._dots = []
        self._small_dots = None
        self._dimmed = False
        self._overlay, self._bridge_items, self._char_items = [], [], []
        self._overlay_sids = set()
        self._char_sids = set()
        self._pending_fit = None
        self._relabel = QTimer(self)
        self._relabel.setSingleShot(True)
        self._relabel.setInterval(40)
        self._relabel.timeout.connect(self._update_labels)
        self.horizontalScrollBar().valueChanged.connect(lambda _: self._relabel.start())
        self.verticalScrollBar().valueChanged.connect(lambda _: self._relabel.start())

    # --- static layer -------------------------------------------------------
    def pos_of(self, sid):
        s = self.universe.systems[sid]
        return QPointF(s.x / LY * PX_PER_LY, -s.z / LY * PX_PER_LY)

    def set_universe(self, universe):
        if universe is self.universe:
            return
        self.universe = universe
        scene = self.scene()
        scene.clear()
        self._labels, self._region_labels, self._base, self._dots = {}, [], [], []
        self._small_dots, self._dimmed = None, False
        self._overlay, self._bridge_items, self._char_items, self._overlay_sids = [], [], [], set()
        dark = _dark()
        pal = QApplication.palette()
        self.setBackgroundBrush(pal.base())
        text = pal.text().color()
        kspace = [sid for sid in universe.systems if universe.is_kspace(sid)]

        local, border = QPainterPath(), QPainterPath()
        done = set()
        for a in kspace:
            for b in universe.gates.get(a, ()):
                if (b, a) in done or b not in universe.systems:
                    continue
                done.add((a, b))
                path = border if universe.systems[a].region_id != universe.systems[b].region_id else local
                path.moveTo(self.pos_of(a))
                path.lineTo(self.pos_of(b))
        for path, color in ((local, QColor(150, 158, 166, 110 if dark else 140)),
                            (border, QColor(155, 89, 182, 110 if dark else 120))):
            item = QGraphicsPathItem(path)
            item.setPen(_pen(color, 0.8))
            item.setZValue(1)
            scene.addItem(item)
            self._base.append(item)

        font = QFont()
        font.setPointSizeF(7.5)
        label_bg = QColor(pal.base().color())
        label_bg.setAlpha(200)
        for sid in kspace:
            p = self.pos_of(sid)
            dot = QGraphicsEllipseItem(-2.5, -2.5, 5, 5)
            dot.setPos(p)
            dot.setFlag(IGNORE)
            dot.setBrush(QBrush(QColor(sec_color(universe.sec(sid)) or "#888")))
            dot.setPen(QPen(Qt.PenStyle.NoPen))
            dot.setZValue(3)
            dot.setData(0, sid)
            dot.setToolTip(f"{universe.name(sid)} ({universe.sec(sid):.1f}) · {universe.region_name(sid)}")
            scene.addItem(dot)
            self._base.append(dot)
            self._dots.append(dot)
            label = Label(universe.name(sid), font, text, label_bg)
            label.setPos(p)
            label.setZValue(4)
            label.setData(0, sid)
            label.setVisible(False)
            scene.addItem(label)
            self._labels[sid] = label
        # busier systems (more gates, NPC stations) get their names placed first
        self._priority = sorted(kspace, key=lambda s: (-len(universe.gates.get(s, ())),
                                                       -len(universe.stations.get(s, ())), universe.name(s)))

        region_font = QFont()
        region_font.setPointSizeF(10)
        region_font.setBold(True)
        region_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        region_color = QColor(text)
        region_color.setAlpha(190)
        region_bg = QColor(pal.base().color())
        region_bg.setAlpha(185)
        by_region = {}
        for sid in kspace:
            by_region.setdefault(universe.systems[sid].region_id, []).append(self.pos_of(sid))
        # bigger regions get their names placed first when names would overlap
        for rid, pts in sorted(by_region.items(), key=lambda kv: -len(kv[1])):
            label = Label(universe.regions.get(rid, "").upper(), region_font, region_color, region_bg, centered=True)
            label.setPos(QPointF(sum(p.x() for p in pts) / len(pts), sum(p.y() for p in pts) / len(pts)))
            label.setZValue(5)
            scene.addItem(label)
            self._region_labels.append(label)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))
        self.fitInView(scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._update_labels()

    # --- zoom / labels --------------------------------------------------------
    def wheelEvent(self, event):
        factor = 1.2 ** (event.angleDelta().y() / 120)
        zoom = self.transform().m11() * factor
        if 0.05 < zoom < 40:
            self.scale(factor, factor)
            self._relabel.start()

    def _update_labels(self):
        """Show names that fit without overlapping, most important first."""
        zoom = self.transform().m11()
        small = zoom < SMALL_DOTS_BELOW
        if small != self._small_dots:
            self._small_dots = small
            r = 1.6 if small else 2.5
            for dot in self._dots:
                dot.setRect(-r, -r, 2 * r, 2 * r)
        view = self.viewport().rect()
        cell, grid = 80, {}

        def cells(r):
            for cx in range(r.left() // cell, r.right() // cell + 1):
                for cy in range(r.top() // cell, r.bottom() // cell + 1):
                    yield cx, cy

        def free(r):
            return view.intersects(r) and not any(r.intersects(o) for c in cells(r) for o in grid.get(c, ()))

        def take(r):
            for c in cells(r):
                grid.setdefault(c, []).append(r)

        for r in self._reserved_rects():
            take(r)
        gx, gy = LABEL_GAP
        lo, hi = REGION_FADE
        fade = 1.0 - min(max((zoom - lo) / (hi - lo), 0.0), 1.0)
        for label in self._region_labels:
            r = label.rect.toRect().translated(self.mapFromScene(label.pos())).adjusted(-gx, -gy, gx, gy)
            show = fade > 0.05 and free(r)
            label.setVisible(show)
            if show:
                label.setOpacity(fade)
                take(r)
        if not self._labels:
            return
        for label in self._labels.values():
            label.setVisible(False)
        if zoom < LABEL_ZOOM:
            return
        visible_scene = self.mapToScene(view).boundingRect()
        opacity = DIMMED_LABELS if self._dimmed else 1.0
        for sid in self._priority:
            if sid in self._overlay_sids or sid in self._char_sids:
                continue
            label = self._labels[sid]
            pos = label.pos()
            if not visible_scene.contains(pos):
                continue
            r = label.rect.toRect().translated(self.mapFromScene(pos)).adjusted(-gx, -gy, gx, gy)
            if free(r):
                label.setOpacity(opacity)
                label.setVisible(True)
                take(r)

    def _reserved_rects(self):
        """Screen rects of overlay labels (route, characters) so base names keep clear of them."""
        for item in self._overlay + self._char_items:
            if isinstance(item, Label) and item.isVisible():
                yield item.rect.toRect().translated(self.mapFromScene(item.pos()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pending_fit is not None:
            self.fit(self._pending_fit)
        self._relabel.start()

    def fit(self, rect):
        self._pending_fit = rect
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._update_labels()

    def center_on_system(self, sid, zoom=3.0):
        if not self.universe or sid not in self.universe.systems:
            return
        self._pending_fit = None
        self.resetTransform()
        self.scale(zoom, zoom)
        self.centerOn(self.pos_of(sid))
        self._update_labels()

    def mousePressEvent(self, event):
        self._pending_fit = None  # the user is taking over the view
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        sid = item.data(0) if item is not None else None
        if sid:
            self.system_menu.emit(int(sid), event.globalPos())

    # --- overlays -------------------------------------------------------------
    def _add(self, item, store):
        self.scene().addItem(item)
        store.append(item)
        return item

    def _clear(self, store):
        for item in store:
            self.scene().removeItem(item)
        store.clear()

    def _dim(self, on):
        self._dimmed = on
        for item in self._base:
            item.setOpacity(DIMMED if on else 1.0)

    def _label(self, sid, store, text, bold=False, color=None):
        font = QFont()
        font.setPointSizeF(9 if bold else 8)
        font.setBold(bold)
        bg = QColor(QApplication.palette().base().color())
        bg.setAlpha(230)
        label = Label(text, font, color or QApplication.palette().text().color(), bg, offset=(9, -9))
        label.setPos(self.pos_of(sid))
        label.setZValue(9)
        label.setData(0, sid)
        return self._add(label, store)

    def _ring(self, sid, color, store, label=None, radius=7, bold=True):
        ring = QGraphicsEllipseItem(-radius, -radius, radius * 2, radius * 2)
        ring.setPos(self.pos_of(sid))
        ring.setFlag(IGNORE)
        ring.setPen(_pen(color, 2.5))
        ring.setBrush(QBrush(QApplication.palette().base().color()))
        ring.setZValue(8)
        ring.setData(0, sid)
        self._add(ring, store)
        if label:
            self._label(sid, store, label, bold)

    def _dot(self, sid, store, color, radius=3.5):
        dot = QGraphicsEllipseItem(-radius, -radius, radius * 2, radius * 2)
        dot.setPos(self.pos_of(sid))
        dot.setFlag(IGNORE)
        dot.setBrush(QBrush(QColor(color)))
        dot.setPen(_pen("#ffffff" if _dark() else "#333333", 0.8))
        dot.setZValue(7)
        dot.setData(0, sid)
        self._add(dot, store)

    def _finish_overlay(self, sids, rect):
        self._overlay_sids = set(sids)
        self._dim(True)
        self.fit(rect)

    def clear_route(self):
        self._clear(self._overlay)
        self._overlay_sids = set()
        self._dim(False)
        self._update_labels()

    def show_route(self, steps, kind="gate"):
        """steps: [(system_id, how)] for gate routes ('start'/'gate'/'bridge') or [(system_id, ly)] for jumps."""
        self._clear(self._overlay)
        if not self.universe or not steps:
            return self.clear_route()
        sids = [s for s, _ in steps]
        name = self.universe.name
        if kind == "jump":
            path = QPainterPath()
            for a, b in zip(sids, sids[1:]):
                pa, pb = self.pos_of(a), self.pos_of(b)
                mid = (pa + pb) / 2
                dx, dy = pb.x() - pa.x(), pb.y() - pa.y()
                path.moveTo(pa)
                path.quadTo(QPointF(mid.x() - dy * 0.12, mid.y() + dx * 0.12), pb)  # gentle arc like Dotlan
            item = self._add(QGraphicsPathItem(path), self._overlay)
            item.setPen(_pen(JUMP_COLOR, 2.5))
            item.setZValue(6)
            for sid in sids:
                self._ring(sid, WAYPOINT_COLOR, self._overlay, name(sid))
        else:
            gate_path, bridge_path = QPainterPath(), QPainterPath()
            for (a, _), (b, how) in zip(steps, steps[1:]):
                target = bridge_path if how == "bridge" else gate_path
                target.moveTo(self.pos_of(a))
                target.lineTo(self.pos_of(b))
            for path, color, style in ((gate_path, ROUTE_COLOR, Qt.PenStyle.SolidLine),
                                       (bridge_path, BRIDGE_COLOR, Qt.PenStyle.DashLine)):
                item = self._add(QGraphicsPathItem(path), self._overlay)
                item.setPen(_pen(color, 4, style))
                item.setZValue(6)
            bridged = {a for (a, _), (_, how) in zip(steps, steps[1:]) if how == "bridge"}
            for sid in sids[1:-1]:
                self._dot(sid, self._overlay, sec_color(self.universe.sec(sid)) or "#888")
            for sid in sids[1:-1]:
                if sid in bridged:
                    self._ring(sid, BRIDGE_COLOR, self._overlay, name(sid), radius=5)
                else:
                    self._label(sid, self._overlay, name(sid))
            self._ring(sids[0], WAYPOINT_COLOR, self._overlay, name(sids[0]))
            self._ring(sids[-1], WAYPOINT_COLOR, self._overlay, name(sids[-1]))
        rect = QRectF()
        for sid in sids:
            rect = rect.united(QRectF(self.pos_of(sid), self.pos_of(sid)).adjusted(-1, -1, 1, 1))
        margin = max(rect.width(), rect.height()) * 0.15 + 30
        self._finish_overlay(sids, rect.adjusted(-margin, -margin, margin, margin))
        self._declutter_overlay()

    def _declutter_overlay(self):
        """Hide plain route labels (not start/end/bridges) that would overlap each other."""
        placed = []
        for item in self._overlay:
            if not isinstance(item, Label):
                continue
            r = item.rect.toRect().translated(self.mapFromScene(item.pos()))
            bold = item.font.bold()
            item.setVisible(bold or not any(r.intersects(o) for o in placed))
            if item.isVisible():
                placed.append(r)
        self._update_labels()

    def show_highlight(self, origin, sids, ly):
        """Range view: a circle of `ly` around origin and the systems in range."""
        self._clear(self._overlay)
        if not self.universe:
            return
        c = self.pos_of(origin)
        r = ly * PX_PER_LY  # projected onto the map plane; the list is the exact 3D answer
        circle = self._add(QGraphicsEllipseItem(c.x() - r, c.y() - r, 2 * r, 2 * r), self._overlay)
        circle.setPen(_pen(JUMP_COLOR, 1.5, Qt.PenStyle.DashLine))
        circle.setBrush(QBrush(QColor(77, 93, 255, 22)))
        circle.setZValue(2)
        for sid in sids:
            self._dot(sid, self._overlay, sec_color(self.universe.sec(sid)) or "#888", radius=4)
        self._ring(origin, WAYPOINT_COLOR, self._overlay, self.universe.name(origin))
        self._finish_overlay([origin], QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r).adjusted(-30, -30, 30, 30))
        # systems in range keep normal (decluttered) names
        self._overlay_sids = {origin}
        self._update_labels()

    def show_bridges(self, bridges, visible=True):
        self._clear(self._bridge_items)
        if not self.universe or not visible:
            return
        path = QPainterPath()
        for b in bridges:
            path.moveTo(self.pos_of(b["a"]))
            path.lineTo(self.pos_of(b["b"]))
        item = self._add(QGraphicsPathItem(path), self._bridge_items)
        item.setPen(_pen(BRIDGE_COLOR, 1.5, Qt.PenStyle.DashLine))
        item.setZValue(2)

    def show_characters(self, by_system, visible=True):
        """by_system: {system_id: [character names]}"""
        self._clear(self._char_items)
        self._char_sids = set()
        if not self.universe or not visible:
            return self._update_labels()
        self._char_sids = set(by_system)
        for sid, names in by_system.items():
            if sid in self.universe.systems:
                self._ring(sid, CHAR_COLOR, self._char_items, ", ".join(names), radius=5)
        self._update_labels()
