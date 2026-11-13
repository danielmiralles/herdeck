# herdeck herdr-app flavor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve a second deck on its own port (default `7778`) backed by herdr-core (`hc`) + herdr-app, with tiles identical to the herdr deck, and add space filters, PIN login and widget removal to both decks without any other change to `:7777`.

**Architecture:** `Source` is split into a backend-neutral `BaseSource` (publishing, `since` tracking, Claude enrichment) and the unchanged herdr backend. A new `AppSource` per machine is fed by one `HcFeed` thread (JSON-RPC 2.0 on the hc socket, names from `herdr-app ls`) and publishes the same per-source dict into its own `Hub`. A second `ThreadingHTTPServer` in the same process serves `deck.html` with its own `AppDeck` (manifest, `/api/config` with a `features` flag, focus via `herdr-app do agent.focus`).

**Tech Stack:** Python >= 3.9 stdlib only (`http.server`, `socket`, `subprocess`, `unittest`), vanilla JS in `deck.html`.

**Spec:** `docs/superpowers/specs/2026-11-13-herdr-app-flavor-design.md`

## Global Constraints

- Zero third-party dependencies, tests included. Python floor 3.9 (`herdeck.py:4`): no `match`, no runtime `X | Y` types.
- Tests run from the repo root with `python3 -m unittest discover -s tests -v` (`python -m` puts the repo root on `sys.path`, so `import herdeck` works; `discover` puts `tests/` on it, so `import support` works).
- `:7777` must not change visibly and its PWA must not need reinstalling, other than space filters, PIN login and the removed widgets row. Its manifest stays exactly `{"name": "herdeck", "short_name": "herdeck", "display": "standalone", "background_color": "#0b0d10", "theme_color": "#0b0d10", "start_url": "/"}`.
- Any shared-code change beyond `error` in `STATUS_ORDER` and accepting `claude-code` as Claude must leave herdr behaviour byte-for-byte the same (the characterization tests from Task 1 guard this).
- Without a `herdr_app` config block herdeck behaves exactly as today (one listener, same sources).
- herdr-app listener: default port `7778`, configurable; `start_url: "/"`; same token as `:7777`.
- Cookie string stays `herdeck=<token>; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly`; `?t=TOKEN`, `X-Herdeck-Token` and the existing cookie stay valid.
- hc socket default `~/.local/state/herdr-core/default/default.sock`. Focus command `herdr-app do agent.focus --terminal <address>`; `Herdr.app` is `/Applications/Herdr.app`.
- Terminals whose `status.app` is missing or null never become tiles.
- User-visible copy is Spanish, matching the existing deck; code, comments and tests are English.
- Commits use Conventional Commits with no `Co-Authored-By` or AI attribution lines.
- Read-only probing of hc/herdr-app is fine (`hc ls`, `hc get`, `herdr-app ls`); never run `hc send`, `hc keys`, or other mutating commands outside the explicit manual focus checks.

## Verified protocol facts (do not re-derive)

- hc needs **no `hello`**: the socket speaks plain JSON-RPC 2.0, one JSON object per line, until a client asks for `hello {framed: true}`. Request: `{"jsonrpc": "2.0", "id": "hd_<hex>", "method": "terminal.list", "params": {}}` + `\n`. Reply: `{"id", "jsonrpc", "result"}` or `{"id", "jsonrpc", "error": {"code": -32601, "message": "...", "data": {"kind": "unknown_method", "hint": "..."}}}`.
- `terminal.list` result: `{"terminals": [Terminal], "machines": [{"name", "state", ...}], "hidden_finished": int}`. Each Terminal already carries `address`, `status` (`{"app", "state", "title"?, "msg"?}`, absent for bash), `labels` (`herdr.space`, `herdr.tab`, `where: "personal / herdeck"`), `foreground` (`{"argv", "cwd", "name", "pid"}`), `title`, `cwd`. Remote terminals are listed as `workbox/tN`.
- `events.subscribe {}` on its own connection: first line is the reply `{"result": {"seq", "terminals", "channel": null}}`; after that the connection carries notifications only: `{"jsonrpc": "2.0", "method": "heartbeat", "params": {"seq"}}` every 15 s, plus `event` and `missed` notifications.
- `herdr-app ls` → `{"recent": [], "spaces": [{"id", "name", "folder", "machine": null | "workbox", "active", "tabs": [{"id", "name", "settled", "terminals": [{"terminal", "pane", "status", "app", "sub_agents"}]}]}]}`. Remote ids are prefixed (`workbox/sp…`, `workbox/tb…`).
- `~/.claude/sessions/<pid>.json` contains `"sessionId"` for live Claude processes; `foreground.pid` is that pid (verified locally and on `workbox`). Old files may lack `sessionId`.

## Review Focus

1. A remote machine that hc lists as not `connected` (VPN down, `workbox` asleep): the Trabajo source must show as disconnected with a reason, like a dropped ssh tunnel today, and not as "connected, 0 agents". Test in Task 7 (`test_dispatch_marks_disconnected_machine`).
2. hc up but the herdr-app window closed (`herdr-app ls` fails or hangs): tiles still render, grouped by the `where` label, and the source stays connected. Tests in Task 5 (`test_normalize_hc_without_names_uses_where_label`) and Task 7 (`test_read_app_names_failure_returns_empty`).
3. Missing, stale or malformed `~/.claude/sessions/<pid>.json` (old file without `sessionId`, garbage JSON, path-traversal `sessionId`): the tile renders without model/ctx and nothing crashes. Tests in Task 6.
4. PIN guessing from many LAN addresses, or while locked out: a correct PIN sent during a lockout must still be refused, and spreading guesses across IPs must hit a global lockout. Tests in Task 4 (`test_global_lock_across_ips`, `test_login_locked_refuses_correct_pin`).
5. The remembered space filter is not present yet (deck loads before hc connects) or is gone: the deck shows "Todo" without overwriting the stored filter, so it comes back once the space reappears. Manual check in Task 3, step 7.

## File Structure

- `herdeck.py` — stays the single server file (existing pattern). New sections: `BaseSource`, hc client + mapping helpers, `AppSource`, `HcFeed`, `AppDeck`, `PinGuard`.
- `deck.html` — deck UI; gains space filters, `error` style, 401 handling, `features`/`accent` handling; loses widgets.
- `pin.html` — new, the PIN keypad page served to unauthenticated `/`.
- `install-macos.sh` — copies `pin.html` too.
- `config.example.json`, `README.md` — documentation of the new block and PIN.
- `tests/support.py` — `FakeHub`, `load_fixture(name)`, `FROZEN_NOW`.
- `tests/fixtures/` — `herdr_snapshot.json`, `herdr_publish_golden.json`, `hc_list.json`, `herdr_app_ls.json`.
- `tests/test_characterization.py`, `tests/test_spaces.py`, `tests/test_pin.py`, `tests/test_http.py`, `tests/test_hc.py`, `tests/test_claude_link.py`, `tests/test_app_source.py`.

---

### Task 1: Test harness, herdr characterization and `BaseSource` seam

**Files:**
- Create: `tests/support.py`, `tests/fixtures/herdr_snapshot.json`, `tests/fixtures/herdr_publish_golden.json`, `tests/test_characterization.py`
- Modify: `herdeck.py:280-581` (split `Source`)

