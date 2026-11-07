"""Read the CRM back (read-only) and check it against the expected end state.

    uv run python scripts/verify.py

The expected end state is tests/fixtures/expected_end_state.json, written by hand from docs/DESIGN.md
section 9. Every field it does not name must still equal the snapshot taken before any change, so an
extra write anywhere is reported too. Exit 0 when everything matches, 1 otherwise.
"""

import sys
from pathlib import Path

from bellhaven_sync.config import TOKEN_VAR, load_config
from bellhaven_sync.crm import CrmClient
from bellhaven_sync.verify_lib import diff_against_expected

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "fixtures" / "snapshot"
EXPECTED = ROOT / "tests" / "fixtures" / "expected_end_state.json"


def verify_main(crm=None) -> int:
    if crm is None:
        cfg = load_config(ROOT / "config.toml")
        if not cfg.token:
            print(f"Set {TOKEN_VAR} first.", file=sys.stderr)
            return 2
        crm = CrmClient(cfg.crm_url, cfg.token, read_only=True)
    problems = diff_against_expected(crm, SNAPSHOT, EXPECTED)
    if problems:
        print(f"FAIL: {len(problems)} difference(s) from the expected end state")
        for line in problems:
            print("  " + line)
        return 1
    accounts, contacts = len(crm.list_accounts()), len(crm.list_contacts())
    print(
        f"PASS: the CRM matches the expected end state ({accounts} accounts and {contacts} contacts checked)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(verify_main())
