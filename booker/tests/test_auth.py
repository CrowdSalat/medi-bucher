import os
import tempfile
import unittest

import httpx

from booker.auth import (
    AuthSession,
    AuthenticationError,
    Credentials,
    TokenNotValidError,
    login,
    resolve_user_id,
)
from booker.client import AuthClient

LOGIN_URL = "https://core.mywellness.com/v2/enduser/authentication/login"
ME_URL = "https://services.mywellness.com/application/EC1D38D7-0000-0000-0000-000000000000/Me"
BOOK_URL = "https://calendar.mywellness.com/v2/enduser/class/Book"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class LoginTests(unittest.TestCase):
    def test_login_success(self):
        http = _client(lambda req: httpx.Response(200, json={"bearerToken": "tok-abc"}))
        self.assertEqual(login("u", "p", http), "tok-abc")

    def test_login_accepts_alt_token_key(self):
        http = _client(lambda req: httpx.Response(200, json={"token": "tok-xyz"}))
        self.assertEqual(login("u", "p", http), "tok-xyz")

    def test_login_http_401(self):
        http = _client(lambda req: httpx.Response(401, json={"error": "invalid"}))
        with self.assertRaises(AuthenticationError):
            login("u", "p", http)

    def test_login_missing_token(self):
        http = _client(lambda req: httpx.Response(200, json={"kind": "ok"}))
        with self.assertRaises(AuthenticationError):
            login("u", "p", http)


class ResolveUserTests(unittest.TestCase):
    def test_data_id(self):
        http = _client(lambda req: httpx.Response(200, json={"data": {"id": "x"}}))
        self.assertEqual(resolve_user_id("tok", http), "x")

    def test_token_not_valid(self):
        http = _client(
            lambda req: httpx.Response(
                200, json={"errors": [{"field": "TokenNotValid", "type": "Security"}]}
            )
        )
        with self.assertRaises(TokenNotValidError):
            resolve_user_id("tok", http)

    def test_user_context_fallback(self):
        http = _client(lambda req: httpx.Response(200, json={"userContext": {"id": "y"}}))
        self.assertEqual(resolve_user_id("tok", http), "y")

    def test_data_user_context_fallback(self):
        http = _client(
            lambda req: httpx.Response(200, json={"data": {"userContext": {"id": "z"}}})
        )
        self.assertEqual(resolve_user_id("tok", http), "z")

    def test_no_user_id(self):
        http = _client(lambda req: httpx.Response(200, json={"data": {}}))
        with self.assertRaises(TokenNotValidError):
            resolve_user_id("tok", http)


