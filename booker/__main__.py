import argparse
import asyncio
import datetime
import logging
import signal
import sys
from zoneinfo import ZoneInfo

from . import __version__
from .book import BurstExecutor
from .config import ConfigError, load_config
from .client import AuthClient, ScheduleClient
from .scheduler import (
    STATUS_ELIGIBLE,
    BurstFire,
    Scheduler,
    format_burst_line,
)
from .state import BookedHistory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="booker",
        description="Automated course-booking daemon for MyWellness "
        f"(booker {__version__}).",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="config.yaml",
        help="path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run discovery + scheduling once (no loop) and never book; "
        "prints the resolved plan and re-verification rows",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single discovery + burst-trigger pass for bursts due "
        "now, then exit (cron-style)",
    )
    return parser


def print_summary(cfg) -> None:
    facility = cfg.facility
    name = facility.name or "<unnamed>"
    print(f"facility: {name} (id={facility.id})")
    for account in cfg.accounts:
        if account.available:
            status = "available"
        else:
            status = f"skipped ({account.skip_reason})"
        print(
            f"account: {account.name} timezone={account.timezone} "
            f"{status}"
        )
        for target in account.targets:
            print(
                f"  target: {target.name} day={target.day} "
                f"time={target.time} priority={target.priority}"
            )


def _today_int_for(accounts) -> int:
    tz = accounts[0].timezone if accounts else "UTC"
    return int(datetime.datetime.now(ZoneInfo(tz)).strftime("%Y%m%d"))


def build_scheduler(cfg, accounts, *, dry_run: bool = False) -> Scheduler:
    account_targets = {}
    account_tz = {}
    for account in accounts:
        account_targets[account.name] = account.targets
        account_tz[account.name] = account.timezone
    scheduler = Scheduler(
        facility_id=cfg.facility.id,
        account_targets=account_targets,
        account_tz=account_tz,
        schedule_client_factory=ScheduleClient,
        auth_client_factory=AuthClient,
    )
    history = BookedHistory()
    history.prune(_today_int_for(accounts))
    if not dry_run:
        history.save()
    scheduler.history = history
    scheduler.on_burst = BurstFire(
        executor_factory=_executor_factory(scheduler, dry_run),
        history=history,
        dry_run=dry_run,
    )
    return scheduler


def _executor_factory(scheduler, dry_run):
    def make(account_name: str) -> BurstExecutor:
        auth = scheduler.auth_client(account_name)
        if auth is None:
            raise RuntimeError(f"no authenticated client for account {account_name}")
        return BurstExecutor(auth, dry_run=dry_run)

    return make


async def run_dry_run(cfg, accounts) -> int:
    scheduler = build_scheduler(cfg, accounts, dry_run=True)
    try:
        await scheduler.authenticate_all()
        plan = await scheduler.discovery_pass(verify=True)
        for burst in plan:
            print(format_burst_line(burst))
        for burst in plan:
            if burst.status == STATUS_ELIGIBLE:
                await scheduler.on_burst(burst.account_name, burst)
    finally:
        scheduler.close()
    return 0


async def run_once(cfg, accounts) -> int:
    scheduler = build_scheduler(cfg, accounts)
    try:
        await scheduler.authenticate_all()
        plan = await scheduler.discovery_pass(verify=True)
        for burst in plan:
            print(format_burst_line(burst))
        now = scheduler.now_fn()
        lead = datetime.timedelta(seconds=scheduler.burst_lead_s)
        due = [
            burst
            for burst in plan
            if burst.status == STATUS_ELIGIBLE
            and burst.trigger_at - lead <= now
        ]
        if due:
            print(f"once: firing {len(due)} burst(s) due now")
            await asyncio.gather(*(scheduler.on_burst(b.account_name, b) for b in due))
        else:
            print("once: no bursts due now")
    finally:
        scheduler.close()
    return 0


async def run_daemon(cfg, accounts) -> int:
    scheduler = build_scheduler(cfg, accounts)
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, main_task.cancel)
    try:
        await scheduler.run_daemon()
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s booker: %(message)s")
    args = build_parser().parse_args(argv)

    if args.dry_run and args.once:
        print("booker: --dry-run and --once are mutually exclusive", file=sys.stderr)
        return 2

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    for account in cfg.accounts:
        if not account.available:
            env_prefix = f"MEDI_CREDS_{account.name.upper()}"
            print(
                f"warning: account {account.name}: {env_prefix}_NAME and/or "
                f"{env_prefix}_PW not set; account will be skipped",
                file=sys.stderr,
            )

    print_summary(cfg)

    accounts = [a for a in cfg.accounts if a.available]
    if not accounts:
        print("booker: no available accounts (all missing credentials); nothing to schedule")
        return 0

    if args.dry_run:
        return asyncio.run(run_dry_run(cfg, accounts))
    if args.once:
        return asyncio.run(run_once(cfg, accounts))
    return asyncio.run(run_daemon(cfg, accounts))


if __name__ == "__main__":
    sys.exit(main())