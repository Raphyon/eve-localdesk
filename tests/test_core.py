import hashlib
import threading
import time
import urllib.parse
import urllib.request

import pytest

from localdesk import contacts_sync, eve_settings, sso
from localdesk.model import DataView, display_security, fmt_isk
from localdesk.sync import Syncer

from conftest import FakeESI, add_char, make_jwt


# --- SSO --------------------------------------------------------------------
def test_character_from_token():
    token = make_jwt({"sub": "CHARACTER:EVE:2112345678", "name": "Test Pilot", "owner": "abc",
                      "scp": ["esi-skills.read_skills.v1", "publicData"]})
    info = sso.character_from_token(token)
    assert info == {"character_id": 2112345678, "name": "Test Pilot", "owner_hash": "abc",
                    "scopes": ["esi-skills.read_skills.v1", "publicData"]}
    assert sso.character_from_token(make_jwt({"sub": "CHARACTER:EVE:1", "scp": "publicData"}))["scopes"] == ["publicData"]


def test_login_flow_uses_pkce(monkeypatch):
    """Drive the whole login: browser redirect -> local callback -> token exchange."""
    captured = {}

    def fake_token_request(data):
        captured["token_request"] = data
        return {"access_token": make_jwt({"sub": "CHARACTER:EVE:42", "name": "Alt", "scp": ["publicData"]}),
                "refresh_token": "refresh", "expires_in": 1199}

    monkeypatch.setattr(sso, "_token_request", fake_token_request)

    def fake_browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        captured["authorize"] = q

        def hit_callback():
            time.sleep(0.2)
            urllib.request.urlopen(f"{q['redirect_uri'][0]}?code=abc&state={q['state'][0]}").read()

        threading.Thread(target=hit_callback, daemon=True).start()

    tokens, info = sso.login("my-client-id", port=18642, open_browser=fake_browser, timeout=10)
    auth = captured["authorize"]
    assert auth["code_challenge_method"] == ["S256"]
    assert auth["redirect_uri"] == ["http://localhost:18642/callback"]
    verifier = captured["token_request"]["code_verifier"]
    expected = sso._b64url(hashlib.sha256(verifier.encode()).digest())
    assert auth["code_challenge"] == [expected]
    assert "client_secret" not in captured["token_request"]
    assert info["character_id"] == 42 and tokens["refresh_token"] == "refresh"


def test_login_rejects_wrong_state(monkeypatch):
    def fake_browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        threading.Thread(target=lambda: (time.sleep(0.2), urllib.request.urlopen(
            f"{q['redirect_uri'][0]}?code=abc&state=evil").read()), daemon=True).start()

    with pytest.raises(sso.SSOError, match="state"):
        sso.login("id", port=18643, open_browser=fake_browser, timeout=10)


# --- contacts sync ------------------------------------------------------------
def c(cid, standing, ctype="character"):
    return {"contact_id": cid, "contact_type": ctype, "standing": standing}


def test_plan_modes():
    source = [c(1, 10), c(2, -10), c(3, 5), c(100, 10)]
    target = [c(2, -5), c(4, 0), c(101, 10)]
    own = [100, 101, 200]
    add = contacts_sync.plan(source, target, 200, "add", exclude=own)
    assert {(x.action, x.contact_id) for x in add} == {("add", 1), ("add", 3)}
    upd = contacts_sync.plan(source, target, 200, "update", exclude=own)
    assert ("update", 2) in {(x.action, x.contact_id) for x in upd}
    mirror = contacts_sync.plan(source, target, 200, "mirror", exclude=own)
    actions = {(x.action, x.contact_id) for x in mirror}
    assert ("remove", 4) in actions
    assert ("remove", 101) not in actions  # own character protected


def test_plan_adds_alts_but_not_self():
    extra = {100: ("character", 10.0), 200: ("character", 10.0)}
    changes = contacts_sync.plan([], [], 200, "add", extra=extra)
    assert [(x.action, x.contact_id) for x in changes] == [("add", 100)]


