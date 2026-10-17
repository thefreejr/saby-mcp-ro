"""Сводка входящих: проверки на то, что сломано у Saby.

Каждый тест здесь — реальная особенность боевого контура, а не абстрактное
требование. Проверено живыми вызовами (октябрь 2026, рабочая сессия):

  • порция в 25 строк не обходится ни одним из пяти способов пагинации;
  • `ДатаС`/`ДатаПо` фильтруют по времени события, а не по дате документа;
  • 25 событий весят 209 КБ, и восемь девятых — это ссылки на PDF в base64;
  • «На мне» отдаёт 9 строк на 8 документов;
  • `Фильтр.Сотрудник` в задачах игнорируется — возвращает чужие задачи;
  • `Срок` пуст у 24 событий из 25, просрочку по нему считать нельзя;
  • `ТипВложения` в реестре отдаёт пустоту вместо фильтра;
  • `СписокИзменений` присылает один документ дважды.

Если чей-то из них «починят» в Saby или сломают у нас — тест это покажет.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from saby_mcp_ro import digest
from saby_mcp_ro.client import SabyClient  # noqa: F401 — фикстуры клиента
from saby_mcp_ro.credentials import CredentialStore
from saby_mcp_ro.digest_state import DigestState
from saby_mcp_ro.server import build  # noqa: F401 — сервер поднимается в тестах

NOW = datetime(2026, 10, 12, 18, 0, 0)


# --- подставные ответы Saby -------------------------------------------------

PDF_LINK = "https://online.sbis.ru/pdfservicepublic/service/?method=PDFService" + "x" * 4000


def _event(number="191", doc_date="20.07.2026", when="12.10.2026 17.39.53",
           state="Ожидает аннулирования", doc_type="ДоговорДок",
           counterparty="Комитет Земельных и Ммущественных Отношений",
           ident="019f7ec8-f092-7d9a-afb8-a868f4d39ac7"):
    return {
        "ДатаВремя": when,
        "Состояние": {"Код": "27", "Название": state, "Примечание": "уточнение"},
        "Документ": {
            "Идентификатор": ident,
            "Номер": number,
            "Название": f"Договор № {number} от {doc_date}",
            "Дата": doc_date,
            "Тип": doc_type,
            "Направление": "Входящий",
            "Сумма": "",
            "Срок": "",
            "Удален": "Нет",
            "Регламент": {"Название": "Входящий договор"},
            "Состояние": {"Код": "7", "Название": "Выполнение завершено успешно"},
            "Контрагент": {"СвЮЛ": {"Название": counterparty, "ИНН": "1658008723"}},
            "НашаОрганизация": {"СвЮЛ": {"Название": "ООО Ромашка"}},
            "Ответственный": {"Фамилия": "Урлин", "Имя": "Андрей",
                              "Отчество": "Олегович"},
            "Подразделение": {"Название": "Бухгалтерия"},
            "СсылкаНаPDF": PDF_LINK,
            "СсылкаНаАрхив": PDF_LINK,
            "СсылкаДляНашаОрганизация": "https://online.sbis.ru/opendoc.html?guid=x",
            "Вложение": [{
                "Название": "Договор.docx", "Тип": doc_type, "Дата": doc_date,
                "Редакция": {"Номер": "1"}, "Зашифрован": "Нет",
                "Сумма": "10000,00", "СуммаБезНДС": "8333,33",
                "СсылкаВКабинет": "https://online.sbis.ru/opendoc.html?guid=x",
                "СсылкаНаPDF": PDF_LINK, "Файл": PDF_LINK, "Упакован": "",
            }],
        },
    }


def _task(ident="becfafc6", title="Договор № 776 от 10.10.2026",
          state="В обработке", code="10", created="10.10.2026 11.50.58",
          person="Урлин", due=""):
    return {
        "Идентификатор": ident,
        "Название": title,
        "Тип": "ДоговорДок",
        "Дата": "10.10.2026",
        "ДатаВремяСоздания": created,
        "Направление": "Входящий",
        "Состояние": {"Код": code, "Название": state},
        "Срок": due,
        "Ответственный": {"Фамилия": person, "Имя": "А", "Отчество": "О"},
        "Регламент": {"Название": "Входящий договор"},
        "Контрагент": {"СвЮЛ": {"Название": "Казаньоргсинтез, ПАО"}},
        "СсылкаНаPDF": PDF_LINK,
    }


def _change(ident="becfafc6", title="Договор № 776 от 10.10.2026",
            when="12.10.2026 08.11.36", action="Переназначить",
            event_id="10a3a7d2", executor="Сиразиева"):
    return {
        "Идентификатор": ident,
        "Название": title,
        "Тип": "ДоговорДок",
        "Дата": "10.10.2026",
        "ДатаВремяСоздания": "10.10.2026 11.50.58",
        "Направление": "Входящий",
        "Состояние": {"Код": "10", "Название": "В обработке"},
        "Контрагент": {"СвЮЛ": {"Название": "Казаньоргсинтез, ПАО"}},
        "Событие": [{
            "Идентификатор": event_id,
            "ДатаВремя": when,
            "Название": "Утверждение",
            "Действие": {"Название": action},
            "Исполнитель": {"Фамилия": executor, "Имя": "Алсу",
                            "Отчество": "Хасиятулловна"},
            "Подразделение": {"Название": "Бухгатерия"},
            "Комментарий": "",
        }],
    }


def _nav(more="Нет"):
    return {"ЕстьЕще": more, "РазмерСтраницы": "25"}


class _Fake:
    """Клиент, отвечающий по командам, а не по порядку вызовов.

    Порядок вызовов сводки — деталь реализации, а тесты должны были бы падать
    при его изменении вплоть до перестановки двух независимых строк.
    """

    def __init__(self, **pages):
        self.pages = pages
        self.session_id = "TEST-SESSION"
        self.seen: list[tuple[str, dict]] = []

    async def call_command(self, command_id, params=None):
        params = params or {}
        self.seen.append((command_id, params))
        page = self.pages.get(command_id)
        if callable(page):
            return page(params)
        return page if page is not None else {}

    def params_of(self, command_id):
        return [p for c, p in self.seen if c == command_id]


# --- проекция ---------------------------------------------------------------

def test_long_links_are_dropped():
    """209 КБ на 25 событий — это в основном ссылки на PDF в base64."""
    doc = digest.compact_doc(_event()["Документ"])
    blob = json.dumps(doc, ensure_ascii=False)
    assert "СсылкаНаPDF" not in blob
    assert PDF_LINK not in blob
    assert "ссылка" not in doc  # по умолчанию ссылок нет вовсе


def test_link_only_when_asked():
    doc = digest.compact_doc(_event()["Документ"], with_link=True)
    assert doc["ссылка"].startswith("https://online.sbis.ru/opendoc.html")


def test_projection_keeps_what_a_person_reads():
    doc = digest.compact_doc(_event()["Документ"])
    assert doc["номер"] == "191"
    assert doc["контрагент"]["название"].startswith("Комитет")
    assert doc["состояние"] == "Выполнение завершено успешно"
    assert doc["сумма"] == "10000,00"
    assert doc["сумма_без_ндс"] == "8333,33"
    assert doc["ответственный"] == "Урлин Андрей Олегович"


def test_event_state_is_not_confused_with_document_state():
    """«Ожидает аннулирования» — это про событие, а не про документ."""
    event = digest.compact_event(_event())
    assert event["состояние_события"] == "Ожидает аннулирования"
    assert event["состояние"] == "Выполнение завершено успешно"
    assert event["требует_внимания"] is True


def test_attention_follows_confirmed_codes_and_words():
    assert digest.attention({"код": "10", "название": "В обработке"}) is True
    assert digest.attention({"код": "27", "название": "Ожидает аннулирования"}) is True
    assert digest.attention({"код": "7", "название": "Выполнение завершено успешно"}) is False
    assert digest.attention({"код": "", "название": "Выполнение завершено с проблемами"}) is True


# --- окно --------------------------------------------------------------------

def test_window_defaults_to_yesterday():
    start, end, source = digest.resolve_window(1, None, None, None, now=NOW)
    assert source == "дни"
    assert start == datetime(2026, 10, 11, 0, 0, 0)
    assert end == NOW


def test_window_from_cursor_wins_over_days():
    start, end, source = digest.resolve_window(
        7, None, None, "10.10.2026 18.00.00", now=NOW)
    assert source == "курсор"
    assert start == datetime(2026, 10, 10, 18, 0, 0)


def test_explicit_since_wins_over_cursor():
    start, _, source = digest.resolve_window(
        7, "01.10.2026", None, "10.10.2026 18.00.00", now=NOW)
    assert source == "задана"
    assert start == datetime(2026, 10, 1, 0, 0, 0)


def test_broken_cursor_is_refused_with_advice():
    with pytest.raises(Exception) as exc:
        digest.resolve_window(1, None, None, "вчера вечером", now=NOW)
    assert "--reset" in str(exc.value)


def test_reversed_window_is_normalised():
    start, end, _ = digest.resolve_window(
        1, "12.10.2026", "01.10.2026", None, now=NOW)
    assert start <= end


# --- реестр по дням ---------------------------------------------------------

async def test_incoming_asks_one_call_per_day():
    """Широкое окно Saby молча усекает до 25 строк; суточное — нет."""
    def page(params):
        day = (params.get("Фильтр") or {})["ДатаС"]
        return {"Реестр": [_event(number=day[-2:], when=f"{day} 09.00.00")],
                "Навигация": _nav()}

    client = _Fake(spisok_dokumentov_po_sobytiyam=page)
    notes: list[str] = []
    result = await digest.incoming(client, datetime(2026, 10, 11, 0, 0, 0),
                                   datetime(2026, 10, 12, 23, 59, 59),
                                   digest._Budget(), notes)
    assert len(client.seen) == 2
    assert result["всего"] == 2
    assert result["полно"] is True
    assert notes == []


async def test_incoming_always_asks_for_a_big_page():
    """Страница запрашивается явно: без неё Saby отдаёт 25 строк.

    Раньше здесь стоял обратный тест — «Навигация не работает». Утверждение
    было ложным: оно родилось из пробы несуществующего поля
    `Навигация.Позиция.ДатаВремя`. Живая проверка 15.10.2026 показала, что
    `Навигация.РазмерСтраницы` внутри `Фильтр` работает и снимает усечение.
    """
    client = _Fake(spisok_dokumentov_po_sobytiyam={"Реестр": [_event()],
                                                   "Навигация": _nav("Да")})
    await digest.incoming(client, datetime(2026, 10, 12, 0, 0, 0),
                          datetime(2026, 10, 12, 23, 59, 59), digest._Budget(), [])
    assert client.seen, "реестр не запрашивался"
    for command, params in client.seen:
        nav = (params.get("Фильтр") or {}).get("Навигация")
        assert nav == {"РазмерСтраницы": digest.PAGE_SIZE}, f"{command}: {nav}"
        # Навигация должна быть ВНУТРИ фильтра: в корне параметров Saby её
        # игнорирует и отвечает пустым реестром.
        assert "Навигация" not in params


async def test_changes_ask_for_a_big_page_too():
    """`СписокИзменений` — главный потребитель страницы: без неё он режет
    окно до 15 минут рекурсией и теряет события на стыках.

    Порция — 50, потолок из справки changeslist, а не общие 200."""
    client = _Fake(spisok_izmeneniy={"Документ": [_change()], "Навигация": _nav()})
    await digest.changes(client, datetime(2026, 10, 12, 0, 0, 0),
                         datetime(2026, 10, 12, 23, 59, 59), digest._Budget(), [])
    sent = client.params_of("spisok_izmeneniy")
    assert len(sent) == 1, f"окно не влезло в одну страницу: {len(sent)} вызовов"
    assert (sent[0]["Фильтр"]["Навигация"]
            == {"РазмерСтраницы": "50"})


async def test_regression_overflow_day_is_split_by_state_and_says_so():
    """День не влез в порцию: режем по состояниям и честно пишем в notes."""
    states = ["Выполнение завершено успешно", "В обработке"]

    def page(params):
        state = (params.get("Фильтр") or {}).get("Состояние")
        if state is None:
            return {"Реестр": [_event(state=s, number=f"{s[:2]}{n}",
                                     ident=f"doc-{s[:2]}{n}")
                               for s in states for n in ("1", "2")],
                    "Навигация": _nav("Да")}
        return {"Реестр": [_event(state=state, number=f"{state[:2]}{n}",
                                 ident=f"doc-{state[:2]}{n}")
                           for n in ("1", "2")], "Навигация": _nav()}

    client = _Fake(spisok_dokumentov_po_sobytiyam=page)
    notes: list[str] = []
    result = await digest.incoming(client, datetime(2026, 10, 12, 0, 0, 0),
                                   datetime(2026, 10, 12, 23, 59, 59),
                                   digest._Budget(), notes)
    assert result["всего"] == 4
    assert result["полно"] is True
    assert len(client.seen) == 3  # первый обход + два состояния
    assert any("порции" in note for note in notes)


async def test_regression_never_splits_by_attachment_type():
    """ТипВложения на живом контуре вернул 0 строк вместо 13 счетов.

    «Разбиение» по нему дало бы пустоту вместо полноты, поэтому его нет.
    """
    def page(params):
        if "ТипВложения" in (params.get("Фильтр") or {}):
            return {"Реестр": [], "Навигация": _nav()}
        return {"Реестр": [_event(state="В обработке")], "Навигация": _nav("Да")}

    client = _Fake(spisok_dokumentov_po_sobytiyam=page)
    notes: list[str] = []
    result = await digest.incoming(client, datetime(2026, 10, 12, 0, 0, 0),
                                   datetime(2026, 10, 12, 23, 59, 59),
                                   digest._Budget(), notes)
    assert result["полно"] is False
    assert result["всего"] == 1
    assert not any("ТипВложения" in p.get("Фильтр", {})
                   for p in client.params_of("spisok_dokumentov_po_sobytiyam"))
    assert any("не полностью" in note for note in notes)


async def test_regression_cursor_window_trims_events_by_time():
    """Фильтр реестра понимает только даты — остаток окна дорезаем сами.

    Курсор может стоять на 06:00, а день реестр отдаёт с полуночи.
    """
    def page(params):
        return {"Реестр": [_event(number="1", when="12.10.2026 02.00.00", ident="d1"),
                           _event(number="2", when="12.10.2026 14.50.12", ident="d2")],
                "Навигация": _nav()}

    client = _Fake(spisok_dokumentov_po_sobytiyam=page)
    result = await digest.incoming(client, datetime(2026, 10, 12, 6, 0, 0),
                                   datetime(2026, 10, 12, 23, 59, 59),
                                   digest._Budget(), [])
    assert result["всего"] == 1
    assert result["события"][0]["номер"] == "2"


async def test_events_without_readable_time_are_not_dropped():
    result = digest._within([{"время_события": "когда-то", "ид": "a"}],
                            datetime(2026, 10, 12, 6, 0, 0), NOW)
    assert len(result) == 1


async def test_duplicate_events_are_collapsed():
    client = _Fake(spisok_dokumentov_po_sobytiyam={
        "Реестр": [_event(), _event()], "Навигация": _nav()})
    result = await digest.incoming(client, datetime(2026, 10, 12, 0, 0, 0),
                                   datetime(2026, 10, 12, 23, 59, 59),
                                   digest._Budget(), [])
    assert result["всего"] == 1


# --- задачи -----------------------------------------------------------------

async def test_regression_task_list_duplicates_are_removed():
    """«На мне» отдаёт 9 строк на 8 документов — дубль по Идентификатор."""
    client = _Fake(spisok_zadach={"Документ": [
        _task(), _task(), _task(ident="other", title="Задача № 76")],
        "Навигация": _nav()})
    result = await digest.pending(client, digest._Budget(), [], NOW.date())
    on_me = result["списки"]["НаМне"]
    assert len(on_me) == 2
    assert result["на_мне"] == 2


async def test_regression_employee_filter_is_never_sent():
    """Фильтр Сотрудник Saby молча игнорирует — задачи чужих остаются."""
    client = _Fake(spisok_zadach={"Документ": [_task(person="Шаймуллина")],
                                  "Навигация": _nav()})
    await digest.pending(client, digest._Budget(), [], NOW.date())
    sent = client.params_of("spisok_zadach")[0]
    assert "Сотрудник" not in sent["Фильтр"]


async def test_regression_overdue_needs_a_deadline():
    """Срок пуст у 24 событий из 25: просрочку по нему не выдумываем."""
    client = _Fake(spisok_zadach={"Документ": [
        _task(created="16.03.2026 10.00.00"),      # старый, но без срока
        _task(ident="b", created="01.10.2026 10.00.00", due="05.10.2026"),
    ], "Навигация": _nav()})
    result = await digest.pending(client, digest._Budget(), [], NOW.date())
    items = {i["ид"]: i for i in result["списки"]["НаМне"]}
    assert items["becfafc6"].get("просрочено") is None
    assert items["b"]["просрочено"] is True
    assert result["самый_старый_дней"] > 200


async def test_outgoing_list_is_optional():
    client = _Fake(spisok_zadach={"Документ": [], "Навигация": _nav()})
    result = await digest.pending(client, digest._Budget(), [], NOW.date())
    assert "ОтМеня" not in result["списки"]
    result = await digest.pending(client, digest._Budget(), [], NOW.date(),
                                  include_outgoing=True)
    assert "ОтМеня" in result["списки"]


# --- изменения и курсор -----------------------------------------------------

async def test_regression_duplicate_documents_are_collapsed():
    client = _Fake(spisok_izmeneniy={"Документ": [_change(), _change()],
                                     "Навигация": _nav()})
    result = await digest.changes(client, datetime(2026, 10, 11),
                                  datetime(2026, 10, 12), digest._Budget(), [])
    assert result["всего"] == 1


async def test_cursor_advances_only_on_complete_answer():
    client = _Fake(spisok_izmeneniy={"Документ": [_change()], "Навигация": _nav()})
    result = await digest.changes(client, datetime(2026, 10, 11), NOW,
                                  digest._Budget(), [])
    assert result["курсор_новый"] == "12.10.2026 18.00.00"


async def test_regression_truncated_changes_do_not_move_the_cursor():
    """Сервер вечно говорит «ЕстьЕще» — курсор сдвигаться не должен.

    Обход идёт по «ИдентификаторСобытия». Сервер, отдающий одну и ту же
    строку, курсор не двигает: обход обязан остановиться, сказать, что выдача
    неполная, и оставить курсок на месте.
    """
    client = _Fake(spisok_izmeneniy={
        "Документ": [_change()],
        "Навигация": _nav("Да"),
    })
    notes: list[str] = []
    result = await digest.changes(client, datetime(2026, 10, 12), NOW,
                                   digest._Budget(), notes)
    assert result["курсор_новый"] is None
    assert result["полно"] is False
    assert any("урсор" in note for note in notes)
    assert len(client.seen) <= 3  # остановился, а не крутился


async def test_regression_unsliceable_changes_keep_cursor():
    """Обход упёрся в бюджет и не прошёл — курсор всё равно не двигаем."""
    client = _Fake(spisok_izmeneniy={
        "Документ": [_change(ident=str(i), event_id=f"ev-{i}")
                     for i in range(int(digest.PAGE_SIZE))],
        "Навигация": _nav("Да"),
    })
    notes: list[str] = []
    result = await digest.changes(client, datetime(2026, 10, 1), NOW,
                                   digest._Budget(4), notes)
    assert result["курсор_новый"] is None
    assert result["полно"] is False
    assert len(client.seen) <= 4
    assert notes
async def test_changes_are_sent_with_datetime_boundary():
    client = _Fake(spisok_izmeneniy={"Документ": [], "Навигация": _nav()})
    await digest.changes(client, datetime(2026, 10, 11, 8, 30, 0),
                         datetime(2026, 10, 12, 18, 0, 0), digest._Budget(), [])
    sent = client.params_of("spisok_izmeneniy")[0]["Фильтр"]
    assert sent["ДатаВремяС"] == "11.10.2026 08.30.00"
    assert sent["ДатаВремяПо"] == "12.10.2026 18.00.00"


# --- служебные этапы --------------------------------------------------------

async def test_stage_list_is_only_fetched_when_counter_is_positive():
    client = _Fake(informaciya_o_sluzhebnyh_etapah={"ЧислоНеобработанныхЭтапов": "0"})
    result = await digest.stages(client, digest._Budget())
    assert result["необработанных"] == 0
    assert client.params_of("spisok_sluzhebnyh_etapov") == []


async def test_stages_are_fetched_when_counter_is_positive():
    client = _Fake(informaciya_o_sluzhebnyh_etapah={"ЧислоНеобработанныхЭтапов": "2"},
                   spisok_sluzhebnyh_etapov={"Документ": [_task()], "Навигация": _nav()})
    result = await digest.stages(client, digest._Budget())
    assert result["необработанных"] == 2
    assert len(result["этапы"]) == 1


# --- сборка и бюджет --------------------------------------------------------

async def test_build_stays_within_call_budget():
    client = _Fake(
        spisok_dokumentov_po_sobytiyam={"Реестр": [_event()], "Навигация": _nav()},
        spisok_zadach={"Документ": [_task()], "Навигация": _nav()},
        spisok_izmeneniy={"Документ": [_change()], "Навигация": _nav()},
        informaciya_o_sluzhebnyh_etapah={"ЧислоНеобработанныхЭтапов": "0"},
    )
    payload = await digest.build(client, days=1, now=NOW)
    assert payload["вызовы"] <= digest.MAX_CALLS
    assert payload["вызовы"] == 5  # 1 реестр + 1 задачи + 1 изменения + 1 этапы
    assert payload["ok"] is True


async def test_budget_stops_collecting_and_says_so():
    """Сводка обязана уместиться в разумное число запросов и сказать, если нет."""
    budget = digest._Budget(3)
    assert budget.take() is True
    assert budget.take(3) is False  # не хватает места — запрет целиком
    notes: list[str] = []
    client = _Fake(spisok_dokumentov_po_sobytiyam={"Реестр": [], "Навигация": _nav()},
                   spisok_zadach={"Документ": [], "Навигация": _nav()},
                   spisok_izmeneniy={"Документ": [], "Навигация": _nav()},
                   informaciya_o_sluzhebnyh_etapah={})
    payload = await digest.build(client, days=5, now=NOW, max_calls=3)
    # Пять суток реестра плюс задачи, изменения и этапы — больше лимита не
    # выдаём, а в notes говорим, где окно обрезано.
    assert payload["вызовы"] <= 3
    assert payload["пришло"]["полно"] is False
    assert any("лимит" in note or "обрезано" in note for note in payload["notes"])


async def test_render_says_where_data_is_incomplete():
    payload = {
        "окно": {"с": "11.10.2026 00.00.00", "по": "12.10.2026 18.00.00"},
        "пришло": {"всего": 1, "документов": 1, "полно": False,
                   "неполные_дни": ["11.10.2026"], "события": []},
        "требует_внимания": {"требует_внимания": 1, "самый_старый_дней": 240,
                             "списки": {"НаМне": [{
                                 "название": "Договор № 8957",
                                 "состояние": "В обработке", "возраст_дней": 240,
                                 "требует_внимания": True}]}},
        "изменилось": {"всего": 0, "документов": 0, "полно": False, "события": []},
        "служебные_этапы": {"необработанных": 0},
        "notes": ["11.10.2026: показано не полностью."],
        "вызовы": 5,
    }
    text = digest.render(payload)
    assert "неполно за: 11.10.2026" in text
    assert "Договор № 8957" in text
    assert "240 дн." in text
    assert "курсор не сдвинут" in text


def test_render_works_without_optional_sections():
    text = digest.render({"окно": {"с": "11.10.2026 00.00.00",
                                   "по": "12.10.2026 18.00.00"},
                          "пришло": {"всего": 0, "документов": 0, "полно": True,
                                     "события": []},
                          "вызовы": 1})
    assert "Пришло событий: 0" in text
    assert "Требует внимания" not in text


# --- курсор в файле ---------------------------------------------------------

def test_cursor_roundtrip(tmp_path):
    state = DigestState(tmp_path / "digest-state.json")
    assert state.cursor() is None
    state.save_cursor("12.10.2026 18.00.00", calls=5)
    assert state.cursor() == "12.10.2026 18.00.00"
    assert state.describe()["полно"] is True


def test_regression_incomplete_run_keeps_previous_cursor(tmp_path):
    state = DigestState(tmp_path / "digest-state.json")
    state.save_cursor("11.10.2026 18.00.00")
    state.save_cursor("12.10.2026 18.00.00", complete=False)
    assert state.cursor() == "11.10.2026 18.00.00"


def test_corrupt_cursor_file_does_not_break_startup(tmp_path):
    path = tmp_path / "digest-state.json"
    path.write_text("{не json", encoding="utf-8")
    assert DigestState(path).cursor() is None


def test_clear_removes_cursor(tmp_path):
    state = DigestState(tmp_path / "digest-state.json")
    state.save_cursor("12.10.2026 18.00.00")
    assert state.clear() is True
    assert state.clear() is False


def test_state_shares_home_with_session(tmp_path, monkeypatch):
    """SABY_MCP_HOME перекрывает оба файла разом."""
    monkeypatch.setenv("SABY_MCP_HOME", str(tmp_path / "home"))
    assert DigestState().path.parent == CredentialStore().path.parent


# --- инструмент и границы ----------------------------------------------------

async def _tool(mcp, name: str, args: dict | None = None) -> dict:
    result = await mcp.call_tool(name, args or {})
    blocks = getattr(result, "content", result)
    if isinstance(blocks, tuple):
        blocks = blocks[0]
    if isinstance(blocks, dict):
        return blocks
    return json.loads("\n".join(getattr(b, "text", "") for b in blocks))


@pytest.fixture
async def mcp_with_fake(tmp_path):
    from mcp.server.mcpserver import MCPServer

    from saby_mcp_ro import tools as tools_mod

    server = MCPServer("saby-digest-test")
    fake = _Fake(
        spisok_dokumentov_po_sobytiyam={"Реестр": [_event()], "Навигация": _nav()},
        spisok_zadach={"Документ": [_task(), _task()], "Навигация": _nav()},
        spisok_izmeneniy={"Документ": [_change()], "Навигация": _nav()},
        informaciya_o_sluzhebnyh_etapah={"ЧислоНеобработанныхЭтапов": "0"},
    )
    # Клиент — подставной, логика сводки настоящая: тест проверяет сборку,
    # а не подмену.
    tools_mod.register(server, fake, CredentialStore(tmp_path / "session.json"))
    return server


async def test_digest_tool_returns_readable_text(mcp_with_fake):
    # Явное `since`, а не `days`: подставные события датированы 12.10.2026, а
    # `days` считается от реального «сейчас» и каждый день после 12-го делает
    # тест падающим. Здесь окно фиксировано, результат не зависит от даты.
    out = await _tool(mcp_with_fake, "saby_digest", {"since": "12.10.2026"})
    assert out["ok"] is True
    assert out["сводка"]
    assert out["пришло"]["всего"] == 1
    assert out["требует_внимания"]["на_мне"] == 1  # дубль задачи убран
    assert out["курсор"]["сохранён"] is False


async def test_digest_tool_saves_cursor_only_when_asked(mcp_with_fake):
    out = await _tool(mcp_with_fake, "saby_digest",
                      {"since": "11.10.2026", "save_cursor": True})
    assert out["курсор"]["сохранён"] is True
    assert out["курсор"]["новый"]


async def test_digest_tool_never_writes_without_save(mcp_with_fake, tmp_path):
    await _tool(mcp_with_fake, "saby_digest", {"since": "11.10.2026"})
    assert not (tmp_path / "digest-state.json").exists()


async def test_cursor_tool_reports_and_resets(mcp_with_fake, tmp_path):
    info = await _tool(mcp_with_fake, "saby_digest_cursor")
    assert info["exists"] is False
    assert "курсор" in info


async def test_digest_tool_is_read_only(mcp_with_fake):
    for tool in await mcp_with_fake.list_tools():
        if tool.name.startswith("saby_digest"):
            assert tool.annotations.read_only_hint is True
            assert not tool.annotations.destructive_hint
            props = (tool.input_schema or {}).get("properties", {})
            for forbidden in ("url", "host", "path", "method", "endpoint"):
                assert forbidden not in props


def test_server_still_registers_only_read_commands():
    """Сводка не должна была пробить границу «только чтение»."""
    from saby_mcp_ro import catalog

    for cmd in catalog.ALL_COMMANDS:
        assert cmd.command.startswith(("СБИС.", "sabyMchd.", "sabyDepartment.",
                                       "sabyOurCompany."))


def test_build_is_importable_for_cli():
    """CLI и инструмент обязаны звать одну и ту же реализацию."""
    import inspect

    from saby_mcp_ro import server

    assert inspect.iscoroutinefunction(digest.build)
    assert "digest.build" in inspect.getsource(server._digest)
