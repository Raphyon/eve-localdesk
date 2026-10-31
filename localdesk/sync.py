"""Pulls data from ESI into the local cache. Everything here blocks; call it from worker threads.

ESI's own cache timers (the Expires header) are respected, so pressing refresh often is harmless."""
import time
from concurrent.futures import ThreadPoolExecutor

from .datasets import DATASETS
from .esi import ESIError

S_LOCATION = "esi-location.read_location.v1"
S_SHIP = "esi-location.read_ship_type.v1"
S_ONLINE = "esi-location.read_online.v1"
S_WALLET = "esi-wallet.read_character_wallet.v1"
S_SKILLS = "esi-skills.read_skills.v1"
S_QUEUE = "esi-skills.read_skillqueue.v1"
S_CLONES = "esi-clones.read_clones.v1"
S_IMPLANTS = "esi-clones.read_implants.v1"
S_ASSETS = "esi-assets.read_assets.v1"
S_CONTACTS = "esi-characters.read_contacts.v1"
S_WRITE_CONTACTS = "esi-characters.write_contacts.v1"
S_MAIL = "esi-mail.read_mail.v1"
S_SEND_MAIL = "esi-mail.send_mail.v1"
S_ORGANIZE_MAIL = "esi-mail.organize_mail.v1"
S_STRUCTURES = "esi-universe.read_structures.v1"
S_WAYPOINT = "esi-ui.write_waypoint.v1"
S_SEARCH_STRUCTURES = "esi-search.search_structures.v1"

# Structure names are "<System> - <Name>" and Ansiblex gates "<A> » <B> - <Name>", so these searches
# return the structures (and jump bridges) a character has access to. ESI wants 3+ characters.
ACCESS_SEARCH_TERMS = (" » ", " - ")
ACCESS_TTL = 6 * 3600
STRUCTURE_TTL = 7 * 86400
STRUCTURE_RETRY = 86400  # wait a day before asking again about a structure no character could see

# (cache key, path, scope, paginated)
CHAR_ENDPOINTS = [
    ("location", "/characters/{cid}/location/", S_LOCATION, False),
    ("ship", "/characters/{cid}/ship/", S_SHIP, False),
    ("online", "/characters/{cid}/online/", S_ONLINE, False),
    ("wallet", "/characters/{cid}/wallet/", S_WALLET, False),
    ("journal", "/characters/{cid}/wallet/journal/", S_WALLET, True),
    ("skills", "/characters/{cid}/skills/", S_SKILLS, False),
    ("skillqueue", "/characters/{cid}/skillqueue/", S_QUEUE, False),
    ("attributes", "/characters/{cid}/attributes/", S_SKILLS, False),
    ("clones", "/characters/{cid}/clones/", S_CLONES, False),
    ("implants", "/characters/{cid}/implants/", S_IMPLANTS, False),
    ("assets", "/characters/{cid}/assets/", S_ASSETS, True),
    ("contacts", "/characters/{cid}/contacts/", S_CONTACTS, True),
    ("mail_labels", "/characters/{cid}/mail/labels/", S_MAIL, False),
    ("mail_lists", "/characters/{cid}/mail/lists/", S_MAIL, False),
]

NO_ACCESS_TTL = 6 * 3600  # how long to wait before retrying a corp endpoint we lack the role for
KILLMAIL_LIMIT = 100
MAIL_PAGES = 6  # ESI returns 50 mails per page
LONG_TTL = 30 * 86400
MAX_NAME_ID = 2_147_483_647  # /universe/names/ only accepts 32-bit IDs


def is_celestial(id_):
    """Planets, moons, belts and other celestials share the 40,000,000-49,999,999 ID range."""
    return 40_000_000 <= id_ < 50_000_000


def location_kind(location_id):
    if 60_000_000 <= location_id < 64_000_000:
        return "station"
    if 30_000_000 <= location_id < 33_000_000:
        return "system"
    if location_id == 2004:
        return "asset_safety"
    if location_id > 1_000_000_000_000:
        return "structure"
    return "other"


