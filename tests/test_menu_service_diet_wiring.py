from types import SimpleNamespace

from safeplate.menu_service import _diet_summary_payload


def _item(name, dietary=()):
    return SimpleNamespace(item_name=name, dietary_terms=list(dietary), allergen_terms=[])


def test_payload_includes_basis_and_notes():
    items = [_item("House Salad", dietary=["vegan"]), _item("Tofu Bowl", dietary=["vegan"])]
    payload = _diet_summary_payload(["vegan"], items)
    assert payload and "basis" in payload[0] and "notes" in payload[0]


def test_no_diets_yields_empty_payload():
    assert _diet_summary_payload([], [_item("Anything")]) == []
