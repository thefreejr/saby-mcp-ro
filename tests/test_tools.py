"""Инструменты MCP: поднимаем сервер и говорим с ним как агент."""
from __future__ import annotations

import json

import pytest

from saby_mcp_ro import __version__, catalog
from saby_mcp_ro.client import SabyClient
from saby_mcp_ro.credentials import CredentialStore
from saby_mcp_ro.server import build
from saby_mcp_ro.tools import register


async def _tool(mcp, name: str, args: dict | None = None) -> dict:
    """Вызвать инструмент и разобрать его JSON-ответ.

    В mcp 2.x call_tool возвращает CallToolResult, а не пару/список.
    """
    result = await mcp.call_tool(name, args or {})
    blocks = getattr(result, "content", result)
    if isinstance(blocks, tuple):
        blocks = blocks[0]
    if isinstance(blocks, dict):
        return blocks
    text = "\n".join(getattr(b, "text", "") for b in blocks)
    return json.loads(text)


@pytest.fixture
async def mcp(recorder, tmp_path):
    """Сервер с готовой сессией.

    Собираем инструменты вручную, а не через build(): build() берёт сессию
    из хранилища, а в изолированном окружении её там нет, и любой вызов
    упирался бы в «войдите сначала», а проверял бы не то.
    """
    from mcp.server.mcpserver import MCPServer

    from saby_mcp_ro import tools as tools_mod

    server = MCPServer("saby-test")
    tools_mod.register(server, SabyClient(session_id="TEST-SESSION"),
                       CredentialStore(tmp_path / "session.json"))
    return server


@pytest.fixture
async def mcp_no_session(tmp_path):
    """Тот же сервер, но без сессии — для проверки входа."""
    from mcp.server.mcpserver import MCPServer

    from saby_mcp_ro import tools as tools_mod

    server = MCPServer("saby-test-nosession")
    tools_mod.register(server, SabyClient(session_id=None),
                       CredentialStore(tmp_path / "session.json"))
    return server


# -- состав ------------------------------------------------------------------

async def test_server_exposes_few_tools(mcp):
    """Девять инструментов вместо восемнадцати.

    У изученного сервера их было 18, из них три давали агенту прямой доступ
    к произвольному пути на домене API. Для сервера только на чтение такой
    доступ не нужен, а лишняя поверхность — да.
    """
    names = {t.name for t in await mcp.list_tools()}
    assert names == {
        "saby_search", "saby_describe", "saby_call",
        "saby_fetch_all", "saby_whoami", "saby_doc_types", "saby_catalog",
        "saby_digest", "saby_digest_cursor",
    }


async def test_no_tool_takes_a_raw_url(mcp):
    """Ни один инструмент не принимает адрес: команды адресуются по имени."""
    for tool in await mcp.list_tools():
        props = (tool.input_schema or {}).get("properties", {})
        for forbidden in ("url", "host", "path", "method", "endpoint"):
            assert forbidden not in props, f"{tool.name}: принимает {forbidden}"


async def test_every_tool_is_declared_read_only(mcp):
    """Каждый инструмент помечен только-чтение и не помечен разрушающим.

    В mcp 2.x ToolAnnotations — модель pydantic с полями snake_case,
    поэтому обращаемся к атрибутам, а не к ключам словаря.
    """
    for tool in await mcp.list_tools():
        ann = tool.annotations
        assert ann is not None, f"{tool.name}: нет аннотаций"
        assert not ann.destructive_hint, f"{tool.name}: помечен разрушающим"
        assert ann.read_only_hint is True, \
            f"{tool.name}: не помечен только-чтение"


# -- поиск и описание --------------------------------------------------------

async def test_search_finds_command(mcp):
    out = await _tool(mcp, "saby_search", {"query": "список документов"})
    assert out["results"]
    assert out["results"][0]["id"] == "spisok_dokumentov"


async def test_search_explains_when_nothing_found(mcp):
    out = await _tool(mcp, "saby_search", {"query": "zzz"})
    assert out["results"] == []
    assert "только чтение" in out["message"]


