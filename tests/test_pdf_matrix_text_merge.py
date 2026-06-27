"""A TEXT-based allergen/ingredient PDF (e.g. Pressed Juicery's, ~117 products with a
facility cross-contact note) is UNDER-read by the Gemini vision matrix path (it found
22). The vision result must no longer SUPPRESS the plain-text LLM: run both and UNION,
with the matrix's allergen-bearing rows winning on dedupe, so the full catalog is
recovered WITHOUT losing allergen data (no tier regression)."""

from unittest import mock

from safeplate.extraction2 import pipeline
from safeplate.extraction2 import interpret_llm
from safeplate.extraction2.schema import Payload, PayloadKind, Policy
from safeplate.menu_text import MenuItemRecord


def _rec(name, allergens=()):
    return MenuItemRecord(
        restaurant_name="Pressed", restaurant_source_id="", menu_source_url="",
        category="", item_name=name, description="", price="",
        dietary_terms=[], allergen_terms=list(allergens), source_type="pdf",
        extraction_method=("gemini_allergen_matrix" if allergens else "gemini_text"),
        confidence=0.6, raw_text=name, fetched_at="",
    )


def _pdf_payload():
    return Payload(url="https://x.test/allergens.pdf", source_type="pdf",
                   kind=PayloadKind.TEXT,
                   text="Allergen info. Beauty Tonic. Carrot Juice. Greens 3.",
                   content=b"%PDF fake")


def test_allergen_pdf_unions_matrix_and_text_keeping_allergens():
    matrix_items = [_rec("Non-Dairy Almond Milk Chocolate", allergens=["tree nut"])]
    text_items = [
        _rec("Non-Dairy Almond Milk Chocolate"),  # dup of matrix -> matrix wins
        _rec("Beauty Tonic"),
        _rec("Carrot Juice"),
        _rec("Greens 3"),
    ]
    with mock.patch.object(pipeline, "interpret_structured", return_value=[]), \
         mock.patch.object(interpret_llm, "interpret_pdf_matrix", return_value=matrix_items), \
         mock.patch.object(interpret_llm, "interpret_text",
                           return_value=(text_items, False, 2)), \
         mock.patch.object(pipeline, "verify", side_effect=lambda items, p, require_grounding: (items, [])):
        result = pipeline.extract_menu(
            [_pdf_payload()], policy=Policy.HYBRID, llm_enabled=True, gemini_api_key="k"
        )

    names = {i.item_name for i in result.items}
    # Full catalog recovered (matrix ∪ text), not just the 1 matrix row.
    assert names == {"Non-Dairy Almond Milk Chocolate", "Beauty Tonic",
                     "Carrot Juice", "Greens 3"}
    # Allergen data preserved: the matrix row keeps its tree-nut flag (won the dedupe).
    almond = next(i for i in result.items if i.item_name == "Non-Dairy Almond Milk Chocolate")
    assert almond.allergen_terms == ["tree nut"]
    # Accounting: 1 vision + 2 text chunk calls.
    assert result.llm_calls == 3


def _comp(name, parent, allergens=()):
    return MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="", category="",
        item_name=name, description="", price="", dietary_terms=[],
        allergen_terms=list(allergens), source_type="pdf",
        extraction_method="gemini_allergen_matrix", confidence=0.6, raw_text=name,
        fetched_at="", is_component=True, parent_item=parent,
    )


def test_collapse_components_folds_allergens_and_drops_subrows():
    """Ingredient sub-rows fold their allergens UP into the parent dish and are dropped;
    no allergen is lost; an unattributable component is kept; non-components pass through."""
    items = [
        _rec("ShackBurger", allergens=["egg"]),              # top-level dish
        _comp("Burger Bun", "ShackBurger", ["wheat", "sesame"]),
        _comp("American Cheese", "ShackBurger", ["milk"]),
        _rec("Fries"),                                        # unrelated top-level dish
        _comp("Mystery Sauce", "Unknown Dish", ["soy"]),      # parent not present -> keep
    ]
    out = pipeline._collapse_components(items)
    names = [i.item_name for i in out]
    # Components folded away; parent + unrelated dish + unattributable component remain.
    assert names == ["ShackBurger", "Fries", "Mystery Sauce"]
    # No allergen lost: parent inherited the union of its components' allergens.
    burger = next(i for i in out if i.item_name == "ShackBurger")
    assert set(burger.allergen_terms) == {"egg", "wheat", "sesame", "milk"}
    # The unattributable component keeps its own allergen data (never silently dropped).
    sauce = next(i for i in out if i.item_name == "Mystery Sauce")
    assert sauce.allergen_terms == ["soy"]