**Interfaces:**
- Produces:
  - `tests/support.py`: `FROZEN_NOW = 1_800_000_000.0`; `class FakeHub` with `update_source(name, data)` storing into `self.sources: dict` and `self.calls: int`; `load_fixture(name: str) -> Any` (JSON from `tests/fixtures/`).
  - `herdeck.BaseSource(cfg: dict, hub: Hub)` with attributes `cfg, name_, hub, since, error, connected, pub_lock, dirty, claude_keys: List[str], claude_sessions, claude_rate, claude_last, claude_busy, claude_failed`, and methods `publish() -> None`, `_publish() -> None`, `maybe_refresh_claude() -> None`, `_refresh_claude(keys: List[str]) -> None`, plus hooks subclasses override: `snapshot_rows(self) -> Optional[Dict[str, Any]]` (returns `{"agents": [...], "workspaces": int}` or `None` when there is nothing to show) and `claude_key(self, row: dict) -> Optional[str]`, and `focus(self, pane: str) -> dict`.
  - `herdeck.Source(BaseSource, threading.Thread)` — the herdr backend, same constructor `Source(cfg, hub, herdr_bin)`.

- [ ] **Step 1: Write the fixture `tests/fixtures/herdr_snapshot.json`** (shape copied from a live `session.snapshot`)

```json
{"snapshot": {
  "focused_pane_id": "w1:p2",
  "workspaces": [
    {"workspace_id": "w1", "label": "personal"},
    {"workspace_id": "w2", "label": "lab", "branch": "main"}],
  "tabs": [
    {"tab_id": "w1:t1", "workspace_id": "w1", "label": "home"},
    {"tab_id": "w1:t2", "workspace_id": "w1", "label": "herdeck"},
    {"tab_id": "w2:t1", "workspace_id": "w2", "label": "site"}],
  "panes": [
    {"pane_id": "w1:p1", "workspace_id": "w1", "tab_id": "w1:t1", "cwd": "/Users/u", "agent": "claude", "agent_status": "idle", "terminal_title": "✳ Engram"},
    {"pane_id": "w1:p2", "workspace_id": "w1", "tab_id": "w1:t2", "cwd": "/Users/u/herdeck", "agent": "codex", "agent_status": "working"},
    {"pane_id": "w2:p1", "workspace_id": "w2", "tab_id": "w2:t1", "cwd": "/Users/u/site"}],
  "agents": [
    {"pane_id": "w1:p1", "agent": "claude", "agent_status": "blocked", "terminal_title": "✳ Engram",
     "agent_session": {"source": "herdr:claude", "agent": "claude", "kind": "id", "value": "c07e20e3-2305-4a65-a02a-8d8570552d82"},
     "workspace_id": "w1", "tab_id": "w1:t1", "focused": false},
    {"pane_id": "w1:p2", "agent": "codex", "agent_status": "working", "workspace_id": "w1", "tab_id": "w1:t2"}]
}}
```

- [ ] **Step 2: Write `tests/test_characterization.py`** against the current, unrefactored code

```python
class NormalizeSnapshotTest(unittest.TestCase):
    def test_agents_rows(self):
        out = herdeck.normalize_snapshot(load_fixture("herdr_snapshot.json"))
        self.assertEqual(out["pane_ids"], ["w1:p1", "w1:p2"])
        self.assertEqual(out["workspaces"], 2)
        a, b = out["agents"]
        self.assertEqual(a, {"pane_id": "w1:p1", "name": None, "kind": "claude", "status": "blocked",
            "workspace_id": "w1", "workspace": "personal", "branch": None, "tab": "home",
            "cwd": "/Users/u", "title": "✳ Engram", "focused": False,
            "session_id": "c07e20e3-2305-4a65-a02a-8d8570552d82"})
        self.assertEqual((b["kind"], b["status"], b["tab"], b["focused"], b["session_id"]),
                         ("codex", "working", "herdeck", True, None))

    def test_falls_back_to_panes_without_agents_list(self):
        snap = load_fixture("herdr_snapshot.json"); del snap["snapshot"]["agents"]
        out = herdeck.normalize_snapshot(snap)
        self.assertEqual(out["pane_ids"], ["w1:p1", "w1:p2"])  # w2:p1 has no agent
        self.assertEqual(out["agents"][0]["status"], "idle")

class PublishGoldenTest(unittest.TestCase):
    """_publish output for a fixed input must never change (except keys in ADDED_KEYS)."""
    ADDED_KEYS = {"source": set(), "agent": set()}   # later tasks add names here, never edit the golden
    def make_source(self): ...  # Source({"name": "personal", "label": "Personal", "color": "#7aa2f7",
                                #         "socket": "/nonexistent/herdr.sock"}, FakeHub(), "herdr")
                                # raw = fixture, connected = True,
                                # claude_sessions = {"c07e20e3-2305-4a65-a02a-8d8570552d82": {
                                #   "model": {"id": "claude-opus-4-1", "display_name": "Opus 4.1"},
                                #   "context_window": {"used_percentage": 42.04, "context_window_size": 200000}}},
                                # claude_rate = {"five_hour": {"used_percentage": 12.3, "resets_at": FROZEN_NOW + 3600},
                                #                "seven_day": {"used_percentage": 50, "resets_at": FROZEN_NOW - 1}}
    def test_publish_matches_golden(self): ...
        # with mock.patch("herdeck.time.time", return_value=FROZEN_NOW): src._publish()
        # data = hub.sources["personal"], minus ADDED_KEYS; claude ids from src.claude_keys
        #        (before the refactor: src.claude_ids) — compare
        #        {"data": data, "claude_ids": ids} to herdr_publish_golden.json.
        # When env HERDECK_WRITE_GOLDEN=1, write the file instead and skip the compare.
    def test_disconnected_publish(self): ...
        # connected = False, error = "boom" -> data["agents"] == [], data["connected"] is False,
        # data["error"] == "boom", "workspaces" not in data

class HubStateTest(unittest.TestCase):
    def test_sorts_by_status_then_newest(self):
        # sources with agents (status, since): (idle,5) (blocked,1) (done,9) (blocked,3) (working,2)
        # -> order of (status, since): blocked 3, blocked 1, done 9, working 2, idle 5
```

- [ ] **Step 3: Generate the golden from the current code and review it**

Run: `HERDECK_WRITE_GOLDEN=1 python3 -m unittest discover -s tests -p test_characterization.py -v && python3 -m json.tool tests/fixtures/herdr_publish_golden.json | head -60`
Expected: tests PASS; the golden shows `quota == {"five_hour": {"pct": 12.3, "resets_at": 1800003600.0}}` (seven_day dropped as expired), agent `w1:p1` with `"model": "opus"`, `"ctx": {"pct": 42.0, "size": 200000}`, `"id": "personal/w1:p1"`, `"since": 1800000000.0`, `"source_label": "Personal"`, `counts == {"blocked": 1, "working": 1}`, `claude_ids == ["c07e20e3-2305-4a65-a02a-8d8570552d82"]`.

- [ ] **Step 4: Run the suite normally to confirm the golden path passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 5: Commit the harness**

```bash
git add tests/
git commit -m "test: add characterization tests for herdr snapshot and publish"
```

- [ ] **Step 6: Refactor `Source` into `BaseSource` + herdr `Source` in `herdeck.py`**

