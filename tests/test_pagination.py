"""Пагинация по тому, что Saby делает на самом деле.

Главное, что здесь проверяется, — не «сколько страниц пройдено», а честность:
сервер говорит «ЕстьЕще = Да», обход не может продолжить, и он ОБЯЗАН об этом
сказать. Молча вернуть усечённый список значило бы солгать.
"""
from __future__ import annotations

import pytest

from saby_mcp_ro import catalog, pagination
from saby_mcp_ro.client import SabyClient, SabyError


def _page(rows, key="Документ", **extra):
    return {key: rows, **extra}


def _rows(count, start=0):
    return [{"Идентификатор": str(start + i)} for i in range(count)]


def _bodies(recorder):
    return [c["json"]["params"] for c in recorder.calls]


# --- regression: Навигация в запросе ----------------------------------------

async def test_regression_never_sends_navigation_object(recorder, client):
    """Saby не принимает объект Навигация в запросе.

    Проверено живьём: и с РазмерСтраницы, и с Позиция.ДатаВремя, и даже пустой
    `{}` — ответ -32601 «метод не найден». Ключевое слово здесь «метод»:
    команда существует и называется верно, не разобрались именно params.
    """
    recorder.result(_page(_rows(10), Навигация={"ЕстьЕще": "Нет"}))
    await pagination.fetch_all(client, "spisok_dokumentov",
                               {"Фильтр": {"Тип": "x"}})
    assert "Навигация" not in recorder.last["json"]["params"]


@pytest.mark.parametrize("command_id", [
    "spisok_dokumentov", "spisok_zadach", "spisok_izmeneniy",
    "spisok_sluzhebnyh_etapov", "spisok_nashih_organizaciy",
])
async def test_no_navigation_for_any_paginated_command(recorder, client, command_id):
    recorder.result(_page(_rows(5)))
    cmd = catalog.get(command_id)
    await pagination.fetch_all(client, command_id, {})
    assert "Навигация" not in recorder.last["json"]["params"], command_id
    assert cmd is not None


# --- подстановка корневого объекта ------------------------------------------

async def test_root_object_is_injected(recorder, client):
    """Без «Фильтр» Saby отвечает «метод не найден» — проверено живьём."""
    recorder.result(_page(_rows(5)))
    await pagination.fetch_all(client, "spisok_dokumentov")
    assert "Фильтр" in _bodies(recorder)[0]


async def test_root_object_not_overwritten(recorder, client):
    """Фильтр вызывающего не теряется, хотя страница в него добавляется.

    Раньше проверка была строгой (`== {"Тип": "Счет"}`), но с появлением
    `Навигация.РазмерСтраницы` в фильтр добавляется ещё и размер страницы —
    суть проверки не в этом, а в том, что ключи вызывающего целы.
    """
    recorder.result(_page(_rows(5)))
    await pagination.fetch_all(client, "spisok_dokumentov",
                               {"Фильтр": {"Тип": "Счет"}})
    sent = _bodies(recorder)[0]["Фильтр"]
    assert sent["Тип"] == "Счет"
    assert sent["Навигация"] == {"РазмерСтраницы": pagination.PAGE_SIZE}


async def test_page_size_is_capped_at_saby_limit(recorder, client):
    """Saby молча срезает всё, что выше 200, — лучше не просить заведомо."""
    recorder.result(_page(_rows(5)))
    await pagination.fetch_all(client, "spisok_dokumentov", {}, page_size=5000)
    sent = _bodies(recorder)[0]["Фильтр"]["Навигация"]
    assert sent == {"РазмерСтраницы": "200"}


async def test_command_without_root_stays_empty(recorder, client):
    """СБИС.СписокАккаунтов принимает пустые params — проверено живьём."""
    recorder.result({"Список": [{"НазваниеАккаунта": "ООО Ромашка"}]})
    await pagination.fetch_all(client, "spisok_akkauntov")
    assert _bodies(recorder)[0] == {}


# --- честность при усечении -------------------------------------------------

