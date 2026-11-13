import unittest
from unittest import mock

import herdeck
from support import FakeHub, FROZEN_NOW, load_fixture


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

    def test_label_falls_back_to_id(self):
        a = [{"space_key": "ws:p/w1", "workspace": None, "workspace_id": "w1"}]
        self.assertEqual(herdeck.space_entries(a), [{"key": "ws:p/w1", "label": "w1"}])


class PublishSpacesTest(unittest.TestCase):
    def make(self, connected=True):
        src = herdeck.Source({"name": "personal", "label": "Personal",
                              "socket": "/nonexistent/herdr.sock"}, FakeHub(), "herdr")
        src.raw = load_fixture("herdr_snapshot.json")
        src.connected = connected
        return src

    def test_published_source_lists_spaces(self):
        src = self.make()
        with mock.patch("herdeck.time.time", return_value=FROZEN_NOW):
            src._publish()
        data = src.hub.sources["personal"]
        self.assertEqual(data["spaces"], [{"key": "ws:personal/w1", "label": "personal"}])
        self.assertTrue(data["agents"])
        for a in data["agents"]:
            self.assertEqual(a["space_key"], "ws:personal/w1")

    def test_disconnected_has_empty_spaces(self):
        src = self.make(connected=False)
        with mock.patch("herdeck.time.time", return_value=FROZEN_NOW):
            src._publish()
        self.assertEqual(src.hub.sources["personal"]["spaces"], [])
