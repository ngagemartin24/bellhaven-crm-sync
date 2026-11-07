# Bellhaven CRM sync: design

Status: draft for review, 2026-11-07. Author: Gage Martin (built with Claude Code; reviewed by GPT).
Every decision below traces to a line in Appendix A (ids like `D4`).

## 1. Purpose

Long-term-care facilities change owners all the time, and each sale, rebrand or consolidation can
silently break the link between a facility and its parent company in the CRM. This system keeps one
operator's link set correct: it reads Bellhaven Senior Living's public website (the truth about what
Bellhaven owns today), compares it with the CRM, and proposes the fixes with their evidence. A person
approves or rejects each fix in a small local app; only an approval writes to the CRM. It runs daily,
and a second run never re-asks a decided question.

The deliverable is the corrected CRM copy plus the system that produced it.

## 2. Success criteria

- **SC-1** Every one of the website's locations ends in its expected state in the CRM copy (section 9),
  read back through the API by `scripts/verify.py`.
- **SC-2** Every parent move follows the billing rule: revenue > 0 AND outstanding AR > 0 means the old
  account keeps its parent, a new account is created under the right parent, and the old account's
  `chow_current_account` points to it; otherwise the account is re-parented directly.
- **SC-3** No look-alike account is changed (section 9.4).
- **SC-4** Nothing writes to the CRM except an Approve, Retry or Revert click in the review app. The
  pipeline's CRM client refuses writes, and a test proves it.
- **SC-5** A second run proposes nothing already approved, rejected or reverted.
- **SC-6** A partial scrape never proposes "gone from the website" changes.
- **SC-7** Each judgment call is one edit in `rules.py`, pinned by a test, so a live change is
  edit, test, show.
- **SC-8** Submitted before 2026-11-09T20:15Z with honest time, a ~2-minute narrated video, a public
  repo and a ~250–300 word writeup.

## 3. Scope

In: the scraper, matching, card generation, decision ledger, review app, apply/revert, daily schedule,
tests, a read-back verifier, the writeup, the demo video, walkthrough preparation.

Out (named in "what I'd build next", not built): other operators, Medicare ownership-file matching,
learning from decisions, change-triggered runs and alerts, a hosted deployment, any LLM call inside the
pipeline (A3, C1).

## 4. Architecture

Two programs share one SQLite file (`decisions.db`).

```
website ─▶ scrape ─┐
                   ├─▶ match ─▶ propose ─▶ decisions.db ─▶ review app ─▶ [Approve] ─▶ apply ─▶ CRM API
CRM API ─(read)────┘                         ▲                                              │
                                             └────────── step log, before-values ◀──────────┘
```

- **Pipeline** `uv run bellhaven-sync run`: scrape, read the CRM, match, build cards, upsert them into
  the ledger. Opens the CRM client in read-only mode; any write method raises `ReadOnlyError`.
- **Review app** `uv run bellhaven-sync serve`: lists cards, records decisions; Approve, Retry and
  Revert are the only paths that open a writable CRM client.

| File | One job |
|---|---|
| `src/bellhaven_sync/config.py` | Loads `config.toml` and the CRM key from the `BELLHAVEN_CRM_TOKEN` environment variable |
| `scrape.py` | Crawls the website, returns `Location` records plus a scrape report |
| `crm.py` | The CRM API client; `read_only=True` by default |
| `normalize.py` | Street, name, phone, ZIP and care-label cleanup |
| `rules.py` | Every judgment call: weights, bands, billing rule, survivor order, PO Box rule, care map, note wording, gone-from-site policy |
| `match.py` | Candidate gate, signals, score, band per (location, account) pair |
| `propose.py` | Turns matches and leftovers into cards with evidence and ordered write steps |
| `ledger.py` | SQLite schema, fingerprints, upsert, supersede, decisions, step log |
| `apply.py` | Executes an approved card's steps idempotently; retry; revert |
| `app.py`, `templates/`, `static/` | The review screens |
| `cli.py` | `run`, `serve`, `flag`, `export` commands |
| `scripts/verify.py` | Reads the CRM back and checks the expected end state |

