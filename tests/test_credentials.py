"""Хранилище сессии: секрет лежит в файле с правами 600 и переживает падение."""
from __future__ import annotations

import json
import os
import stat

import pytest

from saby_mcp_ro.credentials import CredentialStore


def test_save_and_load(tmp_path):
    store = CredentialStore(tmp_path / "session.json")
    store.save_session("SECRET-SESSION")
    assert store.load_session() == "SECRET-SESSION"


def test_file_is_private(tmp_path):
    store = CredentialStore(tmp_path / "session.json")
    store.save_session("SECRET")
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    # На Windows chmod ограничен, поэтому проверяем только там, где есть POSIX.
    if os.name != "nt":
        assert mode == 0o600, oct(mode)


def test_missing_file_returns_none(tmp_path):
    assert CredentialStore(tmp_path / "nope.json").load_session() is None


def test_corrupt_file_is_survivable(tmp_path):
    """Битый файл не должен ронять сервер при старте."""
    path = tmp_path / "session.json"
    path.write_text("{не json", encoding="utf-8")
    assert CredentialStore(path).load_session() is None


def test_empty_file_returns_none(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("   ", encoding="utf-8")
    assert CredentialStore(path).load_session() is None


def test_env_is_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("SBIS_SESSION_ID", "FROM-ENV")
    store = CredentialStore(tmp_path / "session.json")
    assert store.load_session() == "FROM-ENV"


def test_file_wins_over_env(tmp_path, monkeypatch):
    """Файл свежее: его пишет saby_login, переменную человек задал вчера."""
    monkeypatch.setenv("SBIS_SESSION_ID", "FROM-ENV")
    store = CredentialStore(tmp_path / "session.json")
    store.save_session("FROM-FILE")
    assert store.load_session() == "FROM-FILE"


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    store = CredentialStore(tmp_path / "session.json")
    store.save_session("A")
    store.save_session("B")
    store.save_session("C")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert store.load_session() == "C"


def test_password_is_never_stored(tmp_path):
    store = CredentialStore(tmp_path / "session.json")
    store.save_session("SECRET", login="user@example.ru")
    raw = store.path.read_text(encoding="utf-8")
    assert "SECRET" in raw
    assert "user@example.ru" in raw
    assert "password" not in raw.lower()


def test_clear_session(tmp_path):
    store = CredentialStore(tmp_path / "session.json")
    store.save_session("SECRET")
    assert store.clear_session() is True
    assert store.load_session() is None
    assert store.clear_session() is False


def test_describe_never_leaks_secret(tmp_path):
    store = CredentialStore(tmp_path / "session.json")
    store.save_session("VERY-SECRET", login="me")
    info = store.describe()
    assert info["session_present"] is True
    assert info["source"] == "file"
    assert "VERY-SECRET" not in json.dumps(info, ensure_ascii=False)


def test_save_rejects_whitespace_session(tmp_path):
    store = CredentialStore(tmp_path / "session.json")
    store.save_session("   ")
    assert store.load_session() is None
