"""verify() must ground an item on its verbatim evidence_quote, not only its
composed item_name.

Regression: the text interpreter is prompted to emit one row per size, so it names
items like "Cicero's Special (Small)" -- a label it COMPOSES, which never appears
verbatim in the source. Grounding only the name dropped every size variant (a pizza
menu collapsed to its 2 unsized salads). The model also copies a verbatim
`evidence_quote` for every item; grounding that keeps real items while still
rejecting invented ones (a fabricated dish has no quote that appears in the source).
"""

import unittest

from safeplate.extraction2.schema import Payload, PayloadKind
from safeplate.extraction2.verify import verify
from safeplate.menu_text import MenuItemRecord

# A faithful slice of the kind of text a pizza-menu PDF yields: the dish name, its
# S/M/L prices, and toppings -- with "Small Medium Large" only as a column header,
# never attached to a dish name.
SOURCE = (
    "Small Medium Large "
    "Cicero's Special 14.50 25.25 34.50 - Cheese, Olives, Mushrooms, Salami "
    "Baker's Pride 14.50 25.25 34.50 - Cheese, Mushrooms, Sausage, Onions "
    "House Salad - Choice of Italian, Blue Cheese, Ranch"
)


def _rec(item_name: str, evidence_quote: str) -> MenuItemRecord:
    return MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="https://x/menu",
        category="", item_name=item_name, description="", price="",
        dietary_terms=[], allergen_terms=[], source_type="pdf",
        extraction_method="gemini_text", confidence=0.5,
        raw_text=evidence_quote, fetched_at="",
    )


def _payload() -> Payload:
    return Payload(url="https://x/menu", source_type="pdf",
                   kind=PayloadKind.TEXT, text=SOURCE)


class VerifyEvidenceQuoteTest(unittest.TestCase):
    def test_size_decorated_name_kept_when_quote_is_grounded(self) -> None:
        # Name ("... (Small)") is NOT verbatim in source, but the evidence quote is.
        items = [_rec("Cicero's Special (Small)", "Cicero's Special 14.50 25.25 34.50")]
        kept, dropped = verify(items, _payload(), require_grounding=True)
        self.assertEqual([k.item_name for k in kept], ["Cicero's Special (Small)"])
        self.assertEqual(dropped, [])

    def test_all_size_variants_survive(self) -> None:
        items = [
            _rec("Cicero's Special (Small)", "Cicero's Special 14.50 25.25 34.50"),
            _rec("Cicero's Special (Medium)", "Cicero's Special 14.50 25.25 34.50"),
            _rec("Baker's Pride (Large)", "Baker's Pride 14.50 25.25 34.50"),
        ]
        kept, dropped = verify(items, _payload(), require_grounding=True)
        self.assertEqual(len(kept), 3)
        self.assertEqual(dropped, [])

    def test_hallucinated_item_still_dropped(self) -> None:
        # A fabricated dish whose quote is NOT in the source -> still rejected.
        items = [_rec("Truffle Risotto", "Truffle Risotto 28.00 wild mushroom cream")]
        kept, dropped = verify(items, _payload(), require_grounding=True)
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)

    def test_verbatim_name_without_size_still_kept(self) -> None:
        # The unchanged happy path: a plain name that IS in the source.
        items = [_rec("House Salad", "House Salad - Choice of Italian")]
        kept, dropped = verify(items, _payload(), require_grounding=True)
        self.assertEqual([k.item_name for k in kept], ["House Salad"])
        self.assertEqual(dropped, [])

    def test_size_variant_recovered_without_relying_on_quote(self) -> None:
        # The legit size-variant case must ground via the size-stripped NAME, so it
        # works even when the evidence quote is missing — no quote-trust needed.
        items = [_rec("Cicero's Special (Small)", "")]
        kept, dropped = verify(items, _payload(), require_grounding=True)
        self.assertEqual([k.item_name for k in kept], ["Cicero's Special (Small)"])

    def test_fabricated_name_with_generic_grounded_quote_dropped(self) -> None:
        # The anti-hallucination hole the review caught: a FABRICATED dish name paired
        # with a generic quote that happens to be verbatim in the source ("Small Medium
        # Large" is a column header) must NOT ground — the quote doesn't name the dish.
        items = [_rec("Peanut-Free Brownie", "Small Medium Large")]
        kept, dropped = verify(items, _payload(), require_grounding=True)
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)


if __name__ == "__main__":
    unittest.main()
