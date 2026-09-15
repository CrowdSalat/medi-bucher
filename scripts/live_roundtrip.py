#!/usr/bin/env python3
"""Live Book->Unbook round-trip probe for the daemon's booking path.

This script performs REAL, live Book/Unbook calls against MyWellness and
must therefore only be run with explicit user approval.

Safety model:
  * Without ``--confirm`` the script only resolves the class, authenticates
    and prints the exact plan + payload it WOULD send, then exits 0.
  * With ``--confirm`` it sends ``Book``, logs the result, then ``Unbook``
    if the booking succeeded, and logs that result too. The round trip is
    always the SAME class instance.
  * It asks nothing interactively; approval is the orchestrator passing
    ``--confirm`` on the command line.
  * It deliberately does NOT touch ``booked_history.json`` (a round trip is
    a probe, not a real booked slot) and never logs credentials/tokens.

Usage:
    source ./.env
    python3 scripts/live_roundtrip.py                # plan only
    python3 scripts/live_roundtrip.py --confirm      # real Book -> Unbook
"""
import argparse
import datetime as _dt
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from booker.auth import AuthenticationError, Credentials
from booker.book import BOOK_HEADERS, BurstExecutor
from booker.client import AuthClient, ScheduleClient
from booker.config import ConfigError, load_config

UNBOOK_URL = "https://calendar.mywellness.com/v2/enduser/class/Unbook?_c=de-DE"
BOOK_RESULTS = ("Booked", "UserAlreadyBooked")
UNBOOK_OK = ("UnBooked", "EventNotExists", "UserNotExists")


def _aware(dt) -> _dt.datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(_dt.timezone.utc)
    return dt.replace(tzinfo=_dt.timezone.utc)


def pick_instance(instances, now_utc, min_days_ahead):
    future = [
        inst
        for inst in instances
        if not inst.has_layout
        and inst.start_date is not None
        and _aware(inst.start_date) > now_utc
        and inst.booking_info.booking_opens_on is not None
        and _aware(inst.booking_info.booking_opens_on) <= now_utc
        and not inst.is_participant
    ]
    future.sort(key=lambda inst: (_aware(inst.start_date), -inst.available_places))
    far = [
        inst
        for inst in future
        if _aware(inst.start_date) >= now_utc + _dt.timedelta(days=min_days_ahead)
    ]
    return (far or future)[0] if (far or future) else None


def build_parser():
    parser = argparse.ArgumentParser(
        prog="live_roundtrip",
        description="Live Book->Unbook probe; never run without explicit approval "
        "(requires --confirm)",
    )
    parser.add_argument("--config", default="config.yaml", help="config.yaml path")
    parser.add_argument("--account", default=None, help="account name (default: first available)")
    parser.add_argument("--days-ahead", type=int, default=3, help="min days to class start")
    parser.add_argument("--class-id", default=None, help="pin a specific class id instead of auto-picking")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="SEND real Book and Unbook; omit for plan-only mode",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"live_roundtrip: config error: {exc}", file=sys.stderr)
        return 2

    if args.account is not None:
        account = next((a for a in cfg.accounts if a.name == args.account), None)
        if account is None:
            print(f"live_roundtrip: no account named {args.account!r}", file=sys.stderr)
            return 2
    else:
        account = next((a for a in cfg.accounts if a.available), None)
    if account is None or not account.available:
        print(
            "live_roundtrip: no available account (set MEDI_CREDS_<NAME>_NAME/_PW)",
            file=sys.stderr,
        )
        return 2

    creds = Credentials.from_env(account.name)
    if creds is None:
        print(
            f"live_roundtrip: credentials missing for account {account.name}",
            file=sys.stderr,
        )
        return 2

    now_utc = _dt.datetime.now(_dt.timezone.utc)

    schedule = ScheduleClient()
    try:
        instances = schedule.fetch(
            cfg.facility.id, now_utc.date(), now_utc.date() + _dt.timedelta(days=14)
        )
    finally:
        schedule.close()

    chosen = None
    if args.class_id:
        chosen = next((i for i in instances if i.id == args.class_id), None)
        if chosen is None:
            print(
                f"live_roundtrip: no instance with id {args.class_id!r} in the "
                "next 14 days",
                file=sys.stderr,
            )
            return 2
    else:
        chosen = pick_instance(instances, now_utc, args.days_ahead)
    if chosen is None:
        print(
            "live_roundtrip: no hasLayout:false class with an open booking "
            "window found in the next 14 days"
        )
        return 1

    days_to_start = (_aware(chosen.start_date) - now_utc).total_seconds() / 86400.0
    if days_to_start < args.days_ahead:
        print(
            "note: closest open class starts sooner than "
            f"{args.days_ahead} day(s); using it anyway (round trip unbooks "
            "immediately)",
            file=sys.stderr,
        )

    print(f"facility: {cfg.facility.name} (id={cfg.facility.id})")
    print(f"account:  {account.name}")
    print(f"class:    {chosen.name} ({chosen.id})")
    print(f"  start:          {chosen.start_date.isoformat()}")
    print(f"  partitionDate:  {chosen.partition_date}")
    print(f"  hasLayout:      {chosen.has_layout}")
    print(f"  availablePlaces:{chosen.available_places}")
    print(f"  bookingOpensOn: {chosen.booking_info.booking_opens_on.isoformat()}")

    auth = AuthClient(account.name, credentials=creds)
    try:
        auth.authenticate()
    except AuthenticationError as exc:
        print(f"live_roundtrip: authentication failed: {exc}", file=sys.stderr)
        return 2

    executor = BurstExecutor(auth)
    payload = executor.build_payload(chosen.id, chosen.partition_date)
    print(f"payload:  {payload}")
    print(f"will:     Book  {chosen.id}", flush=True)

    if not args.confirm:
        print("plan only: re-run with --confirm (after explicit approval) to "
              "send the real Book/Unbook")
        auth.close()
        return 0

    print("CONFIRMED: sending live bookings against the real API")
    result = executor.book(chosen.id, chosen.partition_date)
    print(f"Book   -> {result}")

    if result not in BOOK_RESULTS:
        print(
            f"live_roundtrip: booking did not succeed ({result}); skipping Unbook",
            file=sys.stderr,
        )
        auth.close()
        return 1

    resp = auth.call(
        "POST",
        UNBOOK_URL,
        json={
            "partitionDate": chosen.partition_date,
            "userId": auth.user_id,
            "classId": chosen.id,
        },
        headers=dict(BOOK_HEADERS),
    )
    try:
        unbook_result = resp.json().get("result", "<missing>")
    except Exception:
        unbook_result = f"<non-JSON http {resp.status_code}>"
    print(f"Unbook -> {unbook_result}")
    auth.close()

    if unbook_result not in UNBOOK_OK:
        print(
            f"live_roundtrip: unbook returned unexpected result {unbook_result!r}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())