import json
import os
import unittest
from unittest import mock

import herdeck
from support import FROZEN_NOW, FakeHub, load_fixture

SID = "c07e20e3-2305-4a65-a02a-8d8570552d82"
GOLDEN = "herdr_publish_golden.json"


class NormalizeSnapshotTest(unittest.TestCase):
    def test_agents_rows(self):
        out = herdeck.normalize_snapshot(load_fixture("herdr_snapshot.json"))
        self.assertEqual(out["pane_ids"], ["w1:p1", "w1:p2"])
        self.assertEqual(out["workspaces"], 2)
        a, b = out["agents"]
        self.assertEqual(a, {"pane_id": "w1:p1", "name": None, "kind": "claude", "status": "blocked",
                             "workspace_id": "w1", "workspace": "personal", "branch": None, "tab": "home",
                             "cwd": "/Users/u", "title": "✳ Engram", "focused": False,
                             "session_id": SID})
        self.assertEqual((b["kind"], b["status"], b["tab"], b["focused"], b["session_id"]),
                         ("codex", "working", "herdeck", True, None))

    def test_falls_back_to_panes_without_agents_list(self):
        snap = load_fixture("herdr_snapshot.json")
        del snap["snapshot"]["agents"]
        out = herdeck.normalize_snapshot(snap)
        self.assertEqual(out["pane_ids"], ["w1:p1", "w1:p2"])  # w2:p1 has no agent
        self.assertEqual(out["agents"][0]["status"], "idle")


class PublishGoldenTest(unittest.TestCase):
    """_publish output for a fixed input must never change (except keys in ADDED_KEYS)."""
    ADDED_KEYS = {"source": {"spaces"}, "agent": {"space_key"}}  # later tasks add names here, never edit the golden

    def make_source(self):
        src = herdeck.Source({"name": "personal", "label": "Personal", "color": "#7aa2f7",
                              "socket": "/nonexistent/herdr.sock"}, FakeHub(), "herdr")
        src.raw = load_fixture("herdr_snapshot.json")
        src.connected = True
        src.claude_sessions = {SID: {
            "model": {"id": "claude-opus-4-1", "display_name": "Opus 4.1"},
            "context_window": {"used_percentage": 42.04, "context_window_size": 200000}}}
        src.claude_rate = {"five_hour": {"used_percentage": 12.3, "resets_at": FROZEN_NOW + 3600},
                           "seven_day": {"used_percentage": 50, "resets_at": FROZEN_NOW - 1}}
        return src

    def strip_added(self, data):
        data = {k: v for k, v in data.items() if k not in self.ADDED_KEYS["source"]}
        data["agents"] = [{k: v for k, v in a.items() if k not in self.ADDED_KEYS["agent"]}
                          for a in data["agents"]]
        return data

    def test_publish_matches_golden(self):
        src = self.make_source()
        with mock.patch("herdeck.time.time", return_value=FROZEN_NOW):
            src._publish()
        data = self.strip_added(src.hub.sources["personal"])
        ids = src.claude_keys
        actual = json.loads(json.dumps({"data": data, "claude_ids": ids}))
        from support import FIXTURES
        path = FIXTURES / GOLDEN
        if os.environ.get("HERDECK_WRITE_GOLDEN") == "1":
            path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self.skipTest("golden written")
        self.assertEqual(actual, load_fixture(GOLDEN))

    def test_disconnected_publish(self):
        src = self.make_source()
        src.connected, src.error = False, "boom"
        src._publish()
        data = src.hub.sources["personal"]
        self.assertEqual(data["agents"], [])
        self.assertIs(data["connected"], False)
        self.assertEqual(data["error"], "boom")
        self.assertNotIn("workspaces", data)


class HubStateTest(unittest.TestCase):
    def test_sorts_by_status_then_newest(self):
        hub = herdeck.Hub()
        rows = [("idle", 5), ("blocked", 1), ("done", 9), ("blocked", 3), ("working", 2)]
        hub.update_source("s", {"agents": [{"status": s, "since": t} for s, t in rows]})
        out = [(a["status"], a["since"]) for a in hub.state()["agents"]]
        self.assertEqual(out, [("blocked", 3), ("blocked", 1), ("done", 9), ("working", 2), ("idle", 5)])

    def test_error_sorts_after_blocked_before_done(self):
        hub = herdeck.Hub()
        hub.update_source("s", {"agents": [{"status": s, "since": 1} for s in ("done", "error", "blocked", "working")]})
        self.assertEqual([a["status"] for a in hub.state()["agents"]], ["blocked", "error", "done", "working"])


class WidgetsRemovedTest(unittest.TestCase):
    def test_state_has_no_widgets(self):
        self.assertNotIn("widgets", herdeck.Hub().state())

    def test_widgets_config_key_is_ignored(self):
        cfg = {"sources": [{"name": "personal", "socket": "/nonexistent.sock"}],
               "widgets": [{"label": "x", "cmd": ["true"]}]}
        app = herdeck.App(cfg)
        self.assertFalse(hasattr(app, "widgets"))
        self.assertFalse(hasattr(herdeck, "Widget"))


if __name__ == "__main__":
    unittest.main()
