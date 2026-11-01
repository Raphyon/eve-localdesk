# EVE LocalDesk

A desktop take on [SeAT](https://github.com/eveseat/seat) that runs entirely on your own computer. There's no web
server, database server or hosting: one Python app, one SQLite file and EVE's official SSO + ESI API.
The interface follows SeAT's AdminLTE look (dark sidebar, cards, stat tiles, badge-style statuses) with a
light/dark toggle (moon icon, top right).

## Features

| Area | What you get |
|---|---|
| **Dashboard** | All characters at a glance: location, security, ship, ISK, SP, current skill, queue end, unread mail, online status |
| **Alerts** *(not in SeAT)* | Empty/ending skill queues, stopped PI extractors, finished industry jobs, contracts assigned to you, structure fuel, moon chunks, jump fatigue, expired logins, plus **desktop notifications** |
| **Character sheet** | Skill queue with countdowns, skills by group, attributes and remaps, wallet journal, jump clones and implants |
| **Assets** | Every character's assets in one tree (location → ship/container → contents), custom ship/container names, estimated value, instant search across all characters |
| **Navigation** | Dotlan-style tools, all planned locally: **Fleet** (where everyone is) · **Route**: gate routes using your **jump bridges**, shortest / safer / less secure, avoid list, **several alternative routes**, sets in-game waypoints · **Jump planner** (laid out like Dotlan's): waypoints you can drag to sort, ship hull, skill levels (filled from the pilot), prefer station systems (NPC stations, accessible structures and cyno beacons), avoid incursions, avoid systems or whole regions, save settings, saved routes. Range and fuel follow from the ship and skills. Shows per-jump fuel, jump timers and fatigue · **Jump range**: every system in range · **Map**: zoomable New Eden map with routes, bridges and your characters. Names never overlap and appear as you zoom in, and the map dims around a planned route. Right-click to route / avoid / open on Dotlan · **Bridges & access**: bridges and dockable structures found via each character's ESI access, plus importing a bridge list |
| **Mail** | Read, mark read, compose, reply (to characters, corps, alliances, mailing lists) |
| **Contacts** | View contacts; **sync contacts between characters** (add missing / also update standings / mirror exactly), with a preview of every change before applying |
| **Client settings** *(not in SeAT)* | **Copy overview + window layout** (core_char) between characters, and account-wide settings (core_user) between accounts. Automatic backups and one-click restore |
| **Data browser** | Sortable, filterable tables (one character or all of them) for: wallet transactions, market orders and history, contracts, industry jobs, blueprints, mining ledger with value, killmails (with zKillboard links), planetary industry with extractor expiry, NPC standings, loyalty points, research agents, notifications, saved fittings (**copy as EFT**), calendar, medals, jump fatigue, employment history |
| **Corporation** | A page per corporation you're in: **overview** (ticker, CEO, tax, members, combined wallets, structures with fuel left, upcoming moon chunks), **mining ledger** (moon mining per character, ore, structure or day, with estimated value and a **mining tax** calculator, copy as CSV; history is kept locally beyond ESI's 30 days), **member activity** (active in 7/30 days, inactive). The Data browser adds structures, members, wallets and journal, contracts, industry jobs, market orders, blueprints, customs offices and moon extractions. Needs characters with the matching in-game roles (Accountant for mining and wallets, Director for members and blueprints, Station Manager for structures and moons). |

Everything refreshes in the background every 15 minutes (or click **Refresh all**), following ESI's own cache
timers, so it never hammers CCP's API.

Tables fit the window: long text is shortened with "…" (hover for the full text) instead of scrolling sideways.
**Right-click any table header to show or hide columns**; the choice is remembered per table.

## Setup (once, about 5 minutes)

### 1. Register a free EVE application
ESI needs to know which app is asking. Other community tools such as EveLens ship with their author's app built in.
This one uses your own, so it can request the write permissions that contact sync, sending mail and autopilot
waypoints need. It's free and there's no secret to look after; login uses PKCE.

1. Go to <https://developers.eveonline.com/applications> and log in.
2. **Create new application** → connection type **Authentication & API Access**.
3. Tick **every** scope listed in `localdesk/sso.py` (the Setup dialog in the app shows them all for copy/paste).
4. Callback URL: `http://localhost:8642/callback`
5. Copy the **Client ID**. You don't need the secret key.

### 2. Install

**Windows (recommended):** download `EVE-LocalDesk-<version>-Setup.exe` from the Releases page and run it.
It installs just for you (no admin rights), adds a Start menu entry and optionally a desktop shortcut, and
can be uninstalled from Windows Settings → Apps. Upgrading is just running the newer installer.
Windows SmartScreen may warn about an unknown publisher because the installer isn't code-signed; choose
*More info → Run anyway*.

Other downloads on the Releases page:
- `EVE-LocalDesk-<version>-windows-portable.zip`: no install. Unzip anywhere and run `EVE LocalDesk.exe`.
- `EVE-LocalDesk-<version>-macos.zip`: unzip and move *EVE LocalDesk* to Applications. It isn't notarized,
  so the first time, right-click it and choose *Open*.
- `eve-localdesk-<version>-linux-x86_64.tar.gz`: unpack and run `eve-localdesk/eve-localdesk`.

The downloads are around 150-250 MB, mostly the built-in browser used for the EVE login window.

**From source** (Python 3.10+): `run.bat` on Windows or `./run.sh` on macOS/Linux, or `pip install .` and
then run `eve-localdesk`.

On first start the Setup dialog asks for the Client ID. Alternatively, put an `esi-credentials.json` (see
`esi-credentials.example.json`) next to the program.

### 3. Add your characters
Click **Add character**. A login window opens inside the app showing the official EVE login. Pick a character
and authorize, and the window closes by itself. It remembers your EVE account, so adding more characters from
the same account is just a click. For characters on another account, click **Use a different EVE account**
in the login window. If you'd rather use your normal web browser, untick "Log in inside the app" in Setup.

## Navigation, jump bridges & docking access

The map data (systems, positions, gates, NPC stations) is downloaded once on first start from
[Fuzzwork's copy](https://www.fuzzwork.co.uk/dump/latest/) of CCP's static data export (about 2 MB). If that site is
unreachable, the app builds the map from ESI instead, which takes a few minutes. Use *Bridges & access → Re-download
map data* after CCP adds or changes systems.

**Jump bridges and structure access** come from ESI: every few hours the app asks, for each character, which
structures it can access (`esi-search.search_structures.v1` + `esi-universe.read_structures.v1`). Ansiblex gates
among them become jump bridges for the route planner. The other structures count as dock/tether points for the jump
planner's pilot. Alliance bridges your characters can't see can be pasted in via *Import list…* (one per line, e.g.
`1DQ1-A » 8WA-Z6`).

The jump planner works per hull (Archon, Nyx, Rhea…). Range and fuel can't be edited: they come from the hull and
the skill levels, as on Dotlan. Picking a pilot fills in their Jump Drive Calibration, Jump Fuel Conservation and
Jump Freighter levels. You can still change the levels by hand, and "Save settings" remembers them. Each hull's base jump range, fuel per light year and isotope
type are read from CCP's ship data through ESI and cached for a month. Fuel per jump is reduced by the pilot's Jump
Fuel Conservation (-10% per level), plus the Jump Freighters skill for jump freighters.
The total also shows an estimated ISK cost at CCP's average isotope price. The planner
also shows each jump's timer and the fatigue after it. It starts
from the pilot's current fatigue (from ESI), assumes you jump as soon as each timer ends, applies the Black Ops
(-75%) and Jump Freighter/Rorqual (-90%) reductions, and caps fatigue at 5 hours.
In-game autopilot can't use jump bridges, so "Set waypoints" adds a waypoint at each bridge's entry system.

## Copying overview & UI layouts between characters

The **Client settings** page finds your EVE settings folders automatically (Windows; Linux via Steam/Proton,
Wine or Lutris; macOS). If yours isn't found, use **Browse…** to pick
`%LOCALAPPDATA%\CCP\EVE\<something>_tranquility\settings_Default`.

1. **Close all EVE clients.** The client overwrites these files when it exits.
2. On the *Characters* tab, click the character whose layout you like (the source row).
3. Tick the characters to copy it to. Your registered characters show up even if they have no settings file yet.
4. Click **Copy selected row → ticked rows**. The files being replaced are backed up first, and **Restore**
   puts them back.

The *Accounts* tab does the same for account-wide settings (keyboard shortcuts and so on). EVE only numbers
these files, so the **Probably these characters** column matches each one to the characters whose settings were
saved at the same moment (the client writes both when you log out). To be certain which is your main account:
log out of your main, exit the game, click **Rescan**, and the newest account file is your main's. Double-click it
to name it. The line above each table always shows which file you're copying *from*.

## Privacy & where data lives
- All data, including login refresh tokens, is stored only on your computer:
  - Windows: `%APPDATA%\eve-localdesk\`
  - macOS: `~/Library/Application Support/eve-localdesk/`
  - Linux: `~/.local/share/eve-localdesk/`
- The app only talks to `login.eveonline.com`, `esi.evetech.net` and `images.evetech.net` (portraits).
- Removing a character deletes its token and cached data. You can also revoke access any time at
  <https://community.eveonline.com/support/third-party-applications/>.

## Notes & limitations
- If you add scopes to your application later, re-add the affected characters so their tokens include them.
  Characters missing scopes show a notice, and those modules show "no access".
- Corporation modules only work for characters holding the matching in-game roles (Director, Accountant,
  Station Manager, …). Without a role the module shows "no access" and is retried after a few hours.
- ESI caches data on its side. Assets update roughly hourly, and contacts and mail every few minutes.
- Asset values use CCP's average market prices (blueprint copies count as 0).
- PI extractor times only update after you view the colony in game (an ESI limitation).
- Not included from SeAT: its multi-user web parts (user accounts, squads, roles, Discord/Slack notification
  bots). The desktop Alerts page covers the personal-notification side.

## Building the downloads

Everything is built by GitHub Actions (`.github/workflows/build.yml`) on Windows, macOS and Linux runners.
Each build runs the tests, builds the app with PyInstaller (`packaging/eve-localdesk.spec`), starts the built
app with `--selftest` (checks the icon font, the app icon and that the built-in browser can render a page),
then packages it. On Windows that means an Inno Setup installer (`packaging/installer.iss`) plus a portable zip.

Every push to `main` builds and tests everything; the files appear as build artifacts on the run. To publish
a release:
1. Bump `__version__` in `localdesk/__init__.py`, e.g. to `0.3.0`.
2. Commit and push to `main`.
3. The workflow sees there's no `v0.3.0` release yet, creates the tag and release, and attaches the downloads.
Building locally: `pip install -r requirements.txt pyinstaller pillow`, then
`pyinstaller --noconfirm packaging/eve-localdesk.spec`. The output lands in `dist/`. PyInstaller only builds
for the system it runs on, so a Windows `.exe` needs Windows.

## Development
```
pip install -r requirements.txt pytest
python -m pytest tests
```
Code layout: `localdesk/esi.py` (ESI client), `sso.py` (login), `sync.py` (fetch + cache), `universe.py` (map data, route /
jump / range planners), `navigation.py` (bridges, access, jump ranges), `datasets.py`
(declarative modules for the data browser; add a `Dataset` to add a module), `contacts_sync.py`,
`eve_settings.py` (client settings files), `alerts.py`, `ui/` (PySide6 GUI; `ui/theme.py` holds the
AdminLTE-style stylesheet and colours), `packaging/` (icon, PyInstaller spec, Windows installer script).
