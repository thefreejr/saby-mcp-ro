"""Транспорт: конверт, адреса, заголовок сессии.

Тесты, помеченные regression, закрывают ошибки, найденные в изученном сервере.
"""
from __future__ import annotations

import pytest

from saby_mcp_ro import catalog
from saby_mcp_ro.client import SabyClient, SabyError, build_envelope


async def test_envelope_carries_command_name(recorder, client):
    """regression: конверт собирает сервер, а не агент.

    В изученном сервере имя команды лежало в каталоге полем params.body, но
    в запрос не подставлялось: агент должен был прислать конверт сам, а
    пример в README предлагал вызвать вообще без тела.
    """
    recorder.result({"Документ": []})
    await client.call_command("spisok_dokumentov")
    body = recorder.last["json"]
    assert body["method"] == "СБИС.СписокДокументов"
    assert body["jsonrpc"] == "2.0"
    assert "id" in body
    assert "params" in body


async def test_agent_params_go_inside_envelope(recorder, client):
    """Параметры, которые прислал агент, попадают в params конверта."""
    recorder.result({"Документ": []})
    await client.call_command("spisok_dokumentov",
                              {"Фильтр": {"Тип": "Счет"}})
    assert recorder.last["json"]["params"] == {"Фильтр": {"Тип": "Счет"}}


async def test_service_command_hits_service_path(recorder, client):
    """regression: у обычных команд адрес /service/?srv=1."""
    recorder.result({"Документ": []})
    await client.call_command("spisok_dokumentov")
    assert recorder.last["url"] == "https://online.sbis.ru/service/?srv=1"


async def test_auth_command_hits_auth_path(recorder, client):
    """regression: командам аутентификации нужен /auth/service/.

    В изученном сервере все команды раздела auth были на /service/, где их
    нет: вход физически не мог сработать.
    """
    recorder.result("SESSION-ID-123")
    await client.login("user", "pass")
    assert recorder.last["url"] == "https://online.sbis.ru/auth/service/"
    assert recorder.last["json"]["method"] == "СБИС.Аутентифицировать"


async def test_login_sends_no_session_header(recorder):
    """Вход идёт без заголовка сессии: сессии ещё нет, её вход и выдаёт."""
    recorder.result("SESSION-ID-123")
    await SabyClient(session_id=None).login("user", "pass")
    assert "X-SBISSessionID" not in recorder.last["headers"]


async def test_read_command_sends_session_header(recorder, client):
    recorder.result({"Документ": []})
    await client.call_command("spisok_dokumentov")
    assert recorder.last["headers"]["X-SBISSessionID"] == "TEST-SESSION"


async def test_read_without_session_is_refused(recorder):
    """Без сессии чтение не идёт, и сказано почему."""
    with pytest.raises(SabyError, match="session|сесси"):
        await SabyClient(session_id=None).call_command("spisok_dokumentov")
    assert recorder.calls == []


async def test_every_request_goes_to_one_host(recorder, client):
    """Адрес подставляет сервер; подменить его нечем."""
    recorder.result({"Документ": []})
    for command_id in ("spisok_dokumentov", "spisok_nashih_organizaciy",
                       "informaciya_o_versii"):
        await client.call_command(command_id)
    hosts = {call["url"].split("/")[2] for call in recorder.calls}
    assert hosts == {"online.sbis.ru"}


async def test_unknown_command_is_refused(recorder, client):
    with pytest.raises(SabyError):
        await client.call_command("pocalzovat_dokument")
    assert recorder.calls == []


async def test_mutating_command_name_is_refused_by_guard(recorder, client):
    """regression: последний рубеж перед отправкой.

    Даже если имя команды каким-то образом просочилось в клиент, отправки не
    будет: guard сверяет его с каталогом.
    """
    from saby_mcp_ro.client import _check_allowed

    with pytest.raises(SabyError, match="только чтение"):
        _check_allowed("СБИС.ЗаписатьДокумент")
    with pytest.raises(SabyError, match="только чтение"):
        _check_allowed("СБИС.УдалитьДокумент")
    with pytest.raises(SabyError, match="только чтение"):
        _check_allowed("СБИС.УничтожитьДокумент")
    assert recorder.calls == []


async def test_login_returns_session_id(recorder):
    recorder.result("ABC-123")
    session = await SabyClient().login("user", "pass")
    assert session == "ABC-123"


async def test_login_needs_credentials(recorder):
    with pytest.raises(SabyError, match="логин"):
        await SabyClient().login("", "pass")
    assert recorder.calls == []


async def test_login_without_session_id_in_response(recorder):
    recorder.result({})
    with pytest.raises(SabyError):
        await SabyClient().login("user", "pass")


async def test_rpc_error_is_raised_as_readable(recorder, client):
    """Ошибка JSON-RPC разворачивается в текст, а не теряется."""
    recorder.push({"jsonrpc": "2.0", "id": 0,
                   "error": {"code": -32602, "message": "Неверный тип документа"}})
    with pytest.raises(SabyError, match="Неверный тип документа"):
        await client.call_command("spisok_dokumentov")


async def test_expired_session_explains_what_to_do(recorder, client):
    """Истёкшая сессия: сказано, что делать, и сказано верно.

    Раньше здесь упоминался инструмент saby_login, которого нет и не будет:
    вход идёт из терминала. Ошибка вводила в заблуждение — агент пытался
    вызвать несуществующий инструмент и упирался в «не знаю команду».
    """
    recorder.push({"jsonrpc": "2.0", "id": 0, "error": {}}, status=401)
    with pytest.raises(SabyError, match="saby-mcp login"):
        await client.call_command("spisok_dokumentov")


async def test_whoami_points_at_the_cli(recorder):
    """Тот же текст на уровне инструмента — см. test_tools.py."""
    from saby_mcp_ro.client import SabyError

    with pytest.raises(SabyError, match="saby-mcp login"):
        await SabyClient(session_id=None).call_command("spisok_dokumentov")


async def test_non_json_response_is_reported(recorder, client):
    class TextResponse:
        status_code = 200
        headers = {"Content-Type": "text/html"}
        text = "<html>Captcha</html>"

        def json(self):
            raise ValueError("not json")

    recorder.responses = [TextResponse()]
    with pytest.raises(SabyError):
        await client.call_command("spisok_dokumentov")


async def test_result_without_result_or_error(recorder, client):
    recorder.push({"jsonrpc": "2.0", "id": 0})
    with pytest.raises(SabyError, match="нет ни result"):
        await client.call_command("spisok_dokumentov")


def test_build_envelope_shape():
    cmd = catalog.get("spisok_dokumentov")
    envelope = build_envelope(cmd, {"Фильтр": {}})
    assert envelope == {
        "jsonrpc": "2.0",
        "method": "СБИС.СписокДокументов",
        "params": {"Фильтр": {}},
        "id": 0,
    }
