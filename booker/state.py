from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile


def _key(account: str, partition_date: int, class_id: str) -> str:
    return f"{account}::{int(partition_date)}::{class_id}"


class BookedHistory:
    def __init__(self, path: str = "booked_history.json") -> None:
        self.path = path
        self._entries: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for key, val in raw.items():
            if isinstance(key, str) and isinstance(val, dict):
                booked_at = val.get("booked_at")
                if isinstance(booked_at, str):
                    self._entries[key] = booked_at

    def has(self, account: str, partition_date: int, class_id: str) -> bool:
        return _key(account, partition_date, class_id) in self._entries

    def add(self, account: str, partition_date: int, class_id: str) -> None:
        self._entries[_key(account, partition_date, class_id)] = (
            _dt.datetime.now(_dt.timezone.utc).isoformat()
        )

    def remove(self, account: str, partition_date: int, class_id: str) -> None:
        self._entries.pop(_key(account, partition_date, class_id), None)

    def all(self) -> dict[str, dict[str, str]]:
        return {k: {"booked_at": v} for k, v in self._entries.items()}

    def prune(self, today_int: int | None = None) -> int:
        if callable(today_int):
            today_int = today_int()
        if today_int is None:
            today_int = int(_dt.date.today().strftime("%Y%m%d"))
        removed = 0
        for key in list(self._entries):
            parts = key.split("::")
            if len(parts) != 3:
                continue
            try:
                partition = int(parts[1])
            except ValueError:
                continue
            if partition < today_int:
                del self._entries[key]
                removed += 1
        return removed

    def save(self) -> None:
        data = {k: {"booked_at": ts} for k, ts in self._entries.items()}
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(
            dir=directory, prefix=".booked_history.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise