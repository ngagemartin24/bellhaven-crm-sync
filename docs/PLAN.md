# Bellhaven CRM sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily pipeline that matches Bellhaven's website locations to CRM accounts and proposes fixes, plus a local review app where only an approval writes to the CRM.

**Architecture:** Pure functions do the thinking (`normalize` → `match` → `propose`), a SQLite ledger remembers every decision by change key, and one module (`apply`) is the only code that writes to the CRM, after re-checking the card against fresh data. The pipeline and the app share the ledger file; the pipeline's CRM client is read-only.

**Tech Stack:** Python 3.13 via uv; requests, BeautifulSoup 4; FastAPI, Jinja2, uvicorn, python-multipart; SQLite (standard library); pytest, Hypothesis, Playwright; ruff.

**Spec:** `docs/DESIGN.md` (v2 + Clarifications). Section numbers below (§8.3) refer to it. Every task's
implementer reads the spec sections its task names.

## Global Constraints

- Python `>=3.13,<3.14` (`.python-version` = `3.13`); every command runs through `uv run`; versions locked in `uv.lock`.
- Runtime dependencies only: `fastapi`, `jinja2`, `uvicorn`, `python-multipart`, `requests`, `beautifulsoup4`. Dev: `pytest`, `hypothesis`, `playwright`, `httpx`, `ruff`.
- The CRM key comes only from `BELLHAVEN_CRM_TOKEN` (or a git-ignored `.env`); never in the repo, the database, a log line or an exception message.
- **No test touches the network.** `tests/conftest.py` has an autouse fixture that makes `requests.Session.request` and `socket.create_connection` raise `RuntimeError("network disabled in tests")`; the Playwright test's loopback server is the only exception (it binds 127.0.0.1).
- **No build step writes to the real CRM.** The only real writes are Gage's approvals in the review app.
- Status values exactly `Active`, `Inactive`, `Needs Review`.
- Note line exactly `[Bellhaven sync YYYY-MM-DD] <what>; <why>` (UTC run date), appended on a new line, never overwriting (§8.9).
- Section names and order exactly: `Change of ownership`, `Duplicates`, `Wrong parent`, `New accounts`, `Gone from the website`, `Possible matches`, `Renames`, `Details`, `Needs attention` (§8.1).
- Operator parent name exactly `Bellhaven Senior Living (Parent Account)`.
- Money fields (`lifetime_revenue`, `outstanding_ar`) are never written by any code path.
- The spec's file table gains two files: `models.py` (shared dataclasses) and `pipeline.py` (`run_once`, used by the CLI, the tests and apply's re-check).

## Review Focus

