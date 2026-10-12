"""Справочник типов документов.

Ожидания взяты из ЖИВОГО прогона боевой сессии, а не из справочки: именно
поэтому здесь есть список принятых и отклонённых.
"""
from __future__ import annotations

import pytest

from saby_mcp_ro import doctypes

# Прогнано настоящими вызовами на боевой сессии (октябрь 2026).
ACCEPTED_LIVE = {
    "СчетИсх", "СчетВх", "ФактураИсх", "ФактураВх", "ДокОтгрИсх", "ДокОтгрВх",
    "КоррИсх", "КоррВх", "CorrIn", "CorrOut", "SimpleDoc", "ConsignmentNote",
    "Waybill", "ReturnIn", "ReturnOut", "DeviationActIn", "DeviationActOut",
    "PriceMatchingIn", "PriceMatchingOut", "АктВыпуска", "АктИнв",
    "АктСписания", "ВнутрПрм", "КассовыйДень", "Пересорт", "ПриходныйОрдер",
    "РасходныйОрдер", "Приказ", "ПриказНаУвольнение", "ТабельДокумент",
    "BusinessTrip", "Отпуск",
}
REJECTED_LIVE = {"Док", "АктСверки", "Заказ", "Договор", "Корреспонденция",
                 "Счет", "Накладная"}


# --- соответствие живому прогону -------------------------------------------

@pytest.mark.parametrize("code", sorted(ACCEPTED_LIVE))
def test_accepted_live_are_marked_verified(code):
    assert code in doctypes.BY_CODE, f"{code}: принят Saby, но его нет в справочнике"
    assert doctypes.BY_CODE[code].verified is True, \
        f"{code}: Saby его принял, а мы помечаем непроверенным"


@pytest.mark.parametrize("code", sorted(REJECTED_LIVE - {"Счет", "Накладная",
                                                         "Договор",
                                                         "Корреспонденция"}))
def test_rejected_live_are_not_claimed_verified(code):
    """Эти есть в справочнике, но наш кабинет их не принял."""
    if code in doctypes.BY_CODE:
        assert doctypes.BY_CODE[code].verified is False, \
            f"{code}: Saby отверг, а мы считаем проверенным"


@pytest.mark.parametrize("name", ["Счет", "Накладная", "Фактура", "Договор",
                                  "Корреспонденция"])
def test_russian_names_are_not_in_reference(name):
    """Русские названия Saby не принимает — их не должно быть и у нас,
    иначе агент подставит их по привычке и упрётся в отказ."""
    assert name not in doctypes.BY_CODE


# -- целостность --------------------------------------------------------------

def test_codes_are_unique():
    codes = [t.code for t in doctypes.ALL_TYPES]
    assert len(codes) == len(set(codes)), "повторяющиеся коды типов"


def test_every_type_has_title_and_directions():
    for t in doctypes.ALL_TYPES:
        assert t.title, t.code
        assert t.directions, f"{t.code}: нет направлений"
        for d in t.directions:
            assert d in ("Входящий", "Исходящий", "Внутренний"), \
                f"{t.code}: недопустимое направление {d!r}"


def test_no_invisible_characters():
    for t in doctypes.ALL_TYPES:
        for text in (t.code, t.title, " ".join(t.synonyms)):
            assert not any(ord(c) < 32 or 0xE000 <= ord(c) <= 0xF8FF for c in text)


def test_categories_cover_all_types():
    covered = [t for group in doctypes.CATEGORIES.values() for t in group]
    assert len(covered) == len(doctypes.ALL_TYPES)


# -- поиск по-человечески -----------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("счет", "СчетИсх"),
    ("счёт", "СчетИсх"),
    ("накладная", "ДокОтгрИсх"),
    ("фактура", "ФактураИсх"),
    ("приказ", "Приказ"),
    ("командировка", "BusinessTrip"),
    ("возврат", "ReturnIn"),
])
def test_russian_query_finds_latin_code(query, expected):
    """Главная задача: человек пишет по-русски, машина хочет латиницу."""
    found = doctypes.search(query, limit=5)
    codes = [t.code for t in found]
    assert expected in codes, f"{query!r} -> {codes}"


def test_search_by_exact_code():
    assert doctypes.search("СчетИсх", limit=3)[0].code == "СчетИсх"


def test_search_does_not_match_inside_words():
    """regression: «счет» не должен находиться внутри «пересчет».

    При сравнении по подстроке инвентаризация (синоним «пересчёт») вставала
    первым номером в выдаче по запросу «счёт» — человек получал не тот тип.
    """
    codes = [t.code for t in doctypes.search("счет", limit=10)]
    assert codes[:2] == ["СчетВх", "СчетИсх"], codes
    assert "АктИнв" not in codes[:2]


def test_its_own_synonym_still_finds_its_type():
    """При этом «пересчет» по-прежнему находит инвентаризацию."""
    codes = [t.code for t in doctypes.search("пересчет", limit=5)]
    assert "АктИнв" in codes


def test_search_verified_come_first():
    """От проверенных типов больше пользы — они первыми."""
    for query in ("счет", "приказ", "накладная", ""):
        hits = doctypes.search(query, limit=10)
        flags = [t.verified for t in hits]
        if any(flags):
            assert flags == sorted(flags, reverse=True), query


def test_empty_query_returns_verified():
    assert doctypes.search("") and all(t.verified for t in doctypes.search(""))


def test_describe_and_is_known():
    assert doctypes.is_known("СчетИсх")
    assert not doctypes.is_known("Счёт")
    assert doctypes.describe("СчетВх").directions == ("Входящий",)


# -- подсказки при отказе -----------------------------------------------------

@pytest.mark.parametrize("wrong,expected_among", [
    ("Счет", {"СчетИсх", "СчетВх"}),
    ("Накладная", {"ДокОтгрИсх", "ДокОтгрВх"}),
    ("Фактура", {"ФактураИсх", "ФактураВх"}),
    ("счёт", {"СчетИсх", "СчетВх"}),
])
def test_suggest_replaces_a_russian_name(wrong, expected_among):
    codes = {t.code for t in doctypes.suggest(wrong)}
    assert codes & expected_among, f"{wrong!r} -> {codes}"


def test_suggest_is_empty_for_nonsense():
    assert doctypes.suggest("") == []
    assert doctypes.suggest("ыыыыы") == []


def test_suggest_marks_unverified():
    found = doctypes.suggest("Договор")
    assert found
    for t in found:
        assert isinstance(t.verified, bool)
