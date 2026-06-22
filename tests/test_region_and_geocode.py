from __future__ import annotations

from safeplate.allergen_prior import region_from_address
from safeplate.geo import _best_place


# --- region_from_address: coords fallback (was ignoring lat/lon) ---------------

def test_region_from_address_string_wins():
    assert region_from_address("123 Main St, San Jose, CA 95129") == "US"


def test_region_falls_back_to_coords_when_address_unrecognized():
    # No country/state in the string, but US coords -> US (not 'unknown').
    assert region_from_address("Mystery Bistro", latitude=37.4, longitude=-122.1) == "US"
    assert region_from_address(None, latitude=-33.8, longitude=151.2) == "AU"
    assert region_from_address("Cafe", latitude=51.5, longitude=-0.12) == "GB"


def test_region_unknown_when_both_fail():
    assert region_from_address("Nowhere", latitude=0.0, longitude=0.0) == "unknown"
    assert region_from_address(None) == "unknown"


# --- _best_place: same-named city must rank by importance ----------------------

def test_geocode_picks_real_city_over_samename_village():
    cands = [
        {"category": "boundary", "type": "administrative", "importance": 0.568,
         "lat": "37.3893889", "lon": "-122.0832101", "display_name": "Mountain View, Santa Clara"},
        {"category": "place", "type": "village", "importance": 0.414,
         "lat": "38.0088", "lon": "-122.117", "display_name": "Mountain View, Martinez"},
    ]
    best = _best_place(cands)
    assert best["lat"] == "37.3893889"  # the real Mountain View, not the Martinez village


def test_geocode_prefers_place_over_offcentre_feature():
    cands = [
        {"category": "amenity", "type": "restaurant", "importance": 0.9,
         "lat": "1.0", "lon": "1.0", "display_name": "Some random feature"},
        {"category": "place", "type": "city", "importance": 0.5,
         "lat": "2.0", "lon": "2.0", "display_name": "The City"},
    ]
    assert _best_place(cands)["lat"] == "2.0"  # populated place beats a high-importance feature
