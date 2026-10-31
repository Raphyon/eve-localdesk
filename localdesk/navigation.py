"""Navigation helpers shared by the planners: jump ranges, jump bridges and docking access."""
import json
import math

from .universe import ANSIBLEX_TYPE, CYNO_BEACON_TYPE, bridge_from_name

JDC_SKILL = 21611  # Jump Drive Calibration: +20% jump range per level
JFC_SKILL = 21610  # Jump Fuel Conservation: -10% jump fuel per level
JF_SKILL = 29029   # Jump Freighters: -10% jump fuel per level (jump freighters only)

# Ship groups with jump drives, and the share of each light year that counts towards jump fatigue.
JUMP_GROUPS = {"Titan": 1.0, "Supercarrier": 1.0, "Carrier": 1.0, "Dreadnought": 1.0, "Lancer Dreadnought": 1.0,
               "Force Auxiliary": 1.0, "Black Ops": 0.25, "Jump Freighter": 0.1, "Capital Industrial Ship": 0.1}
ATTR_FUEL_TYPE, ATTR_RANGE, ATTR_FUEL_AMOUNT = 866, 867, 868  # dogma attributes on the ship type
# Order of the class headings in the ship list
GROUP_ORDER = ["Black Ops", "Jump Freighter", "Capital Industrial Ship", "Carrier", "Force Auxiliary",
               "Dreadnought", "Lancer Dreadnought", "Supercarrier", "Titan"]
GROUP_LABELS = {"Capital Industrial Ship": "Capital Industrial (Rorqual)"}


def grouped_ships(ships):
    """[(class label, [ships sorted by name])] in GROUP_ORDER, unknown classes last."""
    groups = {}
    for ship in ships:
        groups.setdefault(ship["group"], []).append(ship)
    order = sorted(groups, key=lambda g: (GROUP_ORDER.index(g) if g in GROUP_ORDER else len(GROUP_ORDER), g))
    return [(GROUP_LABELS.get(g, g), sorted(groups[g], key=lambda s: s["name"])) for g in order]

# (name, max range with Jump Drive Calibration V, fatigue factor). CCP rebalances these now and then;
# the range can be overridden in the planner. The fatigue factor is the share of each light year that
# counts towards jump fatigue (Black Ops -75%, Jump Freighters and the Rorqual -90%).
JUMP_SHIPS = [
    ("Carrier / Dreadnought / Force Auxiliary", 7.0, 1.0),
    ("Supercarrier", 6.0, 1.0),
    ("Titan", 6.0, 1.0),
    ("Black Ops", 8.0, 0.25),
    ("Jump Freighter", 10.0, 0.1),
    ("Rorqual", 10.0, 0.1),
]
FATIGUE_CAP = 5 * 3600  # seconds


def jump_range(max_range, jdc_level):
    return max_range / 2 * (1 + 0.2 * jdc_level)


def fatigue_plan(jumps_ly, factor=1.0, start_fatigue=0.0):
    """Jump fatigue along a route, assuming each jump is made as soon as the jump timer allows.

    Per jump (all in seconds): reactivation = max(1 + effective LY minutes, fatigue before / 10);
    fatigue after = min(max(10 * (1 + effective LY) minutes, fatigue before * (1 + effective LY)), 5 h).
    Fatigue drains in real time while waiting for the next jump."""
    fatigue, out = max(0.0, start_fatigue), []
    for ly in jumps_ly:
        eff = 1 + ly * factor
        reactivation = max(eff * 60, fatigue / 10)
        after = min(max(10 * 60 * eff, fatigue * eff), FATIGUE_CAP)
        out.append({"before": fatigue, "reactivation": reactivation, "after": after})
        fatigue = max(0.0, after - reactivation)
    return out


def current_fatigue(db, character_id, now):
    """Seconds of jump fatigue the character has right now, from ESI (0 if unknown)."""
    from .model import parse_time
    data = db.cache_get(character_id, "fatigue") or {}
    expires = parse_time(data.get("jump_fatigue_expire_date")) if isinstance(data, dict) else None
    return max(0.0, expires - now) if expires else 0.0


def skill_level(db, character_id, skill_id):
    for s in (db.cache_get(character_id, "skills") or {}).get("skills", []):
        if s["skill_id"] == skill_id:
            return s["active_skill_level"]
    return None


def jdc_level(db, character_id):
    return skill_level(db, character_id, JDC_SKILL)


def fallback_ships():
    """Used until the ship catalogue has been fetched from ESI (no fuel data)."""
    return [{"type_id": None, "name": name, "group": name, "range_base": max_range / 2, "fuel_per_ly": 0,
             "fuel_type": None, "factor": factor} for name, max_range, factor in JUMP_SHIPS]


def fuel_reduction(group, jfc_level, jf_level=0, other_pct=0.0):
    """Multiplier on base fuel need from skills and bonuses."""
    m = (1 - 0.1 * jfc_level) * (1 - other_pct / 100)
    if group == "Jump Freighter":
        m *= 1 - 0.1 * jf_level
    return max(m, 0.0)


def fuel_for_jump(ly, fuel_per_ly, multiplier):
    return math.ceil(ly * fuel_per_ly * multiplier - 1e-9)


def accessible_structures(db, character_ids):
    """{structure_id: {name, system_id, type_id, characters: [ids]}} the characters can see/dock at via ESI."""
    out = {}
    for cid in character_ids:
        for s in db.cache_get(cid, "access_structures") or []:
            entry = out.setdefault(s["id"], dict(s, characters=[]))
            entry["characters"].append(cid)
    return out


def manual_bridges(db):
    return [tuple(p) for p in json.loads(db.get_setting("manual_bridges", "[]"))]


def set_manual_bridges(db, pairs):
    unique = sorted({(min(a, b), max(a, b)) for a, b in pairs})
    db.set_setting("manual_bridges", json.dumps(unique))


def all_bridges(db, universe, character_ids):
    """[{a, b, name, source}] from Ansiblex gates your characters can access plus imported ones."""
    found = {}
    for s in accessible_structures(db, character_ids).values():
        if s.get("type_id") == ANSIBLEX_TYPE:
            pair = bridge_from_name(s["name"], universe)
            if pair:
                found.setdefault(frozenset(pair), {"a": pair[0], "b": pair[1], "name": s["name"], "source": "ESI"})
    for a, b in manual_bridges(db):
        if a in universe.systems and b in universe.systems:
            found.setdefault(frozenset((a, b)), {"a": a, "b": b, "name": "", "source": "Imported"})
    return list(found.values())


def cyno_beacons(db, character_ids):
    """Pharolux cyno beacons the characters can access: [{id, name, system_id, characters}]."""
    return [s for s in accessible_structures(db, character_ids).values() if s.get("type_id") == CYNO_BEACON_TYPE]


def incursion_systems(syncer):
    """System IDs currently infested by incursions (public ESI data, cached a few minutes)."""
    data = syncer.fetch(0, "incursions", "/incursions/", auth=False)
    return {sid for inc in data or [] for sid in inc.get("infested_solar_systems", [])}


def saved_routes(db):
    return json.loads(db.get_setting("saved_jump_routes", "{}"))


def save_route(db, name, waypoints, avoid):
    routes = saved_routes(db)
    routes[name] = {"waypoints": list(waypoints), "avoid": list(avoid)}
    db.set_setting("saved_jump_routes", json.dumps(routes))


def delete_route(db, name):
    routes = saved_routes(db)
    routes.pop(name, None)
    db.set_setting("saved_jump_routes", json.dumps(routes))
