import pytest

from localdesk import navigation as nav
from localdesk.universe import LY, ANSIBLEX_TYPE, System, Universe, bridge_from_name, parse_bridge_list

from conftest import FakeESI, add_char

# A tiny map. High-sec H1-H2-H3, a low-sec shortcut H1-L1-H3, then null-sec N1..N5 in a line with a
# detour N1-N6-N7-N5. Positions are along the x axis, in light years.
LAYOUT = {
    1: ("H1", 0.9, 0), 2: ("H2", 0.8, 1), 3: ("H3", 0.5, 2), 4: ("L1", 0.3, 1),
    11: ("N1", -0.5, 3), 12: ("N2", -0.4, 6), 13: ("N3", -0.3, 9), 14: ("N4", -0.2, 12), 15: ("N5", -0.6, 15),
    16: ("N6", -0.1, 4), 17: ("N7", -0.1, 5),
}
GATES = [(1, 2), (2, 3), (1, 4), (4, 3), (3, 11), (11, 12), (12, 13), (13, 14), (14, 15), (11, 16), (16, 17),
         (17, 15)]


def make_universe():
    systems = {sid: System(sid, name, 10000001 if sec >= 0.45 else 10000002, 20000001, x * LY, 0.0, 0.0, sec)
               for sid, (name, sec, x) in LAYOUT.items()}
    gates = {}
    for a, b in GATES:
        gates.setdefault(a, set()).add(b)
        gates.setdefault(b, set()).add(a)
    return Universe(systems, {10000001: "Highland", 10000002: "Nullia"}, gates, {13: ["N3 - Station"]})


@pytest.fixture
def u():
    return make_universe()


def names(u, path):
    return [u.name(s) for s in path]


def test_gate_routes_modes_avoid_and_bridges(u):
    assert names(u, u.gate_route(1, 3)) in (["H1", "H2", "H3"], ["H1", "L1", "H3"])
    assert names(u, u.gate_route(1, 3, "secure")) == ["H1", "H2", "H3"]
    assert names(u, u.gate_route(1, 3, "insecure")) == ["H1", "L1", "H3"]
    assert names(u, u.gate_route(11, 15)) == ["N1", "N6", "N7", "N5"]
    assert names(u, u.gate_route(11, 15, avoid=[16])) == ["N1", "N2", "N3", "N4", "N5"]
    route = u.gate_route(1, 15, "secure", bridges=[(3, 15)])
    assert names(u, route) == ["H1", "H2", "H3", "N5"]
    assert [how for _, how in u.describe(route)] == ["start", "gate", "gate", "bridge"]


def test_route_alternatives_are_distinct(u):
    alts = u.route_alternatives(11, 15, count=3)
    assert [names(u, a) for a in alts] == [["N1", "N6", "N7", "N5"], ["N1", "N2", "N3", "N4", "N5"]]
    assert u.route_alternatives(11, 15, avoid=[16, 12]) == []


def test_jump_planner(u):
    # N1 (3 LY) to N5 (15 LY) with 5 LY range: midpoints sit at 4, 5, 6, 9 and 12, so 4 jumps is the minimum
    route = u.jump_route(11, 15, 5.0)
    assert len(route) - 1 == 4
    assert all(d <= 5.0 for _, d in route) and abs(sum(d for _, d in route) - 12.0) < 1e-6
    assert len(u.jump_route(11, 15, 12.0)) - 1 == 1
    # only dockable midpoints: N3 is the only system with a station
    docked = u.jump_route(11, 15, 6.0, dockable=lambda s: bool(u.stations.get(s)), midpoints="only")
    assert names(u, [s for s, _ in docked]) == ["N1", "N3", "N5"]
    assert u.jump_route(11, 15, 2.0, dockable=lambda s: False, midpoints="only") is None
    with pytest.raises(ValueError, match="can't be jumped to"):
        u.jump_route(11, 3, 7.0)  # H3 is high-sec


