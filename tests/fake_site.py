"""Serves the saved copy of the website (tests/fixtures/snapshot/site) the way scrape() fetches it."""

import re
from collections.abc import Callable
from pathlib import Path

from bellhaven_sync.scrape import FetchError

LAST_DIRECTORY_PAGE = 3  # the real site repeats its last page for any later page number


def fake_fetcher(root: Path, fail=frozenset(), replace: dict[str, str] | None = None) -> Callable[[str], str]:
    replace = replace or {}

    def fetch(path: str) -> str:
        page = re.fullmatch(r"/communities\?page=(\d+)", path)
        if page and int(page.group(1)) > LAST_DIRECTORY_PAGE:
            path = f"/communities?page={LAST_DIRECTORY_PAGE}"
            page = re.fullmatch(r"/communities\?page=(\d+)", path)
        if path in fail:
            raise FetchError(path)
        if path in replace:
            return replace[path]
        if path == "/":
            return (root / "home.html").read_text()
        if path == "/about":
            return (root / "about.html").read_text()
        if page:
            return (root / f"communities-page-{page.group(1)}.html").read_text()
        community = re.fullmatch(r"/communities/([a-z0-9-]+)", path)
        if community and (root / "communities" / f"{community.group(1)}.html").exists():
            return (root / "communities" / f"{community.group(1)}.html").read_text()
        raise FetchError(path)

    return fetch