Move into `BaseSource`: the state fields, `publish`, `_publish`, `maybe_refresh_claude`, `_refresh_claude`. `_publish` calls `self.snapshot_rows()` instead of `normalize_snapshot(self.raw)` and looks Claude info up with `self.claude_sessions.get(self.claude_key(a) or "")`; `self.claude_keys` = sorted set of non-`None` `self.claude_key(a)` over the rows. Rename `claude_ids` → `claude_keys`. The remote host for the reader stays `self.cfg.get("ssh")`. herdr `Source` keeps everything else (`_default_socket`, tunnel, `refresh`, `run`, `_session`, `cli`, `attach_cmd`, `has_local_client`, `focus`, `read`, `prompt`, `keys`, `plugin_action`) and implements:
- `snapshot_rows()` → `normalize_snapshot(self.raw) if self.connected and self.raw else None`
- `claude_key(row)` → `row["session_id"]` when `row["kind"].lower() == "claude"` and it matches `SESSION_ID_RE`, else `None`.

Update the test's `claude_ids` read to `claude_keys`.

- [ ] **Step 7: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS, golden unchanged (`git diff --exit-code tests/fixtures` exits 0).

- [ ] **Step 8: Manual smoke on `:7777`**

Run: `python3 herdeck.py --config ~/.config/herdeck/config.json` (stop the launchd copy first if it holds 7777: `launchctl list | rg herdeck`), open `http://127.0.0.1:7777/?t=<token>`.
Expected: same tiles, model chips and quota rings as before; `curl -s -H "X-Herdeck-Token: <token>" localhost:7777/api/state | python3 -m json.tool | head` shows agents.

- [ ] **Step 9: Commit**

```bash
git add herdeck.py tests/test_characterization.py
git commit -m "refactor: extract BaseSource from herdr Source"
```

---

### Task 2: Remove widgets

**Files:**
- Modify: `herdeck.py` (`Hub.widgets`, `Hub.update_widget`, `"widgets"` in `Hub.state()`, `class Widget`, `App.widgets`, `App.start`, module docstring line 10)
- Modify: `deck.html` (`.widget*` CSS at :73-76, `#widSec` at :110, `widgets` in `S` at :131, widget lines in `renderExtras` at :203-204)
- Modify: `config.example.json` (drop `"widgets"`), `README.md:19` (drop the Widgets bullet)
- Test: `tests/test_characterization.py`

**Interfaces:**
- Produces: `Hub.state()` returns `{"version", "now", "sources", "agents"}` only.

- [ ] **Step 1: Write the failing tests**

```python
class WidgetsRemovedTest(unittest.TestCase):
    def test_state_has_no_widgets(self):
        self.assertNotIn("widgets", herdeck.Hub().state())
    def test_widgets_config_key_is_ignored(self):
        cfg = {"sources": [{"name": "personal", "socket": "/nonexistent.sock"}],
               "widgets": [{"label": "x", "cmd": ["true"]}]}
        app = herdeck.App(cfg)
        self.assertFalse(hasattr(app, "widgets"))
        self.assertFalse(hasattr(herdeck, "Widget"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s tests -p test_characterization.py -v`
Expected: FAIL on both new tests.

- [ ] **Step 3: Delete the widget code in `herdeck.py`, `deck.html`, `config.example.json`, `README.md`**

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v && rg -n -i "widget" herdeck.py deck.html config.example.json README.md`
Expected: all PASS; `rg` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add herdeck.py deck.html config.example.json README.md tests/test_characterization.py
git commit -m "feat: remove widgets row and Widget threads"
```

---

### Task 3: Space filters (both flavors)

**Files:**
- Modify: `herdeck.py` (`BaseSource._publish`, new helpers next to `normalize_snapshot`)
- Modify: `deck.html` (`renderHeader` :181, `visible` :186, click handler :270)
- Test: `tests/test_spaces.py`, `tests/test_characterization.py` (`ADDED_KEYS`)

**Interfaces:**
- Produces:
  - `space_key(source: str, workspace_id: Any) -> Optional[str]` → `"ws:%s/%s" % (source, workspace_id)`, `None` when `workspace_id` is `None` or `""`.
  - `space_entries(agents: List[dict]) -> List[Dict[str, str]]` → `[{"key", "label"}]`, one per distinct `space_key`, in first-appearance order of the (pre-sort) agent list, label = the agent's `workspace` (falls back to the id).
  - Every published agent has `space_key`; every published source dict has `spaces` (empty list when disconnected). Both reach the deck through `Hub.state()` unchanged (`sources` summary keeps every key but `agents`/`raw`).

- [ ] **Step 1: Write the failing tests**

```python
class SpaceKeyTest(unittest.TestCase):
    def test_key_uses_source_and_id(self):
        self.assertEqual(herdeck.space_key("personal", "w1"), "ws:personal/w1")
        self.assertEqual(herdeck.space_key("trabajo", "workbox/sp51"), "ws:trabajo/workbox/sp51")
    def test_missing_id_has_no_key(self):
        self.assertIsNone(herdeck.space_key("personal", None))
        self.assertIsNone(herdeck.space_key("personal", ""))

class SpaceEntriesTest(unittest.TestCase):
    def test_distinct_in_order_with_names(self):
        agents = [{"space_key": "ws:p/w2", "workspace": "lab", "workspace_id": "w2"},
                  {"space_key": "ws:p/w1", "workspace": "personal", "workspace_id": "w1"},
                  {"space_key": "ws:p/w2", "workspace": "lab", "workspace_id": "w2"},
                  {"space_key": None, "workspace": None, "workspace_id": None}]
        self.assertEqual(herdeck.space_entries(agents),
                         [{"key": "ws:p/w2", "label": "lab"}, {"key": "ws:p/w1", "label": "personal"}])
    def test_rename_keeps_key(self):
        a = [{"space_key": "ws:p/w1", "workspace": "renamed", "workspace_id": "w1"}]
        self.assertEqual(herdeck.space_entries(a)[0]["key"], "ws:p/w1")

class PublishSpacesTest(unittest.TestCase):
    def test_published_source_lists_spaces(self):
        # herdr Source on the Task 1 fixture, _publish():
        # data["spaces"] == [{"key": "ws:personal/w1", "label": "personal"}]
        # every agent has space_key "ws:personal/w1"
```

