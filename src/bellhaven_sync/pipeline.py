"""The daily run: scrape the website, read the CRM, propose, record (DESIGN 5 and 10.3). It never writes to the CRM."""

from collections import Counter
from dataclasses import dataclass
from datetime import date

import requests

from .crm import CrmError
from .ledger import Ledger
from .models import Account, Verdict
from .propose import propose
from .scrape import scrape


class RunAborted(Exception):
    """The run stopped before recording anything: the CRM could not be read or the operator was not found."""


@dataclass(frozen=True)
class RunSummary:
    run_id: int
    complete: bool
    reason: str
    found: int
    stated: int | None
    accounts_read: int
    counts: dict[str, int]
    by_section: dict[str, int]
    verdicts: list[Verdict]


def find_operator(accounts: list[Account], name: str) -> str:
    hits = [a["account_id"] for a in accounts if a.get("name") == name]
    if len(hits) != 1:
        raise RunAborted(f"Expected exactly one CRM account named {name!r}, found {len(hits)}")
    return hits[0]


def run_once(
    fetch, crm, ledger: Ledger, operator_parent_name: str, today: date, base_url: str = ""
) -> RunSummary:
    report = scrape(fetch, base_url)
    try:
        accounts = crm.list_accounts()
        contacts = crm.list_contacts()
    except (CrmError, requests.RequestException) as e:
        raise RunAborted(f"Could not read the CRM: {e}") from e
    operator_id = find_operator(accounts, operator_parent_name)

    proposal = propose(
        report,
        accounts,
        contacts,
        operator_id,
        today,
        ledger.reserved_accounts(),
        ledger.reserved_locations(),
        ledger.decided_keys(),
    )
    run_id = ledger.start_run()
    counts = ledger.record_cards(run_id, proposal.cards, report.complete)
    ledger.record_verdicts(run_id, proposal.verdicts)
    ledger.finish_run(run_id, report, len(accounts), counts)
    queue = [c for c in ledger.cards() if c.status in ("pending", "blocked")]
    return RunSummary(
        run_id=run_id,
        complete=report.complete,
        reason=report.reason,
        found=report.found,
        stated=report.stated,
        accounts_read=len(accounts),
        counts=counts,
        by_section=dict(Counter(c.section for c in queue)),
        verdicts=proposal.verdicts,
    )