def test_in_range(u):
    found = dict(u.in_range(13, 3.0))
    assert set(found) == {12, 14}
    assert abs(found[12] - 3.0) < 1e-9
    assert 3 not in dict(u.in_range(11, 1.5))           # high-sec excluded
    assert 3 in dict(u.in_range(11, 1.5, jumpable_only=False))


def test_round_trip_and_tables(u):
    again = Universe.from_json(u.to_json())
    assert names(again, again.gate_route(11, 15)) == ["N1", "N6", "N7", "N5"]
    systems = ("regionID,constellationID,solarSystemID,solarSystemName,x,y,z,security\n"
               "10000002,20000001,30000001,Tanoo,1e16,0,0,0.85\n10000002,20000001,30000002,Lashesih,2e16,0,0,0.75\n"
               "11000001,21000001,31000001,J100001,0,0,0,-1.0\n")
    jumps = "fromSolarSystemID,toSolarSystemID\n30000001,30000002\n30000002,30000001\n"
    regions = "regionID,regionName\n10000002,Derelik\n"
    stations = "stationID,solarSystemID,stationName\n60000001,30000001,Tanoo V - Moon 1\n"
    t = Universe.from_tables(systems, jumps, regions, stations)
    assert set(t.systems) == {30000001, 30000002}  # wormholes skipped
    assert t.gates[30000001] == {30000002} and t.region_name(30000001) == "Derelik"
    assert t.stations[30000001] == ["Tanoo V - Moon 1"]


def test_bridge_parsing(u):
    assert bridge_from_name("N1 » N5 - Home Sweet Home", u) == (11, 15)
    assert bridge_from_name("N1 - Keepstar", u) is None
    pairs, bad = parse_bridge_list("N1 » N5\nn2 <-> n4\nN3,N6\n# comment\nN1 » Nowhere\n", u)
    assert pairs == [(11, 15), (12, 14), (13, 16)] and bad == ["N1 » Nowhere"]


def test_scan_access_and_bridges(db, u):
    from localdesk.sync import Syncer, S_SEARCH_STRUCTURES, S_STRUCTURES
    cid = 90000010
    add_char(db, cid, "Scout", scopes=[S_SEARCH_STRUCTURES, S_STRUCTURES])

    def search(params, _):
        return {"structure": [1000000000001, 1000000000002]} if params["search"] == " » " else \
            {"structure": [1000000000003, 1000000000004]}

    from localdesk.esi import ESIError

    def forbidden(p, j):
        raise ESIError(403, "Forbidden")

    esi = FakeESI({
        ("GET", f"/characters/{cid}/search/"): search,
        ("GET", "/universe/structures/1000000000001/"): {"name": "N1 » N5 - Bridge", "solar_system_id": 11,
                                                         "type_id": ANSIBLEX_TYPE},
        ("GET", "/universe/structures/1000000000002/"): {"name": "N5 » N1 - Bridge back", "solar_system_id": 15,
                                                         "type_id": ANSIBLEX_TYPE},
        ("GET", "/universe/structures/1000000000003/"): {"name": "N2 - Fort", "solar_system_id": 12,
                                                         "type_id": 35833},
        ("GET", "/universe/structures/1000000000004/"): forbidden,
    })
    out = Syncer(db, esi).scan_access(cid)
    assert {s["id"] for s in out} == {1000000000001, 1000000000002, 1000000000003}
    bridges = nav.all_bridges(db, u, [cid])
    assert [(b["a"], b["b"]) for b in bridges] == [(11, 15)]  # both directions collapse into one bridge
    nav.set_manual_bridges(db, [(13, 14), (14, 13)])
    assert len(nav.all_bridges(db, u, [cid])) == 2
    assert nav.jump_range(7.0, 5) == 7.0 and nav.jump_range(7.0, 4) == pytest.approx(6.3)


