"""Hardening from the testing pass: what a reviewer sees after a refused action, host and origin checks,
the signed form token, response headers, the CRM key kept out of pages, and the decision file's mode."""

import os
import re
import stat

from conftest import CFG_AT, DAY, ready
from fastapi.testclient import TestClient

from bellhaven_sync.app import create_app


def test_second_approve_lands_on_the_card_saying_it_was_already_written(env):
    c = env.pending("rename", account="6K5BGN")
    assert env.post(c, "approve").status_code == 303
    r = env.post(c, "approve")
    assert r.status_code == 303 and r.headers["location"].startswith(f"/?card={c.id}")
    page = env.client.get(r.headers["location"]).text
    assert "Already decided: Approved and written. Nothing more was written." in page
    assert len(env.fake.writes) == 1


def test_a_changed_card_reopens_with_its_current_version(env):
    c = env.any_pending()
    r = env.client.post(
        f"/cards/{c.id}/approve", data={"csrf": env.token, "version": "stale"}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"].startswith(f"/?card={c.id}")
    assert "changed since you opened it, so nothing was written" in env.client.get(r.headers["location"]).text
    assert env.fake.writes == []


def _app(tmp_path, **kw):
    l, fake = ready(tmp_path)
    return TestClient(
        create_app(CFG_AT(tmp_path), l, lambda: fake, lambda: DAY, **kw), base_url="http://100.64.0.1:8771"
    ), fake


def test_unknown_host_is_refused(tmp_path):  # DNS rebinding: a page on another name must not reach the app
    client, _ = _app(tmp_path, allowed_hosts=("100.64.0.1",))
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"host": "attacker.example:8771"}).status_code == 400


def test_post_from_another_origin_is_refused(tmp_path):
    client, fake = _app(tmp_path, allowed_hosts=("100.64.0.1",))
    page = client.get("/").text
    card_id = re.search(r"/cards/([0-9a-f]+)/approve", page).group(1)
    form = {
        "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1),
        "version": re.search(r'name="version" value="([^"]+)"', page).group(1),
    }
    assert (
        client.post(
            f"/cards/{card_id}/reject", data=form, headers={"origin": "http://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/cards/{card_id}/reject", data=form, headers={"referer": "http://evil.example/x"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/cards/{card_id}/reject",
            data=form,
            headers={"origin": "http://100.64.0.1:8771"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert fake.writes == []


def test_cookie_value_alone_is_not_a_valid_token(env):  # the token is signed by the running app
    c = env.any_pending()
    env.client.cookies.set("bh_csrf", "attacker-chosen")
    r = env.client.post(f"/cards/{c.id}/approve", data={"csrf": "attacker-chosen", "version": c.version})
    assert r.status_code == 403 and env.fake.writes == []


def test_pages_carry_protective_headers(env):
    h = env.client.get("/").headers
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "same-origin" and h["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "default-src 'self'" in h["content-security-policy"]


def test_crm_key_never_appears_in_a_page(env):
    for path in (
        "/",
        "/locations",
        "/history",
        f"/?card={env.pending('chow', slug='bellhaven-of-tiffin').id}",
    ):
        assert "test-token" not in env.client.get(path).text, path
    page = env.client.get(f"/?card={env.pending('chow', slug='bellhaven-of-tiffin').id}").text
    link = re.search(r'href="(/crm/[^"]+)"', page).group(1)
    r = env.client.get(link, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(
        "http://crm.invalid/crm/test-token/accounts/"
    )


def test_decision_file_is_private(tmp_path):
    l, _ = ready(tmp_path)
    assert stat.S_IMODE(os.stat(l.path).st_mode) == 0o600
