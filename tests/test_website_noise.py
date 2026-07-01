"""A restaurant's Google-Places "website" is sometimes a social-media or Google-Maps
link, not a real site with a menu. `is_noise_website(url)` flags those so discovery
skips seeding them (and blanks the Brave domain so name-based recovery runs cleanly).

Delivery / menu aggregators (Uber Eats, e-food, Zomato, TripAdvisor) are NOT noise:
they carry menu content and must remain usable as sources.
"""

from __future__ import annotations

import pytest

from safeplate.extraction2.discover import is_noise_website


@pytest.mark.parametrize("url", [
    "https://www.instagram.com/some_bistro/",
    "https://instagram.com/some_bistro",
    "https://facebook.com/somecafe",
    "https://m.facebook.com/somecafe",
    "https://www.fb.com/somecafe",
    "https://fb.me/xyz",
    "https://www.tiktok.com/@bistro",
    "https://twitter.com/bistro",
    "https://x.com/bistro",
    "https://www.youtube.com/@bistro",
    "https://youtu.be/abc123",
    "https://www.linkedin.com/company/bistro",
    "https://pinterest.com/bistro",
    "https://threads.net/@bistro",
    "https://wa.me/15551234567",
    "https://t.me/bistro",
    "https://maps.google.com/?cid=123",
    "https://www.google.com/maps/place/Some+Bistro",
    "https://maps.app.goo.gl/abcXYZ",
    "https://goo.gl/maps/abcXYZ",
])
def test_social_and_maps_are_noise(url):
    assert is_noise_website(url) is True, url


@pytest.mark.parametrize("url", [
    "https://derpepperngror.no/",
    "https://www.example-bistro.com/menu",
    "https://sites.google.com/view/some-bistro",   # real menu host (Google Sites)
    "https://www.ubereats.com/store/some-bistro",  # aggregator: keeps menu
    "https://www.e-food.gr/delivery/athens/bistro",
    "https://www.zomato.com/athens/some-bistro",
    "https://www.tripadvisor.com/Restaurant_Review-bistro",
    "https://wolt.com/en/restaurant/bistro",
    "",
])
def test_real_sites_and_aggregators_are_not_noise(url):
    assert is_noise_website(url) is False, url
