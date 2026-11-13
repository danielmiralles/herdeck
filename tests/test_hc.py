import json
import os
import shutil
import socketserver
import tempfile
import threading
import unittest

import herdeck
from support import load_fixture


class NormalizeHcTest(unittest.TestCase):
    def setUp(self):
        self.terms = load_fixture("hc_list.json")["terminals"]
        self.names = herdeck.app_names(load_fixture("herdr_app_ls.json"))

    def rows(self, terms=None, names=None):
        return herdeck.normalize_hc(self.terms if terms is None else terms,
                                    self.names if names is None else names)["agents"]

    def test_only_agent_terminals_become_rows(self):
        self.assertEqual([r["pane_id"] for r in self.rows()], ["t1", "t3", "workbox/t1", "workbox/t9"])

    def test_claude_row_matches_herdr_shape(self):
        r = self.rows()[0]
        self.assertEqual(set(r), set(herdeck.normalize_snapshot(load_fixture("herdr_snapshot.json"))["agents"][0]) | {"pid"})
        self.assertEqual((r["kind"], r["status"], r["workspace"], r["tab"], r["workspace_id"], r["pid"], r["title"]),
                         ("claude", "idle", "personal", "proj-a", "sp75f1bfb20e4cc7a8061f48571bc0f708", 45915, "Problemas con engram"))

    def test_title_falls_back_to_status_title(self):
        self.assertEqual(self.rows()[1]["title"], "Write plan")

    def test_status_mapping(self):
        rows = {r["pane_id"]: r for r in self.rows()}
        self.assertEqual(rows["workbox/t1"]["status"], "blocked")
        self.assertEqual((rows["workbox/t9"]["status"], rows["workbox/t9"]["kind"]), ("error", "codex"))

    def test_unknown_state_becomes_unknown(self):
        terms = [{"address": "t1", "status": {"app": "codex", "state": "weird"}}]
        self.assertEqual(self.rows(terms, {})[0]["status"], "unknown")

    def test_normalize_hc_without_names_uses_where_label(self):
        r = self.rows(names={})[0]
        self.assertEqual((r["workspace"], r["tab"], r["workspace_id"]),
                         ("personal", "proj-a", "sp75f1bfb20e4cc7a8061f48571bc0f708"))

    def test_bad_pid_is_none(self):
        base = {"address": "t1", "status": {"app": "claude-code", "state": "idle"}}
        self.assertIsNone(self.rows([dict(base, foreground={"pid": "x"})], {})[0]["pid"])
        self.assertIsNone(self.rows([dict(base, foreground={"pid": 0})], {})[0]["pid"])
        self.assertIsNone(self.rows([base], {})[0]["pid"])

    def test_workspaces_count(self):
        out = herdeck.normalize_hc(self.terms, self.names)
        self.assertEqual(out["workspaces"], len({r["workspace_id"] for r in out["agents"]}))


class HcHelpersTest(unittest.TestCase):
    def test_machine_of(self):
        self.assertEqual(herdeck.machine_of("workbox/t1"), "workbox")
        self.assertIsNone(herdeck.machine_of("t1"))

    def test_split_by_machine(self):
        terms = load_fixture("hc_list.json")["terminals"]
        out = herdeck.split_by_machine(terms, [None, "workbox"])
        self.assertEqual({k: [t["address"] for t in v] for k, v in out.items()},
                         {None: ["t1", "t3", "t8"], "workbox": ["workbox/t1", "workbox/t3", "workbox/t9"]})
        only_local = herdeck.split_by_machine(terms, [None])
        self.assertEqual(list(only_local), [None])

    def test_machine_errors(self):
        self.assertEqual(herdeck.machine_errors({"machines": [{"name": "workbox", "state": "connecting"}]}),
                         {"workbox": "connecting"})
        self.assertEqual(herdeck.machine_errors({"machines": [{"name": "workbox", "state": "connected"}]}), {})

    def test_app_names_malformed(self):
        self.assertEqual(herdeck.app_names(None), {})
        self.assertEqual(herdeck.app_names({"spaces": "x"}), {})

    def test_app_names_maps_addresses(self):
        n = herdeck.app_names(load_fixture("herdr_app_ls.json"))
        self.assertEqual(n["workbox/t1"]["tab"], "PROJ-101")
        self.assertEqual(n["t3"]["space"], "personal")

    def test_is_claude(self):
        self.assertTrue(herdeck.is_claude("claude"))
        self.assertTrue(herdeck.is_claude("Claude-Code"))
        self.assertFalse(herdeck.is_claude("codex"))
        self.assertFalse(herdeck.is_claude(None))


class HcCallTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir="/tmp")  # short path: AF_UNIX limit
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "s.sock")
        self.requests = []

    def serve(self, reply):
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                outer.requests.append(json.loads(self.rfile.readline()))
                if reply is not None:
                    self.wfile.write((json.dumps(dict(reply, id=outer.requests[-1]["id"], jsonrpc="2.0")) + "\n").encode())

        srv = socketserver.UnixStreamServer(self.path, H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

    def test_success_returns_result(self):
        self.serve({"result": {"terminals": []}})
        self.assertEqual(herdeck.hc_call(self.path, "terminal.list"), {"terminals": []})
        req = self.requests[0]
        self.assertEqual((req["jsonrpc"], req["method"], req["params"]), ("2.0", "terminal.list", {}))

    def test_error_raises_with_kind(self):
        self.serve({"error": {"code": -32601, "message": "unknown method", "data": {"kind": "unknown_method"}}})
        with self.assertRaises(RuntimeError) as cm:
            herdeck.hc_call(self.path, "nope")
        self.assertTrue(str(cm.exception).startswith("unknown_method"))

    def test_empty_reply_raises(self):
        self.serve(None)
        with self.assertRaises(ConnectionError):
            herdeck.hc_call(self.path, "terminal.list")


if __name__ == "__main__":
    unittest.main()