def test_fatigue_plan():
    # first jump of 5 LY in a carrier from zero fatigue: 6 min timer, 60 min fatigue
    plan = nav.fatigue_plan([5.0, 5.0], factor=1.0)
    assert plan[0]["reactivation"] == 6 * 60 and plan[0]["after"] == 60 * 60
    # second jump right after the timer: 54 min left, x6 -> 324 min, capped at 5 h;
    # timer = max(6 min, 54 min / 10 = 5.4 min) = 6 min
    assert plan[1]["before"] == 54 * 60
    assert plan[1]["after"] == nav.FATIGUE_CAP and plan[1]["reactivation"] == 6 * 60
    # a third jump with 4h54m left: the timer is now driven by fatigue (29.4 min)
    third = nav.fatigue_plan([5.0, 5.0, 5.0])[2]
    assert abs(third["reactivation"] - (nav.FATIGUE_CAP - 360) / 10) < 1e-9
    # jump freighter: only 10% of the distance counts
    jf = nav.fatigue_plan([10.0], factor=0.1)
    assert jf[0]["reactivation"] == 2 * 60 and jf[0]["after"] == 20 * 60


def test_fuel():
    # 5.5 LY in a carrier needing 3,000/LY with Jump Fuel Conservation IV: 5.5 * 3000 * 0.6 = 9,900
    m = nav.fuel_reduction("Carrier", 4)
    assert nav.fuel_for_jump(5.5, 3000, m) == 9900
    # jump freighter: JFC V and Jump Freighters V -> 0.5 * 0.5
    assert nav.fuel_reduction("Jump Freighter", 5, 5) == pytest.approx(0.25)
    assert nav.fuel_reduction("Carrier", 5, 5) == pytest.approx(0.5)  # JF skill ignored for other hulls
    assert nav.fuel_for_jump(1.23, 1000, 1.0) == 1230  # no rounding up on exact values
    assert nav.fuel_for_jump(1.231, 1000, 1.0) == 1231


def test_jump_ship_catalog(db):
    from localdesk.sync import Syncer

    def t(type_id, name, rng, fuel, isotope, published=True):
        return {"type_id": type_id, "name": name, "published": published, "dogma_attributes": [
            {"attribute_id": 867, "value": rng}, {"attribute_id": 868, "value": fuel},
            {"attribute_id": 866, "value": isotope}]}

    esi = FakeESI({
        ("GET", "/universe/categories/6/"): {"groups": [547, 902, 25]},
        ("GET", "/universe/groups/547/"): {"name": "Carrier", "types": [23757, 99999]},
        ("GET", "/universe/groups/902/"): {"name": "Jump Freighter", "types": [28844]},
        ("GET", "/universe/groups/25/"): {"name": "Frigate", "types": [587]},
        ("GET", "/universe/types/23757/"): t(23757, "Archon", 3.5, 3000, 16274),
        ("GET", "/universe/types/99999/"): t(99999, "Unreleased Carrier", 3.5, 3000, 16274, published=False),
        ("GET", "/universe/types/28844/"): t(28844, "Rhea", 5.0, 10000, 17888),
        ("POST", "/universe/names/"): lambda p, ids: [{"id": i, "name": {16274: "Helium Isotopes",
                                                                         17888: "Nitrogen Isotopes"}[i],
                                                        "category": "inventory_type"} for i in ids],
    })
    ships = Syncer(db, esi).jump_ship_catalog()
    assert [(s["name"], s["range_base"], s["fuel_per_ly"], s["factor"]) for s in ships] == [
        ("Archon", 3.5, 3000, 1.0), ("Rhea", 5.0, 10000, 0.1)]
    assert db.get_names([16274])[16274] == "Helium Isotopes"
    assert not any(c[1] == "/universe/types/587/" for c in esi.calls)  # frigates never fetched


