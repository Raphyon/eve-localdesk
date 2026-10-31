"""Things that need attention across all characters, computed from the local cache."""
import time

from .datasets import BY_KEY
from .model import fmt_duration, parse_time

FUEL_WARNING = 7 * 86400
PI_STALE_AFTER = 14 * 86400  # extractors that stopped longer ago than this are treated as abandoned, not alerted


def compute(db, syncer, view):
    """[{id, level ('critical'|'warning'|'info'), character, text, when}] newest/most urgent first."""
    now = time.time()
    out = []

    def add(level, cid, key, text, when=None):
        out.append({"id": f"{cid}:{key}", "level": level, "character": cid, "text": text, "when": when})

    seen_structures = set()
    for ch in db.list_characters():
        cid = ch["character_id"]
        errors = syncer.last_errors.get(cid, [])
        if any("token refresh failed" in e or "token:" in e for e in errors):
            add("critical", cid, "token", "Login expired or revoked. Remove and add this character again.")

        tr = view.training(cid)
        if "esi-skills.read_skillqueue.v1" in ch["scopes"] and view.get(cid, "skillqueue") is not None:
            if tr["state"] in ("empty", "done"):
                add("warning", cid, "queue-empty", "Skill queue is empty")
            elif tr["state"] == "paused":
                add("info", cid, "queue-paused", tr["text"])
            elif tr["end"] and tr["end"] - now < 86400:
                add("warning", cid, f"queue-low-{int(tr['end'])}",
                    f"Skill queue ends in {fmt_duration(tr['end'] - now)}", tr["end"])

        unread = (view.get(cid, "mail_labels") or {}).get("total_unread_count")
        if unread:
            add("info", cid, f"mail-{unread}", f"{unread} unread mail(s)")

        for p in syncer.dataset_rows(cid, BY_KEY["planets"]) or []:
            exp = parse_time(p.get("extractor_expiry"))
            if exp is None:
                continue
            planet = view.name(p["planet_id"])
            if exp < now - PI_STALE_AFTER:
                continue
            if exp < now:
                add("warning", cid, f"pi-{p['planet_id']}-{int(exp)}", f"Extractors stopped on {planet}", exp)
            elif exp - now < 86400:
                add("info", cid, f"pi-soon-{p['planet_id']}-{int(exp)}",
                    f"Extractors on {planet} stop in {fmt_duration(exp - now)}", exp)

        for job in syncer.dataset_rows(cid, BY_KEY["industry"]) or []:
            end = parse_time(job.get("end_date"))
            if job.get("status") == "ready" or (job.get("status") == "active" and end and end < now):
                add("info", cid, f"job-{job['job_id']}",
                    f"Industry job ready to deliver: {view.name(job.get('product_type_id') or job['blueprint_type_id'])}",
                    end)

        for c in syncer.dataset_rows(cid, BY_KEY["contracts"]) or []:
            if c.get("status") == "outstanding" and c.get("assignee_id") == cid:
                add("info", cid, f"contract-{c['contract_id']}",
                    f"Contract waiting for you from {view.name(c.get('issuer_id'))}: {c.get('title') or c.get('type')}",
                    parse_time(c.get("date_issued")))

        fatigue = view.get(cid, "fatigue") or {}
        fexp = parse_time(fatigue.get("jump_fatigue_expire_date")) if isinstance(fatigue, dict) else None
        if fexp and fexp > now:
            add("info", cid, "fatigue", f"Jump fatigue for {fmt_duration(fexp - now)}", fexp)

        for s in syncer.dataset_rows(cid, BY_KEY["corp_structures"]) or []:
            if s["structure_id"] in seen_structures:
                continue
            seen_structures.add(s["structure_id"])
            fuel = parse_time(s.get("fuel_expires"))
            if fuel is not None and fuel - now < FUEL_WARNING:
                level = "critical" if fuel - now < 2 * 86400 else "warning"
                left = "is OUT of fuel" if fuel < now else f"runs out of fuel in {fmt_duration(fuel - now)}"
                add(level, cid, f"fuel-{s['structure_id']}-{int(fuel) // 86400}", f"{s.get('name')} {left}", fuel)

        for e in syncer.dataset_rows(cid, BY_KEY["corp_extractions"]) or []:
            arrive = parse_time(e.get("chunk_arrival_time"))
            if arrive and 0 < arrive - now < 86400:
                add("info", cid, f"moon-{e['moon_id']}-{int(arrive)}",
                    f"Moon chunk arrives at {view.name(e['moon_id'])} in {fmt_duration(arrive - now)}", arrive)

    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda a: (order[a["level"]], a["when"] or 0))
