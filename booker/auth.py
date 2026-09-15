from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

_APP_ID = "EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9"
_LOGIN_URL = "https://core.mywellness.com/v2/enduser/authentication/login?_c=de-DE"
_ME_URL = f"https://services.mywellness.com/application/{_APP_ID}/Me"

_LOGIN_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "x-mwapps-appid": _APP_ID,
    "x-mwapps-client": "enduserweb",
}

_ME_HEADERS = {
    "Content-Type": "application/json",
    "x-mwapps-appid": _APP_ID,
    "x-mwapps-client": "enduserweb",
}


class AuthenticationError(Exception):
    pass


class TokenNotValidError(AuthenticationError):
    pass


@dataclass
class Credentials:
    username: str
    password: str

    @classmethod
    def from_env(cls, account_name: str) -> Credentials | None:
        prefix = f"MEDI_CREDS_{account_name.upper()}"
        username = os.environ.get(f"{prefix}_NAME")
        password = os.environ.get(f"{prefix}_PW")
        if not username or not password:
            return None
        return cls(username=username, password=password)


@dataclass
class AuthSession:
    token: str = ""
    user_id: str | None = None

    @classmethod
    def from_token_file(cls, path: str) -> AuthSession:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                return cls(token=line, user_id=None)
        return cls(token="", user_id=None)

    def validate(self, http: httpx.Client) -> bool:
        try:
            self.user_id = resolve_user_id(self.token, http)
        except AuthenticationError:
            return False
        return True


def _has_token_not_valid(body) -> bool:
    errors = body.get("errors")
    if not isinstance(errors, list):
        return False
    return any(
        isinstance(err, dict) and err.get("field") == "TokenNotValid"
        for err in errors
    )


def _extract_user_id(body) -> str | None:
    data = body.get("data")
    if isinstance(data, dict):
        uid = data.get("id")
        if uid:
            return uid
        ctx = data.get("userContext")
        if isinstance(ctx, dict):
            return ctx.get("id") or None
    ctx = body.get("userContext")
    if isinstance(ctx, dict):
        return ctx.get("id") or None
    return None


def login(username: str, password: str, http: httpx.Client) -> str:
    body = {"username": username, "password": password, "keepMeLoggedIn": False}
    resp = http.post(_LOGIN_URL, json=body, headers=_LOGIN_HEADERS)
    if resp.status_code >= 400:
        raise AuthenticationError(f"login failed: http {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        raise AuthenticationError("login returned an unexpected response")
    token = data.get("bearerToken") or data.get("token")
    if not token:
        raise AuthenticationError("login returned no token")
    return token


def _parse_me_response(resp: httpx.Response) -> str:
    if resp.status_code >= 400:
        raise AuthenticationError(f"Me: http {resp.status_code}")
    try:
        body = resp.json()
    except Exception:
        raise AuthenticationError("Me returned a non-JSON response")
    if not isinstance(body, dict) or _has_token_not_valid(body):
        raise TokenNotValidError("token not valid")
    uid = _extract_user_id(body)
    if not uid:
        raise TokenNotValidError("Me returned no user id")
    return uid


def resolve_user_id(token: str, http: httpx.Client) -> str:
    headers = dict(_ME_HEADERS)
    headers["Authorization"] = f"Bearer {token}"
    resp = http.post(_ME_URL, json={}, headers=headers)
    return _parse_me_response(resp)