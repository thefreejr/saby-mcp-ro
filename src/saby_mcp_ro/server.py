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
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__, catalog, tools
from .client import SabyClient, SabyError
from .credentials import CredentialStore

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
            "ПРО ПАГИНАЦИЮ. Saby отдаёт данные порциями и сообщает «ЕстьЕще», но "
            "объект Навигация в запросе не принимает, поэтому обход всех страниц "
            "невозможен. Если saby_fetch_all вернул truncated — выдача неполная, "
            "сузьте период в фильтре. Не выдавайте усечённый список за полный.\n\n"
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


def main() -> None:
    args = sys.argv[1:]
    command = args[0] if args and not args[0].startswith("-") else ""

    if command == "login":
        store = CredentialStore()
        raise SystemExit(_login_interactive(SabyClient(), store))

    if command == "doctor":
        store = CredentialStore()
        client = SabyClient(session_id=store.load_session())
        raise SystemExit(_doctor(client, store, live="--live" in args))

    if command:
        print(f"{SERVER_NAME}: неизвестная команда {command!r}. "
              "Доступны: login, doctor. Без аргументов — запуск MCP-сервера.",
              file=sys.stderr)
        raise SystemExit(2)

    mcp, _, _ = build()
    mcp.run()


if __name__ == "__main__":
    main()
