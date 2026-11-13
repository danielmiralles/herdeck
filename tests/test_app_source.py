import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import herdeck
from support import FROZEN_NOW, FakeHub, load_fixture

HERDR_MANIFEST = {"name": "herdeck", "short_name": "herdeck", "display": "standalone",
                  "background_color": "#0b0d10", "theme_color": "#0b0d10", "start_url": "/"}


def make_block():
    return {"sources": [
        {"name": "personal", "label": "Personal", "color": "#7aa2f7", "machine": None},
        {"name": "trabajo", "label": "Trabajo", "color": "#e0af68", "machine": "workbox", "ssh": "workbox"},
    ]}


class HcFeedDispatchTest(unittest.TestCase):
    def setUp(self):
        self.hub = FakeHub()
        self.block = make_block()
        self.srcs = [herdeck.AppSource(s, self.hub, self.block) for s in self.block["sources"]]
        self.feed = herdeck.HcFeed(self.block, self.srcs)
        self.names = herdeck.app_names(load_fixture("herdr_app_ls.json"))
        # never spawn the Claude reader from a unit test
        for s in self.srcs:
            s.claude_last = time.time() + 3600

    def dispatch(self, result):
        with mock.patch("herdeck.time.time", return_value=FROZEN_NOW):
            self.feed.dispatch(result, self.names)

    def ids(self, name):
        return [a["id"] for a in self.hub.sources[name]["agents"]]

    def test_dispatch_splits_by_machine(self):
        self.dispatch(load_fixture("hc_list.json"))
        self.assertEqual(self.ids("personal"), ["personal/t1", "personal/t3"])
        self.assertEqual(self.ids("trabajo"), ["trabajo/workbox/t1", "trabajo/workbox/t9"])
        p, t = self.hub.sources["personal"], self.hub.sources["trabajo"]
        self.assertIs(p["connected"], True)
        self.assertIs(t["connected"], True)
        self.assertIs(t["remote"], True)
        self.assertEqual((t["icon"], p["icon"]), ("work", "home"))
        # workbox/t9 is missing from the herdr-app names; its label-derived id must match
        self.assertEqual(t["spaces"], [{"key": "ws:trabajo/workbox/spba2ecd8563af0e39a13371ec5922392a",
                                        "label": "work"}])
        self.assertEqual(p["spaces"], [{"key": "ws:personal/sp75f1bfb20e4cc7a8061f48571bc0f708",
                                        "label": "personal"}])

    def test_dispatch_marks_disconnected_machine(self):
        r = load_fixture("hc_list.json")
        r["machines"] = [{"name": "workbox", "state": "connecting"}]
        self.dispatch(r)
        t = self.hub.sources["trabajo"]
        self.assertIs(t["connected"], False)
        self.assertEqual(t["error"], "workbox: connecting")
        self.assertEqual(t["agents"], [])
        self.assertIs(self.hub.sources["personal"]["connected"], True)
        self.assertEqual(self.ids("personal"), ["personal/t1", "personal/t3"])

    def test_dispatch_machine_missing_from_hc(self):
        r = load_fixture("hc_list.json")
        r["machines"] = []
        self.dispatch(r)
        self.assertEqual(self.hub.sources["trabajo"]["error"], "workbox: no está en hc")
        self.assertIs(self.hub.sources["trabajo"]["connected"], False)

    def test_dispatch_recovers_after_failure(self):
        self.feed.fail("boom")
        self.dispatch(load_fixture("hc_list.json"))
        self.assertIs(self.hub.sources["trabajo"]["connected"], True)
        self.assertIsNone(self.hub.sources["trabajo"]["error"])

    def test_fail_marks_all_disconnected(self):
        self.dispatch(load_fixture("hc_list.json"))
        self.feed.fail("hc: socket missing")
        for name in ("personal", "trabajo"):
            d = self.hub.sources[name]
            self.assertIs(d["connected"], False)
            self.assertEqual(d["error"], "hc: socket missing")
            self.assertEqual(d["agents"], [])

    def test_fail_truncates_error(self):
        self.feed.fail("x" * 1000)
        self.assertEqual(len(self.hub.sources["personal"]["error"]), 300)

    def test_claude_info_by_pid(self):
        personal = self.srcs[0]
        personal.claude_sessions = {"pid:45915": {"model": {"id": "claude-sonnet-4-5"},
                                                  "context_window": {"used_percentage": 61.2}}}
        self.dispatch(load_fixture("hc_list.json"))
        t1 = self.hub.sources["personal"]["agents"][0]
        self.assertEqual(t1["pane_id"], "t1")
        self.assertEqual(t1["model"], "sonnet")
        self.assertEqual(t1["ctx"], {"pct": 61.2, "size": None})
        self.assertIn("pid:45915", personal.claude_keys)

    def test_rows_identical_shape_to_herdr(self):
        self.srcs[0].claude_sessions = {"pid:45915": {"model": {"id": "claude-sonnet-4-5"},
                                                      "context_window": {"used_percentage": 61.2}}}
        self.dispatch(load_fixture("hc_list.json"))
        golden = load_fixture("herdr_publish_golden.json")["data"]
        herdr_agent = set(golden["agents"][0]) | {"space_key"}
        app = self.hub.sources["personal"]
        self.assertEqual(set(app["agents"][0]), herdr_agent | {"pid"})
        self.assertEqual(set(app), set(golden) | {"spaces"})
        for a in app["agents"] + self.hub.sources["trabajo"]["agents"]:
            self.assertIsInstance(a["kind"], str)

    def test_snapshot_rows_none_when_disconnected(self):
        s = self.srcs[0]
        s.set_data(load_fixture("hc_list.json")["terminals"], {}, False, "x")
        self.assertIsNone(s.snapshot_rows())


class ReadAppNamesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def script(self, body):
        p = os.path.join(self.dir, "fake-herdr-app")
        with open(p, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, 0o755)
        return p

    def test_read_app_names_failure_returns_empty(self):
        self.assertEqual(herdeck.read_app_names("/nonexistent/herdr-app"), {})
        self.assertEqual(herdeck.read_app_names(self.script('echo "not json"')), {})
        self.assertEqual(herdeck.read_app_names(self.script("exit 3")), {})
        self.assertEqual(herdeck.read_app_names(self.script("sleep 5"), timeout=0.3), {})

    def test_read_app_names_success(self):
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "herdr_app_ls.json")
        out = herdeck.read_app_names(self.script('[ "$1" = ls ] && cat "%s"' % fixture))
        self.assertEqual(out["workbox/t1"]["tab"], "PROJ-101")


class FakeHc:
    """Tiny hc stand-in: replies to terminal.list and streams notifications to subscribers."""

    def __init__(self, path, list_result):
        self.path = path
        self.list_result = list_result
        self.subscribers = []
        self.lists = 0
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(8)
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c):
        f = c.makefile("rb")
        line = f.readline()
        if not line:
            c.close()
            return
        req = json.loads(line)
        if req["method"] == "events.subscribe":
            c.sendall((json.dumps({"jsonrpc": "2.0", "id": req["id"],
                                   "result": {"seq": 1, "terminals": []}}) + "\n").encode())
            self.subscribers.append(c)
            return
        self.lists += 1
        c.sendall((json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": self.list_result}) + "\n").encode())
        c.close()

    def notify(self, method):
        for c in list(self.subscribers):
            c.sendall((json.dumps({"jsonrpc": "2.0", "method": method, "params": {}}) + "\n").encode())

    def close(self):
        self.sock.close()
        for c in self.subscribers:
            c.close()


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class HcFeedSessionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir="/tmp")  # short path: AF_UNIX limit
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "hc.sock")
        self.hub = FakeHub()
        self.block = dict(make_block(), hc_socket=self.path, herdr_app_bin="/nonexistent/herdr-app")
        self.srcs = [herdeck.AppSource(s, self.hub, self.block) for s in self.block["sources"]]
        for s in self.srcs:
            s.claude_last = time.time() + 3600

    def test_session_refreshes_on_notification_and_ignores_heartbeat(self):
        hc = FakeHc(self.path, load_fixture("hc_list.json"))
        self.addCleanup(hc.close)
        feed = herdeck.HcFeed(self.block, self.srcs)
        feed.start()
        self.assertTrue(wait_for(lambda: hc.lists == 1 and hc.subscribers))
        self.assertEqual([a["id"] for a in self.hub.sources["trabajo"]["agents"]],
                         ["trabajo/workbox/t1", "trabajo/workbox/t9"])
        # names unavailable -> grouping falls back to the "where" label
        self.assertEqual(self.hub.sources["trabajo"]["agents"][0]["tab"], "PROJ-101")
        hc.notify("heartbeat")
        time.sleep(1.3)
        self.assertEqual(hc.lists, 1)
        hc.notify("terminal.status")
        self.assertTrue(wait_for(lambda: hc.lists == 2))
        self.assertEqual(feed.last_terminals, load_fixture("hc_list.json")["terminals"])

    def test_missing_socket_marks_sources_disconnected(self):
        feed = herdeck.HcFeed(self.block, self.srcs)
        feed.start()
        self.assertTrue(wait_for(lambda: "trabajo" in self.hub.sources
                                 and self.hub.sources["trabajo"]["error"]))
        self.assertIs(self.hub.sources["personal"]["connected"], False)
        self.assertTrue(self.hub.sources["personal"]["error"].startswith("hc: "))
        self.assertTrue(feed.is_alive())

    def test_bad_reply_does_not_kill_thread(self):
        hc = FakeHc(self.path, ["not", "a", "dict"])
        self.addCleanup(hc.close)
        feed = herdeck.HcFeed(self.block, self.srcs)
        feed.start()
        self.assertTrue(wait_for(lambda: hc.lists >= 1 and self.hub.sources.get("personal", {}).get("error")))
        self.assertTrue(feed.is_alive())


class ConfigBlockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def load(self, **extra):
        cfg = {"token": "TOK", "pin": "123456", "port": 7777,
               "sources": [{"name": "personal"}]}
        cfg.update(extra)
        p = os.path.join(self.dir, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        from pathlib import Path
        return herdeck.load_config(Path(p))

    def test_config_block_validation(self):
        two_nter = {"sources": [{"name": "a", "machine": "workbox"}, {"name": "b", "machine": "workbox"}]}
        with self.assertRaises(SystemExit):
            self.load(herdr_app=two_nter)
        with self.assertRaises(SystemExit):
            self.load(herdr_app=dict(make_block(), port=7777))
        dup = {"sources": [{"name": "a", "machine": None}, {"name": "a", "machine": "workbox"}]}
        with self.assertRaises(SystemExit):
            self.load(herdr_app=dup)
        with self.assertRaises(SystemExit):
            self.load(herdr_app={"sources": []})
        self.assertNotIn("herdr_app", self.load())

    def test_block_defaults(self):
        b = self.load(herdr_app=make_block())["herdr_app"]
        self.assertEqual((b["port"], b["title"], b["accent"], b["hc_socket"], b["herdr_app_bin"],
                          b["app_name"], b["poll_seconds"]),
                         (7778, "herdeck app", "#bb9af7", herdeck.HC_SOCKET, "herdr-app", "Herdr", 20))

    def test_disabled_block_is_not_validated(self):
        cfg = self.load(herdr_app={"enabled": False, "port": 7777, "sources": []})
        self.assertIsNone(herdeck.app_block(cfg))
        self.assertIsNotNone(herdeck.app_block(self.load(herdr_app=make_block())))


class AppDeckConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {"token": "TOK", "pin": "123456", "sources": [{"name": "personal", "socket": "/nonexistent.sock"}]}
        self.block = dict(make_block(), title="herdeck app", accent="#bb9af7")

    def test_client_config_hides_sheet(self):
        c = herdeck.AppDeck(self.cfg, self.block).client_config()
        self.assertEqual(c["features"], {"sheet": False})
        self.assertEqual((c["allow_input"], c["quick_replies"], c["plugin_actions"], c["title"], c["accent"]),
                         (False, [], [], "herdeck app", "#bb9af7"))

    def test_manifest_is_distinct(self):
        m = herdeck.AppDeck(self.cfg, self.block).manifest()
        self.assertEqual(m, {"name": "herdeck app", "short_name": "herdeck app", "display": "standalone",
                             "background_color": "#0b0d10", "theme_color": "#bb9af7", "start_url": "/"})

    def test_herdr_app_keeps_sheet(self):
        c = herdeck.App(self.cfg).client_config()
        self.assertEqual(c["features"], {"sheet": True})
        self.assertNotIn("accent", c)

    def test_herdr_manifest_unchanged(self):
        self.assertEqual(herdeck.App(self.cfg).manifest(), HERDR_MANIFEST)

    def test_action_refresh_only(self):
        deck = herdeck.AppDeck(self.cfg, self.block)
        self.assertEqual(deck.action({"action": "refresh"}), {"ok": True})
        self.assertTrue(deck.feed.dirty.is_set())
        with self.assertRaises(ValueError):
            deck.action({"action": "read"})

    def test_raw(self):
        deck = herdeck.AppDeck(self.cfg, self.block)
        deck.feed.fail("boom")
        r = deck.raw()
        self.assertEqual((r["error"], r["terminals"]), ("boom", []))
        self.assertTrue(r["socket"].endswith("default.sock"))


class AppDeckFocusTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {"token": "TOK", "pin": "123456", "sources": []}
        self.deck = herdeck.AppDeck(self.cfg, make_block())
        for s in self.deck.sources.values():
            s.claude_last = time.time() + 3600
        names = herdeck.app_names(load_fixture("herdr_app_ls.json"))
        with mock.patch("herdeck.time.time", return_value=FROZEN_NOW):
            self.deck.feed.dispatch(load_fixture("hc_list.json"), names)
        self.calls = []
        self.rc, self.err = 0, ""

    def fake_run(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.rc, "", self.err)

    def act(self, body):
        with mock.patch("herdeck.subprocess.run", side_effect=self.fake_run):
            return self.deck.action(body)

    def test_open_activates_then_focuses_remote(self):
        r = self.act({"action": "open", "source": "trabajo", "pane_id": "workbox/t1"})
        self.assertEqual(r, {"ok": True, "terminal": "activated"})
        self.assertEqual(self.calls, [["open", "-a", "Herdr"],
                                      ["herdr-app", "do", "agent.focus", "--terminal", "workbox/t1"]])

    def test_focus_unknown_terminal(self):
        with self.assertRaises(ValueError):
            self.act({"action": "focus", "source": "personal", "pane_id": "t99"})
        self.assertEqual(self.calls, [])

    def test_focus_other_sources_terminal(self):
        with self.assertRaises(ValueError):
            self.act({"action": "open", "source": "personal", "pane_id": "workbox/t1"})
        self.assertEqual(self.calls, [])

    def test_unknown_source(self):
        with self.assertRaises(ValueError):
            self.act({"action": "open", "source": "nope", "pane_id": "t1"})

    def test_focus_failure_raises(self):
        self.rc, self.err = 1, "no window"
        with self.assertRaisesRegex(RuntimeError, "no window"):
            self.act({"action": "focus", "source": "trabajo", "pane_id": "workbox/t1"})

    def test_unsupported_actions(self):
        for kind in ("read", "prompt", "keys", "plugin"):
            with self.assertRaisesRegex(ValueError, kind):
                self.act({"action": kind, "source": "personal", "pane_id": "t1"})


if __name__ == "__main__":
    unittest.main()


class StartAppListenerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {"token": "TOK", "pin": "123456", "sources": [{"name": "personal", "socket": "/nonexistent.sock"}]}

    def test_busy_port_returns_none_instead_of_raising(self):
        import socket
        busy = socket.socket()
        self.addCleanup(busy.close)
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        block = dict(make_block(), port=busy.getsockname()[1])
        self.assertIsNone(herdeck.start_app_listener(self.cfg, block, herdeck.PinGuard(), "127.0.0.1"))

    def test_free_port_returns_serving_server(self):
        block = dict(make_block(), port=0)
        srv = herdeck.start_app_listener(self.cfg, block, herdeck.PinGuard(), "127.0.0.1")
        self.assertIsNotNone(srv)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)


class HcFeedBackoffTest(unittest.TestCase):
    def test_backoff_resets_after_a_session_that_refreshed(self):
        block = dict(make_block(), hc_socket="/nonexistent.sock")
        feed = herdeck.HcFeed(block, [])
        feed.fail = lambda m: None
        sleeps, calls = [], [0]

        class Stop(BaseException):
            pass

        def session():
            calls[0] += 1
            if calls[0] in (3, 5):
                feed.session_ok = True  # this session got its first refresh
            if calls[0] > 5:
                raise Stop()
            raise ConnectionError("x")

        feed._session = session
        orig = herdeck.time.sleep
        herdeck.time.sleep = sleeps.append
        try:
            with self.assertRaises(Stop):
                feed.run()
        finally:
            herdeck.time.sleep = orig
        self.assertEqual(sleeps, [1.0, 2.0, 1.0, 2.0, 1.0])
