"""Text cleaning used for matching. Each function is small and has a test (DESIGN section 6)."""

import html
import re

STREET_TOKENS = {
    "rd": "road",
    "dr": "drive",
    "ave": "avenue",
    "blvd": "boulevard",
    "ln": "lane",
    "pk": "pike",
    "pke": "pike",
    "hwy": "highway",
    "ct": "court",
    "pl": "place",
    "pkwy": "parkway",
    "cir": "circle",
}
DIRECTIONS = {
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "nw": "northwest",
    "ne": "northeast",
    "sw": "southwest",
    "se": "southeast",
}
DIRECTION_WORDS = set(DIRECTIONS) | set(DIRECTIONS.values())
NAME_FILLER = {
    "bellhaven",
    "the",
    "of",
    "at",
    "senior",
    "living",
    "care",
    "center",
    "centre",
    "community",
    "communities",
    "home",
}
CARE_MAP = {
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
    "Memory Support": "Memory Care",
}
PO_BOX = re.compile(r"^\s*p\.?\s*o\.?\s*box", re.IGNORECASE)
PUNCT = re.compile(r"[^\w\s]")


def clean_text(s: str) -> str:
    """Unescape HTML entities, turn non-breaking spaces into spaces, collapse runs of spaces."""
    return " ".join(html.unescape(s).replace("\xa0", " ").split())


def _words(s: str) -> list[str]:
    return PUNCT.sub(" ", s.lower()).split()


def norm_street(s: str) -> str:
    """'45 St Lawrence Dr' -> '45 saint lawrence drive'; 'St' at the end (or before a direction) is street."""
    words = _words(s)
    out = []
    for i, w in enumerate(words):
        if w == "st":
            rest = words[i + 1 :]
            out.append("street" if all(r in DIRECTION_WORDS for r in rest) else "saint")
        else:
            out.append(STREET_TOKENS.get(w) or DIRECTIONS.get(w) or w)
    return " ".join(out)


def streets_equivalent(a: str, b: str) -> bool:
    return norm_street(a) == norm_street(b)


def is_po_box(s: str) -> bool:
    return bool(PO_BOX.match(s or ""))


def norm_phone(s: str) -> str:
    return re.sub(r"\D", "", s or "")[-10:]


def norm_zip(s: str) -> str:
    return re.sub(r"\D", "", s or "")[:5]


def name_tokens(s: str) -> frozenset[str]:
    return frozenset(w for w in _words((s or "").replace("&", " and ")) if w not in NAME_FILLER)


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def same_person(a: str, b: str) -> bool:
    x, y = " ".join((a or "").lower().split()), " ".join((b or "").lower().split())
    return bool(x) and x == y


def map_care(label: str) -> str:
    return CARE_MAP.get(label, label)


def full_address(street: str, city: str, state: str, zip: str) -> tuple[str, str, str, str]:
    return (
        norm_street(street),
        " ".join((city or "").lower().split()),
        (state or "").strip().upper(),
        norm_zip(zip),
    )
