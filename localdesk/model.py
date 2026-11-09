"""Turns cached ESI data into things the UI can display. Reads only from the local database."""
import time
from datetime import datetime

from .sync import location_kind

ROMAN = ["0", "I", "II", "III", "IV", "V"]


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def fmt_time(ts, with_seconds=False):
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M" + (":%S" if with_seconds else ""), time.localtime(ts))


def fmt_time_short(ts):
    """'30 Oct 13:59' this year, '30 Oct 2025' otherwise: short enough for table columns."""
    if not ts:
        return "—"
    t = time.localtime(ts)
    return time.strftime("%d %b %H:%M" if t.tm_year == time.localtime().tm_year else "%d %b %Y", t)


def fmt_isk(v):
    if v is None:
        return "—"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:,.2f}{suffix}"
    return f"{v:,.2f}"


def fmt_sp(v):
    return "—" if v is None else f"{v:,}"


def fmt_duration(seconds):
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def display_security(sec):
    """EVE rounds security to one decimal, except that anything above 0 shows at least 0.1."""
    if sec is None:
        return None
    if 0 < sec < 0.05:
        return 0.1
    return round(sec, 1)


class DataView:
    def __init__(self, db, syncer):
        self.db = db
        self.syncer = syncer

    def get(self, cid, key, default=None):
        return self.db.cache_get(cid, key, default)

    def name(self, id_, default=None):
        if not id_:
            return default if default is not None else ""
        return self.db.get_names([id_]).get(int(id_), default if default is not None else f"#{id_}")

    def names(self, ids):
        return self.db.get_names(ids)

    def security(self, system_id):
        info = self.db.cache_get(0, f"system:{system_id}") if system_id else None
        return info.get("security_status") if info else None

    def location_name(self, location_id):
        kind = location_kind(location_id)
        if kind == "asset_safety":
            return "Asset Safety"
        if kind == "structure":
            return self.name(location_id, f"Structure #{location_id}")
        return self.name(location_id, f"Location #{location_id}")

    def location(self, cid):
        loc = self.get(cid, "location")
        if not loc:
            return {"system_id": None, "system": "—", "docked": None, "text": "—"}
        system = self.name(loc.get("solar_system_id"))
        docked = None
        if loc.get("station_id"):
            docked = self.name(loc["station_id"])
        elif loc.get("structure_id"):
            docked = self.location_name(loc["structure_id"])
        return {"system_id": loc.get("solar_system_id"), "system": system, "docked": docked,
                "text": docked or f"{system} (in space)"}

    def ship(self, cid):
        ship = self.get(cid, "ship")
        if not ship:
            return "—"
        type_name = self.name(ship.get("ship_type_id"))
        return f"{type_name} — {ship['ship_name']}" if ship.get("ship_name") else type_name

    def training(self, cid):
        queue = sorted(self.get(cid, "skillqueue") or [], key=lambda q: q.get("queue_position", 0))
        if not queue:
            return {"state": "empty", "text": "Not training", "current_end": None, "end": None}
        dated = [q for q in queue if q.get("finish_date")]
        first = f"{self.name(queue[0]['skill_id'])} {ROMAN[queue[0]['finished_level']]}"
        if not dated:
            return {"state": "paused", "text": f"Paused: {first}", "current_end": None, "end": None}
        now = time.time()
        end = max(parse_time(q["finish_date"]) for q in dated)
        current = next((q for q in dated if parse_time(q["finish_date"]) > now), None)
        if current is None:
            return {"state": "done", "text": "Queue finished", "current_end": None, "end": end}
        return {"state": "training",
                "text": f"{self.name(current['skill_id'])} {ROMAN[current['finished_level']]}",
                "current_end": parse_time(current["finish_date"]), "end": end}

    def summary(self, cid):
        ch = self.db.get_character(cid) or {}
        info = self.get(cid, "info") or {}
        skills = self.get(cid, "skills") or {}
        loc = self.location(cid)
        return {
            "id": cid,
            "name": ch.get("name") or self.name(cid),
            "corporation": self.name(info.get("corporation_id"), ""),
            "alliance": self.name(info.get("alliance_id"), ""),
            "security_status": info.get("security_status"),
            "location": loc,
            "security": self.security(loc["system_id"]),
            "ship": self.ship(cid),
            "wallet": self.get(cid, "wallet"),
            "sp": skills.get("total_sp"),
            "unallocated_sp": skills.get("unallocated_sp"),
            "training": self.training(cid),
            "unread": (self.get(cid, "mail_labels") or {}).get("total_unread_count"),
            "online": (self.get(cid, "online") or {}).get("online"),
            "last_sync": ch.get("last_sync"),
            "scopes": ch.get("scopes", []),
        }

    def skill_group_of(self):
        out = {}
        for group, types in (self.db.cache_get(0, "skilltree") or {}).items():
            for t in types:
                out[t] = group
        return out

    def asset_tree(self, cids):
        """Returns location roots: [{location_id, name, items: [node], total}], with
        node = {item, owner, name, value, total, children}. Values use CCP's average market price."""
        prices = self.syncer.price_map()
        roots = {}
        for cid in cids:
            assets = self.get(cid, "assets") or []
            custom = self.get(cid, "asset_names") or {}
            type_names = self.names({a["type_id"] for a in assets})
            nodes = {}
            for a in assets:
                tname = type_names.get(a["type_id"], f"Type #{a['type_id']}")
                cname = custom.get(str(a["item_id"]))
                unit = 0.0 if a.get("is_blueprint_copy") else prices.get(a["type_id"], 0.0)
                nodes[a["item_id"]] = {"item": a, "owner": cid, "type_name": tname,
                                       "name": f"{cname} ({tname})" if cname else tname,
                                       "value": unit * a["quantity"], "children": []}
            for node in nodes.values():
                parent = nodes.get(node["item"]["location_id"])
                if parent:
                    parent["children"].append(node)
                else:
                    lid = node["item"]["location_id"]
                    root = roots.setdefault(lid, {"location_id": lid, "name": self.location_name(lid), "items": []})
                    root["items"].append(node)

        def total(node):
            node["total"] = node["value"] + sum(total(c) for c in node["children"])
            return node["total"]

        for root in roots.values():
            root["total"] = sum(total(n) for n in root["items"])
        return sorted(roots.values(), key=lambda r: r["name"].lower())

    @staticmethod
    def flatten_assets(roots):
        """Yield (node, path, root location id) for every item; path is 'Location › Container › …'."""
        def walk(node, path, root):
            yield node, path, root
            for child in node["children"]:
                yield from walk(child, f"{path} › {node['name']}", root)
        for root in roots:
            for node in root["items"]:
                yield from walk(node, root["name"], root["location_id"])

    def location_system(self, location_id, universe=None):
        """Solar system of an asset location (system, NPC station or player structure), or None."""
        kind = location_kind(location_id)
        if kind == "system":
            return location_id
        if kind == "station":
            if universe is not None and location_id in universe.station_systems:
                return universe.station_systems[location_id]
            return (self.db.cache_get(0, f"station:{location_id}") or {}).get("system_id")
        if kind == "structure":
            info = self.db.cache_get(0, f"structure:{location_id}") or {}
            if info.get("solar_system_id"):
                return info["solar_system_id"]
            for ch in self.db.list_characters():
                for s in self.db.cache_get(ch["character_id"], "access_structures") or []:
                    if s["id"] == location_id:
                        return s.get("system_id")
        return None
