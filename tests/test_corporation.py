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


def test_corp_wallet_divisions_assets_and_killmails(db):
    from localdesk.datasets import BY_KEY
    cid, corp = 90000040, 98000200
    add_char(db, cid, "Director", scopes=["esi-wallet.read_corporation_wallets.v1", "esi-assets.read_corporation_assets.v1",
                                          "esi-killmails.read_corporation_killmails.v1", "esi-alliances.read_contacts.v1"])
    db.cache_put(cid, "info", {"corporation_id": corp}, time.time() + 999)  # no alliance
    routes = {("GET", f"/corporations/{corp}/wallets/{d}/journal/"): [{"id": d, "amount": d * 10.0}] for d in range(1, 8)}
    routes.update({
        ("GET", f"/corporations/{corp}/assets/"): [
            {"item_id": 1, "type_id": 27, "location_id": 60003760, "location_flag": "OfficeFolder", "quantity": 1},
            {"item_id": 2, "type_id": 34, "location_id": 1, "location_flag": "CorpSAG3", "quantity": 1000},
            {"item_id": 3, "type_id": 17366, "location_id": 1, "location_flag": "CorpSAG2", "quantity": 1},
            {"item_id": 4, "type_id": 35, "location_id": 3, "location_flag": "Unlocked", "quantity": 50},
            {"item_id": 5, "type_id": 587, "location_id": 1035466617946, "location_flag": "CorpDeliveries", "quantity": 1}],
        ("GET", f"/corporations/{corp}/killmails/recent/"): [{"killmail_id": 9, "killmail_hash": "h"}],
        ("GET", "/killmails/9/h/"): {"killmail_time": "2026-10-01T00:00:00Z", "solar_system_id": 30000142,
                                    "victim": {"character_id": 123, "corporation_id": corp, "ship_type_id": 587},
                                    "attackers": [{"character_id": 7, "final_blow": True}]},
    })
    syncer = Syncer(db, FakeESI(routes))
    db.cache_put(0, "prices", [{"type_id": 34, "average_price": 4.0}, {"type_id": 35, "average_price": 10.0}],
                 time.time() + 999)
    for key in ("corp_journal", "corp_assets", "corp_killmails", "alliance_contacts"):
        syncer.sync_dataset(cid, BY_KEY[key])
    journal = syncer.dataset_rows(cid, BY_KEY["corp_journal"])
    assert sorted((r["division"], r["amount"]) for r in journal) == [(d, d * 10.0) for d in range(1, 8)]
    assets = {a["item_id"]: a for a in syncer.dataset_rows(cid, BY_KEY["corp_assets"])}
    assert 1 not in assets                                    # the office container itself is skipped
    assert assets[2]["root_location"] == 60003760 and assets[2]["hangar"] == 3 and assets[2]["value"] == 4000
    assert assets[4]["hangar"] == 2 and assets[4]["root_location"] == 60003760  # inside a container in hangar 2
    assert assets[5]["hangar"] == "CorpDeliveries" and assets[5]["root_location"] == 1035466617946
    kills = syncer.dataset_rows(cid, BY_KEY["corp_killmails"])
    assert kills[0]["loss"] is True                           # victim was in our corporation
    assert syncer.dataset_rows(cid, BY_KEY["alliance_contacts"]) == []  # not in an alliance: nothing fetched


def test_division_names(db):
    cid, corp = 90000041, 98000201
    add_char(db, cid, "Director")
    db.cache_put(cid, "info", {"corporation_id": corp}, time.time() + 999)
    assert corp_mod.division_names(db, corp)["wallet"][1] == "Master Wallet"
    db.cache_put(cid, "corp_divisions", {"wallet": [{"division": 2, "name": "SRP"}], "hangar": [{"division": 1, "name": "Ammo"}]},
                 time.time() + 999)
    names = corp_mod.division_names(db, corp)
    assert names["wallet"][2] == "SRP" and names["hangar"][1] == "Ammo" and names["wallet"][3] == "Wallet division 3"
