import http.client
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import herdeck
from herdeck import PinGuard

COOKIE = "herdeck=TOK; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly"
PIN_HTML = (Path(herdeck.__file__).resolve().parent / "pin.html").read_text(encoding="utf-8")


class HttpTest(unittest.TestCase):
    def setUp(self):
        stub = SimpleNamespace(
            cfg={}, html="<deck/>", html_pin=PIN_HTML, hub=herdeck.Hub(),
            client_config=lambda: {"title": "t"}, manifest=lambda: {"name": "herdeck"},
            raw=lambda: {}, action=lambda b: {"ok": True})
        self.srv = herdeck.Server(("127.0.0.1", 0), herdeck.make_handler(stub, "TOK", "123456", PinGuard()))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def login(self, pin):
        return self.req("POST", "/api/login", json.dumps({"pin": pin}), {"Content-Type": "application/json"})

    def test_root_without_auth_serves_pin_page(self):
        r, body = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b'id="pinpad"', body)
        self.assertEqual(r.getheader("Cache-Control"), "no-store")

    def test_api_without_auth_is_401(self):
        self.assertEqual(self.req("GET", "/api/state")[0].status, 401)
        self.assertEqual(self.req("POST", "/api/action", "{}")[0].status, 401)

    def test_token_query_still_works(self):
        r, body = self.req("GET", "/?t=TOK")
        self.assertEqual(r.status, 200)
        self.assertEqual(body, b"<deck/>")
        self.assertEqual(r.getheader("Set-Cookie"), COOKIE)

    def test_token_header_still_works(self):
        r, _ = self.req("GET", "/api/state", headers={"X-Herdeck-Token": "TOK"})
        self.assertEqual(r.status, 200)

    def test_wrong_pin(self):
        r, body = self.login("000000")
        self.assertEqual(r.status, 401)
        j = json.loads(body)
        self.assertEqual(j["error"], "bad_pin")
        self.assertEqual(j["retry_after"], 0)

    def test_right_pin_sets_cookie(self):
        r, body = self.login("123456")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Set-Cookie"), COOKIE)
        r2, _ = self.req("GET", "/api/state", headers={"Cookie": "herdeck=TOK"})
        self.assertEqual(r2.status, 200)

    def test_login_locked_refuses_correct_pin(self):
        for _ in range(5):
            self.login("000000")
        r, body = self.login("123456")
        self.assertEqual(r.status, 429)
        j = json.loads(body)
        self.assertEqual(j["error"], "locked")
        self.assertEqual(j["retry_after"], 30)
        self.assertIsNone(r.getheader("Set-Cookie"))

    def test_login_oversized_body_rejected(self):
        r, _ = self.req("POST", "/api/login", json.dumps({"pin": "1" * 5000}))
        self.assertEqual(r.status, 400)


if __name__ == "__main__":
    unittest.main()
