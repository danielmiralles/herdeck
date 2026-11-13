import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import herdeck
from herdeck import PinGuard


class PinGuardTest(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.guard = PinGuard(clock=lambda: self.now[0])

    def test_four_failures_do_not_lock(self):
        for _ in range(4):
            self.guard.failed("a")
        self.assertEqual(self.guard.wait("a"), 0)

    def test_fifth_failure_locks_30s(self):
        for _ in range(4):
            self.guard.failed("a")
        self.assertEqual(self.guard.failed("a"), 30.0)
        self.assertEqual(self.guard.wait("a"), 30.0)

    def test_backoff_doubles_and_caps(self):
        got = {}
        for i in range(1, 21):
            got[i] = self.guard.failed("a")
        self.assertEqual(got[6], 60.0)
        self.assertEqual(got[7], 120.0)
        self.assertEqual(got[12], 3600.0)
        self.assertEqual(got[20], 3600.0)

    def test_lock_expires(self):
        for _ in range(5):
            self.guard.failed("a")
        self.now[0] += 30
        self.assertEqual(self.guard.wait("a"), 0)

    def test_wait_does_not_count(self):
        for _ in range(4):
            self.guard.failed("a")
        for _ in range(10):
            self.guard.wait("a")
        self.assertEqual(self.guard.failed("a"), 30.0)

    def test_success_resets_ip(self):
        for _ in range(4):
            self.guard.failed("a")
        self.guard.succeeded("a")
        for _ in range(4):
            self.guard.failed("a")
        self.assertEqual(self.guard.wait("a"), 0)

    def test_global_lock_across_ips(self):
        for i in range(20):
            self.guard.failed("10.0.0.%d" % i)
        self.assertEqual(self.guard.wait("10.0.0.99"), 30.0)

    def test_other_ip_not_locked_by_one_ip(self):
        for _ in range(5):
            self.guard.failed("a")
        self.assertEqual(self.guard.wait("b"), 0)


class PinConfigTest(unittest.TestCase):
    def _cfg(self, d, **extra):
        p = Path(d) / "config.json"
        cfg = {"token": "T", "sources": [{"name": "x"}]}
        cfg.update(extra)
        p.write_text(json.dumps(cfg), encoding="utf-8")
        return p

    def test_generates_and_persists_pin(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._cfg(d)
            cfg = herdeck.load_config(p)
            self.assertRegex(cfg["pin"], r"\d{6}")
            self.assertEqual(json.loads(p.read_text())["pin"], cfg["pin"])

    def test_keeps_existing_pin(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = herdeck.load_config(self._cfg(d, pin="4321"))
            self.assertEqual(cfg["pin"], "4321")

    def test_rejects_invalid_pin(self):
        for bad in ("12ab", "123", "\u0661\u0662\u0663\u0664"):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(SystemExit):
                    herdeck.load_config(self._cfg(d, pin=bad))


class PrintUrlTest(unittest.TestCase):
    def _run(self, **extra):
        cfg = {"token": "TOK", "pin": "123456", "public_host": "deck.local", "sources": [{"name": "x"}]}
        cfg.update(extra)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps(cfg), encoding="utf-8")
            root = Path(herdeck.__file__).resolve().parent
            out = subprocess.run([sys.executable, str(root / "herdeck.py"), "--config", str(p), "--print-url"],
                                 capture_output=True, text=True, check=True).stdout
        return out.splitlines()

    def test_print_url_lists_both_decks_and_pin(self):
        lines = self._run(herdr_app={"sources": [{"name": "personal", "machine": None}]})
        self.assertEqual(lines, ["http://deck.local:7777/?t=TOK", "http://deck.local:7778/?t=TOK", "PIN: 123456"])

    def test_print_url_without_block(self):
        self.assertEqual(self._run(), ["http://deck.local:7777/?t=TOK", "PIN: 123456"])


if __name__ == "__main__":
    unittest.main()