Stack (B2): Python 3.13 pinned with uv; requests + BeautifulSoup; FastAPI + Jinja2 + uvicorn +
python-multipart (plain HTML forms, no JavaScript framework); SQLite from the standard library; pytest
and Playwright. Exact versions locked in `uv.lock`.

## 5. Inputs

### 5.1 Website (B1)

1. Fetch `/`, `/about` and `/communities?page=1`, then `?page=2…` until a page's community links repeat
   the previous page's (the site repeats its last page past the end) or 20 pages, whichever first.
2. Collect every link matching `/communities/<slug>` on every fetched page, de-duplicated by slug. The
   home page's "new this year" link is how Findlay is found.
3. Read the expected count from the home page ("35 communities"; regex `(\d+)\s+communities`).
4. Fetch each community page; parse name, street, "City, ST 12345", care offerings (one or more), the
   administrator and the phone.
5. Each request: 20 s timeout, 3 tries with 1 s / 2 s / 4 s back-off, a polite 0.2 s gap, and a
   descriptive User-Agent.
6. Scrape report: pages fetched, pages failed, locations found, expected count, warnings.
   **Complete** means zero failed pages AND found ≥ expected AND found ≥ the last complete run's count.

### 5.2 CRM read

`GET /accounts?page=N&page_size=200` until a short page; `GET /contacts` the same way. A read failure
aborts the run with no ledger changes (section 15). The operator's parent account is found by its exact
name in `config.toml` (`Bellhaven Senior Living (Parent Account)`); zero or several matches abort the run.

### 5.3 Configuration

`config.toml`: site base URL, CRM base URL, CRM browser URL template, operator parent account name,
database path. The CRM key is read only from `BELLHAVEN_CRM_TOKEN` (or a git-ignored `.env`) and
never written to the repo, the database or a log.

## 6. Normalization (`normalize.py`)

- **Street:** lower-case; drop punctuation; expand suffixes and directions token by token (`st`→street,
  `rd`→road, `dr`→drive, `ave`→avenue, `blvd`→boulevard, `ln`→lane, `pk`/`pke`→pike, `hwy`→highway,
  `ct`→court, `pl`→place, `pkwy`→parkway, `cir`→circle, `n/s/e/w`→north/south/east/west,
  `nw/ne/sw/se`→northwest…); collapse spaces. "199 Barks Rd W" and "199 Barks Road West" both become
  `199 barks road west`.
- **PO Box:** `^\s*p\.?\s*o\.?\s*box` (case-insensitive) marks a billing street as a PO Box.
- **Phone:** digits only, last 10.
- **ZIP:** first 5 digits.
- **Name tokens:** lower-case, `&`→and, drop punctuation, drop filler words (`bellhaven`, `the`, `of`,
  `at`, `senior`, `living`, `care`, `center`, `centre`, `community`, `communities`, `home`), then a set.
- **Person names:** case- and space-insensitive full-name comparison.
- **Care labels (C2c):** Short-Term Rehabilitation & Nursing → Skilled Nursing; Memory Support → Memory
  Care; Assisted Living → Assisted Living; Independent Living → Independent Living.

## 7. Matching (`match.py`, C1)

### 7.1 Accounts that never match

- Parent accounts: any account named `… (Parent Account)` or referenced as another account's `parent_id`.
- Accounts with `duplicate_of_account` set (already retired copies).
- Accounts with `chow_current_account` set (kept only for billing after an ownership change).
- Accounts the ledger lists as retired (created by this system, then reverted).

Without this rule, approving Tiffin's change of ownership would make tomorrow's run see two Tiffin
accounts and propose a duplicate.

### 7.2 Candidate gate

A (location, account) pair is compared only when the states are equal AND at least one of: same
normalized city, same ZIP, same phone, the site's administrator among the account's active contacts.
The state gate is what keeps Amberly Manor (Colorado Springs, CO) away from Amberly Manor (Hudson, OH),
and city/ZIP keep Bellhaven of New Carlisle (OH) apart from Bellhaven of Carlisle (PA).

### 7.3 Signals and score

