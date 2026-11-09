"""EVE map data (systems, gates, NPC stations) and the gate-route, jump and range planners.

Map data comes from CCP's static data export via Fuzzwork's CSV mirror (one ~2 MB download, then cached),
with a slower ESI-only fallback. All planning runs locally, so jump bridges and docking access can be used."""
import bz2
import csv
import gzip
import heapq
import io
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

LY = 9_460_730_472_580_800  # metres in a light year
FUZZWORK = "https://www.fuzzwork.co.uk/dump/latest/"
POCHVEN_REGION = 10000070
ZARZAKH = 30100000
WORMHOLE_REGION_MIN = 11000000
ANSIBLEX_TYPE = 35841
CYNO_BEACON_TYPE = 35840  # Pharolux Cyno Beacon
CELL = 10 * LY  # spatial grid cell for range lookups
PENALTY = 1000  # extra cost per unwanted system for the "safer" / "less secure" preferences


@dataclass
class System:
    id: int
    name: str
    region_id: int
    constellation_id: int
    x: float
    y: float
    z: float
    security: float


class Universe:
    def __init__(self, systems, regions, gates, stations, station_systems=None):
        self.systems = systems      # {system_id: System}
        self.regions = regions      # {region_id: name}
        self.gates = gates          # {system_id: set(neighbour ids)}
        self.stations = stations    # {system_id: [station names]}
        self.station_systems = station_systems or {}  # {station_id: system_id}
        self.by_name = {s.name.lower(): s.id for s in systems.values()}
        self.region_by_name = {name.lower(): rid for rid, name in regions.items()}
        self._grid = {}
        for s in systems.values():
            if self.is_kspace(s.id):
                self._grid.setdefault(self._cell(s), []).append(s.id)

    # --- lookups ----------------------------------------------------------
    def find(self, text):
        text = str(text).strip()
        if text.isdigit() and int(text) in self.systems:
            return int(text)
        return self.by_name.get(text.lower())

    def require(self, text):
        sid = self.find(text)
        if sid is None:
            raise ValueError(f"Unknown solar system: {text}")
        return sid

    def resolve_avoid(self, names):
        """System or region names -> set of system IDs. Raises ValueError for unknown names."""
        out = set()
        for name in names:
            name = name.strip()
            if not name:
                continue
            sid = self.find(name)
            if sid is not None:
                out.add(sid)
            elif name.lower() in self.region_by_name:
                rid = self.region_by_name[name.lower()]
                out.update(s.id for s in self.systems.values() if s.region_id == rid)
            else:
                raise ValueError(f"Unknown system or region: {name}")
        return out

    def name(self, sid):
        return self.systems[sid].name if sid in self.systems else f"#{sid}"

    def region_name(self, sid):
        s = self.systems.get(sid)
        return self.regions.get(s.region_id, "") if s else ""

    def sec(self, sid):
        return self.systems[sid].security

    def is_highsec(self, sid):
        return self.systems[sid].security >= 0.45  # rounds to 0.5+

    def is_kspace(self, sid):
        return self.systems[sid].region_id < WORMHOLE_REGION_MIN

    def can_jump_to(self, sid):
        """Jump drives can't target high-sec, wormhole space, Pochven or Zarzakh."""
        s = self.systems[sid]
        return (self.is_kspace(sid) and not self.is_highsec(sid) and s.region_id != POCHVEN_REGION
                and sid != ZARZAKH)

    def distance_ly(self, a, b):
        sa, sb = self.systems[a], self.systems[b]
        return math.dist((sa.x, sa.y, sa.z), (sb.x, sb.y, sb.z)) / LY

    @staticmethod
    def _cell(s):
        return int(s.x // CELL), int(s.y // CELL), int(s.z // CELL)

    def in_range(self, origin, ly, jumpable_only=True):
        """[(system_id, distance_ly)] within `ly` of origin, nearest first."""
        o = self.systems[origin]
        reach, k = ly * LY, math.ceil(ly * LY / CELL)
        cx, cy, cz = self._cell(o)
        out = []
        for dx in range(-k, k + 1):
            for dy in range(-k, k + 1):
                for dz in range(-k, k + 1):
                    for sid in self._grid.get((cx + dx, cy + dy, cz + dz), ()):
                        if sid == origin or (jumpable_only and not self.can_jump_to(sid)):
                            continue
                        s = self.systems[sid]
                        d = math.dist((o.x, o.y, o.z), (s.x, s.y, s.z))
                        if d <= reach:
                            out.append((sid, d / LY))
        return sorted(out, key=lambda t: t[1])

    # --- gate routes (with jump bridges) ------------------------------------
    @staticmethod
    def bridge_map(bridges):
        out = {}
        for a, b in bridges:
            out.setdefault(a, set()).add(b)
            out.setdefault(b, set()).add(a)
        return out

    def _entry_cost(self, sid, mode):
        if mode == "secure":
            return 1 + (0 if self.is_highsec(sid) else PENALTY)
        if mode == "insecure":
            return 1 + (PENALTY if self.is_highsec(sid) else 0)
        return 1

    def path_cost(self, path, mode):
        return sum(self._entry_cost(s, mode) for s in path[1:])

    def gate_route(self, origin, dest, mode="shortest", avoid=(), bridges=(), banned_edges=frozenset(),
                   _bmap=None):
        """Cheapest path as a list of system IDs (or None). `avoid` never applies to origin/destination."""
        bmap = _bmap if _bmap is not None else self.bridge_map(bridges)
        avoid = set(avoid) - {origin, dest}
        best, prev = {origin: 0}, {}
        heap = [(0, origin)]
        while heap:
            cost, cur = heapq.heappop(heap)
            if cur == dest:
                path = [dest]
                while path[-1] != origin:
                    path.append(prev[path[-1]])
                return path[::-1]
            if cost > best.get(cur, math.inf):
                continue
            for nxt in self.gates.get(cur, set()) | bmap.get(cur, set()):
                if nxt in avoid or (cur, nxt) in banned_edges:
                    continue
                c = cost + self._entry_cost(nxt, mode)
                if c < best.get(nxt, math.inf):
                    best[nxt], prev[nxt] = c, cur
                    heapq.heappush(heap, (c, nxt))
        return None

    def route_alternatives(self, origin, dest, mode="shortest", avoid=(), bridges=(), count=4, overlap=0.8):
        """Up to `count` loopless routes (Yen's algorithm), skipping ones that mostly overlap a better one."""
        bmap = self.bridge_map(bridges)
        first = self.gate_route(origin, dest, mode, avoid, _bmap=bmap)
        if not first:
            return []
        found, candidates, seen = [first], [], {tuple(first)}
        while len(found) < count * 3:
            last = found[-1]
            for j in range(len(last) - 1):
                root = last[:j + 1]
                banned = {(p[j], p[j + 1]) for p in found if p[:j + 1] == root}
                spur = self.gate_route(root[-1], dest, mode, set(avoid) | set(root[:-1]),
                                       banned_edges=banned, _bmap=bmap)
                if spur:
                    path = root[:-1] + spur
                    if tuple(path) not in seen:
                        seen.add(tuple(path))
                        heapq.heappush(candidates, (self.path_cost(path, mode), len(path), path))
            if not candidates:
                break
            found.append(heapq.heappop(candidates)[2])
        chosen = []
        for path in found:
            s = set(path)
            if all(len(s & set(c)) / len(s | set(c)) < overlap for c in chosen):
                chosen.append(path)
            if len(chosen) == count:
                break
        return chosen

    def jump_distances(self, origin, bridges=()):
        """{system_id: gate jumps from origin} for every reachable system (jump bridges count as one jump)."""
        bmap = self.bridge_map(bridges)
        dist, frontier = {origin: 0}, [origin]
        while frontier:
            nxt = []
            for cur in frontier:
                for n in self.gates.get(cur, set()) | bmap.get(cur, set()):
                    if n not in dist:
                        dist[n] = dist[cur] + 1
                        nxt.append(n)
            frontier = nxt
        return dist

    def describe(self, path):
        """[(system_id, how_reached)] where how_reached is 'start', 'gate' or 'bridge'."""
        out = [(path[0], "start")]
        for a, b in zip(path, path[1:]):
            out.append((b, "gate" if b in self.gates.get(a, ()) else "bridge"))
        return out

    # --- capital jumps ------------------------------------------------------
    def jump_route(self, origin, dest, ly, dockable=None, midpoints="any", avoid=()):
        """Fewest jumps, then fewest undockable midpoints ('prefer'), then shortest total distance.
        Returns [(system_id, jump distance_ly)] starting with (origin, 0)."""
        if not self.can_jump_to(dest):
            raise ValueError(f"{self.name(dest)} can't be jumped to (high-sec, Pochven, Zarzakh or wormhole).")
        dockable = dockable or (lambda sid: False)
        avoid = set(avoid) - {origin, dest}
        best, prev = {origin: (0, 0, 0.0)}, {}
        heap = [((0, 0, 0.0), origin)]
        while heap:
            cost, cur = heapq.heappop(heap)
            if cur == dest:
                path = [(dest, prev[dest][1])] if dest != origin else []
                node = dest
                while node in prev and prev[node][0] != origin:
                    node = prev[node][0]
                    path.append((node, prev[node][1]))
                return [(origin, 0.0)] + path[::-1]
            if cost > best.get(cur, (math.inf,)):
                continue
            jumps, undocked, dist = cost
            for nxt, d in self.in_range(cur, ly):
                if nxt in avoid:
                    continue
                is_mid = nxt != dest
                if is_mid and midpoints == "only" and not dockable(nxt):
                    continue
                pen = 1 if (is_mid and midpoints == "prefer" and not dockable(nxt)) else 0
                c = (jumps + 1, undocked + pen, dist + d)
                if c < best.get(nxt, (math.inf,)):
                    best[nxt], prev[nxt] = c, (cur, d)
                    heapq.heappush(heap, (c, nxt))
        return None

    def jump_route_via(self, waypoints, ly, dockable=None, midpoints="any", avoid=()):
        """Jump route through several waypoints in order. Returns ([(system_id, jump ly)], waypoint
        indexes into that list), or (None, None) if a leg can't be done."""
        route, marks = [(waypoints[0], 0.0)], [0]
        for a, b in zip(waypoints, waypoints[1:]):
            leg = self.jump_route(a, b, ly, dockable, midpoints, set(avoid) - set(waypoints))
            if leg is None:
                return None, None
            route += leg[1:]
            marks.append(len(route) - 1)
        return route, marks

    # --- persistence --------------------------------------------------------
    def to_json(self):
        pairs = sorted({(min(a, b), max(a, b)) for a, ns in self.gates.items() for b in ns})
        return {"version": 1,
                "systems": [[s.id, s.name, s.region_id, s.constellation_id, s.x, s.y, s.z, s.security]
                            for s in self.systems.values()],
                "regions": {str(k): v for k, v in self.regions.items()},
                "gates": pairs,
                "stations": {str(k): v for k, v in self.stations.items()},
                "station_systems": {str(k): v for k, v in self.station_systems.items()}}

    @classmethod
    def from_json(cls, data):
        systems = {row[0]: System(*row) for row in data["systems"]}
        gates = {}
        for a, b in data["gates"]:
            gates.setdefault(a, set()).add(b)
            gates.setdefault(b, set()).add(a)
        return cls(systems, {int(k): v for k, v in data["regions"].items()}, gates,
                   {int(k): v for k, v in data["stations"].items()},
                   {int(k): v for k, v in data.get("station_systems", {}).items()})

    # --- building the data --------------------------------------------------
    @classmethod
    def from_tables(cls, systems_csv, jumps_csv, regions_csv, stations_csv):
        """Build from the SDE CSV tables (mapSolarSystems, mapSolarSystemJumps, mapRegions, staStations)."""
        rows = lambda text: csv.DictReader(io.StringIO(text))
        systems = {}
        for r in rows(systems_csv):
            sid = int(r["solarSystemID"])
            if sid >= 31_000_000:  # wormhole and abyssal space aren't useful for routing here
                continue
            systems[sid] = System(sid, r["solarSystemName"], int(r["regionID"]), int(r["constellationID"]),
                                  float(r["x"]), float(r["y"]), float(r["z"]), float(r["security"]))
        gates = {}
        for r in rows(jumps_csv):
            a, b = int(r["fromSolarSystemID"]), int(r["toSolarSystemID"])
            if a in systems and b in systems:
                gates.setdefault(a, set()).add(b)
                gates.setdefault(b, set()).add(a)
        regions = {int(r["regionID"]): r["regionName"] for r in rows(regions_csv)}
        stations, station_systems = {}, {}
        for r in rows(stations_csv):
            sid = int(r["solarSystemID"])
            if sid in systems:
                stations.setdefault(sid, []).append(r["stationName"])
                if r.get("stationID"):
                    station_systems[int(r["stationID"])] = sid
        return cls(systems, regions, gates, stations, station_systems)

    @classmethod
    def from_fuzzwork(cls, http, progress=None):
        texts = []
        for name in ("mapSolarSystems", "mapSolarSystemJumps", "mapRegions", "staStations"):
            if progress:
                progress(f"Downloading map data: {name}…")
            r = http.get(f"{FUZZWORK}{name}.csv.bz2", timeout=120)
            r.raise_for_status()
            texts.append(bz2.decompress(r.content).decode("utf-8"))
        return cls.from_tables(*texts)

    @classmethod
    def from_esi(cls, esi, progress=None):
        """Fallback: build the k-space map from public ESI endpoints (~20k requests, several minutes)."""
        def say(msg):
            if progress:
                progress(msg)

        ids = [i for i in esi.get("/universe/systems/")[0] if i < 31_000_000]
        with ThreadPoolExecutor(16) as pool:
            say(f"Building map from ESI: {len(ids)} systems…")
            infos = list(pool.map(lambda i: esi.get(f"/universe/systems/{i}/")[0], ids))
            const_ids = sorted({s["constellation_id"] for s in infos})
            say("Building map from ESI: constellations…")
            consts = dict(zip(const_ids, pool.map(lambda c: esi.get(f"/universe/constellations/{c}/")[0], const_ids)))
            region_ids = sorted({c["region_id"] for c in consts.values()})
            regions = dict(zip(region_ids, (r["name"] for r in pool.map(
                lambda r: esi.get(f"/universe/regions/{r}/")[0], region_ids))))
            gate_ids = [g for s in infos for g in s.get("stargates", [])]
            say(f"Building map from ESI: {len(gate_ids)} stargates…")
            gate_info = list(pool.map(lambda g: esi.get(f"/universe/stargates/{g}/")[0], gate_ids))
        systems = {}
        for s in infos:
            p = s.get("position", {})
            systems[s["system_id"]] = System(s["system_id"], s["name"], consts[s["constellation_id"]]["region_id"],
                                             s["constellation_id"], p.get("x", 0.0), p.get("y", 0.0), p.get("z", 0.0),
                                             s.get("security_status", 0.0))
        gates = {}
        for g in gate_info:
            a, b = g["system_id"], g["destination"]["system_id"]
            if a in systems and b in systems:
                gates.setdefault(a, set()).add(b)
                gates.setdefault(b, set()).add(a)
        station_ids = [(st, s["system_id"]) for s in infos for st in s.get("stations", [])]
        stations = {}
        say("Building map from ESI: station names…")
        for i in range(0, len(station_ids), 1000):
            chunk = station_ids[i:i + 1000]
            names = {n["id"]: n["name"] for n in esi.request("POST", "/universe/names/",
                                                            json=[c[0] for c in chunk]).json()}
            for st, sid in chunk:
                stations.setdefault(sid, []).append(names.get(st, f"Station #{st}"))
        return cls(systems, regions, gates, stations, {st: sid for st, sid in station_ids})


def save(universe, path):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(universe.to_json(), f)


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return Universe.from_json(json.load(f))


# --- jump bridges --------------------------------------------------------------
_ANSIBLEX_NAME = re.compile(r"^\s*(.+?)\s*»\s*(.+?)(?:\s+-\s+.*)?$")
_SEPARATORS = re.compile(r"\s*(?:»|<->|<=>|-->|->|=>|\t|,)\s*")


def bridge_from_name(name, universe):
    """'1DQ1-A » 8WA-Z6 - Imperial Palace' -> (id, id), or None."""
    m = _ANSIBLEX_NAME.match(name or "")
    if not m:
        return None
    a, b = universe.find(m.group(1)), universe.find(m.group(2))
    return (a, b) if a and b and a != b else None


def parse_bridge_list(text, universe):
    """Parse a pasted bridge list: one bridge per line, e.g. 'A » B', 'A <-> B', 'A,B' or an Ansiblex name.
    Returns ([(id, id)], [lines that couldn't be understood])."""
    pairs, bad = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in _SEPARATORS.split(line) if p]
        if len(parts) >= 2:
            a = universe.find(parts[0])
            b = universe.find(re.split(r"\s+-\s+", parts[1])[0])
            if a and b and a != b:
                pairs.append((a, b))
                continue
        bad.append(line)
    return pairs, bad
