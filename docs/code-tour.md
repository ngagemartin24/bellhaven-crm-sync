# Code tour

Two layers. Read the core path first; the safety layer is what makes the core path safe to run every day
against a real CRM.

## Layer 1: the core path (website → proposal → approval → CRM)

| Step | Where | What happens |
|---|---|---|
| 1. Start a run | `cli.py` → `pipeline.run_once` | Scrape the website, read every CRM account and contact, build proposals, record them. Nothing is written to the CRM. |
| 2. Read the website | `scrape.py`: `scrape`, `parse_community`, `stated_count` | Crawl the home page, About page and every directory page; collect every community link; read each community page; compare the count with the "35 communities" the home page states. |
| 3. Clean the text | `normalize.py` | "Rd" = "Road", phone digits only, ZIP to five digits, the site's care labels to the CRM's care types. |
| 4. Match | `match.py`: `signals`, `match_location` | For each location, every CRM account in the same state that shares a city, ZIP, phone or administrator is scored. Duplicates are the strong (High) matches at the same full address with a compatible care type. |
| 5. Decide | `rules.py` | The main judgment calls: the weights and the High/Medium/Low bands, the billing rule (`needs_chow`), which duplicate survives, which fields sync, what to do with an account that left the website, the note format. |
| 6. Propose | `propose.py`: `propose` | Each needed change becomes a card: what changes (for the ledger), the exact API calls in order (for apply), and the evidence (for the reviewer). |
| 7. Remember | `ledger.py` | SQLite: runs, cards, decisions, how each location was classified. |
| 8. Review | `app.py`, `templates/` | Queue, Locations, History. Approve and Reject are plain HTML forms. |
| 9. Write | `apply.py`: `approve` | Runs the card's API calls in order through `crm.py`. |

## Layer 2: the safety layer

| Risk | Where | Guard |
|---|---|---|
| The daily run writes something | `crm.py` | The pipeline's client is read-only; every write method raises. Only the review app opens a writable client. |
| A decided change comes back tomorrow | `ledger.py` change keys | Every change has a key (record, field, value). A key that was approved or rejected is never proposed again; a different value is a new proposal shown beside the earlier rejection. |
| The record changed after review | `apply.py` | Before the first write, every prerequisite is re-read (for a change of ownership, the billing figures). Before each write, the record must still hold what the reviewer saw; otherwise it stops and says what changed. |
| A write fails halfway | `apply.py`, step log in `ledger.py` | Each step is recorded. Retry resumes at the first unfinished step; a step already at its target is skipped. |
| A create's answer never arrives | `apply.py`: `check_again` | The create is never re-sent. Check again looks for the record; Discard is allowed only after a later empty check and ten minutes. |
| Tomorrow's run trips over a half-done change | `ledger.py` reservations | Accounts and locations in an unfinished operation are left alone by the next run. |
| Two choices for one location (Union Square) | `ledger.py` alternative groups | Approving one choice closes the others; rejecting every choice raises a "needs attention" card. |
| The website read is short or fails | `scrape.py` report, `ledger.record_cards` | "Gone from the website" cards are held until a complete run. |
| The app is reachable or forgeable | `cli.py`, `app.py` | Listens only on loopback or a private address; one app process per ledger; every POST checks an anti-forgery token. |

## Where the tests are

- `tests/test_end_state.py`: the whole intended review against the hand-written answer key
  (`tests/fixtures/expected_end_state.json`), including "every field not named stays as it was".
- `tests/test_match.py`, `tests/test_propose.py`: one test per rule; the expected outcome for every location.
- `tests/test_rerun.py`, `tests/test_guard.py`: re-runs and incomplete website reads.
- `tests/test_apply.py`, `tests/test_restart.py`, `tests/test_alternatives.py`: failures, retries, lost answers.
- `tests/e2e/test_review_flow.py`: one real browser: approve, reject, re-run.