def test_batches_respect_esi_limits():
    changes = [contacts_sync.ContactChange("add", i, "character", 10.0) for i in range(250)]
    changes += [contacts_sync.ContactChange("add", 1000 + i, "character", -10.0) for i in range(5)]
    changes += [contacts_sync.ContactChange("remove", 2000 + i, "character", 0.0) for i in range(45)]
    b = contacts_sync.batches(changes)
    assert [(m, s, len(ids)) for m, s, ids in b] == [
        ("POST", -10.0, 5), ("POST", 10.0, 100), ("POST", 10.0, 100), ("POST", 10.0, 50),
        ("DELETE", None, 20), ("DELETE", None, 20), ("DELETE", None, 5)]


def test_apply_retries_individually_when_batch_fails():
    from localdesk.esi import ESIError
    bad = 3

    class ESI:
        def __init__(self):
            self.calls = []

        def request(self, method, path, cid, params=None, json=None):
            self.calls.append((method, json))
            if json and bad in json:
                raise ESIError(400, "cannot add")

    esi = ESI()
    changes = [contacts_sync.ContactChange("add", i, "character", 10.0) for i in (1, 2, 3)]
    applied, failures = contacts_sync.apply(esi, 9, changes)
    assert applied == 2 and [f[0] for f in failures] == [3]


# --- client settings files ----------------------------------------------------
def test_settings_copy_backup_restore(tmp_path):
    profile = tmp_path / "CCP/EVE/c_eve_sharedcache_tq_tranquility/settings_Default"
    profile.mkdir(parents=True)
    (profile / "core_char_111.dat").write_bytes(b"main-layout")
    (profile / "core_char_222.dat").write_bytes(b"alt-layout")
    (profile / "core_char__.dat").write_bytes(b"template")
    (profile / "core_user_555.dat").write_bytes(b"account")
    assert eve_settings.find_profiles([tmp_path / "CCP/EVE"]) == [profile]
    chars, users = eve_settings.scan(profile)
    assert [f.owner_id for f in chars] == [111, 222] and [f.owner_id for f in users] == [555]

    new_target = eve_settings.char_file(profile, 333)
    assert not new_target.exists
    backups = tmp_path / "backups"
    bdir = eve_settings.copy_settings(profile / "core_char_111.dat",
                                      [profile / "core_char_222.dat", new_target.path], backups)
    assert (profile / "core_char_222.dat").read_bytes() == b"main-layout"
    assert (profile / "core_char_333.dat").read_bytes() == b"main-layout"
    assert (bdir / "core_char_222.dat").read_bytes() == b"alt-layout"

    listed = eve_settings.list_backups(backups)
    assert len(listed) == 1
    eve_settings.restore(listed[0]["path"], backups)
    assert (profile / "core_char_222.dat").read_bytes() == b"alt-layout"
    assert len(eve_settings.list_backups(backups)) == 2  # restore backs up current state first


def test_copy_refuses_self_only(tmp_path):
    f = tmp_path / "core_char_1.dat"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        eve_settings.copy_settings(f, [f], tmp_path / "b")


