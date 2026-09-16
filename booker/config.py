import os
import re
from dataclasses import dataclass, field

import yaml

_TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")

MEDITERANA_ID = "0273e18b-52bf-404e-afa6-8bfb2eeccbad"
MEDITERANA_NAME = "mediterana"


class ConfigError(ValueError):
    pass


@dataclass
class Target:
    name: str
    day: int
    time: str
    priority: int = 1
    event_type_id: str | None = None


@dataclass
class Account:
    name: str
    timezone: str = "UTC"
    targets: list[Target] = field(default_factory=list)
    available: bool = True
    skip_reason: str | None = None


@dataclass
class Facility:
    id: str
    name: str = ""


@dataclass
class Config:
    facility: Facility = field(default_factory=lambda: Facility(id=MEDITERANA_ID, name=MEDITERANA_NAME))
    accounts: list[Account] = field(default_factory=list)


def load_config(path: str) -> Config:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: file not found") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    return parse_config(raw, source=path)


def parse_config(raw, source: str = "<config>") -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: must be a YAML mapping")

    accounts_raw = raw.get("accounts", [])
    if not isinstance(accounts_raw, list):
        raise ConfigError(f"{source}: 'accounts' must be a list")

    accounts = []
    for i, acc_raw in enumerate(accounts_raw):
        path = f"{source}: accounts[{i}]"
        if not isinstance(acc_raw, dict):
            raise ConfigError(f"{path}: must be a mapping")

        name = acc_raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{path}.name: is required and must not be empty")
        name = name.strip()

        timezone = acc_raw.get("timezone", "UTC")
        if not isinstance(timezone, str) or not timezone.strip():
            raise ConfigError(f"{path}.timezone: must be a non-empty string")

        targets = []
        targets_raw = acc_raw.get("targets", [])
        if not isinstance(targets_raw, list):
            raise ConfigError(f"{path}.targets: must be a list")
        for j, trg_raw in enumerate(targets_raw):
            tpath = f"{path}.targets[{j}]"
            if not isinstance(trg_raw, dict):
                raise ConfigError(f"{tpath}: must be a mapping")

            trg_name = trg_raw.get("name")
            if not isinstance(trg_name, str) or not trg_name.strip():
                raise ConfigError(f"{tpath}.name: is required and must not be empty")

            day = trg_raw.get("day")
            if isinstance(day, bool) or not isinstance(day, int):
                raise ConfigError(f"{tpath}.day: must be an integer 1..7, got {day!r}")
            if not 1 <= day <= 7:
                raise ConfigError(f"{tpath}.day: must be in 1..7, got {day}")

            time = trg_raw.get("time")
            if not isinstance(time, str) or not _TIME_RE.fullmatch(time):
                raise ConfigError(
                    f"{tpath}.time: must match HH:MM (24h), got {time!r}"
                )

            priority = trg_raw.get("priority", 1)
            if isinstance(priority, bool) or not isinstance(priority, int):
                raise ConfigError(f"{tpath}.priority: must be an integer, got {priority!r}")

            event_type_id = trg_raw.get("event_type_id")
            if event_type_id is not None and not isinstance(event_type_id, str):
                raise ConfigError(f"{tpath}.event_type_id: must be a string")

            targets.append(
                Target(
                    name=trg_name.strip(),
                    day=day,
                    time=time,
                    priority=priority,
                    event_type_id=event_type_id,
                )
            )

        seen = set()
        for j, trg in enumerate(targets):
            triple = (trg.name, trg.day, trg.time)
            if triple in seen:
                raise ConfigError(
                    f"{path}.targets[{j}]: duplicate target "
                    f"(name={trg.name!r}, day={trg.day}, time={trg.time!r})"
                )
            seen.add(triple)

        env_prefix = f"MEDI_CREDS_{name.upper()}"
        cred_name = os.environ.get(f"{env_prefix}_NAME")
        cred_pw = os.environ.get(f"{env_prefix}_PW")
        available = bool(cred_name) and bool(cred_pw)
        accounts.append(
            Account(
                name=name,
                timezone=timezone.strip(),
                targets=targets,
                available=available,
                skip_reason=None if available else "missing credentials",
            )
        )

    if not accounts:
        raise ConfigError(f"{source}: no accounts configured")

    return Config(accounts=accounts)