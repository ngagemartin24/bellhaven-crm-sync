# Bellhaven CRM sync: design

Status: v5, 2026-11-07, revised after four independent review rounds by GPT-6-Astra and a spec/plan
consistency check (dispositions in Appendix B).

> **As built (2026-11-08).** This spec was written before the build and is kept as written; Appendix B records
> every later review. Where the real run differed: the first real approvals were Marietta and Kettering, not
> the Grove City and Batavia cards planned in section 2; Gage made two decisions in the app himself and a
> browser script clicked the other 40 through the same buttons on his instruction (section 15); the demo is a
> captioned recording of the real CRM, narrated with an ElevenLabs clone of his voice. The planned
> `docs/api-notes.md` and repo cheat sheet were not kept; what the real CRM showed is in docs/TESTING.md
> ("Found in real use"). Author: Gage Martin, built with Claude Code. Every decision traces to Appendix A (ids like `D4`).

## 1. Purpose

Long-term-care facilities change owners all the time, and each sale, rebrand or consolidation can
silently break the link between a facility and its parent company in the CRM. This system keeps one
operator's links correct: it reads Bellhaven Senior Living's public website (the truth about what
Bellhaven owns today), compares it with the CRM, and proposes fixes with their evidence. A person approves
or rejects each fix in a small local app; only an approval writes to the CRM. It runs daily, and a second
run never re-asks a decided question.

The deliverable is the corrected CRM copy plus the system that produced it.

## Clarifications

### Session 2026-11-07

Clarify pass on v2: no open question needed the candidate; six assumptions settled by the builder, each
applied in the section named.

- Assumed: each card kind has one section. Change of ownership: every card whose old account keeps billing
  and gets only `chow_current_account`, including a sold facility not on the website (Sandusky). Duplicates:
  every duplicate card, including a sold facility without billing (8.6). Wrong parent: an ownership/name card
  that changes `parent_id`, even if it also renames. Renames: an ownership/name card that changes only the
  name or status. Gone from the website: Needs Review cards (8.1).
- Assumed: a new account's care type is the site's first listed offering, mapped (8.5).
- Assumed: an operator account not on the website that is already Inactive gets no card; a person has already
  retired it (8.6).
- Assumed: the first real writes, off camera, are the Grove City rename (one PATCH) and the Batavia new account
  with its administrator (two POSTs); `parent_name` is derived by the CRM from `parent_id` and never written
  (12).
- Assumed: after the review, the pipeline runs once more against the real CRM and must propose zero cards;
  that output goes in the writeup next to the verifier's (17).
- Assumed: in a group of alternatives, the reviewer rejects the look-alike before approving the new account,
  so the rejection is his own click on camera; either order ends in the same CRM state (10.6, 18).

## 2. Success criteria

- **SC-1** Every website location ends in its expected state (section 9), read back by `scripts/verify.py`,
  and every account section 9 does not name is byte-for-byte unchanged in the fields the system can write.
- **SC-2** Every parent move follows the billing rule. Revenue > 0 AND outstanding AR > 0: the old account
  is left exactly as it is except `chow_current_account`, which points to the facility's current account
  under the right parent; otherwise the account is re-parented directly. The current account is newly
  created, except where one already exists under the buyer at the same full address (Sandusky, a
  disclosed exception, D4).
- **SC-3** No look-alike account changes unless a reviewer approves its Possible-match card; the planned
  review rejects the one that exists today.
- **SC-4** Only a reviewer's click in the review app (Approve, Retry, Check again) writes to the CRM. The pipeline's CRM client
  refuses writes, and a test proves it.
- **SC-5** A second run proposes no change that was already approved or rejected, however the cards are
  regrouped.
- **SC-6** An incomplete scrape never proposes, and never lets anyone approve, a "gone from the website"
  change.
- **SC-7** Each judgment call is one edit in `rules.py`, pinned by a test, so a live change is edit, test, show.
- **SC-8** Submitted before 2026-11-09T19:30Z with honest time, a ~2-minute narrated video, a public repo
  and a ~250–300 word writeup.

## 3. Scope

In: the scraper, matching, card generation, decision ledger, review app, apply and retry, daily schedule,
tests, a read-back verifier, the writeup, the demo video, walkthrough preparation.

Out (named in "what I'd build next", not built): an undo button (F2), administrator-contact upkeep on
existing accounts (D9), rewriting street spellings that differ only by abbreviation (C2), other operators,
Medicare ownership-file matching, learning from decisions, change-triggered runs, missed-run alerts, a
hosted deployment, any LLM call inside the pipeline (A3, C1).

## 4. Architecture

Two programs share one SQLite file (`decisions.db`).

```
website ─▶ scrape ─┐
                   ├─▶ match ─▶ propose ─▶ decisions.db ─▶ review app ─▶ [Approve] ─▶ apply (check, then write) ─▶ CRM API
CRM API ─(read)────┘                         ▲                                                                       │
                                             └──────────────────── step log, before-values ◀────────────────────────┘
```

- **Pipeline** `uv run bellhaven-sync run`: scrape, read the CRM, match, build cards, record them. Its CRM
  client is read-only; every write method raises `ReadOnlyError`.
- **Review app** `uv run bellhaven-sync serve`: lists cards and locations and records decisions. Approve,
  Retry and Check again are the only paths that open a writable client, and every write is preceded by a
  check that the record still holds what the reviewer saw (11).

| File | One job |
|---|---|
| `src/bellhaven_sync/config.py` | Loads `config.toml`; the CRM key from `BELLHAVEN_CRM_TOKEN` |
| `scrape.py` | Crawls the website, validates each page, returns `Location` records and a scrape report |
| `crm.py` | The CRM API client; `read_only=True` by default |
| `normalize.py` | Street, name, phone, ZIP and care-label cleanup |
| `rules.py` | Every judgment call: eligibility, candidate gate, weights, bands, billing rule, survivor order, PO Box rule, synced detail fields, care map, note wording, the gone-or-sold policy (8.6), section per card kind |
| `match.py` | Computes signals and scores, applies the gate and bands from `rules.py`, forms duplicate groups, classifies each location |
| `propose.py` | Turns matches and leftovers into changes and cards with evidence and ordered write steps |
| `ledger.py` | SQLite schema, change keys, decisions, operations, reservations, alternative groups |
| `apply.py` | Execute an approved card's steps, each after its check; Retry; Check again for uncertain creates |
| `app.py`, `templates/`, `static/` | The review screens |
| `cli.py` | `run`, `serve`, `flag` |
| `scripts/verify.py` | Reads the CRM back; checks section 9 and the unchanged-everything-else invariants |

Stack (B2): Python 3.13 pinned with uv; requests + BeautifulSoup; FastAPI + Jinja2 + uvicorn +
python-multipart (plain HTML forms, no JavaScript framework); SQLite from the standard library; pytest,
Hypothesis and Playwright for tests. Exact versions locked in `uv.lock`.

## 5. Inputs

### 5.1 Website (B1)