# --- sync + model -------------------------------------------------------------
def test_sync_and_asset_tree(db):
    add_char(db, 90000001, "Main")
    cid = 90000001
    assets = [
        {"item_id": 1, "type_id": 587, "location_id": 60003760, "location_type": "station",
         "location_flag": "Hangar", "quantity": 1, "is_singleton": True},
        {"item_id": 2, "type_id": 34, "location_id": 1, "location_type": "item",
         "location_flag": "Cargo", "quantity": 1000, "is_singleton": False},
        {"item_id": 3, "type_id": 34, "location_id": 1035466617946, "location_type": "item",
         "location_flag": "Hangar", "quantity": 10, "is_singleton": False},
        {"item_id": 4, "type_id": 999, "location_id": 60003760, "location_type": "station",
         "location_flag": "Hangar", "quantity": 1, "is_singleton": True, "is_blueprint_copy": True},
    ]
    names = {587: "Rifter", 34: "Tritanium", 999: "Rifter Blueprint", 60003760: "Jita IV - Moon 4",
             90000001: "Main", 1000: "Corp"}
    routes = {
        ("GET", f"/characters/{cid}/"): {"corporation_id": 1000, "security_status": 1.5},
        ("GET", f"/characters/{cid}/assets/"): assets,
        ("POST", f"/characters/{cid}/assets/names/"): lambda p, ids: [
            {"item_id": i, "name": "My Rifter" if i == 1 else "None"} for i in ids],
        ("GET", f"/characters/{cid}/contacts/"): [],
        ("GET", f"/characters/{cid}/corporationhistory/"): [{"corporation_id": 1000, "start_date": "2020-01-01T00:00:00Z"}],
        ("GET", f"/characters/{cid}/mail/labels/"): {"labels": [], "total_unread_count": 2},
        ("GET", f"/characters/{cid}/mail/lists/"): [],
        ("GET", f"/characters/{cid}/mail/"): lambda p, j: [] if p else [
            {"mail_id": 5, "from": 1000, "subject": "hi", "timestamp": "2026-10-01T10:00:00Z"}],
        ("POST", "/universe/names/"): lambda p, ids: [
            {"id": i, "name": names[i], "category": "x"} for i in ids if i in names] if all(
            i in names for i in ids) else (_ for _ in ()).throw(__import__("localdesk.esi").esi.ESIError(404, "bad")),
        ("GET", "/universe/structures/1035466617946/"): {"name": "Perimeter - Tranquility Trading Tower",
                                                          "solar_system_id": None},
    }
    esi = FakeESI(routes)
    syncer = Syncer(db, esi)
    db.cache_put(0, "prices", [{"type_id": 587, "average_price": 500000.0}, {"type_id": 34, "average_price": 4.0},
                               {"type_id": 999, "average_price": 1e6}], time.time() + 999)
    errors = syncer.sync_character(cid)
    assert errors == []
    view = DataView(db, syncer)
    roots = {r["name"]: r for r in view.asset_tree([cid])}
    jita = roots["Jita IV - Moon 4"]
    rifter = next(n for n in jita["items"] if n["item"]["item_id"] == 1)
    assert rifter["name"] == "My Rifter (Rifter)"
    assert rifter["total"] == 500000 + 4000
    assert jita["total"] == 504000  # BPC valued at 0
    # structure without docking access falls back to placeholder; with structure scope missing it isn't queried
    assert any("Structure" in name for name in roots)
    assert view.summary(cid)["unread"] == 2
    assert view.summary(cid)["corporation"] == "Corp"


def test_resolve_isolates_bad_ids(db):
    from localdesk.esi import ESIError

    def names(params, ids):
        if 666 in ids:
            raise ESIError(404, "invalid")
        return [{"id": i, "name": f"N{i}", "category": "character"} for i in ids]

    esi = FakeESI({("POST", "/universe/names/"): names})
    Syncer(db, esi).resolve([1, 2, 3, 666, 5])
    got = db.get_names([1, 2, 3, 666, 5])
    assert got[1] == "N1" and got[666].startswith("Unknown")


