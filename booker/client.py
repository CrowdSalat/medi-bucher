from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import httpx

from .auth import (
    AuthSession,
    AuthenticationError,
    Credentials,
    TokenNotValidError,
    login,
    resolve_user_id,
    _ME_HEADERS,
    _ME_URL,
    _has_token_not_valid,
    _parse_me_response,
)

_SCHEDULE_URL = "https://calendar.mywellness.com/v2/enduser/class/Search"
_CHANNEL_ID = "5bf51e0109ee93b5aef82c77"
_MAX_SPAN_DAYS = 7


class ScheduleApiError(Exception):
    def __init__(self, method: str, url: str, status: int, detail: str = "") -> None:
        self.method = method
        self.url = url
        self.status = status
        self.detail = detail
        msg = f"{method} {url} -> {status}"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


@dataclass
class BookingInfo:
    booking_opens_on: _dt.datetime | None = None
    booking_user_status: str = ""
    cancellation_minutes_in_advance: int | None = None
    booking_has_waiting_list: bool = False

@dataclass
class ScheduleInstance:
    id: str
    name: str
    event_type_id: str
    start_date: _dt.datetime
    partition_date: int
    has_layout: bool
    room_id: str | None = None
    room: str | None = None
    available_places: int = 0
    max_participants: int = 0
    booking_info: BookingInfo = field(default_factory=BookingInfo)
    is_participant: bool = False
    waiting_list_position: int | None = None


def _parse_dt(val: str | None) -> _dt.datetime | None:
    if val is None:
        return None
    return _dt.datetime.fromisoformat(val)


def _parse_instance(raw: dict) -> ScheduleInstance:
    bi_raw = raw.get("bookingInfo", {}) or {}
    return ScheduleInstance(
        id=raw["id"],
        name=raw.get("name", ""),
        event_type_id=raw.get("eventTypeId", ""),
        start_date=_parse_dt(raw["startDate"]),
        partition_date=int(raw.get("partitionDate", 0)),
        has_layout=bool(raw.get("hasLayout", False)),
        room_id=raw.get("roomId"),
        room=raw.get("room"),
        available_places=int(raw.get("availablePlaces", 0)),
        max_participants=int(raw.get("maxParticipants", 0)),
        booking_info=BookingInfo(
            booking_opens_on=_parse_dt(bi_raw.get("bookingOpensOn")),
            booking_user_status=bi_raw.get("bookingUserStatus", ""),
            cancellation_minutes_in_advance=(
                int(bi_raw["cancellationMinutesInAdvance"])
                if "cancellationMinutesInAdvance" in bi_raw and bi_raw["cancellationMinutesInAdvance"] is not None
                else None
            ),
            booking_has_waiting_list=bool(bi_raw.get("bookingHasWaitingList", False)),
        ),
        is_participant=bool(raw.get("isParticipant", False)),
        waiting_list_position=(
            int(raw["waitingListPosition"]) if raw.get("waitingListPosition") is not None else None
        ),
    )


def _fetch_page(
    client: httpx.Client,
    facility_id: str,
    from_date: _dt.date,
    to_date: _dt.date,
) -> list[ScheduleInstance]:
    params = {
        "eventTypes": "Class",
        "facilityId": facility_id,
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
    }
    try:
        resp = client.get(
            _SCHEDULE_URL,
            params=params,
            headers={"X-MWAPPS-CHANNELID": _CHANNEL_ID},
        )
    except httpx.HTTPError as exc:
        raise ScheduleApiError("GET", _SCHEDULE_URL, 0, str(exc)) from exc

    if resp.status_code >= 400:
        raise ScheduleApiError(
            "GET", _SCHEDULE_URL, resp.status_code, resp.text[:200]
        )

    try:
        body = resp.json()
    except Exception as exc:
        raise ScheduleApiError("GET", _SCHEDULE_URL, resp.status_code, "non-JSON response") from exc

    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, dict):
            items = data.get("items", [])
        else:
            items = body.get("items", [])
    else:
        items = []

    return [_parse_instance(it) for it in items]


class ScheduleClient:
    def __init__(self) -> None:
        self._client = httpx.Client(timeout=30.0)

    def fetch(
        self,
        facility_id: str,
        from_date: _dt.date,
        to_date: _dt.date,
    ) -> list[ScheduleInstance]:
        span = (to_date - from_date).days
        if span <= 0:
            return []

        all_instances: list[ScheduleInstance] = []
        current = from_date
        while current <= to_date:
            chunk_end = min(current + _dt.timedelta(days=_MAX_SPAN_DAYS - 1), to_date)
            all_instances.extend(
                _fetch_page(self._client, facility_id, current, chunk_end)
            )
            current = chunk_end + _dt.timedelta(days=1)
        return all_instances

    def close(self) -> None:
        self._client.close()


class AuthClient:
    def __init__(
        self,
        account_name: str,
        credentials: Credentials | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self._account_name = account_name
        self._credentials = credentials
        self._session = AuthSession()
        self._http = http if http is not None else httpx.Client(timeout=30.0)

    @property
    def user_id(self) -> str | None:
        return self._session.user_id

    @property
    def token(self) -> str:
        return self._session.token

    def authenticate(
        self,
        credentials: Credentials | None = None,
        token_file: str | None = None,
    ) -> None:
        if credentials is not None:
            self._credentials = credentials
        creds = self._credentials or Credentials.from_env(self._account_name)
        if token_file:
            token = AuthSession.from_token_file(token_file).token
            if not token:
                raise AuthenticationError(f"{token_file}: no token found")
            self._session.token = token
            try:
                self.me()
            except AuthenticationError as exc:
                raise AuthenticationError(
                    f"account {self._account_name}: "
                    f"token override invalid ({type(exc).__name__})"
                ) from exc
            return
        if creds is None:
            raise AuthenticationError(
                f"account {self._account_name}: no credentials and no token override"
            )
        self._credentials = creds
        self.refresh()

    def refresh(self) -> None:
        creds = self._credentials or Credentials.from_env(self._account_name)
        if creds is None:
            raise AuthenticationError(
                f"account {self._account_name}: no credentials to refresh with"
            )
        token = login(creds.username, creds.password, self._http)
        self._session.token = token
        try:
            self._session.user_id = resolve_user_id(token, self._http)
        except TokenNotValidError as exc:
            raise AuthenticationError(
                f"account {self._account_name}: fresh token rejected by Me"
            ) from exc

    def me(self) -> str:
        resp = self.call("POST", _ME_URL, json={}, headers=_ME_HEADERS)
        try:
            uid = _parse_me_response(resp)
        except TokenNotValidError as exc:
            raise AuthenticationError(
                f"account {self._account_name}: Me rejected token after refresh"
            ) from exc
        self._session.user_id = uid
        return uid

    def call(self, method: str, url: str, **kwargs) -> httpx.Response:
        headers = dict(kwargs.get("headers") or {})
        headers["Authorization"] = f"Bearer {self._session.token}"
        kwargs["headers"] = headers
        resp = self._http.request(method, url, **kwargs)
        if self._is_expired(resp):
            self.refresh()
            resp = self._http.request(method, url, **kwargs)
        return resp

    @staticmethod
    def _is_expired(resp: httpx.Response) -> bool:
        if resp.status_code == 401:
            return True
        if resp.status_code // 100 != 2:
            return False
        try:
            body = resp.json()
        except Exception:
            return False
        return isinstance(body, dict) and _has_token_not_valid(body)

    def close(self) -> None:
        self._http.close()