| Signal | Kind | Score |
|---|---|---|
| Normalized street equal | identity, strong | 50 |
| Site administrator among the account's active contacts | identity, strong | 25 |
| Phone equal | identity, weak (phones agree on only 14 of 30 matched accounts) | 10 |
| ZIP equal | place | 5 |
| City equal | place | 5 |
| Name token overlap (Jaccard) | name | 0–10 |

The score orders candidates and is shown to the reviewer; the band decides what happens.

### 7.4 Bands

- **High:** normalized street equal AND (same city OR same ZIP).
- **Medium:** street not equal (or the CRM street is a PO Box) AND same city or ZIP AND at least one
  identity signal (administrator or phone). Ashtabula (PO Box billing street) is the Medium case today.
- **Low:** same city or ZIP, no identity signal, name overlap ≥ 0.5. Union Square Senior Living is the
  Low case today.

City and ZIP alone never make a match: they say "same town", not "same facility".

### 7.5 The match set for one location

- High and Medium candidates form the **match set**.
- If a candidate's non-empty care type differs from every other member's, it is left out of the set
  and listed in the evidence as "same address, different care type: not treated as a duplicate" (a
  campus may hold two real facilities). No such case exists today.
- Low candidates are shown only when the match set is empty.

## 8. From matches to cards (`propose.py`, `rules.py`)

### 8.1 Cards and sections

A card is one reviewable change: a title, the target record(s), a confidence band, before → after
per field, the evidence, and an ordered list of write steps. Up to three cards per account (C2b, D9b):
**ownership/name**, **details** (phone, street, city, state, ZIP, care type), **people** (contacts).
The app groups cards by the most significant change they carry (F1), in this order: Change of
ownership · Duplicates · Wrong parent · New accounts · Gone from the website · Possible matches (Low) ·
Renames · Details · People.

### 8.2 One matched account (match set of size 1)

Ownership/name card, when any of these differ:
- **Parent** ≠ the operator's parent → apply the billing rule (8.3).
- **Name** ≠ the site's exact name → rename (C4).
- **Status** ≠ Active → Active.

### 8.3 The billing rule (SOP, SC-2)

`needs_chow(account) = lifetime_revenue > 0 and outstanding_ar > 0` (in `rules.py`).

- **True:** a Change-of-ownership card with steps (1) create a new account from the site's values under
  the operator's parent, status Active, with a note; (2) create the site's administrator as a contact on
  the new account; (3) on the old account set `chow_current_account` = the new id and append one note
  line (D5). Nothing else on the old account changes: not its parent, name, status, phone, address,
  revenue, AR or contacts.
- **False:** re-parent the existing account directly on its ownership/name card.
- The rule is checked only when the parent must change. Accounts already under the operator that carry
  AR (Akron, Defiance, Meadville) are not moved, so the rule does not apply to them.

### 8.4 Duplicates (match set of size ≥ 2, D1, D3)

- **Survivor order:** (1) billing history (revenue > 0 or AR > 0; most revenue first); (2) already under
  the operator; (3) has contacts; (4) closest to the site record (street spelling, phone, administrator);
  (5) came from Harborview (acquired whole in 2025); (6) lowest account id, for determinism. A warning
  shows when more than one copy has billing history.
- **Duplicates card:** for each losing copy set `duplicate_of_account` = survivor and `status` = Inactive,
  append a note line, and move its active contacts to the survivor.
- The survivor then gets its own ownership/name, details and people cards like any matched account.

### 8.5 Location with no match (new account)

- **New account card:** create from the site's values (name, street, city, state, ZIP, phone, mapped
  care type) under the operator's parent, status Active, a note naming the source page and date; create
  the site's administrator as a contact (title Administrator, the site's phone, no invented email).
- When Low candidates exist (C3), each becomes a **Possible match** card ("this is the same facility":
  its ownership/name, details and people changes) and both cards show "competes with". Approving one
  supersedes the other.

### 8.6 Accounts under the operator that are not on the website (D4, D6, E3)

Only when the scrape is complete (SC-6). For each operator child left unmatched:
- **Another account at the same normalized address with a different parent:** the facility was sold.
  If `needs_chow` → set `chow_current_account` on the old account to that existing account and append
  a note on both accounts (no second account is created; one already exists under the buyer).
  Otherwise → `duplicate_of_account` = that account, Inactive, note.