class SessionTests(unittest.TestCase):
    def test_from_token_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("# comment\n\n   the-token  \n")
            path = fh.name
        try:
            session = AuthSession.from_token_file(path)
            self.assertEqual(session.token, "the-token")
            self.assertIsNone(session.user_id)
        finally:
            os.unlink(path)

    def test_from_token_file_empty(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("# nothing\n\n")
            path = fh.name
        try:
            self.assertEqual(AuthSession.from_token_file(path).token, "")
        finally:
            os.unlink(path)

    def test_validate_ok(self):
        http = _client(lambda req: httpx.Response(200, json={"data": {"id": "u1"}}))
        session = AuthSession(token="tok")
        self.assertTrue(session.validate(http))
        self.assertEqual(session.user_id, "u1")

    def test_validate_invalid(self):
        http = _client(
            lambda req: httpx.Response(
                200, json={"errors": [{"field": "TokenNotValid"}]}
            )
        )
        session = AuthSession(token="tok")
        self.assertFalse(session.validate(http))
        self.assertIsNone(session.user_id)


class EnvCredsTests(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for key in ("MEDI_CREDS_ZED_NAME", "MEDI_CREDS_ZED_PW"):
            self._saved[key] = os.environ.pop(key, None)

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_from_env_absent(self):
        self.assertIsNone(Credentials.from_env("zed"))

    def test_from_env_partial(self):
        os.environ["MEDI_CREDS_ZED_NAME"] = "a@b.c"
        self.assertIsNone(Credentials.from_env("zed"))

    def test_from_env_present(self):
        os.environ["MEDI_CREDS_ZED_NAME"] = "a@b.c"
        os.environ["MEDI_CREDS_ZED_PW"] = "pw"
        creds = Credentials.from_env("zed")
        self.assertEqual(creds.username, "a@b.c")
        self.assertEqual(creds.password, "pw")


class GuardTests(unittest.TestCase):
    def test_401_triggers_single_refresh_and_retry(self):
        counts = {"login": 0, "book": 0}

        def handler(req):
            path = req.url.path
            if "login" in path:
                counts["login"] += 1
                return httpx.Response(200, json={"bearerToken": "fresh"})
            if "Me" in path:
                return httpx.Response(200, json={"data": {"id": "u1"}})
            if "Book" in path:
                counts["book"] += 1
                if counts["book"] == 1:
                    return httpx.Response(401, json={})
                return httpx.Response(200, json={"result": "Booked"})
            return httpx.Response(404, json={})

        ac = AuthClient("zed", credentials=Credentials("u", "p"), http=_client(handler))
        ac.authenticate()
        resp = ac.call("POST", BOOK_URL, json={"partitionDate": 20260916})
        self.assertEqual(resp.json()["result"], "Booked")
        self.assertEqual(counts["login"], 2)
        self.assertEqual(counts["book"], 2)

    def test_refresh_failure_propagates_no_double_retry(self):
        counts = {"login": 0, "book": 0}

        def handler(req):
            path = req.url.path
            if "login" in path:
                counts["login"] += 1
                if counts["login"] == 1:
                    return httpx.Response(200, json={"bearerToken": "tok1"})
                return httpx.Response(401, json={})
            if "Me" in path:
                return httpx.Response(200, json={"data": {"id": "u1"}})
            if "Book" in path:
                counts["book"] += 1
                return httpx.Response(401, json={})
            return httpx.Response(404, json={})

        ac = AuthClient("zed", credentials=Credentials("u", "p"), http=_client(handler))
        ac.authenticate()
        with self.assertRaises(AuthenticationError):
            ac.call("POST", BOOK_URL, json={})
        self.assertEqual(counts["login"], 2)
        self.assertEqual(counts["book"], 1)

    def test_token_not_valid_body_triggers_refresh(self):
        counts = {"login": 0, "book": 0}

        def handler(req):
            path = req.url.path
            if "login" in path:
                counts["login"] += 1
                return httpx.Response(200, json={"bearerToken": "fresh"})
            if "Me" in path:
                return httpx.Response(200, json={"data": {"id": "u1"}})
            if "Book" in path:
                counts["book"] += 1
                if counts["book"] == 1:
                    return httpx.Response(
                        200, json={"errors": [{"field": "TokenNotValid"}]}
                    )
                return httpx.Response(200, json={"result": "Booked"})
            return httpx.Response(404, json={})

        ac = AuthClient("zed", credentials=Credentials("u", "p"), http=_client(handler))
        ac.authenticate()
        resp = ac.call("POST", BOOK_URL, json={})
        self.assertEqual(resp.json()["result"], "Booked")
        self.assertEqual(counts["login"], 2)
        self.assertEqual(counts["book"], 2)

    def test_stale_token_override_falls_back_to_login(self):
        served = {"stale": False}

        def handler(req):
            path = req.url.path
            if "login" in path:
                return httpx.Response(200, json={"bearerToken": "fresh"})
            if "Me" in path:
                if not served["stale"]:
                    served["stale"] = True
                    return httpx.Response(
                        200, json={"errors": [{"field": "TokenNotValid"}]}
                    )
                return httpx.Response(200, json={"data": {"id": "u1"}})
            return httpx.Response(404, json={})

        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("# stale override\nsome-old-token\n")
            path = fh.name
        try:
            ac = AuthClient("zed", credentials=Credentials("u", "p"), http=_client(handler))
            ac.authenticate(token_file=path)
            self.assertEqual(ac.user_id, "u1")
            self.assertEqual(ac.token, "fresh")
        finally:
            os.unlink(path)

    def test_bad_override_without_credentials_suspended(self):
        def handler(req):
            path = req.url.path
            if "Me" in path:
                return httpx.Response(200, json={"errors": [{"field": "TokenNotValid"}]})
            return httpx.Response(200, json={})

        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("garbage-token\n")
            path = fh.name
        try:
            ac = AuthClient("zed", http=_client(handler))
            with self.assertRaises(AuthenticationError):
                ac.authenticate(token_file=path)
        finally:
            os.unlink(path)

    def test_authenticate_sets_user_id(self):
        ac = AuthClient(
            "zed",
            credentials=Credentials("u", "p"),
            http=_client(
                lambda req: httpx.Response(200, json={"data": {"id": "u9"}})
                if "Me" in req.url.path
                else httpx.Response(200, json={"bearerToken": "tok"})
            ),
        )
        ac.authenticate()
        self.assertEqual(ac.user_id, "u9")
        self.assertEqual(ac.token, "tok")
        self.assertEqual(ac.me(), "u9")


if __name__ == "__main__":
    unittest.main()