def test_plan_route(db):
    esi = FakeESI({
        ("POST", "/universe/ids/"): {"systems": [{"id": 30000142, "name": "Jita"}, {"id": 30002187, "name": "Amarr"},
                                                 {"id": 30045342, "name": "Uedama"}]},
        ("GET", "/route/30000142/30002187/"): [30000142, 30000144, 30002187],
        ("GET", "/universe/systems/30000142/"): {"name": "Jita", "security_status": 0.9459},
        ("GET", "/universe/systems/30000144/"): {"name": "Perimeter", "security_status": 0.95},
        ("GET", "/universe/systems/30002187/"): {"name": "Amarr", "security_status": 1.0},
    })
    route = Syncer(db, esi).plan_route("jita", "Amarr", "secure", ["Uedama"])
    assert [r["name"] for r in route] == ["Jita", "Perimeter", "Amarr"]
    route_call = next(c for c in esi.calls if c[1].startswith("/route/"))
    assert route_call[3] == {"flag": "secure", "avoid": "30045342"}
    with pytest.raises(ValueError):
        Syncer(db, esi).plan_route("Nowhere", "Amarr")


def test_formatting():
    assert fmt_isk(1_234_567_890) == "1.23B"
    assert display_security(0.04) == 0.1 and display_security(-0.3) == -0.3 and display_security(0.46) == 0.5


def test_datasets_hooks_and_corp_no_access(db):
    from localdesk.datasets import BY_KEY
    cid = 90000002
    add_char(db, cid, "Indy", scopes=["esi-killmails.read_killmails.v1", "esi-planets.manage_planets.v1",
                                      "esi-corporations.read_structures.v1"])
    db.cache_put(cid, "info", {"corporation_id": 98000001}, time.time() + 999)
    from localdesk.esi import ESIError

    def structures(p, j):
        raise ESIError(403, "Character does not have required role(s)")

    esi = FakeESI({
        ("GET", f"/characters/{cid}/killmails/recent/"): [{"killmail_id": 5, "killmail_hash": "h"}],
        ("GET", "/killmails/5/h/"): {"killmail_time": "2026-10-01T00:00:00Z", "solar_system_id": 30000142,
                                    "victim": {"character_id": cid, "corporation_id": 98000001, "ship_type_id": 587},
                                    "attackers": [{"character_id": 7, "final_blow": True}, {"character_id": 8}]},
        ("GET", f"/characters/{cid}/planets/"): [{"planet_id": 40000001, "solar_system_id": 30000142,
                                                  "planet_type": "barren", "upgrade_level": 4, "num_pins": 9}],
        ("GET", f"/characters/{cid}/planets/40000001/"): {"pins": [
            {"extractor_details": {}, "expiry_time": "2026-11-02T00:00:00Z"},
            {"extractor_details": {}, "expiry_time": "2026-11-01T00:00:00Z"}, {"type_id": 1}]},
        ("GET", "/corporations/98000001/structures/"): structures,
    })
    syncer = Syncer(db, esi)
    for key in ("killmails", "planets", "corp_structures"):
        syncer.sync_dataset(cid, BY_KEY[key])
    kms = syncer.dataset_rows(cid, BY_KEY["killmails"])
    assert kms[0]["loss"] and kms[0]["attackers"] == 2 and kms[0]["final_blow_id"] == 7
    assert syncer.dataset_rows(cid, BY_KEY["planets"])[0]["extractor_expiry"] == "2026-11-01T00:00:00Z"
    assert syncer.dataset_rows(cid, BY_KEY["corp_structures"]) is None  # no role -> no access, not an error
    calls = len(esi.calls)
    syncer.sync_dataset(cid, BY_KEY["corp_structures"])
    assert len(esi.calls) == calls  # 403 is remembered, so ESI isn't hammered
    assert syncer.dataset_rows(cid, BY_KEY["contracts"]) is None  # scope not granted


