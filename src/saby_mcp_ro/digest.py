"""Сводка входящих документов: что пришло, что висит, что изменилось.

Зачем модуль, если агент умеет вызывать команды сам
-------------------------------------------------
Четыре причины, каждая проверена на боевой сессии (октябрь 2026):

1. **Ответ реестра нечитаем.** `СБИС.СписокДокументовПоСобытиям` отдаёт 25
   событий весом 209 КБ: восемь из них — это `СсылкаНаPDF` (base64) и
   `СсылкаНаАрхив` в каждом документе и в каждом вложении. Агент, который
   приносит такой ответ в переписку, тратит контекст на то, что человеку
   не нужно, и всё равно не видит сути. Здесь от документа остаётся ~20 полей,
   а ссылки появляются только по запросу.

2. **Порция в 25 строк не обходится.** Saby сообщает `Навигация.ЕстьЕще`,
   но объект `Навигация` в запросе не принимает (проверено: `Навигация`,
   `Позиция`, `НачальнаяПозиция`, `Страница`, `РазмерСтраницы` — все пять
   вернули ту же первую страницу). Поэтому окно режется, и режется тем, что
   команда умеет: реестр принимает только даты, поэтому он обходится по
   суткам (и по состояниям, если сутки не влезли), а список изменений
   принимает полное время и делится пополам до 15 минут. На живых данных
   окно изменений за двое суток после деления вернуло 92 события по 38
   документам вместо усечённых 25 строк.

   ╔═══ ИСПРАВЛЕНИЕ 15.10.2026 ═══════════════════════════════════════════╗
   Пункт выше верен в выводе и неверен в причине. `Навигация.РазмерСтраницы`
   РАБОТАЕТ: живая проверка на `СписокИзменений` за окно 10:00–11:00 отдала
   без навигации 25 строк с `ЕстьЕще = «Да»`, а с `РазмерСтраницы = «200»` —
   все 98 строк с `ЕстьЕще = «Нет»`, за один вызов. За сутки 15.10 так
   пришло 175 событий вместо 154 у старой схемы с делением окна.
   Прежний вывод «Навигация не принимается» родился из пробы несуществующего
   поля `Навигация.Позиция.ДатаВремя` (на него Saby отвечает -32601) и из
   пробы курсора с идентификатором ДОКУМЕНТА вместо СОБЫТИЯ.
   Теперь страница запрашивается явно, а деление окна осталось запасным
   путём на случай, когда даже 200 строк не хватило.
   ╚═══════════════════════════════════════════════════════════════════════╝

3. **Детали дублируются и молчат.** «На мне» отдаёт 9 строк на 8 документов;
   `СписокИзменений` присылает один документ дважды; `Фильтр.Сотрудник`
   в `СписокЗадач` молча игнорируется (вернул чужие задачи); `Срок` пуст у
   24 из 25 событий; `ТипВложения` в реестре отсеивает всё подряд. Дедупликация
   и честные метрики — здесь, а не в голове у агента.

4. **Фильтр реестра — по времени события, а не документа.** За 12.10 пришло
   событие по договору от 20.07. Для сводки это верно, но окно вида «с 06:00»
   после обхода по суткам дорезается по времени события своими руками.

Чего модуль не делает
---------------------
Не выдумывает: валюту (Saby её не отдаёт), просрочку по пустому `Срок`,
«всё прочитано» при `ЕстьЕще = Да`. Усечение всегда попадает в `notes` и в
текст сводки — человек должен видеть, где данных не хватило.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from . import config
from . import pagination
from .client import SabyClient, SabyError

# Форматы Saby: даты в фильтрах реестра и даты-время в списке изменений.
DATE_FMT = "%d.%m.%Y"
DATETIME_FMT = "%d.%m.%Y %H.%M.%S"

DEFAULT_DAYS = 1
MAX_DAYS = 31
# Потолок вызовов на сводку. С явной страницей (см. PAGE_SIZE) типичный
# ежедневный запуск — 3..5 вызовов; столько же нужно, если окно всё же
# пришлось дробить. Лимит срабатывает на самых загруженных днях и честно
# попадает в notes.
MAX_CALLS = 48
MAX_EVENTS = 5000
# Предел событий в разделе «изменилось». Лимит вызовов ограничивает обход сам
# (48 вызовов по 200 строк — это до 9600 событий), поэтому отдельно держать
# маленький потолок незачем: на загруженных сутках 16.10 он срабатывал на
# 400 событиях и объявлял сводку неполной, потратив всего пять вызовов.
# Размер страницы, который мы ПРОСИМ у Saby. Без `Навигации` сервер отдаёт
# первую страницу сам и режет по 25 строк, а `Навигация.РазмерСтраницы`
# внутри `Фильтр` увеличивает её до 200 — проверено живьём 15.10.2026 на
# `СписокИзменений` (окно 10:00–11:00: 25 строк и «ЕстьЕще» без навигации
# против 98 строк и «ЕстьЕще = Нет» с ней).
#
# Потолок Saby — 200: всё, что больше, молча схлопывается в 200, а 5000 и
# выше дают ошибку. Значение принимается и строкой, и числом.
PAGE_SIZE = "200"
# Одностраничный фильтр для списочных команд. Указывать нужно ВНУТРИ `Фильтр`:
# в корне параметров объект игнорируется и ответ приходит пустым.
def page_filter(extra: dict[str, Any] | None = None,
                accepts_org: bool = True) -> dict[str, Any]:
    """Фильтр списочной команды: страница плюс отбор по организации.

    Отбор берётся из `config.json` — без него Saby отдаёт смесь по всем
    организациям кабинета, а у нас их две. Организацию, заданную в `extra`,
    не перебиваем.
    """
    merged: dict[str, Any] = dict(extra or {})
    merged["Навигация"] = {"РазмерСтраницы": PAGE_SIZE}
    return config.scoped_filter({"Фильтр": merged}, accepts_org)


# Дробь, до которой окно изменений режется — ТОЛЬКО запасной путь, когда
# `ЕстьЕще = «Да»` даже при странице в 200 строк. На боевых данных 15.10
# до него не дошло ни разу; до введения `Навигации` это был основной путь
# и стоил ~20 вызовов на сутки.
MIN_SLICE_MINUTES = 15
# Сколько состояний пробуем при переполненном дне: имена берём из первой
# порции, а перебор всех состояний Saby здесь недоступен и не нужен.
MAX_SPLITS = 6

# Что считаем «требует внимания».
#
# Код состояния надёжнее названия: проверено живьём 7 = «Выполнение завершено
# успешно», 10 = «В обработке», 27 = «Ожидает аннулирования». Полного
# справочника состояний у API нет, поэтому по коду берём только подтверждённое,
# а всё остальное ловим по слову в названии. Ложное срабатывание здесь
# безопаснее пропуска: лишняя строка в сводке дешевле потерянного документа.
ATTENTION_CODES = frozenset({"10"})
ATTENTION_WORDS = ("проблем", "ошибк", "отказ", "ожидает", "не обработан",
                    "необработан", "требует")


# --- даты -------------------------------------------------------------------

def parse_date(value: Any) -> date | None:
    """Дата в любом из форматов, которые встречаются у людей и у Saby."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in (DATE_FMT, "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def fmt_date(value: date) -> str:
    return value.strftime(DATE_FMT)


