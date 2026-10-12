"""HTTP-транспорт к API Saby.

Ключевое отличие от изученного сервера: **конверт JSON-RPC собирает сервер,
а не вызывает агент**. Там имя команды лежало в каталоге как поле `params.body`,
но при вызове оно не подставлялось в запрос — агент обязан был сам составить
и передать конверт, а пример в README предлагал вызвать вообще без тела. Здесь
агент передаёт только содержимое `params`, а `method`, `jsonrpc` и `id`
проставляются здесь, из записи каталога. Ошибку такого рода сделать нечем.

Ещё два отличия, оба про адрес:
- адрес берётся из каталога, а не от вызывающего. У агента нет инструмента
  «позвать произвольный URL», поэтому подменить хост или путь нечем;
- путь `/auth/service/` для команд аутентификации и `/service/?srv=1` для
  остальных. В изученном сервере все 45 записей были на `/service/`, из-за
  чего вход был физически невозможен.
"""
from __future__ import annotations

import asyncio
import email.utils
import time
from typing import Any

import httpx

from . import catalog

DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
BACKOFF_BASE = 1.5

SESSION_HEADER = "X-SBISSessionID"


class SabyError(Exception):
    """Ошибка транспорта. Текст пригоден для показа человеку.

    code и kind несут разобранную классификацию ответа Saby: их использует
    login, чтобы вместо сырого JSON объяснить, что именно не так.
    """

    def __init__(self, message: str, *, code: Any = None, kind: str = ""):
        super().__init__(message)
        self.code = code
        self.kind = kind


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError):
        return None
    if dt is None:
        return None
    return max(0.0, dt.timestamp() - time.time())


