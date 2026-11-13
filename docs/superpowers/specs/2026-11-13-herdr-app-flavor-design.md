# herdeck for herdr-app — design

## Intent

herdeck is a touch deck: a PWA on a tablet that shows herdr agents as grouped
tiles, updates them live when their status changes, and focuses the agent on
tap — "like a Stream Deck". It works very well against herdr (terminal).

The user will mostly work with **herdr-app** (the desktop app, `Herdr.app`)
from now on, but wants to keep herdr terminal available as an option. Goal:
add a second "flavor" so the exact same deck works against herdr-app, as a
**separate app on the tablet**, without changing anything that works today.

## Goals

- A second PWA, served on its own port, whose tiles are **identical** to
  today's: status, grouping, live updates, Claude data (model, context, 5h
  quota), and tap-to-focus.
- The current herdr deck (`:7777`, its config, its installed PWA) keeps
  working exactly as it does now.
- Both flavors are served by the same herdeck process.

- Requested later by the user, for **both** flavors:
  - **Space filters** in addition to today's filters (see below).
  - **PIN login** replacing the "missing token" dead end (see below).
  - **Remove the widgets** row (Carga Mac, PRs abiertas) — it never worked
    well.

## Non-goals

- Reading agent output, sending prompts, quick replies, keys, plugin actions
  for herdr-app. The user does not use these on the deck; only tiles + focus.
- Showing herdr and herdr-app agents mixed in one grid. The user does not run
  both at once.
- An in-deck selector to switch flavor.

## Anti-goals

- Any regression or visible change on the existing `:7777` deck or its PWA
  (it must not need reinstalling), other than the three user-requested
  changes above (space filters, PIN, widgets removed).
- herdr-app tiles that look or behave differently from herdr tiles.

## Decisions taken with the user

1. **Separate URL per flavor, by port** (not by path). The current manifest
   (`herdeck.py:725`) has `start_url: "/"` and no `scope`, so a second PWA
   under `/app/` would fall inside the existing PWA's scope; moving it would
   force reinstalling. A different port is a different origin, so both PWAs
   are independent installs. Default: herdr stays on `7777`, herdr-app on
   `7778` (port number is configurable).
2. The herdr-app PWA must be distinguishable on the tablet: its own manifest
   `name`/`short_name` (e.g. "herdeck app") and its own icon or accent colour.
3. Claude data must appear on herdr-app tiles too.

## Current state (relevant facts)

- All server code is `herdeck.py` (~836 lines) + `deck.html`. Config:
  `~/.config/herdeck/config.json` (see `config.example.json`), with
  `sources[]` (today: `personal` local, `trabajo` over `ssh: workbox`).
- A `Source` thread (`herdeck.py:283`) talks to the herdr socket with
  newline JSON RPC (`rpc()`, `:132`), normalizes `session.snapshot`
  (`normalize_snapshot`, `:216`), subscribes to events, and publishes a
  per-source dict to `Hub` (`_publish`, `:362`). The deck only consumes
  `Hub.state()` over `/api/state` and `/api/stream`.
- Claude enrichment: the statusline script (`statusline/herdeck-statusline.py`)
  writes `~/.claude/herdeck/<session_id>.json`; `Source` matches it to agents
  through herdr's `agent_session` value (`:389`) and only for
  `kind == "claude"` (`:400`). For remote sources a reader script runs over
  ssh (`CLAUDE_READER`, `:51`).
- `STATUS_ORDER` (`:40`) knows `blocked, done, working, unknown, idle`.

## How herdr-app differs (verified on this machine)

- herdr-app is two layers:
  - **Window** — `herdr-app` CLI (`~/.local/bin/herdr-app`, app 0.1.15).
    `herdr-app ls` returns JSON `spaces[].tabs[].terminals[]` with names;
    `herdr-app do agent.focus --terminal tN` focuses an agent.
  - **Runtime** — herdr-core, CLI `hc` (0.1.21). JSON-RPC 2.0 over
    `~/.local/state/herdr-core/default/default.sock` (full API: `hc schema`).
    Methods include `terminal.list`, `terminal.get`, `events.subscribe`.