In `test_characterization.py` set `ADDED_KEYS = {"source": {"spaces"}, "agent": {"space_key"}}`; the golden file is not regenerated.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p test_spaces.py -v`
Expected: FAIL, `module 'herdeck' has no attribute 'space_key'`.

- [ ] **Step 3: Implement `space_key`, `space_entries` and wire them into `BaseSource._publish`** (`data["spaces"] = []` in the base dict; when rows exist set each `a["space_key"]` then `data["spaces"] = space_entries(agents)`).

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS, including the golden test.

- [ ] **Step 5: Implement the deck side in `deck.html`**

- Options: after the host buttons append `...S.sources.flatMap(s => (s.spaces||[]).map(x => [x.key, x.label]))`.
- Effective filter: `function activeFilter(keys){ return filter.startsWith("ws:") && !keys.includes(filter) ? "all" : filter; }` — used for the `on` class and by `visible()`; the stored `filter` and `localStorage["hd_filter"]` are only written on a tap (existing click handler), never by the fallback.
- `visible()` gains `|| (f.startsWith("ws:") && a.space_key === f)` where `f = activeFilter(...)`.

- [ ] **Step 6: Browser check on `:7777`**

Run the server as in Task 1 step 8, open the deck.
Expected: buttons `Todo · Atención · Trabajando · Personal · Trabajo · <one per herdr workspace with agents>`; tapping a workspace shows only its tiles; reload keeps it selected.

- [ ] **Step 7: Browser check for the missing-space fallback (Review Focus 5)**

In the deck's dev console: `localStorage.setItem("hd_filter","ws:personal/nope"); location.reload()`.
Expected: "Todo" highlighted and all tiles shown; `localStorage.getItem("hd_filter")` still returns `"ws:personal/nope"`. Then select a real space, stop the server for 10 s and restart it: while reconnecting "Todo" shows, and the space filter comes back once state arrives.

- [ ] **Step 8: Commit**

```bash
git add herdeck.py deck.html tests/test_spaces.py tests/test_characterization.py
git commit -m "feat: add per-space filters to the deck"
```

---

### Task 4: PIN login (both flavors)

**Files:**
- Create: `pin.html`, `tests/test_pin.py`, `tests/test_http.py`
- Modify: `herdeck.py` (`load_config`, `make_handler`, new `PinGuard`, `main`), `deck.html` (fetch wrapper, `connect()` error path), `install-macos.sh:28` (copy `pin.html`)

**Interfaces:**
- Produces:
  - `PIN_RE = re.compile(r"\d{4,6}")`; `load_config` guarantees `cfg["pin"]` (generated `"%06d" % secrets.randbelow(10**6)` and persisted like the token; an existing value that does not `fullmatch` `PIN_RE` → `SystemExit("config: 'pin' debe tener entre 4 y 6 dígitos")`).
  - `class PinGuard(clock: Callable[[], float] = time.monotonic, free_per_ip: int = 5, free_global: int = 20, base: float = 30.0, cap: float = 3600.0)` with `wait(ip: str) -> float`, `failed(ip: str) -> float`, `succeeded(ip: str) -> None`. Thread-safe (one `threading.Lock`). Failures count for both `ip` and the global key `"*"`. For a key with `n` failures and allowance `f`: locked for `min(cap, base * 2 ** (n - f))` seconds after the n-th failure when `n >= f`. `wait` returns the max remaining lock of `ip` and `"*"`, never counts. `succeeded` clears `ip` and `"*"`. (6-digit PIN: ~30 guesses/day per IP after the first ~2 h, global cap similar → >40 years for half the space.)
  - `make_handler(app, token: str, pin: str, guard: PinGuard)`; `app` needs `cfg, html, hub, client_config(), manifest(), raw(), action(body)` (Task 7 adds `manifest()`/`raw()` to the real classes; in this task add them to `App` now: `manifest()` returns the exact `:7777` dict from Global Constraints, `raw()` returns today's `/api/raw` body).
  - `POST /api/login` body `{"pin": "<digits>"}` (read at most 1024 bytes), no auth required:
    - locked → `429 {"ok": false, "error": "locked", "retry_after": ceil(wait)}` without checking the PIN;
    - correct (`secrets.compare_digest`) → `guard.succeeded(ip)`, `200 {"ok": true}` + the exact cookie header;
    - wrong → `401 {"ok": false, "error": "bad_pin", "retry_after": ceil(guard.failed(ip))}` (`retry_after` 0 while still in the free allowance).
  - Unauthenticated `GET /` or `/index.html` → `200` with `pin.html` (`text/html; charset=utf-8`, `Cache-Control: no-store`) and one `log("[auth] sin credenciales: %s %s UA=%s")` line (ip, path, User-Agent[:80]) to help confirm the root cause. Any other unauthenticated GET keeps today's `401` text body; unauthenticated POST to other paths keeps `401 {"error": "unauthorized"}`.
  - `App.html_pin` (and later `AppDeck.html_pin`) = `(HERE / "pin.html").read_text(...)`.

- [ ] **Step 1: Write the failing `PinGuard` and config tests in `tests/test_pin.py`**

```python
class PinGuardTest(unittest.TestCase):
    # clock is a list-backed fake: now = [0.0]; guard = PinGuard(clock=lambda: now[0])
    def test_four_failures_do_not_lock(self):        # 4x failed("a") -> wait("a") == 0
    def test_fifth_failure_locks_30s(self):           # 5th failed("a") returns 30.0; wait("a") == 30.0
    def test_backoff_doubles_and_caps(self):          # 6th -> 60.0, 7th -> 120.0, 12th -> 3600.0, 20th -> 3600.0
    def test_lock_expires(self):                      # 5 fails, now[0] += 30 -> wait("a") == 0
    def test_wait_does_not_count(self):               # 4 fails, 10x wait("a"), 1 fail -> lock 30.0 (not more)
    def test_success_resets_ip(self):                 # 4 fails, succeeded("a"), 4 fails -> wait("a") == 0
    def test_global_lock_across_ips(self):            # failed("10.0.0.%d" % i) for i in range(20) -> wait("10.0.0.99") == 30.0
    def test_other_ip_not_locked_by_one_ip(self):     # 5x failed("a") -> wait("b") == 0

class PinConfigTest(unittest.TestCase):
    def test_generates_and_persists_pin(self):        # tmp config without "pin" -> cfg["pin"] fullmatches \d{6}; file on disk has same pin
    def test_keeps_existing_pin(self):                # "pin": "4321" -> unchanged
    def test_rejects_invalid_pin(self):               # "pin": "12ab" and "pin": "123" -> SystemExit
```

- [ ] **Step 2: Write the failing HTTP tests in `tests/test_http.py`**

Start `herdeck.Server(("127.0.0.1", 0), herdeck.make_handler(stub, "TOK", "123456", PinGuard()))` in a daemon thread; `stub = SimpleNamespace(cfg={}, html="<deck/>", html_pin=<real pin.html text>, hub=herdeck.Hub(), client_config=lambda: {"title": "t"}, manifest=lambda: {"name": "herdeck"}, raw=lambda: {}, action=lambda b: {"ok": True})`; use `http.client`.

```python
def test_root_without_auth_serves_pin_page(self):   # GET / -> 200, body contains 'id="pinpad"'
def test_api_without_auth_is_401(self):             # GET /api/state -> 401; POST /api/action -> 401
def test_token_query_still_works(self):             # GET /?t=TOK -> 200, body "<deck/>", Set-Cookie == COOKIE
def test_token_header_still_works(self):            # GET /api/state with X-Herdeck-Token: TOK -> 200
def test_wrong_pin(self):                            # POST /api/login {"pin":"000000"} -> 401, json error "bad_pin", retry_after 0
def test_right_pin_sets_cookie(self):               # POST /api/login {"pin":"123456"} -> 200, Set-Cookie == COOKIE;
                                                     # then GET /api/state with that cookie -> 200
def test_login_locked_refuses_correct_pin(self):    # 5 wrong, then right -> 429, error "locked", retry_after 30
```
with `COOKIE = "herdeck=TOK; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly"`.

- [ ] **Step 3: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p "test_[ph]*.py" -v`
Expected: FAIL, `PinGuard` undefined / `make_handler()` takes 2 positional arguments.

- [ ] **Step 4: Implement `PIN_RE`, `PinGuard`, the `load_config` change, the handler changes and `App.manifest()/raw()/html_pin` in `herdeck.py`; `main()` builds one `PinGuard()` and passes `cfg["pin"]`; `--print-url` prints a second line `PIN: <pin>`.**

- [ ] **Step 5: Write `pin.html`**

Same `:root` colour tokens and font as `deck.html`; title `herdeck`; a 6-dot indicator (`id="dots"`), a 3×4 keypad (`id="pinpad"`, keys 1–9, ⌫, 0, `Entrar`) with buttons at least 64 px; status line `id="msg"`. Auto-submits when the length reaches `6`, `Entrar` submits shorter PINs (4–5 digits). Copy: `"Introduce el PIN"`, `"PIN incorrecto"`, `"Demasiados intentos. Espera %d s"` (countdown from `retry_after`, keypad disabled meanwhile). On `200` → `location.replace("/")`.

