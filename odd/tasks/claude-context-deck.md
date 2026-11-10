# Feature: Claude Code context and quota in the deck

Locator: `odd/tasks/claude-context-deck.md` · Engram mirror: `odd/claude-context-deck/tasks`

## Objective

Show Claude Code information subtly on the herdeck tablet deck: per-agent context usage and model, per-source plan quota (5h / 7d), source icons, and terminal-style animated status glyphs.

## Why

The user glances at the deck to triage agents; knowing which session is close to its context limit and how much plan quota is left avoids surprises, without cluttering the tiles.

## Approved design (variant B, "Anillo", from browser mockups)

- Claude Code tiles: small SVG ring next to the age in the footer showing context used %. Ring color: blue < 60%, amber 60–85%, red > 85%. Model chip (`opus`, `sonnet`, ...) next to the kind label. Agents without data render as today (no ring, no chip).
- Quota: 5h and 7d rings per source (Personal and Work may be different accounts). A window that is absent or whose `resets_at` passed is not shown.
- Icons: agent kind icon (Claude spark, Codex `<>`), source icons (home for local, briefcase for SSH; overridable with `icon` in the source config).
- Status glyphs (monospace text + CSS, colored by state): working = braille spinner `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` (~80 ms/frame); blocked = blinking block cursor `▌`; done = `✓` typed once then static; idle = faint floating `zZ`.
- Data source: a minimal Claude Code statusline script on both machines writes its stdin JSON to `~/.claude/herdeck/<session_id>.json` (atomic write) and prints a short line `model · ctx N% · 5h N%`. herdr exposes the session id per pane (`agent_session.value`). herdeck reads those files locally, and over SSH for remote sources.
- Fields (official statusline docs, https://code.claude.com/docs/en/statusline): `context_window.used_percentage`, `context_window.context_window_size`, `model.display_name`/`model.id`, `rate_limits.five_hour.used_percentage|resets_at`, `rate_limits.seven_day.used_percentage|resets_at`. Rate limits only exist for Pro/Max/Ultra subscribers.

## Scope / constraints

- Python stdlib only (project rule). No GIFs, no external assets.
- Do not overwrite an existing `statusLine` in any settings.json.
- Remote reads reuse the source's `ssh` host with `BatchMode=yes`; bounded frequency (no more than one SSH read per source every ~10 s).
- Out of scope: cost/tokens on tiles (variant C); "only last tapped tile stays highlighted" (separate change).

## Tasks

- [x] T1 Statusline script `statusline/herdeck-statusline.py` + install support in `install-macos.sh` (route: delegated writer)
- [x] T2 Backend: session id in snapshot normalization; per-source Claude info reader (local + SSH); `ctx`/`model` on agents, `quota` on sources (route: delegated writer)
- [x] T3 Frontend: variant B rings, model chip, icons, animated glyphs; README (route: delegated writer)
- [ ] T4 Install statusline on Mac and `workbox`, reinstall service, verify end to end (route: inline, parent)

Route evidence: T1–T3 touch 3+ non-trivial files → writer trigger → one delegated writer. T4 is operational, done by parent.

## Checks

No test suite or runner exists in this repo (test-first exception). Functional checks: `python3 -m py_compile herdeck.py statusline/herdeck-statusline.py`; pipe a sample statusline JSON into the script and verify file + printed line; `/api/state` shows `ctx`/`model` on Claude agents and `quota` on sources; visual check on the tablet.

Delivery: the directory is not a git repository, so work-unit commits are not possible; recorded as skipped.

## Progress

- 2026-11-10: design approved by user in browser mockups (variant B + terminal glyphs).
- 2026-11-10: T1–T3 implemented by delegated writer (route: delegated, writer trigger). Evidence:
  - `py_compile herdeck.py statusline/herdeck-statusline.py`: OK.
  - Statusline sample (temp HOME): printed `Opus · ctx 31% · 5h 42%`, wrote `~/.claude/herdeck/test-herdeck-0000.json`; `not json` → no output, exit 0.
  - `bash -n install-macos.sh`: OK; settings merge snippet tested on a temp file: adds statusLine + `.bak-herdeck`, second run leaves existing statusLine untouched.
  - `normalize_snapshot` fake snapshot → `session_id: test-herdeck-0000`; local reader + publish → agent `model: opus`, `ctx {pct 31, size 200000}`, source `icon: home`, `quota` 5h/7d; expired window dropped; background refresh thread works.
  - `node --check` on deck.html script: OK. Visual check on tablet pending (T4).
  - Deviation: quota uses the newest of the last 5 statusline files that has `rate_limits` (an API-key session without rate_limits does not hide the quota). Claude info refresh only runs while the source has Claude agents with a session id.
- 2026-11-10 T4 (inline): Mac install via install-macos.sh (statusLine added, backup `.bak-herdeck`; launchd bootstrap race fixed by re-bootstrap + kickstart). workbox: script copied, statusLine added (backup), probe printed `Opus · ctx 10%`. `/api/state`: Personal agents show model/ctx, source quota 7d. Trabajo: herdr snapshot had `agent_session: null` because workbox's herdr Claude integration was v10 (Mac v11); updated with `herdr integration install claude` (user approved) → v11, statusLine preserved. Session ids appear on the next Claude hook event per session.
- Pending: confirm Trabajo tiles get session_id/ctx after next interaction; visual check on tablet.
