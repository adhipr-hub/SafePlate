from safeplate import community_signals as C


def test_grounded_community_diet_signal_built():
    snippets = "Reviewers rave that many dishes can be made vegan here."
    parsed = {"handling": [], "dishes": [],
              "diet_flexibility": [{"diet": "vegan",
                                    "quote": "many dishes can be made vegan"}]}
    res = C._build_result(parsed, snippets=snippets, urls=["https://r.test/1"],
                          restaurant_name="Test Cafe", want_dishes=False)
    assert any(s.diet == "vegan" and s.source == "community" for s in res.diet_signals)


def test_ungrounded_community_diet_signal_dropped():
    parsed = {"handling": [], "dishes": [],
              "diet_flexibility": [{"diet": "vegan", "quote": "fully vegan menu"}]}
    res = C._build_result(parsed, snippets="Great tacos and margaritas.",
                          urls=["https://r.test/1"], restaurant_name="Test Cafe",
                          want_dishes=False)
    assert res.diet_signals == []
