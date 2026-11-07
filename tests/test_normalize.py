from hypothesis import given
from hypothesis import strategies as st

from bellhaven_sync.normalize import (
    clean_text,
    is_po_box,
    map_care,
    name_tokens,
    norm_phone,
    norm_street,
    norm_zip,
)


def test_street_abbreviations():
    assert norm_street("199 Barks Rd W") == norm_street("199 Barks Road West") == "199 barks road west"
    assert norm_street("4850 NW Sylvania Ave") == "4850 northwest sylvania avenue"
    assert norm_street("2401 Wilmington Pk") == "2401 wilmington pike"


def test_st_is_street_or_saint():
    assert norm_street("45 St Lawrence Dr") == "45 saint lawrence drive"
    assert norm_street("341 Chestnut St") == "341 chestnut street"
    assert norm_street("1250 NW Franklin St") == norm_street("1250 Northwest Franklin Street")


def test_po_box():
    assert is_po_box("PO Box 517") and is_po_box("P.O. Box 9") and is_po_box("p o box 12")
    assert not is_po_box("12 Box Elder Rd")


def test_phone_zip_care():
    assert norm_phone("(260) 589-2589") == norm_phone("+1 260.589.2589") == "2605892589"
    assert norm_zip("45662-1234") == "45662"
    assert map_care("Short-Term Rehabilitation & Nursing") == "Skilled Nursing"
    assert map_care("Memory Support") == "Memory Care"
    assert map_care("Assisted Living") == "Assisted Living"


def test_name_tokens_drop_filler():
    assert name_tokens("Bellhaven Rehabilitation & Nursing of Grove City") == {
        "rehabilitation", "and", "nursing", "grove", "city",
    }


def test_entities_and_spaces():  # Review Focus 5
    assert clean_text("Bellhaven\xa0of  Tiffin ") == "Bellhaven of Tiffin"
    assert clean_text("Rehabilitation &amp; Nursing") == "Rehabilitation & Nursing"


@given(st.text())
def test_norm_street_idempotent(s):
    assert norm_street(norm_street(s)) == norm_street(s)
