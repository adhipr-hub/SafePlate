"""api_capture must stamp a content-locale region on its coverage so backend-captured
allergen data participates in the from-another-region banner (the S4 review finding:
captured items were appended to result.items with no CoverageReport, so a wrong-country
backend feed showed with no notice)."""
import dataclasses

from safeplate.menu_text import MenuItemRecord
import safeplate.extraction2.api_capture as ac


def _rec(name: str) -> MenuItemRecord:
    fields = {f.name: "" for f in dataclasses.fields(MenuItemRecord)}
    fields.update(item_name=name, dietary_terms=[], allergen_terms=["peanut"], confidence=0.9)
    return MenuItemRecord(**fields)


def test_capture_coverage_detects_region_from_response_text():
    # Endpoint host is a country-neutral CDN; the response body cites a .co.nz domain,
    # which is the decisive NZ tell.
    text = '{"site":"burgerking.co.nz","items":[{"name":"Whopper"}]}'
    cov = ac._capture_coverage("https://cdn.example.com/api/allergens.json", [_rec("Whopper")], text)
    assert cov.region == "NZ"
    assert cov.found is True and cov.item_count == 1 and cov.interpreter == "api_capture"


def test_capture_coverage_no_tell_is_blank_region():
    cov = ac._capture_coverage("https://cdn.example.com/api/x.json",
                               [_rec("Whopper")], '{"items":[{"name":"Whopper"}]}')
    assert cov.region == ""  # conservative: no false region claim


def test_capture_allergen_api_returns_coverage_with_region(monkeypatch):
    # Drive the assembly without network: stub the page fetch + endpoint discovery,
    # and have one endpoint yield a record plus NZ-tell text.
    monkeypatch.setattr(ac, "fetch_html_page",
                        lambda *a, **k: type("P", (), {"html": "<html></html>"})())
    monkeypatch.setattr(ac, "_candidate_endpoints",
                        lambda *a, **k: ["https://cdn.example.com/api/allergens.json"])
    monkeypatch.setattr(
        ac, "_allergens_from_endpoint",
        lambda url, ua: ([_rec("Whopper")], "served by burgerking.co.nz"),
    )
    records, coverage = ac.capture_allergen_api("https://x.com/menu", user_agent="UA")
    assert [r.item_name for r in records] == ["Whopper"]
    assert len(coverage) == 1 and coverage[0].region == "NZ"


def test_capture_allergen_api_empty_when_no_endpoints(monkeypatch):
    monkeypatch.setattr(ac, "fetch_html_page",
                        lambda *a, **k: type("P", (), {"html": "<html></html>"})())
    monkeypatch.setattr(ac, "_candidate_endpoints", lambda *a, **k: [])
    assert ac.capture_allergen_api("https://x.com/menu", user_agent="UA") == ([], [])