- [ ] **Step 6: Make the deck react to 401** in `deck.html`

`async function api(url, opts)` wraps `fetch`; on `r.status === 401` it calls `location.replace("/")` and throws. Use it for `/api/config`, `/api/state` and in `post()`. In `connect()`'s `onerror`, before scheduling the retry, `fetch("/api/config")` and `location.replace("/")` on 401.

- [ ] **Step 7: Add `pin.html` to the `cp` line in `install-macos.sh:28`.**

- [ ] **Step 8: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 9: Browser check**

Run the server; in a private window open `http://<lan-ip>:7777/`.
Expected: keypad page; a wrong PIN shows "PIN incorrecto"; the right PIN (from `python3 herdeck.py --print-url`) loads the deck; deleting the `herdeck` cookie in devtools while the deck is open sends the deck back to the keypad within one SSE retry (≤ 3 s); the installed tablet PWA still opens the deck without asking for the PIN.

- [ ] **Step 10: Root-cause note**

For a few days after deploying, check `~/Library/Logs` (launchd stderr path from `install-macos.sh`) for `[auth] sin credenciales` lines from the tablet IP with the PWA's User-Agent. Record in the commit body of Task 9 whether they appear after iPadOS clears site data; this is the evidence the spec asks for.

- [ ] **Step 11: Commit**

```bash
git add herdeck.py deck.html pin.html install-macos.sh tests/test_pin.py tests/test_http.py
git commit -m "feat: add PIN login page with per-IP and global lockout"
```

---

### Task 5: Shared status/kind changes, hc client and hc → agent-row mapping

**Files:**
- Create: `tests/test_hc.py`, `tests/fixtures/hc_list.json`, `tests/fixtures/herdr_app_ls.json`
- Modify: `herdeck.py` (`STATUS_ORDER`, new helpers), `deck.html` (`error` colour, label, glyph, counts, attention)
- Test: `tests/test_characterization.py` (Hub sort with `error`)

**Interfaces:**
- Produces:
  - `STATUS_ORDER = {"blocked": 0, "error": 1, "done": 2, "working": 3, "unknown": 4, "idle": 5}` (relative order of the old states unchanged).
  - `CLAUDE_KINDS = {"claude", "claude-code"}`; `is_claude(kind: Any) -> bool` (case-insensitive). herdr `Source.claude_key` uses it.
  - `HC_SOCKET = "~/.local/state/herdr-core/default/default.sock"`.
  - `hc_call(sock_path: str, method: str, params: Optional[dict] = None, timeout: float = 5.0) -> dict` — JSON-RPC 2.0 line request as in "Verified protocol facts"; error → `RuntimeError("<data.kind or code>: <message>")`; empty reply → `ConnectionError("respuesta vacía de hc")`. Reads until the first `\n`.
  - `machine_of(address: str) -> Optional[str]` — `"workbox/t1"` → `"workbox"`, `"t1"` → `None`.
  - `app_names(ls: Any) -> Dict[str, Dict[str, Any]]` — terminal address → `{"space_id", "space", "tab"}` from `herdr-app ls`; `{}` for anything malformed.
  - `normalize_hc(terminals: List[dict], names: Dict[str, dict]) -> Dict[str, Any]` → `{"agents": rows, "workspaces": <distinct workspace_id count>}`. Row keys exactly those of `normalize_snapshot` rows plus `pid`:
    - `pane_id` = `address`; `name` = terminal `name`; `kind` = `"claude"` when `status.app == "claude-code"`, else `status.app`; `status` = lowercased `status.state` if in `STATUS_ORDER`, else `"unknown"`;
    - `workspace_id`/`workspace`/`tab` from `names[address]`, else from `labels.where` split on the first `" / "` (`workspace_id` = `labels["herdr.space"]` or the space name);
    - `branch` = `None`; `cwd` = `foreground.cwd` or `cwd`; `title` = `title` or `status.title` or `status.msg`; `focused` = `False`; `session_id` = `None`; `pid` = `foreground.pid` when it is a positive `int`, else `None`.
    - Rows only for terminals with a dict `status` whose `app` is truthy; input order preserved.
  - `split_by_machine(terminals: List[dict], machines: List[Optional[str]]) -> Dict[Optional[str], List[dict]]` — terminals of machines not in `machines` are dropped.
  - `machine_errors(list_result: dict) -> Dict[str, str]` — `{name: state}` for each `machines[]` entry whose `state != "connected"`.

- [ ] **Step 1: Write fixtures** — `hc_list.json` is a trimmed real `terminal.list` result: local `t1` (claude-code, idle, `foreground.pid` 45915, `labels: {"herdr.space": "sp75f1bfb20e4cc7a8061f48571bc0f708", "herdr.tab": "tb9850e759c31fce496da1638292025a22", "where": "personal / proj-a"}`, `title: "Problemas con engram"`), local `t3` (claude-code, `status: {"app": "claude-code", "state": "working", "title": "Write plan"}`, no `title`), `workbox/t1` (claude-code, `state: "blocked"`, `foreground.pid` 1897594), `workbox/t3` (bash: no `status`), `workbox/t9` (`status: {"app": "codex", "state": "error"}`), `t8` (`status: {"app": null, "state": "idle"}`); `machines: [{"name": "workbox", "state": "connected"}]`. `herdr_app_ls.json` is a trimmed real `herdr-app ls` with spaces `personal` (`sp75…`, tabs `proj-a`→`t1`, `herdeck`→`t3`) and `work` (`workbox/spba…`, `machine: "workbox"`, tab `PROJ-101`→`workbox/t1`).

- [ ] **Step 2: Write the failing tests in `tests/test_hc.py`**

```python
class NormalizeHcTest(unittest.TestCase):
    def setUp(self): self.terms = load_fixture("hc_list.json")["terminals"]; self.names = herdeck.app_names(load_fixture("herdr_app_ls.json"))
    def test_only_agent_terminals_become_rows(self):
        self.assertEqual([r["pane_id"] for r in herdeck.normalize_hc(self.terms, self.names)["agents"]],
                         ["t1", "t3", "workbox/t1", "workbox/t9"])
    def test_claude_row_matches_herdr_shape(self):
        r = herdeck.normalize_hc(self.terms, self.names)["agents"][0]
        self.assertEqual(set(r), set(herdeck.normalize_snapshot(load_fixture("herdr_snapshot.json"))["agents"][0]) | {"pid"})
        self.assertEqual((r["kind"], r["status"], r["workspace"], r["tab"], r["workspace_id"], r["pid"], r["title"]),
                         ("claude", "idle", "personal", "proj-a", "sp75f1bfb20e4cc7a8061f48571bc0f708", 45915, "Problemas con engram"))
    def test_title_falls_back_to_status_title(self):    # t3 title == "Write plan"
    def test_status_mapping(self):                        # workbox/t1 "blocked", workbox/t9 "error" with kind "codex"
    def test_unknown_state_becomes_unknown(self):         # state "weird" -> "unknown"
    def test_normalize_hc_without_names_uses_where_label(self):
        r = herdeck.normalize_hc(self.terms, {})["agents"][0]
        self.assertEqual((r["workspace"], r["tab"], r["workspace_id"]), ("personal", "proj-a", "sp75f1bfb20e4cc7a8061f48571bc0f708"))
    def test_bad_pid_is_none(self):                       # foreground {"pid": "x"} / missing foreground -> pid None
    def test_workspaces_count(self):                      # == number of distinct workspace_id among rows

class HcHelpersTest(unittest.TestCase):
    def test_machine_of(self):        # "workbox/t1" -> "workbox"; "t1" -> None
    def test_split_by_machine(self):  # machines [None, "workbox"] -> {None: [t1,t3,t8], "workbox": [workbox/t1,workbox/t3,workbox/t9]}; [None] drops workbox
    def test_machine_errors(self):    # {"machines": [{"name": "workbox", "state": "connecting"}]} -> {"workbox": "connecting"}; connected -> {}
    def test_app_names_malformed(self):  # app_names(None) == {}, app_names({"spaces": "x"}) == {}
    def test_is_claude(self):         # "claude", "Claude-Code" True; "codex", None False

class HcCallTest(unittest.TestCase):
    # a socketserver.UnixStreamServer in a TemporaryDirectory answers one line per connection
    def test_success_returns_result(self):   # server replies {"jsonrpc":"2.0","id":..,"result":{"terminals":[]}} -> {"terminals": []};
                                             # request line parsed by server has jsonrpc "2.0", method, params {}
    def test_error_raises_with_kind(self):   # reply error {"code":-32601,"message":"unknown method","data":{"kind":"unknown_method"}}
                                             # -> RuntimeError whose str starts with "unknown_method"
    def test_empty_reply_raises(self):       # server closes without writing -> ConnectionError
```

