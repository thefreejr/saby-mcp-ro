"""Постраничный обход списков Saby — по тому, что сервер делает на самом деле.

Что выяснено живыми вызовами (перепроверено 15.10.2026, боевая сессия)
------------------------------------------------------------------------
СПРАВКА ВЕРНА, ПРЕДЫДУЩИЙ ВЫВОД БЫЛ ОШИБОЧНЫМ.

Раньше здесь было записано, что объект `Навигация` в запросе Saby не
принимает вообще (якобы отвечает -32601 на `РазмерСтраницы`), и поэтому
обход всех страниц невозможен. Это не так: -32601 приходит на
`Навигация.Позиция.ДатаВремя` — поля, которого нет ни в справке, ни в
контракте. За диагностическую ошибку на этом поле сделали вывод обо всей
`Навигации`.

Живая проверка 15.10.2026 на `СБИС.СписокИзменений` (окно 10:00–11:00):

    без навигации            → 25 строк,  ЕстьЕще = «Да»
    РазмерСтраницы = "50"    → 50 строк,  ЕстьЕще = «Да»
    РазмерСтраницы = "200"   → 98 строк,  ЕстьЕще = «Нет»   ← всё окно за один вызов
    РазмерСтраницы = "201"   → 98 строк,  ЕстьЕще = «Нет»
    РазмерСтраницы = "1000"  → 98 строк,  ЕстьЕще = «Нет»   (молча схлопывается в 200)

1. `Навигация.РазмерСтраницы` РАБОТАЕТ. Потолок — 200: всё, что больше,
   молча срезается до 200; 5000 и выше дают ошибку. Значение принимается и
   строкой, и числом. Указывать надо внутри `Фильтр` — в корне параметров
   объект игнорируется и ответ приходит пустым.

2. Без `Навигации` сервер отдаёт первую страницу сам и сообщает:
       "Навигация": {"ЕстьЕще": "Да", "РазмерСтраницы": "25"}
   Потолок без навигации у разных команд разный (25, 10, 50) и приходит строкой.

3. Документированный курсор `Фильтр.ИдентификаторСобытия` РАБОТАЕТ — при
   условии, что туда кладётся `Событие.Идентификатор` последнего события
   (а не `Документ.Идентификатор`; с идентификатором документа сервер
   отвечает иначе, и это, судя по всему, породило прежний вывод).
   Передаётся вместе с `Фильтр.ДатаВремяС`, повтор последнего события не
   возвращается, пропусков на стыке нет — проверено на 98 событиях.

Итог: ограничения, о которых писал этот модуль, — не ограничения Saby.
Чтобы не потерять события, достаточно просить страницу нужного размера и
уметь дойти курсором. Обход по-прежнему честно отдаёт одну страницу и, если
`ЕстьЕще` = «Да», прямо сообщает об усечении: молча вернуть 25 строк из 300
значило бы сказать человеку «это все документы», а это неправда.
"""
from __future__ import annotations

from typing import Any

from . import catalog
from .client import SabyClient, SabyError

DEFAULT_MAX_ITEMS = 5_000


