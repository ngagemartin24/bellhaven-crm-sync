"""Shared data shapes."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

Account = dict[str, Any]
Contact = dict[str, Any]


@dataclass(frozen=True)
class Location:
    """One community as the website lists it."""

    slug: str
    name: str
    street: str
    city: str
    state: str
    zip: str
    offerings: tuple[str, ...]
    administrator: str
    phone: str
    url: str


@dataclass(frozen=True)
class Signals:
    """What one website location and one CRM account have in common (DESIGN 7.3)."""

    street: bool
    admin: bool
    phone: bool
    zip: bool
    city: bool
    name: float
    po_box: bool


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Change:
    """One decidable unit (DESIGN 8.1). Its key never includes notes, evidence, scores or dates (10.2)."""

    kind: str  # set_field | retire_duplicate | set_chow | create_account | attention
    record: str
    field: str
    value: Any
    location_slug: str | None

    @property
    def key(self) -> str:
        return digest([self.kind, self.record, self.field, self.value])


@dataclass(frozen=True)
class Step:
    """One CRM call. A body value {"$created": k} means the id created by step k."""

    n: int
    method: str
    path: str
    body: dict
    expect: dict
    note_append: str | None
    change_keys: tuple[str, ...]


@dataclass(frozen=True)
class Card:
    change_set: str
    kind: str
    section: str
    title: str
    summary: str
    location_slug: str | None
    account_ids: tuple[str, ...]
    band: str | None
    changes: tuple[Change, ...]
    steps: tuple[Step, ...]
    evidence: dict
    alt_group: str | None

    @property
    def change_keys(self) -> frozenset[str]:
        return frozenset(c.key for c in self.changes)


@dataclass(frozen=True)
class Verdict:
    """How the matcher classified one website location or one leftover operator account (DESIGN 13)."""

    subject: str
    name: str
    verdict: str
    band: str | None
    account_ids: tuple[str, ...]
    change_sets: tuple[str, ...]
    reason: str