1. Fetch `/`, `/about` and `/communities?page=1`, then `?page=2…` until a page's community links repeat the
   previous page's (the site repeats its last page past the end). Reaching 20 pages without that repeat
   marks the run incomplete.
2. Collect every `/communities/<slug>` link on every fetched page, de-duplicated by slug (the home page's
   "new this year" link is how Findlay is found).
3. Read the stated count from the home page (`(\d+)\s+communities`).
4. Fetch each community page and parse name, street, "City, ST 12345", one or more care offerings,
   administrator and phone. A page missing any of name, street, city, state or ZIP is **invalid**.
5. Requests: 20 s timeout, 3 tries with 1 s / 2 s / 4 s back-off, 0.2 s gap, descriptive User-Agent.
6. The run is **complete** when no page failed, the directory ended by repeating, every community page is
   valid, and the number of locations equals the home page's stated count. A real sale that takes the site
   from 35 to 34 is complete as soon as the home page says 34 (F13).

### 5.2 CRM read

`GET /accounts?page=N&page_size=200` and `GET /contacts` the same way, until a short page. A read failure
aborts the run with no ledger change. The operator's parent is found by its exact name in `config.toml`
(`Bellhaven Senior Living (Parent Account)`); zero or several matches abort the run.

### 5.3 Configuration

`config.toml`: site base URL, CRM base URL, CRM browser URL template, operator parent name, database
path. The CRM key comes only from `BELLHAVEN_CRM_TOKEN` (or a git-ignored `.env`) and is never written to
the repo, the database or a log.

## 6. Normalization (`normalize.py`)

- **Street:** lower-case; drop punctuation; expand tokens: `rd`→road, `dr`→drive, `ave`→avenue,
  `blvd`→boulevard, `ln`→lane, `pk`/`pke`→pike, `hwy`→highway, `ct`→court, `pl`→place, `pkwy`→parkway,
  `cir`→circle, `n/s/e/w`→north/south/east/west, `nw/ne/sw/se`→northwest…; `st` becomes `street` when it
  is the last token or is followed only by a direction, and `saint` otherwise ("45 St Lawrence Dr" →
  `45 saint lawrence drive`; "341 Chestnut St" → `341 chestnut street`). "199 Barks Rd W" and "199 Barks
  Road West" both become `199 barks road west`.
- **Abbreviation-equivalent:** two streets whose normalized forms are equal. They match, and the CRM's
  spelling is never rewritten (C2 revised).
- **Full address:** normalized street + lower-cased city + state + 5-digit ZIP.
- **PO Box:** `^\s*p\.?\s*o\.?\s*box` (case-insensitive) on a billing street.
- **Phone:** digits only, last 10. **ZIP:** first 5 digits.
- **Name tokens:** lower-case, `&`→and, drop punctuation, drop filler (`bellhaven`, `the`, `of`, `at`,
  `senior`, `living`, `care`, `center`, `centre`, `community`, `communities`, `home`); a set.
- **Person names:** case- and space-insensitive full-name comparison.
- **Care labels (C2c):** Short-Term Rehabilitation & Nursing → Skilled Nursing; Memory Support → Memory
  Care; Assisted Living and Independent Living unchanged.

## 7. Matching (`match.py`, C1)

### 7.1 One eligibility policy (F8)

