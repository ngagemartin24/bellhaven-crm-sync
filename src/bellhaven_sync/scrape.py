"""Reads every Bellhaven location from the website and reports whether the read was complete (DESIGN 5.1)."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from .models import Location
from .normalize import clean_text

USER_AGENT = "bellhaven-crm-sync/1.0 (CRM upkeep; read-only)"
TIMEOUT_S = 20
BACKOFF_S = (1, 2)  # 3 tries: wait 1 s, then 2 s
GAP_S = 0.2
LINK = re.compile(r"^/communities/([a-z0-9-]+)/?$")
CITY_LINE = re.compile(r"^(.+?),\s*([A-Za-z]{2})\s+(\d{5})(?:-\d{4})?$")
STATED = re.compile(r"(\d+)\s+communities")


class FetchError(Exception):
    """A page could not be read after every try."""


@dataclass(frozen=True)
class ScrapeReport:
    locations: list[Location]
    stated: int | None
    found: int
    pages_failed: list[str]
    invalid: list[str]
    directory_ended: bool
    complete: bool
    reason: str


def community_links(html: str) -> list[str]:
    """Community slugs linked from a page, in page order, without repeats."""
    slugs: list[str] = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        m = LINK.match(a["href"])
        if m and m.group(1) not in slugs:
            slugs.append(m.group(1))
    return slugs


def stated_count(home_html: str) -> int | None:
    m = STATED.search(BeautifulSoup(home_html, "html.parser").get_text(" "))
    return int(m.group(1)) if m else None


def _detail(soup: BeautifulSoup, label: str):
    for dt in soup.select("dl.detail dt"):
        if clean_text(dt.get_text()) == label:
            return dt.find_next_sibling("dd")
    return None


def parse_community(html: str, slug: str, url: str) -> Location | None:
    """One community page; None when name, street, city, state or ZIP is missing."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    name = clean_text(h1.get_text()) if h1 else ""
    address = _detail(soup, "Address")
    lines = [clean_text(x) for x in address.get_text("\n").split("\n")] if address else []
    lines = [x for x in lines if x]
    street = lines[0] if lines else ""
    city_line = CITY_LINE.match(lines[1]) if len(lines) > 1 else None
    if not (name and street and city_line):
        return None
    city, state, zip_ = city_line.groups()
    care = _detail(soup, "Care Offerings")
    admin = _detail(soup, "Administrator")
    phone = _detail(soup, "Phone")
    return Location(
        slug=slug,
        name=name,
        street=street,
        city=clean_text(city),
        state=state.upper(),
        zip=zip_,
        offerings=tuple(clean_text(s.get_text()) for s in care.select("span.badge")) if care else (),
        administrator=clean_text(admin.get_text()) if admin else "",
        phone=clean_text(phone.get_text()) if phone else "",
        url=url,
    )


def http_fetcher(base_url: str) -> Callable[[str], str]:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    def fetch(path: str) -> str:
        for attempt in range(len(BACKOFF_S) + 1):
            try:
                r = session.get(base_url.rstrip("/") + path, timeout=TIMEOUT_S)
                r.raise_for_status()
                time.sleep(GAP_S)
                return r.text
            except requests.RequestException:
                if attempt == len(BACKOFF_S):
                    raise FetchError(path) from None
                time.sleep(BACKOFF_S[attempt])
        raise FetchError(path)

    return fetch


def scrape(fetch: Callable[[str], str], base_url: str, max_pages: int = 20) -> ScrapeReport:
    failed: list[str] = []
    slugs: list[str] = []

    def get(path: str) -> str | None:
        try:
            return fetch(path)
        except FetchError:
            failed.append(path)
            return None

    def add(html: str | None) -> None:
        for s in community_links(html or ""):
            if s not in slugs:
                slugs.append(s)

    home = get("/")
    stated = stated_count(home) if home else None
    add(home)
    add(get("/about"))

    ended, previous = False, None
    for n in range(1, max_pages + 1):
        html = get(f"/communities?page={n}")
        if html is None:
            break
        links = set(community_links(html))
        if previous is not None and links == previous:
            ended = True
            break
        add(html)
        previous = links

    locations: list[Location] = []
    invalid: list[str] = []
    base = base_url.rstrip("/")
    for slug in slugs:
        html = get(f"/communities/{slug}")
        if html is None:
            continue
        loc = parse_community(html, slug, f"{base}/communities/{slug}")
        if loc is None:
            invalid.append(slug)
        else:
            locations.append(loc)

    problems = []
    if failed:
        problems.append(f"{len(failed)} page(s) could not be read")
    if not ended:
        problems.append(f"the directory did not end within {max_pages} pages")
    if invalid:
        problems.append(f"{len(invalid)} community page(s) missing name or address")
    if stated is None:
        problems.append("the home page states no community count")
    elif len(locations) != stated:
        problems.append(f"found {len(locations)}, stated {stated}")
    return ScrapeReport(
        locations=locations,
        stated=stated,
        found=len(locations),
        pages_failed=failed,
        invalid=invalid,
        directory_ended=ended,
        complete=not problems,
        reason="; ".join(problems) or "complete",
    )