def parse_dt(value: Any) -> datetime | None:
    """Дата-время Saby («12.10.2026 17.39.53») либо дата без времени."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in (DATETIME_FMT, DATE_FMT, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def fmt_dt(value: datetime) -> str:
    return value.strftime(DATETIME_FMT)


def _days_ago(moment: str | None, today: date) -> int | None:
    parsed = parse_dt(moment)
    if parsed is None:
        return None
    return max(0, (today - parsed.date()).days)


# --- проекция документа -----------------------------------------------------

def _fio(person: Any) -> str:
    if not isinstance(person, dict):
        return ""
    return " ".join(str(person.get(k) or "") for k in ("Фамилия", "Имя", "Отчество")).strip()


def _org(node: Any) -> dict[str, str]:
    """Контрагент или своя организация: название и реквизиты.

    Saby кладёт их в `СвЮЛ`; для физлица там может быть `СвФЛ`, поэтому
    берём любой вложенный словарь с названием, а не только СвЮЛ.
    """
    if not isinstance(node, dict):
        return {"название": "", "инн": "", "кпп": ""}
    source = node.get("СвЮЛ") if isinstance(node.get("СвЮЛ"), dict) else {}
    if not source:
        source = node.get("СвФЛ") if isinstance(node.get("СвФЛ"), dict) else node
    return {
        "название": str(source.get("Название") or ""),
        "инн": str(source.get("ИНН") or ""),
        "кпп": str(source.get("КПП") or ""),
    }


def _state(node: Any) -> dict[str, str]:
    state = node if isinstance(node, dict) else {}
    return {
        "код": str(state.get("Код") or ""),
        "название": str(state.get("Название") or ""),
        "примечание": str(state.get("Примечание") or ""),
    }


def attention(state: dict[str, str]) -> bool:
    """Требует ли состояние внимания человека."""
    if state.get("код") in ATTENTION_CODES:
        return True
    name = (state.get("название") or "").lower()
    return any(word in name for word in ATTENTION_WORDS)


def compact_doc(doc: Any, *, event_time: str = "", with_link: bool = False) -> dict[str, Any]:
    """Документ в виде, пригодном для сводки.

    Из ответа Saby уходят `СсылкаНаPDF`, `СсылкаНаАрхив`, `СсылкаДляКонтрагент`,
    `Файл` и `Упакован` — это тысячи символов base64 на строку. Ссылка в
    кабинет остаётся, но только когда её попросили: по умолчанию её в сводке
    нет, чтобы человек читал документы, а не URL.
    """
    if not isinstance(doc, dict):
        return {}

    state = _state(doc.get("Состояние"))
    attachments = [a for a in (doc.get("Вложение") or []) if isinstance(a, dict)]

    out: dict[str, Any] = {
        "ид": str(doc.get("Идентификатор") or ""),
        "номер": str(doc.get("Номер") or ""),
        "название": str(doc.get("Название") or ""),
        "дата": str(doc.get("Дата") or ""),
        "тип": str(doc.get("Тип") or ""),
        "направление": str(doc.get("Направление") or ""),
        "регламент": str((doc.get("Регламент") or {}).get("Название") or "")
        if isinstance(doc.get("Регламент"), dict) else "",
        "контрагент": _org(doc.get("Контрагент")),
        "наша_организация": _org(doc.get("НашаОрганизация"))["название"],
        "ответственный": _fio(doc.get("Ответственный")),
        "подразделение": str((doc.get("Подразделение") or {}).get("Название") or "")
        if isinstance(doc.get("Подразделение"), dict) else "",
        "состояние": state["название"],
        "код_состояния": state["код"],
        "примечание_состояния": state["примечание"],
        "требует_внимания": attention(state),
        "сумма": str(doc.get("Сумма") or ""),
        "срок": str(doc.get("Срок") or ""),
        "примечание": str(doc.get("Примечание") or ""),
        "удален": str(doc.get("Удален") or ""),
    }
    if event_time:
        out["время_события"] = event_time

    if attachments:
        first = attachments[0]
        out["вложение"] = {
            "название": str(first.get("Название") or ""),
            "тип": str(first.get("Тип") or ""),
            "дата": str(first.get("Дата") or ""),
            "редакция": str((first.get("Редакция") or {}).get("Номер") or "")
            if isinstance(first.get("Редакция"), dict) else "",
            "зашифрован": str(first.get("Зашифрован") or ""),
            "колонок": len(attachments),
        }
        if not out["сумма"]:
            out["сумма"] = str(first.get("Сумма") or "")
        if first.get("СуммаБезНДС"):
            out["сумма_без_ндс"] = str(first.get("СуммаБезНДС"))

    if with_link:
        link = ""
        for source in (doc.get("СсылкаДляНашаОрганизация"),
                       doc.get("СсылкаВКабинет")):
            if source:
                link = str(source)
                break
        if not link and attachments:
            link = str(attachments[0].get("СсылкаВКабинет") or "")
        if link:
            out["ссылка"] = link
    return out


def compact_event(row: Any, *, with_link: bool = False) -> dict[str, Any]:
    """Событие реестра: документ плюс состояние именно события.

    У события своё `Состояние` — «Ожидает аннулирования» относится к приходу
    новой редакции, а не к судьбе документа, и в сводке это важно не путать.
    """
    if not isinstance(row, dict):
        return {}
    event_state = _state(row.get("Состояние"))
    event_time = str(row.get("ДатаВремя") or "")
    out = compact_doc(row.get("Документ"), event_time=event_time, with_link=with_link)
    if event_state.get("название"):
        out["состояние_события"] = event_state["название"]
        out["примечание_события"] = event_state["примечание"]
        out["требует_внимания"] = out.get("требует_внимания") or attention(event_state)
    return out


# --- сборщики ---------------------------------------------------------------

class _Budget:
    """Счётчик вызовов. Сводка обязана уместиться в разумное число запросов."""

    def __init__(self, limit: int = MAX_CALLS):
        self.limit = limit
        self.used = 0

    def take(self, count: int = 1) -> bool:
        if self.used + count > self.limit:
            return False
        self.used += count
        return True

    @property
    def remaining(self) -> int:
        """Сколько вызовов ещё можно потратить."""
        return max(0, self.limit - self.used)


async def _incoming_day(
    client: SabyClient, day: date, budget: _Budget, notes: list[str],
    with_link: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """События реестра «Входящие» за один день.

    День — единица обхода, потому что фильтр реестра понимает только даты, а
    не время. Страница запрашивается явно (`Навигация.РазмерСтраницы`), так
    что сутки помещаются в один вызов почти всегда; если всё же не влезли,
    день делится по состояниям: имена берём из первой порции, а фильтр
    `Состояние` в реестре работает (проверено живьём: «В обработке» за месяц
    вернул 9 событий, `ЕстьЕще = Нет`).

    Чего сознательно НЕ делаем: не делим день по типу документа. Фильтр
    `ТипВложения` на живом контуре вернул 0 строк вместо 13 счетов — то есть
    молча отсеивает всё, и «разбиение» дало бы пустоту вместо полноты.
    """
    base = {"ТипРеестра": "Входящие", "ДатаС": fmt_date(day), "ДатаПо": fmt_date(day)}
    result = await client.call_command(
        "spisok_dokumentov_po_sobytiyam", {"Фильтр": page_filter(base)})
    rows = pagination.extract_rows(result, "Реестр")
    events = [compact_event(r, with_link=with_link) for r in rows]

    if not pagination.has_more(result):
        return events, True

    # Состояние приходит словарём, но если контракт сдвинется и придёт строкой,
    # сводка не должна падать на ровном месте — отсюда _state, а не .get() напрямую.
    states = sorted({_state(r.get("Состояние"))["название"] for r in rows} - {""})
    if 1 < len(states) <= MAX_SPLITS and budget.take(len(states)):
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        complete = True
        for name in states:
            part = await client.call_command(
                "spisok_dokumentov_po_sobytiyam",
                {"Фильтр": page_filter({**base, "Состояние": name})},
            )
            if pagination.has_more(part):
                complete = False
            for row in pagination.extract_rows(part, "Реестр"):
                event = compact_event(row, with_link=with_link)
                merged[(str(event.get("время_события")), str(event.get("ид")),
                        str(event.get("состояние_события") or ""))] = event
        notes.append(
            f"{fmt_date(day)}: событий больше порции в {len(states)} строк, день "
            f"собран по состояниям"
            + ("" if complete else ", и даже после этого помечен как неполный")
            + "."
        )
        return list(merged.values()), complete

    notes.append(
        f"{fmt_date(day)}: Saby отдала только первые {len(rows)} событий — "
        f"порция кончилась, а разделить день нечем (ТипВложения отдаёт пустоту). "
        "Эти данные за день показаны не полностью."
    )
    return events, False


def _within(events: list[dict[str, Any]], start: datetime,
            end: datetime) -> list[dict[str, Any]]:
    """Оставить события, попавшие в окно по времени.

    Событие без разбираемого времени остаётся: выбросить его — значило бы
    потерять документ из сводки из-за того, что Saby прислал нестандартный формат.
    """
    kept = []
    for event in events:
        moment = parse_dt(event.get("время_события"))
        if moment is None or start <= moment <= end:
            kept.append(event)
    return kept


async def incoming(
    client: SabyClient, start: datetime, end: datetime, budget: _Budget,
    notes: list[str], with_link: bool = False,
) -> dict[str, Any]:
    """Реестр «Входящие» за окно, по дням.

    Фильтр реестра понимает только даты, поэтому окно вида «с 12.10 06:00»
    после обхода по суткам дорезается по времени события. Это точная отсечка,
    а не приближение: лишнее не показывается, потерянным ничего не становится.
    """
    events: list[dict[str, Any]] = []
    partial: list[str] = []
    day = start.date()
    while day <= end.date():
        if not budget.take():
            notes.append(
                f"Окно до {fmt_date(end)} обрезано: исчерпан лимит "
                f"{budget.limit} вызовов на сводку."
            )
            partial.append(f"{fmt_date(day)}-{fmt_date(end)}")
            break
        day_events, complete = await _incoming_day(client, day, budget, notes, with_link)
        events.extend(day_events)
        if not complete:
            partial.append(fmt_date(day))
        day += timedelta(days=1)

    events = _within(events, start, end)

    # Один и тот же документ может прийти в реестр дважды (например, пришло
    # уточнение). Ключ — время события, документ И состояние события: на
    # живых данных повторялись целые строки, но два разных события одного
    # документа в одну секунду — это разные события, и схлопывать их нельзя.
    seen: set[tuple[str, str, str]] = set()
    unique_events = []
    for event in events:
        key = (str(event.get("время_события")), str(event.get("ид")),
               str(event.get("состояние_события") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique_events.append(event)

    if len(unique_events) > MAX_EVENTS:
        notes.append(
            f"В окне больше {MAX_EVENTS} событий; показаны первые {MAX_EVENTS}."
        )
        unique_events = unique_events[:MAX_EVENTS]

    return {
        "всего": len(unique_events),
        "документов": len({str(e.get("ид")) for e in unique_events if e.get("ид")}),
        "по_типам": _tally(unique_events, "тип"),
        "по_контрагентам": _tally(unique_events, "контрагент.название"),
        "требует_внимания": sum(1 for e in unique_events if e.get("требует_внимания")),
        "полно": not partial,
        "неполные_дни": partial,
        "события": unique_events,
    }


def _tally(rows: list[dict[str, Any]], dotted: str) -> dict[str, int]:
    """Сколько раз встретилось значение по точечному пути. По убыванию."""
    counts: dict[str, int] = {}
    for row in rows:
        value = pagination.dig(row, dotted)
        if isinstance(value, str):
            value = value.strip()
        if not value:
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


async def pending(
    client: SabyClient, budget: _Budget, notes: list[str], today: date,
    include_outgoing: bool = False,
) -> dict[str, Any]:
    """Задачи «На мне» (и по желанию «От меня»).

    Проверено живьём: в списке «На мне» один документ приходит дважды, а
    фильтр `Сотрудник` игнорируется — задачи чужих сотрудников остаются в
    выдаче. Поэтому дедуплицируем по идентификатору и не фильтруем по людям,
    а возраст считаем от `ДатаВремяСоздания`: `Срок` пуст у подавляющего
    большинства строк, и «просрочка» по нему была бы выдумкой.
    """
    lists = ["НаМне"] + (["ОтМеня"] if include_outgoing else [])
    sections: dict[str, Any] = {}
    for name in lists:
        if not budget.take():
            notes.append(f"Список «{name}» пропущен: исчерпан лимит вызовов.")
            continue
        result = await client.call_command("spisok_zadach", {"Фильтр": page_filter({"ТипСписка": name})})
        rows = pagination.extract_rows(result, "Документ")
        if pagination.has_more(result):
            notes.append(
                f"Список «{name}»: Saby отдала только {len(rows)} задач, "
                "остальные не показаны — порция кончилась."
            )
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = compact_doc(row)
            key = str(item.get("ид") or f"{name}-{len(unique)}")
            item["возраст_дней"] = _days_ago(row.get("ДатаВремяСоздания"), today)
            if item.get("срок"):
                due = parse_date(item["срок"])
                item["просрочено"] = bool(due and due < today)
            unique[key] = item
        sections[name] = sorted(
            unique.values(),
            key=lambda i: (not i.get("требует_внимания"), -(i.get("возраст_дней") or 0)),
        )

    on_me = sections.get("НаМне", [])
    attention_rows = [i for i in on_me if i.get("требует_внимания")]
    overdue = [i for i in on_me if i.get("просрочено")]
    oldest = max((i.get("возраст_дней") or 0) for i in on_me) if on_me else 0

    return {
        "на_мне": len(on_me),
        "требует_внимания": len(attention_rows),
        "просрочено_по_сроку": len(overdue),
        "самый_старый_дней": oldest,
        "списки": sections,
    }


def _row_key(row: Any) -> tuple:
    """Ключ строки изменений: документ + его события + когда создан."""
    if not isinstance(row, dict):
        return ("", "", ())
    events = row.get("Событие") if isinstance(row.get("Событие"), list) else []
    return (
        str(row.get("Идентификатор") or ""),
        str(row.get("ДатаВремяСоздания") or ""),
        tuple(str(e.get("Идентификатор") or "") for e in events
              if isinstance(e, dict)),
    )


class _BudgetSpent(Exception):
    """Лимит вызовов на сводку исчерпан."""


async def _changes_range(
    client: SabyClient, start: datetime, end: datetime, budget: _Budget,
    notes: list[str],
) -> tuple[list[Any], bool]:
    """Строки `СписокИзменений` за окно — обходом по курсору события.

    Раньше здесь окно делилось пополам рекурсией до 15 минут: считалось, что
    листать нечем. Утверждение было ложным (см. pagination), и деление стоило
    ~20 вызовов на сутки.

    Теперь деление заменено курсором — «Фильтр.ИдентификаторСобытия» плюс
    «ДатаВремяС». Обход общий с `pagination.fetch_all`: заводить рядом второй
    алгоритм после проверенного незачем.

    Почему деление всё равно было плохо. Дно в 15 минут не спасает: за сутки
    16.10 накопилось 860 событий, и в часы пик в 15-минутный кусок влезало
    больше 200. Обход упирался в дно и каждый раз честно, но постоянно,
    объявляя сводку неполной.
    """
    if budget.remaining <= 0:
        notes.append(
            f"Изменения с {fmt_dt(start)}: исчерпан лимит вызовов на сводку — "
            "раздел «что изменилось» собран не полностью."
        )
        return [], False

    result = await pagination.fetch_all(
        client, "spisok_izmeneniy",
        {"Фильтр": {"ДатаВремяС": fmt_dt(start), "ДатаВремяПо": fmt_dt(end)}},
        page_size=PAGE_SIZE, max_items=MAX_EVENTS,
        max_pages=budget.remaining,
    )
    budget.take(result["pages_fetched"])
    notes.extend(result.get("notes") or [])
    complete = not result["truncated"]
    if not complete:
        notes.append(
            f"Изменения с {fmt_dt(start)}: часть событий не показана. Курсор "
            "намеренно не сдвинут — в следующий раз они попадут в сводку снова."
        )
    return result["items"], complete
    """Строки `СписокИзменений` за окно; делением окна пополам — только как фолбэк.

    Эта команда без явной страницы отдаёт 25 строк и `ЕстьЕще = Да`. Раньше
    отсюда делали вывод, что обойти порцию нельзя, и резали окно пополам до
    15 минут рекурсией — это и порождало ~20 вызовов на сутки и неполные
    интервалы в сводке.

    ИСПРАВЛЕНИЕ 15.10.2026: `Навигация.РазмерСтраницы = «200»` внутри `Фильтр`
    отдаёт всю выборку за один вызов. Замерено на боевых данных:

        без навигации        → 25 строк, ЕстьЕще = «Да»
        РазмерСтраницы=«50» → 50 строк, ЕстьЕще = «Да»
        РазмерСтраницы=«200»→ 98 строк, ЕстьЕще = «Нет»   (окно 10:00–11:00)
        сутки 15.10          → 175 событий, ЕстьЕще = «Нет», 1 вызов

    Деление окна оставлено на случай `ЕстьЕще = «Да»` даже при 200 строках —
    такой случай на живых данных не встретился, но код обязан выжить и на
    нём, а не молча отдать неполное.
    """
    minutes = max(1, int((end - start).total_seconds() // 60))
    collected: list[Any] = []

    async def walk(first: int, last: int) -> bool:
        frm = start + timedelta(minutes=first)
        # Полуинтервал: правую границу отдаём на секунду раньше, иначе событие
        # ровно на стыке двух окон попадёт в оба и дважды попадёт в сводку.
        # Последнее окно — это всё окно, и его правая граница точная.
        to = end if last >= minutes else start + timedelta(minutes=last) - timedelta(
            seconds=1)
        if not budget.take():
            raise _BudgetSpent()
        result = await client.call_command("spisok_izmeneniy", {"Фильтр": page_filter({
            "ДатаВремяС": fmt_dt(frm),
            "ДатаВремяПо": fmt_dt(to),
        })})
        rows = pagination.extract_rows(result, "Документ")
        complete = not pagination.has_more(result)
        if complete:
            collected.extend(rows)
            return True
        if last - first <= MIN_SLICE_MINUTES or to <= frm:
            notes.append(
                f"Изменения с {fmt_dt(frm)} по {fmt_dt(to)}: Saby отдала только "
                f"{len(rows)} строк даже при странице в {PAGE_SIZE}, порция "
                "кончилась. Эти данные показаны не полностью."
            )
            collected.extend(rows)
            return False
        middle = first + (last - first) // 2
        left = await walk(first, middle)
        right = await walk(middle, last)
        return left and right

    try:
        complete = await walk(0, minutes)
    except _BudgetSpent:
        notes.append(
            f"Изменения с {fmt_dt(start)}: окно перебрано не полностью — "
            f"исчерпан лимит {budget.limit} вызовов на сводку."
        )
        complete = False

    merged = {_row_key(row): row for row in collected}
    return list(merged.values()), complete


async def changes(
    client: SabyClient, since_dt: datetime, until_dt: datetime, budget: _Budget,
    notes: list[str],
) -> dict[str, Any]:
    """Что изменилось с курсора.

    `СписокИзменений` присылает документ столько раз, сколько по нему событий,
    и живая проверка показала дубли одного и того же документа. Поэтому запись
    сводки — это пара «документ + событие», а событие здесь и есть главное:
    именно в нём лежат время, действие («Переназначить», «Утверждение») и
    исполнитель.
    """
    rows, complete = await _changes_range(client, since_dt, until_dt, budget, notes)

    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    latest = since_dt
    for row in rows:
        base = compact_doc(row)
        base["дата_документа"] = str(row.get("Дата") or "")
        base["дата_создания"] = str(row.get("ДатаВремяСоздания") or "")
        for event in (row.get("Событие") or []):
            if not isinstance(event, dict):
                continue
            moment = str(event.get("ДатаВремя") or "")
            action = str((event.get("Действие") or {}).get("Название") or "")
            key = "|".join([str(base.get("ид")), str(event.get("Идентификатор") or ""),
                            moment, action])
            if key in seen:
                continue
            seen.add(key)
            parsed = parse_dt(moment)
            if parsed and parsed > latest:
                latest = parsed
            events.append({
                "когда": moment,
                "что": str(event.get("Название") or ""),
                "действие": action,
                "исполнитель": _fio(event.get("Исполнитель")),
                "подразделение": str((event.get("Подразделение") or {}).get("Название") or "")
                if isinstance(event.get("Подразделение"), dict) else "",
                "комментарий": str(event.get("Комментарий") or ""),
                "документ": {
                    "ид": base.get("ид"),
                    "название": base.get("название"),
                    "номер": base.get("номер"),
                    "дата": base.get("дата"),
                    "тип": base.get("тип"),
                    "направление": base.get("направление"),
                    "контрагент": base.get("контрагент"),
                    "состояние": base.get("состояние"),
                },
            })
    events.sort(key=lambda e: str(e.get("когда") or ""), reverse=True)

    # Курсор двигаем только по полному ответу: при усечении хвост ещё не виден,
    # и сдвиг вперёд означал бы, что эти изменения потеряны навсегда.
    new_cursor = until_dt if complete else None

    return {
        "всего": len(events),
        "документов": len({str(e["документ"].get("ид")) for e in events}),
        "по_действиям": _tally(events, "действие"),
        "полно": complete,
        "курсор_новый": fmt_dt(new_cursor) if new_cursor else None,
        "события": events,
    }


async def stages(client: SabyClient, budget: _Budget) -> dict[str, Any]:
    """Служебные этапы: сначала счётчик, и только если он больше нуля — список."""
    if not budget.take():
        return {"необработанных": None, "этапы": []}
    result = await client.call_command("informaciya_o_sluzhebnyh_etapah", {})
    counter = str(result.get("ЧислоНеобработанныхЭтапов") or "0") \
        if isinstance(result, dict) else "0"
    pending_count = 0
    try:
        pending_count = int(counter)
    except ValueError:
        pending_count = 0

    items: list[dict[str, Any]] = []
    if pending_count > 0 and budget.take():
        # «Блокировать: Нет» — обязателен именно для чтения. По умолчанию
        # Saby блокирует список служебных этапов на минуту для всех остальных
        # пользователей кабинета, привязываясь к сессии вызывающего (справка
        # stage_doc, комментарий 1). Сводка только смотрит и никто ничего не
        # обрабатывает — значит, блокировать коллег незачем.
        listing = await client.call_command(
            "spisok_sluzhebnyh_etapov",
            {"Фильтр": config.scoped_filter({"Блокировать": "Нет"}, True)})
        items = [compact_doc(r) for r in pagination.extract_rows(listing, "Документ")]
    return {"необработанных": pending_count, "этапы": items}


# --- сборка -----------------------------------------------------------------

def resolve_window(
    days: int, since: str | None, until: str | None, cursor: str | None,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """Границы окна и объяснение, откуда они взялись.

    Приоритет: явный `since` → курсор прошлой сводки → вчерашние сутки.
    `until` по умолчанию — «сейчас»: в Saby это означает «всё до этого
    момента», а не полночь.
    """
    now = now or datetime.now()
    end = parse_dt(until) if until else now
    if end is None:
        raise SabyError(
            f"Не понял дату {until!r}. Жду «ДД.ММ.ГГГГ» или «ДД.ММ.ГГГГ ЧЧ.ММ.СС»."
        )

    if since:
        start = parse_dt(since)
        if start is None:
            start_dt = parse_date(since)
            start = datetime.combine(start_dt, datetime.min.time()) if start_dt else None
        if start is None:
            raise SabyError(f"Не понял дату {since!r}.")
        source = "задана"
    elif cursor:
        start = parse_dt(cursor)
        if start is None:
            raise SabyError(
                f"Курсор в файле состояния битый: {cursor!r}. Ожидалось "
                "«ДД.ММ.ГГГГ ЧЧ.ММ.СС». Сбросить его — saby-mcp digest --reset."
            )
        source = "курсор"
    else:
        days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
        start = datetime.combine(now.date() - timedelta(days=days), datetime.min.time())
        source = "дни"

    if start > end:
        start, end = end, start
    return start, end, source


async def build(
    client: SabyClient,
    *,
    days: int = DEFAULT_DAYS,
    since: str | None = None,
    until: str | None = None,
    cursor: str | None = None,
    with_tasks: bool = True,
    with_changes: bool = True,
    with_stages: bool = True,
    with_links: bool = False,
    include_outgoing: bool = False,
    now: datetime | None = None,
    max_calls: int = MAX_CALLS,
) -> dict[str, Any]:
    """Собрать сводку.

    Ничего не пишет и не меняет: только команды каталога, только чтение.
    Возвращает словарь, который инструмент отдаёт как JSON, а CLI печатает
    полем `сводка` — готовым текстом для человека.

    `max_calls` — предохранитель: окно шире, чем бюджет выдерживает, будет
    обрезано, и об этом появится запись в notes. Проверяется тестами.
    """
    now = now or datetime.now()
    today = now.date()
    start, end, source = resolve_window(days, since, until, cursor, now)
    budget = _Budget(max_calls)
    notes: list[str] = []

    got = await incoming(client, start, end, budget, notes, with_links)

    payload: dict[str, Any] = {
        "ok": True,
        "окно": {
            "с": fmt_dt(start),
            "по": fmt_dt(end),
            "источник": source,
        },
        "пришло": got,
    }

    if with_tasks:
        payload["требует_внимания"] = await pending(
            client, budget, notes, today, include_outgoing=include_outgoing)
    if with_changes:
        payload["изменилось"] = await changes(client, start, end, budget, notes)
    if with_stages:
        payload["служебные_этапы"] = await stages(client, budget)

    payload["вызовы"] = budget.used
    payload["notes"] = notes
    payload["сводка"] = render(payload)
    return payload


def render(payload: dict[str, Any]) -> str:
    """Текст сводки для человека.

    Короткий намеренно: это то, что человек читает поутрам. Подробности —
    в JSON рядом, если понадобятся.
    """
    window = payload.get("окно") or {}
    lines: list[str] = [
        f"Сводка входящих · {fmt_date(parse_date(window.get('по')) or date.today())}"
        f" (с {window.get('с')})",
    ]

    got = payload.get("пришло") or {}
    lines.append("")
    lines.append(f"Пришло событий: {got.get('всего', 0)}"
                 f" (документов: {got.get('документов', 0)})")
    if not got.get("полно", True):
        lines.append(f"  ! неполно за: {', '.join(got.get('неполные_дни') or ['окно'])}")
    for event in (got.get("события") or [])[:20]:
        lines.append(_event_line(event))
    if len(got.get("события") or []) > 20:
        lines.append(f"  … и ещё {len(got['события']) - 20}; полный список в JSON")

    watch = payload.get("требует_внимания")
    if watch:
        lines.append("")
        lines.append(
            f"Требует внимания: {watch.get('требует_внимания', 0)} задач на мне"
            + (f", самый старый — {watch['самый_старый_дней']} дн."
               if watch.get("самый_старый_дней") else "")
        )
        for item in ((watch.get("списки") or {}).get("НаМне") or []):
            if item.get("требует_внимания"):
                lines.append(_doc_line(item))

    diff = payload.get("изменилось")
    if diff is not None:
        lines.append("")
        lines.append(f"Изменилось с прошлого раза: {diff.get('всего', 0)} событий"
                     f" по {diff.get('документов', 0)} документам")
        if not diff.get("полно", True):
            lines.append("  ! список неполный, курсор не сдвинут")
        for event in (diff.get("события") or [])[:10]:
            doc = event.get("документ") or {}
            lines.append(
                f"  {event.get('когда')}  {event.get('действие') or event.get('что')}"
                f" · {doc.get('название') or doc.get('ид')}"
                + (f" → {event['исполнитель']}" if event.get("исполнитель") else "")
            )

    stage_info = payload.get("служебные_этапы")
    if stage_info is not None:
        lines.append("")
        lines.append(f"Необработанных служебных этапов: {stage_info.get('необработанных')}")

    notes = payload.get("notes") or []
    if notes:
        lines.append("")
        lines.append("Замечания к сводке:")
        lines.extend(f"  — {note}" for note in notes)

    lines.append("")
    lines.append(f"Вызовов к Saby: {payload.get('вызовы', 0)}")
    return "\n".join(lines)


def _event_line(event: dict[str, Any]) -> str:
    state = event.get("состояние_события") or event.get("состояние") or ""
    mark = "!" if event.get("требует_внимания") else " "
    return (
        f" {mark} {event.get('время_события', '')}  {event.get('название') or event.get('ид')}"
        f" · {(event.get('контрагент') or {}).get('название', '')}"
        + (f" — {state}" if state else "")
    )


def _doc_line(item: dict[str, Any]) -> str:
    age = item.get("возраст_дней")
    return (
        f"  · {item.get('название') or item.get('ид')}"
        f" — {item.get('состояние') or 'состояние неизвестно'}"
        + (f" ({age} дн. в работе)" if age else "")
        + (f", срок {item['срок']}" if item.get("срок") else "")
    )