def dig(obj: Any, dotted: str) -> Any:
    """Пройти по точечному пути вложенных словарей."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def extract_rows(result: Any, result_key: str) -> list[Any]:
    """Строки выдачи из `result`.

    result_key указывает на ключ верхнего уровня (`Документ`, `Реестр`,
    `СписокПриглашений`...). У части команд (`sabyDepartment.Read`,
    `sabyOurCompany.List`) сам result является массивом — тогда result_key
    пустой и берём его как есть.
    """
    if not result_key:
        return result if isinstance(result, list) else []
    found = dig(result, result_key)
    if isinstance(found, list):
        return found
    if found is None:
        # Ключ не совпал. Берём первый массив, который найдётся: строки лучше,
        # чем ничего, а несовпадение ключа видно в самом ответе.
        for value in _first_list(result):
            return value
        return []
    return []


def _first_list(obj: Any, depth: int = 0) -> list[list[Any]]:
    if depth > 3 or not isinstance(obj, dict):
        return []
    for value in obj.values():
        if isinstance(value, list):
            return [value]
        if isinstance(value, dict):
            found = _first_list(value, depth + 1)
            if found:
                return found
    return []


def server_navigation(result: Any) -> dict[str, Any]:
    """Навигация, которую сервер вернул в ответе."""
    nav = dig(result, "Навигация")
    return nav if isinstance(nav, dict) else {}


def has_more(result: Any) -> bool:
    """Сервер говорит, что есть ещё строки.

    Значение приходит строкой «Да»/«Нет», но на всякий случай понимаем и
    логическое True: контракт меняется между версиями.
    """
    value = server_navigation(result).get("ЕстьЕще")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("да", "yes", "true", "1")
    return False


# Потолок Saby. Всё, что больше, молча срезается до него; 5000 и выше дают
# ошибку. Проверено на `СписокИзменений` 15.10.2026: 50 → 50 строк,
# 200 → вся выборка с ЕстьЕще = «Нет», 1000 → молча те же 200.
PAGE_SIZE = "200"
# Корневой объект параметров: у команд он называется по-разному («Фильтр»
# почти у всех), и его задаёт каталог. Навигация должна лежать ВНУТРИ него.
FILTER_ROOTS = ("Фильтр", "Параметр", "Отбор")


def _with_page_size(params: dict[str, Any],
                    page_size: int | str | None = None) -> dict[str, Any]:
    """Вложить запрошенный размер страницы в корневой объект фильтра.

    Копия, а не мутация: объект фильтра у команды может быть общим.

    Если корневого объекта нет — команда его и не принимает, и `with_root`
    отдала пустые параметры. Добавлять «Фильтр» от себя нельзя: это ломает
    контракт, который проверяет test_command_without_root_stays_empty.
    """
    size = str(page_size) if page_size else PAGE_SIZE
    try:
        wanted = int(size)
    except (TypeError, ValueError):
        return params
    if wanted > 200:  # Saby молча срезает, но лучше не просить заведомо недостижимое
        size = "200"
    nav = {"Навигация": {"РазмерСтраницы": size}}
    for root in FILTER_ROOTS:
        inner = params.get(root)
        if isinstance(inner, dict):
            return {**params, root: {**inner, **nav}}
    return params


async def fetch_all(
    client: SabyClient,
    command_id: str,
    params: dict[str, Any] | None = None,
    page_size: int | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> dict[str, Any]:
    """Получить строки команды.

    Страница запрашивается явно: `Навигация.РазмерСтраницы` внутри объекта
    фильтра поднимает порцию с 25 до потолка Saby (200). Без этого запроса
    список обрывался на 25 строках с «ЕстьЕще = Да».

    Дальше первой страницы обход пока не идёт — заготовка `_advance` ждёт
    курсора, который клиент умеет подставить. Возвращает {"ok", "items",
    "total_fetched", "truncated", "notes"}.
    """
    cmd = catalog.get(command_id)
    if cmd is None:
        raise SabyError(f"Не знаю команду {command_id!r}.")

    base = catalog.with_root(cmd, params)
    base = _with_page_size(base, page_size)

    result = await client.call_command(cmd.id, base)
    rows = extract_rows(result, cmd.result_key)

    notes: list[str] = []
    nav = server_navigation(result)
    truncated = has_more(result)

    if len(rows) > max_items:
        rows = rows[:max_items]
        truncated = True
        notes.append(
            f"Возвращено больше лимита {max_items} строк, лишние отброшены."
        )
    if truncated:
        size = nav.get("РазмерСтраницы")
        notes.append(
            "Saby отдала не всё: в ответе Навигация.ЕстьЕще = «Да»"
            + (f", порция {size} строк" if size else "")
            + ". Запрошена максимальная страница (потолок Saby — 200 строк), "
            "дальше нужен курсор. Сузите период или тип документов."
        )

    out: dict[str, Any] = {
        "ok": True,
        "command": cmd.command,
        "items": rows,
        "total_fetched": len(rows),
        "pages_fetched": 1,
        "truncated": truncated,
        "server_navigation": nav or None,
    }
    if notes:
        out["notes"] = notes
    return out


def _advance(cmd: catalog.Command, result: Any, rows: list[Any]) -> Any:
    """Курсор следующей страницы.

    Заглушка. Должна вернуть курсор, который клиент умеет подставить в запрос,
    либо None, если продолжить нечем.

    Что уже проверено живьём и НЕ подходит:
    • `Навигация.Позиция.ДатаВремя` — такого поля нет, Saby отвечает -32601;
    • `Навигация.Страница`, `Навигация.НачальнаяПозиция` — молча игнорируются;
    • `Фильтр.ИдентификаторСобытия` с идентификатором ДОКУМЕНТА.

    Что подходит и ждёт реализации: `Фильтр.ИдентификаторСобытия` (именно
    события, не документа) вместе с `Фильтр.ДатаВремяС` — на 98 событиях
    повтора не было и пропусков не вылезло. Плюс у реестра в ответе есть
    `Навигация.СледующаяПозиция` с `StartPosition` и `пкСобытие`.
    """
    return None
