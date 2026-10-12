"""Ошибки входа различаются и переводятся в человеческий текст.

Отдельно проверяется главное: отказ по логину и паролю не должен выглядеть
как «сервер упал». Проверено живьём — на кривом теле Saby отвечает другим
кодом, так что эти два случая различимы.
"""
from __future__ import annotations

import pytest

from saby_mcp_ro import client as client_mod
from saby_mcp_ro.client import SabyClient, SabyError, classify_rpc_error


async def _login_raising(recorder, payload):
    recorder.push(payload)
    with pytest.raises(SabyError) as exc:
        await SabyClient(session_id=None).login("aurlin", "any-password")
    return exc.value


# --- классификатор ----------------------------------------------------------

@pytest.mark.parametrize("code,message,expected", [
    # Живые ответы Saby, снятые при разборе входа.
    (-32000, "Проверьте правильность ввода логина и пароля.",
     client_mod.KIND_BAD_CREDENTIALS),
    (-32000, "Проверьте правильность ввода логина и пароля.\n"
             "Попробуйте выполнить операцию позднее.",
     client_mod.KIND_BAD_CREDENTIALS),
    (-32700, "", client_mod.KIND_PARSE),
    (-32601, "Внутренняя ошибка сервера. Метод ... не найден или недоступен",
     client_mod.KIND_METHOD_UNKNOWN),
    (-32602, "Некорректные параметры", client_mod.KIND_BAD_PARAMETERS),
    (-32000, "Слишком много вызовов", client_mod.KIND_TOO_MANY_CALLS),
])
def test_classify_live_shapes(code, message, expected):
    assert classify_rpc_error(code, message, 500) == expected


def test_sms_detected_by_word_not_by_code():
    """Код у СМС-ошибки такой же -32000, различает только текст."""
    assert classify_rpc_error(-32000, "Ошибка подтверждения аутентификации через смс",
                              500) == client_mod.KIND_SMS_CONFIRM


def test_unknown_error_stays_unclassified():
    assert classify_rpc_error(-12345, "что-то новое", 500) == ""


# -- тексты ------------------------------------------------------------------

async def test_bad_credentials_gives_advice_not_json(recorder):
    """Главное: вместо сырого JSON — что делать.

    Раньше в этом месте наружу уходил JSON Saby, и разобраться в нём мог
    только тот, кто уже знает протокол.
    """
    err = await _login_raising(recorder, {
        "jsonrpc": "2.0", "id": 0,
        "error": {"code": -32000, "type": "warning",
                  "message": "Проверьте правильность ввода логина и пароля.",
                  "data": {"classid": "{00000000-0000-0000-0000-1fa000001001}"}},
    })
    text = str(err)
    assert err.kind == client_mod.KIND_BAD_CREDENTIALS
    assert '{"jsonrpc"' not in text
    assert "classid" not in text
    assert "разобран верно" in text
    assert "НомерАккаунта" in text or "номер аккаунта" in text.lower()


async def test_rate_limit_explains_the_limit(recorder):
    err = await _login_raising(recorder, {
        "jsonrpc": "2.0", "id": 0,
        "error": {"code": -32000, "message": "Слишком много вызовов"},
    })
    assert "300" in str(err)
    assert "минуту" in str(err).lower()


async def test_sms_explains_the_missing_piece(recorder):
    err = await _login_raising(recorder, {
        "jsonrpc": "2.0", "id": 0,
        "error": {"code": -32000,
                  "message": "Ошибка подтверждения аутентификации через смс"},
    })
    assert "СБИС.ОтправитьКодАутентификации" in str(err)
    assert "только читает" in str(err) or "только читает" in str(err)


async def test_unknown_method_points_at_the_contract(recorder):
    err = await _login_raising(recorder, {
        "jsonrpc": "2.0", "id": 0,
        "error": {"code": -32601,
                  "message": "Метод СБИС.Аутентифицировать/0 не найден или недоступен"},
    })
    assert "не связано с логином" in str(err)


async def test_unclassified_error_keeps_original_text(recorder):
    """Не знаем, что это — не выдумываем, отдаём как есть."""
    err = await _login_raising(recorder, {
        "jsonrpc": "2.0", "id": 0,
        "error": {"code": -12345, "message": "какой-то новый отказ"},
    })
    assert "какой-то новый отказ" in str(err)


# -- регрессии ---------------------------------------------------------------

async def test_password_is_sent_as_valid_utf8(recorder):
    """Кириллица в пароле не должна превратиться в мусор.

    Проверено живьём: при отказе Saby байты на провод остаются корректным
    UTF-8, значит транспорт данные не портит и отказ не из-за кодировки.
    """
    recorder.push({"jsonrpc": "2.0", "id": 0,
                   "error": {"code": -32000,
                             "message": "Проверьте правильность ввода логина и пароля."}})
    with pytest.raises(SabyError):
        await SabyClient().login("aurlin", "Пароль_Кириллица_123!")

    sent = recorder.last["json"]["params"]["Параметр"]["Пароль"]
    assert sent == "Пароль_Кириллица_123!"
    assert len(sent) == len("Пароль_Кириллица_123!")
    assert recorder.last["headers"]["Content-Type"].endswith("charset=utf-8")


async def test_login_goes_to_auth_path(recorder):
    """regression: /auth/service/, а не /service/ — иначе вход невозможен."""
    recorder.result("SESSION-ID")
    await SabyClient().login("aurlin", "pw")
    assert recorder.last["url"] == "https://online.sbis.ru/auth/service/"
    assert recorder.last["json"]["method"] == "СБИС.Аутентифицировать"


async def test_login_sends_no_session_header(recorder):
    """Сессии ещё нет, её вход и выдаёт."""
    recorder.result("SESSION-ID")
    await SabyClient(session_id=None).login("aurlin", "pw")
    assert "X-SBISSessionID" not in recorder.last["headers"]


async def test_account_number_is_passed_through(recorder):
    recorder.result("SESSION-ID")
    await SabyClient().login("aurlin", "pw", account_number="123")
    assert recorder.last["json"]["params"]["Параметр"]["НомерАккаунта"] == "123"


async def test_account_number_omitted_when_empty(recorder):
    recorder.result("SESSION-ID")
    await SabyClient().login("aurlin", "pw")
    assert "НомерАккаунта" not in recorder.last["json"]["params"]["Параметр"]
