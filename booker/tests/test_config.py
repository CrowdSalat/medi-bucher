import os
import unittest
from contextlib import contextmanager

from booker.config import ConfigError, parse_config

_ENV_KEYS = ("MEDI_CREDS_JOHANNA_NAME", "MEDI_CREDS_JOHANNA_PW")


class ParseConfigTests(unittest.TestCase):
    def _config(self, username=None, password=None):
        account = {
            "name": "johanna",
            "timezone": "Europe/Berlin",
            "targets": [
                {"name": "Body Pump", "day": 1, "time": "19:00", "event_type_id": "evt-1"},
            ],
        }
        if username is not None:
            account["username"] = username
        if password is not None:
            account["password"] = password
        return {"accounts": [account]}

    @contextmanager
    def _env(self, **kwargs):
        saved = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
        for k, v in kwargs.items():
            os.environ[k] = v
        try:
            yield
        finally:
            for k in _ENV_KEYS:
                os.environ.pop(k, None)
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_inline_credentials_preferred(self):
        raw = self._config(username="a@b.c", password="pw1")
        with self._env(MEDI_CREDS_JOHANNA_NAME="old@x", MEDI_CREDS_JOHANNA_PW="oldpw"):
            cfg = parse_config(raw)
        acct = cfg.accounts[0]
        self.assertTrue(acct.available)
        self.assertIsNone(acct.skip_reason)
        self.assertEqual(acct.credentials.username, "a@b.c")
        self.assertEqual(acct.credentials.password, "pw1")
        self.assertEqual(acct.targets[0].name, "Body Pump")

    def test_env_fallback_when_inline_absent(self):
        raw = self._config()
        with self._env(MEDI_CREDS_JOHANNA_NAME="env@x", MEDI_CREDS_JOHANNA_PW="envpw"):
            cfg = parse_config(raw)
        acct = cfg.accounts[0]
        self.assertTrue(acct.available)
        self.assertEqual(acct.credentials.username, "env@x")
        self.assertEqual(acct.credentials.password, "envpw")

    def test_partial_inline_skipped_env_used(self):
        raw = self._config(username="only-user")
        with self._env(MEDI_CREDS_JOHANNA_NAME="env@x", MEDI_CREDS_JOHANNA_PW="envpw"):
            cfg = parse_config(raw)
        acct = cfg.accounts[0]
        self.assertTrue(acct.available)
        self.assertEqual(acct.credentials.username, "env@x")

    def test_no_credentials_marks_unavailable(self):
        raw = self._config()
        with self._env():
            cfg = parse_config(raw)
        acct = cfg.accounts[0]
        self.assertFalse(acct.available)
        self.assertEqual(acct.skip_reason, "missing credentials")
        self.assertIsNone(acct.credentials)

    def test_duplicate_targets_rejected(self):
        raw = self._config(username="u", password="p")
        raw["accounts"][0]["targets"].append({"name": "Body Pump", "day": 1, "time": "19:00"})
        with self.assertRaises(ConfigError):
            parse_config(raw)

    def test_facility_hardcoded_not_in_config(self):
        raw = self._config(username="u", password="p")
        cfg = parse_config(raw)
        self.assertEqual(cfg.facility.id, "0273e18b-52bf-404e-afa6-8bfb2eeccbad")
        self.assertEqual(cfg.facility.name, "mediterana")


if __name__ == "__main__":
    unittest.main()