def build_envelope(cmd: catalog.Command, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Конверт JSON-RPC для команды каталога.

    Единственное место в проекте, где собирается тело запроса. Единственный
    способ получить `method` — указать id записи из каталога.
    """
    return {
        "jsonrpc": "2.0",
        "method": cmd.command,
        "params": params or {},
        "id": 0,
    }


def _check_allowed(command: str) -> None:
    """Последний рубеж перед отправкой.

    Каталог и так не содержит команд записи, и адрес берётся из него же, так
    что штатно сюда нечему попасть. Но проверка стоит одну строку, а снимает
    целый класс вопросов «а если кто-то всё-таки подсунет СБИС.ЗаписатьДокумент».
    """
    cmd = catalog.BY_COMMAND.get(command)
    if cmd is None:
        raise SabyError(
            f"Команда {command!r} отсутствует в каталоге. Этот сервер умеет "
            f"только чтение: в каталоге {len(catalog.ALL_COMMANDS)} команд, "
            "все они возвращают данные и ничего не меняют."
        )


# Коды и тексты взяты из живых ответов Saby и из перечисления ошибок в справке.
# Нужны, чтобы вместо сырого JSON человек прочитал, что делать.
KIND_BAD_CREDENTIALS = "bad_credentials"
KIND_TOO_MANY_CALLS = "too_many_calls"
KIND_SMS_CONFIRM = "sms_confirm"
KIND_BAD_PARAMETERS = "bad_parameters"
KIND_METHOD_UNKNOWN = "method_unknown"
KIND_PARSE = "parse"
KIND_UNKNOWN_DOC_TYPE = "unknown_doc_type"


def classify_rpc_error(code: Any, message: Any, status: int) -> str:
    """Отнести ошибку Saby к понятному классу.

    Разбираем и код, и текст: Saby для разных случаев даёт пересекающиеся
    коды, и текст здесь надёжнее.
    """
    text = str(message or "").lower()
    if "неизвестный тип документа" in text:
        return KIND_UNKNOWN_DOC_TYPE
    if code == -32700 or "parse error" in text:
        return KIND_PARSE
    if code == -32601 or "не найден или недоступен" in text:
        return KIND_METHOD_UNKNOWN
    if "слишком много" in text or "too many" in text:
        return KIND_TOO_MANY_CALLS
    if "смс" in text or "sms" in text:
        return KIND_SMS_CONFIRM
    if "правильность ввода логина и пароля" in text or "неверный логин" in text:
        return KIND_BAD_CREDENTIALS
    if code == -32602 or "параметр" in text:
        return KIND_BAD_PARAMETERS
    if status == 500 and code == -32000:
        # Обёртка Saby: HTTP 500 с -32000 — это «сервер разобрал запрос и
        # отказал по существу», а не «сервер упал». Так приходит отказ по
        # логину и паролю.
        return KIND_BAD_CREDENTIALS
    return ""


class SabyClient:
    """Клиент одного аккаунта Saby.

    Сессия передаётся снаружи и может отсутствовать: командам раздела auth
    она не нужна, они по ней и создаются. Проверка «есть ли сессия» живёт в
    `call`, а не в конструкторе, иначе войти было бы невозможно.
    """

    def __init__(self, session_id: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.session_id = (session_id or "").strip() or None
        self.timeout = timeout

    # -- служебное -----------------------------------------------------------

    def _headers(self, with_session: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "saby-mcp-ro/0.1 (read-only)",
        }
        if with_session and self.session_id:
            headers[SESSION_HEADER] = self.session_id
        return headers

    # -- вызов ---------------------------------------------------------------

    async def call_command(
        self,
        command_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Вызвать команду каталога. Возвращает разобранный `result`.

        Бросает SabyError с понятным текстом при любой неудаче: транспортной
        или возвращённой Saby. Ошибок «получили 200, а внутри непонятно» здесь
        не бывает — JSON-RPC всегда либо result, либо error.
        """
        cmd = catalog.get(command_id)
        if cmd is None:
            hint = self._did_you_mean(command_id)
            raise SabyError(
                f"Не знаю команду {command_id!r}.{hint} "
                f"Доступные команды: {', '.join(sorted(catalog.BY_ID))}."
            )
        if cmd.needs_session and not self.session_id:
            raise SabyError(
                "Нет идентификатора сессии. Вход идёт из терминала: "
                "выполните `saby-mcp login`. Если сессия уже есть, задайте её "
                "переменной SBIS_SESSION_ID."
            )
        _check_allowed(cmd.command)
        try:
            return await self._send(cmd, catalog.with_root(cmd, params))
        except SabyError as exc:
            if exc.kind == KIND_UNKNOWN_DOC_TYPE:
                raise SabyError(_explain_doc_type(exc, cmd, params),
                                code=exc.code, kind=exc.kind) from None
            raise

    async def login(
        self,
        login: str,
        password: str,
        account_number: str | None = None,
    ) -> str:
        """Создать сессию. Возвращает идентификатор.

        Идёт на /auth/service/ БЕЗ заголовка сессии — так устроен сам Saby:
        вход и есть единственный способ получить сессию.
        """
        if not login or not password:
            raise SabyError("Нужны и логин, и пароль.")
        param: dict[str, Any] = {"Логин": login, "Пароль": password}
        if account_number:
            param["НомерАккаунта"] = str(account_number)

        cmd = catalog.BY_ID["autentificirovat"]
        _check_allowed(cmd.command)
        try:
            result = await self._send(cmd, {"Параметр": param}, with_session=False)
        except SabyError as exc:
            raise SabyError(_explain_login_error(exc), code=exc.code,
                            kind=exc.kind) from None
        # Справка: результат — строка, идентификатор сессии.
        if isinstance(result, str):
            session = result.strip()
        elif isinstance(result, dict):
            session = str(
                result.get("СессионныйИдентификатор")
                or result.get("ИдентификаторСессии")
                or ""
            ).strip()
        else:
            session = ""
        if not session:
            raise SabyError(
                "Saby не вернул идентификатор сессии. Если у сотрудника включена "
                "двухфакторная аутентификация, потребуется код из СМС — его "
                "запрашивает команда СБИС.ОтправитьКодАутентификации, которая "
                "в каталог не входит: она пишет."
            )
        self.session_id = session
        return session

    async def _send(
        self,
        cmd: catalog.Command,
        params: dict[str, Any],
        with_session: bool | None = None,
    ) -> Any:
        """Один HTTP-запрос с повторами по 429. Возвращает `result`."""
        use_session = cmd.needs_session if with_session is None else with_session
        headers = self._headers(use_session)
        body = build_envelope(cmd, params)
        url = f"https://{catalog.HOST}{cmd.path}"

        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as http:
                    resp = await http.post(url, json=body, headers=headers)
            except httpx.ConnectError as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                    attempt += 1
                    continue
                raise SabyError(
                    f"Не удалось соединиться с {catalog.HOST}: {exc}. "
                    "Проверьте сеть и доступ к online.sbis.ru."
                ) from exc
            except httpx.TimeoutException as exc:
                # Таймаут чтения — запрос мог уже исполниться на сервере, но
                # повторять его нельзя: единственное, что делает этот сервер,
                # это чтение, так что повтор безопасен. Пишем это явно, чтобы
                # при добавлении команд записи это место сразу бросалось в глаза.
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                    attempt += 1
                    continue
                raise SabyError(f"Saby не ответил за {self.timeout} с.") from exc
            except httpx.HTTPError as exc:
                raise SabyError(f"Сбой обращения к Saby: {exc}") from exc

            if resp.status_code == 429 and attempt < MAX_RETRIES:
                delay = _parse_retry_after(resp.headers.get("Retry-After"))
                await asyncio.sleep(min(delay or (BACKOFF_BASE * (2 ** attempt)), 30.0))
                attempt += 1
                continue

            if resp.status_code in (401, 403):
                raise SabyError(
                    "Saby отклонил сессию (HTTP "
                    f"{resp.status_code}). Она живёт ограниченное время: войдите "
                    "заново командой `saby-mcp login` в терминале."
                )
            if resp.status_code >= 400:
                # Тело разбираем ПЕРЕД тем, как смотреть на код. Saby
                # отдаёт JSON-RPC ошибку с кодом 500 — в том числе отказ по
                # логину и паролю. Раньше здесь выплёвывался сырой текст,
                # и классификация ответа не доходила никуда.
                try:
                    payload = resp.json()
                except ValueError:
                    payload = None
                err = self._rpc_error(payload, cmd, resp.status_code)
                if err is not None:
                    raise err
                raise SabyError(
                    f"Saby вернул HTTP {resp.status_code}: {_short(resp.text)}"
                )

            return self._unwrap(resp, cmd)

    @staticmethod
    @staticmethod
    def _rpc_error(payload: Any, cmd: catalog.Command,
                   status: int) -> SabyError | None:
        """Разобрать тело JSON-RPC в SabyError, если ошибка в нём есть.

        Отдельная функция нужна потому, что Saby отдаёт нормальную
        JSON-RPC ошибку с кодом HTTP 500 — в том числе отказ по логину и
        паролю. Если смотреть только на HTTP-код, мимо пройдёт всё, что важно,
        и наружу уйдёт сырой JSON вместо разбора.
        """
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not error:
            return None
        if isinstance(error, dict):
            code = error.get("code", "?")
            message = error.get("message") or error.get("data") or ""
            return SabyError(
                f"{cmd.command}: ошибка {code}. {message}".strip(),
                code=code,
                kind=classify_rpc_error(code, message, status),
            )
        return SabyError(f"{cmd.command}: {error}")

    @classmethod
    def _unwrap(cls, resp: httpx.Response, cmd: catalog.Command) -> Any:
        try:
            payload = resp.json()
        except ValueError:
            raise SabyError(
                f"Saby ответил не JSON: {_short(resp.text)}"
            ) from None

        err = cls._rpc_error(payload, cmd, resp.status_code)
        if err is not None:
            raise err

        if not isinstance(payload, dict):
            raise SabyError("Неожиданный формат ответа Saby.")
        result = payload.get("result")
        if result is None:
            raise SabyError(
                f"{cmd.command}: в ответе нет ни result, ни error. "
                f"Ответ: {_short(resp.text)}"
            )
        return result

    @staticmethod
    def _did_you_mean(unknown: str) -> str:
        from . import search as search_mod

        words = search_mod.tokens(unknown)
        if not words:
            return ""
        near = search_mod.suggest(words[-1])
        return f" Похожее: {', '.join(near)}." if near else ""


def _explain_doc_type(exc: SabyError, cmd: catalog.Command,
                      params: dict[str, Any] | None) -> str:
    """Перевести «неизвестный тип документа» в подсказку, чем заменить.

    Отказ Saby на это поле — самый частый способ упереться в сервер: человек
    пишет «счёт», а машина хочет `СчетИсх`. Голое «неизвестный тип» ни о чём
    не говорит, поэтому здесь перечисляются подходящие типы.
    """
    from . import doctypes

    filt = (params or {}).get("Фильтр") or {}
    given = str(filt.get("Тип", "") or "").strip()
    lines = [
        f"{cmd.command}: Saby не знает тип документа {given!r}.",
        "",
        "Поле Фильтр.Тип принимает не название, а внутренний код типа из",
        "справочника формата документов. Например:",
        "  «счёт»          -> СчетИсх или СчетВх",
        "  «накладная»     -> ДокОтгрИсх или ДокОтгрВх",
        "  «фактура»       -> ФактураИсх или ФактураВх",
    ]
    if given:
        near = doctypes.suggest(given)
        if near:
            lines.append("")
            lines.append(f"Похожее на {given!r}:")
            for t in near:
                mark = "" if t.verified else "  (не проверен на этой учётной записи)"
                lines.append(
                    f"  {t.code:22s} {t.title} — {', '.join(t.directions)}{mark}"
                )
    lines += [
        "",
        f"Весь справочник ({len(doctypes.ALL_TYPES)} типов, "
        f"{len(doctypes.VERIFIED)} проверенных) — saby_doc_types.",
    ]
    return "\n".join(lines)


def _explain_login_error(exc: SabyError) -> str:
    """Перевести отказ Saby на входе в текст, с которым можно что-то сделать.

    Раньше здесь наружу уходил сырой JSON Saby — информативности в нём ноль,
    кроме самого факта отказа. Формы запроса проверены живьём: при верном
    запросе Saby отвечает именно так, а за структурную ошибку даёт другой код,
    так что этот текст означает «данные не приняты», а не «запрос не разобран».
    """
    if exc.kind == KIND_BAD_CREDENTIALS:
        return (
            "Saby не принял логин и пароль. Запрос при этом разобран верно — "
            "это отказ по данным, а не ошибка формата (проверено живьём: на "
            "кривом теле Saby отвечает другим кодом).\n"
            "Что стоит проверить по порядку:\n"
            "  • пароль набирается в консоли cmd — раскладка, Caps Lock, "
            "Shift; вводите тем же, чем входите на online.sbis.ru;\n"
            "  • если в пароле есть кириллица или знаки вроде ! @, проверьте "
            "их: набирать лучше в блокноте и вставлять, консоль такие "
            "символы иногда искажает;\n"
            "  • логин в Saby — это телефон или почта; попробуйте тот, что "
            "указывали при регистрации;\n"
            "  • если у вас несколько кабинетов, укажите номер аккаунта "
            "при входе — бывает, что без него выбирается не тот кабинет;\n"
            "  • вход на online.sbis.ru браузером работает? Если нет — "
            "проблема в учётной записи, а не здесь."
        )
    if exc.kind == KIND_TOO_MANY_CALLS:
        return (
            "Saby отказал из-за частоты запросов: у входа ограничение 300 "
            "вызовов в минуту. Подождите минуту и повторите."
        )
    if exc.kind == KIND_SMS_CONFIRM:
        return (
            "Saby требует подтверждение по СМС. Нужна команда "
            "СБИС.ОтправитьКодАутентификации, а она в каталоге отсутствует: "
            "она пишет, а сервер только читает. Пока двух факторов не "
            "поддерживаем — скажите, и я сделаю исключение только для входа."
        )
    if exc.kind == KIND_METHOD_UNKNOWN:
        return (
            "Saby не нашёл команду аутентификации по этому адресу. Это "
            "не связано с логином: значит, у адреса /auth/service/ изменился "
            "контракт. Пришлите вывод — поправлю."
        )
    return str(exc)


def _short(text: str, limit: int = 300) -> str:
    return (text or "").strip()[:limit]