Add to `HubStateTest`: `test_error_sorts_after_blocked_before_done`.

- [ ] **Step 3: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p test_hc.py -v`
Expected: FAIL, `module 'herdeck' has no attribute 'normalize_hc'`.

- [ ] **Step 4: Implement the helpers and the `STATUS_ORDER` / `is_claude` changes in `herdeck.py`**

- [ ] **Step 5: Deck `error` style in `deck.html`**

`--error:#ff9e64` in `:root`; `LBL.error = "Error"`; `.tile.error` gradient like `.tile.blocked` with `rgba(255,158,100,…)`; glyph `!` for `error`; header counts gain `${c.error?`<span class="e">● ${c.error}</span> `:""}` with `.cnt .e{color:var(--error)}`; Atención counts and filters `blocked || done || error`. `KIND_ICON` gains `"claude-code": "i-claude"`.

- [ ] **Step 6: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS, golden unchanged.

- [ ] **Step 7: Live read-only check**

Run: `python3 -c "import herdeck,os,json; r=herdeck.hc_call(os.path.expanduser(herdeck.HC_SOCKET),'terminal.list'); print(json.dumps(herdeck.normalize_hc(r['terminals'], {})['agents'][:2], indent=1))"`
Expected: two rows with `kind: "claude"`, a real `pid`, workspace/tab from `where`.

- [ ] **Step 8: Commit**

```bash
git add herdeck.py deck.html tests/test_hc.py tests/fixtures/hc_list.json tests/fixtures/herdr_app_ls.json tests/test_characterization.py
git commit -m "feat: add hc client and herdr-app terminal mapping"
```

---

### Task 6: Claude pid → session link (local and remote)

**Files:**
- Modify: `herdeck.py` (`CLAUDE_READER` :51-77, new `app_claude_key`)
- Test: `tests/test_claude_link.py`

**Interfaces:**
- Consumes: rows from `normalize_hc` (Task 5).
- Produces:
  - `CLAUDE_READER` accepts argv items `pid:<digits>` besides plain session ids. For `pid:N` it reads `~/.claude/sessions/N.json`, takes `sessionId` only if it fullmatches `[A-Za-z0-9-]{1,128}`, then reads `~/.claude/herdeck/<sessionId>.json`; the result is stored under the original argv key (`"pid:N"`). Plain ids behave exactly as today. Rate-limit lookup unchanged.
  - `app_claude_key(row: dict) -> Optional[str]` → `"pid:%d" % row["pid"]` when `is_claude(row["kind"])` and `row["pid"]` is a positive int, else `None`.

- [ ] **Step 1: Write the failing tests**

Run the reader exactly like production: `subprocess.run([sys.executable, "-"] + args, input=herdeck.CLAUDE_READER, capture_output=True, text=True, env=dict(os.environ, HOME=tmp))`, with `tmp/.claude/sessions/` and `tmp/.claude/herdeck/` built per test.

```python
def test_pid_resolves_to_statusline_data(self):     # sessions/123.json {"pid":123,"sessionId":"sid-a"}, herdeck/sid-a.json {"model":{"id":"claude-opus-4-1"},"context_window":{"used_percentage":10}}
                                                     # args ["pid:123"] -> out["sessions"]["pid:123"]["model"]["id"] == "claude-opus-4-1"
def test_plain_session_id_unchanged(self):           # args ["sid-a"] -> out["sessions"]["sid-a"] present, no "pid:" keys
def test_missing_session_file(self):                 # args ["pid:999"] -> "pid:999" not in sessions; returncode 0
def test_session_file_without_session_id(self):      # sessions/5.json {"peerToken":"x"} -> absent
def test_garbage_session_file(self):                 # sessions/6.json "{not json" -> absent
def test_traversal_session_id_rejected(self):        # sessionId "../../etc/passwd" -> absent
def test_non_digit_pid_ignored(self):                # args ["pid:12a"] -> absent
def test_rate_limits_still_read(self):               # herdeck/sid-a.json has rate_limits {"five_hour":{...}} -> out["rate_limits"] equals it

class AppClaudeKeyTest(unittest.TestCase):
    def test_claude_with_pid(self):   # {"kind": "claude", "pid": 50516} -> "pid:50516"
    def test_non_claude(self):        # {"kind": "codex", "pid": 1} -> None
    def test_no_pid(self):            # {"kind": "claude", "pid": None} -> None; pid 0 -> None; pid True -> None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p test_claude_link.py -v`
Expected: FAIL on the `pid:` tests and `app_claude_key`; `test_plain_session_id_unchanged` and `test_rate_limits_still_read` already PASS.

- [ ] **Step 3: Extend `CLAUDE_READER` and add `app_claude_key`.**

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS, golden unchanged.

- [ ] **Step 5: Live check, local and remote**

Run: `PID=$(hc get t3 | python3 -c 'import json,sys;print(json.load(sys.stdin)["foreground"]["pid"])'); python3 -c "import herdeck;print(herdeck.CLAUDE_READER)" > /tmp/herdeck-reader.py; python3 /tmp/herdeck-reader.py pid:$PID`
Expected: `sessions` has `pid:<PID>` with a `model` (requires the herdeck statusline installed).
Run: `RP=$(hc get workbox/t1 | python3 -c 'import json,sys;print(json.load(sys.stdin)["foreground"]["pid"])'); ssh -o BatchMode=yes workbox python3 - pid:$RP < /tmp/herdeck-reader.py`
Expected: `sessions` has `pid:<RP>` when the remote statusline is installed; otherwise `{"sessions": {}, ...}` and exit code 0.

- [ ] **Step 6: Commit**

```bash
git add herdeck.py tests/test_claude_link.py
git commit -m "feat: resolve Claude sessions from process pid"
```

---

### Task 7: herdr-app sources, hc feed and the `:7778` listener

**Files:**
- Modify: `herdeck.py` (new `AppSource`, `HcFeed`, `read_app_names`, `AppDeck`; `App.client_config`; `load_config`; `main`), `deck.html` (`features`, `accent`)
- Test: `tests/test_app_source.py`, `tests/test_http.py`

