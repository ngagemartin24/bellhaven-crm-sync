"""Accessibility fixes from the WCAG 2.2 audit of 2026-11-08 (testing/out/a11y/REPORT.md, ids A11Y-0nn)."""

import html as htmlmod
import re

import pytest


def test_unknown_page_is_an_html_page(env):  # untested-list: raw JSON 404
    r = env.client.get("/no-such-page")
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
    assert "<h1>Not found</h1>" in r.text and "Back to the review queue" in r.text


def test_message_title_names_the_problem(env):  # A11Y-013
    r = env.client.get("/no-such-page")
    assert "<title>Not found · Bellhaven CRM sync</title>" in r.text


def test_open_proposal_names_the_tab(env):  # A11Y-013
    c = env.pending("rename", account="6K5BGN")
    html = env.client.get(f"/?card={c.id}").text
    assert re.search(
        r"<title>[^<]*" + re.escape(htmlmod.escape(c.title)) + r"[^<]*· Bellhaven CRM sync</title>", html
    )


def test_skip_link_reaches_the_open_proposal(env):  # A11Y-012
    c = env.pending("rename", account="6K5BGN")
    html = env.client.get(f"/?card={c.id}").text
    assert '<a class="skip" href="#proposal">Skip to the open proposal</a>' in html
    assert 'id="proposal"' in html


def test_side_by_side_table_scrolls_by_keyboard_and_says_what_differs(env):  # A11Y-004, A11Y-009
    c = next(c for c in env.l.cards("pending") if c.kind == "duplicates")
    html = env.client.get(f"/?card={c.id}").text
    assert re.search(
        r'<div class="scroller" role="region" aria-label="[^"]+" tabindex="0">\s*<table class="compare">',
        html,
    )
    assert '<span class="visually-hidden">(differs from the website)</span>' in html


@pytest.mark.parametrize("path", ["/locations"])
def test_table_cells_carry_their_column_label(env, path):  # A11Y-005
    html = env.client.get(path).text
    assert 'data-label="Verdict"' in html and 'data-label="Linked CRM account"' in html


def test_history_cells_carry_their_column_label(env):  # A11Y-005
    c = env.pending("rename", account="6K5BGN")
    env.post(c, "approve")
    html = env.client.get("/history").text
    assert 'data-label="Request"' in html and 'data-label="Result"' in html


def test_result_message_takes_focus(env):  # A11Y-014
    c = env.pending("rename", account="6K5BGN")
    html = env.post(c, "approve", follow_redirects=True).text
    assert re.search(r'<p class="flash[^"]*" role="status" tabindex="-1" autofocus>', html)


def test_phone_layout_rules(env):  # A11Y-001/002/003/005/006/007/008/010/011
    css = env.client.get("/static/app.css").text
    assert "--control: #8a8a8a" in css  # field edges at 3.45:1
    assert ".tags { display: flex; flex-wrap: wrap;" in css
    assert ".list { scroll-padding-top: 64px; }" in css
    assert ".entry summary::after" in css
    assert ".table .who { white-space: normal; overflow-wrap: anywhere; }" in css
    phone = css[css.index("@media (max-width: 640px)") :]
    assert "html { scroll-padding-bottom: 72px; }" in phone
    assert ".table thead { position: absolute;" in phone and ".table thead { display: none; }" not in phone
    assert ".table td[data-label]::before { content: attr(data-label);" in phone
    assert "@media (max-width: 640px), (max-height: 700px) {\n  .actions { position: static;" in css


# Lighthouse best-practices and SEO deductions, 2026-11-08 (testing/out/lighthouse)
def test_pages_are_compressed(env):
    r = env.client.get("/locations", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"


def test_static_files_cache_for_a_year_behind_a_versioned_link(env):
    html = env.client.get("/").text
    m = re.search(r'href="/static/app\.css\?v=([0-9a-f]{8})"', html)
    assert m, "the stylesheet link carries a content version"
    for path in ("/static/app.css?v=" + m.group(1), "/static/fonts/Geist-Variable.woff2"):
        assert "max-age=31536000" in env.client.get(path).headers["cache-control"]
    assert env.client.get("/").headers["cache-control"] == "no-store"


def test_pages_describe_themselves(env):
    for path in ("/", "/no-such-page"):
        assert '<meta name="description" content="' in env.client.get(path).text
