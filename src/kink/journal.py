"""Append-only decision journal.

Every scan writes one record per candidate -- including the ones that were
refused, and why. A refusal is as much a decision as a fill, and the journal
is the artifact that makes the agent's behaviour auditable after the fact.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

JOURNAL_DIR = pathlib.Path(__file__).resolve().parents[2] / "journal"


def _path(kind: str) -> pathlib.Path:
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    return JOURNAL_DIR / f"{kind}-{day}.jsonl"


def record(kind: str, payload: dict[str, Any]) -> None:
    entry = {"ts": dt.datetime.now(dt.UTC).isoformat(), **payload}
    with _path(kind).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def read(kind: str, day: str | None = None) -> list[dict]:
    day = day or dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    path = JOURNAL_DIR / f"{kind}-{day}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