`eligible(account)` in `rules.py` is used by matching, by the leftover pass, and by the successor search.
An account is **not** eligible when it is a parent account (named `… (Parent Account)` or referenced as
another account's `parent_id`), has `duplicate_of_account` set, has `chow_current_account` set, or is
reserved by an unfinished operation (10.5). Without this, approving Tiffin's change of ownership would
make tomorrow's run see two Tiffin accounts.

### 7.2 Candidate gate

A (location, eligible account) pair is compared when the states are equal AND at least one of: same city,
same ZIP, same phone, the site's administrator among the account's active contacts. A pair that fails only
the state test but shares the full street and ZIP, or both phone and administrator, is a **conflict**
(possible state typo), never silently ignored (F16). The state test keeps Amberly Manor (Colorado Springs,
CO) apart from Amberly Manor (Hudson, OH); city and ZIP keep New Carlisle (OH) apart from Carlisle (PA).

### 7.3 Signals and score

| Signal | Kind | Score |
|---|---|---|
| Normalized street equal | identity, strong | 50 |
| Site administrator among the account's active contacts | identity, strong | 25 |
| Phone equal | identity, weak (agrees on 14 of 31 matched accounts) | 10 |
| ZIP equal | place | 5 |
| City equal | place | 5 |
| Name token overlap (Jaccard) | name | 0–10 |

The score (0–105) orders candidates and is shown as points, never as a percentage. The band decides.

### 7.4 Bands

- **High:** normalized street equal AND (same city OR same ZIP).
- **Medium:** street not equal (or the CRM street is a PO Box) AND same city or ZIP AND at least one identity
  signal (administrator or phone). Ashtabula is today's Medium.
- **Low:** same city or ZIP, no identity signal, name overlap ≥ 0.5. Union Square Senior Living is today's
  Low (Maplewood Senior Care Center is also Low but suppressed, because Maplewood has a High).

City and ZIP alone never make a match: they say "same town", not "same facility".

### 7.5 The match for one location (F11, F12)

- **High candidates sharing one full address** form the duplicate group. In a group of two or more, a member
  whose non-empty care type is not among the site's offerings, while another member's is, leaves the group
  and is shown as a conflict ("same address, different care type").
- A **Medium** candidate is the match only when there is no High. A Medium beside a High is a conflict shown
  in the evidence, never merged as a duplicate.
- **Low** candidates appear only when there is no High or Medium.
- Conflicts become a **Needs attention** card (8.8) and block a create for that location.

## 8. From matches to changes and cards (`propose.py`, `rules.py`)

### 8.1 Changes, cards, sections

A **change** is one durable, decidable unit: `set_field` (one field to one value on one record);
`retire_duplicate` (loser → survivor: `duplicate_of_account` = survivor **and** `status` = Inactive **and**
the loser's active contacts move, never split); `set_chow` (old account → its successor, an existing account
or the one created for a location); `create_account` (one account for one location, with its administrator
contact). Each change has a stable **change key** (10.2). A **card** groups the changes a reviewer judges together and shows before → after,
the evidence and the ordered write steps. Sections, in order (F1): Change of ownership · Duplicates ·
Wrong parent · New accounts · Gone from the website · Possible matches · Renames · Details · Needs attention.
Each card kind has exactly one section: a card that sets only `chow_current_account` on a billing account is
Change of ownership (including Sandusky, 8.6); a duplicate card is Duplicates (including a sold facility
without billing, 8.6); an ownership/name card that changes `parent_id` is Wrong parent even when it also
renames; one that changes only name or status is Renames; a Needs Review card is Gone from the website.

### 8.2 One matched account

An ownership/name card when any of: **parent** ≠ the operator's parent (billing rule, 8.3); **name** ≠ the
site's exact name (C4); **status** ≠ Active.

### 8.3 The billing rule (SOP, SC-2)

`needs_chow(account) = lifetime_revenue > 0 and outstanding_ar > 0`, checked only when the parent must change.

- **True:** a Change-of-ownership card. Steps: (1) create the new account from the site's values under the
  operator's parent, status Active, with a note naming the old account and why it was kept; (2) create the
  site's administrator as a contact on the new account; (3) set `chow_current_account` on the old account to
  the new id. **Nothing else on the old account changes**: not its parent, name, status, note, phone,
  address, revenue, AR or contacts (D5 revised).
- **False:** re-parent the existing account directly on its ownership/name card.
- Akron, Defiance and Meadville carry AR but are already under Bellhaven, so they get no parent or CHOW
  change.

### 8.4 Duplicates (D1, D3)

- **Survivor order:** (1) billing history (revenue > 0 or AR > 0; most revenue first); (2) already under the
  operator; (3) has contacts; (4) closest to the site record (street spelling, phone, administrator);
  (5) came from Harborview (acquired whole in 2025); (6) lowest account id. A warning shows when more than
  one copy has billing history.
- **Duplicates card:** one `retire_duplicate` change per losing copy: `duplicate_of_account` = survivor,
  `status` = Inactive and a note line, and its active contacts move to the survivor (today: Tricia
  Lindqvist, Owosso).
- The survivor gets its own ownership/name and details cards like any matched account.

### 8.5 A location with no match

- **New account card:** create from the site's values (name, street, city, state, ZIP, phone, and the care
  type mapped from the site's first listed offering) under the operator's parent, Active, with a note naming the source page and date, plus the site's
  administrator as a contact (title Administrator, the site's phone, no invented email).
- When Low candidates exist (C3), each becomes a **Possible match** card ("this is the same facility": its
  ownership/name and details changes). The New account card and its Possible-match cards form one
  **alternative group** (10.6); once an approved choice finishes, the group closes.

### 8.6 Operator accounts not on the website (D4, D6, E3, F14)

Only built from a complete run, and only approvable while the latest run is complete (SC-6). For each
eligible operator child left unmatched and not already Inactive (an Inactive one was retired by a person and
gets no card), look at the accounts under a different parent at the same full address:
- **Exactly one, Active, eligible, with a compatible care type:** the buyer's current account; the facility
  was sold. If `needs_chow`: a Change of ownership card that sets only `chow_current_account` on the old
  account to it and appends a note on the buyer's account (Sandusky today). Otherwise this is duplicate
  consolidation, not a parent move: two records describe one facility and the current owner's record
  survives, so the old copy is retired as its duplicate (`retire_duplicate`, note).
- **None at all:** a Gone-from-the-website card: status Needs Review, parent unchanged, note "Not listed on
  Bellhaven's website as of YYYY-MM-DD; no other CRM account at this address. Confirm closure or sale
  before re-parenting." (Alliance and Coldwater today.)
- **Some, but not exactly one Active, eligible, compatible account** (inactive, retired, a different care
  type, or several): a Needs attention card describing them; no write.
- **A counterpart reserved by an unfinished operation:** no card this run.

### 8.7 Details card (C2 revised, C2a, C2c)

On each surviving matched account except old change-of-ownership accounts, propose the site's value where
it differs for: phone; street, only when not abbreviation-equivalent and the CRM street is not a PO Box;
city; state; ZIP; and the mapped care type, only when the CRM's care type is not among the site's offerings.

### 8.8 Needs attention card

A location with a conflict (7.2, 7.5), an invalid page, or an alternative group rejected in full (10.6) gets
one card describing it, with no writes. Its single change is `attention` (record = the location slug or
account id, value = a hash of what it describes), so each subject has its own key. It can be acknowledged
(recorded as decided on that key, so the same situation is not raised again) but never approved into a write.

### 8.9 Notes (D10)

One line appended, never overwritten: `[Bellhaven sync YYYY-MM-DD] <what>; <why>`. Written on: re-parent,
rename, duplicate losers, new accounts (including change-of-ownership replacements), Needs Review, and the
buyer's account in 8.6. **Never on an old change-of-ownership account.** Phone, street and ZIP edits are
recorded only in the app's history. A note is never a change on its own.

### 8.10 Write invariants

- Revenue and AR are never written anywhere.
- An old change-of-ownership account receives exactly one write: `chow_current_account`.
- A duplicate loser receives only `duplicate_of_account`, `status` and a note line.
- Harborview's and Cedar Trail's parent accounts receive no write (D7).
- A look-alike receives writes only through an approved Possible-match card.
- Every account no approved card names receives no write; on an account a card names, only the fields that
  card lists change. `scripts/verify.py` checks both against section 9 and the snapshot.

## 9. Expected end state on today's data (the answer key)

Built from `tests/fixtures/snapshot/` (the 2026-11-07 site and CRM). Ids are the last six characters.
Astra's independent derivation (Appendix B) agrees with every row.

### 9.1 Locations (35)

| Location(s) | CRM account(s) | Expected end state |
|---|---|---|
| Maplewood, New Carlisle, Wooster, Akron, Saline, Meadville, Toledo, Kalamazoo, Altoona | J8DYYV, RES536, MRP58M, 4R00CD, TVYRC4, EKKA3T, U3RMM5, JLPQ20, MF81C8 | Unchanged (street spellings differ only by abbreviation) |
| Goshen, Defiance, Marion | NRLNME, 6FNN8V, 47ZZP7 | Phone set to the site's |
| Grove City | 6K5BGN | Renamed to Bellhaven Rehabilitation & Nursing of Grove City |
| Sycamore Ridge, Arbors Dayton, Ashland | 71SZ34, NTZVDA, ZN5HX5 | Renamed to the site's exact name; phone set |
| Portsmouth | L09P4F | ZIP 45626 → 45662 |
| Ashtabula | EPSLSV | Unchanged; Medium match on phone, administrator and city; PO Box 517 kept |
| Chagrin Falls, Willow Creek | C1Q0G7 (Riverbend Manor Care Center), DS8WQZ (Sunny Acres Retirement Home) | Renamed; phone set |
| Chesterton | Z8DXEG (Chesterton Senior Commons) | Renamed |
| Lima | 9MB6KL (Harborview; $47,000 / $0) | Re-parented directly; phone set |
| Zanesville | 6D5VUF (Cedar Trail; "Cedar Trail of Zanesville"; $0) | Re-parented directly + renamed; phone set |
| Findlay | YCZ4YM (no parent; $22,000 / $0) | Parent set directly; phone set |
| Tiffin | WSXZZB (Cedar Trail; $84,000 / $12,400) | New Bellhaven of Tiffin account + Walt Osei; old account unchanged except `chow_current_account` |
| Marietta | CRBLFT (Cedar Trail; $51,250 / $3,800) | New Bellhaven of Marietta account + Rosa Dietz; old account unchanged except `chow_current_account` |
| Port Clinton | KB8932 survives; LTSN24 (Harborview) | LTSN24 duplicate of KB8932, Inactive; KB8932 phone set |
| Erie | YHN220 survives; SZLZHH (Harborview) | SZLZHH duplicate, Inactive; YHN220 phone set |
| Monroe | AGG1S5 survives; LA9HTX (Cedar Trail), KM4FHM (Harborview) | Both duplicates, Inactive; AGG1S5 phone set |
| Owosso | 2ZTRE6 survives; 15UA71 | 15UA71 duplicate, Inactive; Tricia Lindqvist moved to 2ZTRE6 |
| Kettering | CAE2X4 (Harborview) survives; RXS09J (no parent), ALS9GP (Cedar Trail, "Wilmington Pk") | Two duplicates, Inactive; CAE2X4 re-parented directly ($0), renamed Bellhaven of Kettering, phone set |
| Batavia, Carlisle (PA), Amberly Manor (Hudson, OH), Bellhaven at Union Square | none | New accounts under Bellhaven, each with its administrator |

### 9.2 Operator accounts not on the website

| Account | Facts | Expected end state |
|---|---|---|
| Z2LGDM Bellhaven of Sandusky | $130,000 / $5,200; Millstone Care of Sandusky 63BVK3 at the same full address, Skilled Nursing | `chow_current_account` = 63BVK3 and nothing else; note on 63BVK3 |
| 7DTQP7 Bellhaven Care Center of Alliance | $0 / $0, nothing else at the address | Needs Review + note; parent unchanged |
| HWR7QE Bellhaven of Coldwater | $0 / $0, nothing else at the address | Needs Review + note; parent unchanged |

### 9.3 Unchanged on purpose

10RGY5 Amberly Manor (Colorado Springs, CO), RLLJ9P Union Square Senior Living (240 Market St; Dale Croft vs
the site's Phil Holloway), 2DJ971 Maplewood Senior Care Center (431 Main St), all six parent accounts, the
70 accounts no row names. Administrator mismatches on existing accounts (Wooster, Saline, Sycamore Ridge,
Lima) and missing administrators (Meadville, Grove City, Monroe, Willow Creek, Kettering) are shown as
evidence and listed under "next", not written (D9 revised).

### 9.4 Counts today

**42 cards**: Change of ownership 3 · Duplicates 5 · Wrong parent 4 (Lima, Zanesville, Findlay, Kettering) ·
New accounts 4 · Gone from the website 2 · Possible matches 1 · Renames 7 · Details 16 (15 phones, 1 ZIP).
The intended review approves **41** and rejects **1** (the Union Square Possible match). End state: 127
accounts (121 + 6 created), 73 contacts (67 + 6 created; one moved).

## 10. Ledger and re-run safety (`ledger.py`, E1–E3)

### 10.1 Tables

- `runs(id, started_at, finished_at, complete, reason, pages_failed, found, stated, accounts_read,
  locations_json, verdicts_json)`: `verdicts_json` holds every location's and leftover account's verdict (13).
- `changes(change_key PRIMARY KEY, kind, record, field, value, location_slug, first_run, last_run)`
- `decisions(change_key, decision, card_id, at, reviewer_note)`: decision is approved or rejected.
- `cards(id, change_set, kind, section, title, band, change_keys, evidence, steps, status, alt_group, flag,
  built_run)`: `id` is a fresh instance id; `change_set` is the hash of the sorted change keys. A pending card
  whose `change_set` is unchanged keeps its instance; a new review (after a discard, say) gets a new instance,
  and the old one stays in history.
- `operations(id, card_id, status, started_at, finished_at)`, `steps(op_id, n, method, path, body, expect,
  before, intent_at, response_status, response_body, created_id, status)`
- `reservations(op_id, account_id | location_slug)`, `alt_groups(id, location_slug, status, owner_op)`

### 10.2 Change keys (F6)

A change key is `sha256` of canonical JSON: change kind + the stable record identity (account id, contact
id, or for a create the location slug) + field + value (or the relation target). Notes, evidence, scores
and dates never enter it. A decision is recorded **per change key**, never per card, so regrouping cannot
revive a decided change: if Goshen's phone-and-street card is rejected and the street is later fixed by
someone else, the phone change keeps its rejected key and is not proposed again. Relations are single
changes: `retire_duplicate` and `set_chow` each carry all of their effects, so no decision can leave half a
retirement behind (a rejected Monroe retirement whose survivor later changes comes back whole, pointer and
Inactive together). Cards and their write steps are built only from changes that survive the decision
filter (10.3 step 2), so a rejected field can never ride along in another card's PATCH.

### 10.3 Each run

1. A CRM read failure aborts the run; the ledger is unchanged.
2. Build every candidate change. Drop changes whose key has a decision, changes already true in the CRM, and
   changes touching a reserved account or location (10.5).
3. Group the remaining changes into cards. A pending card whose change set is unchanged keeps its id and
   refreshes its evidence; otherwise the old card is superseded and a new one is created.
4. A new change on a record and field where an earlier value was rejected links that rejection ("you
   rejected X on DATE").
5. An incomplete run builds no gone-from-the-website cards and supersedes nothing; the app shows a red banner
   with the reason, and existing gone-from-the-website cards show as blocked (SC-6).

### 10.4 States (F10)

| Thing | Moves |
|---|---|
| Card | pending → applying → done · pending → rejected · pending → acknowledged (attention cards) · pending → superseded · pending → blocked → pending · applying → failed → applying (Retry) · applying → needs-reconciliation → applying (Check again finds the record) · failed → discarded (Discard, when no write of the operation has landed) · needs-reconciliation → discarded (Discard, under 11 step 5's rule) |
| Operation | applying → done · applying → failed · applying → needs-reconciliation · failed or needs-reconciliation → applying · failed or needs-reconciliation → discarded |
| Alternative group | open → claimed (owner's approval) → closed (owner done) · claimed → open (owner discarded) · open → closed as "none of these" (every card rejected; an attention card follows) |

Each action has its allowed starting states: Approve, Reject and Acknowledge from pending; Retry from failed;
Check again from needs-reconciliation; Discard from failed or needs-reconciliation, and only while **no write in
the operation has been confirmed** (R4-1: a new account whose administrator contact is uncertain keeps its
account; Check again or Retry finishes it). Any other state is refused with "already decided" or "not available now".

A card and its operation move together inside one SQLite transaction; every move is guarded by the expected
current status. Only the review app applies, and it runs as **one process holding an exclusive lock** on
`decisions.db.lock` (a second `serve` refuses to start). At startup it reads the step log of every operation
left `applying`: a POST with a recorded intent and no recorded outcome makes the operation
needs-reconciliation (the request may have reached the CRM); otherwise the operation becomes failed with
"Interrupted; Retry resumes". A discarded card carries no decision, so the next run proposes its changes again
as a new card instance.

### 10.5 Reservations (F5)

An operation that is applying, failed or needs reconciliation reserves every account it touches and its
location. The pipeline builds no change for a reserved account or location, so a half-finished change of
ownership is never mistaken for a duplicate. The reservation ends when the operation is done or discarded,
and never blocks the operation that holds it. An operation needing reconciliation keeps its reservation.

### 10.6 Alternative groups (F9)

Approving a card in a group claims the group, the card and a new operation in one transaction and records the
operation as the group's owner (`UPDATE alt_groups SET status='claimed', owner_op=? WHERE id=? AND
status='open'`). Only the owner may Retry or Check again. When the owner's operation is done, the other cards
become superseded and their change keys are recorded as rejected ("another choice was approved"). If the
owner's operation is discarded, the group reopens and the siblings stay pending; a reviewer's own rejection of
a sibling stands either way. Rejecting every card in a group closes it as "none of these"; the location then
appears under Needs attention.

### 10.7 Reviewer flags

`bellhaven-sync flag <card-id> "<reason>"` (or `--from-json`) sets a flag; flagged cards show a yellow
banner and sort first in their section. The blind model check (17.1) uses this.

## 11. Applying an approved card (`apply.py`, E5)

1. **Claim** in one transaction: card pending → applying, a new operation, its reservations and, for an
   alternative, the group. A double click, a second tab or the back button sees "already decided".
2. **Every step carries what it expects** (`expect`), fixed when the card was built from the data reviewed:
   - a PATCH: the current value of every field it writes (`note` included when it appends a line), plus the
     facts its decision rests on: revenue and AR for a parent move or a CHOW pointer (so new AR sends Lima
     back for review, and cleared AR sends Tiffin back), `parent_id`, `duplicate_of_account` and
     `chow_current_account` for a retirement or a CHOW pointer, and a contact's `account_id` for a move;
   - a create: that no Active account under the operator has the same full address; for a contact, that the
     target account has no active contact with the same name;
   - the records a relationship rests on but the step does not write (`related`): a duplicate's survivor is
     still a live, separate account at the reviewed place (or the website's); a buyer is still the account at
     the old address with the same care type.
3. **Before the first write**, every step's expectations about records that already exist are checked
   together, so a change of ownership whose old account no longer qualifies stops before anything is created.
   **Before each unfinished step**, its own expectations are checked again: read the record (or search, for a
   create). Equal to `expect` → write.
   Already equal to the target → the step counts as done without a write. Anything else → the operation stops
   and the card shows what changed ("Phone is now (330) 555-0123, not (517) 409-8156 as reviewed"); nothing
   more is written. Steps this operation already finished are never re-judged, and ids it created come from
   its own step log, so Retry resumes cleanly.
4. **Writes.** A PATCH is confirmed by its response showing the targets; a note line is appended once (skipped
   when already present). Before a POST, its intent and full identity are stored; on success the new id is
   stored at once.
5. **Unknown outcome** (no answer, a 5xx, an answer that does not confirm the write, a crash between sending a
   POST and recording its answer). A create → needs-reconciliation;
   the system never sends that POST again, and the operation keeps its reservations. **Check again** searches
   records with `created_by_candidate = true` and the step's full identity (for an account: name, street, city,
   state, ZIP, parent; for a contact: name, title, account) that no other operation has adopted: exactly one →
   adopted and the operation continues; none → "Not found yet" and the card waits; several → stop for a person.
   Timestamps are shown as evidence only; the CRM reports whole seconds, so they never decide ownership.
   **Discard** of an uncertain create is allowed only when no earlier step of the operation is confirmed, at least 10 minutes have passed since its intent
   (thirty times the 20-second request timeout) and a Check again made after that found nothing; the next run
   proposes the location again, and that create re-checks that no account exists at the address. The CRM
   offers no create-once key, so this is the strongest guarantee available; the residual case is in section 20.
   An update with an unknown outcome → failed ("may have landed"); Retry reads the record and finishes it,
   keeping the original request and before-values in the step log. **Discard** reads every update the
   operation sent and is refused when the CRM already shows one.
6. **A definite refusal** (a 4xx: nothing was written) → failed with "Failed at step n of m: <API message>".
   **Retry** resumes at the first unfinished step under the same checks.
7. **Claims and the daily run.** Approve is refused while a change on the card is already decided or a record it
   writes belongs to an unfinished operation. A run records its cards in one transaction that reads decisions
   and reservations again, so nothing decided while it was working comes back. Cards resting on a location's
   absence (gone, sold) are blocked by an incomplete read and their Approve, Retry and Check again wait for a
   complete one. A "none of these" card stays until acknowledged.
8. **No undo button (F2 revised).** History keeps every write's before-values; a mistake is corrected on
   Gage's explicit yes from those values.

## 12. CRM API contract (F2)

- Endpoints used: `GET/POST /accounts`, `GET/PATCH /accounts/{id}`, `GET/POST /contacts`,
  `GET/PATCH /contacts/{id}`. No delete, no merge.
- The published API description gives no request-body shape. Field names are taken from the GET responses.
  No probe requests are sent. The first real writes are ordinary approved cards made **off camera before
  recording**: the Grove City rename (one PATCH) and the Batavia new account with its administrator (two
  POSTs), each checked in the CRM browser; what they show goes into `docs/api-notes.md`.
- `parent_name` is derived by the CRM from `parent_id`; the system never writes it and the first PATCH
  confirms it follows. Records the system creates come back with `created_by_candidate = true` (every
  record in today's snapshot is false), which Check again (11 step 5) relies on.
- Status values: Active, Inactive, Needs Review. Page size ≤ 200. Bearer key on every request.
- Only an idempotent PATCH is retried after a connection error, inside one Approve, Retry or Check again
  click; a POST never is.

## 13. Review app (`app.py`, F1, F3)

- **Queue (`/`):** run banner (time, complete or why not, locations found / stated, accounts read, cards by
  section); a location filter; sections in 8.1 order with counts; each card a `<details>` block. Collapsed:
  section tag, facility, band, one-line summary ("Move to Bellhaven: new account; old kept for billing
  ($84,000 revenue, $12,400 AR)"). Expanded: before → after per field with changes highlighted; site record
  and CRM record side by side, including the site's administrator vs the account's contacts; signals with
  ✓/✗ and points; ruled-out candidates and conflicts with reasons; the billing-rule reasoning; "what will be
  written" (each API call); the alternative group and any earlier rejection; a "View in CRM" link per
  account; Approve and Reject with an optional reviewer note. Blocked cards show why and have no Approve
  button. A failed card shows its message (the difference found, or the API's error) with Retry, and Discard
  when nothing has landed; a card needing reconciliation shows Check again, and Discard once 11 step 5 allows.
- **Locations (`/locations`):** every website location and every leftover operator account from the latest
  run with its verdict: **Confident match** (High, nothing to change), **Medium match, nothing to change**
  (with the signals that carried it), **Match, needs a fix** (with the cards that fix it), **Possible match only** (Low, with the reason), **New** (no CRM account), **Gone from the website**,
  **Sold** (a buyer's account at the same address), **Needs attention**; with the linked CRM account and a
  "View in CRM" link. `bellhaven-sync run` prints the same table. This is the matcher's classification of all
  35, including the 10 that need no change.
- **One card at a time** (F3). One app process at a time (10.4).
- **After a click:** back to the same section with "Approved: 3 of 3 writes done" or "Failed at step 2 of 3:
  <API message> [Retry]".
- **History (`/history`):** every decision newest first, with each step's request, response and before-values.
- **Safety (F21):** writes only on POST; each form carries a per-session anti-forgery token checked by the
  server; the form names the card and the version it showed, and the server refuses a changed version;
  binds to 127.0.0.1 unless `--host` names a private-network address.
- **Look and feel:** calm, dense, legible at screen-recording size; one small CSS file; system fonts;
  reviewed with the installed impeccable and taste-skill passes before the code review.

## 14. Schedule and operations (E4, F24)

- `deploy/crontab`: `30 11 * * * cd /opt/bellhaven-crm-sync && mkdir -p logs && flock -n .run.lock uv run
  bellhaven-sync run >> logs/run.log 2>&1`. The server clock is UTC: 11:30 UTC is 06:30 US Eastern in
  winter, 07:30 in summer. `flock` stops overlapping runs. README: install path, `uv sync`, the key in the
  environment.
- `deploy/github-actions-daily.yml`: the same job as a scheduled workflow, outside `.github/workflows/` so it
  never runs; the README explains that in Actions the ledger would persist on a state branch.

## 15. Errors

| Situation | Behaviour |
|---|---|
| A page fails after 3 tries, an invalid page, the directory never repeats, or found ≠ stated | Run incomplete; no gone-from-the-website cards; nothing superseded; red banner; existing gone cards blocked |
| CRM read fails or the operator's parent is not found exactly once | Run aborts; ledger untouched; exit code 1 |
| A value differs from what was reviewed | Stops before that write and shows the difference; Discard while nothing has landed, and the next run rebuilds the card |
| A write returns 4xx/5xx | Card failed with the API message and "n of m done"; Retry |
| A create's outcome is unknown | Needs reconciliation; Check again; the system never re-sends the POST; Discard only after 10 minutes and an empty check |
| Two clicks, two tabs, or two alternatives at once | One transaction claims; the other sees "already decided" |
| Process killed mid-apply | At the next start: a POST without a recorded answer → needs reconciliation; otherwise failed ("Interrupted") and Retry resumes under the same checks |

## 16. Tests (G1, F19)

pytest against an in-memory fake CRM loaded from `tests/fixtures/snapshot/`; the real CRM is never touched.
- `test_normalize`: today's abbreviation pairs; `St` as Street vs Saint; Hypothesis properties (normalizing
  twice changes nothing; abbreviation-equivalent pairs normalize equal; PO Box detection).
- `test_scrape`: saved HTML yields 35 locations including Findlay; a failed page, an invalid page and a short
  count each mark the run incomplete; a home page saying 34 with 34 found is complete.
- `test_answer_key`: the run produces exactly the 42 cards of section 9.
- `test_end_state`: approve the 41 intended cards and reject the Union Square Possible match on the fake CRM;
  the result equals section 9, and revenue, AR, the three old CHOW accounts' other fields and the 70
  unrelated accounts are unchanged field by field.
- `test_billing_rule`: Tiffin and Marietta take the new-account route; Lima and Findlay re-parent directly;
  Akron, Defiance and Meadville get no parent or CHOW change; Sandusky points at 63BVK3 and nothing else.
- `test_rerun`: after the end-state run, a second run proposes nothing; after rejections, nothing returns;
  a rejected two-field card whose street is fixed elsewhere does not return as a phone-only card; a rejected
  Monroe retirement whose survivor later changes returns whole (pointer and Inactive together).
- `test_guard`: an incomplete run builds no gone cards, supersedes nothing, and blocks an existing gone card;
  an operator account not on the website that is already Inactive gets no card.
- `test_apply`: a phone changed in the CRM after the run stops its Details card with the difference and writes
  nothing; Lima gaining AR stops its re-parent; Batavia created elsewhere stops its create; a failure at step 2
  of 3 shows "Failed at step 2 of 3" and Retry finishes with exactly one new Tiffin account; a lost POST
  response becomes needs-reconciliation, Check again adopts the record and only one POST was sent; Discard is
  refused once a write has landed; a half-done Tiffin change reserves both accounts so the next run proposes no
  duplicate; an operation left applying is failed at app start and Retry completes it.
- `test_apply` also: Tiffin's AR cleared before approval stops the change of ownership before any create; a
  rejected phone plus a new street change on the same account yields a Details PATCH with the street only;
  an uncertain create found in the same second as its intent is still adopted; Discard of an uncertain create
  is refused before 10 minutes.
- `test_restart`: a crash after a POST was sent and before its answer was recorded comes back as
  needs-reconciliation, and Check again adopts the record with exactly one account created; a crash between
  two PATCH steps comes back failed, and Retry finishes the card.
- `test_alternatives`: two approvals in one group, one wins; the owner retries its failed choice and finishes;
  discarding it reopens the group with the sibling still pending; rejecting every choice closes the group and
  raises one attention card.
- `test_locations`: the Locations page and the run's table list 35 locations and 3 leftover accounts; the 9
  unchanged rows of 9.1 show as Confident match and Ashtabula as Medium match, nothing to change.
- `test_readonly`: the pipeline's client raises on every write method.
- `test_app_security`: a write without the anti-forgery token, by GET, or against a changed card version is refused.
- `test_single_process`: a second `serve` on the same database refuses to start.
- `test_actions`: Retry, Check again and Discard succeed through their HTTP endpoints from their allowed states
  and are refused from any other.
- `e2e/test_review_flow.py` (Playwright): approve one card, reject one, run again, the rejected card stays gone.

## 17. Checking the answers before anything is approved (H1, H2)

1. **Blind model check:** a fresh GPT-6-Astra session receives only the brief, the scraped locations and the
   CRM snapshot (not this design) and returns each location's and leftover account's outcome as JSON. A
   script compares it with the cards; each disagreement becomes a flag (10.7). The spec review in Appendix B
   saw this design, so it does not count as blind.
2. **Second opinion** on the judgment calls (Sandusky, Kettering, Owosso, Alliance, Coldwater) from Fable.
3. **Same-facility probabilities** for each candidate pair from an evaluator, compared with the bands during
   development only; never shipped, never decides.
4. **Gage clicks every card**, flagged ones first (H1).
5. **`scripts/verify.py`** reads every account and contact and prints PASS/FAIL per row of section 9 plus the
   unchanged-everything-else invariants (8.10). Its output goes in the writeup.
6. **Second run on the real CRM:** `bellhaven-sync run` once more; it must propose zero cards. The run banner
   goes in the writeup beside the verifier's output, as the proof that re-runs are safe.

## 18. Deliverables

- `README.md`: what it is, quick start (`uv sync`, set the key, `uv run bellhaven-sync run`, `serve`), how it
  works in one screen, schedule, tests.
- `WRITEUP.md`: matching approach; every judgment call with its evidence; the billing rule, with Sandusky
  stated as the one exception ("An account already existed under Millstone at the same full address and care
  type. I used it as the CHOW successor rather than create a duplicate, and set only the old account's
  pointer."); how AI tools were used; what I'd build next; time by session; the verifier's output.
- The form's writeup box: ~250–300 plain-text words (matching ~120, AI ~80, next ~60) ending with the link to
  `WRITEUP.md` (K2). "Next" leads with Medicare's public ownership files (K3).
- Demo video (I2): recorded with Cap at the start of the review session, after the off-camera first writes
  (12). 0:00 run banner and the queue's sections; 0:15 Tiffin's change of ownership (Cedar Trail, $84,000 /
  $12,400): the evidence and its three writes, approved; 0:55 Union Square's Possible match (240 Market St and
  Dale Croft, where the site lists Phil Holloway), rejected; 1:20 the CRM browser shows the new Bellhaven of
  Tiffin and the old account's pointer; 1:45 one sentence on re-runs. Everything else is approved after the
  recording. Gage uploads it unlisted to YouTube.
- Time (A2, K4): Gage's focused time from 19:33:57Z, by session, nearest quarter hour, "X hours".
- Walkthrough preparation (J2): a one-page code tour in two layers (the core path: scrape → match → propose →
  approve; then the safety layer: ledger, checks before writes, recovery), a cheat sheet of about 8 likely live changes with the one
  place each lands, a 20-minute mock with a surprise change.

## 19. Timeline (UTC)

| When | Who | What |
|---|---|---|
| 2026-11-07 evening | agents | Plan, build, tests, blind check, code review |
| When the build is verified | Gage, ~45 min | Off-camera first writes; read the flags; record the video; click the 42 cards; verifier; second run proposes zero |
| Next | Gage, ~30 min | Edit the writeup; repo public; upload video; submit the form |
| 2026-11-09T19:30Z | | Deadline (48 hours from his first read) |
| Before the interview | Gage, ~30 min | Code tour, cheat sheet, mock walkthrough |

## 20. Risks and concerns

- **Time:** honest focused time will exceed the brief's ~2 hours.
- **Sandusky (D4):** a disclosed exception to "create a new account"; the writeup says why.
- **Live change in the usual window (J1):** hook messages may show on the screen share; a one-line explanation
  is rehearsed.
- **Uncertain creates (residual):** the CRM has no create-once key. If the CRM committed a POST more than 10
  minutes after the app gave up on it, and a reviewer discarded it in between, the next approved create could
  duplicate it. The 20-second request timeout and the absence check before every create make this
  unrealistic; it is disclosed rather than claimed impossible.
- **Write shapes unknown until the first approved writes:** done off camera first; a 422 shows its message
  on the card and nothing else changes.

## Appendix A. Decision log

| Id | Decision |
|---|---|
| A1 | Deadline 2026-11-09T19:30Z: 48 hours from Gage's first read (revised after review) |
| A2 | Time counted: Gage's focused minutes only |
| A3 | AI story: directing and checking; one line on the personal agent setup |
| A4/A5 | Repo `ngagemartin24/bellhaven-crm-sync`, private while building, public at submit |
| A6 | Runs on a Linux server viewed from the laptop; tested macOS fallback |
| A7 | Claude builds; GPT reviews code and checks matches blind |
| B1 | Crawl the whole site; warn when the count differs from the home page |
| B2 | FastAPI + HTML templates; requests + BeautifulSoup; SQLite; uv + Python 3.13 |
| C1 | Explainable rules and score; no AI in the pipeline |
| C2 | Sync phone, city, state, ZIP, care type and genuinely different streets; never rewrite abbreviation-only spelling (revised after review) |
| C2a | Never replace a PO Box billing street |
| C2b | Ownership/name card and details card per account |
| C2c | Care type: keep if among the site's offerings, else the first offering mapped |
| C3 | Look-alikes appear as Low Possible-match cards in an alternative group with "create new" |
| C4 | Rename all four small name variants |
| D1 | Survivor: billing history first |
| D2 | Kettering survivor CAE2X4, re-parented and renamed |
| D3 | Contacts on a retired copy move with the duplicate fix |
| D4 | Sandusky points at the existing Millstone account; disclosed exception |
| D5 | Old change-of-ownership accounts get only the pointer (revised after review) |
| D6 | Alliance and Coldwater: Needs Review + note |
| D7 | Harborview parent account left as is |
| D8 | New accounts take the site's values and its administrator |
| D9 | No administrator upkeep on existing accounts; mismatches shown as evidence (revised after review) |
| D10 | Dated note line when an account's meaning changes, never on old CHOW accounts |
| E1/E2 | Decisions per change key; pending cards refreshed; regrouped cards cannot revive decisions |
| E3 | An incomplete scrape holds back and blocks gone-from-the-website cards |
| E4 | Cron as the schedule; inert Actions example |
| E5 | Multi-write cards run as recorded steps with re-check and safe retry |
| F1 | Queue grouped by kind of change |
| F2 | No undo button; before-values kept in History (revised after review) |
| F3 | One card at a time |
| G1 | Full test set including one browser run |
| H1/H2 | Gage clicks every card after the blind check; verifier afterwards |
| I1/I2 | Video on unlisted YouTube; Tiffin approve, Union Square reject |
| J1/J2 | Live change in the usual window; code tour, cheat sheet, mock |
| K1–K4 | One text box; ~250–300 words; Medicare files lead "next"; clock from 19:33:57Z |

## Appendix B. Independent review, round 1 (GPT-6-Astra, 2026-11-07)

Verdict on v1: not sound. Its independent answer key agreed with v1 on every row except the note on the
three old change-of-ownership accounts, now removed (D5). Dispositions:

| Finding | Where fixed |
|---|---|
| F1 note on old CHOW accounts | D5 revised; 8.3, 8.9, 8.10 |
| F2 probes could write | 12: no probes; first writes approved off camera |
| F3 prerequisites not re-checked | 11 step 2 |
| F4 unsafe create retry | 11 step 4 |
| F5 unfinished operations re-matched | 10.5 |
| F6 decisions tied to card composition | 10.2 |
| F7 unsafe revert | F2 revised: no undo button |
| F8 eligibility inconsistent | 7.1 |
| F9 competing alternatives | 10.6 |
| F10 incomplete state machine | 10.4 |
| F11 care guard on singletons | 7.5 |
| F12 duplicates across addresses | 7.5 |
| F13 count floor blocks real sales | 5.1 step 6 |
| F14 weak successor rule | 8.6 |
| F15 stale removal cards approvable | 10.3 step 5, 13 |
| F16 gate has no unresolved path | 5.1 step 4, 6, 7.2, 8.8 |
| F17 scope and noise | C2 and D9 revised; Actions example and dev-only evaluator kept |
| F18 Sandusky exception | SC-2, 18 |
| F19 verification circular | 16, 17.1 |
| F20 first create on camera | 12, 18 |
| F21 no request-forgery protection | 13 |
| F22 wrong counts | 7.3, 9.4 |
| F23 contradictory wording | SC-3, 8.10 |
| F24 schedule time | 14 |
| F25 deadline assumed | A1 revised |

## Appendix B2. Independent review, round 2 (GPT-6-Astra, 2026-11-07)

Verdict on v2: not sound on write safety; the answer key (every row of section 9, 42 cards, 127 accounts, 73
contacts) and the billing rule were confirmed again, with Sandusky noted as the disclosed exception (D4).
Round-1 status: 17 resolved, 8 partial (F3, F4, F9, F10, F14, F20, F23 and the N findings below cover them).

| Finding | Where fixed in v3 |
|---|---|
| N1 re-check could overwrite a newer value; contacts not read | 11 steps 2–3: per-step expected values, contacts read |
| N2 Retry rejected its own finished work | 11 step 3: resume from the step log; finished steps never re-judged; 10.5 own reservation |
| N3 no lifecycle for stale or interrupted operations | 10.4: one process with a lock, startup recovery, Discard while nothing landed; 10.5 release |
| N4 alternative group could not retry its choice | 10.6: one-transaction claim with an owner; siblings settled only when the owner is done |
| N5 "Create again" could duplicate | 11 step 5: removed; Check again only; Discard and let the next run re-check |
| N6 a duplicate decision could split | 8.1, 10.2: `retire_duplicate` and `set_chow` are single changes |
| N7 "every other account receives no write" contradicted section 9 | 8.10 reworded |
| N8 false "no other account at this address" note | 8.6: none / exactly one current / anything else → Needs attention / reserved → wait |
| F14 an inactive account could be the successor | 8.6: the successor must be Active, eligible and compatible; consolidation named as such |
| F20 crowded video | 18: Tiffin approve, Union Square reject, CRM browser |

## Appendix B4. Independent review round 4 (GPT-6-Astra, 2026-11-07)

Verdict on v4: NOT SOUND on one Major (R4-1: uncertain-create Discard could abandon an operation whose first
create had landed). N5 resolved: the 10-minute bound is adequate for this use, with the disclosed residual risk.
Section 9 byte-for-byte unchanged; 42 cards, 41/1, 127 accounts, 73 contacts re-derived. Fixed in v5: 10.4 and
11 step 5 refuse Discard from either state once any write in the operation is confirmed; PLAN Task 6 and a Task 7
test (account landed, administrator uncertain, Discard refused after 10 minutes).

## Appendix B3. Independent review round 3 (GPT-6-Astra) and consistency check, 2026-11-07

Round 3 verdict on v3: not sound on uncertain creates and plan consistency; section 9 confirmed byte-for-byte
unchanged and re-derived (42 / 41 + 1 / 127 / 73). Round-2 items: 10 resolved, 4 partial, N5 not resolved.

| Finding | Where fixed in v4 (and the plan) |
|---|---|
| N5, R3-1 uncertain create could be repeated (incl. after restart) | 10.4 startup reads the step log; 10.5 reservation kept; 11 step 5 Discard only after 10 minutes and an empty check; residual risk in 20 |
| R3-2 timestamp filter could hide our own create | 11 step 5: identity and adoption, timestamps evidence only |
| R3-3 CHOW prerequisites checked too late | 11 steps 2–3: all prerequisites before the first write; revenue/AR in the CHOW pointer's expectations |
| R3-4, consistency F2 section 4 wording | 4 |
| P1 recovery actions refused by the app | 10.4 allowed starting states; PLAN Task 8 |
| P2 card identity after discard; attention identity | 10.1 instance id + change set; 8.8 attention key |
| P3 decided field could ride in a rebuilt PATCH | 10.2: cards built only from surviving changes; PLAN Tasks 5–6 |
| P4 note missing from expectations | 11 step 2; PLAN Task 5 |
| P5, consistency E2/E5 missing tests | 16 (Monroe re-run, owner Retry, restart) |
| Consistency E1 no per-location classification | 13 Locations page and run table; 10.1 `verdicts_json`; 16 `test_locations` |
| Consistency F1 judgment calls outside `rules.py` | 4 file table; PLAN Task 4 |
| Consistency E3 "none of these" state | 10.4 group table; 8.8; 16 |
| Consistency B1 Discard rule vague | 11 step 5 |
| Consistency F3 proportion | 18: two-layer code tour |
| Consistency F4 wording | 8.5, 12, 13 |

## Appendix B5. Code review, round 5 (GPT-6-Astra, 2026-11-07)

Verdict FAIL at 60808f8: AC-3, AC-4 and AC-8 passed; 6 Major and 3 Minor defects. Each was checked against the
code before fixing, and each now has a test (`tests/test_write_safety.py` unless noted).

| Finding | Fix |
|---|---|
| 1 fractional AR truncated by `int(float())` | `rules.money` returns an exact Decimal; `test_match::test_fractional_balances_count` |
| 2 survivor and buyer facts never re-checked | 11 step 2 `related`; buyer address and care type in the sale's expectations |
| 3 incomplete read stopped only pending gone cards | `needs_complete_read` on every absence card; blocked, and Approve/Retry/Check again gated |
| 4 a run could revive a change decided mid-run | 11 step 7: decisions and reservations re-read inside the recording transaction; claim conflicts refused |
| 5 a lost update answer or a 5xx create could be discarded | 11 steps 5-6: uncertain vs definite; Discard reads the CRM |
| 6 verifier checked a few fields of created records | every field of created accounts and administrators, notes included; `test_verify` |
| 7 Retry overwrote the first attempt's request | the original intent is kept |
| 8 the approval note was dropped | passed through to the decision |
| 9 "none of these" disappeared on the next run | ledger-raised cards are never superseded |
| AC-7 two ambiguity rules outside `rules.py` | `rules.medium_match`, `rules.look_alike_needs_person`, each tested |


## Appendix B6. Code review, round 6 (GPT-6-Astra, 2026-11-08)

Verdict FAIL at 9bd1a19: round-5 defects 1, 3, 4, 6, 7, 8, 9 and AC-7 resolved; AC-1 to AC-8 passed; three Major
findings remained. Each was reproduced as a failing test first.

| Finding | Fix |
|---|---|
| 1 a duplicate retirement did not check the survivor's `chow_current_account` | the survivor's sale pointer is in the retirement's expectations; `test_sold_survivor_stops_the_duplicate_retirement` |
| 2 Discard compared a lost update's whole note, so a later staff note hid a landed write | Discard reads each field of an update sent without a definite answer: our value means landed, the before-value means not landed, anything else is refused for a person; a note counts as landed when it holds the line this update added; three tests |
| 3 a decision database from before the absence flag would crash | `PRAGMA user_version` schema version; an older database gains the column in one transaction, absence cards marked, history kept; `test_ledger_upgrade` |

## Appendix B7. Code review, round 7 (GPT-6-Astra, 2026-11-08)

Verdict FAIL at 7d4184e: the three round-6 findings resolved, AC-1 to AC-8 passed, one new Major finding on AC-9.

| Finding | Fix |
|---|---|
| A definite error on Retry's first read (for example a 429) marked an earlier uncertain write "failed", so Discard skipped it and a landed retirement could be abandoned | "sent" now means this attempt stamped a new intent; an earlier attempt's uncertain step keeps its status and the card says the write may have landed. `step_failed` has no other caller. `test_a_read_error_during_retry_keeps_an_earlier_write_uncertain` |

Discard still reads only steps whose outcome is unknown: once a step's status is truthful, a definite refusal
means the write did not land, and re-reading those would only strand cards that someone edited afterwards.
