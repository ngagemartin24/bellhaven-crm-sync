# Bellhaven CRM sync

Keeps the CRM's picture of Bellhaven Senior Living's facilities in line with Bellhaven's own website.
A daily run reads the website and the CRM, matches every location to its account, and proposes fixes with
the evidence for each. A person approves or rejects each one in a small review app; nothing reaches the CRM
without an approval.

## Quick start

```sh
uv sync                                   # Python 3.13 and the locked dependencies
export BELLHAVEN_CRM_TOKEN=...            # or put BELLHAVEN_CRM_TOKEN=... in a .env file (git-ignored)
uv run bellhaven-sync run                 # read the website and the CRM, record proposals
uv run bellhaven-sync serve               # review at http://127.0.0.1:8000
```

`run` prints the run summary and how every location was classified. Running it again proposes nothing it
has already proposed or that has been decided.

## How it works

```
website ─▶ scrape ─┐
                   ├─▶ match ─▶ propose ─▶ decisions.db ─▶ review app ─▶ [Approve] ─▶ apply (check, then write) ─▶ CRM API
CRM API ─(read)────┘                         ▲                                                                       │
                                             └──────────────────── step log, before-values ◀────────────────────────┘
```

- **Scrape.** Crawls the home page, About page and every directory page, collects every community link, and
  checks the count against the number the home page states. A short or failed read holds back every
  "gone from the website" proposal for that day.
- **Match.** A location and an account must be in the same city and state and share a street, an
  administrator or a phone number. Street, ZIP and administrator count most, the name somewhat, the phone
  least. Each match is High, Medium or Low; a Low match is only ever offered as a possible match.
- **Propose.** Each needed change becomes a card: a change of ownership, a duplicate group, a wrong parent,
  a new account, an account gone from the website, a possible match, a rename, or a phone/ZIP fix. Every
  judgment call (score weights, the billing rule, which duplicate survives, what syncs) lives in
  [`src/bellhaven_sync/rules.py`](src/bellhaven_sync/rules.py).
- **Billing rule.** Before an account moves to a different parent: with revenue history *and* outstanding
  AR, the old account is left exactly as it is, a new account is created under the right parent, and the
  old one points to it through `chow_current_account`. Otherwise, the account is re-parented directly.
- **Review and apply.** Approve writes the card's steps in order. Before each write the app re-reads the
  record and writes only if it still holds what the reviewer saw; otherwise it stops and says what changed.
  A step log makes a retry safe, and a create whose answer never arrived is looked for, never sent twice.
- **Decisions are remembered.** Each change has a fingerprint (which record, which field, which value). An
  approved or rejected change is never proposed again; a genuinely different value is a new proposal, shown
  beside the earlier rejection.

The pages: **Queue** (proposals grouped by kind, one open beside the list), **Locations** (the verdict for
every website location and every leftover Bellhaven account) and **History** (every decision with each
write's request, the CRM's answer and the values it replaced).

## Schedule

[`deploy/crontab`](deploy/crontab) runs `bellhaven-sync run` at 11:30 UTC every day (06:30 US Eastern in
winter) under `flock`, so runs never overlap. [`deploy/github-actions-daily.yml`](deploy/github-actions-daily.yml)
is the same job as a GitHub Actions workflow; it sits outside `.github/workflows/`, so it does not run.
In Actions the ledger would be kept on a `state` branch between runs.

## Tests

```sh
uv run pytest                 # everything except the browser test; no network, a few seconds
uv run pytest -m e2e          # one browser run: approve, reject, re-run (needs `uv run playwright install chromium`)
```

The tests run against a saved copy of the website and an in-memory CRM. They pin the expected end state
for every location and leftover account, the billing rule (including accounts that carry AR but do not
move), re-runs, incomplete website reads, retries and uncertain creates.

## More

- [docs/DESIGN.md](docs/DESIGN.md): the full design, including the expected outcome for every account.
- [docs/PLAN.md](docs/PLAN.md): how it was built, task by task.
- [docs/TESTING.md](docs/TESTING.md): every test and audit (accessibility, Lighthouse, security, code review), what
  each found and what was fixed, plus seven recorded walkthroughs of the app.
- [docs/demo.mp4](docs/demo.mp4): the two-minute demo on the real CRM (approve, reject, the change in the CRM
  browser), captioned.
- [WRITEUP.md](WRITEUP.md): the matching approach, the judgment calls, how AI tools were used, and what
  comes next.