- **Nothing else at the address:** status Needs Review, parent unchanged, note "Not listed on
  Bellhaven's website as of YYYY-MM-DD; no other CRM account at this address. Confirm closure or sale
  before re-parenting."

### 8.7 Details card (full sync, C2, C2a, C2c)

On every surviving matched account except old change-of-ownership accounts: propose the site's exact
phone, street, city, state and ZIP wherever the CRM string differs, and the mapped care type only when
the CRM's care type is not among the site's offerings. A PO Box billing street is never replaced.

### 8.8 People card (D9, D9a)

On every surviving matched account except old change-of-ownership accounts:
- Site administrator already an active contact → nothing.
- An active contact titled Administrator with a different name → set it `is_active=false` and create
  the site's administrator (title Administrator, site phone).
- No active Administrator → create the site's administrator.
(New accounts get their administrator on the creating card.)

### 8.9 Notes (D10)

One line appended, never overwritten: `[Bellhaven sync YYYY-MM-DD] <what>; <why>`, written on re-parent,
rename, duplicate, change of ownership (old and new accounts, D5), Needs Review and new accounts. Phone,
street and contact edits are recorded only in the app's history. A note never forms a card on its own.

### 8.10 Never touched

Harborview's and Cedar Trail's parent accounts (D7); look-alikes unless a reviewer approves a Possible
match card; accounts of other operators; old change-of-ownership accounts beyond the pointer and note;
retired duplicates beyond their duplicate fields; revenue and AR fields everywhere.

## 9. Expected end state on today's data (the answer key)

Built from `tests/fixtures/snapshot/` (the 2026-11-07 site and CRM). Ids are the last six characters.

### 9.1 Locations (35)

| Location(s) | CRM account(s) | Expected end state |
|---|---|---|
| Maplewood, New Carlisle, Wooster, Akron, Saline, Goshen, Meadville, Defiance, Toledo, Marion, Kalamazoo, Altoona | J8DYYV, RES536, MRP58M, 4R00CD, TVYRC4, NRLNME, EKKA3T, 6FNN8V, U3RMM5, 47ZZP7, JLPQ20, MF81C8 | Linked as is; details and people synced where they differ |
| Grove City, Sycamore Ridge, Arbors Dayton, Ashland | 6K5BGN, 71SZ34, NTZVDA, ZN5HX5 | Renamed to the site's exact name |
| Portsmouth | L09P4F | ZIP 45626 → 45662 |
| Ashtabula | EPSLSV | Medium match on phone, administrator, city; PO Box 517 kept |
| Chagrin Falls, Willow Creek, Chesterton | C1Q0G7 (Riverbend Manor Care Center), DS8WQZ (Sunny Acres Retirement Home), Z8DXEG (Chesterton Senior Commons) | Renamed |
| Lima | 9MB6KL (Harborview; $47,000 / $0) | Re-parented directly |
| Zanesville | 6D5VUF (Cedar Trail, "Cedar Trail of Zanesville"; $0) | Re-parented directly + renamed |
| Findlay | YCZ4YM (no parent; $22,000 / $0) | Parent set directly |
| Tiffin | WSXZZB (Cedar Trail; $84,000 / $12,400) | New Bellhaven of Tiffin account + Walt Osei; old account untouched except `chow_current_account` + note |
| Marietta | CRBLFT (Cedar Trail; $51,250 / $3,800) | New Bellhaven of Marietta account + Rosa Dietz; same as Tiffin |
| Port Clinton | KB8932 survives; LTSN24 (Harborview) | LTSN24 duplicate of KB8932, Inactive |
| Erie | YHN220 survives; SZLZHH (Harborview) | SZLZHH duplicate, Inactive |
| Monroe | AGG1S5 survives; LA9HTX (Cedar Trail), KM4FHM (Harborview) | Both duplicates, Inactive |
| Owosso | 2ZTRE6 survives; 15UA71 | 15UA71 duplicate, Inactive; Tricia Lindqvist moved to 2ZTRE6 |
| Kettering | CAE2X4 (Harborview) survives; RXS09J (no parent), ALS9GP (Cedar Trail, "Wilmington Pk") | Two duplicates, Inactive; CAE2X4 re-parented directly ($0) and renamed Bellhaven of Kettering |
| Batavia, Carlisle (PA), Amberly Manor (Hudson, OH), Bellhaven at Union Square | none | New accounts under Bellhaven with their administrators |

