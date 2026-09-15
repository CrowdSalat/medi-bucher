from __future__ import annotations

import json
import logging

from .client import AuthClient

log = logging.getLogger("booker")

BOOK_URL = "https://calendar.mywellness.com/v2/enduser/class/Book?_c=de-DE"
BOOK_APP_ID = "EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9"
BOOK_HEADERS = {
    "Content-Type": "application/json",
    "x-mwapps-appid": BOOK_APP_ID,
    "x-mwapps-client": "enduserweb",
}

RESULT_BOOKED = "Booked"
RESULT_ALREADY_BOOKED = "UserAlreadyBooked"
RESULT_NOT_OPEN = "NotOpen"
RESULT_FAILED = "Failed"
RESULT_UNKNOWN = "UnknownResponse"

SUCCESS_RESULTS = {RESULT_BOOKED, RESULT_ALREADY_BOOKED}
FAIL_RESULTS = {
    "PlaceNotAvailable",
    "EventNotExists",
    "NoPermissionsForUserException",
    RESULT_FAILED,
}


def _error_message(resp) -> str:
    try:
        body = resp.json()
    except Exception:
        return ""
    if isinstance(body, list):
        parts = [
            str(item.get("errorMessage", ""))
            for item in body
            if isinstance(item, dict) and item.get("errorMessage")
        ]
        return " ".join(parts)
    if isinstance(body, dict):
        return str(body.get("errorMessage", ""))
    return ""


class BurstExecutor:
    def __init__(self, auth: AuthClient, dry_run: bool = False) -> None:
        self._auth = auth
        self.dry_run = dry_run

    @property
    def user_id(self) -> str | None:
        return self._auth.user_id

    def build_payload(
        self,
        class_id: str,
        partition_date: int,
        *,
        station: int | None = None,
        has_layout: bool = False,
    ) -> dict:
        payload = {
            "partitionDate": int(partition_date),
            "userId": self._auth.user_id,
            "classId": class_id,
        }
        if station is not None and has_layout:
            payload["station"] = int(station)
        return payload

    def book(
        self,
        class_id: str,
        partition_date: int,
        *,
        station: int | None = None,
        has_layout: bool = False,
    ) -> str:
        if has_layout and station is None:
            log.warning(
                "booker: hasLayout class %s booked without station (v1 limitation)",
                class_id,
            )
        user_id = self._auth.user_id
        if user_id is None:
            log.warning("booker: no user_id for account; cannot book %s", class_id)
            return RESULT_FAILED
        payload = self.build_payload(
            class_id, partition_date, station=station, has_layout=has_layout
        )
        if self.dry_run:
            print(f"PAYLOAD would POST {BOOK_URL} {json.dumps(payload, sort_keys=True)}")
            return RESULT_BOOKED
        resp = self._auth.call("POST", BOOK_URL, json=payload, headers=dict(BOOK_HEADERS))
        return self._map_result(resp, class_id)

    def _map_result(self, resp, class_id: str) -> str:
        if resp.status_code == 400:
            message = _error_message(resp)
            if "not opened" in message.lower():
                log.warning(
                    "booker: class %s: booking has not opened yet (NotOpen)", class_id
                )
                return RESULT_NOT_OPEN
            log.warning(
                "booker: class %s: rejected http 400: %s",
                class_id,
                message or resp.text[:200],
            )
            return RESULT_FAILED
        if resp.status_code // 100 != 2:
            log.warning(
                "booker: class %s: book failed http %s: %s",
                class_id,
                resp.status_code,
                resp.text[:200],
            )
            return RESULT_FAILED
        try:
            body = resp.json()
        except Exception:
            log.warning(
                "booker: class %s: non-JSON response: %r", class_id, resp.text[:200]
            )
            return RESULT_UNKNOWN
        if not isinstance(body, dict):
            log.warning(
                "booker: class %s: unexpected response shape: %r",
                class_id,
                resp.text[:200],
            )
            return RESULT_UNKNOWN
        result = body.get("result")
        if result in SUCCESS_RESULTS:
            return result
        if result in FAIL_RESULTS:
            log.warning("booker: class %s: book result %s", class_id, result)
            return result
        log.warning(
            "booker: class %s: unrecognized result %r; raw=%r",
            class_id,
            result,
            resp.text[:500],
        )
        return RESULT_UNKNOWN

    def waiting_list(self, class_id: str, partition_date: int) -> None:
        log.warning(
            "booker: class %s: waiting list not implemented (v1); no join", class_id
        )