1. **The real CRM rejects a write body the fake accepted** (an unknown field, a wrong type, a contact's `account_id` not patchable): the card shows `Failed at step n of m: <API message>`, nothing after that step is sent, and Retry is offered. Test added to Task 7.
2. **Money fields arrive as strings, `null` or empty** instead of ints: `needs_chow` treats `"5200"` as 5200 and `None`/`""` as 0. Test added to Task 4.
3. **The browser's back button or a double click re-submits an Approve form:** the second POST gets "already decided" and sends no write. Test added to Task 8.
4. **The app is opened from the laptop over a private network on plain http:** the anti-forgery cookie must not carry `Secure`, or every Approve fails with 403. Test added to Task 8.
5. **Site text with HTML entities, non-breaking spaces or doubled spaces** (`Rehabilitation &amp; Nursing`, `Bellhaven\xa0of Tiffin`): names and streets normalize equal to the plain form. Test added to Task 1.

---

## File structure

```
pyproject.toml  uv.lock  .python-version  .gitignore  .env.example  config.toml
src/bellhaven_sync/
  __init__.py   models.py   config.py   normalize.py   scrape.py   crm.py
  rules.py      match.py    propose.py  ledger.py      pipeline.py apply.py
  verify_lib.py app.py      cli.py      templates/{base,queue,history,_card}.html   static/app.css
scripts/verify.py   scripts/blind_check.py
deploy/crontab      deploy/github-actions-daily.yml
tests/conftest.py   tests/fake_crm.py   tests/fake_site.py
tests/fixtures/snapshot/site/{home,about,communities-page-1..4}.html, site/communities/<slug>.html (35)
tests/fixtures/snapshot/crm-accounts.json   crm-contacts.json
tests/fixtures/expected_end_state.json
tests/test_{normalize,scrape,crm,match,propose,ledger,rerun,guard,apply,end_state,alternatives,app,app_security,cli,verify,blind_check}.py
tests/e2e/test_review_flow.py
README.md  WRITEUP.md  docs/{DESIGN,PLAN,code-tour,cheat-sheet,api-notes}.md
```

Fixture sources (copy, do not edit): `~/.fleet/bellhaven-assessment/data/site-index/*.html` → `site/`,
`data/site-pages/*.html` → `site/communities/`, `data/crm-accounts-initial.json` → `crm-accounts.json`,
`data/crm-contacts-initial.json` → `crm-contacts.json`. Both JSON files keep the API's envelope
`{"data": [...], "page", "page_size", "total"}`.

---

### Task 1: Project scaffold and normalization

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore` (`.env`, `*.db`, `logs/`, `.venv/`, `blind/`), `.env.example`, `config.toml`, `src/bellhaven_sync/__init__.py`, `src/bellhaven_sync/models.py`, `src/bellhaven_sync/config.py`, `src/bellhaven_sync/normalize.py`, `tests/conftest.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Produces (`models.py`):
  - `Location(slug: str, name: str, street: str, city: str, state: str, zip: str, offerings: tuple[str, ...], administrator: str, phone: str, url: str)`, frozen.
  - `Account = dict[str, Any]`, `Contact = dict[str, Any]` (API records as returned).
- Produces (`config.py`): `Config(site_url: str, crm_url: str, crm_browser_url: str, operator_parent_name: str, db_path: Path, token: str | None)`; `load_config(path: Path = Path("config.toml")) -> Config` (token from `BELLHAVEN_CRM_TOKEN`, then `.env`).
- Produces (`normalize.py`): `clean_text(s: str) -> str` (unescape entities, NBSP → space, collapse spaces, strip); `norm_street(s) -> str`; `streets_equivalent(a, b) -> bool`; `is_po_box(s) -> bool`; `norm_phone(s) -> str`; `norm_zip(s) -> str`; `name_tokens(s) -> frozenset[str]`; `jaccard(a: frozenset, b: frozenset) -> float`; `same_person(a, b) -> bool`; `map_care(label: str) -> str`; `full_address(street, city, state, zip) -> tuple[str, str, str, str]`.
- `pyproject.toml`: package `bellhaven_sync` under `src/`, script `bellhaven-sync = "bellhaven_sync.cli:main"`, `[tool.pytest.ini_options] testpaths = ["tests"]`, `markers = ["e2e"]`, `addopts = "-m 'not e2e'"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_street_abbreviations():
    assert norm_street("199 Barks Rd W") == norm_street("199 Barks Road West") == "199 barks road west"
    assert norm_street("4850 NW Sylvania Ave") == "4850 northwest sylvania avenue"
    assert norm_street("2401 Wilmington Pk") == "2401 wilmington pike"

def test_st_is_street_or_saint():
    assert norm_street("45 St Lawrence Dr") == "45 saint lawrence drive"
    assert norm_street("341 Chestnut St") == "341 chestnut street"
    assert norm_street("1250 NW Franklin St") == norm_street("1250 Northwest Franklin Street")

def test_po_box():
    assert is_po_box("PO Box 517") and is_po_box("P.O. Box 9") and is_po_box("p o box 12")
    assert not is_po_box("12 Box Elder Rd")

def test_phone_zip_care():
    assert norm_phone("(260) 589-2589") == norm_phone("+1 260.589.2589") == "2605892589"
    assert norm_zip("45662-1234") == "45662"
    assert map_care("Short-Term Rehabilitation & Nursing") == "Skilled Nursing"
    assert map_care("Memory Support") == "Memory Care"
    assert map_care("Assisted Living") == "Assisted Living"

def test_name_tokens_drop_filler():
    assert name_tokens("Bellhaven Rehabilitation & Nursing of Grove City") == {"rehabilitation", "and", "nursing", "grove", "city"}

def test_entities_and_spaces():   # Review Focus 5
    assert clean_text("Bellhaven\xa0of  Tiffin ") == "Bellhaven of Tiffin"
    assert clean_text("Rehabilitation &amp; Nursing") == "Rehabilitation & Nursing"

@given(st.text())
def test_norm_street_idempotent(s):
    assert norm_street(norm_street(s)) == norm_street(s)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_normalize.py -v`. Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `models.py`, `config.py`, `normalize.py`. Token table exactly §6. `st` → `street` when it is the last token or followed only by direction tokens, else `saint`. `config.toml` holds the five non-secret values from §5.3 (`db_path = "decisions.db"`).
- [ ] **Step 4: Run** `uv run pytest tests/test_normalize.py -v && uv run ruff check .`. Expected: all PASS, ruff clean.
- [ ] **Step 5: Commit** `feat: project scaffold and normalization`.

---

### Task 2: Scraper

**Files:**
- Create: `src/bellhaven_sync/scrape.py`, `tests/fake_site.py`, `tests/fixtures/snapshot/site/**`
- Test: `tests/test_scrape.py`

**Interfaces:**
- Consumes: `Location`, `clean_text`.
- Produces: `ScrapeReport(locations: list[Location], stated: int | None, found: int, pages_failed: list[str], invalid: list[str], directory_ended: bool, complete: bool, reason: str)`; `parse_community(html: str, slug: str, url: str) -> Location | None` (None when name, street, city, state or ZIP is missing); `community_links(html: str) -> list[str]`; `stated_count(home_html: str) -> int | None` (regex `(\d+)\s+communities`, first match); `scrape(fetch: Callable[[str], str], base_url: str, max_pages: int = 20) -> ScrapeReport`; `http_fetcher(base_url: str) -> Callable[[str], str]` (20 s timeout, 3 tries with 1/2/4 s back-off, 0.2 s gap, User-Agent `bellhaven-crm-sync/1.0`; raises `FetchError` after the last try).
- `tests/fake_site.py`: `fake_fetcher(root: Path, fail: set[str] = frozenset(), replace: dict[str, str] | None = None) -> Callable[[str], str]` mapping `/` → `home.html`, `/about` → `about.html`, `/communities?page=N` → `communities-page-N.html` (N > 4 → page 4), `/communities/<slug>` → `communities/<slug>.html`; paths in `fail` raise `FetchError`; `replace` overrides a path's HTML.

Page structure (all 35 pages): `<h1>` name; `<dl class="detail">` with `<dt>` `Address` (`street<br>City, ST 12345`), `Care Offerings` (one `<span class="badge">` each), `Administrator`, `Phone`. The home page states 35; the directory pages say 34 and page 4 repeats page 3.

- [ ] **Step 1: Write the failing tests**

```python
def test_finds_all_35_including_findlay(): 
    r = scrape(fake_fetcher(SITE), BASE)
    assert r.found == 35 and r.stated == 35 and r.complete and r.directory_ended
    assert "bellhaven-meadows-of-findlay" in {l.slug for l in r.locations}

def test_parses_tiffin():
    l = by_slug(scrape(fake_fetcher(SITE), BASE), "bellhaven-of-tiffin")
    assert (l.name, l.street, l.city, l.state, l.zip) == ("Bellhaven of Tiffin", "45 St Lawrence Dr", "Tiffin", "OH", "44883")
    assert l.offerings == ("Short-Term Rehabilitation & Nursing",) and l.administrator == "Walt Osei" and l.phone == "(260) 589-2589"

def test_failed_page_is_incomplete():
    r = scrape(fake_fetcher(SITE, fail={"/communities/bellhaven-of-tiffin"}), BASE)
    assert not r.complete and r.pages_failed == ["/communities/bellhaven-of-tiffin"]

def test_invalid_page_is_incomplete():
    r = scrape(fake_fetcher(SITE, replace={"/communities/bellhaven-of-tiffin": "<h1>Bellhaven of Tiffin</h1>"}), BASE)
    assert not r.complete and r.invalid == ["bellhaven-of-tiffin"]

def test_short_count_is_incomplete():   # a directory link removed, home still says 35
    r = scrape(fake_fetcher(SITE, replace={"/communities?page=3": PAGE3_MINUS_ONE}), BASE)
    assert r.found == 34 and not r.complete and "stated 35" in r.reason

def test_real_sale_is_complete():       # home says 34 and 34 are found
    r = scrape(fake_fetcher(SITE, replace={"/": HOME_SAYS_34, "/communities?page=3": PAGE3_MINUS_ONE}), BASE)
    assert r.found == 34 and r.stated == 34 and r.complete
```

`PAGE3_MINUS_ONE` and `HOME_SAYS_34` are built in the test from the fixture HTML with one `str.replace`.

- [ ] **Step 2: Run** `uv run pytest tests/test_scrape.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `scrape.py` with BeautifulSoup (`html.parser`). Directory: fetch pages 1.. until a page's link set equals the previous page's (`directory_ended=True`) or `max_pages` is reached. Collect links from `/`, `/about` and every directory page, de-duplicated by slug in first-seen order. Complete exactly per §5.1 step 6.
- [ ] **Step 4: Run** `uv run pytest tests/test_scrape.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: website scraper with completeness report`.

---

### Task 3: CRM client and fake CRM

**Files:**
- Create: `src/bellhaven_sync/crm.py`, `tests/fake_crm.py`, `tests/fixtures/snapshot/crm-*.json`
- Test: `tests/test_crm.py`

**Interfaces:**
- Produces (`crm.py`): `class ReadOnlyError(Exception)`; `class CrmError(Exception)` with `.status: int`, `.message: str`; `class CrmClient(base_url: str, token: str, read_only: bool = True, session: requests.Session | None = None)` with `list_accounts() -> list[Account]`, `list_contacts() -> list[Contact]`, `get_account(id) -> Account`, `get_contact(id) -> Contact`, `patch_account(id, body: dict) -> Account`, `patch_contact(id, body) -> Contact`, `create_account(body) -> Account`, `create_contact(body) -> Contact`. Lists page with `page_size=200` until a page returns fewer than 200. 4xx/5xx raise `CrmError` carrying the response's `detail` text. The token is never included in an exception or log.
- Produces (`tests/fake_crm.py`): `class FakeCrm` with the same eight methods plus `FakeCrm.from_snapshot(dir: Path)`, `fail_next(method: str, status: int, message: str, after: int = 0)` (the (after+1)-th later call with that method, `GET` included, raises `CrmError(status, message)`; a failed write changes nothing), `lose_next_response(method: str)` (performs the write, then raises `requests.ConnectionError`), `reject_fields(names: set[str])` (POST/PATCH bodies containing them raise `CrmError(422, ...)`), `writes: list[tuple[str, str, dict]]`, and `add_account(record)`. Creates assign ids `001FAKE` + 11-digit counter, `created_by_candidate=True`, `parent_name` derived from `parent_id`, `updated_at` now. PATCH refreshes `parent_name` when `parent_id` changes.

- [ ] **Step 1: Write the failing tests**

```python
def test_read_only_client_refuses_every_write():
    c = CrmClient("http://x", "t")
    for call in (lambda: c.patch_account("a", {}), lambda: c.patch_contact("c", {}),
                 lambda: c.create_account({}), lambda: c.create_contact({})):
        with pytest.raises(ReadOnlyError): call()

def test_pagination_reads_until_short_page(): # session stub returns 200 + 200 + 7 records
    assert len(CrmClient("http://x", "t", session=PagingStub([200, 200, 7])).list_accounts()) == 407

def test_error_carries_detail_not_token():
    with pytest.raises(CrmError) as e:
        CrmClient("http://x", "SECRET", read_only=False, session=ErrorStub(422, {"detail": "bad field"})).create_account({})
    assert e.value.status == 422 and "bad field" in str(e.value) and "SECRET" not in str(e.value)

def test_fake_loads_snapshot_and_derives_parent_name():
    f = FakeCrm.from_snapshot(SNAP)
    assert len(f.list_accounts()) == 121 and len(f.list_contacts()) == 67
    lima = f.get_account(full_id("9MB6KL"))
    f.patch_account(lima["account_id"], {"parent_id": OPERATOR_ID})
    assert f.get_account(lima["account_id"])["parent_name"] == "Bellhaven Senior Living (Parent Account)"
```

`full_id(suffix)` and `OPERATOR_ID` live in `tests/conftest.py` (suffix lookup over the snapshot; exactly one match or the helper raises).

- [ ] **Step 2: Run** `uv run pytest tests/test_crm.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `crm.py` and `tests/fake_crm.py`.
- [ ] **Step 4: Run** `uv run pytest tests/test_crm.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: read-only CRM client and fake CRM`.

---

### Task 4: Rules and matching

**Files:**
- Create: `src/bellhaven_sync/rules.py`, `src/bellhaven_sync/match.py`
- Test: `tests/test_match.py`

**Interfaces:**
- Produces (`rules.py`, every judgment call in one file):
  - `WEIGHTS = {"street": 50, "admin": 25, "phone": 10, "zip": 5, "city": 5}`, `NAME_MAX = 10`, `LOW_NAME_OVERLAP = 0.5`.
  - `SECTIONS: tuple[str, ...]` (Global Constraints order); `SECTION_FOR_KIND: dict[str, str]` for kinds `chow`, `duplicates`, `wrong_parent`, `new_account`, `gone`, `possible_match`, `rename`, `details`, `attention`.
  - `money(v) -> int` (int, numeric string, `None`, `""` → int; `None`/`""` → 0).
  - `needs_chow(acc: Account) -> bool` = `money(lifetime_revenue) > 0 and money(outstanding_ar) > 0`.
  - `is_parent_account(acc, accounts) -> bool`; `eligible(acc, accounts, reserved_accounts: set[str]) -> bool` (§7.1).
  - `survivor_sort_key(acc, loc: Location, contacts_by_account, operator_id: str) -> tuple` (§8.4 order; lower sorts first).
  - `can_sync_street(crm_street: str, site_street: str) -> bool` (False when PO Box or abbreviation-equivalent).
  - `care_compatible(crm_care: str, loc: Location) -> bool`; `care_for_new(loc) -> str` (first offering mapped).
  - `note_line(day: date, what: str, why: str) -> str`; `GONE_WHAT = "Not listed on Bellhaven's website as of {day}"`, `GONE_WHY = "no other CRM account at this address. Confirm closure or sale before re-parenting."`.
- Produces (`match.py`): `Signals(street: bool, admin: bool, phone: bool, zip: bool, city: bool, name: float, po_box: bool)`; `Candidate(account_id: str, signals: Signals, score: int, band: str | None)`; `LocationMatch(location: Location, group: list[str], band: str | None, lows: list[Candidate], conflicts: list[str], ruled_out: list[Candidate])`; `signals(loc, acc, contacts_by_account) -> Signals`; `band(s: Signals) -> str | None` (§7.4); `match_location(loc, accounts, contacts_by_account, eligible_ids: set[str]) -> LocationMatch`; `match_all(locations, accounts, contacts, eligible_ids) -> dict[str, LocationMatch]`; `contacts_by_account(contacts) -> dict[str, list[Contact]]` (active contacts only).

- [ ] **Step 1: Write the failing tests** (fixtures: scraped snapshot + CRM snapshot; ids by suffix)

```python
@pytest.mark.parametrize("slug,group,band_", [
    ("bellhaven-of-maplewood", ["J8DYYV"], "High"),
    ("bellhaven-of-ashtabula", ["EPSLSV"], "Medium"),
    ("bellhaven-of-owosso", ["2ZTRE6", "15UA71"], "High"),
    ("bellhaven-gardens-of-monroe", ["AGG1S5", "LA9HTX", "KM4FHM"], "High"),
    ("bellhaven-of-kettering", ["CAE2X4", "RXS09J", "ALS9GP"], "High"),
    ("bellhaven-of-port-clinton", ["KB8932", "LTSN24"], "High"),
    ("bellhaven-shores-of-erie", ["YHN220", "SZLZHH"], "High"),
])
def test_groups(slug, group, band_):
    m = MATCHES[slug]; assert suffixes(m.group) == set(group) and m.band == band_

def test_look_alikes():
    assert MATCHES["bellhaven-at-union-square"].group == [] and suffixes(c.account_id for c in MATCHES["bellhaven-at-union-square"].lows) == {"RLLJ9P"}
    assert "10RGY5" not in all_candidate_suffixes("amberly-manor")      # Colorado
    assert "RES536" not in all_candidate_suffixes("bellhaven-of-carlisle")  # New Carlisle, OH
    assert MATCHES["bellhaven-of-maplewood"].lows == []                  # 2DJ971 suppressed

def test_new_locations_have_no_match():
    for s in ("bellhaven-of-batavia", "bellhaven-of-carlisle", "amberly-manor", "bellhaven-at-union-square"):
        assert MATCHES[s].group == []

def test_no_conflicts_today():
    assert all(m.conflicts == [] for m in MATCHES.values())

def test_state_conflict_is_reported():  # same street+zip, different state
    m = match_location(loc_with(state="MI", slug="bellhaven-of-tiffin"), ACCOUNTS, CONTACTS_BY_ACCOUNT, ELIGIBLE)
    assert m.group == [] and m.conflicts

def test_care_type_splits_a_duplicate_group():   # §7.5
    odd = dict(acct("LTSN24"), care_type="Independent Living")
    m = match_location(LOC["bellhaven-of-port-clinton"], replace(ACCOUNTS, odd), CONTACTS_BY_ACCOUNT, ELIGIBLE)
    assert suffixes(m.group) == {"KB8932"} and m.conflicts

def test_money_and_billing_rule():     # Review Focus 2
    assert money("5200") == 5200 and money(None) == 0 and money("") == 0
    assert needs_chow(acct("WSXZZB")) and needs_chow(acct("CRBLFT")) and needs_chow(acct("Z2LGDM"))
    assert not needs_chow(acct("9MB6KL")) and not needs_chow(acct("YCZ4YM")) and not needs_chow(acct("CAE2X4"))

def test_eligibility_excludes_retired_and_parents():
    a = dict(acct("WSXZZB"), chow_current_account="X")
    assert not eligible(a, ACCOUNTS, set()) and not eligible(acct_by_name("Bellhaven Senior Living (Parent Account)"), ACCOUNTS, set())
```

- [ ] **Step 2: Run** `uv run pytest tests/test_match.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `rules.py` and `match.py` exactly per §7. Score = sum of the matched signals' weights + `round(NAME_MAX * jaccard)`. The group is the High candidates sharing one full address, ordered by `survivor_sort_key`; §7.5's care-type split and Medium-beside-High conflict apply.
- [ ] **Step 4: Run** `uv run pytest tests/test_match.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: rules and matching`.

---

### Task 5: Proposals

**Files:**
- Create: `src/bellhaven_sync/propose.py` (models `Change`, `Step`, `Card` go in `models.py`)
- Test: `tests/test_propose.py`

**Interfaces:**
- Produces (`models.py`):
  - `Change(kind: str, record: str, field: str, value: Any, location_slug: str)`, frozen, with `.key -> str` = `sha256(json.dumps([kind, record, field, value], sort_keys=True, separators=(",", ":")))`. Kinds: `set_field`, `create_account` (record `loc:<slug>`, field `""`, value = identity dict `{name, billing_street, billing_city, billing_state, billing_zip, parent_id}`), `create_contact` (record `loc:<slug>`, value `{name, title}`), `move_contact` (record contact id, field `account_id`), `set_relation` (field `duplicate_of_account` or `chow_current_account`; value an account id or `{"created_for": slug}`).
  - `Step(n: int, method: str, path: str, body: dict, note_append: str | None, change_keys: tuple[str, ...])`. A body value `{"$created": k}` means "the id created by step k".
  - `Card(id: str, kind: str, section: str, title: str, summary: str, location_slug: str | None, account_ids: tuple[str, ...], band: str | None, changes: tuple[Change, ...], steps: tuple[Step, ...], evidence: dict, alt_group: str | None)`; `id` = first 12 hex of sha256 of the sorted change keys; `.change_keys -> frozenset[str]`.
- Produces (`propose.py`): `propose(report: ScrapeReport, accounts, contacts, operator_id: str, today: date, reserved_accounts: set[str], reserved_locations: set[str]) -> list[Card]`. Pure. Drops changes already true in the CRM. Does **not** filter decided keys (the ledger does). Gone-from-the-website cards only when `report.complete`.
- Card contents per §8.2–§8.8. Exact step shapes (tests pin them):
  - **chow (new account):** 1 `POST /accounts` (site values, `parent_id` operator, `status` Active, `care_type` mapped, `note` = one note line naming the old account); 2 `POST /contacts` (`account_id {"$created": 1}`, site administrator, title `Administrator`, site phone, `is_active` true); 3 `PATCH /accounts/<old>` body exactly `{"chow_current_account": {"$created": 1}}`, `note_append` None.
  - **chow (existing successor, Sandusky):** 1 `PATCH /accounts/<old>` body exactly `{"chow_current_account": <successor>}`; 2 `PATCH /accounts/<successor>` body `{}` with `note_append`.
  - **duplicates:** per loser `PATCH {"duplicate_of_account": survivor, "status": "Inactive"}` + `note_append`; per active contact on a loser `PATCH /contacts/<id> {"account_id": survivor}`.
  - **wrong_parent / rename:** one `PATCH` with the changed fields among `parent_id`, `name`, `status` + `note_append`.
  - **new_account:** steps 1–2 as chow-new without step 3.
  - **gone:** `PATCH {"status": "Needs Review"}` + `note_append` = `note_line(today, GONE_WHAT, GONE_WHY)`.
  - **details:** one `PATCH` with the differing fields among `phone`, `billing_street` (only if `can_sync_street`), `billing_city`, `billing_state`, `billing_zip`, `care_type` (only if not `care_compatible`); no note.
  - **possible_match:** the rename/details changes that would make the look-alike agree with the site; same `alt_group` as the location's new_account card.
  - **attention:** no steps.

- [ ] **Step 1: Write the failing tests** (run on the snapshot, `today=date(2026, 11, 7)`)

```python
def test_card_counts_by_section():
    assert Counter(c.section for c in CARDS) == {"Change of ownership": 3, "Duplicates": 5, "Wrong parent": 4,
        "New accounts": 4, "Gone from the website": 2, "Possible matches": 1, "Renames": 7, "Details": 16}

def test_tiffin_change_of_ownership_shape():
    c = card(kind="chow", slug="bellhaven-of-tiffin")
    assert [(s.method, s.path) for s in c.steps] == [("POST", "/accounts"), ("POST", "/contacts"), ("PATCH", f"/accounts/{full_id('WSXZZB')}")]
    assert c.steps[0].body["name"] == "Bellhaven of Tiffin" and c.steps[0].body["parent_id"] == OPERATOR_ID
    assert c.steps[1].body["name"] == "Walt Osei" and c.steps[2].body == {"chow_current_account": {"$created": 1}}
    assert c.steps[2].note_append is None

def test_sandusky_points_at_millstone_only():
    c = card(kind="chow", account="Z2LGDM")
    assert c.steps[0].body == {"chow_current_account": full_id("63BVK3")} and c.steps[0].note_append is None

def test_direct_reparents():
    assert card(kind="wrong_parent", account="9MB6KL").steps[0].body == {"parent_id": OPERATOR_ID}
    assert card(kind="wrong_parent", account="CAE2X4").steps[0].body == {"parent_id": OPERATOR_ID, "name": "Bellhaven of Kettering"}
    assert not any(c for c in CARDS if suffixes(c.account_ids) & {"4R00CD", "6FNN8V", "EKKA3T"} and c.kind in ("chow", "wrong_parent"))

def test_owosso_duplicate_moves_tricia():
    c = card(kind="duplicates", slug="bellhaven-of-owosso")
    assert ("PATCH", f"/contacts/{contact_id('Tricia Lindqvist')}") in [(s.method, s.path) for s in c.steps]

def test_gone_cards_and_note():
    gone = [c for c in CARDS if c.kind == "gone"]
    assert suffixes(a for c in gone for a in c.account_ids) == {"7DTQP7", "HWR7QE"}
    assert gone[0].steps[0].note_append == "[Bellhaven sync 2026-11-07] Not listed on Bellhaven's website as of 2026-11-07; no other CRM account at this address. Confirm closure or sale before re-parenting."

def test_details_never_rewrite_spelling_or_po_box():
    bodies = [s.body for c in CARDS if c.kind == "details" for s in c.steps]
    assert not any("billing_street" in b for b in bodies)
    assert card(kind="details", account="L09P4F").steps[0].body == {"billing_zip": "45662"}

def test_union_square_alternatives_share_a_group():
    new, pm = card(kind="new_account", slug="bellhaven-at-union-square"), card(kind="possible_match", slug="bellhaven-at-union-square")
    assert new.alt_group == pm.alt_group is not None

def test_incomplete_scrape_builds_no_gone_cards():
    assert not any(c.kind == "gone" for c in propose(incomplete(REPORT), ACCOUNTS, CONTACTS, OPERATOR_ID, DAY, set(), set()))

def test_conflict_becomes_attention_card():   # §8.8
    cards = propose(report_with(loc_with(state="MI", slug="bellhaven-of-tiffin")), ACCOUNTS, CONTACTS, OPERATOR_ID, DAY, set(), set())
    a = [c for c in cards if c.location_slug == "bellhaven-of-tiffin"]
    assert [c.kind for c in a] == ["attention"] and a[0].steps == ()

def test_money_never_written():
    assert not any(k in s.body for c in CARDS for s in c.steps for k in ("lifetime_revenue", "outstanding_ar"))
```

- [ ] **Step 2: Run** `uv run pytest tests/test_propose.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `propose.py`. Evidence dict per card: `site` (Location fields), `crm` (each account's current fields + active contacts), `signals` (per candidate: each signal ✓/✗ with points), `ruled_out` (candidate, reason), `billing` (revenue, AR, `needs_chow`, the rule's sentence), `conflicts`.
- [ ] **Step 4: Run** `uv run pytest tests/test_propose.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: proposals with evidence and ordered write steps`.

---

### Task 6: Ledger and the daily run

**Files:**
- Create: `src/bellhaven_sync/ledger.py`, `src/bellhaven_sync/pipeline.py`
- Test: `tests/test_ledger.py`, `tests/test_rerun.py`, `tests/test_guard.py`

**Interfaces:**
- Produces (`ledger.py`): `class Ledger(path: Path | str)` (SQLite, `journal_mode=WAL`, `busy_timeout=5000`; schema §10.1 plus `runs.locations_json`, the complete run's locations for re-checks). Methods:
  - `start_run() -> int`; `finish_run(run_id, report: ScrapeReport, accounts_read: int) -> None`; `latest_run() -> dict | None`; `latest_complete_locations() -> list[Location]`.
  - `decided_keys() -> set[str]`; `record_cards(run_id, cards: list[Card], complete: bool) -> dict[str, int]` (§10.3 steps 2–5; returns counts `new`, `refreshed`, `superseded`, `blocked`, `unblocked`).
  - `cards(status: str | None = None) -> list[CardRow]`; `card(card_id) -> CardRow`; `CardRow` = `Card` + `status`, `version` (sha256 of change keys + `refreshed_at`), `flag`, `prior_rejections`, `operation` (latest operation with its steps).
  - `claim(card_id, version) -> bool` (`pending` → `applying`, guarded); `set_status(card_id, expected, new) -> bool`; `decide(card_id, decision: str, note: str = "") -> None` (one row per change key); `claim_alt_group(group_id, card_id) -> bool`; `close_alt_group(group_id) -> None`.
  - `start_operation(card_id) -> int`; `step_intent(op_id, n, method, path, body, before)`; `step_done(op_id, n, status, response, created_id)`; `step_failed(op_id, n, status, message)`; `finish_operation(op_id, status)`.
  - `reserved_accounts() -> set[str]`; `reserved_locations() -> set[str]`; `flag(card_id, reason)`; `history() -> list[dict]`.
- Produces (`pipeline.py`): `RunSummary(run_id: int, complete: bool, reason: str, found: int, stated: int | None, accounts_read: int, counts: dict[str, int], by_section: dict[str, int])`; `run_once(fetch, crm, ledger: Ledger, operator_parent_name: str, today: date) -> RunSummary` (scrape → read CRM → find operator exactly once or raise `RunAborted` → reserved sets from the ledger → `propose` → drop decided keys → `record_cards`). A CRM read failure raises `RunAborted` before any ledger write.

- [ ] **Step 1: Write the failing tests**

```python
def test_first_run_records_42(tmp_path):        # test_ledger.py
    s = run_once(FETCH, FakeCrm.from_snapshot(SNAP), Ledger(tmp_path/"d.db"), PARENT, DAY)
    assert s.complete and sum(s.by_section.values()) == 42

def test_second_run_same_cards_same_ids(tmp_path):
    l = Ledger(tmp_path/"d.db"); crm = FakeCrm.from_snapshot(SNAP)
    run_once(FETCH, crm, l, PARENT, DAY); ids = {c.id for c in l.cards("pending")}
    s = run_once(FETCH, crm, l, PARENT, DAY)
    assert {c.id for c in l.cards("pending")} == ids and s.counts["new"] == 0

def test_rejected_never_returns(tmp_path):      # test_rerun.py
    l, crm = fresh(tmp_path); run_once(FETCH, crm, l, PARENT, DAY)
    pm = only(l.cards("pending"), kind="possible_match"); l.decide(pm.id, "rejected"); l.set_status(pm.id, "pending", "rejected")
    run_once(FETCH, crm, l, PARENT, DAY)
    assert not [c for c in l.cards("pending") if c.kind == "possible_match"]

def test_regrouped_rejection_stays_rejected(tmp_path):
    crm = FakeCrm.from_snapshot(SNAP); crm.patch_account_raw(full_id("NRLNME"), {"billing_street": "1600 College Avenue"})
    l = Ledger(tmp_path/"d.db"); run_once(FETCH, crm, l, PARENT, DAY)
    d = only(l.cards("pending"), kind="details", account="NRLNME"); assert {"phone", "billing_street"} <= set(d.steps[0].body)
    l.decide(d.id, "rejected"); l.set_status(d.id, "pending", "rejected")
    crm.patch_account_raw(full_id("NRLNME"), {"billing_street": "1610 College Avenue"})
    run_once(FETCH, crm, l, PARENT, DAY)
    assert not [c for c in l.cards("pending") if full_id("NRLNME") in c.account_ids]

def test_crm_read_failure_leaves_ledger_untouched(tmp_path):
    l = Ledger(tmp_path/"d.db"); crm = FakeCrm.from_snapshot(SNAP); crm.fail_next("GET", 500, "down")
    with pytest.raises(RunAborted): run_once(FETCH, crm, l, PARENT, DAY)
    assert l.latest_run() is None and l.cards() == []

def test_operator_must_exist_exactly_once(tmp_path):
    with pytest.raises(RunAborted): run_once(FETCH, FakeCrm.from_snapshot(SNAP), Ledger(tmp_path/"d.db"), "No Such Parent", DAY)

def test_incomplete_run_blocks_gone_cards(tmp_path):   # test_guard.py
    l, crm = fresh(tmp_path); run_once(FETCH, crm, l, PARENT, DAY)
    s = run_once(fake_fetcher(SITE, fail={"/communities/bellhaven-of-tiffin"}), crm, l, PARENT, DAY)
    assert not s.complete and s.counts["superseded"] == 0
    assert {c.status for c in l.cards() if c.kind == "gone"} == {"blocked"}
    run_once(FETCH, crm, l, PARENT, DAY)
    assert {c.status for c in l.cards() if c.kind == "gone"} == {"pending"}

def test_already_inactive_operator_account_gets_no_card(tmp_path):
    crm = FakeCrm.from_snapshot(SNAP); crm.patch_account_raw(full_id("7DTQP7"), {"status": "Inactive"})
    l = Ledger(tmp_path/"d.db"); run_once(FETCH, crm, l, PARENT, DAY)
    assert not [c for c in l.cards() if full_id("7DTQP7") in c.account_ids]
```

`patch_account_raw` is a FakeCrm test helper that edits a record without logging a write (add it in this task).

- [ ] **Step 2: Run** `uv run pytest tests/test_ledger.py tests/test_rerun.py tests/test_guard.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `ledger.py` and `pipeline.py`. Every status move is one `UPDATE … WHERE id=? AND status=?` and returns whether one row changed.
- [ ] **Step 4: Run** the three files again. Expected: PASS.
- [ ] **Step 5: Commit** `feat: decision ledger and the daily run`.

---

### Task 7: Applying approved cards

**Files:**
- Create: `src/bellhaven_sync/apply.py`, `src/bellhaven_sync/verify_lib.py`, `tests/fixtures/expected_end_state.json`
- Test: `tests/test_apply.py`, `tests/test_end_state.py`, `tests/test_alternatives.py`

**Interfaces:**
- Consumes: `Ledger`, `propose`, `CrmClient`/`FakeCrm`, `run_once`'s operator lookup (expose `find_operator(accounts, name) -> str` in `pipeline.py`).
- Produces (`apply.py`): `ApplyResult(status: str, done: int, total: int, message: str)`; `approve(card_id: str, version: str, ledger: Ledger, crm, operator_parent_name: str, today: date) -> ApplyResult`; `reject(card_id, version, ledger, note: str = "") -> ApplyResult`; `retry(card_id, ledger, crm, operator_parent_name, today) -> ApplyResult`; `reconcile(card_id, ledger, crm) -> ApplyResult`; `create_again(card_id, ledger, crm, operator_parent_name, today) -> ApplyResult`; `acknowledge(card_id, version, ledger) -> ApplyResult`.
- Behaviour exactly §11: claim (and alt-group claim) → re-check → steps. **Re-check** = `propose()` on a fresh CRM read and `ledger.latest_complete_locations()`, with reservations from *other* operations only; the card passes when some fresh card of the same kind and location/accounts has exactly the card's change keys minus the keys of steps this operation already finished. Otherwise status `stale`, no write. PATCH steps read the record first and skip when already at target; note steps append once (skip if the line is already present). POST steps record intent first; an unknown outcome → `needs-reconciliation`, never re-POSTed; `reconcile` searches `created_by_candidate=true` records with the full identity (one → adopt; none → message "nothing found" and `create_again` becomes available; several → stop). A write error → `failed` with `"Failed at step n of m: <message>"`.
- `expected_end_state.json` schema: `{"accounts": {"<full id>": {<field>: <value or {"created_for": slug}>, "note_contains": [str]}}, "created_accounts": [{"slug", "name", "parent": "operator", "billing_street", "billing_city", "billing_state", "billing_zip", "care_type", "status", "phone", "administrator"}], "moved_contacts": [{"contact_id", "account_id"}]}`. Every row of DESIGN §9.1–9.2, with full ids. Any field not listed must equal the snapshot.

- [ ] **Step 1: Write the failing tests**

```python
def test_end_state(tmp_path):                       # test_end_state.py
    l, crm = fresh(tmp_path); run_once(FETCH, crm, l, PARENT, DAY)
    pm = only(l.cards("pending"), kind="possible_match"); reject(pm.id, pm.version, l)
    for c in l.cards("pending"): assert approve(c.id, c.version, l, crm, PARENT, DAY).status == "done"
    assert diff_against_expected(crm, SNAP, EXPECTED) == []      # helper shared with scripts/verify.py
    assert len(crm.list_accounts()) == 127 and len(crm.list_contacts()) == 73
    assert run_once(FETCH, crm, l, PARENT, DAY).counts["new"] == 0

def test_old_chow_accounts_get_one_write_only(tmp_path):
    l, crm = end_state(tmp_path)
    for s in ("WSXZZB", "CRBLFT", "Z2LGDM"):
        assert [list(b) for (m, p, b) in crm.writes if p.endswith(full_id(s))] == [["chow_current_account"]]

def test_failure_then_retry_no_second_create(tmp_path):   # test_apply.py
    c = chow_card("bellhaven-of-tiffin"); crm.fail_next("POST", 500, "boom", after=1)  # step 1 succeeds, step 2 fails
    r = approve(c.id, c.version, l, crm, PARENT, DAY); assert r.status == "failed" and r.message.startswith("Failed at step 2 of 3")
    assert retry(c.id, l, crm, PARENT, DAY).status == "done"
    assert sum(1 for a in crm.list_accounts() if a["name"] == "Bellhaven of Tiffin") == 1

def test_lost_post_reconciles_without_reposting(tmp_path):
    c = new_card("bellhaven-of-batavia"); crm.lose_next_response("POST")
    assert approve(c.id, c.version, l, crm, PARENT, DAY).status == "needs-reconciliation"
    assert reconcile(c.id, l, crm).status in ("applying", "done")
    assert sum(1 for (m, p, _) in crm.writes if m == "POST" and p == "/accounts") == 1

def test_new_ar_makes_reparent_stale(tmp_path):
    c = wrong_parent_card("9MB6KL"); crm.patch_account_raw(full_id("9MB6KL"), {"outstanding_ar": 100})
    assert approve(c.id, c.version, l, crm, PARENT, DAY).status == "stale" and crm.writes == []

def test_created_elsewhere_makes_new_stale(tmp_path):
    c = new_card("bellhaven-of-batavia"); crm.add_account(batavia_record_under_operator())
    assert approve(c.id, c.version, l, crm, PARENT, DAY).status == "stale"

def test_half_done_chow_reserves_both_accounts(tmp_path):
    c = chow_card("bellhaven-of-tiffin"); crm.fail_next("PATCH", 500, "boom")  # steps 1-2 (POSTs) succeed, step 3 fails
    approve(c.id, c.version, l, crm, PARENT, DAY)
    run_once(FETCH, crm, l, PARENT, DAY)
    assert not [x for x in l.cards("pending") if x.location_slug == "bellhaven-of-tiffin" or full_id("WSXZZB") in x.account_ids]

def test_rejected_write_shape_stops_cleanly(tmp_path):    # Review Focus 1
    crm.reject_fields({"care_type"}); c = new_card("bellhaven-of-batavia")
    r = approve(c.id, c.version, l, crm, PARENT, DAY)
    assert r.status == "failed" and "Failed at step 1 of 2" in r.message and crm.writes == []

def test_two_alternatives_one_wins(tmp_path):              # test_alternatives.py
    new, pm = union_square_cards(); l2 = Ledger(l.path)
    results = [l.claim_alt_group(new.alt_group, new.id), l2.claim_alt_group(pm.alt_group, pm.id)]
    assert sorted(results) == [False, True]
```

Shared fixtures in `tests/conftest.py` (added in this task): `fresh(tmp_path) -> (Ledger, FakeCrm)`; `end_state(tmp_path) -> (Ledger, FakeCrm)` = the flow of `test_end_state` up to the approvals; `chow_card`, `new_card`, `wrong_parent_card`, `union_square_cards` return the pending `CardRow`s after one `run_once`.

- [ ] **Step 2: Run** `uv run pytest tests/test_apply.py tests/test_end_state.py tests/test_alternatives.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `apply.py`, write `expected_end_state.json` from DESIGN §9, and `diff_against_expected(crm, snapshot_dir: Path, expected_path: Path) -> list[str]` in `src/bellhaven_sync/verify_lib.py` (one line per mismatch: `<id or slug> <field>: expected X, got Y`; ignores `updated_at` and `parent_name`).
- [ ] **Step 4: Run** the three files. Expected: PASS.
- [ ] **Step 5: Commit** `feat: apply approved cards with re-check, safe creates and retry`.

---

### Task 8: Review app

**Files:**
- Create: `src/bellhaven_sync/app.py`, `templates/{base,queue,history,_card}.html`, `static/app.css`
- Test: `tests/test_app.py`, `tests/test_app_security.py`

**Interfaces:**
- Consumes: `Ledger`, `apply.*`, `Config`.
- Produces: `create_app(cfg: Config, ledger: Ledger, crm_factory: Callable[[], Any], today: Callable[[], date]) -> FastAPI`. Routes: `GET /` (queue, optional `?location=<slug>`), `GET /history`, `POST /cards/{id}/approve|reject|retry|reconcile|create-again|acknowledge` (form fields `csrf`, `version`, optional `note`), each redirecting `303` to `/?section=<section>#card-<id>` with a flash message in a short-lived cookie.
- Anti-forgery: on first GET, set cookie `bh_csrf` = `secrets.token_urlsafe(32)` (`HttpOnly`, `SameSite=Strict`, **no `Secure`**); every POST must carry the same value in `csrf` (`hmac.compare_digest`), else 403. A changed `version` → 409 "This card changed since you opened it. Reload." A card no longer `pending` → 409 "Already decided".
- Page content exactly §13: run banner (time, complete/why not, found/stated, accounts read, counts by section), red banner when the latest run is incomplete, sections in order with counts, each card a `<details id="card-<id>">`: collapsed line (section tag, facility, band, `summary`); expanded: before → after per field, site vs CRM side by side with administrator vs contacts, signals with ✓/✗ and points, ruled-out and conflicts, billing reasoning, "what will be written" (each step's method, path, body), alt-group siblings, prior rejections, "View in CRM" links (`cfg.crm_browser_url.format(account_id=...)`), Approve/Reject with an optional note. Blocked/stale cards show why and render no Approve button. Flagged cards show a yellow banner and sort first.
- Interrupted applies (§15): at startup `create_app` moves every card left `applying` (and its operation) to `failed` with message `Interrupted; Retry resumes after a fresh check.`

- [ ] **Step 1: Write the failing tests** (FastAPI `TestClient` over the fake CRM after one `run_once`)

```python
def test_queue_shows_sections_in_order(client):
    html = client.get("/").text
    assert [h for h in SECTIONS if h in html][:8] == list(SECTIONS[:8])
    assert "42 proposals" in html and "35 of 35 locations" in html

def test_card_shows_evidence_and_writes(client):
    html = client.get("/?location=bellhaven-of-tiffin").text
    assert "$84,000" in html and "$12,400" in html and "POST /accounts" in html and "chow_current_account" in html

def test_approve_then_already_decided(client):     # Review Focus 3
    c = pending(kind="rename", slug="bellhaven-rehabilitation-nursing-of-grove-city")
    r = client.post(f"/cards/{c.id}/approve", data={"csrf": client.cookies["bh_csrf"], "version": c.version}, follow_redirects=True)
    assert "Approved: 1 of 1 writes done" in r.text
    r2 = client.post(f"/cards/{c.id}/approve", data={"csrf": client.cookies["bh_csrf"], "version": c.version})
    assert r2.status_code == 409 and len(FAKE.writes) == 1

def test_interrupted_apply_offers_retry(tmp_path):
    l = ledger_with_card_in("applying", tmp_path); client = TestClient(create_app(CFG, l, lambda: FAKE, lambda: DAY))
    assert "Interrupted; Retry resumes" in client.get("/").text and ">Retry<" in client.get("/").text

def test_csrf_cookie_not_secure(client):          # Review Focus 4
    set_cookie = client.get("/").headers["set-cookie"]
    assert "bh_csrf=" in set_cookie and "secure" not in set_cookie.lower()

def test_post_without_token_refused(client):      # test_app_security.py
    c = any_pending(); assert client.post(f"/cards/{c.id}/approve", data={"version": c.version}).status_code == 403

def test_get_cannot_write(client):
    assert client.get(f"/cards/{any_pending().id}/approve").status_code == 405

def test_changed_version_refused(client):
    c = any_pending(); assert client.post(f"/cards/{c.id}/approve", data={"csrf": client.cookies["bh_csrf"], "version": "stale"}).status_code == 409
```

- [ ] **Step 2: Run** `uv run pytest tests/test_app.py tests/test_app_security.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `app.py`, templates and CSS. Plain HTML forms, no JavaScript. System font stack; one accent colour; legible at 1280×800 screen-recording size.
- [ ] **Step 4: Run** the two files. Expected: PASS.
- [ ] **Step 5: Design pass.** Run the installed `impeccable` and `taste-skill` reviews on a screenshot of `/` and one expanded card; fix what they find. Re-run Step 4.
- [ ] **Step 6: Commit** `feat: review app`.

---

### Task 9: CLI, schedule and README

**Files:**
- Create: `src/bellhaven_sync/cli.py`, `deploy/crontab`, `deploy/github-actions-daily.yml`, `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`. Subcommands: `run` (prints the run banner; exit 0, or 1 on `RunAborted`), `serve [--host 127.0.0.1] [--port 8000]` (refuses a `--host` that is not loopback or a private/tailnet address: 10/8, 172.16/12, 192.168/16, 100.64/10), `flag <card-id> <reason>` and `flag --from-json <path>` (`[{"card_id", "reason"}]`).
- `deploy/crontab` exactly as §14. `deploy/github-actions-daily.yml`: `on: schedule: - cron: "30 11 * * *"` plus `workflow_dispatch`; checkout, `astral-sh/setup-uv`, `uv sync`, restore/save `decisions.db` on a `state` branch, `uv run bellhaven-sync run` with `BELLHAVEN_CRM_TOKEN: ${{ secrets.BELLHAVEN_CRM_TOKEN }}`. It lives outside `.github/workflows/`, so it never runs.
- README: what it is (3 lines), quick start (`uv sync`; `export BELLHAVEN_CRM_TOKEN=…`; `uv run bellhaven-sync run`; `uv run bellhaven-sync serve`), how it works (the §4 diagram), schedule, tests (`uv run pytest`; `uv run pytest -m e2e`), links to DESIGN and WRITEUP.

- [ ] **Step 1: Write the failing tests**

```python
def test_run_prints_banner(monkeypatch, tmp_path, capsys):   # wires fake fetcher + FakeCrm via monkeypatch of cli._dependencies
    assert main(["run"]) == 0 and "42 proposals" in capsys.readouterr().out

def test_serve_refuses_public_host():
    assert main(["serve", "--host", "0.0.0.0"]) == 2

def test_flag_from_json(tmp_path):
    (tmp_path/"f.json").write_text(json.dumps([{"card_id": CARD_ID, "reason": "check Sandusky"}]))
    assert main(["flag", "--from-json", str(tmp_path/"f.json")]) == 0 and LEDGER.card(CARD_ID).flag == "check Sandusky"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `cli.py` (`_dependencies(cfg) -> (fetch, crm, ledger)` is the one seam tests replace), the deploy files and README.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat: CLI, daily schedule and README`.

---

### Task 10: Verifier, blind-check tooling and the browser test

**Files:**
- Create: `scripts/verify.py`, `scripts/blind_check.py`, `tests/e2e/test_review_flow.py`
- Test: `tests/test_verify.py`, `tests/test_blind_check.py`, `tests/e2e/test_review_flow.py`

**Interfaces:**
- `scripts/verify.py`: `verify_main(crm=None) -> int` (None = a read-only `CrmClient` from config); `uv run python scripts/verify.py` reads the real CRM read-only, calls `diff_against_expected(crm, tests/fixtures/snapshot, tests/fixtures/expected_end_state.json)`, prints `PASS <n> checks` or each mismatch, exit 0/1.
- `scripts/blind_check.py prepare --out blind/` writes `locations.json` (scraped), `accounts.json`, `contacts.json` (CRM read-only) and `PROMPT.md` (the brief's task and SOP text, the CRM conventions, and the answer schema `{"locations": {slug: {"outcome": "match|fix|new|look_alike", "account_ids": [..], "survivor": id|null, "parent_action": "none|reparent|chow_new|chow_existing", "rename_to": str|null}}, "leftovers": {account_id: {"outcome": "needs_review|chow_existing|duplicate|inactive", "target": id|null}}}`); it never includes DESIGN.md or card data. `compare blind/answers.json` maps each pending card to the same outcome vocabulary, calls `ledger.flag(card_id, "Blind check disagrees: <their answer>")` on each disagreement, and prints a table.
- e2e: start `create_app` over a FakeCrm on `127.0.0.1:<free port>` in a background thread (uvicorn `Server.run`).

- [ ] **Step 1: Write the failing tests**

```python
def test_verify_passes_on_expected_state(tmp_path):
    l, crm = end_state(tmp_path); assert verify_main(crm) == 0

def test_verify_reports_a_mismatch(tmp_path, capsys):
    l, crm = end_state(tmp_path); crm.patch_account_raw(full_id("NRLNME"), {"phone": "(000) 000-0000"})
    assert verify_main(crm) == 1 and full_id("NRLNME") in capsys.readouterr().out

def test_compare_flags_disagreement(tmp_path):
    answers = expected_answers(); answers["locations"]["bellhaven-at-union-square"]["outcome"] = "match"
    compare(answers, l); assert only(l.cards("pending"), kind="possible_match").flag

@pytest.mark.e2e
def test_approve_reject_rerun(page, live_app):
    page.goto(live_app.url); page.click("text=Renames")
    page.locator("#card-" + GROVE_CITY_RENAME).locator("text=Approve").click()
    expect(page.locator("text=Approved: 1 of 1 writes done")).to_be_visible()
    page.locator("#card-" + UNION_SQUARE_PM).locator("text=Reject").click()
    live_app.run_once(); page.reload()
    expect(page.locator("#card-" + UNION_SQUARE_PM)).to_have_count(0)
    page.goto(live_app.url + "/history"); expect(page.locator("text=Rejected")).to_be_visible()
```

- [ ] **Step 2: Run** `uv run pytest tests/test_verify.py tests/test_blind_check.py -v` and `uv run playwright install chromium && uv run pytest -m e2e -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both scripts and the e2e fixtures.
- [ ] **Step 4: Run** `uv run pytest -q && uv run pytest -m e2e -q`. Expected: all PASS; save the Playwright screenshots to `docs/screenshots/`.
- [ ] **Step 5: Commit** `feat: verifier, blind-check tooling, browser test`.

---

### Task 11: Independent review and pre-publication checks

No new product code; fixes from this task go in the files they concern.

- [ ] **Step 1:** Cross-model code review: GPT-6-Astra (`codex exec -m gpt-6-astra -c model_reasoning_effort=high -s read-only`) over the whole repo at the exact head, graded against DESIGN §2's SC-1…SC-8 and this plan's Review Focus. Record the verdict in `docs/review/`. Fix every FAIL; re-review the changed lines.
- [ ] **Step 2:** Secret and code scans: `gitleaks detect --source .`, `trivy fs --scanners secret,misconfig .`, `semgrep --config=p/python src scripts`. Expected: no secret; no high finding. Confirm `git log -p | grep -c bh_` returns 0.
- [ ] **Step 3:** Prose checks on README.md: `typos README.md docs/`, `harper-cli lint README.md`, the estate `humanizer-lint`. Fix findings.
- [ ] **Step 4:** Run the full suite once: `uv run pytest -q && uv run pytest -m e2e -q`. Expected: PASS.
- [ ] **Step 5: Commit** `chore: review fixes and pre-publication checks`.

---

## After the build (Gage's session; DESIGN §17–§19)

These are not code tasks; they are listed so nothing is missed.

1. Seat: `bellhaven-sync run` against the real CRM (read-only); `blind_check.py prepare`; a fresh Astra session answers `PROMPT.md` without the design; `blind_check.py compare`; Fable second opinion on Sandusky, Kettering, Owosso, Alliance, Coldwater; evaluator pair probabilities (development only).
2. Gage: off-camera first writes (Grove City rename; Batavia new account); seat writes `docs/api-notes.md` from what they show.
3. Gage: record the video with Cap (DESIGN §18 run-sheet), then click the remaining cards, flagged ones first.
4. Seat: `scripts/verify.py` and a second `bellhaven-sync run` (must propose zero); both outputs into WRITEUP.md.
5. Seat drafts, Gage edits: WRITEUP.md and the ~250–300-word form text; `docs/code-tour.md`, `docs/cheat-sheet.md`.
6. Gage: make the repo public, upload the video unlisted, submit the form before 2026-11-09T19:30Z; mock walkthrough before the interview.
