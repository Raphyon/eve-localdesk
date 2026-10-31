"""Copy contacts from one character to others through ESI."""
from collections import defaultdict
from dataclasses import dataclass

from .esi import ESIError

MODES = {
    "add": "Add missing contacts only",
    "update": "Add missing + update standings",
    "mirror": "Mirror exactly (also removes contacts the source doesn't have)",
}
ADD_BATCH = 100     # ESI limit for POST/PUT contact_ids
DELETE_BATCH = 20   # ESI limit for DELETE contact_ids


@dataclass
class ContactChange:
    action: str          # "add", "update" or "remove"
    contact_id: int
    contact_type: str
    standing: float      # the new standing (for "remove": the current one)
    old_standing: float = None


def plan(source, target, target_id, mode="add", extra=None, exclude=()):
    """Work out the changes needed on `target` (a list of ESI contact dicts).

    extra:   {contact_id: (contact_type, standing)} added on top of the source's contacts,
             e.g. your other characters at +10.
    exclude: contact IDs not copied from the source. In mirror mode these, and `extra`,
             are also never removed from the target.
    """
    exclude = set(exclude)
    want = {c["contact_id"]: (c["contact_type"], float(c["standing"]))
            for c in source if c["contact_id"] not in exclude}
    want.update(extra or {})
    want.pop(target_id, None)
    have = {c["contact_id"]: (c["contact_type"], float(c["standing"])) for c in target}
    protected = exclude | set(extra or {})

    changes = []
    for cid, (ctype, standing) in want.items():
        if cid not in have:
            changes.append(ContactChange("add", cid, ctype, standing))
        elif mode in ("update", "mirror") and abs(have[cid][1] - standing) > 1e-6:
            changes.append(ContactChange("update", cid, ctype, standing, have[cid][1]))
    if mode == "mirror":
        for cid, (ctype, standing) in have.items():
            if cid not in want and cid not in protected:
                changes.append(ContactChange("remove", cid, ctype, standing, standing))
    return changes


def batches(changes):
    """Group changes into ESI calls: (method, standing, [ids])."""
    out = []
    for action, method in (("add", "POST"), ("update", "PUT")):
        by_standing = defaultdict(list)
        for c in changes:
            if c.action == action:
                by_standing[c.standing].append(c.contact_id)
        for standing, ids in sorted(by_standing.items()):
            out.extend((method, standing, ids[i:i + ADD_BATCH]) for i in range(0, len(ids), ADD_BATCH))
    removes = [c.contact_id for c in changes if c.action == "remove"]
    out.extend(("DELETE", None, removes[i:i + DELETE_BATCH]) for i in range(0, len(removes), DELETE_BATCH))
    return out


def _send(esi, character_id, method, standing, ids):
    path = f"/characters/{character_id}/contacts/"
    if method == "DELETE":
        esi.request("DELETE", path, character_id, params={"contact_ids": ",".join(map(str, ids))})
    else:
        esi.request(method, path, character_id, params={"standing": standing}, json=ids)


def apply(esi, character_id, changes, progress=None):
    """Apply the changes. Returns (number applied, [(contact_id, error)])."""
    applied, failures = 0, []
    for n, (method, standing, ids) in enumerate(batches(changes), 1):
        if progress:
            progress(f"Updating contacts ({n})…")
        try:
            _send(esi, character_id, method, standing, ids)
            applied += len(ids)
        except ESIError as e:
            if len(ids) == 1:
                failures.append((ids[0], str(e)))
                continue
            # A single unaddable contact (e.g. some NPC entities) fails the batch; retry one by one.
            for cid in ids:
                try:
                    _send(esi, character_id, method, standing, [cid])
                    applied += 1
                except ESIError as e2:
                    failures.append((cid, str(e2)))
    return applied, failures
