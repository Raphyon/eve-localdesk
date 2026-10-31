"""Declarative definitions of the ESI data that is browsed as tables (SeAT's character and
corporation modules). Adding a module = adding a Dataset here; syncing, name resolution and the
Data browser page pick it up automatically.

Column kinds: text, int, float, isk, date, name (any ID resolved via /universe/names), type
(inventory type ID), location (station/structure/system ID), bool, standing, badge (status pill)."""
from dataclasses import dataclass, field


@dataclass
class Col:
    header: str
    get: object               # callable(row) -> raw value, or a key name
    kind: str = "text"

    def value(self, row):
        return self.get(row) if callable(self.get) else row.get(self.get)


@dataclass
class Dataset:
    key: str
    title: str
    group: str                # "Character" or "Corporation"
    path: str                 # may contain {cid} and {corp}
    scope: str
    cols: list
    paged: bool = False
    params: dict = None
    single: bool = False      # the endpoint returns one object, shown as a one-row table
    hook: str = None          # Syncer method that post-processes the raw response
    note: str = ""            # shown above the table (e.g. which corp role is needed)
    actions: list = field(default_factory=list)  # [(label, function name in ui.data_browser)]
    extra_ids: object = None  # callable(row) -> more type/name IDs to resolve

    @property
    def corp(self):
        return "{corp}" in self.path


ACTIVITIES = {1: "Manufacturing", 3: "TE research", 4: "ME research", 5: "Copying", 7: "Reverse engineering",
              8: "Invention", 9: "Reactions", 11: "Reactions"}

SCOPE_TRANSACTIONS = "esi-wallet.read_character_wallet.v1"


def _total(row):
    return (row.get("quantity") or 0) * (row.get("unit_price") or 0) * (-1 if row.get("is_buy") else 1)


ORDER_COLS = [
    Col("Issued", "issued", "date"), Col("Item", "type_id", "type"),
    Col("Buy/Sell", lambda r: "Buy" if r.get("is_buy_order") else "Sell", "badge"),
    Col("Price", "price", "isk"), Col("Remaining", "volume_remain", "int"), Col("Total", "volume_total", "int"),
    Col("Location", "location_id", "location"), Col("Range", "range"), Col("Duration (d)", "duration", "int"),
    Col("State", lambda r: r.get("state", "active"), "badge"),
]
CONTRACT_COLS = [
    Col("Issued", "date_issued", "date"), Col("Type", "type"), Col("Status", "status", "badge"), Col("Title", "title"),
    Col("Issuer", "issuer_id", "name"), Col("Assignee", "assignee_id", "name"),
    Col("Acceptor", "acceptor_id", "name"), Col("Price", "price", "isk"), Col("Reward", "reward", "isk"),
    Col("Collateral", "collateral", "isk"), Col("Volume m³", "volume", "float"),
    Col("From", "start_location_id", "location"), Col("To", "end_location_id", "location"),
    Col("Expires", "date_expired", "date"),
]
JOB_COLS = [
    Col("Activity", lambda r: ACTIVITIES.get(r.get("activity_id"), r.get("activity_id"))),
    Col("Blueprint", "blueprint_type_id", "type"), Col("Product", "product_type_id", "type"),
    Col("Runs", "runs", "int"), Col("Status", "status", "badge"), Col("Installer", "installer_id", "name"),
    Col("Started", "start_date", "date"), Col("Ends", "end_date", "date"),
    Col("Facility", "facility_id", "location"), Col("Cost", "cost", "isk"),
]
JOURNAL_COLS = [
    Col("Date", "date", "date"), Col("Type", lambda r: (r.get("ref_type") or "").replace("_", " ")),
    Col("Amount", "amount", "isk"), Col("Balance", "balance", "isk"),
    Col("First party", "first_party_id", "name"), Col("Second party", "second_party_id", "name"),
    Col("Description", "description"), Col("Reason", "reason"),
]