### 9.2 Operator children not on the website

| Account | Facts | Expected end state |
|---|---|---|
| Z2LGDM Bellhaven of Sandusky | $130,000 / $5,200; Millstone Care of Sandusky 63BVK3 at 2715 Columbus Ave | `chow_current_account` = 63BVK3 + note; note on 63BVK3; nothing else |
| 7DTQP7 Bellhaven Care Center of Alliance | $0 / $0, nothing else at the address | Needs Review + note; parent unchanged |
| HWR7QE Bellhaven of Coldwater | $0 / $0, nothing else at the address | Needs Review + note; parent unchanged |

### 9.3 People (D9)

Administrator replaced (old contact inactive, new one added): Wooster (Ray Amato → Lorna Sorensen),
Saline (Carla Castellano → Dale Green), Sycamore Ridge (Sofia Castellano → Tom Bloom), Lima (Nadia
Sandoval → Tricia Fitch). Administrator added: Meadville (Chris Pike), Grove City (Marie Vogel), Monroe
(Phil Mabry), Willow Creek (Walt Mabry), Kettering (Priya Ashby). On new accounts: Tiffin (Walt Osei),
Marietta (Rosa Dietz), Batavia (Elaine Lindqvist), Carlisle (Ken Ashby), Amberly Manor (Tom Trent),
Union Square (Phil Holloway).

### 9.4 Deliberately unchanged look-alikes

