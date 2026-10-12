"""Постраничный обход списков Saby — по тому, что сервер делает на самом деле.

Что выяснено живыми вызовами (сентябрь-октябрь 2026, боевая сессия)
------------------------------------------------------------------
1. Объект `Навигация` в ЗАПРОСЕ Saby не принимает вообще: отвечает -32601
   «метод не найден» и на `РазмерСтраницы`, и на `Позиция.ДатаВремя`, и даже
   на пустой `{}`. Справка при этом описывает `Навигацию` как способ навигации
   — расхождение между документацией и контуром.

2. Без `Навигации` сервер отдаёт первую страницу сам и сообщает в ответе:
       "Навигация": {"ЕстьЕще": "Да", "РазмерСтраницы": "25"}
   Потолок у разных команд разный (25, 10, 50) и приходит строкой.

3. Продвинуть курсор документированным способом не удалось:
   - `Навигация.Позиция.ДатаВремя` — -32601;
   - `Фильтр.ИдентификаторСобытия` со значением из последней строки — -32000
     (сервер ждёт другой формат идентификатора события).

Поэтому обход здесь честно отдаёт одну страницу и, если `ЕстьЕще` = «Да»,
прямо сообщает об усечении. Молча вернуть 25 строк из 300 — значило бы
сказать человеку «это все документы», а это неправда.
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


async def fetch_all(
    client: SabyClient,
    command_id: str,
    params: dict[str, Any] | None = None,
    page_size: int | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> dict[str, Any]:
    """Получить строки команды.

    Сейчас это одна страница плюс честное признание, если сервер отдавал
    ещё. Страницу увеличить нечем: см. докстринг модуля. Когда у Saby появится
    документированный и работающий способ — сюда добавится обход; заготовка
    `_advance` уже разделяет, что именно нужно команде.

    Возвращает {"ok", "items", "total_fetched", "truncated", "notes"}.
    """
    cmd = catalog.get(command_id)
    if cmd is None:
        raise SabyError(f"Не знаю команду {command_id!r}.")

    base = catalog.with_root(cmd, params)
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
            + ". Увеличьте выдачу не получится — Saby не принимает объект "
            "Навигация в запросе (проверено: отвечает «метод не найден»). "
            "Сузите период или тип документов."
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
    либо None, если продолжить нечем. Проверенные на живом контуре способы
    (Навигация.Позиция.ДатаВремя, Фильтр.ИдентификаторСобытия) сервером
    отклоняются, поэтому курсора пока нет — и fetch_all честно об этом говорит.
    """
    return None