def test_collapse_folds_across_symbol_differences_and_keeps_near_dupes():
    """A component's parent ('ShackBurger') folds into the dish even when the dish name
    carries a ®/™ ('ShackBurger®') -- the match key ignores symbols/punctuation. And
    near-duplicate top-level dishes are kept SEPARATE (not merged), each made
    allergen-complete."""
    items = [
        _rec("ShackBurger", allergens=["egg"]),
        _rec("ShackBurger®", allergens=["egg"]),          # near-dup, kept separate
        _comp("Burger Bun", "ShackBurger", ["wheat", "sesame"]),
    ]
    out = pipeline._collapse_components(items)
    assert [i.item_name for i in out] == ["ShackBurger", "ShackBurger®"]  # both kept
    for dish in out:  # component allergens folded into BOTH near-dups
        assert {"egg", "wheat", "sesame"} <= set(dish.allergen_terms)


def test_partial_text_grid_does_not_suppress_vision():
    """Scope A: a graphical allergen PDF whose text-grid parser salvages only a
    partial, mangled subset (e.g. Shake Shack: 14 mangled rows vs ~100 by vision)
    must STILL run the vision read and prefer it -- the partial parse can no longer
    short-circuit vision. Vision wins name conflicts; nothing allergen-bearing is lost."""
    # Deterministic text-grid: a partial, mangled parse (a fragment + a real row).
    structured_items = [_rec("Bacon Breakfast", allergens=["soy"]),  # mangled fragment
                        _rec("Hot Dog", allergens=["wheat"])]        # also seen by vision
    matrix_items = [_rec("Bacon Breakfast Sandwich", allergens=["tree nut"]),
                    _rec("Hot Dog", allergens=["milk", "wheat"]),    # conflict -> vision wins
                    _rec("ShackBurger", allergens=["egg"])]
    text_items = [_rec("Fries")]
    with mock.patch.object(pipeline, "interpret_structured", return_value=structured_items), \
         mock.patch.object(interpret_llm, "interpret_pdf_matrix", return_value=matrix_items) as vis, \
         mock.patch.object(interpret_llm, "interpret_text", return_value=(text_items, False, 1)), \
         mock.patch.object(pipeline, "verify", side_effect=lambda items, p, require_grounding: (items, [])):
        result = pipeline.extract_menu(
            [_pdf_payload()], policy=Policy.HYBRID, llm_enabled=True, gemini_api_key="k"
        )

    vis.assert_called_once()  # vision ran despite a non-empty text-grid parse
    names = {i.item_name for i in result.items}
    # Vision's full read present; the text-grid rows are unioned in (no allergen loss).
    assert {"Bacon Breakfast Sandwich", "Hot Dog", "ShackBurger"} <= names
    # Vision has the full chart (text LLM found only 1 vs vision's 3) -> the text LLM's
    # "Fries" is dropped as padding, not unioned in.
    assert "Fries" not in names
    # Vision wins the conflict on a shared dish (its richer allergen read is kept).
    hotdog = next(i for i in result.items if i.item_name == "Hot Dog")
    assert hotdog.allergen_terms == ["milk", "wheat"]


def test_text_llm_kept_when_vision_underreads():
    """When the text reader finds materially MORE dishes than vision (vision under-read
    a TEXT-based PDF, e.g. Pressed 22 vs ~117), its catalog IS unioned in -- so we don't
    regress that recovery."""
    matrix_items = [_rec("Almond Milk Chocolate", allergens=["tree nut"])]  # vision: 1
    text_items = [_rec("Almond Milk Chocolate"), _rec("Beauty Tonic"),
                  _rec("Carrot Juice"), _rec("Greens 3")]                    # text: 4 (>1.5x)
    with mock.patch.object(pipeline, "interpret_structured", return_value=[]), \
         mock.patch.object(interpret_llm, "interpret_pdf_matrix", return_value=matrix_items), \
         mock.patch.object(interpret_llm, "interpret_text", return_value=(text_items, False, 1)), \
         mock.patch.object(pipeline, "verify", side_effect=lambda items, p, require_grounding: (items, [])):
        result = pipeline.extract_menu(
            [_pdf_payload()], policy=Policy.HYBRID, llm_enabled=True, gemini_api_key="k"
        )
    names = {i.item_name for i in result.items}
    assert {"Almond Milk Chocolate", "Beauty Tonic", "Carrot Juice", "Greens 3"} <= names
