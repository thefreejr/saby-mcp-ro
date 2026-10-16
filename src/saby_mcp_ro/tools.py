"""Инструменты MCP.

Их девять, и это осознанный выбор. В изученном сервере их было 18, из них
половина — инструменты управления кавинетами, а три (`get_raw`, `write_raw`,
`delete_raw`) давали агенту прямой доступ к произвольному пути на домене API.
Для сервера только на чтение такой доступ не нужен, а вот лишняя поверхность —
да.

Два из девяти — `saby_digest` и `saby_digest_cursor` — собирают сводку
входящих из тех же команд каталога, но дедуплицируют, выбрасывают тяжёлые
ссылки и обходят порцию в 25 строк по суткам. Это чтение, как и всё
остальное: курсор живёт в локальном файле, а не в Saby.

Ключевое отличие: здесь НЕТ инструмента, который принимает адрес. Ни параметра
`url`, ни `host`, ни `path`, ни `method`. Обратиться куда попало нечем. Сырых
инструментов (`get_raw` / `write_raw` / `delete_raw`) в проекте нет.

Отдельный случай — вход. Он идёт из терминала командой `saby-mcp login`,
а не инструментом: пароль не должен попадать в переписку с ассистентом.
Логин ходит на /auth/service/ без заголовка сессии, потому что так устроен
сам Saby — сессию выдаёт только вход.
"""
from __future__ import annotations

import json
from typing import Any

from . import catalog, digest, doctypes, pagination, search
from .client import SabyClient, SabyError
from .credentials import CredentialStore
from .digest_state import FILENAME as DIGEST_STATE_FILE, DigestState


def _matches(t, query: str) -> bool:
    hay = " ".join((t.code, t.title, " ".join(t.synonyms))).lower()
    return query.lower() in hay or query.lower().replace("ё", "е") in hay

def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _error(exc: Exception) -> str:
    return _j({"ok": False, "error": type(exc).__name__, "message": str(exc)})


PAGINATION_LABELS = {
    "page": ("листается «Навигация.Страница» (номер страницы с нуля)"),
    "position": ("листается «Навигация.Позиция.ДатаВремя» из "
                 "«Навигация.СледующаяПозиция.ДатаВремя»"),
    "event_cursor": ("упрощённый вариант «Навигации», поля «Страница» нет; "
                     "листает «Фильтр.ИдентификаторСобытия» + «ДатаВремяС»"),
    "invitation_cursor": ("«Фильтр.ИдентификаторПриглашения» — сервер поле "
                          "принимает, но выдачу не двигает"),
    "none": ("«ЕстьЕще» возможен, но способа листать нет: saby_fetch_all "
             "отдаст одну страницу и пометит результат truncated"),
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
        "pagination_mode": PAGINATION_LABELS.get(cmd.pagination_mode,
                                              cmd.pagination_mode),
        "params": cmd.params_shape or "у команды нет параметров",
        "required": list(cmd.required_params) or "нет обязательных",
        "result_key": cmd.result_key or "result сам является массивом",
        "verified_on_live_session": cmd.verified,
        "doc": cmd.doc,
    }