def test_avoid_regions_and_waypoints(u, db):
    assert u.resolve_avoid(["N2", "highland"]) == {12, 1, 2, 3}
    with pytest.raises(ValueError, match="Unknown system or region"):
        u.resolve_avoid(["Nowhere"])
    # N1 -> N3 -> N5 in two legs with a 6 LY range; waypoint marks point at N3 and N5
    route, marks = u.jump_route_via([11, 13, 15], 6.0)
    assert [u.name(s) for s, _ in route][0] == "N1" and [route[m][0] for m in marks] == [11, 13, 15]
    # avoiding a waypoint is ignored for that waypoint
    route2, _ = u.jump_route_via([11, 13, 15], 6.0, avoid={13})
    assert route2 is not None
    assert u.jump_route_via([11, 15], 1.0) == (None, None)
    nav.save_route(db, "home", ["N1", "N5"], ["Highland"])
    assert nav.saved_routes(db) == {"home": {"waypoints": ["N1", "N5"], "avoid": ["Highland"]}}
    nav.delete_route(db, "home")
    assert nav.saved_routes(db) == {}


def test_beacons_and_incursions(db, u):
    from localdesk.sync import Syncer
    cid = 90000011
    add_char(db, cid, "Cyno")
    db.cache_put(cid, "access_structures", [{"id": 1, "name": "N3 - Beacon", "system_id": 13, "type_id": 35840},
                                            {"id": 2, "name": "N2 - Fort", "system_id": 12, "type_id": 35833}],
                 9e12)
    assert [b["system_id"] for b in nav.cyno_beacons(db, [cid])] == [13]
    syncer = Syncer(db, FakeESI({("GET", "/incursions/"): [{"infested_solar_systems": [12, 14]}]}))
    assert nav.incursion_systems(syncer) == {12, 14}


def test_grouped_ships():
    ships = [{"name": n, "group": g} for n, g in (("Thanatos", "Carrier"), ("Archon", "Carrier"),
                                                  ("Rhea", "Jump Freighter"), ("Rorqual", "Capital Industrial Ship"),
                                                  ("Panther", "Black Ops"), ("Odd", "Mystery"))]
    grouped = nav.grouped_ships(ships)
    assert [g for g, _ in grouped] == ["Black Ops", "Jump Freighter", "Capital Industrial (Rorqual)", "Carrier",
                                       "Mystery"]
    assert [s["name"] for s in grouped[3][1]] == ["Archon", "Thanatos"]


def test_jump_distances_and_station_systems(u, db):
    d = u.jump_distances(1)
    assert d[1] == 0 and d[3] == 2 and d[11] == 3 and d[15] == 6   # H1-H3-N1-N6-N7-N5
    assert u.jump_distances(1, bridges=[(3, 15)])[15] == 3       # a bridge shortcut counts as one jump
    systems = ("regionID,constellationID,solarSystemID,solarSystemName,x,y,z,security\n"
               "10000002,20000001,30000001,Tanoo,1e16,0,0,0.85\n")
    stations = "stationID,solarSystemID,stationName\n60000001,30000001,Tanoo V - Moon 1\n"
    t = Universe.from_tables(systems, "fromSolarSystemID,toSolarSystemID\n", "regionID,regionName\n", stations)
    assert t.station_systems == {60000001: 30000001}
    assert Universe.from_json(t.to_json()).station_systems == {60000001: 30000001}
    # older saved map data without station ids still loads
    old = t.to_json()
    del old["station_systems"]
    assert Universe.from_json(old).station_systems == {}


def test_location_system(db):
    import time
    from localdesk.model import DataView
    view = DataView(db, None)
    t = Universe({30000001: System(30000001, "Tanoo", 1, 1, 0, 0, 0, 0.9)}, {}, {}, {}, {60000001: 30000001})
    assert view.location_system(30000001, t) == 30000001                 # items in space
    assert view.location_system(60000001, t) == 30000001                 # NPC station from map data
    db.cache_put(0, "station:60000002", {"system_id": 30000002}, time.time() + 999)
    assert view.location_system(60000002, t) == 30000002                 # station looked up via ESI
    db.cache_put(0, "structure:1035000000001", {"solar_system_id": 30000003}, time.time() + 999)
    assert view.location_system(1035000000001, t) == 30000003            # structure
    assert view.location_system(1035000000002, t) is None                # unknown structure
