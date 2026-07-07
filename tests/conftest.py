import pytest


@pytest.fixture(autouse=True)
def _shield_database_url(monkeypatch):
    """Tests must never touch a real database.

    On a machine with DATABASE_URL exported (e.g. the EC2 host after the RDS
    cutover), any test that calls cache_store.save/load would otherwise write
    junk into the production cache_entries table. Stripping DATABASE_URL and
    resetting the cache_store pool state before (and after) every test forces
    all tests onto the disk backend by default.

    The opt-in live test (test_live_database_round_trip) sets DATABASE_URL
    itself inside the test body, which runs after this fixture's setup --
    so it still works.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import safeplate.cache_store as cache_store

    cache_store._reset_for_tests()
    yield
    cache_store._reset_for_tests()
