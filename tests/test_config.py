"""Отбор по «Нашей организации»: настройка, разбор и подстановка.

Зачем он: в кабинете может быть несколько организаций, а Saby без явного
отбора отдаёт события по всем сразу (справка changeslist говорит это прямым
текстом). Для курсора по событию это ещё и риск пропуска, который не видно
в результате.
"""
from __future__ import annotations

import json

import pytest

from saby_mcp_ro import config
from saby_mcp_ro.config import Settings, scope_filter, scoped_filter


@pytest.fixture
def store():
    """Настройка по умолчанию — там же, откуда её читает `scope_filter`.

    Фикстура `isolated_home` в conftest переносит `SABY_MCP_HOME` во временный
    каталог на время теста, поэтому путь здесь не задаём: иначе запись ушла бы
    в один файл, а чтение шло бы из другого.
    """
    return Settings()


LEGAL = {"СвЮЛ": {"ИНН": "1615013278", "КПП": "165501001"}}


# --- разбор значения ---------------------------------------------------------

def test_legal_company_accepted(store):
    assert store.set_our_company(LEGAL) == LEGAL
    assert store.our_company() == LEGAL


def test_company_without_kpp_is_rejected():
    """Юрлицо без КПП не опознать — это не отбор, а шум."""
    assert config._parse({"СвЮЛ": {"ИНН": "1615013278"}}) is None


def test_empty_and_garbage_are_rejected():
    assert config._parse({}) is None
    assert config._parse(None) is None
    assert config._parse("не json") is None
    assert config._parse({"СвЮЛ": {}}) is None
    assert config._parse({"СвЮЛ": {"КПП": "165501001"}}) is None


def test_string_form_is_accepted(store):
    store.set_our_company(json.dumps(LEGAL))
    assert store.our_company() == LEGAL


def test_only_documented_fields_survive(store):
    """Лишние поля в файл не тащим — справка их не перечисляет."""
    org = config._parse({"СвЮЛ": {"ИНН": "1", "КПП": "2", "Придумано": "x"}})
    assert set(org["СвЮЛ"]) == {"ИНН", "КПП"}


def test_individual_accepts_without_kpp():
    org = config._parse({"СвФЛ": {"ИНН": "123456789012"}})
    assert org["СвФЛ"]["ИНН"] == "123456789012"


def test_removing_the_setting(store):
    store.set_our_company(LEGAL)
    assert store.set_our_company(None) is None
    assert store.our_company() is None


def test_broken_file_does_not_raise(store):
    store.set_our_company(LEGAL)
    store.path.write_text("{не json", encoding="utf-8")
    assert store.our_company() is None


def test_environment_overrides_file(store, monkeypatch):
    store.set_our_company(LEGAL)
    other = {"СвЮЛ": {"ИНН": "1659193187", "КПП": "165501001"}}
    monkeypatch.setenv(config.ENV_KEY, json.dumps(other, ensure_ascii=False))
    assert store.our_company() == other
    assert store.describe()["env_override"] is True


# --- подстановка в фильтр ----------------------------------------------------

def test_scope_added_when_configured(store):
    store.set_our_company(LEGAL)
    out = scope_filter({"Фильтр": {"ТипРеестра": "Входящие"}}, True)
    assert out["Фильтр"]["НашаОрганизация"] == LEGAL
    assert out["Фильтр"]["ТипРеестра"] == "Входящие"


def test_caller_filter_is_never_overridden(store):
    store.set_our_company(LEGAL)
    mine = {"СвЮЛ": {"ИНН": "1659193187", "КПП": "165501001"}}
    given = {"Фильтр": {"ТипРеестра": "Входящие", "НашаОрганизация": mine}}
    assert scope_filter(given, True) == given


def test_nothing_added_without_config():
    given = {"Фильтр": {"ТипРеестра": "Входящие"}}
    assert scope_filter(given, True) == given


def test_not_added_where_command_does_not_accept_it(store):
    """У «СписокЗадач» такого поля нет — подставлять некуда."""
    store.set_our_company(LEGAL)
    given = {"Фильтр": {"ТипСписка": "НаМне"}}
    assert scope_filter(given, False) == given


def test_params_not_mutated_in_place(store):
    store.set_our_company(LEGAL)
    original = {"Фильтр": {"ТипРеестра": "Входящие"}}
    scope_filter(original, True)
    assert "НашаОрганизация" not in original["Фильтр"]


def test_command_without_root_stays_without_root(store):
    """«СписокАккаунтов» не примет ни «Фильтр», ни «Навигацию»."""
    store.set_our_company(LEGAL)
    assert scope_filter({}, True) == {}


def test_scoped_filter_returns_inner_dict(store):
    store.set_our_company(LEGAL)
    got = scoped_filter({"Фильтр": {"Блокировать": "Нет"}}, True)
    assert got["Блокировать"] == "Нет"
    assert got["НашаОрганизация"] == LEGAL


def test_scoped_filter_without_config_keeps_input():
    got = scoped_filter({"Фильтр": {"Блокировать": "Нет"}}, True)
    assert got == {"Блокировать": "Нет"}