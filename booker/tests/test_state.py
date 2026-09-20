import datetime as dt
import json
import os
import tempfile
import unittest

from booker.state import AuthStatus, BookedHistory


class BookedHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "booked_history.json")

    def history(self) -> BookedHistory:
        return BookedHistory(self.path)

    def test_empty_when_missing(self):
        h = self.history()
        self.assertFalse(h.has("jan", 20260921, "c1"))
        self.assertEqual(h.all(), {})
        self.assertFalse(os.path.exists(self.path))

    def test_add_has_remove(self):
        h = self.history()
        self.assertFalse(h.has("jan", 20260921, "c1"))
        h.add("jan", 20260921, "c1")
        self.assertTrue(h.has("jan", 20260921, "c1"))
        self.assertFalse(h.has("jan", 20260921, "c2"))
        h.remove("jan", 20260921, "c1")
        self.assertFalse(h.has("jan", 20260921, "c1"))

    def test_all_shape(self):
        h = self.history()
        h.add("jan", 20260921, "c1")
        entries = h.all()
        self.assertEqual(list(entries), ["jan::20260921::c1"])
        dt.datetime.fromisoformat(entries["jan::20260921::c1"]["booked_at"])

    def test_save_reload(self):
        h = self.history()
        h.add("jan", 20260921, "c1")
        h.save()
        self.assertTrue(os.path.exists(self.path))
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertIn("jan::20260921::c1", raw)
        self.assertEqual(set(raw["jan::20260921::c1"]), {"booked_at"})
        fresh = self.history()
        self.assertTrue(fresh.has("jan", 20260921, "c1"))

    def test_save_is_atomic_no_tmp_left(self):
        h = self.history()
        h.add("jan", 20260921, "c1")
        h.save()
        leftovers = [p for p in os.listdir(self._tmp.name) if p != "booked_history.json"]
        self.assertEqual(leftovers, [])

    def test_save_empty_writes_empty_map(self):
        self.history().save()
        with open(self.path, "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {})

    def test_prune_removes_past_keeps_current(self):
        h = self.history()
        h.add("jan", 20260910, "past")
        h.add("jan", 20260915, "today")
        h.add("jan", 20260921, "future")
        removed = h.prune(20260915)
        self.assertEqual(removed, 1)
        self.assertFalse(h.has("jan", 20260910, "past"))
        self.assertTrue(h.has("jan", 20260915, "today"))
        self.assertTrue(h.has("jan", 20260921, "future"))

    def test_prune_callable_today(self):
        h = self.history()
        h.add("jan", 20260910, "past")
        self.assertEqual(h.prune(lambda: 20260915), 1)
        self.assertFalse(h.has("jan", 20260910, "past"))

    def test_account_scoping_and_key_shape(self):
        h = self.history()
        h.add("anna", 20260921, "c1")
        self.assertFalse(h.has("jan", 20260921, "c1"))
        h.add("jan", 20260928, "c1")
        self.assertTrue(h.has("jan", 20260928, "c1"))
        self.assertEqual(
            set(h.all()), {"anna::20260921::c1", "jan::20260928::c1"}
        )

    def test_ignore_corrupt_file(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json {{{")
        h = self.history()
        self.assertEqual(h.all(), {})

    def test_persisted_entries_hold_no_credentials(self):
        h = self.history()
        h.add("jan", 20260921, "c1")
        h.save()
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(
            list(raw["jan::20260921::c1"]), ["booked_at"]
        )


class AuthStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "auth_status.json")

    def status(self) -> AuthStatus:
        return AuthStatus(self.path)

    def test_empty_when_missing(self):
        s = self.status()
        self.assertEqual(s.all(), {})
        self.assertEqual(s.get("jan"), {})
        self.assertFalse(os.path.exists(self.path))

    def test_set_get_roundtrip(self):
        s = self.status()
        s.set("jan", True, user_id="u-42")
        entry = s.get("jan")
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["user_id"], "u-42")
        dt.datetime.fromisoformat(entry["checked_at"])
        s.set("jan", False)
        self.assertEqual(s.get("jan")["status"], "failed")
        self.assertIsNone(s.get("jan")["user_id"])
        self.assertEqual(s.get("anna"), {})

    def test_save_reload(self):
        s = self.status()
        s.set("jan", True, user_id="u-42")
        s.set("anna", False)
        s.save()
        self.assertTrue(os.path.exists(self.path))
        fresh = self.status()
        self.assertEqual(fresh.get("jan")["status"], "ok")
        self.assertEqual(fresh.get("jan")["user_id"], "u-42")
        self.assertEqual(fresh.get("anna")["status"], "failed")
        self.assertIsNone(fresh.get("anna")["user_id"])
        self.assertEqual(
            set(fresh.all()), {"anna", "jan"}
        )

    def test_save_is_atomic_no_tmp_left(self):
        s = self.status()
        s.set("jan", True)
        s.save()
        leftovers = [p for p in os.listdir(self._tmp.name) if p != "auth_status.json"]
        self.assertEqual(leftovers, [])

    def test_ignore_corrupt_file(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json {{{")
        s = self.status()
        self.assertEqual(s.all(), {})

    def test_ignores_foreign_and_bad_status_entries(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(
                '{"jan": {"status": "ok", "user_id": "u"}, '
                '"x": "nope", "bogus": {"status": "unknown"}}'
            )
        s = self.status()
        self.assertEqual(set(s.all()), {"jan"})


if __name__ == "__main__":
    unittest.main()