DATASETS = [
    # --- character -------------------------------------------------------
    Dataset("transactions", "Wallet transactions", "Character", "/characters/{cid}/wallet/transactions/",
            SCOPE_TRANSACTIONS, [
                Col("Date", "date", "date"), Col("Item", "type_id", "type"), Col("Qty", "quantity", "int"),
                Col("Unit price", "unit_price", "isk"), Col("Total", _total, "isk"),
                Col("Buy/Sell", lambda r: "Buy" if r.get("is_buy") else "Sell", "badge"),
                Col("Client", "client_id", "name"), Col("Location", "location_id", "location"),
                Col("Personal", "is_personal", "bool")]),
    Dataset("orders", "Market orders (open)", "Character", "/characters/{cid}/orders/",
            "esi-markets.read_character_orders.v1", ORDER_COLS),
    Dataset("order_history", "Market orders (history)", "Character", "/characters/{cid}/orders/history/",
            "esi-markets.read_character_orders.v1", ORDER_COLS, paged=True),
    Dataset("contracts", "Contracts", "Character", "/characters/{cid}/contracts/",
            "esi-contracts.read_character_contracts.v1", CONTRACT_COLS, paged=True),
    Dataset("industry", "Industry jobs", "Character", "/characters/{cid}/industry/jobs/",
            "esi-industry.read_character_jobs.v1", JOB_COLS, params={"include_completed": "true"}),
    Dataset("blueprints", "Blueprints", "Character", "/characters/{cid}/blueprints/",
            "esi-characters.read_blueprints.v1", [
                Col("Blueprint", "type_id", "type"),
                Col("Kind", lambda r: "Original" if r.get("runs") == -1 else "Copy"),
                Col("ME", "material_efficiency", "int"), Col("TE", "time_efficiency", "int"),
                Col("Runs", lambda r: None if r.get("runs") == -1 else r.get("runs"), "int"),
                Col("Location", "location_id", "location"), Col("Flag", "location_flag")], paged=True),
    Dataset("mining", "Mining ledger", "Character", "/characters/{cid}/mining/",
            "esi-industry.read_character_mining.v1", [
                Col("Date", "date", "date"), Col("System", "solar_system_id", "name"),
                Col("Ore", "type_id", "type"), Col("Quantity", "quantity", "int"),
                Col("Est. value", "value", "isk")], paged=True, hook="hook_mining_value"),
    Dataset("killmails", "Killmails", "Character", "/characters/{cid}/killmails/recent/",
            "esi-killmails.read_killmails.v1", [
                Col("Time", "time", "date"), Col("Kill/Loss", lambda r: "Loss" if r.get("loss") else "Kill", "badge"),
                Col("Victim", "victim_id", "name"), Col("Victim corp", "victim_corp_id", "name"),
                Col("Ship", "ship_type_id", "type"), Col("System", "system_id", "name"),
                Col("Attackers", "attackers", "int"), Col("Final blow", "final_blow_id", "name"),
                Col("Est. ship value", "ship_value", "isk"), Col("Killmail ID", "killmail_id")],
            paged=True, hook="hook_killmails", actions=[("Open on zKillboard", "open_zkill")]),
    Dataset("planets", "Planetary industry", "Character", "/characters/{cid}/planets/",
            "esi-planets.manage_planets.v1", [
                Col("Planet", "planet_id", "name"), Col("Type", "planet_type"),
                Col("System", "solar_system_id", "name"), Col("CC level", "upgrade_level", "int"),
                Col("Structures", "num_pins", "int"), Col("Extractors", "extractors", "int"),
                Col("Extractors expire", "extractor_expiry", "date"), Col("Last update", "last_update", "date")],
            hook="hook_planets", note="Extractor expiry times come from each colony's layout. ESI only "
                                      "updates a colony after you view it in game."),
    Dataset("standings", "Standings (NPC)", "Character", "/characters/{cid}/standings/",
            "esi-characters.read_standings.v1", [
                Col("From", "from_id", "name"), Col("Type", "from_type"), Col("Standing", "standing", "standing")]),
    Dataset("loyalty", "Loyalty points", "Character", "/characters/{cid}/loyalty/points/",
            "esi-characters.read_loyalty.v1", [
                Col("Corporation", "corporation_id", "name"), Col("LP", "loyalty_points", "int")]),
    Dataset("research", "Research agents", "Character", "/characters/{cid}/agents_research/",
            "esi-characters.read_agents_research.v1", [
                Col("Agent", "agent_id", "name"), Col("Field", "skill_type_id", "type"),
                Col("Started", "started_at", "date"), Col("Points/day", "points_per_day", "float"),
                Col("Remainder", "remainder_points", "float")]),
    Dataset("notifications", "Notifications", "Character", "/characters/{cid}/notifications/",
            "esi-characters.read_notifications.v1", [
                Col("Time", "timestamp", "date"), Col("Type", "type"), Col("Sender", "sender_id", "name"),
                Col("Read", "is_read", "bool"), Col("Details", lambda r: (r.get("text") or "").replace("\n", "  "))]),
    Dataset("fittings", "Saved fittings", "Character", "/characters/{cid}/fittings/",
            "esi-fittings.read_fittings.v1", [
                Col("Name", "name"), Col("Ship", "ship_type_id", "type"),
                Col("Modules/items", lambda r: len(r.get("items", [])), "int"), Col("Description", "description")],
            actions=[("Copy as EFT text", "copy_eft")],
            extra_ids=lambda r: [i["type_id"] for i in r.get("items", [])]),
    Dataset("calendar", "Calendar", "Character", "/characters/{cid}/calendar/",
            "esi-calendar.read_calendar_events.v1", [
                Col("When", "event_date", "date"), Col("Title", "title"),
                Col("Response", "event_response"), Col("Importance", "importance", "int")]),
    Dataset("medals", "Medals", "Character", "/characters/{cid}/medals/", "esi-characters.read_medals.v1", [
                Col("Awarded", "date", "date"), Col("Medal", "title"), Col("Corporation", "corporation_id", "name"),
                Col("Issuer", "issuer_id", "name"), Col("Reason", "reason")]),
    Dataset("fatigue", "Jump fatigue", "Character", "/characters/{cid}/fatigue/", "esi-characters.read_fatigue.v1", [
                Col("Fatigue expires", "jump_fatigue_expire_date", "date"), Col("Last jump", "last_jump_date", "date"),
                Col("Last update", "last_update_date", "date")], single=True),
    Dataset("corp_history", "Employment history", "Character", "/characters/{cid}/corporationhistory/", None, [
                Col("Joined", "start_date", "date"), Col("Corporation", "corporation_id", "name"),
                Col("Deleted corp", "is_deleted", "bool")]),
    # --- corporation (needs in-game corp roles; a 403 just means "no access") ---
    Dataset("corp_structures", "Structures & fuel", "Corporation", "/corporations/{corp}/structures/",
            "esi-corporations.read_structures.v1", [
                Col("Name", "name"), Col("Type", "type_id", "type"), Col("System", "system_id", "name"),
                Col("State", "state", "badge"), Col("Fuel expires", "fuel_expires", "date"),
                Col("Reinforce hour", "reinforce_hour", "int"),
                Col("Services", lambda r: ", ".join(f"{s['name']} ({s['state']})" for s in r.get("services", [])))],
            paged=True, note="Requires the Station Manager role."),
    Dataset("corp_members", "Member tracking", "Corporation", "/corporations/{corp}/membertracking/",
            "esi-corporations.track_members.v1", [
                Col("Member", "character_id", "name"), Col("Location", "location_id", "location"),
                Col("Ship", "ship_type_id", "type"), Col("Last logon", "logon_date", "date"),
                Col("Last logoff", "logoff_date", "date"), Col("Joined", "start_date", "date")],
            note="Requires the Director role."),
    Dataset("corp_wallets", "Wallet divisions", "Corporation", "/corporations/{corp}/wallets/",
            "esi-wallet.read_corporation_wallets.v1", [
                Col("Division", "division", "int"), Col("Balance", "balance", "isk")],
            note="Requires the Accountant or Junior Accountant role."),
    Dataset("corp_journal", "Master wallet journal", "Corporation", "/corporations/{corp}/wallets/1/journal/",
            "esi-wallet.read_corporation_wallets.v1", JOURNAL_COLS, paged=True,
            note="Division 1 (master wallet). Requires the Accountant role."),
    Dataset("corp_contracts", "Contracts", "Corporation", "/corporations/{corp}/contracts/",
            "esi-contracts.read_corporation_contracts.v1", CONTRACT_COLS, paged=True),
    Dataset("corp_industry", "Industry jobs", "Corporation", "/corporations/{corp}/industry/jobs/",
            "esi-industry.read_corporation_jobs.v1", JOB_COLS, paged=True, params={"include_completed": "true"},
            note="Requires the Factory Manager role."),
    Dataset("corp_orders", "Market orders", "Corporation", "/corporations/{corp}/orders/",
            "esi-markets.read_corporation_orders.v1", ORDER_COLS, paged=True,
            note="Requires the Accountant or Trader role."),
    Dataset("corp_extractions", "Moon extractions", "Corporation", "/corporation/{corp}/mining/extractions/",
            "esi-industry.read_corporation_mining.v1", [
                Col("Moon", "moon_id", "name"), Col("Refinery", "structure_id", "location"),
                Col("Chunk arrives", "chunk_arrival_time", "date"), Col("Auto-fracture", "natural_decay_time", "date"),
                Col("Started", "extraction_start_time", "date")], note="Requires the Station Manager role."),
]

BY_KEY = {d.key: d for d in DATASETS}
SCOPES = sorted({d.scope for d in DATASETS if d.scope})
