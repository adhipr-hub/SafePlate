"""Bulk import of the on-disk warm cache into Postgres (stubbed store)."""
import importlib.util
import json
from pathlib import Path

import pytest

from safeplate import cache_store


def _load_script():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "migrate_cache_to_db", root / "scripts" / "migrate_cache_to_db.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrates_all_namespaces_and_skips_junk(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SAFEPLATE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")

    (tmp_path / "extraction2_result").mkdir(parents=True)
    (tmp_path / "extraction2_result" / "aaa.json").write_text(
        json.dumps({"at": 1.0, "items": []}), encoding="utf-8"
    )
    (tmp_path / "diet_llm").mkdir(parents=True)
    (tmp_path / "diet_llm" / "bbb.json").write_text(
        json.dumps({"at": 2.0, "parsed": []}), encoding="utf-8"
    )
    (tmp_path / "diet_llm" / "junk.json").write_text("{broken", encoding="utf-8")
    # out-of-scope namespace must NOT be imported
    (tmp_path / "http").mkdir(parents=True)
    (tmp_path / "http" / "page.json").write_text(json.dumps({"body": "x"}), encoding="utf-8")

    saved = []
    monkeypatch.setattr(cache_store, "save", lambda ns, key, blob: saved.append((ns, key, blob)))
    monkeypatch.setattr(cache_store, "_get_pool", lambda: object())  # "connected"

    script = _load_script()
    assert script.main() == 0
    assert ("extraction2_result", "aaa", {"at": 1.0, "items": []}) in saved
    assert ("diet_llm", "bbb", {"at": 2.0, "parsed": []}) in saved
    assert not any(ns == "http" for ns, _, _ in saved)
    assert len(saved) == 2
    out = capsys.readouterr().out
    assert "imported 2 entries" in out and "1 unreadable" in out


def test_exits_1_without_database_url(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SAFEPLATE_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    script = _load_script()
    assert script.main() == 1