- None of the herdr methods herdeck uses exist in hc. A separate backend is
  required; pointing at another socket is not enough.
- `hc` already aggregates remote machines: `hc ls` lists `workbox/t1…` next to
  local `t1…`. No ssh tunnel is needed for the agent list.
- Terminal record (`hc get t3`): `status: {app: "claude-code", state, msg}`,
  `labels: {herdr.space, herdr.tab, where: "personal / herdeck"}`, and
  `foreground: {argv: ["claude"], pid: 50516}`.
- hc states: `idle | working | done | blocked | error`.
- **Claude link without touching the statusline:** Claude Code writes
  `~/.claude/sessions/<pid>.json` containing `sessionId`. So
  `foreground.pid` → `~/.claude/sessions/<pid>.json` → `sessionId` →
  existing `~/.claude/herdeck/<sessionId>.json`. Verified for a live session.
  Also verified remotely: `hc get workbox/t1` gives `foreground.pid` 1897594 and
  `ssh workbox cat ~/.claude/sessions/1897594.json` has its `sessionId`, so the
  pid is a pid on the remote host and the chain works there over ssh.
- `herdr-app ls` includes remote terminals: spaces carry `machine: "workbox"`
  (local spaces have `machine: null`) and terminals use the hc address
  (`workbox/t1`). Spaces seen today: `personal`, `lab` (local), `work`,
  `ops` (workbox). Non-agent terminals appear with `app: null` (hc
  `agent: "bash"`) and must not become tiles.
  Risk: `~/.claude/sessions/` is internal to Claude Code and may change.
  Fallback if it breaks: have the statusline also record its Claude pid; the
  rest of the chain stays.

## Design

### Shape

- One process, two HTTP listeners. The existing listener and its sources are
  untouched. A new, optional config block enables the herdr-app listener; if
  the block is absent, herdeck behaves exactly as today.
- The herdr-app listener serves the same `deck.html` and the same API
  surface, backed by its own `Hub` and its own sources, plus its own manifest.
- A new source backend for herdr-app produces the **same per-source dict**
  `_publish` produces today (agents with `id`, `status`, `since`, `source`,
  `source_label`, grouping info, `model`, `ctx`; `workspaces`; `counts`;
  `quota`), so `deck.html` renders it with no flavor-specific logic.

### herdr-app source backend

- **List + live updates:** `events.subscribe` on the hc socket (it streams a
  snapshot then changes for all terminals), with a periodic `terminal.list`
  as safety net, mirroring today's `poll_seconds` behaviour.
- **Which terminals are tiles:** terminals whose `status.app` is an agent
  (e.g. `claude-code`). Treat `claude-code` as Claude for all Claude logic.
- **Status mapping:** hc states map 1:1 by name; `error` must be added to
  `STATUS_ORDER` and rendered by the deck (builder decides its colour/order,
  near `blocked`).
- **Grouping:** space → workspace, tab → tab, using names from
  `herdr-app ls` (labels `herdr.space`/`herdr.tab` are ids; the `where`
  label is a fallback).
- **Personal / work split:** map hc machine to source — local terminals to
  one source (e.g. "Personal"), `workbox/*` to another (e.g. "Trabajo") — so the
  deck shows the same two groups with the same labels/colours as today.
- **Claude data:** per Claude terminal, resolve `foreground.pid` →
  `sessionId` as above. Local: read files directly. Remote machine: run the
  lookup over ssh, as today's `CLAUDE_READER` does (requires that source to
  have an `ssh` host configured). Quota is derived the same way as today.
- **Tap / focus:** `herdr-app do agent.focus --terminal <address>` and bring
  `Herdr.app` to the front.