def test_alerts(db):
    from localdesk import alerts
    cid = 90000003
    add_char(db, cid, "Alerty", scopes=["esi-skills.read_skillqueue.v1", "esi-planets.manage_planets.v1",
                                        "esi-corporations.read_structures.v1", "esi-mail.read_mail.v1"])
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    db.cache_put(cid, "skillqueue", [{"skill_id": 1, "finished_level": 5, "queue_position": 0,
                                      "start_date": iso(now - 10), "finish_date": iso(now + 3600)}], now + 99)
    db.cache_put(cid, "planets", [{"planet_id": 40000001, "extractor_expiry": iso(now - 60)}], now + 99)
    db.cache_put(cid, "info", {"corporation_id": 98000001}, now + 99)
    db.cache_put(cid, "corp_structures", [{"structure_id": 1, "name": "Keepstar", "fuel_expires": iso(now + 86400)}],
                 now + 99)
    db.cache_put(cid, "mail_labels", {"total_unread_count": 3}, now + 99)
    syncer = Syncer(db, FakeESI({}))
    found = alerts.compute(db, syncer, DataView(db, syncer))
    texts = [a["text"] for a in found]
    assert found[0]["level"] == "critical" and "Keepstar" in texts[0]
    assert any("Skill queue ends" in t for t in texts)
    assert any("Extractors stopped" in t for t in texts)
    assert any("3 unread" in t for t in texts)


def test_eft_export(db):
    from localdesk.ui.data_browser import to_eft
    db.put_names([(587, "Rifter", "t"), (1, "Damage Control II", "t"), (2, "1MN Afterburner II", "t"),
                  (3, "200mm AutoCannon II", "t"), (4, "EMP S", "t")])
    fit = {"name": "PvP", "ship_type_id": 587, "items": [
        {"type_id": 3, "flag": "HiSlot1", "quantity": 1}, {"type_id": 3, "flag": "HiSlot0", "quantity": 1},
        {"type_id": 1, "flag": "LoSlot0", "quantity": 1}, {"type_id": 2, "flag": "MedSlot0", "quantity": 1},
        {"type_id": 4, "flag": "Cargo", "quantity": 400}]}
    assert to_eft(DataView(db, None), fit) == (
        "[Rifter, PvP]\nDamage Control II\n\n1MN Afterburner II\n\n200mm AutoCannon II\n200mm AutoCannon II\n\n"
        "EMP S x400\n")


def test_planets_and_moons_resolve_via_their_own_endpoints(db):
    from localdesk.esi import ESIError

    def names(params, ids):
        if any(40_000_000 <= i < 50_000_000 for i in ids):
            raise ESIError(404, "Ensure all IDs are valid")
        return [{"id": i, "name": f"N{i}", "category": "character"} for i in ids]

    db.put_names([(40300905, "Unknown #40300905", "unknown")])  # left behind by older versions
    esi = FakeESI({("POST", "/universe/names/"): names,
                   ("GET", "/universe/planets/40300905/"): {"name": "Raphyon's Rock IV"},
                   ("GET", "/universe/moons/40300906/"): {"name": "Raphyon's Rock IV - Moon 1"}})
    Syncer(db, esi).resolve([90000001, 40300905, 40300906])
    got = db.get_names([90000001, 40300905, 40300906])
    assert got == {90000001: "N90000001", 40300905: "Raphyon's Rock IV", 40300906: "Raphyon's Rock IV - Moon 1"}


def test_long_dead_extractors_are_not_alerted(db):
    from localdesk import alerts
    cid = 90000004
    add_char(db, cid, "Raphyon", scopes=["esi-planets.manage_planets.v1"])
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    db.cache_put(cid, "planets", [{"planet_id": 40300905, "extractor_expiry": "2023-02-03T15:57:00Z"},
                                  {"planet_id": 40300906, "extractor_expiry": iso(now - 3600)}], now + 99)
    syncer = Syncer(db, FakeESI({}))
    texts = [a["text"] for a in alerts.compute(db, syncer, DataView(db, syncer))]
    assert len([t for t in texts if "Extractors stopped" in t]) == 1


