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

async def test_regression_navigation_never_goes_to_the_root(recorder, client):
    """Навигация должна быть ВНУТРИ корневого объекта, а не рядом с ним.

    Именно из-за этой ошибки «Навигация не работает» и родился: в корне
    параметров Saby её молча игнорирует и отвечает пустым реестром, что
    выглядит как поломка сервера. Внутри «Фильтра» всё работает.
    """
    recorder.result(_page(_rows(10), Навигация={"ЕстьЕще": "Нет"}))
    await pagination.fetch_all(client, "spisok_dokumentov",
                               {"Фильтр": {"Тип": "x"}})
    assert "Навигация" not in recorder.last["json"]["params"]
    assert recorder.last["json"]["params"]["Фильтр"]["Навигация"]


@pytest.mark.parametrize("command_id", [
    "spisok_dokumentov", "spisok_zadach", "spisok_izmeneniy",
    "spisok_sluzhebnyh_etapov", "spisok_nashih_organizaciy",
])
async def test_every_paginated_command_gets_a_page_size(recorder, client, command_id):
    """Все команды с корневым объектом получают размер страницы — внутри него.

    Имя теста раньше было обратным («навигации нет ни у кого») и держало
    неверное утверждение. Размер страницы работает у всех; различается
    только способ листать, см. `pagination_mode`.
    """
    recorder.result(_page(_rows(5), Навигация={"ЕстьЕще": "Нет"}))
    cmd = catalog.get(command_id)
    assert cmd is not None
    await pagination.fetch_all(client, command_id, {})
    nav = recorder.last["json"]["params"][cmd.params_root]["Навигация"]
    assert nav["РазмерСтраницы"] == pagination.PAGE_SIZE, command_id


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
    assert sent["Навигация"]["РазмерСтраницы"] == pagination.PAGE_SIZE


async def test_page_size_is_capped_at_saby_limit(recorder, client):
    """Saby молча срезает всё, что выше 200, — лучше не просить заведомо."""
    recorder.result(_page(_rows(5)))
    await pagination.fetch_all(client, "spisok_dokumentov", {}, page_size=5000)
    sent = _bodies(recorder)[0]["Фильтр"]["Навигация"]
    assert sent["РазмерСтраницы"] == "200"


async def test_command_without_root_stays_empty(recorder, client):
    """СБИС.СписокАккаунтов принимает пустые params — проверено живьём."""
    recorder.result({"Список": [{"НазваниеАккаунта": "ООО Ромашка"}]})
    await pagination.fetch_all(client, "spisok_akkauntov")
    assert _bodies(recorder)[0] == {}


# --- обход страниц ----------------------------------------------------------

def _paged(recorder, pages):
    """Заложить ответы по одному на вызов — в том порядке, в каком придут."""
    for payload in pages:
        recorder.result(payload)


async def test_pages_are_walked_with_page_number(recorder, client):
    """`page`: страницы идут 0, 1, 2 … пока сервер говорит «ЕстьЕще = Да»."""
    _paged(recorder, [
        _page(_rows(3, 0), Навигация={"ЕстьЕще": "Да", "Страница": "0"}),
        _page(_rows(3, 3), Навигация={"ЕстьЕще": "Да", "Страница": "1"}),
        _page(_rows(2, 6), Навигация={"ЕстьЕще": "Нет", "Страница": "2"}),
    ])
    out = await pagination.fetch_all(client, "spisok_dokumentov_po_sobytiyam",
                                     {"Фильтр": {"ТипРеестра": "Входящие"}})

    assert out["total_fetched"] == 8
    assert out["pages_fetched"] == 3
    assert out["truncated"] is False
    assert "notes" not in out

    sent = [p["Фильтр"]["Навигация"]["Страница"] for p in _bodies(recorder)]
    assert sent == ["0", "1", "2"]


async def test_page_size_survives_the_cursor_merge(recorder, client):
    """Курсор не должен стирать запрошенный размер страницы."""
    _paged(recorder, [
        _page(_rows(2, 0), Навигация={"ЕстьЕще": "Да", "Страница": "0"}),
        _page(_rows(1, 2), Навигация={"ЕстьЕще": "Нет", "Страница": "1"}),
    ])
    await pagination.fetch_all(client, "spisok_dokumentov",
                               {"Фильтр": {"Тип": "СчетИсх"}}, page_size=50)
    for body in _bodies(recorder):
        nav = body["Фильтр"]["Навигация"]
        assert nav["РазмерСтраницы"] == "50"
    assert _bodies(recorder)[0]["Фильтр"]["Тип"] == "СчетИсх"


def _change_rows(count, start, when="15.10.2026 10.00.00"):
    """Документы с событиями — для курсора по событию."""
    return [{
        "Идентификатор": f"doc-{start + i}",
        "Событие": [{"Идентификатор": f"evt-{start + i}",
                     "ДатаВремя": f"{when}#{start + i}"}],
    } for i in range(count)]


async def test_event_cursor_advances_by_event_id(recorder, client):
    """`event_cursor`: в следующий запрос идёт Событие.Идентификатор, не документ."""
    _paged(recorder, [
        _page(_change_rows(2, 0), Навигация={"ЕстьЕще": "Да"}),
        _page(_change_rows(2, 2), Навигация={"ЕстьЕще": "Нет"}),
    ])
    out = await pagination.fetch_all(client, "spisok_izmeneniy",
                                     {"Фильтр": {"ДатаВремяС": "15.10.2026 10.00.00"}})

    assert out["total_fetched"] == 4
    assert out["pages_fetched"] == 2
    assert out["truncated"] is False

    second = _bodies(recorder)[1]["Фильтр"]
    assert second["ИдентификаторСобытия"].startswith("evt-")
    assert "ДатаВремяС" in second
    # документ в курсор попасть не должен — Saby на такой идентификатор
    # отвечает иначе, и на этом когда-то и вырос ложный вывод
    assert second["ИдентификаторСобытия"] not in ("doc-0", "doc-1")


