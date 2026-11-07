import pytest
from conftest import OPERATOR_ID, SNAP, full_id
from fake_crm import FakeCrm

from bellhaven_sync.crm import CrmClient, CrmError, ReadOnlyError


class _Response:
    def __init__(self, status: int, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class PagingStub:
    """A session whose account list returns pages of the given sizes."""

    def __init__(self, sizes: list[int]):
        self.sizes, self.calls = sizes, []

    def request(self, method, url, params=None, **kwargs):
        self.calls.append(params)
        size = self.sizes[params["page"] - 1]
        return _Response(200, {"data": [{"account_id": f"{params['page']}-{i}"} for i in range(size)]})


class ErrorStub:
    def __init__(self, status: int, body):
        self.status, self.body = status, body

    def request(self, method, url, **kwargs):
        return _Response(self.status, self.body)


def test_read_only_client_refuses_every_write():
    c = CrmClient("http://x", "t")
    for call in (
        lambda: c.patch_account("a", {}),
        lambda: c.patch_contact("c", {}),
        lambda: c.create_account({}),
        lambda: c.create_contact({}),
    ):
        with pytest.raises(ReadOnlyError):
            call()


def test_pagination_reads_until_short_page():
    stub = PagingStub([200, 200, 7])
    assert len(CrmClient("http://x", "t", session=stub).list_accounts()) == 407
    assert [p["page"] for p in stub.calls] == [1, 2, 3] and stub.calls[0]["page_size"] == 200


def test_error_carries_detail_not_token():
    with pytest.raises(CrmError) as e:
        CrmClient(
            "http://x", "SECRET", read_only=False, session=ErrorStub(422, {"detail": "bad field"})
        ).create_account({})
    assert e.value.status == 422 and "bad field" in str(e.value) and "SECRET" not in str(e.value)


def test_validation_error_list_is_readable():  # FastAPI-style 422 detail
    body = {"detail": [{"loc": ["body", "status"], "msg": "Input should be 'Active'"}]}
    with pytest.raises(CrmError) as e:
        CrmClient("http://x", "t", read_only=False, session=ErrorStub(422, body)).patch_account(
            "a", {"status": "x"}
        )
    assert "status" in e.value.message and "Input should be 'Active'" in e.value.message


def test_fake_loads_snapshot_and_derives_parent_name():
    f = FakeCrm.from_snapshot(SNAP)
    assert len(f.list_accounts()) == 121 and len(f.list_contacts()) == 67
    lima = f.get_account(full_id("9MB6KL"))
    f.patch_account(lima["account_id"], {"parent_id": OPERATOR_ID})
    assert f.get_account(lima["account_id"])["parent_name"] == "Bellhaven Senior Living (Parent Account)"
    assert f.writes == [("PATCH", f"/accounts/{lima['account_id']}", {"parent_id": OPERATOR_ID})]


def test_fake_create_marks_candidate_records():
    f = FakeCrm.from_snapshot(SNAP)
    a = f.create_account({"name": "Bellhaven of Batavia", "parent_id": OPERATOR_ID})
    assert a["account_id"].startswith("001FAKE") and len(a["account_id"]) == 18
    assert a["created_by_candidate"] is True and a["parent_name"].startswith("Bellhaven Senior Living")


def test_fake_failure_hooks():
    f = FakeCrm.from_snapshot(SNAP)
    f.fail_next("PATCH", 500, "boom")
    with pytest.raises(CrmError):
        f.patch_account(full_id("9MB6KL"), {"name": "x"})
    assert f.get_account(full_id("9MB6KL"))["name"] == "Bellhaven Crossings of Lima" and f.writes == []
    f.reject_fields({"care_type"})
    with pytest.raises(CrmError) as e:
        f.patch_account(full_id("9MB6KL"), {"care_type": "x"})
    assert e.value.status == 422
