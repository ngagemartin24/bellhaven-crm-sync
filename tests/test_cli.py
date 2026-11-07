import json

import pytest
from conftest import CFG_AT, DAY, FETCH, SNAP
from fake_crm import FakeCrm

from bellhaven_sync import cli
from bellhaven_sync.ledger import Ledger


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """The CLI over the saved website, an in-memory CRM and a ledger in tmp_path."""
    cfg = CFG_AT(tmp_path)
    crm = FakeCrm.from_snapshot(SNAP)
    monkeypatch.setattr(cli, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(cli, "_dependencies", lambda c: (FETCH, crm, Ledger(c.db_path)))
    monkeypatch.setattr(cli, "_today", lambda: DAY)
    return cfg, crm


def test_run_prints_banner(wired, capsys):
    assert cli.main(["run"]) == 0
    out = capsys.readouterr().out
    assert "complete" in out and "35 of 35 locations" in out and "121 CRM accounts read" in out
    assert "42 proposals" in out and "Change of ownership 3" in out
    assert "Confident match" in out and "bellhaven-meadows-of-findlay" in out


def test_second_run_proposes_nothing_new(wired, capsys):
    cli.main(["run"])
    capsys.readouterr()
    assert cli.main(["run"]) == 0
    assert "0 new" in capsys.readouterr().out


def test_unreadable_crm_exits_1(wired, monkeypatch, capsys):
    _, crm = wired
    crm.fail_next("GET", 503, "down")
    assert cli.main(["run"]) == 1
    assert "Could not read the CRM" in capsys.readouterr().err


def test_run_without_key_exits_2(monkeypatch, tmp_path, capsys):
    cfg = CFG_AT(tmp_path).__class__(**{**CFG_AT(tmp_path).__dict__, "token": None})
    monkeypatch.setattr(cli, "load_config", lambda path=None: cfg)
    assert cli.main(["run"]) == 2
    assert "BELLHAVEN_CRM_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "example.com", "::"])
def test_serve_refuses_public_host(host, capsys):
    assert cli.main(["serve", "--host", host]) == 2
    assert "loopback or private" in capsys.readouterr().err


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "localhost", "::1", "10.1.2.3", "192.168.0.5", "100.101.102.103"]
)
def test_serve_allows_private_hosts(host):
    assert cli.host_allowed(host)


def test_flag_one_and_from_json(wired, tmp_path):
    cfg, _ = wired
    cli.main(["run"])
    with Ledger(cfg.db_path) as led:
        first, second = [c.id for c in led.cards("pending")][:2]
    assert cli.main(["flag", first, "check the street"]) == 0
    (tmp_path / "f.json").write_text(json.dumps([{"card_id": second, "reason": "check Sandusky"}]))
    assert cli.main(["flag", "--from-json", str(tmp_path / "f.json")]) == 0
    with Ledger(cfg.db_path) as led:
        assert led.card(first).flag == "check the street" and led.card(second).flag == "check Sandusky"


def test_flag_unknown_card_exits_1(wired, capsys):
    cli.main(["run"])
    assert cli.main(["flag", "nope", "x"]) == 1
    assert "No card nope" in capsys.readouterr().err
