"""MCP-сервер для чтения данных Saby (СБИС).

Отличие от существующих серверов для этого API: здесь нет ни одной команды,
которая меняет данные. Не «спросит подтверждение», а не существует — записи
и удаления нет в каталоге (`saby_mcp_ro/catalog.py`), а единственный
инструмент, который ходит на провод, адресует только каталог.

Запуск как MCP-сервер:
    SBIS_SESSION_ID=... saby-mcp

Вход по логину (без переменных окружения):
    saby-mcp login
    saby-mcp doctor

Сводка входящих из терминала (то же, что делает инструмент saby_digest):
    saby-mcp digest
    saby-mcp digest --days 7 --json
    saby-mcp digest --since 01.10.2026 --until 12.10.2026
    saby-mcp digest --save          # сдвинуть курсор на конец окна
    saby-mcp digest --reset         # забыть курсор
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__, catalog, digest, tools
from .client import SabyClient, SabyError
from .credentials import CredentialStore
from .digest_state import FILENAME as DIGEST_STATE_FILE, DigestState

SERVER_NAME = "saby-mcp-ro"
DOCS = catalog.DOCS


def build() -> tuple[MCPServer, SabyClient, CredentialStore]:
    """Собрать сервер. Отделено от main, чтобы тесты поднимали его без argv."""
    store = CredentialStore()
    client = SabyClient(session_id=store.load_session())
    # instructions — то, что ассистент читает один раз при подключении.
    # Место для главного: что сервер умеет и чего не умеет никогда.
    mcp = MCPServer(
        SERVER_NAME,
        version=__version__,
        instructions=(
            "Чтение данных Saby (СБИС): документы, этапы, задачи, контрагенты, "
            "подразделения, организации, сертификаты, МЧД.\n\n"
            "Начинайте с saby_search — опишите задачу словами, он вернёт "
            "подходящие команды. Перед вызовом загляните в saby_describe: "
            "имена параметров кириллические и вложенные, а корневой объект "
            "сервер подставит сам, если вы его не передали.\n\n"
            "ПРО ТИПЫ ДОКУМЕНТОВ. Фильтр.Тип принимает не название, а внутренний "
            "код: «Счет» и «Счет на оплату» Saby отвергает, работает «СчетИсх». "
            "Перевод делает saby_doc_types — спросите его перед выборкой.\n\n"
            "ПРО ПАГИНАЦИЮ. Объект «Навигация» работает, но у каждой команды свой "
            "вариант — смотрите pagination_mode в saby_describe.\n"
            "  • Реестр, список документов, наши организации: «Навигация."
            "Страница» (номер страницы с нуля), страница до 200; служебные "
            "этапы — до 20, подразделения — до 500.\n"
            "  • «СБИС.СписокЗадач»: не номер страницы, а «Навигация."
            "Позиция.ДатаВремя» из «Навигация.СледующаяПозиция.ДатаВремя»; "
            "страница до 50.\n"
            "  • «СБИС.СписокИзменений»: поля «Страница» нет, страница до 50, "
            "листает «Фильтр.ИдентификаторСобытия» (именно «Событие."
            "Идентификатор», не «Документ.Идентификатор»).\n"
            "  • «СБИС.СписокИзмененийПриглашений» отдаёт одну страницу — "
            "работающего курсора нет, помечен «none».\n"
            "«Навигация» должна быть ВНУТРИ корневого объекта; в корне "
            "параметров она игнорируется. Корневой объект не всегда один "
            "уровень: у «СписокПодразделений» это «Параметр», а «Фильтр» с "
            "обязательной «НашаОрганизацией» и навигацией лежит ВНУТРИ него. "
            "Вкладывать это руками не нужно — сервер сам кладёт и «Фильтр», и "
            "«Навигацию» по нужной команде. Единственная команда совсем без "
            "корневого объекта — «СписокАккаунтов», навигацию она не "
            "принимает.\n"
            "saby_fetch_all обходит страницы сам, но ответ урезается до 5 МБ: "
            "сырые документы за неделю занимают 18 МБ, и такой объём убивает "
            "транспорт. Если вернулся truncated — читайте notes: там сказано, "
            "сузить период или взять saby_digest. Не выдавайте усечённый "
            "список за полный.\n\n"
            "ПРО СВОДКУ. На вопросы «что входящего», «что нового», «сводка за "
            "вчера», «что изменилось» отвечайте saby_digest, а не пятью "
            "вызовами вручную: он убирает ссылки на PDF ради объёма, "
            "устраняет дубли, которых Saby отдаёт больше одного на документ, "
            "и сам следит за полнотой. Замеры: тихий день — 4 вызова к Saby, "
            "день активной работы (16.10, 1498 изменений) — 34: порция там "
            "по справке 50 строк, и добирает их курсор. Поле «сводка» обрезано "
            "(20 событий, 10 изменений) и как ответ человеку не годится — "
            "разбирайте JSON по ключам.\n\n"
            "ВНИМАНИЕ: этот сервер только читает. Команды записи и удаления "
            "в нём отсутствуют, вызвать их нечем. Если задача требует "
            "изменения в Saby — скажите об этом человеку, а не пытайтесь "
            "подобрать способ."
        ),
    )
    tools.register(mcp, client, store)
    return mcp, client, store


def _login_interactive(client: SabyClient, store: CredentialStore) -> int:
    """Вход по логину и паролю из терминала.

    Пароль читается через getpass, чтобы не остаться в истории терминала.
    Он не печатается, не логируется и не попадает в файл: в файле лежит
    только выданный Saby идентификатор сессии.
    """
    import getpass

    try:
        login = input("Логин Saby: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nОтменено.", file=sys.stderr)
        return 1
    if not login:
        print("Пустой логин.", file=sys.stderr)
        return 1
    password = getpass.getpass("Пароль: ")
    account = ""
    if sys.stdin.isatty():
        account = input("Номер аккаунта (если кабинетов несколько, иначе Enter): ").strip()

    async def run() -> str:
        return await client.login(login, password, account or None)

    try:
        session = asyncio.run(run())
    except SabyError as exc:
        print(f"Не получилось: {exc}", file=sys.stderr)
        return 1
    store.save_session(session, login)
    print(f"Готово. Сессия сохранена в {store.path}")
    print("Пароль нигде не сохранён — только идентификатор сессии.")
    return 0


def _doctor(client: SabyClient, store: CredentialStore, live: bool) -> int:
    """Проверка установки.

    Секреты не печатаются: видно только, есть ли сессия и откуда она.
    С `--live` делается один реальный вызов чтения — это доказывает, что
    сессия рабочая, а не просто присутствует.
    """
    health = catalog.health()
    info = store.describe()
    lines = [
        f"{SERVER_NAME} doctor",
        "",
        f"команд в каталоге: {health['commands']} (только чтение)",
    ]
    for name, count in health["sections"].items():
        lines.append(f"  {catalog.SECTION_TITLES[name]:<34} {count}")
    lines += [
        "",
        f"файл сессии:      {info['path']}"
        + ("" if info["exists"] else "  (ещё не создан)"),
        f"сессия:           {'есть' if info['session_present'] else 'нет'}"
        + (f", источник {info['source']}" if info["source"] else ""),
    ]
    if health["unverified"]:
        lines.append(
            "не сверено со справкой: " + ", ".join(health["unverified"])
            + "  (проверьте на живом контуре)"
        )

    status = 0
    if live:
        if not client.session_id:
            lines.append("живая проверка: пропущена — нет сессии")
            status = 1
        else:
            try:
                result = asyncio.run(client.call_command("informaciya_o_polzovatele"))
                user = result.get("Пользователь", result) if isinstance(result, dict) else result
                who = " ".join(
                    str(user.get(k, "")) for k in ("Фамилия", "Имя", "Отчество")
                ).strip() if isinstance(user, dict) else str(user)
                lines.append(f"живая проверка: ок — вошли как {who or 'пользователь'}")
            except SabyError as exc:
                lines.append(f"живая проверка: не удалась — {exc}")
                status = 1
    elif not info["session_present"]:
        lines.append("")
        lines.append("Войдите: saby-mcp login   (или задайте SBIS_SESSION_ID)")
        status = 1

    print("\n".join(lines))
    return status


def _digest(client: SabyClient, store: CredentialStore, args: list[str]) -> int:
    """Сводка входящих из терминала.

    Тот же код, что у инструмента saby_digest: одна реализация, чтобы
    терминал и агент не расходились в том, что они считают сводкой.
    """
    state = DigestState(store.path.parent / DIGEST_STATE_FILE)

    def flag(name: str) -> bool:
        return f"--{name}" in args

    def value(name: str, default: str = "") -> str:
        prefix = f"--{name}="
        for i, item in enumerate(args):
            if item == f"--{name}" and i + 1 < len(args):
                return args[i + 1]
            if item.startswith(prefix):
                return item[len(prefix):]
        return default

    if flag("reset"):
        print(f"Курсор сброшен: {state.clear() and 'был и удалён' or 'и так не было'}")
        return 0

    if not client.session_id:
        print("Нет сессии. Войдите: saby-mcp login   "
              "(или задайте SBIS_SESSION_ID)", file=sys.stderr)
        return 1

    days = int(value("days", str(digest.DEFAULT_DAYS)) or digest.DEFAULT_DAYS)
    since = value("since")
    until = value("until")
    use_cursor = not flag("no-cursor")
    cursor = state.cursor() if (use_cursor and not since) else None

    if not since and not cursor:
        print(f"Курсора нет ({state.path}); беру последние {days} суток. "
              "Чтобы смотреть «что изменилось с утра», добавьте --save при "
              "следующем запуске.", file=sys.stderr)

    try:
        payload = asyncio.run(digest.build(
            client,
            days=days,
            since=since or None,
            until=until or None,
            cursor=cursor,
            with_links=flag("links"),
            include_outgoing=flag("all-lists"),
        ))
    except SabyError as exc:
        print(f"Не получилось: {exc}", file=sys.stderr)
        return 1

    if flag("save"):
        changed = payload.get("изменилось") or {}
        new_cursor = changed.get("курсор_новый")
        if new_cursor and changed.get("полно", True):
            state.save_cursor(new_cursor, until=payload["окно"]["по"],
                              calls=payload.get("вызовы", 0))
            print(f"Курсор сохранён: {new_cursor} ({state.path})", file=sys.stderr)
        else:
            print("Курсор НЕ сдвинут: выдача изменений неполная или "
                  "изменения не запрашивались.", file=sys.stderr)

    if flag("json"):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(payload["сводка"])
    return 0


def _use_utf8_stdio() -> None:
    """Перевести вывод в UTF-8.

    Консоль Windows по умолчанию отдаёт cp1251/cp866 и роняет `print` на любом
    символе вне этой кодировки — а сводка печатает «→», «·» и кавычки. На
    Windows это выглядело как `UnicodeEncodeError` после успешного прогона.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main() -> None:
    _use_utf8_stdio()
    args = sys.argv[1:]
    command = args[0] if args and not args[0].startswith("-") else ""

    if command == "login":
        store = CredentialStore()
        raise SystemExit(_login_interactive(SabyClient(), store))

    if command == "doctor":
        store = CredentialStore()
        client = SabyClient(session_id=store.load_session())
        raise SystemExit(_doctor(client, store, live="--live" in args))

    if command == "digest":
        store = CredentialStore()
        client = SabyClient(session_id=store.load_session())
        raise SystemExit(_digest(client, store, args[1:]))

    if command:
        print(f"{SERVER_NAME}: неизвестная команда {command!r}. "
              "Доступны: login, doctor, digest. "
              "Без аргументов — запуск MCP-сервера.",
              file=sys.stderr)
        raise SystemExit(2)

    mcp, _, _ = build()
    mcp.run()


if __name__ == "__main__":
    main()
