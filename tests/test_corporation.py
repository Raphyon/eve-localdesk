import time
from datetime import date

from localdesk import corporation as corp_mod
from localdesk.sync import Syncer, S_CORP_MINING, S_STRUCTURES

from conftest import FakeESI, add_char


def test_period_range():
    d = date(2026, 11, 15)
    assert corp_mod.period_range("this_month", d) == ("2026-11-01", "2026-11-15")
    assert corp_mod.period_range("last_month", d) == ("2026-10-01", "2026-10-31")
    assert corp_mod.period_range("7", d) == ("2026-11-09", "2026-11-15")
    assert corp_mod.period_range("all", d) == (None, None)


def test_mining_ledger_history_grows_and_summarizes(db):
    cid, corp = 90000030, 98000123
    add_char(db, cid, "Accountant", scopes=[S_CORP_MINING, S_STRUCTURES])
    db.cache_put(cid, "info", {"corporation_id": corp}, time.time() + 999)
    ledger = {
        "first": [{"character_id": 1, "recorded_corporation_id": corp, "type_id": 45490, "quantity": 1000,
                   "last_updated": "2026-10-01"},
                  {"character_id": 2, "recorded_corporation_id": corp, "type_id": 45491, "quantity": 500,
                   "last_updated": "2026-10-02"}],
        # a later sync: ESI has dropped Oct 1 (older than 30 days), updated Oct 2 and added Nov 1
        "second": [{"character_id": 2, "recorded_corporation_id": corp, "type_id": 45491, "quantity": 800,
                    "last_updated": "2026-10-02"},
                   {"character_id": 1, "recorded_corporation_id": corp, "type_id": 45490, "quantity": 200,
                    "last_updated": "2026-11-01"}],
    }
    state = {"which": "first"}
    esi = FakeESI({
        ("GET", f"/corporation/{corp}/mining/observers/"): [{"observer_id": 1035000000001,
                                                             "observer_type": "structure",
                                                             "last_updated": "2026-11-01"}],
        ("GET", f"/corporation/{corp}/mining/observers/1035000000001/"): lambda p, j: ledger[state["which"]],
        ("POST", "/universe/names/"): lambda p, ids: [{"id": i, "name": f"N{i}", "category": "x"} for i in ids],
        ("GET", "/universe/structures/1035000000001/"): {"name": "X - Athanor", "solar_system_id": 30000142},
    })
    syncer = Syncer(db, esi)
    syncer.sync_corp_mining(cid, corp)
    state["which"] = "second"
    syncer.sync_corp_mining(cid, corp)          # within the hour: skipped
    assert len(db.cache_get(0, f"corp_mining:{corp}")["entries"]) == 2
    syncer.sync_corp_mining(cid, corp, force=True)
    entries = corp_mod.mining_entries(db, corp, "all")
    assert sorted((e["character_id"], e["date"], e["quantity"]) for e in entries) == [
        (1, "2026-10-01", 1000), (1, "2026-11-01", 200), (2, "2026-10-02", 800)]  # Oct 1 kept locally

    prices = {45490: 100.0, 45491: 1000.0}
    rows = corp_mod.summarize(entries, prices, "character", tax_pct=10)
    assert [(r["key"], r["quantity"], r["value"], r["tax"]) for r in rows] == [
        (2, 800, 800000.0, 80000.0), (1, 1200, 120000.0, 12000.0)]
    by_day = corp_mod.summarize(entries, prices, "day")
    assert [r["key"] for r in by_day] == ["2026-11-01", "2026-10-02", "2026-10-01"]
    oct_only = corp_mod.mining_entries(db, corp, "last_month", today=date(2026, 11, 15))
    assert {e["date"] for e in oct_only} == {"2026-10-01", "2026-10-02"}
    assert corp_mod.mining_observers(db, corp) == [1035000000001]
    assert db.get_names([1035000000001])[1035000000001] == "X - Athanor"
    assert "Character,Qty" in corp_mod.to_csv(["Character", "Qty"], [["A", 1]])


def test_mining_no_role_is_remembered(db):
    from localdesk.esi import ESIError
    cid, corp = 90000031, 98000124
    add_char(db, cid, "Miner", scopes=[S_CORP_MINING])

    def forbidden(p, j):
        raise ESIError(403, "Character does not have required role(s)")

    syncer = Syncer(db, FakeESI({("GET", f"/corporation/{corp}/mining/observers/"): forbidden}))
    syncer.sync_corp_mining(cid, corp)
    assert db.cache_get(cid, "corp_mining_access") == {"no_access": True}


def test_member_activity(db):
    cid, corp = 90000032, 98000125
    add_char(db, cid, "Director", scopes=["esi-corporations.track_members.v1"])
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    db.cache_put(cid, "info", {"corporation_id": corp}, now + 999)
    db.cache_put(cid, "corp_members", [{"character_id": 1, "logon_date": iso(now - 2 * 86400)},
                                       {"character_id": 2, "logon_date": iso(now - 40 * 86400)}], now + 999)
    rows, source = corp_mod.member_activity(db, Syncer(db, FakeESI({})), corp, now)
    assert source == cid and sorted(round(r["days_inactive"]) for r in rows) == [2, 40]
