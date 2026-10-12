"""Инструменты MCP.

Их пять, и это осознанный выбор. В изученном сервере их было 18, из них
половина — инструменты управления кабинетами, а три (`get_raw`,
`write_raw`, `delete_raw`) давали агенту прямой доступ к произвольному пути
на домене API. Для сервера только на чтение такой доступ не нужен, а вот
лишняя поверхность — да.

Ключевое отличие: здесь НЕТ инструмента, который принимает адрес. Любой вызов
адресуется именем команды из каталога, а адрес подставляет сервер из той же
записи. Команды записи и удаления в каталоге нет, поэтому вызвать их нечем —
не «нельзя без подтверждения», а физически: исполнителя не существует.

Отдельный случай — вход. Он идёт из терминала командой `saby-mcp login`,
а не инструментом: пароль не должен попадать в переписку с ассистентом.
Логин ходит на /auth/service/ без заголовка сессии, потому что так устроен
сам Saby — сессию выдаёт только вход.
"""
from __future__ import annotations

import json
from typing import Any

from . import catalog, doctypes, pagination, search
from .client import SabyClient, SabyError
from .credentials import CredentialStore


def _matches(t, query: str) -> bool:
    hay = " ".join((t.code, t.title, " ".join(t.synonyms))).lower()
    return query.lower() in hay or query.lower().replace("ё", "е") in hay

def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _error(exc: Exception) -> str:
    return _j({"ok": False, "error": type(exc).__name__, "message": str(exc)})


PAGINATION_LABELS = {
    "page": "сервер отдаёт порциями, см. notes",
    "none": "нет, одна страница",
}


def _card(cmd: catalog.Command) -> dict[str, Any]:
    """Карточка команды: что это, какие параметры, где смотреть подробнее."""
    return {
        "id": cmd.id,
        "command": cmd.command,
        "section": catalog.SECTION_TITLES[cmd.section],
        "summary": cmd.summary,
        "address": f"https://{catalog.HOST}{cmd.path}",
        "needs_session": cmd.needs_session,
        "params_root": cmd.params_root or "не требуется",
        "paginated": cmd.paginated,
        "params": cmd.params_shape or "у команды нет параметров",
        "required": list(cmd.required_params) or "нет обязательных",
        "result_key": cmd.result_key or "result сам является массивом",
        "verified_on_live_session": cmd.verified,
        "doc": cmd.doc,
    }