### Manifest and PWA

- The herdr-app listener's `/manifest.webmanifest` uses its own name and
  icon/colour; `start_url: "/"`.
- Same token as the main deck. Note: cookies are not isolated by port, so the
  `herdeck` cookie is shared between `:7777` and `:7778` on the same host;
  using the same token makes that harmless. `localStorage` is per port.
- `--print-url` prints both URLs when the herdr-app block is enabled.

### Deck behaviour on `:7778` (calls made by Claude, not the user)

- Long-press normally opens a sheet whose first action is "read output",
  which herdr-app does not support. `/api/config` gains a capability flag
  (e.g. `features`) and the deck hides the sheet / read / prompt / keys /
  quick replies / plugin actions when the flag says so. On `:7777` the flag
  keeps today's behaviour, so nothing changes there.
- Focus on `workbox/*` uses the same `herdr-app do agent.focus --terminal
  workbox/tN` path (address form verified; focusing a remote one is not yet
  tested, check it during build).

### Space filters (both flavors)

- Today the filter bar (`deck.html:181`) has: Todo, Atención (n),
  Trabajando, and one button per source/host. Add one button per space
  after the host buttons: herdr workspaces on `:7777`, herdr-app spaces on
  `:7778` (today: personal, lab, work, ops).
- Single active filter, as today; remembered in `localStorage` per port.
  If the remembered space disappears, fall back to "Todo".
- Space identity must be stable across renames where possible (use the id,
  show the name); builder decides the exact key.

### PIN login (both flavors)

- Problem: the tablet periodically gets "Falta el token: abre la URL con
  ?t=TOKEN" (`herdeck.py` `do_GET`). The cookie has `Max-Age` one year, so it
  is not expiring by itself. Likely cause (unconfirmed): the PWA starts at
  `/` without `?t=`, and when the OS clears the PWA's storage/cookie there is
  no way to recover from inside the app. The PIN fixes this regardless of
  the exact cause; the builder should still try to confirm it.
- Behaviour: an unauthenticated request to `/` shows a PIN entry page
  (touch-friendly keypad, same look as the deck) instead of the plain-text
  401. A correct PIN sets the same `herdeck` cookie and loads the deck.
  API calls without auth still return 401, and the deck reacts by showing
  the PIN page rather than failing silently.
- One PIN shared by both ports (cookies are shared across ports on the same
  host anyway). Configured in `config.json`; created on first run like the
  token today and printed with `--print-url`.
- Brute-force protection: limit failed attempts (e.g. lockout with backoff
  per client IP). Builder picks numbers; must make guessing a 4–6 digit PIN
  impractical on the LAN.
- Claude's call: keep `?t=TOKEN` URLs and the existing cookie valid, so the
  installed PWA keeps working without re-entering anything until it next
  loses its cookie.

### Widgets removed (both flavors)

- Remove the widgets row from the deck and the `Widget` threads from the
  server. Ignore a `widgets` key if it is still in a user's config. Remove
  it from `config.example.json`.

### Small shared changes allowed

- Add `error` to `STATUS_ORDER` and its tile style.
- Claude checks accept `claude-code` as well as `claude`.

Anything beyond that in shared code must be a pure refactor that leaves the
herdr flavor's behaviour byte-for-byte the same.

## Left to the builder

- Exact config key names and shape for the herdr-app block (port, title,
  sources/machine mapping), with a matching entry in `config.example.json`.
- Whether hc is reached by its socket directly or via `hc` CLI calls, and
  JSON-RPC handshake details (check `hc schema`).
- Internal refactor of `Source` (backend interface vs. subclass).
- Icon/colour for the herdr-app PWA, `error` tile colour.
- Behaviour when hc or herdr-app is not running: show the source as
  disconnected, like today.
- Tests: follow whatever exists; at least cover the hc → agent-row mapping
  and the pid → session lookup with fixtures.
