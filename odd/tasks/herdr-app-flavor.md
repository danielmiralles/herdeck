# herdr-app flavor

Objective: second herdeck deck on its own port for herdr-app (hc), identical tiles; plus space filters, PIN login, widgets removed for both decks.
Spec: docs/superpowers/specs/2026-11-13-herdr-app-flavor-design.md
Plan: docs/superpowers/plans/2026-11-13-herdr-app-flavor.md
Branch: feat/herdr-app-flavor
Route: delegated (subagent-driven: one implementer + one reviewer per task; trigger: 2+ non-trivial files per task)
Delivery strategy: ask-on-risk (forecast > 400 authored lines; chain decision before PR)

## Tasks
- [x] T1 test harness + BaseSource seam (04f5107, 39d2606; review clean)
- [x] T2 remove widgets (ea6d810; review clean)
- [x] T3 space filters (77cbf36; review clean)
- [x] T4 PIN login (2c54c46, b12ad4d; 1 fix round: lockout race)
- [x] T5 hc client + mapping + error status (e047e62; review clean)
- [x] T6 Claude pid -> session link (ea2b8e1; review clean)
- [x] T7 herdr-app sources + :7778 listener (9843c2d, 104d13f; 1 fix round: remote space ids)
- [x] T8 focus on :7778 (f9283f7; review clean)
- [x] T9 config example, README, --print-url (ba6a91a, df2bf58)

## Pending manual checks (user)
- Tablet: :7777 PWA unchanged, PIN page flow, space filters, :7778 PWA install, tap-to-focus local and workbox/*.

## Evidence
- Final whole-branch review: ready with fixes; fix wave a41ed6d (busy :7778 no longer kills :7777, README token/PIN wording, PinGuard dedupe, HcFeed backoff reset). Suite 102/102.
- RDD: off (global) — native review not run.
- Deferred minors kept (final review triage): PIN page title fixed "herdeck" on :7778; correct PIN resets global lockout counter; herdr-app ls latency up to 5 s if it hangs; header-read slowloris (pre-existing).

Next step: user runs the pending manual checks, then decides on push/PR (forecast > 400 lines: choose single PR or chained PRs).