class Syncer:
    def __init__(self, db, esi):
        self.db = db
        self.esi = esi
        self.last_errors = {}  # character_id -> [error strings] from the most recent sync

    def has_scope(self, character_id, scope):
        ch = self.db.get_character(character_id)
        return bool(ch) and scope in ch["scopes"]

    def fetch(self, character_id, key, path, *, auth=True, paged=False, params=None, force=False, ttl=None):
        """Return cached data if ESI says it's still fresh, otherwise fetch and store it."""
        row = self.db.cache_row(character_id, key)
        if row and not force and row["expires_at"] > time.time():
            return row["data"]
        getter = self.esi.get_paged if paged else self.esi.get
        data, expires = getter(path, character_id if auth else None, params)
        if ttl:
            expires = max(expires, time.time() + ttl)
        self.db.cache_put(character_id, key, data, expires)
        return data

    # --- full refreshes ---------------------------------------------------
    def sync_public(self, force=False):
        self.fetch(0, "prices", "/markets/prices/", auth=False, force=force)
        self.skill_groups()
        self.jump_ship_catalog()

    def sync_character(self, cid, force=False, progress=None):
        """Refresh everything the character's scopes allow. Returns a list of error strings."""
        ch = self.db.get_character(cid)
        errors = []

        def step(label, fn):
            if progress:
                progress(f"{ch['name']}: {label}…")
            try:
                return fn()
            except Exception as e:  # keep going; one failing endpoint shouldn't stop the rest
                errors.append(f"{label}: {e}")
                return None

        if step("token", lambda: self.esi.access_token(cid)) is None:
            return errors
        step("public info", lambda: self.fetch(cid, "info", f"/characters/{cid}/", auth=False, force=force))
        for key, path, scope, paged in CHAR_ENDPOINTS:
            if scope in ch["scopes"]:
                step(key, lambda key=key, path=path, paged=paged:
                     self.fetch(cid, key, path.format(cid=cid), paged=paged, force=force))
        if S_MAIL in ch["scopes"]:
            step("mail", lambda: self.fetch_mail(cid, force))
        if S_ASSETS in ch["scopes"]:
            step("asset names", lambda: self.fetch_asset_names(cid))
        for ds in DATASETS:
            if ds.scope is None or ds.scope in ch["scopes"]:
                step(ds.title.lower(), lambda ds=ds: self.sync_dataset(cid, ds, force))
        if S_SEARCH_STRUCTURES in ch["scopes"] and S_STRUCTURES in ch["scopes"]:
            step("structure access", lambda: self.scan_access(cid, force))
        loc = self.db.cache_get(cid, "location") or {}
        if loc.get("solar_system_id"):
            step("system info", lambda: self.system(loc["solar_system_id"]))
        step("names", lambda: self.resolve_for_character(cid))
        self.db.mark_synced(cid)
        self.last_errors[cid] = errors
        return errors

    def sync_dataset(self, cid, ds, force=False):
        corp = (self.db.cache_get(cid, "info") or {}).get("corporation_id")
        if ds.corp and (not corp or corp < 2_000_000):  # NPC corps have no corp data
            return None
        path = ds.path.format(cid=cid, corp=corp)
        raw_key = f"{ds.key}:raw" if ds.hook else ds.key
        try:
            data = self.fetch(cid, raw_key, path, paged=ds.paged, params=ds.params, force=force)
        except ESIError as e:
            if ds.corp and e.status in (401, 403):
                # Missing corp role: remember "no access" for a while instead of hitting ESI's error limit.
                self.db.cache_put(cid, raw_key, {"no_access": True}, time.time() + NO_ACCESS_TTL)
                if ds.hook:
                    self.db.cache_put(cid, ds.key, {"no_access": True}, time.time() + NO_ACCESS_TTL)
                return None
            raise
        if ds.hook and not (isinstance(data, dict) and data.get("no_access")):
            row = self.db.cache_row(cid, raw_key)
            self.db.cache_put(cid, ds.key, getattr(self, ds.hook)(cid, data), row["expires_at"])
        return data

    def dataset_rows(self, cid, ds):
        """Rows for display, or None when the character lacks access (missing scope or corp role)."""
        if ds.scope and not self.has_scope(cid, ds.scope):
            return None
        data = self.db.cache_get(cid, ds.key)
        if isinstance(data, dict) and data.get("no_access"):
            return None
        if ds.single:
            return [data] if data else []
        return data or []

    # --- docking access & jump bridges -------------------------------------
    def scan_access(self, cid, force=False):
        """Structures (incl. Ansiblex jump gates) this character can access, via ESI structure search."""
        row = self.db.cache_row(cid, "access_structures")
        if row and not force and row["expires_at"] > time.time():
            return row["data"]
        ids = set()
        for term in ACCESS_SEARCH_TERMS:
            data, _ = self.esi.get(f"/characters/{cid}/search/", cid,
                                   {"categories": "structure", "search": term, "strict": "false"})
            ids.update((data or {}).get("structure", []))
        ids = sorted(ids)
        with ThreadPoolExecutor(8) as pool:
            infos = list(pool.map(lambda sid: self.structure_info(cid, sid), ids))
        out = [{"id": sid, "name": info["name"], "system_id": info.get("solar_system_id"),
                "type_id": info.get("type_id")} for sid, info in zip(ids, infos) if info]
        self.db.put_names([(s["id"], s["name"], "structure") for s in out])
        self.db.cache_put(cid, "access_structures", out, time.time() + ACCESS_TTL)
        return out

    def structure_info(self, cid, structure_id):
        key = f"structure:{structure_id}"
        row = self.db.cache_row(0, key)
        if row and row["expires_at"] > time.time():
            return row["data"]
        try:
            data, _ = self.esi.get(f"/universe/structures/{structure_id}/", cid)
        except ESIError as e:
            if e.status in (401, 403, 404):
                return None
            raise
        self.db.cache_put(0, key, data, time.time() + STRUCTURE_TTL)
        return data

    # --- dataset hooks ------------------------------------------------------
    def hook_mining_value(self, cid, rows):
        prices = self.price_map()
        return [dict(r, value=prices.get(r["type_id"], 0.0) * r.get("quantity", 0)) for r in rows]

    def hook_killmails(self, cid, recent):
        """ESI only lists IDs; details are public and never change, so they are cached forever."""
        prices = self.price_map()
        out = []
        for km in sorted(recent, key=lambda k: k["killmail_id"], reverse=True)[:KILLMAIL_LIMIT]:
            kid = km["killmail_id"]
            try:
                d = self.fetch(0, f"km:{kid}", f"/killmails/{kid}/{km['killmail_hash']}/", auth=False,
                               ttl=3650 * 86400)
            except ESIError:
                continue
            victim, attackers = d.get("victim", {}), d.get("attackers", [])
            final = next((a for a in attackers if a.get("final_blow")), {})
            out.append({
                "killmail_id": kid, "time": d.get("killmail_time"),
                "loss": victim.get("character_id") == cid,
                "victim_id": victim.get("character_id") or victim.get("corporation_id"),
                "victim_corp_id": victim.get("corporation_id"), "ship_type_id": victim.get("ship_type_id"),
                "system_id": d.get("solar_system_id"), "attackers": len(attackers),
                "final_blow_id": final.get("character_id") or final.get("corporation_id"),
                "ship_value": prices.get(victim.get("ship_type_id"), 0.0),
            })
        return out

    def hook_planets(self, cid, colonies):
        out = []
        for p in colonies:
            pid = p["planet_id"]
            try:
                layout = self.fetch(cid, f"planet:{pid}", f"/characters/{cid}/planets/{pid}/")
            except ESIError:
                layout = {}
            expiries = [pin["expiry_time"] for pin in layout.get("pins", [])
                        if "extractor_details" in pin and pin.get("expiry_time")]
            out.append(dict(p, extractors=len(expiries), extractor_expiry=min(expiries) if expiries else None))
        return out

    def fetch_mail(self, cid, force=False):
        row = self.db.cache_row(cid, "mail")
        if row and not force and row["expires_at"] > time.time():
            return row["data"]
        mails, last_id, expires = [], None, None
        for _ in range(MAIL_PAGES):
            page, exp = self.esi.get(f"/characters/{cid}/mail/", cid, {"last_mail_id": last_id} if last_id else None)
            expires = expires or exp
            if not page:
                break
            mails.extend(page)
            last_id = min(m["mail_id"] for m in page)
            if len(page) < 50:
                break
        self.db.cache_put(cid, "mail", mails, expires or time.time() + 30)
        return mails

    def fetch_asset_names(self, cid):
        """Custom names of ships and containers. Only re-fetched when the asset list changed."""
        assets = self.db.cache_row(cid, "assets")
        names = self.db.cache_row(cid, "asset_names")
        if not assets or (names and names["fetched_at"] >= assets["fetched_at"]):
            return
        ids = [a["item_id"] for a in assets["data"] if a.get("is_singleton")]
        result = {}
        for i in range(0, len(ids), 1000):
            try:
                r = self.esi.request("POST", f"/characters/{cid}/assets/names/", cid, json=ids[i:i + 1000])
            except ESIError:
                continue
            for n in r.json():
                if n.get("name") and n["name"] != "None":
                    result[str(n["item_id"])] = n["name"]
        self.db.cache_put(cid, "asset_names", result, assets["expires_at"])

    # --- names ------------------------------------------------------------
    def resolve(self, ids):
        ids = {int(i) for i in ids if i and 0 < int(i) <= MAX_NAME_ID}
        entries = self.db.get_name_entries(ids)
        # Planets/moons aren't supported by /universe/names/; older versions cached them as "unknown".
        missing = sorted(i for i in ids if i not in entries or (is_celestial(i) and entries[i][1] == "unknown"))
        others = [i for i in missing if not is_celestial(i)]
        for i in range(0, len(others), 1000):
            self._resolve_batch(others[i:i + 1000])
        for celestial_id in missing:
            if is_celestial(celestial_id):
                self._resolve_celestial(celestial_id)

    def _resolve_celestial(self, celestial_id):
        for kind in ("planets", "moons"):
            try:
                data, _ = self.esi.get(f"/universe/{kind}/{celestial_id}/")
            except ESIError as e:
                if e.status in (400, 404):
                    continue
                raise
            self.db.put_names([(celestial_id, data["name"], kind[:-1])])
            return
        self.db.put_names([(celestial_id, f"Unknown #{celestial_id}", "unknown")])

    def _resolve_batch(self, batch):
        try:
            r = self.esi.request("POST", "/universe/names/", json=batch)
        except ESIError as e:
            # One bad ID fails the whole request, so split until the bad one is isolated.
            if e.status != 404:
                raise
            if len(batch) == 1:
                self.db.put_names([(batch[0], f"Unknown #{batch[0]}", "unknown")])
                return
            mid = len(batch) // 2
            self._resolve_batch(batch[:mid])
            self._resolve_batch(batch[mid:])
            return
        self.db.put_names([(n["id"], n["name"], n["category"]) for n in r.json()])

    def resolve_structures(self, cid, structure_ids):
        """Name player structures. EVE only tells characters with docking access, so try what the access
        scans already found, then the owning character, then your other characters. Refusals are
        remembered for a day (each one counts against ESI's error limit). Names you gave a structure
        yourself are kept until EVE reveals the real one."""
        entries = self.db.get_name_entries(structure_ids)
        todo = [s for s in structure_ids
                if s not in entries or entries[s][1] in ("structure_unknown", "structure_custom")]
        if not todo:
            return
        characters = self.db.list_characters()
        known = {}
        for ch in characters:
            for s in self.db.cache_get(ch["character_id"], "access_structures") or []:
                known[s["id"]] = s
        askers = [c["character_id"] for c in sorted(characters, key=lambda c: c["character_id"] != cid)
                  if S_STRUCTURES in c["scopes"]]
        systems = set()
        for sid in todo:
            if sid in known:
                self.db.put_names([(sid, known[sid]["name"], "structure")])
                systems.add(known[sid].get("system_id"))
                continue
            denied = self.db.cache_row(0, f"structure_denied:{sid}")
            if denied and denied["expires_at"] > time.time():
                continue
            info = None
            for asker in askers:
                info = self.structure_info(asker, sid)
                if info:
                    break
            if info:
                self.db.put_names([(sid, info["name"], "structure")])
                systems.add(info.get("solar_system_id"))
                continue
            if askers:
                self.db.cache_put(0, f"structure_denied:{sid}", True, time.time() + STRUCTURE_RETRY)
            if entries.get(sid, (None, None))[1] != "structure_custom":
                label = (f"Unknown structure #{sid} (no docking access)" if askers
                         else f"Structure #{sid}")
                self.db.put_names([(sid, label, "structure_unknown")])
        self.resolve(systems)

    def resolve_for_character(self, cid):
        def get(key, default=None):
            return self.db.cache_get(cid, key, default)

        ids, structures = set(), set()

        def add_location(lid):
            if not lid:
                return
            kind = location_kind(lid)
            if kind == "structure":
                structures.add(lid)
            elif kind in ("station", "system", "other"):
                ids.add(lid)

        info = get("info", {})
        ids.update([info.get("corporation_id"), info.get("alliance_id")])
        loc = get("location", {})
        ids.update([loc.get("solar_system_id"), loc.get("station_id")])
        add_location(loc.get("structure_id"))
        ids.add(get("ship", {}).get("ship_type_id"))
        ids.update(s["skill_id"] for s in get("skills", {}).get("skills", []))
        ids.update(q["skill_id"] for q in get("skillqueue", []))
        clones = get("clones", {})
        add_location((clones.get("home_location") or {}).get("location_id"))
        for jc in clones.get("jump_clones", []):
            add_location(jc.get("location_id"))
            ids.update(jc.get("implants", []))
        ids.update(get("implants", []))
        assets = get("assets", [])
        item_ids = {a["item_id"] for a in assets}
        for a in assets:
            ids.add(a["type_id"])
            if a["location_id"] not in item_ids:
                add_location(a["location_id"])
        ids.update(c["contact_id"] for c in get("contacts", []))
        lists = get("mail_lists", [])
        self.db.put_names([(m["mailing_list_id"], m["name"], "mailing_list") for m in lists])
        list_ids = {m["mailing_list_id"] for m in lists}
        for m in get("mail", []):
            ids.add(m.get("from"))
            ids.update(r["recipient_id"] for r in m.get("recipients", []) if r.get("recipient_type") != "mailing_list")
        for j in get("journal", []):
            ids.update([j.get("first_party_id"), j.get("second_party_id")])
        for ds in DATASETS:
            rows = self.dataset_rows(cid, ds) or []
            if ds.extra_ids:
                for row in rows:
                    ids.update(ds.extra_ids(row))
            for col in ds.cols:
                if col.kind not in ("name", "type", "location"):
                    continue
                for row in rows:
                    value = col.value(row)
                    if isinstance(value, int):
                        (add_location if col.kind == "location" else ids.add)(value)
        self.resolve(ids - list_ids)
        if structures:
            self.resolve_structures(cid, structures)

    # --- static-ish public data -------------------------------------------
    def system(self, system_id):
        return self.fetch(0, f"system:{system_id}", f"/universe/systems/{system_id}/", auth=False, ttl=LONG_TTL)

    def systems(self, ids):
        ids = list(dict.fromkeys(ids))
        with ThreadPoolExecutor(8) as pool:
            return dict(zip(ids, pool.map(self.system, ids)))

    def skill_groups(self, force=False):
        """{group name: [skill type ids]} for the Skills category (16). Cached for a month."""
        row = self.db.cache_row(0, "skilltree")
        if row and not force and row["expires_at"] > time.time():
            return row["data"]
        category, _ = self.esi.get("/universe/categories/16/")
        tree = {}
        with ThreadPoolExecutor(8) as pool:
            for group in pool.map(lambda g: self.esi.get(f"/universe/groups/{g}/")[0], category["groups"]):
                if group.get("published", True):
                    tree[group["name"]] = group["types"]
        self.db.cache_put(0, "skilltree", tree, time.time() + LONG_TTL)
        return tree

    def jump_ship_catalog(self, force=False):
        """Every jump-capable hull with its base jump range, fuel per LY and isotope type, from ESI's
        dogma data. Cached for a month."""
        from .navigation import ATTR_FUEL_AMOUNT, ATTR_FUEL_TYPE, ATTR_RANGE, JUMP_GROUPS
        row = self.db.cache_row(0, "jump_ships")
        if row and not force and row["expires_at"] > time.time():
            return row["data"]
        category, _ = self.esi.get("/universe/categories/6/")
        with ThreadPoolExecutor(8) as pool:
            groups = [g for g in pool.map(lambda g: self.esi.get(f"/universe/groups/{g}/")[0], category["groups"])
                      if g.get("name") in JUMP_GROUPS]
            type_group = {t: g["name"] for g in groups for t in g.get("types", [])}
            types = list(pool.map(lambda t: self.esi.get(f"/universe/types/{t}/")[0], type_group))
        ships = []
        for t in types:
            attrs = {a["attribute_id"]: a["value"] for a in t.get("dogma_attributes", [])}
            if not t.get("published") or not attrs.get(ATTR_RANGE):
                continue
            group = type_group[t["type_id"]]
            ships.append({"type_id": t["type_id"], "name": t["name"], "group": group,
                          "range_base": attrs[ATTR_RANGE], "fuel_per_ly": attrs.get(ATTR_FUEL_AMOUNT, 0),
                          "fuel_type": int(attrs[ATTR_FUEL_TYPE]) if attrs.get(ATTR_FUEL_TYPE) else None,
                          "factor": JUMP_GROUPS[group]})
        ships.sort(key=lambda s: (s["group"], s["name"]))
        self.resolve({s["fuel_type"] for s in ships if s["fuel_type"]})
        self.db.cache_put(0, "jump_ships", ships, time.time() + LONG_TTL)
        return ships

    def price_map(self):
        return {p["type_id"]: p.get("average_price") or p.get("adjusted_price") or 0.0
                for p in self.db.cache_get(0, "prices", [])}

    # --- navigation -------------------------------------------------------
    def lookup_ids(self, names):
        names = [n.strip() for n in names if n and n.strip()]
        if not names:
            return {}
        try:
            return self.esi.request("POST", "/universe/ids/", json=names).json() or {}
        except ESIError as e:
            if e.status == 404:
                return {}
            raise

    def plan_route(self, origin, destination, flag="shortest", avoid=()):
        avoid = [a for a in avoid if a.strip()]
        found = self.lookup_ids([origin, destination, *avoid])
        by_name = {s["name"].lower(): s["id"] for s in found.get("systems", [])}

        def system_id(name):
            name = str(name).strip()
            if name.isdigit():
                return int(name)
            if name.lower() not in by_name:
                raise ValueError(f"Unknown solar system: {name}")
            return by_name[name.lower()]

        params = {"flag": flag}
        if avoid:
            params["avoid"] = ",".join(str(system_id(a)) for a in avoid)
        route, _ = self.esi.get(f"/route/{system_id(origin)}/{system_id(destination)}/", params=params)
        info = self.systems(route)
        return [{"id": s, "name": info[s]["name"], "security": info[s]["security_status"]} for s in route]

    def set_waypoint(self, cid, system_id, clear_others=True):
        self.esi.request("POST", "/ui/autopilot/waypoint/", cid, params={
            "add_to_beginning": "false",
            "clear_other_waypoints": "true" if clear_others else "false",
            "destination_id": system_id,
        })

    # --- mail -------------------------------------------------------------
    def mail_body(self, cid, mail_id):
        return self.fetch(cid, f"mailbody:{mail_id}", f"/characters/{cid}/mail/{mail_id}/", ttl=3650 * 86400)

    def mark_mail_read(self, cid, mail_id):
        self.esi.request("PUT", f"/characters/{cid}/mail/{mail_id}/", cid, json={"read": True})
        row = self.db.cache_row(cid, "mail")
        if row:
            for m in row["data"]:
                if m["mail_id"] == mail_id:
                    m["is_read"] = True
            self.db.cache_put(cid, "mail", row["data"], row["expires_at"])

    def send_mail(self, cid, to_names, subject, body_html):
        lists = {m["name"].lower(): m["mailing_list_id"] for m in self.db.cache_get(cid, "mail_lists", [])}
        found = {}
        for category, rtype in (("characters", "character"), ("corporations", "corporation"),
                                ("alliances", "alliance")):
            for entry in self.lookup_ids([n for n in to_names if n.strip().lower() not in lists]).get(category, []):
                found[entry["name"].lower()] = (entry["id"], rtype)
        recipients, missing = [], []
        for name in to_names:
            key = name.strip().lower()
            if key in lists:
                recipients.append({"recipient_id": lists[key], "recipient_type": "mailing_list"})
            elif key in found:
                recipients.append({"recipient_id": found[key][0], "recipient_type": found[key][1]})
            else:
                missing.append(name.strip())
        if missing:
            raise ValueError("Could not find recipient(s): " + ", ".join(missing))
        if not recipients:
            raise ValueError("No recipients given")
        r = self.esi.request("POST", f"/characters/{cid}/mail/", cid, json={
            "approved_cost": 0, "body": body_html, "recipients": recipients, "subject": subject})
        return r.json()
