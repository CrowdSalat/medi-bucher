#!/usr/bin/env python3
"""
Validate a MyWellness session (token OR cookie) and resolve the user's id.

Usage (one of):
    MW_TOKEN='<Bearer token>'            python3 scripts/validate_session.py
    MW_TOKEN='<token>'                   python3 scripts/validate_session.py --prefix-userinfo
    MW_COOKIE='name=value; name2=value2' python3 scripts/validate_session.py
    python3 scripts/validate_session.py --token-file secrets/token.txt
    python3 scripts/validate_session.py --cookie-file secrets/cookie.txt

Prints:
    - session validity (via services.../Me)
    - the logged-in user id (needed for the Book payload)
    - account culture
"""
import os
import sys

import httpx

BASE_SERVICES = "https://services.mywellness.com"
API_APP_ID = "EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9"


def load_secret(kind: str) -> str:
    env = os.environ.get(f"MW_{kind.upper()}")
    if env:
        return env.strip().strip('"\'')
    flag = "--token-file" if kind == "token" else "--cookie-file"
    path = None
    for p in (sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None,
              f"secrets/{kind}.txt"):
        if p and os.path.exists(p):
            path = p
            break
    if not path:
        sys.exit(f"No {kind} provided. Set MW_{kind.upper()} env or {flag} secrets/{kind}.txt")
    with open(path) as fh:
        return fh.read().strip()


def main() -> None:
    token = None
    cookie = None
    if os.environ.get("MW_TOKEN") or os.path.exists("secrets/token.txt") or "--token-file" in sys.argv:
        token = load_secret("token")
    if os.environ.get("MW_COOKIE") or os.path.exists("secrets/cookie.txt") or "--cookie-file" in sys.argv:
        cookie = load_secret("cookie")

    headers = {
        "Content-Type": "application/json",
        "x-mwapps-appid": API_APP_ID,
        "x-mwapps-client": "enduserweb",
    }
    if token:
        token = token.removeprefix("Bearer ").strip()
        headers["Authorization"] = f"Bearer {token}"
        print(f"Using Authorization: Bearer {token[:12]}...")
    if cookie:
        headers["Cookie"] = cookie
        print(f"Using Cookie: {cookie[:40]}...")

    with httpx.Client(headers=headers, timeout=15.0) as client:
        r = client.post(f"{BASE_SERVICES}/application/{API_APP_ID}/Me", json={})
        print(f"[{r.status_code}] Me -> {r.text[:250]}")

        if r.status_code == 200 and "TokenNotValid" not in r.text:
            data = r.json()
            user = (data.get("data") or {}).get("userContext") or data.get("userContext") or data.get("user") or data.get("data")
            uid = (user or {}).get("id") if isinstance(user, dict) else "?"
            culture = (user or {}).get("culture") if isinstance(user, dict) else "?"
            print(f"USER ID        : {uid}")
            print(f"CULTURE        : {culture}")
        else:
            print("SESSION INVALID - re-login required")

    if token:
        with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=15.0) as client:
            r = client.get(
                "https://calendar.mywellness.com/v2/enduser/class/GetParticipants",
                params={"date": "2026-09-16", "classId": "e96a4fc8-a5be-48a8-90ab-c35d770c0417"},
            )
            print(f"[{r.status_code}] GetParticipants (auth check) -> {r.text[:150]}")


if __name__ == "__main__":
    main()