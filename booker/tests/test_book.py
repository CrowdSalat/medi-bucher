import asyncio
import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest

import httpx

from booker.auth import Credentials
from booker.book import (
    RESULT_ALREADY_BOOKED,
    RESULT_BOOKED,
    RESULT_FAILED,
    RESULT_NOT_OPEN,
    RESULT_UNKNOWN,
    BurstExecutor,
)
from booker.client import AuthClient
from booker.config import Target
from booker.scheduler import BurstFire, PlannedBurst
from booker.state import BookedHistory


def make_auth(book_handler, user_id="u-1"):
    def handler(req):
        path = req.url.path
        if path.endswith("/login"):
            return httpx.Response(200, json={"bearerToken": "tok-1"})
        if path.endswith("/Me"):
            return httpx.Response(200, json={"data": {"id": user_id}})
        return book_handler(req)

    ac = AuthClient(
        "zed",
        credentials=Credentials("u", "p"),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    ac.authenticate()
    return ac


class _FakeAuth:
    def __init__(self, user_id="u-1"):
        self.user_id = user_id

    def call(self, *args, **kwargs):
        raise AssertionError("unexpected HTTP call")


class _StubExecutor:
    def __init__(self, result):
        self.result = result
        self.user_id = "u-1"

    def book(self, class_id, partition_date, **kwargs):
        return self.result


class ResultMappingTests(unittest.TestCase):
    def _executor(self, book_response):
        return BurstExecutor(make_auth(lambda req: book_response))

    def test_booked(self):
        ex = self._executor(httpx.Response(200, json={"result": "Booked"}))
        self.assertEqual(ex.book("c-1", 20260921), RESULT_BOOKED)

    def test_user_already_booked(self):
        ex = self._executor(httpx.Response(200, json={"result": "UserAlreadyBooked"}))
        self.assertEqual(ex.book("c-1", 20260921), RESULT_ALREADY_BOOKED)

    def test_place_not_available(self):
        ex = self._executor(httpx.Response(200, json={"result": "PlaceNotAvailable"}))
        self.assertEqual(ex.book("c-1", 20260921), "PlaceNotAvailable")

    def test_generic_failed(self):
        ex = self._executor(httpx.Response(200, json={"result": "Failed"}))
        self.assertEqual(ex.book("c-1", 20260921), RESULT_FAILED)

    def test_event_not_exists(self):
        ex = self._executor(httpx.Response(200, json={"result": "EventNotExists"}))
        self.assertEqual(ex.book("c-1", 20260921), "EventNotExists")

    def test_no_permissions_result(self):
        ex = self._executor(
            httpx.Response(200, json={"result": "NoPermissionsForUserException"})
        )
        self.assertEqual(ex.book("c-1", 20260921), "NoPermissionsForUserException")

    def test_missing_result(self):
        ex = self._executor(httpx.Response(200, json={"message": "fine"}))
        self.assertEqual(ex.book("c-1", 20260921), RESULT_UNKNOWN)

    def test_unrecognized_result(self):
        ex = self._executor(httpx.Response(200, json={"result": "ToMuchParticipants"}))
        self.assertEqual(ex.book("c-1", 20260921), RESULT_UNKNOWN)

    def test_not_open_400(self):
        ex = self._executor(
            httpx.Response(
                400, json=[{"field": "Error", "errorMessage": "Booking has not opened, yet."}]
            )
        )
        self.assertEqual(ex.book("c-1", 20260921), RESULT_NOT_OPEN)

    def test_other_400(self):
        ex = self._executor(
            httpx.Response(
                400,
                json=[{"field": "ClassId", "errorMessage": "The ClassId field must have valid value"}],
            )
        )
        self.assertEqual(ex.book("c-1", 20260921), RESULT_FAILED)

    def test_http_500(self):
        ex = self._executor(httpx.Response(500, text="boom"))
        self.assertEqual(ex.book("c-1", 20260921), RESULT_FAILED)

    def test_non_json_2xx(self):
        ex = self._executor(httpx.Response(200, text="<html>"))
        self.assertEqual(ex.book("c-1", 20260921), RESULT_UNKNOWN)


class PayloadTests(unittest.TestCase):
    def _executor(self):
        return BurstExecutor(make_auth(lambda req: httpx.Response(200, json={"result": "Booked"})))

    def test_exact_payload_sent(self):
        captured = {}

        def handler(req):
            captured["body"] = json.loads(req.read())
            return httpx.Response(200, json={"result": "Booked"})

        ex = BurstExecutor(make_auth(handler))
        self.assertEqual(ex.book("c-1", 20260921), RESULT_BOOKED)
        self.assertEqual(
            captured["body"],
            {"partitionDate": 20260921, "userId": "u-1", "classId": "c-1"},
        )

    def test_build_payload_int_and_no_station(self):
        ex = self._executor()
        payload = ex.build_payload("c-1", "20260921")
        self.assertEqual(
            set(payload), {"partitionDate", "userId", "classId"}
        )
        self.assertIsInstance(payload["partitionDate"], int)
        self.assertEqual(payload["partitionDate"], 20260921)

    def test_build_payload_station_only_when_layout(self):
        ex = self._executor()
        with_layout = ex.build_payload("c-1", 20260921, station=7, has_layout=True)
        self.assertEqual(with_layout["station"], 7)
        no_layout = ex.build_payload("c-1", 20260921, station=7, has_layout=False)
        self.assertNotIn("station", no_layout)
        default = ex.build_payload("c-1", 20260921)
        self.assertNotIn("station", default)


class GuardAndDryRunTests(unittest.TestCase):
    def test_401_triggers_refresh_then_retry_once(self):
        counts = {"login": 0, "book": 0}

        def handler(req):
            path = req.url.path
            if path.endswith("/login"):
                counts["login"] += 1
                return httpx.Response(200, json={"bearerToken": "fresh"})
            if path.endswith("/Me"):
                return httpx.Response(200, json={"data": {"id": "u-1"}})
            counts["book"] += 1
            if counts["book"] == 1:
                return httpx.Response(401, json={})
            return httpx.Response(200, json={"result": "Booked"})

        ac = AuthClient(
            "zed",
            credentials=Credentials("u", "p"),
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        ac.authenticate()
        ex = BurstExecutor(ac)
        self.assertEqual(ex.book("c-1", 20260921), RESULT_BOOKED)
        self.assertEqual(counts["login"], 2)
        self.assertEqual(counts["book"], 2)

    def test_dry_run_books_nothing_over_http(self):
        seen = {"calls": 0}

        def handler(req):
            seen["calls"] += 1
            raise AssertionError("dry-run must not perform HTTP")

        ac = AuthClient(
            "zed",
            credentials=Credentials("u", "p"),
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        ac._session.token = "tok"
        ac._session.user_id = "u-1"
        ex = BurstExecutor(ac, dry_run=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = ex.book("c-1", 20260921)
        self.assertEqual(result, RESULT_BOOKED)
        self.assertEqual(seen["calls"], 0)
        out = buf.getvalue()
        self.assertIn("PAYLOAD would POST", out)
        self.assertIn("20260921", out)
        self.assertIn("u-1", out)
        self.assertIn("c-1", out)
        self.assertNotIn("station", out)


class FireGlueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "booked_history.json")

    def _burst(self):
        return PlannedBurst(
            account_name="jan",
            target=Target(name="WSG", day=1, time="09:00"),
            instance_id="c-1",
            partition_date=20260921,
            trigger_at=dt.datetime(2026, 9, 19, 21, 0),
            fire_toward=dt.datetime(2026, 9, 21, 9, 0),
        )

    def test_live_success_records_and_saves(self):
        history = BookedHistory(self.path)
        fire = BurstFire(
            lambda name: _StubExecutor(RESULT_BOOKED), history=history, dry_run=False
        )
        asyncio.run(fire("jan", self._burst()))
        self.assertTrue(history.has("jan", 20260921, "c-1"))
        self.assertTrue(os.path.exists(self.path))

    def test_live_already_booked_records(self):
        history = BookedHistory(self.path)
        fire = BurstFire(
            lambda name: _StubExecutor(RESULT_ALREADY_BOOKED),
            history=history,
            dry_run=False,
        )
        asyncio.run(fire("jan", self._burst()))
        self.assertTrue(history.has("jan", 20260921, "c-1"))

    def test_live_failure_no_record(self):
        history = BookedHistory(self.path)
        fire = BurstFire(
            lambda name: _StubExecutor("PlaceNotAvailable"),
            history=history,
            dry_run=False,
        )
        asyncio.run(fire("jan", self._burst()))
        self.assertFalse(history.has("jan", 20260921, "c-1"))
        self.assertFalse(os.path.exists(self.path))

    def test_dry_run_never_records(self):
        history = BookedHistory(self.path)
        fire = BurstFire(
            lambda name: _StubExecutor(RESULT_BOOKED), history=history, dry_run=True
        )
        asyncio.run(fire("jan", self._burst()))
        self.assertFalse(history.has("jan", 20260921, "c-1"))
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()