async def test_event_cursor_picks_latest_event_not_last_in_list(recorder, client):
    """Порядок событий в выдаче не гарантирован — берём самый свежий."""
    _paged(recorder, [
        _page([{"Идентификатор": "d1", "Событие": [
            {"Идентификатор": "evt-new", "ДатаВремя": "15.10.2026 12.00.00"},
            {"Идентификатор": "evt-old", "ДатаВремя": "15.10.2026 09.00.00"},
        ]}], Навигация={"ЕстьЕще": "Да"}),
        _page(_rows(0), Навигация={"ЕстьЕще": "Нет"}),
    ])
    await pagination.fetch_all(client, "spisok_izmeneniy",
                               {"Фильтр": {"ДатаВремяС": "15.10.2026 00.00.00"}})
    second = _bodies(recorder)[1]["Фильтр"]
    assert second["ИдентификаторСобытия"] == "evt-new"
    assert second["ДатаВремяС"] == "15.10.2026 12.00.00"


async def test_identical_rows_are_not_counted_twice(recorder, client):
    """Сервер может вернуть одни и те же строки — они не должны задваиваться."""
    same = _rows(4, 0)
    _paged(recorder, [
        _page(same, Навигация={"ЕстьЕще": "Да", "Страница": "0"}),
        _page(same, Навигация={"ЕстьЕще": "Нет", "Страница": "1"}),
    ])
    out = await pagination.fetch_all(client, "spisok_dokumentov",
                                     {"Фильтр": {"Тип": "x"}})
    assert out["total_fetched"] == 4


async def test_same_document_with_new_event_is_not_dropped(recorder, client):
    """Тот же документ с ДРУГИМ событием — это новая строка, а не дубль.

    Регрессия с живых данных 16.10.2026: дедупликация по одному
    `Идентификатор` документа выкидывала события, и за двое суток приходило
    74 строки вместо 200+.
    """
    _paged(recorder, [
        _page(_change_rows(1, 0), Навигация={"ЕстьЕще": "Да"}),
        _page([{"Идентификатор": "doc-0",
                "Событие": [{"Идентификатор": "evt-9",
                             "ДатаВремя": "15.10.2026 11.00.00"}]}],
              Навигация={"ЕстьЕще": "Нет"}),
    ])
    out = await pagination.fetch_all(client, "spisok_izmeneniy",
                                     {"Фильтр": {"ДатаВремяС": "15.10.2026 10.00.00"}})
    assert out["total_fetched"] == 2


async def test_unchanging_cursor_stops_the_walk(recorder, client):
    """Сервер, вечно отвечающий «Да» одной и той же страницей, не зациклит обход."""
    stuck = _page(_rows(3, 0), Навигация={"ЕстьЕще": "Да", "Страница": "0"})
    _paged(recorder, [stuck, stuck, stuck, stuck])
    out = await pagination.fetch_all(client, "spisok_dokumentov",
                                     {"Фильтр": {"Тип": "x"}})
    assert out["pages_fetched"] < pagination.MAX_PAGES
    assert out["truncated"] is True
    assert out["notes"]


async def test_command_without_pagination_says_it_is_a_single_page(recorder, client):
    """У `СписокЗадач` поля Страница нет — обход обрывается и говорит об этом."""
    _paged(recorder, [
        _page(_rows(25, 0), Навигация={"ЕстьЕще": "Да", "РазмерСтраницы": "25"}),
    ])
    out = await pagination.fetch_all(client, "spisok_zadach",
                                     {"Фильтр": {"ТипСписка": "НаМне"}})
    assert out["pages_fetched"] == 1
    assert out["total_fetched"] == 25
    assert out["truncated"] is True
    assert "Страница" in out["notes"][0]


async def test_max_items_cuts_and_says_so(recorder, client):
    _paged(recorder, [
        _page(_rows(10, 0), Навигация={"ЕстьЕще": "Да", "Страница": "0"}),
    ])
    out = await pagination.fetch_all(client, "spisok_dokumentov",
                                     {"Фильтр": {"Тип": "x"}}, max_items=4)
    assert out["total_fetched"] == 4
    assert out["truncated"] is True
    assert any("лимита" in n for n in out["notes"])


async def test_has_more_without_rows_does_not_spin(recorder, client):
    """«ЕстьЕще = Да» при нуле строк — продолжать нечем, и крутиться не надо."""
    _paged(recorder, [
        _page(_rows(0), Навигация={"ЕстьЕще": "Да", "Страница": "0"}),
    ])
    out = await pagination.fetch_all(client, "spisok_dokumentov",
                                     {"Фильтр": {"Тип": "x"}})
    assert out["pages_fetched"] == 1
    assert out["truncated"] is False


async def test_walk_is_bounded_by_max_pages(recorder, client):
    """Даже с исправно работающим курсором обход обязан быть конечным."""
    _paged(recorder, [
        _page(_rows(2, i * 2), Навигация={"ЕстьЕще": "Да", "Страница": str(i)})
        for i in range(pagination.MAX_PAGES + 5)
    ])
    out = await pagination.fetch_all(client, "spisok_dokumentov",
                                     {"Фильтр": {"Тип": "x"}})
    assert out["pages_fetched"] == pagination.MAX_PAGES
    assert out["truncated"] is True
    assert any("страницах" in n for n in out["notes"])


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