def register(mcp, client: SabyClient, store: CredentialStore) -> None:
    """Навесить инструменты на экземпляр FastMCP."""

    @mcp.tool(
        name="saby_search",
        annotations={"title": "Найти команду Saby", "readOnlyHint": True,
                     "openWorldHint": False},
    )
    async def saby_search(query: str, limit: int = 8) -> str:
        """Найти команду API Saby по словам на русском или английском.

        Начните с неё: опишите задачу словами — «список входящих документов»,
        «статус сертификатов», «договор по ИНН» — и получите подходящие команды.

        Args:
            query: свободный текст, по-русски или по-английски.
            limit: сколько команд вернуть (1..20, по умолчанию 8).
        Returns JSON: {"query", "results": [...]}. В каждой команде есть
            id (его принимают остальные инструменты) и command.
        """
        limit = max(1, min(20, limit))
        found = search.search(query, limit=limit)
        if not found:
            words = search.tokens(query)
            near = search.suggest(words[-1]) if words else []
            hint = f" Похожие слова в каталоге: {', '.join(near)}." if near else ""
            return _j({
                "query": query,
                "results": [],
                "message": (
                    "Ничего не нашлось. Сервер умеет только чтение, поэтому "
                    "команд записи и удаления здесь нет и не будет." + hint
                ),
            })
        return _j({
            "query": query,
            "count": len(found),
            "results": [c.to_summary() for c in found],
            "next": "saby_describe(id) — полная карточка команды.",
        })

    @mcp.tool(
        name="saby_describe",
        annotations={"title": "Карточка команды", "readOnlyHint": True,
                     "openWorldHint": False},
    )
    async def saby_describe(command_id: str) -> str:
        """Полная карточка команды: адрес, параметры, форма результата, ссылка.

        Вызывайте перед saby_call, чтобы не гадать с именами параметров:
        у Saby они кириллические и вложенные.

        Args:
            command_id: id из saby_search либо полное имя команды
                («СБИС.СписокДокументов»).
        Returns JSON с ключами params, required, result_key, doc.
        """
        cmd = catalog.get(command_id)
        if cmd is None:
            return _error(SabyError(
                f"Не знаю команду {command_id!r}. "
                f"Доступные: {', '.join(sorted(catalog.BY_ID))}."
            ))
        return _j(_card(cmd))

    @mcp.tool(
        name="saby_call",
        annotations={"title": "Вызвать команду чтения", "readOnlyHint": True,
                     "openWorldHint": True},
    )
    async def saby_call(command_id: str, params: dict[str, Any] | None = None) -> str:
        """Выполнить одну команду чтения.

        Имя команды и её адрес определяются по command_id на стороне сервера.
        Вам передаётся только содержимое params — конверт JSON-RPC сервер
        собирает сам, так что имя команды в тело класть не нужно.

        Сервер только читает: команды, которые меняют данные, в каталоге нет.

        Args:
            command_id: id из saby_search либо полное имя команды.
            params: параметры ровно те, что перечислены в карточке команды
                (saby_describe). Например для СБИС.СписокДокументов:
                {"Фильтр": {"Тип": "Счет", "ДатаС": "01.01.2026",
                            "ДатаПо": "31.01.2026"}}
        Returns JSON: {"ok": true, "command": ..., "result": ...} либо
            {"ok": false, "message": ...} с текстом, который можно показать
            человеку.
        """
        try:
            cmd = catalog.get(command_id)
            if cmd is None:
                raise SabyError(
                    f"Не знаю команду {command_id!r}. "
                    f"Доступные: {', '.join(sorted(catalog.BY_ID))}."
                )
            result = await client.call_command(cmd.id, params or {})
            return _j({"ok": True, "command": cmd.command, "result": result})
        except SabyError as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001 — инструмент не должен ронять сервер
            return _error(exc)

    @mcp.tool(
        name="saby_fetch_all",
        annotations={"title": "Обойти все страницы", "readOnlyHint": True,
                     "openWorldHint": True},
    )
    async def saby_fetch_all(
        command_id: str,
        params: dict[str, Any] | None = None,
        page_size: int = 0,
        max_items: int = pagination.DEFAULT_MAX_ITEMS,
    ) -> str:
        """Получить строки списка.

        Ограничение подтверждено на живом контуре: Saby отдаёт данные порциями
        и сообщает «ЕстьЕще», но объект Навигация в запросе не принимает —
        отвечает «метод не найден». Поэтому saby_fetch_all возвращает одну
        порцию и, если сервер сказал «ЕстьЕще = Да», честно пишет об усечении
        в notes, вместо того чтобы выдать усечённый список за полный.

        Args:
            command_id: id команды из saby_search.
            params: параметры ровно те, что в карточке (saby_describe).
            page_size: оставлен для совместимости, серверу не передаётся.
            max_items: предохранитель, столько строк максимум (по умолчанию 5000).
        Returns JSON: {"ok", "items", "total_fetched", "truncated",
            "server_navigation"}. Если выдача усечена, причина в "notes".
        """
        try:
            max_items = max(1, max_items)
            out = await pagination.fetch_all(
                client, command_id, params or {}, max_items=max_items,
            )
            return _j(out)
        except SabyError as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001
            return _error(exc)

    @mcp.tool(
        name="saby_whoami",
        annotations={"title": "Кем вы вошли", "readOnlyHint": True,
                     "openWorldHint": True},
    )
    async def saby_whoami() -> str:
        """Проверить сессию и узнать, кто вошёл.

        Дёшево и безопасно: одна команда чтения. Если здесь ошибка про сессию —
        она истекла, войдите заново командой `saby-mcp login` в терминале.

        Returns JSON: {"ok", "session_present", "user": {...}} либо текст
            ошибки Saby.
        """
        try:
            if not client.session_id:
                return _j({
                    "ok": False,
                    "session_present": False,
                    "message": (
                        "Сессии нет. Вход идёт из терминала, а не через "
                        "инструмент: команда `saby-mcp login` в кабинете Saby "
                        "спросит логин и пароль и сохранит идентификатор "
                        "сессии. Пароль в чат не передаётся и не сохраняется. "
                        "Если сессия уже есть, задайте её переменной "
                        "SBIS_SESSION_ID и перезапустите opencode."
                    ),
                })
            result = await client.call_command("informaciya_o_polzovatele")
            user = result
            if isinstance(result, dict):
                user = result.get("Пользователь", result)
            return _j({"ok": True, "session_present": True, "user": user})
        except SabyError as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001
            return _error(exc)

    @mcp.tool(
        name="saby_doc_types",
        annotations={"title": "Справочник типов документов", "readOnlyHint": True,
                     "openWorldHint": False},
    )
    async def saby_doc_types(query: str = "", category: str = "") -> str:
        """Типы документов, которые понимает поле Фильтр.Тип.

        Нужен перед saby_call: Saby принимает не название («счёт»), а внутренний
        код («СчетИсх»), и на русском имени отвечает «неизвестный тип
        документа». Здесь перевод с человеческого на машинный.

        Args:
            query: что ищем, по-русски: «счёт», «накладная», «фактура»,
                «приказ», «командировка». Пусто — вернём популярные.
            category: группа: реализация, закупки, кадры, логистика, касса,
                производство, возвраты, корректировки, цены, расхождения,
                корреспонденция, договоры, заказы, склад, прочие.
        Returns JSON: {"results": [{code, title, directions, verified}]}.
        """
        try:
            if category:
                found = [t for t in doctypes.CATEGORIES.get(
                    category.lower(), ()) if not query
                    or _matches(t, query)]
            else:
                found = doctypes.search(query, limit=25) if query \
                    else list(doctypes.VERIFIED[:25])
            return _j({
                "query": query,
                "found": len(found),
                "total_in_reference": len(doctypes.ALL_TYPES),
                "verified_on_live_session": len(doctypes.VERIFIED),
                "note": (
                    "code — это то, что кладётся в Фильтр.Тип. Название "
                    "по-русски Saby не принимает. Помеченные verified прошли "
                    "настоящий вызов на вашей учётной записи."
                ),
                "results": [
                    {
                        "code": t.code,
                        "title": t.title,
                        "directions": list(t.directions),
                        "category": t.category,
                        "verified": t.verified,
                    }
                    for t in found
                ],
                "categories": sorted(doctypes.CATEGORIES),
            })
        except Exception as exc:  # noqa: BLE001
            return _error(exc)

    @mcp.tool(
        name="saby_catalog",
        annotations={"title": "Карта каталога", "readOnlyHint": True,
                     "openWorldHint": False},
    )
    async def saby_catalog() -> str:
        """Что этот сервер умеет: разделы и команды в каждом.

        Вызывайте без аргументов, когда нужно сориентироваться. Содержит
        только команды чтения — записи и удаления здесь нет по построению.

        Returns JSON: {"commands": N, "sections": [{"section", "title",
            "commands": [{id, command, summary}]}]}.
        """
        sections = []
        for key, cmds in catalog.SECTIONS.items():
            sections.append({
                "section": key,
                "title": catalog.SECTION_TITLES[key],
                "count": len(cmds),
                "commands": [c.to_summary() for c in cmds],
            })
        return _j({
            "commands": len(catalog.ALL_COMMANDS),
            "read_only": True,
            "note": (
                "Сервер выполняет только чтение. Команды записи и удаления "
                "в каталоге отсутствуют, поэтому вызвать их нечем."
            ),
            "sections": sections,
        })
