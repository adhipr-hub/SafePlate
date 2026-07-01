"""Multi-location brands put each location's menu on its own subdomain
(radhusplassen.derpepperngror.no). Seeded on the brand apex, discovery must follow
the subdomain that matches the diner's ADDRESS, so it can reach that location's menu
instead of finding nothing.
"""

from __future__ import annotations

from safeplate.extraction2.discover import (
    _address_locality_tokens,
    _address_matched_subdomain_seeds,
)

APEX = "https://derpepperngror.no/"
LINKS = [
    ("https://derpepperngror.no/om-oss", "Om oss"),
    ("https://radhusplassen.derpepperngror.no/", "Rådhusplassen"),
    ("https://akerbrygge.derpepperngror.no/", "Aker Brygge"),
    ("https://bogstadveien.derpepperngror.no/", "Bogstadveien"),
    ("https://www.facebook.com/pepper", "Facebook"),
]


def test_matches_subdomain_to_address_locality():
    seeds = _address_matched_subdomain_seeds(
        LINKS, website_url=APEX, address="Rådhusplassen 1, 0151 Oslo, Norway"
    )
    assert seeds == ["https://radhusplassen.derpepperngror.no/"]


def test_accents_are_folded_when_matching():
    # Address "Rådhusplassen" (å) must match subdomain label "radhusplassen" (a).
    tokens = _address_locality_tokens("Rådhusplassen, Oslo")
    assert "radhusplassen" in tokens


def test_multiword_locality_matches_collapsed_subdomain():
    seeds = _address_matched_subdomain_seeds(
        LINKS, website_url=APEX, address="Aker Brygge, 0250 Oslo, Norway"
    )
    assert "https://akerbrygge.derpepperngror.no/" in seeds


def test_only_the_matching_location_is_seeded():
    seeds = _address_matched_subdomain_seeds(
        LINKS, website_url=APEX, address="Rådhusplassen, Oslo"
    )
    assert "https://akerbrygge.derpepperngror.no/" not in seeds
    assert "https://bogstadveien.derpepperngror.no/" not in seeds


def test_apex_and_offsite_hosts_never_seeded():
    seeds = _address_matched_subdomain_seeds(
        LINKS, website_url=APEX, address="Rådhusplassen, Oslo"
    )
    assert "https://derpepperngror.no/" not in seeds
    assert all("facebook.com" not in s for s in seeds)


def test_no_address_returns_nothing():
    assert _address_matched_subdomain_seeds(LINKS, website_url=APEX, address="") == []


def test_no_matching_subdomain_returns_nothing():
    # A locality with no corresponding subdomain -> nothing to follow.
    seeds = _address_matched_subdomain_seeds(
        LINKS, website_url=APEX, address="Trondheim, Norway"
    )
    assert seeds == []


def test_already_on_subdomain_does_not_reseed_itself():
    seeds = _address_matched_subdomain_seeds(
        LINKS, website_url="https://radhusplassen.derpepperngror.no/",
        address="Rådhusplassen, Oslo",
    )
    assert "https://radhusplassen.derpepperngror.no/" not in seeds
