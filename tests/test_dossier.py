"""Tests for the Deep-Dive Dossier prototype orchestrator (safeplate/dossier.py).

Network + LLM are stubbed: the pipeline itself is covered by its own suites; here we
prove the ORCHESTRATION — query parsing, name-matching, the streamed stage sequence,
the assembled payload shape, and the safety-asymmetric degrade (a failed deeper-site
scan is reported but can never move the verdict toward 'safe')."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from safeplate import dossier
from safeplate.dossier import (
    DeeperSite,
    DeeperSiteSignal,
    Target,
    assemble_dossier,
    build_target,
    iter_dossier_events,
    params_from_query,
    _best_name_match,
)


def parse_events(chunks):
    """Turn an SSE stream (list of frame strings) into [(event, data|None)]."""
    events = []
    for block in "".join(chunks).split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        events.append((event, json.loads(data) if data else None))
    return events


# A representative extraction response + deeper-site result reused across flow tests.
_EXTRACTION = {
    "summary": {
        "tier": "caution",
        "overallRisk": 0.55,
        "overallConfidence": 0.6,
        "evidenceBasis": "menu_coverage",
        "itemCount": 20,
        "menuBackedRisk": {
            "rationale": ["one dessert names peanuts"],
            "riskiestItems": [{"itemName": "Peanut Sundae", "risk": 0.95, "suspected": False}],
            "evidence": [],
        },
        "restaurantSignals": {"has_allergy_disclaimer": True},
        "regionNotice": None,
        "perAllergen": [],
    },
    "communityQuotes": ["They were great with my son's nut allergy."],
    "coverage": [{"found": True}],
}
_DEEPER = DeeperSite(
    pages_scanned=["http://x.com", "http://x.com/allergens"],
    signals=[DeeperSiteSignal("http://x.com/allergens", ["Tell your server about allergies."],
                              True, False, True, True, False)],
    social_links=["https://instagram.com/x"],
)
_ROWS = {
    "rows": [
        {"name": "Wagamama Soho", "website_url": "http://wagamama.com", "address": "1 Soho St",
         "categories": ["japanese"], "latitude": 51.5, "longitude": -0.1,
         "phone_number": "020 100", "rating": 4.2, "review_count": 900},
        {"name": "Pizza Express", "website_url": "http://pe.com", "address": "2 Soho St",
         "categories": ["italian"], "latitude": 51.5, "longitude": -0.1},
    ]
}


class ParamsTests(unittest.TestCase):
    def test_parses_target_and_profile(self):
        p = params_from_query(
            "name=Wagamama&location=Soho%20London&severity=anaphylaxis"
            "&crossContact=strict&nutTypes=peanut,%20cashew"
        )
        self.assertEqual(p["name"], "Wagamama")
        self.assertEqual(p["location"], "Soho London")
        self.assertEqual(p["severity"], "anaphylaxis")
        self.assertEqual(p["crossContact"], "strict")
        self.assertEqual(p["nutTypes"], ["peanut", "cashew"])
        self.assertEqual(p["scoringEngine"], "rules")  # default

    def test_defaults(self):
        p = params_from_query("name=X")
        self.assertEqual(p["severity"], "allergy")
        self.assertEqual(p["provider"], "auto")
        self.assertNotIn("crossContact", p)


class NameMatchTests(unittest.TestCase):
    def test_prefix_match_wins_nearest(self):
        self.assertEqual(_best_name_match(_ROWS["rows"], "wagamama")["name"], "Wagamama Soho")

    def test_case_insensitive_exact(self):
        rows = [{"name": "Nando's"}]
        self.assertEqual(_best_name_match(rows, "nandos")["name"], "Nando's")

    def test_no_match_returns_none(self):
        self.assertIsNone(_best_name_match(_ROWS["rows"], "Five Guys"))

    def test_empty_name_returns_none(self):
        self.assertIsNone(_best_name_match(_ROWS["rows"], ""))


class BuildTargetTests(unittest.TestCase):
    def test_direct_url_bypasses_places(self):
        t = build_target({"url": "wagamama.com"})
        self.assertEqual(t.resolved_via, "url")
        self.assertEqual(t.website_url, "https://wagamama.com")

    def test_places_resolution_and_name_match(self):
        with patch.object(dossier, "run_restaurant_search", lambda payload, demo_mode=False: _ROWS):
            t = build_target({"name": "Wagamama", "location": "London"})
        self.assertEqual(t.resolved_via, "places")
        self.assertEqual(t.website_url, "http://wagamama.com")
        self.assertEqual(t.phone, "020 100")

    def test_no_match_no_url_returns_none(self):
        with patch.object(dossier, "run_restaurant_search", lambda payload, demo_mode=False: {"rows": []}):
            self.assertIsNone(build_target({"name": "Nowhere", "location": "London"}))


class AssembleTests(unittest.TestCase):
    def test_full_payload_shape(self):
        d = assemble_dossier(
            target=Target(name="X", website_url="http://x.com", categories=["thai"]),
            extraction=_EXTRACTION, deeper=_DEEPER, elapsed=0.2,
        )
        self.assertTrue(d["verdict"]["verified"])
        self.assertEqual(d["verdict"]["tier"], "caution")
        self.assertEqual(d["dishes"]["watch"][0]["itemName"], "Peanut Sundae")
        self.assertEqual(d["dishes"]["parsedCount"], 20)
        self.assertEqual(d["dishes"]["otherCount"], 19)
        self.assertEqual(d["community"], _EXTRACTION["communityQuotes"])
        self.assertEqual(len(d["deeperSite"]["signals"]), 1)
        self.assertEqual(d["deeperSite"]["socialLinks"], ["https://instagram.com/x"])

    def test_missing_extraction_degrades_to_unverified(self):
        d = assemble_dossier(
            target=Target(name="X", website_url="http://x.com"),
            extraction={}, deeper=DeeperSite(), elapsed=0.1,
        )
        self.assertFalse(d["verdict"]["verified"])
        self.assertEqual(d["verdict"]["tier"], "unknown")  # never a fabricated 'safe'


class StreamTests(unittest.TestCase):
    def _run(self, params):
        return parse_events(list(iter_dossier_events(params)))

    def test_happy_path_stage_sequence(self):
        with patch.object(dossier, "run_restaurant_search", lambda p, demo_mode=False: _ROWS), \
             patch.object(dossier, "run_menu_extraction", lambda p, demo_mode=False: _EXTRACTION), \
             patch.object(dossier, "scan_deeper_site", lambda *a, **k: _DEEPER):
            events = self._run({"name": "Wagamama", "location": "London"})
        self.assertEqual(
            [e for e, _ in events],
            ["start", "stage_start", "stage_done", "stage_start", "stage_done",
             "stage_start", "stage_done", "dossier", "done"],
        )
        dossier_ev = next(d for e, d in events if e == "dossier")
        self.assertEqual(dossier_ev["verdict"]["tier"], "caution")

    def test_deeper_site_failure_reports_but_cannot_change_verdict(self):
        def boom(*a, **k):
            raise RuntimeError("scan exploded")

        with patch.object(dossier, "run_restaurant_search", lambda p, demo_mode=False: _ROWS), \
             patch.object(dossier, "run_menu_extraction", lambda p, demo_mode=False: _EXTRACTION), \
             patch.object(dossier, "scan_deeper_site", boom):
            events = self._run({"name": "Wagamama", "location": "London"})
        types = [e for e, _ in events]
        self.assertIn("stage_error", types)
        # The verdict still reflects the extraction (caution), NOT softened to likely_ok.
        dossier_ev = next(d for e, d in events if e == "dossier")
        self.assertEqual(dossier_ev["verdict"]["tier"], "caution")

    def test_resolve_failure_stops_before_dossier(self):
        with patch.object(dossier, "run_restaurant_search", lambda p, demo_mode=False: {"rows": []}):
            events = self._run({"name": "Nowhere", "location": "London"})
        types = [e for e, _ in events]
        self.assertIn("error", types)
        self.assertNotIn("dossier", types)
        self.assertNotIn("done", types)


class CandidateExtrasParamTests(unittest.TestCase):
    def test_parses_chosen_candidate_fields(self):
        p = params_from_query(
            "name=Taco%20Bell&website=http://tb.com&address=1%20Main%20St"
            "&lat=37.0&lon=-122.0&rating=3.5&reviewCount=200"
        )
        self.assertEqual(p["website"], "http://tb.com")
        self.assertEqual(p["address"], "1 Main St")
        self.assertEqual(p["lat"], "37.0")
        self.assertEqual(p["rating"], "3.5")
        self.assertEqual(p["reviewCount"], "200")


class BuildTargetCandidateTests(unittest.TestCase):
    def test_chosen_candidate_is_places_and_rich(self):
        t = build_target({
            "name": "Taco Bell", "website": "http://tb.com", "address": "1 Main St",
            "lat": "37.0", "lon": "-122.0", "phone": "555-0100", "rating": "3.5", "reviewCount": "200",
        })
        self.assertEqual(t.resolved_via, "places")  # a candidate (has address), not a bare URL
        self.assertEqual(t.website_url, "http://tb.com")
        self.assertEqual(t.address, "1 Main St")
        self.assertEqual(t.phone, "555-0100")
        self.assertEqual(t.latitude, 37.0)
        self.assertEqual(t.review_count, 200)

    def test_url_only_is_url(self):
        t = build_target({"url": "tb.com"})
        self.assertEqual(t.resolved_via, "url")
        self.assertEqual(t.website_url, "https://tb.com")

    def test_candidate_without_website_still_builds(self):
        t = build_target({"name": "Sweet Maple", "address": "2101 Sutter St", "lat": "37.7", "lon": "-122.4"})
        self.assertIsNotNone(t)
        self.assertEqual(t.website_url, "")  # deep-extract will run community/prior only
        self.assertEqual(t.resolved_via, "places")


class FindCandidatesTests(unittest.TestCase):
    def _rec(self, **kw):
        from types import SimpleNamespace
        base = dict(name="Taco Bell", address="1 Main St", website_url="http://tb.com",
                    latitude=37.0, longitude=-122.0, distance_meters=1500.0,
                    rating=3.5, review_count=200, source_id="abc")
        base.update(kw)
        return SimpleNamespace(**base)

    def test_short_name_returns_empty(self):
        self.assertEqual(dossier.find_candidates({"name": "a"}), [])

    def test_text_search_path(self):
        rec = self._rec()
        with patch.object(dossier, "get_google_places_api_key", lambda: "KEY"), \
             patch("safeplate.providers.google_places.text_search_restaurants",
                   lambda **kw: [rec]):
            out = dossier.find_candidates({"name": "Taco Bell", "lat": "37.0", "lon": "-122.0"})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "Taco Bell")
        self.assertEqual(out[0]["website"], "http://tb.com")
        self.assertEqual(out[0]["distanceKm"], 1.5)

    def test_fallback_to_nearby_when_no_key(self):
        rows = {"rows": [
            {"name": "Taco Bell #22", "address": "9 Elm", "website_url": "http://x",
             "latitude": 1.0, "longitude": 2.0, "distance_meters": 800, "rating": 3.0,
             "review_count": 10, "source_id": "z"},
            {"name": "Sushi Zone", "address": "", "website_url": "", "distance_meters": 100},
        ]}
        with patch.object(dossier, "get_google_places_api_key", lambda: ""), \
             patch.object(dossier, "run_restaurant_search", lambda payload, demo_mode=False: rows):
            out = dossier.find_candidates({"name": "Taco Bell", "lat": "1.0", "lon": "2.0"})
        self.assertEqual([c["name"] for c in out], ["Taco Bell #22"])  # name-filtered
        self.assertEqual(out[0]["distanceKm"], 0.8)


if __name__ == "__main__":
    unittest.main()
