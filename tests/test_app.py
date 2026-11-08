import html

import pytest
from conftest import CFG_AT, DAY, SNAP, ledger_with_card_in
from fake_crm import FakeCrm
from fastapi.testclient import TestClient

from bellhaven_sync.app import AppLockedError, create_app
from bellhaven_sync.ledger import Ledger
from bellhaven_sync.rules import SECTIONS


def test_queue_shows_sections_in_order(env):
    html = env.client.get("/").text
    assert [h for h in SECTIONS if f"<span>{h}</span>" in html] == [
        s for s in SECTIONS if any(c.section == s for c in env.l.cards("pending"))
    ]
    assert "42 proposals" in html and "35 of 35 locations" in html


def test_card_shows_evidence_and_writes(env):
    html = env.client.get("/?location=bellhaven-of-tiffin").text
    assert "$84,000" in html and "$12,400" in html and "POST /accounts" in html
    assert "chow_current_account" in html and "Keep the old account, create a new one" in html


def test_approve_then_already_decided(env):
    c = env.pending("rename", account="6K5BGN")
    r = env.post(c, "approve", follow_redirects=True)
    assert "Approved: 1 of 1 writes done" in r.text
    r2 = env.post(c, "approve")
    assert r2.status_code == 409 and len(env.fake.writes) == 1


def test_reject_with_note_lands_in_history(env):
    c = env.pending("possible_match")
    assert env.post(c, "reject", data={"note": "different street and administrator"}).status_code == 303
    assert env.l.card(c.id).status == "rejected"
    assert "different street and administrator" in env.client.get("/history").text


def test_interrupted_apply_offers_retry(tmp_path):
    l = ledger_with_card_in("applying", tmp_path)
    fake = FakeCrm.from_snapshot(SNAP)
    client = TestClient(create_app(CFG_AT(tmp_path), l, lambda: fake, lambda: DAY))
    html = client.get("/").text
    assert "Interrupted; Retry resumes" in html and ">Retry<" in html
    c = l.cards("failed")[0]
    r = client.post(
        f"/cards/{c.id}/retry",
        data={"csrf": client.cookies["bh_csrf"], "version": c.version},
        follow_redirects=True,
    )
    assert r.status_code == 200 and l.card(c.id).status == "done"  # consistency E5


def test_history_shows_before_values(env):
    c = env.pending("details", account="NRLNME")
    env.post(c, "approve")
    assert "(517) 409-8156" in env.client.get("/history").text


def test_second_app_refuses(tmp_path):
    create_app(CFG_AT(tmp_path), Ledger(tmp_path / "d.db"), lambda: None, lambda: DAY)
    with pytest.raises(AppLockedError):
        create_app(CFG_AT(tmp_path), Ledger(tmp_path / "d.db"), lambda: None, lambda: DAY)


def test_every_card_renders(env):  # no proposal kind breaks the page
    for c in env.l.cards("pending"):
        r = env.client.get(f"/?card={c.id}")
        assert r.status_code == 200 and html.escape(c.title, quote=False) in r.text


def test_crm_link_carries_the_account(env):
    c = env.pending("details", account="NRLNME")
    html = env.client.get(f"/?card={c.id}").text
    assert f"http://crm.invalid/crm/test-token/accounts/{c.account_ids[0]}" in html


@pytest.mark.parametrize("path", ["/", "/locations", "/history"])
def test_every_page_declares_its_tab_icon(env, path):
    # Without one, the browser asks for /favicon.ico, gets a 404 and logs a console error (Lighthouse).
    page = env.client.get(path).text
    assert '<link rel="icon"' in page


def test_queue_link_name_contains_its_visible_text(env):
    # WCAG 2.5.3 Label in Name: on narrow screens the rail shows "Queue" and the count, so the
    # accessible name must contain both, in that order.
    page = env.client.get("/").text
    link = page[page.index('<a href="/" title="Review queue') : page.index("</a>", page.index('<a href="/" title="Review queue'))]
    assert 'aria-label="Queue, 42 to decide"' in link
    assert link.index('class="rail-label">Queue<') < link.index('class="count num">42<')