async def test_describe_gives_cyrillic_params(mcp):
    out = await _tool(mcp, "saby_describe", {"command_id": "spisok_dokumentov"})
    assert out["command"] == "СБИС.СписокДокументов"
    assert "Фильтр" in out["params"]
    assert out["required"]
    assert out["address"].startswith("https://online.sbis.ru/")
    assert out["doc"].startswith("https://")


async def test_describe_by_full_command_name(mcp):
    out = await _tool(mcp, "saby_describe", {"command_id": "СБИС.СписокДокументов"})
    assert out["id"] == "spisok_dokumentov"


async def test_describe_unknown_is_refused(mcp):
    out = await _tool(mcp, "saby_describe", {"command_id": "zapisat_dokument"})
    assert out["ok"] is False


# -- вызов -------------------------------------------------------------------

async def test_call_sends_only_params(mcp, recorder):
    """Агент присылает фильтр; конверт собирает сервер."""
    recorder.result({"Документ": [{"Идентификатор": "1"}]})
    out = await _tool(mcp, "saby_call", {
        "command_id": "spisok_dokumentov",
        "params": {"Фильтр": {"Тип": "Счет"}},
    })
    assert out["ok"] is True
    body = recorder.last["json"]
    assert body["method"] == "СБИС.СписокДокументов"
    assert body["params"] == {"Фильтр": {"Тип": "Счет"}}


async def test_call_without_session_explains_login(mcp_no_session, recorder):
    out = await _tool(mcp_no_session, "saby_call", {"command_id": "spisok_dokumentov"})
    assert out["ok"] is False
    assert "saby_login" in out["message"] or "сесси" in out["message"].lower()
    assert recorder.calls == []


async def test_whoami_without_session(mcp_no_session):
    out = await _tool(mcp_no_session, "saby_whoami")
    assert out["ok"] is False
    assert out["session_present"] is False


async def test_whoami_points_at_the_cli_not_a_missing_tool(mcp_no_session):
    """Вход идёт из терминала, поэтому звать надо `saby-mcp login`.

    Раньше здесь был текст «Войдите: saby_login(логин, пароль)», и агент
    пытался вызвать инструмент saby_login — которого нет и не будет. Ошибка
    была правдоподобной и бесполезной: следующий шаг всё равно проваливался.
    """
    out = await _tool(mcp_no_session, "saby_whoami")
    assert "saby-mcp login" in out["message"]
    assert "saby_login" not in out["message"]


async def test_call_of_mutating_command_is_refused(mcp, recorder):
    """Даже если агент знает имя команды записи, исполнить её нечем."""
    for name in ("zapisat_dokument", "СБИС.ЗаписатьДокумент",
                 "udalit_dokument", "СБИС.УдалитьДокумент",
                 "unichtozhit_dokument", "СБИС.УничтожитьДокумент"):
        out = await _tool(mcp, "saby_call", {"command_id": name})
        assert out["ok"] is False, name
    assert recorder.calls == [], "ни один запрос не должен был уйти на провод"


async def test_call_reports_saby_error(mcp, recorder):
    recorder.push({"jsonrpc": "2.0", "id": 0,
                   "error": {"code": -32602, "message": "Неизвестный тип"}})
    out = await _tool(mcp, "saby_call", {"command_id": "spisok_dokumentov"})
    assert out["ok"] is False
    assert "Неизвестный тип" in out["message"]


# -- пагинация ---------------------------------------------------------------

async def test_fetch_all_reports_truncation_honestly(mcp, recorder):
    """Сервер сказал «ЕстьЕще» — это попадает в notes.

    Инструмент раньше обещал обход всех страниц. Теперь он не может этого
    сделать: Saby не принимает Навигацию в запросе. Вместо обещания он
    сообщает, что выдача усечена.
    """
    recorder.result({"Документ": [{"Идентификатор": str(i)} for i in range(25)],
                     "Навигация": {"ЕстьЕще": "Да", "РазмерСтраницы": "25"}})

    out = await _tool(mcp, "saby_fetch_all", {
        "command_id": "spisok_izmeneniy",
        "params": {"Фильтр": {"ДатаВремяС": "01.01.2026 00:00:00"}},
    })
    assert out["total_fetched"] == 25
    assert out["truncated"] is True
    assert "ЕстьЕще" in out["notes"][0]