**Interfaces:**
- Consumes: `BaseSource` (Task 1), `space_key`/`space_entries` (Task 3), `PinGuard`/`make_handler(app, token, pin, guard)` (Task 4), `hc_call`, `normalize_hc`, `split_by_machine`, `machine_errors`, `app_names` (Task 5), `app_claude_key` (Task 6).
- Produces:
  - Config block (key `herdr_app`; enabled when present and `enabled` is not `false`):
    `{"port": 7778, "title": "herdeck app", "accent": "#bb9af7", "hc_socket": HC_SOCKET, "herdr_app_bin": "herdr-app", "app_name": "Herdr", "poll_seconds": 20, "sources": [{"name", "label", "color", "icon"?, "machine": null | "<hc machine>", "ssh"?: "<host for the Claude reader>"}]}` — every key but `sources` optional with these defaults. `load_config` raises `SystemExit` when source names repeat, when two sources share a `machine`, or when `port` equals the main `port`.
  - `AppSource(BaseSource)`: `__init__(cfg: dict, hub: Hub, block: dict)`; `machine: Optional[str]`; `terminals: List[dict]`; `names: Dict[str, dict]`; `set_data(terminals: List[dict], names: Dict[str, dict], connected: bool, error: Optional[str]) -> None`; `snapshot_rows()` → `normalize_hc(self.terminals, self.names)` when connected, else `None`; `claude_key = app_claude_key`. Source `icon` default stays `"work" if cfg.get("ssh") else "home"` (inherited), so Personal/Trabajo look as on `:7777`.
  - `read_app_names(bin: str, timeout: float = 5.0) -> Dict[str, dict]` — runs `[bin, "ls"]`, returns `app_names(json)`; any failure (missing binary, non-zero, timeout, bad JSON) → `{}` and one log line until it recovers.
  - `HcFeed(threading.Thread)`: `__init__(block: dict, sources: List[AppSource])`; `dirty: threading.Event`; `dispatch(list_result: dict, names: Dict[str, dict]) -> None`; `fail(error: str) -> None`; `run()` reconnect loop with backoff 1 s doubling to 30 s (as `Source.run`); `_session()` opens its own socket, sends `events.subscribe {}`, skips the reply line (its snapshot has no `machines`), does one immediate refresh (`terminal.list` + `read_app_names` + `dispatch`), then reads notifications with a 1 s socket timeout: `heartbeat` is ignored, any other notification sets `dirty`; when `dirty` (after a 0.15 s burst wait) or every `poll_seconds`, it calls `terminal.list` + `read_app_names` and `dispatch`es; it calls `maybe_refresh_claude()` on each source every loop; socket EOF → `ConnectionError("hc cerró la suscripción")`.
  - `dispatch` semantics: for each source, if `source.machine` is set and (`source.machine in machine_errors(r)` or not listed in `r["machines"]`) → `set_data([], names, False, "%s: %s" % (machine, state or "no está en hc"))`; else `set_data(split[source.machine], names, True, None)`; then `publish()`. `fail(e)` → every source `set_data([], {}, False, e[:300])` + `publish()`.
  - `AppDeck`: `__init__(cfg: dict, block: dict)`; `cfg` (root config, for `access_log`), `block`, `hub`, `sources: Dict[str, AppSource]`, `feed: HcFeed`, `html`, `html_pin`; `start()`; `client_config()` → `{"allow_input": False, "quick_replies": [], "plugin_actions": [], "read_lines": 60, "title": <title>, "version": VERSION, "features": {"sheet": False}, "accent": <accent>}`; `manifest()` → `{"name": <title>, "short_name": <title>, "display": "standalone", "background_color": "#0b0d10", "theme_color": <accent>, "start_url": "/"}`; `raw()` → `{"socket": <path>, "error": <first source error>, "terminals": <last terminal.list terminals>}`; `action(body)` (Task 8; here only `"refresh"` → `feed.dirty.set()`, anything else `ValueError("acción no disponible en herdr-app: %s")`).
  - `App.client_config()` adds `"features": {"sheet": True}` (no `accent` key, so `:7777` styling is untouched).

- [ ] **Step 1: Write the failing tests in `tests/test_app_source.py`**

```python
# block = {"sources": [{"name": "personal", "label": "Personal", "color": "#7aa2f7", "machine": None},
#                      {"name": "trabajo", "label": "Trabajo", "color": "#e0af68", "machine": "workbox", "ssh": "workbox"}]}
# feed = HcFeed(block, [AppSource(s, hub, block) for s in block["sources"]]) with hub = FakeHub()
def test_dispatch_splits_by_machine(self):
    # feed.dispatch(load_fixture("hc_list.json"), names) under frozen time
    # hub.sources["personal"]["agents"] ids == ["personal/t1", "personal/t3"]
    # hub.sources["trabajo"]["agents"] ids == ["trabajo/workbox/t1", "trabajo/workbox/t9"]
    # both connected True; trabajo["remote"] is True and icon "work"; personal icon "home"
    # trabajo["spaces"] == [{"key": "ws:trabajo/workbox/spba2ecd8563af0e39a13371ec5922392a", "label": "work"}, ...]
def test_dispatch_marks_disconnected_machine(self):
    # machines [{"name": "workbox", "state": "connecting"}] -> trabajo connected False, error "workbox: connecting", agents []
    # personal still connected with its rows
def test_dispatch_machine_missing_from_hc(self):
    # machines [] -> trabajo error "workbox: no está en hc"
def test_fail_marks_all_disconnected(self):
    # feed.fail("hc: socket missing") -> both sources connected False, error "hc: socket missing"
def test_claude_info_by_pid(self):
    # personal source: claude_sessions = {"pid:45915": {"model": {"id": "claude-sonnet-4-5"}, "context_window": {"used_percentage": 61.2}}}
    # after dispatch, agent t1 has model "sonnet", ctx {"pct": 61.2, "size": None}; personal.claude_keys includes "pid:45915"
def test_rows_identical_shape_to_herdr(self):
    # set(app agent keys) == set(herdr published agent keys from the Task 1 golden run) | {"pid"}
def test_read_app_names_failure_returns_empty(self):
    # read_app_names("/nonexistent/herdr-app") == {}; with a script printing "not json" -> {}
def test_config_block_validation(self):
    # load_config on tmp files: duplicate machine -> SystemExit; port 7777 == main port -> SystemExit; no block -> no "herdr_app" key added

class AppDeckConfigTest(unittest.TestCase):
    def test_client_config_hides_sheet(self):   # AppDeck(cfg, block).client_config()["features"] == {"sheet": False}
    def test_manifest_is_distinct(self):        # name/short_name "herdeck app", theme_color "#bb9af7", start_url "/"
    def test_herdr_app_keeps_sheet(self):       # App(cfg).client_config()["features"] == {"sheet": True}
    def test_herdr_manifest_unchanged(self):    # App(cfg).manifest() == the exact :7777 dict from Global Constraints
```

In `tests/test_http.py` add `test_two_listeners_share_cookie_and_pin`: two `Server`s on port 0 (stub apps with different `manifest()`), one shared `PinGuard`; login on the first; the cookie works on the second's `/api/state`; each `/manifest.webmanifest` returns its own dict.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p test_app_source.py -v`
Expected: FAIL, `module 'herdeck' has no attribute 'AppSource'`.

- [ ] **Step 3: Implement `AppSource`, `read_app_names`, `HcFeed`, `AppDeck`, the `App.client_config` flag and the `load_config` validation.**

- [ ] **Step 4: Wire the second listener in `main()`**

When the block is enabled: `deck2 = AppDeck(cfg, block); deck2.start(); srv2 = Server((host, block_port), make_handler(deck2, cfg["token"], cfg["pin"], guard))`, served by a daemon thread; log `"herdeck app escuchando en %s:%d"`. Without the block, `main()` runs exactly the current code path.

