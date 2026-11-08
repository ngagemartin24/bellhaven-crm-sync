"""One review app per decisions file (DESIGN 10.5): a second one refuses to start."""

import subprocess
import sys

import pytest

from bellhaven_sync import app as app_module
from bellhaven_sync.app import AppLockedError, _lock

SECOND_APP = """
import sys
from bellhaven_sync.app import AppLockedError, _lock
try:
    _lock(sys.argv[1])
except AppLockedError as e:
    print(e)
    sys.exit(3)
"""


@pytest.fixture
def held(tmp_path):
    db = tmp_path / "decisions.db"
    _lock(db)
    yield db
    app_module._HELD_LOCKS.pop(str(db.resolve())).close()


def test_second_app_in_another_process_refuses_to_start(held):
    r = subprocess.run(
        [sys.executable, "-c", SECOND_APP, str(held)], capture_output=True, text=True, check=False
    )
    assert r.returncode == 3 and "already running" in r.stdout


def test_second_app_in_the_same_process_refuses_to_start(held):
    with pytest.raises(AppLockedError):
        _lock(held)
