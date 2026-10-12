"""Поиск по словам.

Тесты, помеченные regression, закрывают ошибки ранжирования изученного сервера:
стоп-форма глагола давала разные основы, а латинский operation_id не сходился
с русским запросом никогда.
"""
from __future__ import annotations

import pytest

from saby_mcp_ro import catalog, search


def _top(query: str) -> str | None:
    found = search.search(query, limit=1)
    return found[0].command if found else None


# --- стеммер ----------------------------------------------------------------

@pytest.mark.parametrize("word,expected_root", [
    ("подписать", "подпис"),
    ("подпись", "подпис"),
    ("подписывать", "подпис"),
])
def test_regression_verb_forms_share_a_stem(word, expected_root):
    """regression: формы одного глагола должны сходиться к одной основе.

    Наивный стеммер давал «подписывать» → «подписыв» и «подписать» →
    «подпис»: формы одного глагола не находили друг друга.
    """
    assert search.stem(word) == expected_root


def test_imperative_form_is_covered_by_synonyms():
    """«подпиши» стеммер к «подпис» не сводит — morphological это разные формы.

    Сводит словарь синонимов: именно поэтому он и нужен, и именно поэтому
    поиск по «подпиши» что-то находит.
    """
    assert search.stem("подпиши") != search.stem("подписать")
    groups = search._concepts(["подпиши"])
    assert search.stem("подписать") in groups[0]


@pytest.mark.parametrize("a,b", [
    ("документы", "документ"),
    ("документах", "документ"),
    ("задачи", "задача"),
    ("изменения", "изменение"),
    ("контрагенты", "контрагент"),
    ("подразделения", "подразделение"),
    ("организации", "организация"),
    ("сертификаты", "сертификат"),
])
def test_ru_declensions_share_a_stem(a, b):
    assert search.stem(a) == search.stem(b)


def test_stem_keeps_short_words():
    assert search.stem("счет") == "счет"
    assert search.stem("акт") == "акт"
    assert search.stem("инн") == "инн"


def test_stem_normalizes_yo():
    assert search.stem("ёж") == search.stem("еж")


def test_english_plural():
    assert search.stem("certificates") == search.stem("certificate")


# --- живой поиск ------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("список документов", "СБИС.СписокДокументов"),
    ("прочитать документ", "СБИС.ПрочитатьДокумент"),
    ("входящие документы", "СБИС.СписокДокументовПоСобытиям"),
    ("задачи", "СБИС.СписокЗадач"),
    ("статус сертификатов", "СБИС.ПроверитьСертификаты"),
    ("список сертификатов", "СБИС.СписокСертификатов"),
    ("войти в систему", "СБИС.Аутентифицировать"),
    ("кто я", "СБИС.ИнформацияОТекущемПользователе"),
    ("контрагент по инн", "СБИС.ИнформацияОКонтрагенте"),
    ("наши организации", "СБИС.СписокНашихОрганизаций"),
    ("подразделения", "СБИС.СписокПодразделений"),
    ("статус мчд", "sabyMchd.GetStatus"),
    ("версия", "СБИС.ИнформацияОВерсии"),
    ("аккаунты", "СБИС.СписокАккаунтов"),
    ("изменения по документам", "СБИС.СписокИзменений"),
    ("служебные этапы", "СБИС.СписокСлужебныхЭтапов"),
    ("содержимое мчд", "sabyMchd.GetContent"),
    ("список наших организаций", "СБИС.СписокНашихОрганизаций"),
])
def test_live_queries_hit_the_right_command(query, expected):
    """Обещание, а не устройство ранжирования: человек спросил словами,
    сверху стоит та команда, которую он имел в виду."""
    assert _top(query) == expected, f"{query!r} -> {_top(query)!r}"


def test_write_requests_find_nothing_to_write():
    """На «удали» или «подпиши» не находится ничего, что можно было бы
    выполнить: команды записи в каталоге нет, и поиск её не выдумывает."""
    for query in ("удалить документ", "подписать документ", "создать документ"):
        for cmd in search.search(query):
            assert cmd.command in catalog.BY_COMMAND


def test_search_never_returns_mutating_commands():
    """Любой запрос — хоть «удали всё» — возвращает только чтение."""
    for query in ("удали", "уничтожь", "подпиши", "запиши", "создай", "удали документ"):
        for cmd in search.search(query, limit=20):
            assert cmd.command.startswith(
                ("СБИС.Список", "СБИС.Прочитать", "СБИС.Информация",
                 "СБИС.Проверить", "СБИС.Аутентифицировать", "sabyMchd.Get",
                 "sabySignature.Check", "sabyDepartment.Read",
                 "sabyOurCompany.List", "СБИС.Сформировать")), cmd.command


def test_ambiguous_query_returns_both_relevant_commands():
    """«приглашения» подходит и прочитать, и за изменения — обе в выдаче."""
    found = {c.command for c in search.search("приглашения", limit=10)}
    assert "СБИС.ПрочитатьПриглашение" in found
    assert "СБИС.СписокИзмененийПриглашений" in found


def test_unknown_query_suggests_similar_words():
    near = search.suggest("документ")
    assert isinstance(near, list)


def test_empty_query_returns_nothing():
    assert search.search("") == []
    assert search.search("   ") == []


def test_stopwords_are_dropped():
    """Служебные слова не должны уводить выдачу.

    regression: предлог «в» из «войти в систему» дословно совпадал со
    словом «в» в описании другой команды и уводил верх с команды входа.
    """
    assert "в" not in search.tokens("войти в систему")
    assert search.tokens("войти в систему", drop_stopwords=False)[1] == "в"
    assert search.search("войти в систему")[0].command == "СБИС.Аутентифицировать"


def test_limit_is_respected():
    assert len(search.search("документы", limit=3)) <= 3


def test_every_command_is_reachable_by_its_own_name():
    """Каждая команда находится поиском по собственному описанию — иначе она
    была бы в каталоге мёртвым грузом."""
    for cmd in catalog.ALL_COMMANDS:
        assert search.search(cmd.summary, limit=20), f"{cmd.id}: не находится"


def test_latin_operation_id_does_not_break_search():
    """regression: operation_id латинский и в индекс не входит.

    Раньше он был единственным, что индексировалось вместе с описанием, и
    русский запрос по нему не проходил.
    """
    found = search.search("подписать вложение", limit=5)
    assert found, "пустой результат на русском запросе"


def test_synonyms_are_one_concept():
    """«статус» и «состояние» — одно слово человека, а не два."""
    assert search.stem("статус") != search.stem("состояние")
    by_status = {c.command for c in search.search("статус сертификатов", limit=20)}
    by_state = {c.command for c in search.search("состояние сертификатов", limit=20)}
    assert "СБИС.ПроверитьСертификаты" in by_status
    assert "СБИС.ПроверитьСертификаты" in by_state


def test_coverage_beats_frequency():
    """Слово, покрывающее большую часть запроса, должно стоять выше слова,
    которое встречается часто, но закрывает одну часть."""
    found = search.search("список сертификатов", limit=20)
    assert found[0].command == "СБИС.СписокСертификатов"
