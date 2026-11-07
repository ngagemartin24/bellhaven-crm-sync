"""Shared data shapes."""

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
