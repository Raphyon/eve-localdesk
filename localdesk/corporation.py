"""Corporation views: the corporations your characters are in, mining-ledger summaries (with mining
tax) and member activity. Reads only from the local database."""
import csv
import io
import time
from datetime import date, timedelta

from .datasets import BY_KEY
from .model import parse_time

PERIODS = [("This month", "this_month"), ("Last month", "last_month"), ("Last 7 days", "7"),
           ("Last 30 days", "30"), ("Last 90 days", "90"), ("All history", "all")]
GROUPS = [("Character", "character"), ("Ore", "ore"), ("Structure", "structure"), ("Day", "day")]


def period_range(key, today=None):
    """(first day, last day) as ISO dates, inclusive; None means open-ended."""
    today = today or date.today()
    if key == "this_month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if key == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1).isoformat(), last.isoformat()
    if key == "all":
        return None, None
    return (today - timedelta(days=int(key) - 1)).isoformat(), today.isoformat()


def my_corporations(db):
    """{corp_id: [character ids]} for the (player) corporations your characters are in."""
    out = {}
    for ch in db.list_characters():
        corp = (db.cache_get(ch["character_id"], "info") or {}).get("corporation_id")
        if corp and corp >= 2_000_000:
            out.setdefault(corp, []).append(ch["character_id"])
    return out


def mining_entries(db, corp, period="this_month", observer=None, today=None):
    history = db.cache_get(0, f"corp_mining:{corp}") or {}
    start, end = period_range(period, today)
    out = []
    for e in history.get("entries", {}).values():
        if (start and e["date"] < start) or (end and e["date"] > end):
            continue
        if observer and e["observer"] != observer:
            continue
        out.append(e)
    return out


def mining_observers(db, corp):
    return sorted(int(o) for o in (db.cache_get(0, f"corp_mining:{corp}") or {}).get("observers", {}))


def history_span(db, corp):
    dates = [e["date"] for e in (db.cache_get(0, f"corp_mining:{corp}") or {}).get("entries", {}).values()]
    return (min(dates), max(dates)) if dates else (None, None)


def summarize(entries, prices, group_by="character", tax_pct=0.0):
    """Group ledger entries. Returns rows sorted by value: {key, corp_id, quantity, value, tax, miners, share}."""
    groups = {}
    for e in entries:
        key = {"character": e["character_id"], "ore": e["type_id"], "structure": e["observer"],
               "day": e["date"]}[group_by]
        g = groups.setdefault(key, {"key": key, "corp_id": e.get("corp_id"), "quantity": 0, "value": 0.0,
                                    "miners": set()})
        g["quantity"] += e["quantity"]
        g["value"] += e["quantity"] * prices.get(e["type_id"], 0.0)
        g["miners"].add(e["character_id"])
    total = sum(g["value"] for g in groups.values()) or 1.0
    rows = []
    for g in groups.values():
        rows.append(dict(g, miners=len(g["miners"]), tax=g["value"] * tax_pct / 100, share=g["value"] / total * 100))
    key = (lambda r: r["key"]) if group_by == "day" else (lambda r: -r["value"])
    return sorted(rows, key=key, reverse=group_by == "day")


def to_csv(header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def member_activity(db, syncer, corp, now=None):
    """Member tracking rows (needs a Director) with days since last logon, from any of your characters in
    the corporation that has access. Returns (rows, character id that provided them) or ([], None)."""
    now = now or time.time()
    for cid in my_corporations(db).get(corp, []):
        rows = syncer.dataset_rows(cid, BY_KEY["corp_members"])
        if rows:
            out = []
            for m in rows:
                logon = parse_time(m.get("logon_date"))
                out.append(dict(m, days_inactive=(now - logon) / 86400 if logon else None))
            return out, cid
    return [], None


def first_with_access(db, syncer, corp, dataset_key):
    """Rows of a corporation dataset from the first of your characters in that corp who can read it."""
    for cid in my_corporations(db).get(corp, []):
        rows = syncer.dataset_rows(cid, BY_KEY[dataset_key])
        if rows is not None:
            return rows, cid
    return None, None


def division_names(db, corp):
    """{"wallet": {1: name, ...}, "hangar": {...}} from the corp's division settings (needs a Director),
    falling back to EVE's defaults."""
    names = {"wallet": {1: "Master Wallet", **{d: f"Wallet division {d}" for d in range(2, 8)}},
             "hangar": {d: f"Hangar {d}" for d in range(1, 8)}}
    for cid in my_corporations(db).get(corp, []):
        data = db.cache_get(cid, "corp_divisions")
        if isinstance(data, dict) and (data.get("wallet") or data.get("hangar")):
            for kind in ("wallet", "hangar"):
                for d in data.get(kind, []):
                    if d.get("name"):
                        names[kind][d["division"]] = d["name"]
            break
    return names
