from safeplate.extraction2.embedded_allergens import (
    extract_allergen_items_from_embedded_json,
)


def _by_name(items):
    return {i.item_name: set(i.allergen_terms) for i in items}


def test_allergen_array_in_next_data():
    html = """
    <script id="__NEXT_DATA__" type="application/json">
    {"props":{"items":[
        {"name":"Pad Thai","allergens":["Peanut","Egg","Soy"]},
        {"name":"Green Salad","allergens":[]}
    ]}}
    </script>
    """
    by_name = _by_name(extract_allergen_items_from_embedded_json(html))
    assert "Pad Thai" in by_name
    assert {"peanut", "egg", "soy"} <= by_name["Pad Thai"]
    # No allergens -> not emitted as an allergen record.
    assert "Green Salad" not in by_name


def test_flag_map_shape():
    html = """
    <script type="application/json">
    {"menu":[{"title":"Cheeseburger","allergenInfo":{"milk":true,"egg":true,"peanut":false}}]}
    </script>
    """
    by_name = _by_name(extract_allergen_items_from_embedded_json(html))
    assert by_name["Cheeseburger"] == {"milk", "egg"}  # false flag dropped


def test_flag_object_list_with_contains():
    html = """
    <script type="application/json">
    {"data":[{"name":"Katsu Curry","allergens":[
        {"name":"Wheat","contains":true},
        {"name":"Sesame","contains":false}
    ]}]}
    </script>
    """
    by_name = _by_name(extract_allergen_items_from_embedded_json(html))
    assert by_name["Katsu Curry"] == {"wheat"}


def test_inline_nuxt_state_blob():
    html = """
    <script>window.__NUXT__={"data":[{"name":"Almond Cake","allergens":["Almond","Milk"]}]};</script>
    """
    by_name = _by_name(extract_allergen_items_from_embedded_json(html))
    assert "Almond Cake" in by_name
    assert {"almond", "milk"} <= by_name["Almond Cake"]


def test_no_allergen_structure_returns_nothing():
    html = '<script type="application/json">{"items":[{"name":"Fries","price":399}]}</script>'
    assert extract_allergen_items_from_embedded_json(html) == []
