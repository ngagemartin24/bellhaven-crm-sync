"""One real browser against the review app over the in-memory CRM: approve, reject, re-run, history.

Run with `uv run pytest -m e2e`; needs `uv run playwright install chromium` once.
"""

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn
from conftest import CFG_AT, DAY, FETCH, PARENT, fresh, full_id, only
from playwright.sync_api import expect, sync_playwright

from bellhaven_sync.app import create_app
from bellhaven_sync.pipeline import run_once

SHOTS = Path(__file__).resolve().parents[2] / "docs" / "screenshots"


@dataclass
class LiveApp:
    url: str
    ledger: object
    crm: object

    def run_once(self):
        return run_once(FETCH, self.crm, self.ledger, PARENT, DAY)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_app(tmp_path):
    ledger, crm = fresh(tmp_path)
    run_once(FETCH, crm, ledger, PARENT, DAY)
    app = create_app(CFG_AT(tmp_path), ledger, lambda: crm, lambda: DAY)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    yield LiveApp(f"http://127.0.0.1:{port}", ledger, crm)
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        yield pg
        browser.close()


@pytest.mark.e2e
def test_approve_reject_rerun(page, live_app):
    rename = only(
        live_app.ledger.cards("pending"), kind="rename", slug="bellhaven-rehabilitation-nursing-of-grove-city"
    )
    look_alike = only(live_app.ledger.cards("pending"), kind="possible_match")
    tiffin = only(live_app.ledger.cards("pending"), kind="chow", slug="bellhaven-of-tiffin")

    page.goto(f"{live_app.url}/?card={tiffin.id}")
    expect(page.locator("#item-title")).to_have_text("Bellhaven of Tiffin")
    expect(page.get_by_text("Keep the old account, create a new one")).to_be_visible()
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / "queue-change-of-ownership.png")

    # on a desktop window the queue fills the window exactly: the panes scroll, the page never does
    # (2026-11-08: screen-reader-only labels positioned against the page stretched it 500px)
    for card in (tiffin, only(live_app.ledger.cards("pending"), kind="duplicates", slug="bellhaven-of-kettering")):
        page.goto(f"{live_app.url}/?card={card.id}#card-{card.id}")
        size = page.evaluate("() => [document.scrollingElement.scrollHeight, innerHeight, scrollY]")
        assert size[0] == size[1] and size[2] == 0, f"page taller than the window: {size}"

    # approve the Grove City rename: one write lands in the CRM
    page.locator(f"#card-{rename.id} a").click()
    expect(page.locator("#item-title")).to_have_text(rename.title)
    page.get_by_role("button", name="Approve").click()
    expect(page.get_by_text("Approved: 1 of 1 writes done")).to_be_visible()
    assert (
        live_app.crm.accounts[full_id("6K5BGN")]["name"] == "Bellhaven Rehabilitation & Nursing of Grove City"
    )

    # reject the Union Square look-alike: nothing is written
    writes_before = len(live_app.crm.writes)
    page.locator(f"#card-{look_alike.id} a").click()
    page.get_by_role("button", name="Reject").click()
    expect(page.get_by_text("Rejected")).to_be_visible()
    assert len(live_app.crm.writes) == writes_before

    # the next daily run proposes neither again
    assert live_app.run_once().counts["new"] == 0
    page.goto(live_app.url)
    expect(page.locator(f"#card-{rename.id}")).to_have_count(0)
    expect(page.locator(f"#card-{look_alike.id}")).to_have_count(0)

    page.goto(f"{live_app.url}/history")
    expect(page.get_by_text("Approved").first).to_be_visible()
    expect(page.get_by_text("Rejected").first).to_be_visible()
    page.screenshot(path=SHOTS / "history.png")

    page.goto(f"{live_app.url}/locations")
    expect(page.get_by_role("heading", name="Locations")).to_be_visible()
    page.screenshot(path=SHOTS / "locations.png", full_page=True)