async def test_regression_truncation_is_reported(recorder, client):
    """Сервер said «ЕстьЕще» — обход обязан это сказать.

    Это главная проверка файла. Молча вернуть 25 строк из 300 значило бы
    сказать человеку «это все документы».
    """
    recorder.result(_page(_rows(25), Навигация={"ЕстьЕще": "Да",
                                                "РазмерСтраницы": "25"}))
    out = await pagination.fetch_all(client, "spisok_izmeneniy",
                                    {"Фильтр": {"ДатаВремяС": "01.01.2026 00:00:00"}})

    assert out["total_fetched"] == 25
    assert out["truncated"] is True
    assert out["notes"]
    assert "ЕстьЕще" in out["notes"][0]


async def test_complete_result_is_not_marked_truncated(recorder, client):
    recorder.result(_page(_rows(9), Навигация={"ЕстьЕще": "Нет",
                                               "РазмерСтраницы": "25"}))
    out = await pagination.fetch_all(client, "spisok_zadach",
                                    {"Фильтр": {"ТипСписка": "НаМне"}})
    assert out["truncated"] is False
    assert "notes" not in out


async def test_missing_navigation_is_not_claimed_as_truncated(recorder, client):
    """Нет навигации в ответе — неизвестно, всё ли отдали.

    Молчать тут можно: это не утверждение «всё», а отсутствие данных.
    Ложное утверждение о полноте — вот что нельзя.
    """
    recorder.result(_page(_rows(3)))
    out = await pagination.fetch_all(client, "spisok_dokumentov",
                                    {"Фильтр": {"Тип": "x"}})
    assert out["truncated"] is False
    assert out["server_navigation"] is None


async def test_max_items_caps_and_says_so(recorder, client):
    recorder.result(_page(_rows(100), Навигация={"ЕстьЕще": "Нет"}))
    out = await pagination.fetch_all(client, "spisok_izmeneniy",
                                    {"Фильтр": {"ДатаВремяС": "x"}},
                                    max_items=40)
    assert len(out["items"]) == 40
    assert out["truncated"] is True
    assert any("40" in n for n in out["notes"])


@pytest.mark.parametrize("value,expected", [
    ("Да", True), ("да", True), ("Нет", False), ("нет", False),
    (True, True), (False, False), (None, False), ("", False),
])
async def test_has_more_understands_real_values(value, expected):
    assert pagination.has_more({"Навигация": {"ЕстьЕще": value}}) is expected


async def test_exactly_one_request_is_made(recorder, client):
    """Обход не имеет права ждать продолжения, которого не будет."""
    for _ in range(4):
        recorder.result(_page(_rows(25), Навигация={"ЕстьЕще": "Да"}))
    await pagination.fetch_all(client, "spisok_izmeneniy",
                               {"Фильтр": {"ДатаВремяС": "x"}})
    assert len(recorder.calls) == 1


async def test_unknown_command_refused(recorder, client):
    with pytest.raises(SabyError):
        await pagination.fetch_all(client, "net_takoy")


# --- разбор result -----------------------------------------------------------

def test_extract_rows_from_key():
    assert pagination.extract_rows({"Документ": [{"a": 1}]}, "Документ") == [{"a": 1}]


def test_extract_rows_bare_array():
    """sabyDepartment.Read отдаёт массивом без обёртки — проверено живьём."""
    assert pagination.extract_rows([{"a": 1}], "") == [{"a": 1}]


def test_extract_rows_missing_key_returns_empty():
    assert pagination.extract_rows({"Прочее": []}, "Документ") == []


def test_extract_rows_falls_back_to_first_list():
    assert pagination.extract_rows({"Документы": [{"a": 1}]}, "Документ") == [{"a": 1}]


def test_dig():
    assert pagination.dig({"a": {"b": 1}}, "a.c") is None
    assert pagination.dig({"a": 1}, "a.b.c") is None
    assert pagination.dig({"a": {"b": 1}}, "a.b") == 1


def test_real_shape_of_list_invitations():
    """Живой ответ: выдача под «СписокПриглашений», а не «Приглашение»."""
    cmd = catalog.get("spisok_izmeneniy_priglaseniy")
    real = {"СписокПриглашений": [{"ДатаВремяСоздания": "01.10.2026 12:00:00"}],
            "Навигация": {"РазмерСтраницы": "20"}}
    rows = pagination.extract_rows(real, cmd.result_key)
    assert len(rows) == 1