- [ ] **Step 5: Deck flags in `deck.html`**

`const sheetOn = () => !(CFG.features && CFG.features.sheet === false);` — the `pointerdown` long-press timer only starts when `sheetOn()`; `renderExtras` hides `#plugSec` when not `sheetOn()`. If `CFG.accent` is set: `$("title").style.color = CFG.accent` and `document.querySelector('meta[name=theme-color]').content = CFG.accent`.

- [ ] **Step 6: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS, golden unchanged.

- [ ] **Step 7: Live check**

Add to `~/.config/herdeck/config.json`: `"herdr_app": {"sources": [{"name": "personal", "label": "Personal", "color": "#7aa2f7", "machine": null}, {"name": "trabajo", "label": "Trabajo", "color": "#e0af68", "machine": "workbox", "ssh": "workbox"}]}`. Run the server; open `:7778/?t=<token>` and `:7777/?t=<token>` side by side.
Expected on `:7778`: header "herdeck app" in purple; Personal and Trabajo chips with the same colours/icons as `:7777`; tiles for every claude-code terminal from `hc ls` (no bash tiles), grouped as space · tab; model chip and context ring on Claude tiles within ~10 s; 5h quota ring on each source; space buttons `personal · lab · work · ops`; long-press does nothing. Status flips within ~1 s when an agent starts/stops working. `:7777` unchanged (long-press still opens the sheet).

- [ ] **Step 8: Failure-mode checks**

Quit Herdr.app (window only; hc keeps running) → `:7778` tiles stay, grouped from `where`. `hc server stop` is mutating — instead point `hc_socket` at a missing path in the block and restart → both sources red with the hc error in the banner, `:7777` unaffected.

- [ ] **Step 9: Commit**

```bash
git add herdeck.py deck.html tests/test_app_source.py tests/test_http.py
git commit -m "feat: serve herdr-app deck on its own port"
```

---

### Task 8: Focus on `:7778`

**Files:**
- Modify: `herdeck.py` (`AppSource.focus`, `AppSource.activate`, `AppDeck.action`)
- Test: `tests/test_app_source.py`

**Interfaces:**
- Produces:
  - `AppSource.focus(address: str) -> dict` — `address` must be the `pane_id` of a current row of this source, else `ValueError("terminal desconocido: %s")`; runs `[herdr_app_bin, "do", "agent.focus", "--terminal", address]` (timeout 10 s); non-zero → `RuntimeError(stderr or stdout or "focus falló")`; returns `{"ok": True}`.
  - `AppSource.activate() -> None` — runs `["open", "-a", app_name]` (timeout 15 s); non-zero → `RuntimeError`.
  - `AppDeck.action(body)`: unknown `source` → `ValueError("fuente desconocida")`; `"focus"` → `src.focus(pane)`; `"open"` (the deck's tap) → `src.activate()` then `src.focus(pane)`, returns `{"ok": True, "terminal": "activated"}`; `"refresh"` → `feed.dirty.set()`; `read`/`prompt`/`keys`/`plugin` → `ValueError("acción no disponible en herdr-app: <kind>")`.

- [ ] **Step 1: Write the failing tests** (patch `herdeck.subprocess.run` with a recorder returning `CompletedProcess(args, 0, "", "")`)

```python
def test_open_activates_then_focuses_remote(self):
    # after dispatch of hc_list.json: deck.action({"action": "open", "source": "trabajo", "pane_id": "workbox/t1"})
    # recorded argv == [["open", "-a", "Herdr"], ["herdr-app", "do", "agent.focus", "--terminal", "workbox/t1"]]
def test_focus_unknown_terminal(self):      # pane_id "t99" -> ValueError, no subprocess call
def test_focus_other_sources_terminal(self):  # source "personal", pane_id "workbox/t1" -> ValueError
def test_focus_failure_raises(self):        # recorder returns returncode 1, stderr "no window" -> RuntimeError("no window")
def test_unsupported_actions(self):         # read / prompt / keys / plugin -> ValueError mentioning the kind
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s tests -p test_app_source.py -v`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement `focus`, `activate`, `AppDeck.action`.**

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 5: Manual focus checks** (these run the mutating `agent.focus`, which is the feature itself)

On the tablet at `:7778`: tap a local tile → Herdr.app comes to the front with that terminal focused; tap an `workbox/*` tile → same for the remote terminal (spec: "not yet tested, check it during build"; record the result in the commit body). Quit Herdr.app and tap a tile → the app opens; if the focus lands on the wrong terminal or errors because the window was not ready, add one retry of the focus after 1.5 s in `AppDeck.action` and note it.

- [ ] **Step 6: Commit**

```bash
git add herdeck.py tests/test_app_source.py
git commit -m "feat: focus herdr-app terminals from the deck"
```

---

### Task 9: Config example, README and `--print-url`

**Files:**
- Modify: `config.example.json`, `README.md`, `herdeck.py` (`main` `--print-url`)
- Test: `tests/test_http.py` (or a new `PrintUrlTest` in `tests/test_pin.py`)

**Interfaces:**
- Produces: `--print-url` prints `http://<host>:<port>/?t=<token>`, then `http://<host>:<app port>/?t=<token>` when the block is enabled, then `PIN: <pin>`. `install-macos.sh` keeps working (it prints whatever `--print-url` prints).

- [ ] **Step 1: Write the failing test**

```python
def test_print_url_lists_both_decks_and_pin(self):
    # tmp config {"token": "TOK", "pin": "123456", "public_host": "deck.local", "sources": [...], "herdr_app": {"sources": [...]}}
    # run [sys.executable, "herdeck.py", "--config", path, "--print-url"] -> stdout lines ==
    # ["http://deck.local:7777/?t=TOK", "http://deck.local:7778/?t=TOK", "PIN: 123456"]
def test_print_url_without_block(self):
    # same without "herdr_app" -> ["http://deck.local:7777/?t=TOK", "PIN: 123456"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL on `test_print_url_lists_both_decks_and_pin`.

- [ ] **Step 3: Implement the extra line in `main()`.**

- [ ] **Step 4: Update `config.example.json`** — add `"pin": ""` next to `"token"` (generated on first run) and a `herdr_app` block with `port 7778`, `title "herdeck app"`, `accent "#bb9af7"`, and the two sources `personal` (`machine: null`) and `trabajo` (`machine: "workbox"`, `ssh: "workbox"`), colours as in `sources`.

- [ ] **Step 5: Update `README.md`** (Spanish, like the rest): a "herdr-app" section (what the block does, that it is a second PWA on `:7778` to install separately, that Claude data needs the statusline on each machine and `ssh` for remote ones, that only tap-to-focus is supported there), a "PIN" paragraph (where it is, `--print-url`, lockout behaviour), and mention the space filters in the feature list.

- [ ] **Step 6: Run the whole suite and a final smoke**

Run: `python3 -m unittest discover -s tests -v && python3 herdeck.py --config ~/.config/herdeck/config.json --print-url`
Expected: all PASS; three lines printed. Install the `:7778` deck on the tablet as a second PWA: its home-screen name is "herdeck app"; the existing "herdeck" PWA still opens `:7777` without reinstalling or re-entering anything.

- [ ] **Step 7: Commit** (body records the PIN root-cause evidence from Task 4 step 10 and the remote-focus result from Task 8 step 5, if not recorded there)

```bash
git add config.example.json README.md herdeck.py tests/
git commit -m "docs: document herdr-app deck, PIN and space filters"
```
