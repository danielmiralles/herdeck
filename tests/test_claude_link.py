import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import herdeck


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.sessions = os.path.join(self.tmp, ".claude", "sessions")
        self.data = os.path.join(self.tmp, ".claude", "herdeck")
        os.makedirs(self.sessions)
        os.makedirs(self.data)

    def write(self, d, name, content):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def stat(self, sid="sid-a", **extra):
        j = {"model": {"id": "claude-opus-4-1"}, "context_window": {"used_percentage": 10}}
        j.update(extra)
        self.write(self.data, sid + ".json", j)

    def run_reader(self, args):
        r = subprocess.run([sys.executable, "-"] + args, input=herdeck.CLAUDE_READER,
                           capture_output=True, text=True, env=dict(os.environ, HOME=self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_pid_resolves_to_statusline_data(self):
        self.write(self.sessions, "123.json", {"pid": 123, "sessionId": "sid-a"})
        self.stat()
        out = self.run_reader(["pid:123"])
        self.assertEqual(out["sessions"]["pid:123"]["model"]["id"], "claude-opus-4-1")

    def test_plain_session_id_unchanged(self):
        self.stat()
        out = self.run_reader(["sid-a"])
        self.assertIn("sid-a", out["sessions"])
        self.assertFalse([k for k in out["sessions"] if k.startswith("pid:")])

    def test_missing_session_file(self):
        self.assertNotIn("pid:999", self.run_reader(["pid:999"])["sessions"])

    def test_session_file_without_session_id(self):
        self.write(self.sessions, "5.json", {"peerToken": "x"})
        self.assertNotIn("pid:5", self.run_reader(["pid:5"])["sessions"])

    def test_garbage_session_file(self):
        self.write(self.sessions, "6.json", "{not json")
        self.assertNotIn("pid:6", self.run_reader(["pid:6"])["sessions"])

    def test_traversal_session_id_rejected(self):
        self.write(self.sessions, "7.json", {"sessionId": "../../etc/passwd"})
        self.assertNotIn("pid:7", self.run_reader(["pid:7"])["sessions"])

    def test_non_digit_pid_ignored(self):
        self.write(self.sessions, "12a.json", {"sessionId": "sid-a"})
        self.stat()
        self.assertNotIn("pid:12a", self.run_reader(["pid:12a"])["sessions"])

    def test_rate_limits_still_read(self):
        rl = {"five_hour": {"used_percentage": 3}}
        self.stat(rate_limits=rl)
        self.assertEqual(self.run_reader(["sid-a"])["rate_limits"], rl)


class AppClaudeKeyTest(unittest.TestCase):
    def test_claude_with_pid(self):
        self.assertEqual(herdeck.app_claude_key({"kind": "claude", "pid": 50516}), "pid:50516")

    def test_non_claude(self):
        self.assertIsNone(herdeck.app_claude_key({"kind": "codex", "pid": 1}))

    def test_no_pid(self):
        for pid in (None, 0, True):
            self.assertIsNone(herdeck.app_claude_key({"kind": "claude", "pid": pid}))


if __name__ == "__main__":
    unittest.main()