def test_match_accounts_by_save_time():
    from pathlib import Path
    F = eve_settings.SettingsFile
    users = [F(Path("u1"), "user", 1, 1000.0), F(Path("u2"), "user", 2, 5000.0), F(Path("u3"), "user", 3, 9000.0)]
    chars = [F(Path("c"), "char", 11, 1003.0), F(Path("c"), "char", 22, 4990.0), F(Path("c"), "char", 33, 7000.0),
             F(Path("c"), "char", 44)]  # 33: saved too far from any account file; 44: no file yet
    assert eve_settings.match_accounts(chars, users) == {1: [11], 2: [22], 3: []}


def test_structure_names_from_other_characters_and_custom_names(db):
    from localdesk.esi import ESIError
    from localdesk.sync import S_STRUCTURES
    main, alt, other = 90000020, 90000021, 90000022
    add_char(db, main, "Main", scopes=[S_STRUCTURES])
    add_char(db, alt, "Alt", scopes=[S_STRUCTURES])
    add_char(db, other, "Other", scopes=[])
    s1, s2, s3 = 1013676713698, 1013676713699, 1013676713700

    calls = []

    class ESI(FakeESI):
        def get(self, path, character_id=None, params=None):
            calls.append((path, character_id))
            if path == f"/universe/structures/{s1}/" and character_id == alt:
                return {"name": "J-ABC - Alt's Fortizar", "solar_system_id": 30000142}, time.time() + 300
            raise ESIError(403, "Forbidden", path)

    db.cache_put(other, "access_structures", [{"id": s3, "name": "X - Scanned Keepstar", "system_id": 30000142,
                                               "type_id": 35834}], time.time() + 999)
    syncer = Syncer(db, ESI({("POST", "/universe/names/"): lambda p, ids: [
        {"id": i, "name": "Jita", "category": "solar_system"} for i in ids]}))
    syncer.resolve_structures(main, {s1, s2, s3})
    names = db.get_name_entries([s1, s2, s3])
    assert names[s1] == ("J-ABC - Alt's Fortizar", "structure")          # the alt could see it
    assert names[s2][1] == "structure_unknown" and "no docking access" in names[s2][0]
    assert names[s3] == ("X - Scanned Keepstar", "structure")            # from an access scan, no ESI call
    assert not any(s3_path for s3_path, _ in calls if str(s3) in s3_path)
    # a refused structure isn't asked about again for a day, and a name you gave it is kept
    db.put_names([(s2, "Old staging Fortizar", "structure_custom")])
    before = len(calls)
    syncer.resolve_structures(main, {s2})
    assert len(calls) == before
    assert db.get_name_entries([s2])[s2] == ("Old staging Fortizar", "structure_custom")


def test_windowed_build_without_console_streams(tmp_path, monkeypatch):
    """Windowed Windows builds start with sys.stdout/sys.stderr = None."""
    import sys
    from localdesk.ui import app
    monkeypatch.setenv("LOCALDESK_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    app.ensure_output_streams()
    import traceback
    try:
        raise RuntimeError("background task failed")
    except RuntimeError:
        traceback.print_exc()  # what Worker.run does; must not raise
    sys.stderr.flush()
    text = (tmp_path / "localdesk.log").read_text()
    assert "started" in text and "background task failed" in text


def test_fit_widths_shrinks_widest_columns_first():
    from localdesk.ui.widgets import fit_widths
    assert fit_widths([100, 200, 300], 1000) == [100, 200, 300]   # fits: unchanged
    w = fit_widths([100, 200, 600], 600)
    assert sum(w) <= 600 and w[0] == 100 and w[1] == 200 and w[2] == 300   # only the widest shrinks
    w = fit_widths([300, 300, 300], 300)
    assert sum(w) <= 300 and len(set(w)) == 1
    # per-column minimums: the badge column (min 110) keeps its width, free text gives way
    w = fit_widths([110, 400, 90], 400, [110, 90, 90])
    assert w[0] == 110 and w[2] == 90 and sum(w) <= 400
    # minimums don't fit: return them (the table scrolls as a last resort)
    assert fit_widths([200, 200], 100, [120, 120]) == [120, 120]
