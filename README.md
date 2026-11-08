# Bellhaven CRM sync

Keeps the CRM's picture of Bellhaven Senior Living's facilities in line with Bellhaven's own website.
A daily run reads the website and the CRM, matches every location to its account, and proposes fixes with
the evidence for each. A person approves or rejects each one in a small review app; nothing reaches the CRM
without an approval.

## Result on the real CRM

42 proposals: 41 approved, 1 rejected (the Union Square look-alike). The CRM went from 121 accounts and 67
contacts to 127 and 73. Afterwards `scripts/verify.py` against the real CRM printed `PASS: the CRM matches the
expected end state (127 accounts and 73 contacts checked)`, and a second run read 35 of 35 locations and 127
accounts and proposed 0 changes. [WRITEUP.md](WRITEUP.md) lists every judgment call behind those changes.

The two real runs, as recorded in the app's decision file (`decisions.db`, table `runs`):

```
run  started (UTC)     website read            CRM accounts  new  refreshed  superseded  blocked
1    2026-11-08 13:41  complete, 35 of 35      121           42   0          0           0
2    2026-11-08 15:43  complete, 35 of 35      127           0    0          0           0
```

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
- **Match.** A location and an account are compared only when they are in the same state and share a city,
  ZIP, phone number or administrator. The same street at the same city or ZIP is a High match. The same place
  with the administrator or phone matching, where the street differs or is a PO Box, is Medium. The same place
  and a similar name with nothing else is Low, and a Low match is only ever offered as a possible match.
  Points (street 50, administrator 25, phone 10, ZIP 5, city 5, name up to 10) order the candidates; the band
  decides.
- **Propose.** Each needed change becomes a card: a change of ownership, a duplicate group, a wrong parent,
  a new account, an account gone from the website, a possible match, a rename, or a phone/ZIP fix. The main
  judgment calls (bands and weights, the billing rule, which duplicate survives, what syncs, what happens to an
  account that left the website, the note format) live in
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
sketches the same daily run as a GitHub Actions workflow; it sits outside `.github/workflows/`, so it does
not run. It keeps the ledger on a `state` branch between runs. A reviewer's app would have to use that same
ledger (download it before reviewing, push it back after), which the sketch does not automate.

## Tests

```sh
uv run pytest                 # everything except the browser test; no network, about half a minute
uv run pytest -m e2e          # one browser run: approve, reject, re-run (needs `uv run playwright install chromium`)
```

The tests run against a saved copy of the website and an in-memory CRM. They pin the expected end state
for every location and leftover account, the billing rule (including accounts that carry AR but do not
move), re-runs, incomplete website reads, retries and uncertain creates.

## More

- [WRITEUP.md](WRITEUP.md): the matching approach, the judgment calls, how AI tools were used, and what
  comes next.
- [docs/demo.mp4](docs/demo.mp4): the two-minute demo on the real CRM (approve, reject, the change in the CRM
  browser), narrated with an ElevenLabs clone of my own voice.
- [docs/code-tour.md](docs/code-tour.md): one page on where each part of the code lives.
- [docs/TESTING.md](docs/TESTING.md): every test and audit (accessibility, Lighthouse, security, code review), what
  each found and what was fixed, plus seven recorded walkthroughs of the app.

Written before the build and kept as a record: [docs/DESIGN.md](docs/DESIGN.md), the full design with the
expected outcome for every account (its "As built" note says where the real run differed), and
[docs/PLAN.md](docs/PLAN.md), the task-by-task build plan.