10RGY5 Amberly Manor (Colorado Springs, CO; Juniper Point), RLLJ9P Union Square Senior Living (240
Market St, New Albany; Juniper Point; administrator Dale Croft vs the site's Phil Holloway), 2DJ971
Maplewood Senior Care Center (431 Main St; Stonebridge). The Harborview and Cedar Trail parent accounts.

### 9.5 Card count today

Change of ownership 3 · Duplicates 5 · Wrong parent 4 (Lima, Zanesville, Findlay, Kettering) · New
accounts 4 · Gone from the website 2 · Possible matches 1 · Renames 7 · Details 26 (31 field changes:
14 phones, 16 street spellings, 1 ZIP) · People 9. **61 cards.**

## 10. Decision ledger and re-run safety (`ledger.py`, E1–E3)

### 10.1 Tables

- `runs(id, started_at, finished_at, complete, pages_failed, found, expected, accounts_read, warnings)`
- `cards(id, fingerprint UNIQUE, kind, section, title, band, targets, location_slug, evidence, steps,
  status, first_run, last_run, competes_with, related_rejection, flag)` with status one of pending,
  applying, approved, failed, rejected, reverted, superseded.
- `decisions(id, card_id, action, at, reviewer_note)` with action approve, reject, retry, revert.
- `steps(id, card_id, n, method, path, body, before, response_status, response_body, created_id, status, at)`.
- `retired_accounts(account_id, reason, at)`.

### 10.2 Fingerprint

`sha256` of canonical JSON holding the card kind, its target ids (or the location slug for a create),
and every write's field → new value, notes excluded. Evidence, scores, dates and note text never enter
it, so a daily re-run of the same finding yields the same fingerprint.

### 10.3 Each run

1. A CRM read failure aborts the run; the ledger is unchanged.
2. For every card built: fingerprint already approved, rejected or reverted → skip (never re-proposed);
   already pending → refresh its evidence and `last_run`; new → insert as pending, linking any earlier
   rejected card with the same kind and target so the reviewer sees "you rejected X on DATE".
3. Only a **complete** run supersedes pending cards it did not rebuild; an incomplete run supersedes
   nothing and skips the gone-from-the-website section, and the app shows a red banner with the reason.
4. A field already at its target value never forms a write, so a card disappears once its change is in
   the CRM by any route.

### 10.4 Reviewer flags

`bellhaven-sync flag <card-id> "<reason>"` (or `--from-json`) sets a card's flag. Flagged cards show a
yellow banner and sort first within their section. The blind model check (section 17) uses this.

## 11. Applying an approved card (`apply.py`, E5, F2)

- **Claim:** `UPDATE cards SET status='applying' WHERE id=? AND status='pending'`; zero rows means
  "already decided" (a double click never writes twice).
- **Steps in order.** For a PATCH: GET the record; if every field already holds its target, mark the step
  done; if any field holds neither the card's captured before-value nor the target, stop as **stale**
  ("the CRM changed since this card was made; re-run"); otherwise save the before-values, PATCH, and check
  the response echoes the targets. A note step reads the current note and appends its line once.
- **Creates:** a created id is stored the moment it returns and reused on retry. If a create's outcome is
  unknown (timeout), search for an account this system created (`created_by_candidate=true`) with the
  same name and street before creating again. Later steps refer to `$step<N>.id`.
- **Failure:** the card becomes failed with "n of m steps done" and a Retry button that resumes at the
  first unfinished step. Creates are never retried automatically.
- **Revert:** reverse order; every patched field gets its before-value back (notes included); created
  accounts become Inactive with a note "created in error; reverted YYYY-MM-DD" and join
  `retired_accounts`; created contacts become inactive; moved contacts move back; deactivated contacts
  are reactivated. The card becomes reverted, which counts as rejected (never re-proposed).

## 12. CRM API contract

- Endpoints: `GET/POST /accounts`, `GET/PATCH /accounts/{id}`, `GET/POST /contacts`,
  `GET/PATCH /contacts/{id}`, `GET /me`. No delete, no merge.
- The published API description gives no request-body shape for POST or PATCH. Before any real write,
  the shapes are learned with requests that cannot write:
  - `PATCH /accounts/does-not-exist` with every text field set to the integer 123. Body validation runs
    before the handler, so the 422 lists every known text field; a valid body would hit a 404 on the
    missing id. Numeric fields (revenue, AR) are never sent.
  - `POST /accounts` with `{"name": 123}`. The 422 lists the type error and every required field; it
    cannot create because `name` is invalid.
  - The same two probes for contacts.
  Findings go in `docs/api-notes.md`. Any 2xx from a probe stops the build and is reported.
- Status values: Active, Inactive, Needs Review. Page size ≤ 200. Bearer key on every request.
- Writes are never retried automatically, except an idempotent PATCH after a connection error.

## 13. Review app (`app.py`, F1–F3)

- **Queue (`/`):** run banner (last run time, locations found / expected, accounts read, cards by section,
  warnings in red); a location filter; sections in the 8.1 order with counts; each card a `<details>`
  block. Collapsed: section tag, facility, band, one-line summary ("Move to Bellhaven: new account; old
  kept for billing ($84,000 revenue, $12,400 AR)"). Expanded: before → after per field with changes
  highlighted; site record and CRM record side by side; signals with ✓/✗ and the score; ruled-out
  candidates with the reason; the billing-rule reasoning where it applies; "what will be written" (each
  API call); competing and earlier-rejected cards; a "View in CRM" link per account; Approve and Reject
  with an optional reviewer note.
- **One card at a time** (F3): no bulk actions.
- **After a click:** the page returns to the same section with a message ("Approved: 3 of 3 writes done"
  or "Failed at step 2 of 3: <API message> [Retry]").
- **History (`/history`):** every decision newest first, its steps with request, response and
  before-values; Revert on approved cards.
- **Binding:** 127.0.0.1 by default; `--host` for a private-network address when viewed from another
  machine. No login (local tool).
- **Look and feel:** calm, dense, legible, readable at screen-recording size; one small CSS file;
  system fonts. Reviewed with the installed impeccable and taste-skill passes before the code review.

## 14. Schedule and operations (E4)

- `deploy/crontab`: `30 10 * * * cd /opt/bellhaven-crm-sync && flock -n .run.lock uv run bellhaven-sync
  run >> logs/run.log 2>&1` (06:30 US Eastern in winter). `flock` stops overlapping runs.
- `deploy/github-actions-daily.yml`: the same job as a scheduled workflow, kept outside
  `.github/workflows/` so it never runs; the README explains that in Actions the ledger would persist as
  a committed file on a state branch or a release asset.
- Logs: one line per run with counts; warnings verbatim.

## 15. Errors

| Situation | Behaviour |
|---|---|
| A website page fails after 3 tries | Run continues; marked incomplete; no gone-from-the-website cards; nothing superseded; red banner |
| Found fewer locations than the home page states or than the last complete run | Same as above |
| CRM read fails or the operator's parent is not found exactly once | Run aborts; ledger untouched; exit code 1 |
| A write returns 4xx/5xx | Card failed with the API message and "n of m done"; Retry |
| CRM changed since the card was built | Card stops as stale; the reviewer re-runs the pipeline |
| Two clicks on Approve | Second click sees "already decided" |
| A probe write unexpectedly succeeds | Build stops; reported; reverted by hand from the before-values |

## 16. Tests (G1)

pytest against an in-memory fake CRM loaded from `tests/fixtures/snapshot/`; the real CRM copy is never touched.
- `test_normalize`: abbreviation pairs from today's data (NW/Northwest, Rd W/Road West, Pk/Pike, St/Street).
- `test_scrape`: saved HTML yields 35 locations including Findlay via the home page; a missing page and a
  short count mark the report incomplete.
- `test_answer_key`: the full run produces exactly the section 9 cards (kind, targets, key values).
- `test_billing_rule`: Tiffin and Marietta take the new-account route; Lima and Findlay re-parent
  directly; Akron, Defiance and Meadville are untouched; Sandusky points at 63BVK3.
- `test_lookalikes`: 10RGY5, RLLJ9P and 2DJ971 never receive a write without an approved Possible match.
- `test_rerun`: approve everything on the fake CRM, run again, zero new cards; reject some, run again,
  none come back; pending cards are refreshed, never duplicated.
- `test_guard`: drop one page, no gone-from-the-website cards, nothing superseded.
- `test_apply`: a failure injected at step 2 of 3 leaves "1 of 3"; Retry finishes without a second create;
  a stale record stops the card.
- `test_revert`: every card type reverts to the exact before state.
- `test_readonly`: the pipeline's client raises on every write method.
- `e2e/test_review_flow.py` (Playwright): start the app on the fake CRM, approve one card, reject one,
  run again, the rejected card stays gone.

## 17. Checking the answers before anything is approved (H1, H2)

1. **Blind model check:** a fresh GPT-6-Astra session receives only the brief, the scraped locations and
   the CRM snapshot (not this design) and returns, per location and per leftover account, its outcome and
   reasoning as JSON. A script compares it with the cards; every disagreement becomes a flag (10.4).
2. **Second opinion** on the judgment calls (Sandusky, Kettering, Owosso, Alliance, Coldwater, the note on
   old change-of-ownership accounts) from a different model.
3. **Same-facility probabilities** for every candidate pair from an evaluator, compared with the bands
   during development only; never shipped, never decides.
4. **Gage clicks every card**, flagged ones first (H1).
5. **`scripts/verify.py`** reads every account and contact back and prints PASS/FAIL per row of section 9.
   Its output goes in the writeup.

## 18. Deliverables

- `README.md`: what it is, quick start (`uv sync`, set the key, `uv run bellhaven-sync run`, `serve`), how
  it works in one screen, schedule, tests.
- `WRITEUP.md`: matching approach; every judgment call with its evidence; the billing rule handling;
  how AI tools were used (Claude Code built to my spec; GPT checked every match blind and I settled the
  disagreements; tests pin the traps; I approved every change); what I'd build next; time by session;
  the verifier's output.
- The form's writeup box: ~250–300 plain-text words (matching ~120, AI ~80, next ~60) ending with the
  link to `WRITEUP.md` (K2). "Next" leads with Medicare's public ownership files (K3).
- Demo video (I2): recorded with Cap at the start of the review session. 0:00 run banner; 0:15 Tiffin
  change of ownership (Cedar Trail, $84,000 / $12,400) approved; 0:50 Monroe duplicates; 1:15 Union
  Square look-alike rejected (different street and administrator), the new account approved; 1:40 the
  CRM browser shows the new Bellhaven of Tiffin and the old account's pointer. Uploaded unlisted to
  YouTube by Gage.
- Time (A2, K4): Gage's focused time from 19:33:57Z, by session, nearest quarter hour, "X hours".
- Walkthrough preparation (J2): a one-page code tour, a cheat sheet of about 8 likely live changes with
  the one place each lands, and a 20-minute mock with a surprise change.

## 19. Timeline (UTC)

| When | Who | What |
|---|---|---|
| 2026-11-07 evening | agents | Spec review, tools pass, plan, build, tests, blind check, internal review |
| When the build is verified | Gage, ~60 min | Read the flags; record the video; click all 61 cards; verifier |
| Next | Gage, ~30 min | Edit the writeup; repo public; upload video; submit the form |
| Before 2026-11-09T20:15Z | | Submission deadline |
| Before the interview | Gage, ~30 min | Code tour, cheat sheet, mock walkthrough |

## 20. Risks and concerns

- **Time:** Gage's focused time already exceeds the brief's ~2 hours; reported honestly.
- **Full sync (C2, against the recommendation):** 31 detail writes, 16 of them only change abbreviation
  spelling, in both directions; a grader may read them as churn.
- **Note on old change-of-ownership accounts (D5, against the recommendation):** the SOP says "leave the
  existing account exactly as it is"; the writeup must explain the note.
- **Full contact sync (D9, against the recommendation):** 19 contact writes that are not the brief's focus.
- **Sandusky (D4):** the SOP says "create a new account"; we point at the buyer's existing account to
  avoid a duplicate; explained in the writeup.
- **Live change in the estate window (J1, against the recommendation):** hook messages and receipts appear
  on the screen share; a one-line explanation is rehearsed.
- **Write shapes unknown until the probes:** the first real create happens on camera (Tiffin).

## Appendix A. Decision log

| Id | Decision |
|---|---|
| A1 | Deadline: submit before 2026-11-09T20:15Z |
| A2 | Time counted: Gage's focused minutes only |
| A3 | AI story: directing and checking; one line on the personal agent setup |
| A4/A5 | Repo `ngagemartin24/bellhaven-crm-sync`, private while building, public at submit |
| A6 | Runs on a Linux server viewed from the laptop; tested macOS fallback |
| A7 | Claude builds; GPT reviews code and checks matches blind |
| B1 | Crawl the whole site; warn when the count differs from the home page |
| B2 | FastAPI + HTML templates; requests + BeautifulSoup; SQLite; uv + Python 3.13 |
| C1 | Explainable rules and score; no AI in the pipeline |
| C2 | Full sync of phone, street, city, state, ZIP, care type |
| C2a | Never replace a PO Box billing street |
| C2b | Ownership/name card and details card per account |
| C2c | Care type: keep if among the site's offerings, else the first offering mapped |
| C3 | Look-alikes appear as Low "possible match" cards competing with "create new" |
| C4 | Rename all four small name variants |
| D1 | Survivor: billing history first |
| D2 | Kettering survivor CAE2X4, re-parented and renamed |
| D3 | Contacts on a retired copy move with the duplicate fix |
| D4 | Sandusky points at the existing Millstone account |
| D5 | Old change-of-ownership accounts get the pointer and a note |
| D6 | Alliance and Coldwater: Needs Review + note |
| D7 | Harborview parent account left as is |
| D8 | New accounts take the site's values |
| D9/D9a/D9b | Full contact sync; replaced administrators kept inactive; separate People card |
| D10 | Dated note line when an account's meaning changes |
| E1/E2 | Exact-change fingerprint; pending cards upserted; unsupported ones superseded |
| E3 | An incomplete scrape holds back gone-from-the-website cards |
| E4 | Cron as the schedule; inert Actions example |
| E5 | Multi-write cards run as recorded steps with safe retry |
| F1 | Queue grouped by kind of change |
| F2 | Revert button |
| F3 | One card at a time |
| G1 | Full test set including one browser run |
| H1/H2 | Gage clicks every card after the blind check; verifier afterwards |
| I1/I2 | Video on unlisted YouTube; Tiffin approve, Union Square reject |
| J1/J2 | Live change in the usual window; code tour, cheat sheet, mock |
| K1–K4 | One text box; ~250–300 words; Medicare files lead "next"; clock from 19:33:57Z |