def register(mcp, client: SabyClient, store: CredentialStore) -> None:
    """Навесить инструменты на экземпляр MCPServer."""
    # Файл курсора — рядом с файлом сессии, в том же каталоге, который
    # перекрывает SABY_MCP_HOME. Именно файл: путь целиком, а не его папка.
    digest_state = DigestState(store.path.parent / DIGEST_STATE_FILE)

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

        Ограничение прежнее было ложным: «Навигация.РазмерСтраницы» внутри
        корневого объекта фильтра работает и поднимает порцию с 25 строк до
        потолка Saby (200) — проверено на боевой сессии 15.10.2026.

        Страницы обходятся самостоятельно, способ зависит от команды:
        у большинства списочных это «Навигация.Страница», у «СБИС.СписокЗадач»
        — «Навигация.Позиция.ДатаВремя», а у «СБИС.СписокИзменений» —
        упрощённый вариант «Навигации» без поля «Страница», там работает курсор
        по «Фильтр.ИдентификаторСобытия». Потолок страницы у каждой команды свой
        (200 у реестра, 50 у задач, 20 у служебных этапов) и учитывается.
        У «СписокИзмененийПриглашений» способа листать нет.

        Args:
            command_id: id команды из saby_search.
            params: параметры ровно те, что в карточке (saby_describe).
            page_size: размер страницы. По умолчанию берётся потолок команды;
                всё, что выше потолка, срезается до него.
            max_items: предохранитель, столько строк максимум (по умолчанию 5000).
        Returns JSON: {"ok", "items", "total_fetched", "pages_fetched",
            "truncated", "server_navigation"}. Если выдача усечена, причина в
            "notes" — не молча, а текстом.
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

    @mcp.tool(
        name="saby_digest",
        annotations={"title": "Сводка входящих документов", "readOnlyHint": True,
                     "openWorldHint": True},
    )
    async def saby_digest(
        days: int = digest.DEFAULT_DAYS,
        since: str = "",
        until: str = "",
        use_cursor: bool = True,
        with_tasks: bool = True,
        with_changes: bool = True,
        with_stages: bool = True,
        with_links: bool = False,
        include_outgoing: bool = False,
        save_cursor: bool = False,
    ) -> str:
        """Сводка входящих: что пришло, что висит на мне, что изменилось.

        Начните с неё, когда человек спрашивает «что входящего», «что нового»,
        «сводка за вчера», «что изменилось с утра» или «что я пропустил».
        Набор команд под капотом — тот же, что дают остальные инструменты,
        но собранный так, чтобы это стоило человеку одного вызова вместо
        двадцати.

        Что она делает и почему так:
        • обходит реестр «Входящие» ПО СУТКАМ: его фильтр понимает даты,
          а не время, поэтому сутки — единственная честная единица обхода.
          Страница внутри «Фильтр» запрашивается явно (Навигация.
          РазмерСтраницы, потолок 200), так что сутки помещаются в один вызов;
        • выбрасывает из ответа ссылки на PDF и архив: 25 событий весят
          209 КБ, из которых нужны две сотни символов на документ;
        • убирает дубли: «На мне» отдаёт 9 строк на 8 документов, изменения
          присылают один документ дважды;
        • НЕ фильтрует задачи по сотруднику: этот фильтр Saby молча
          игнорирует и возвращает чужие задачи тоже;
        • окно изменений делится пополам до 15 минут ТОЛЬКО если событий
          больше 200 за один запрос. На боевых данных 15.10.2026 до этого
          не дошло ни разу.

        Ежедневный запуск — 4..5 вызовов к Saby и около двух секунд.

        Args:
            days: сколько последних суток смотреть, если не задано иначе
                (по умолчанию 1, максимум 31). Работает только без курсора
                и без `since`. ВНИМАНИЕ: days=1 — это вчера 00:00 → сейчас,
                то есть двое суток; для «за сегодня» передавайте `since`.
            since: начало окна, «ДД.ММ.ГГГГ» или «ДД.ММ.ГГГГ ЧЧ.ММ.СС».
                Время — ТОЧКАМИ между часами, минутами и секундами: с
                двоеточиями SabyError «Не понял дату». Перекрывает и days,
                и курсор.
            until: конец окна. Пусто — «сейчас», а не полночь.
            use_cursor: брать начало окна из прошлого запуска (по умолчанию да).
                Работает, только если `since` не задан.
            with_tasks: раздел «требует внимания» из списка задач «На мне».
            with_changes: раздел «изменилось с прошлого раза».
            with_stages: счётчик необработанных служебных этапов.
            with_links: добавить ссылку в кабинет к каждому документу.
                По умолчанию выключено: в сводке она только мешает.
            include_outgoing: добавить список «От меня» к «На мне»
                (плюс один вызов к Saby).
            save_cursor: запомнить конец окна как новый курсор, чтобы в
                следующий раз взять изменения ровно с этого места. Курсор
                НЕ двигается, если выдача оказалась неполной.
        Returns JSON: {"ok", "окно", "пришло", "требует_внимания", "изменилось",
            "служебные_этапы", "вызовы", "notes", "сводка"}. ВНИМАНИЕ: поле
            "сводка" обрезано до 20 событий и 10 изменений — как готовый
            ответ человеку не годится, разбирайте JSON по ключам. "notes"
            пуст при полной выдаче; если он не пуст — усечение, об этом
            надо сказать человеку прямо.
        """
        try:
            cursor = digest_state.cursor() if use_cursor else None
            payload = await digest.build(
                client,
                days=days,
                since=since or None,
                until=until or None,
                cursor=cursor,
                with_tasks=with_tasks,
                with_changes=with_changes,
                with_stages=with_stages,
                with_links=with_links,
                include_outgoing=include_outgoing,
            )
            changed = payload.get("изменилось") or {}
            payload["курсор"] = {
                "использован": cursor,
                "новый": changed.get("курсор_новый"),
                "сохранён": False,
            }
            if save_cursor:
                new_cursor = changed.get("курсор_новый")
                if new_cursor:
                    digest_state.save_cursor(
                        new_cursor,
                        until=payload["окно"]["по"],
                        calls=payload.get("вызовы", 0),
                        complete=bool(changed.get("полно", True)),
                    )
                    payload["курсор"]["сохранён"] = True
                else:
                    payload["курсор"]["причина"] = (
                        "Курсор не сдвинут: либо изменения не запрашивались "
                        "(with_changes=false), либо выдача неполная."
                    )
            return _j(payload)
        except SabyError as exc:
            return _error(exc)
        except Exception as exc:  # noqa: BLE001 — инструмент не должен ронять сервер
            return _error(exc)

    @mcp.tool(
        name="saby_digest_cursor",
        annotations={"title": "Курсор сводки", "readOnlyHint": True,
                     "openWorldHint": False},
    )
    async def saby_digest_cursor(reset: bool = False) -> str:
        """Где остановилась прошлая сводка: время и файл.

        Нужен, когда человек спрашивает «с какого момента мы смотрим» или
        «почему в сводке пусто», и когда после смены кабинета старый курсор
        мешает начать заново.

        Args:
            reset: сбросить курсор, чтобы следующая сводка пошла с текущего
                момента. Ничего, кроме локального файла, при этом не меняется.
        Returns JSON: {"path", "exists", "курсор", "обновлён", "версия",
            "сброшен": bool}.
        """
        try:
            info = digest_state.describe()
            info["сброшен"] = reset and digest_state.clear()
            return _j(info)
        except Exception as exc:  # noqa: BLE001
            return _error(exc)
