from pathlib import Path

from fake_site import fake_fetcher

from bellhaven_sync.scrape import scrape

SITE = Path(__file__).parent / "fixtures" / "snapshot" / "site"
BASE = "https://example.test"
PAGE3_MINUS_ONE = (
    (SITE / "communities-page-3.html")
    .read_text()
    .replace('<a href="/communities/bellhaven-of-chesterton">', "<a>")
)
HOME_SAYS_34 = (SITE / "home.html").read_text().replace("serve 35 communities", "serve 34 communities")


def by_slug(report, slug):
    return next(loc for loc in report.locations if loc.slug == slug)


def test_finds_all_35_including_findlay():
    r = scrape(fake_fetcher(SITE), BASE)
    assert r.found == 35 and r.stated == 35 and r.complete and r.directory_ended
    assert "bellhaven-meadows-of-findlay" in {loc.slug for loc in r.locations}


def test_parses_tiffin():
    loc = by_slug(scrape(fake_fetcher(SITE), BASE), "bellhaven-of-tiffin")
    assert (loc.name, loc.street, loc.city, loc.state, loc.zip) == (
        "Bellhaven of Tiffin",
        "45 St Lawrence Dr",
        "Tiffin",
        "OH",
        "44883",
    )
    assert loc.offerings == ("Short-Term Rehabilitation & Nursing",)
    assert loc.administrator == "Walt Osei" and loc.phone == "(260) 589-2589"
    assert loc.url == f"{BASE}/communities/bellhaven-of-tiffin"


def test_failed_page_is_incomplete():
    r = scrape(fake_fetcher(SITE, fail={"/communities/bellhaven-of-tiffin"}), BASE)
    assert not r.complete and r.pages_failed == ["/communities/bellhaven-of-tiffin"]


def test_invalid_page_is_incomplete():
    r = scrape(
        fake_fetcher(SITE, replace={"/communities/bellhaven-of-tiffin": "<h1>Bellhaven of Tiffin</h1>"}), BASE
    )
    assert not r.complete and r.invalid == ["bellhaven-of-tiffin"]


def test_short_count_is_incomplete():  # a directory link removed, home still says 35
    r = scrape(fake_fetcher(SITE, replace={"/communities?page=3": PAGE3_MINUS_ONE}), BASE)
    assert r.found == 34 and not r.complete and "stated 35" in r.reason


def test_real_sale_is_complete():  # home says 34 and 34 are found
    r = scrape(fake_fetcher(SITE, replace={"/": HOME_SAYS_34, "/communities?page=3": PAGE3_MINUS_ONE}), BASE)
    assert r.found == 34 and r.stated == 34 and r.complete


def test_directory_that_never_repeats_is_incomplete():
    pages = iter(range(1000))

    def endless(path):
        if path.startswith("/communities?page="):
            return f'<a href="/communities/x-{next(pages)}">x</a>'
        return fake_fetcher(SITE)(path)

    r = scrape(endless, BASE, max_pages=5)
    assert not r.directory_ended and not r.complete