async def test_fetch_all_never_sends_navigation(mcp, recorder):
    """regression: объект Навигация в запросе Saby не принимает."""
    recorder.result({"Документ": [], "Навигация": {"ЕстьЕще": "Нет"}})
    await _tool(mcp, "saby_fetch_all", {"command_id": "spisok_dokumentov"})
    assert "Навигация" not in recorder.calls[0]["json"]["params"]


async def test_fetch_all_injects_root_object(mcp, recorder):
    """regression: без «Фильтр» Saby отвечает «метод не найден»."""
    recorder.result({"Документ": [], "Навигация": {"ЕстьЕще": "Нет"}})
    await _tool(mcp, "saby_fetch_all", {"command_id": "spisok_dokumentov"})
    assert "Фильтр" in recorder.calls[0]["json"]["params"]


# -- прочее ------------------------------------------------------------------

async def test_whoami_returns_user(mcp, recorder):
    recorder.result({"Пользователь": {"Фамилия": "Иванов", "Имя": "Пётр"}})
    out = await _tool(mcp, "saby_whoami")
    assert out["ok"] is True
    assert out["session_present"] is True
    assert out["user"]["Фамилия"] == "Иванов"


async def test_catalog_lists_only_reads(mcp):
    out = await _tool(mcp, "saby_catalog")
    assert out["read_only"] is True
    total = sum(s["count"] for s in out["sections"])
    assert total == len(catalog.ALL_COMMANDS)
    commands = [c["command"] for s in out["sections"] for c in s["commands"]]
    for forbidden in ("Записать", "Удалить", "Уничтожить", "Подписать",
                      "Создать", "Отправить"):
        assert not any(forbidden in cmd for cmd in commands), forbidden


async def test_server_instructions_state_the_limit(mcp, recorder):
    """instructions читает ассистент один раз при подключении.

    Место, где агент узнаёт, что писать нельзя, ещё до того, как попробует.
    """
    server, _, _ = build()
    text = server.instructions or ""
    assert "только читает" in text.lower()
    assert "записи и удаления" in text.lower()


async def test_instructions_do_not_promise_pagination(mcp):
    """regression: инструкции обещали обход всех страниц.

    Обход есть, но он не безусловен: у части команд рабочего курсора нет,
    и там обход отдаёт одну страницу. Инструкция, обещающая невозможное,
    вводит агента в заблуждение ровно там, где он потом предъявит неполный
    список как полный.
    """
    server, _, _ = build()
    text = server.instructions or ""
    assert "обходит все страницы" not in text
    assert "truncated" in text or "усеч" in text.lower()


async def test_instructions_do_not_deny_navigation_where_it_works(mcp):
    """regression: инструкция говорила, что «СписокПодразделений» навигацию
    не принимает вовсе.

    Неправда, и она дорого стоит: агент, поверив ей, не стал бы листать
    список подразделений и принял бы две строки за все. Проверено живьём
    17.10.2026 — навигация работает, но лежит не там, где у остальных команд,
    поэтому упоминание обязано быть, а не отсутствовать.
    """
    server, _, _ = build()
    text = server.instructions or ""
    assert "СписокПодразделений" in text
    assert "«СписокАккаунтов», «СписокПодразделений») навигацию" not in text
    # и потолки страниц названы верно: у изменений 50, а не 200
    assert "у служебных этапов" not in text or "до 20" in text
    assert "страница до 50" in text


async def test_instructions_warn_about_document_types(mcp):
    """Про типы — самая частая причина отказа, о ней надо сказать заранее."""
    server, _, _ = build()
    text = server.instructions or ""
    assert "saby_doc_types" in text
    assert "СчетИсх" in text


def test_server_reports_its_version(recorder, isolated_home):
    """Без версии ассистент не может понять, что у него запущено."""
    server, _, _ = build()
    assert server.version == __version__


def test_register_is_idempotent_across_instances():
    """Два экземпляра сервера поднимаются независимо (тестовая изоляция)."""
    from mcp.server.mcpserver import MCPServer

    for _ in range(2):
        mcp = MCPServer("test")
        register(mcp, SabyClient("S"), CredentialStore())
