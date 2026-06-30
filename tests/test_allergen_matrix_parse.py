"""R6 + R5(matrix): the HTML allergen-matrix parser must not mis-map columns
(colspan grouped headers), must not read icon/colour 'contains' cells as absent
(false nut-free), must handle 'Nuts (incl. coconut)' headers, and must UNION a
dish's allergens across tables."""
from safeplate.allergen_matrix import extract_items_from_allergen_matrix


def _terms(records, name):
    for r in records:
        if r.item_name.lower() == name.lower():
            return set(r.allergen_terms)
    return None


def test_colspan_grouped_header_does_not_shift_columns():
    # "Nuts" spans 2 sub-columns; a naive positional parse shifts every column right and
    # reads the Milk tick as the Egg answer. With colspan expansion the milk tick maps to
    # milk and the plain toast has no nut/egg.
    html = """<table><thead><tr>
      <th>Dish</th><th colspan="2">Nuts</th><th>Milk</th><th>Egg</th><th>Soy</th>
    </tr></thead><tbody>
      <tr><td>Plain Toast</td><td></td><td></td><td>x</td><td></td><td></td></tr>
      <tr><td>PB Cookie</td><td>x</td><td>x</td><td></td><td></td><td></td></tr>
    </tbody></table>"""
    recs = extract_items_from_allergen_matrix(html)
    assert _terms(recs, "Plain Toast") == {"milk"}
    assert "tree nut" in _terms(recs, "PB Cookie")


def test_icon_cell_counts_as_contains():
    # A "contains" mark rendered as an icon (no text) must not read as absent.
    html = """<table><thead><tr>
      <th>Dish</th><th>Peanut</th><th>Milk</th><th>Egg</th>
    </tr></thead><tbody>
      <tr><td>Satay</td><td><svg></svg></td><td></td><td></td></tr>
      <tr><td>Bread</td><td></td><td></td><td></td></tr>
    </tbody></table>"""
    assert "peanut" in _terms(extract_items_from_allergen_matrix(html), "Satay")


def test_colour_cell_counts_as_contains():
    html = """<table><thead><tr>
      <th>Dish</th><th>Peanut</th><th>Milk</th><th>Egg</th>
    </tr></thead><tbody>
      <tr><td>Pad Thai</td><td style="background:#cc0000"></td><td></td><td></td></tr>
      <tr><td>Rice</td><td></td><td></td><td></td></tr>
    </tbody></table>"""
    assert "peanut" in _terms(extract_items_from_allergen_matrix(html), "Pad Thai")


def test_coconut_inclusive_nut_header_still_recognized():
    html = """<table><thead><tr>
      <th>Dish</th><th>Nuts (incl. coconut)</th><th>Milk</th><th>Egg</th><th>Soy</th>
    </tr></thead><tbody>
      <tr><td>Walnut Cake</td><td>x</td><td></td><td></td><td></td></tr>
      <tr><td>Sorbet</td><td></td><td></td><td></td><td></td></tr>
    </tbody></table>"""
    assert "tree nut" in _terms(extract_items_from_allergen_matrix(html), "Walnut Cake")


def test_allergens_union_across_tables():
    html = """
    <table><thead><tr><th>Dish</th><th>Peanut</th><th>Milk</th><th>Egg</th></tr></thead>
      <tbody><tr><td>Brownie</td><td>x</td><td></td><td></td></tr>
             <tr><td>Tart</td><td>x</td><td></td><td></td></tr></tbody></table>
    <table><thead><tr><th>Dish</th><th>Tree nut</th><th>Soy</th><th>Gluten</th></tr></thead>
      <tbody><tr><td>Brownie</td><td>x</td><td></td><td></td></tr>
             <tr><td>Scone</td><td></td><td></td><td>x</td></tr></tbody></table>
    """
    recs = extract_items_from_allergen_matrix(html)
    assert _terms(recs, "Brownie") == {"peanut", "tree nut"}
