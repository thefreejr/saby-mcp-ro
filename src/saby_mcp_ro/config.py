"""Настройки инструмента, а не секреты: какую «нашу организацию» считать своей.

Зачем
----
В кабинете может быть несколько организаций. Без явного отбора Saby отдаёт
события по всем сразу: справка changeslist прямо говорит «если параметр
[НашаОрганизация] не указан, в ответе вернутся события для всех организаций
личного кабинета». Для сводки это шум — и, что хуже, для курсора по событию
это ещё и риск.

Файл
----
`$SABY_MCP_HOME/config.json` (то есть `~/.saby-mcp/config.json`), рядом с
сессией и курсором. Ключ `наша_организация` — объект вида
`{"СвЮЛ": {"ИНН": "...", "КПП": "..."}}`, ровно такой, как ждёт справка.
Можно задать ключ целиком: `SABY_OUR_ORGANIZATION` перекрывает файл.

Читание никогда не бросает. Отсутствие настройки — не ошибка: сервер тогда
работает как раньше, по всем организациям, и честно говорит об этом там, где
это влияет на полноту выдачи.
"""
from __future__ import annotations

import os

from .credentials import store_dir
from .jsonstore import JsonStore

FILENAME = "config.json"
ENV_KEY = "SABY_OUR_ORGANIZATION"

# Поля, без которых организация не опознаётся: один ИНН для физика, ИНН+КПП
# для юрлица. Организация без ИНН — это не отбор, а шум.
_SVUL_REQUIRED = ("ИНН", "КПП")
_SVFL_REQUIRED = ("ИНН",)


class Settings(JsonStore):
    """Файл настроек рядом с сессией."""

    def __init__(self, path=None):
        super().__init__(path or (store_dir() / FILENAME))

    def our_company(self) -> dict | None:
        """`НашаОрганизация` из окружения или файла; None, если не задана."""
        raw = os.environ.get(ENV_KEY)
        parsed = _parse(raw) if raw else None
        if parsed is None:
            value = self.read().get("наша_организация")
            parsed = _parse(value) if isinstance(value, (str, dict)) else None
        return parsed

    def set_our_company(self, value: dict | None) -> dict:
        """Записать или убрать отбор по организации. Возвращает что получилось."""
        data = self.read()
        org = _parse(value)
        if org is None:
            data.pop("наша_организация", None)
        else:
            data["наша_организация"] = org
        self.write(data)
        return org

    def describe(self) -> dict:
        out = super().describe()
        org = self.our_company()
        out["наша_организация"] = org
        out["env_override"] = bool(os.environ.get(ENV_KEY))
        return out


def _parse(value) -> dict | None:
    """Привести значение к объекту `НашаОрганизация` или отбросить."""
    import json as _json

    if isinstance(value, str):
        try:
            value = _json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict) or not value:
        return None

    svul = value.get("СвЮЛ")
    if isinstance(svul, dict) and svul.get("ИНН"):
        if not svul.get("КПП"):
            return None
        # ровно те поля, что перечисляет справка
        keep = {k: svul[k] for k in ("ИНН", "КПП", "КодФилиала", "КодСтраны")
                if svul.get(k)}
        return {"СвЮЛ": keep}

    svfl = value.get("СвФЛ")
    if isinstance(svfl, dict) and svfl.get("ИНН"):
        keep = {k: svfl[k] for k in ("ИНН", "КодФилиала", "Фамилия", "Имя",
                                     "Отчество") if svfl.get(k)}
        return {"СвФЛ": keep}

    return None


def scoped_filter(extra: dict | None = None, accepts: bool = True) -> dict:
    """То же, но отдаёт СОДЕРЖИМОЕ корневого объекра, а не params.

    Считает, что корень («Фильтр») уже есть: его задаёт вызывающий.
    Отбор по организации добавляется, если команда его принимает, вызывающий
    ничего не задал и настройка существует. Ничего не мутируется на месте.
    """
    merged = dict(extra or {})
    if not accepts:
        return merged
    for root in ("Фильтр", "Параметр", "Отбор"):
        inner = merged.get(root)
        if isinstance(inner, dict) and inner.get("НашаОрганизация"):
            return dict(inner)
    org = Settings().our_company()
    if not org:
        for root in ("Фильтр", "Параметр", "Отбор"):
            inner = merged.get(root)
            if isinstance(inner, dict):
                return dict(inner)
        return merged
    for root in ("Фильтр", "Параметр", "Отбор"):
        inner = merged.get(root)
        if isinstance(inner, dict):
            return {**inner, "НашаОрганизация": org}
    return {**merged, "НашаОрганизация": org}


def _place(params: dict, path: tuple[str, ...], extra: dict) -> dict:
    """Вложить `extra` по пути `path`, создав недостающие уровни.

    У большинства команд путь один — корневой объект. У «СписокПодразделений»
    их два: `Параметр` → `Фильтр`, и «Навигация» с «НашаОрганизацией» лежат
    именно во внутреннем (справка list_squad, живая проверка 17.10.2026).
    """
    out = dict(params)
    cur = out
    for key in path:
        nxt = cur.get(key)
        cur[key] = dict(nxt) if isinstance(nxt, dict) else {}
        cur = cur[key]
    cur.update(extra)
    return out


def scope_filter(params: dict, command_accepts: bool,
                 path: tuple[str, ...] = ()) -> dict:
    """Подставить отбор по организации туда, где команда его принимает.

    Ничего не подменяет: если вызывающий уже передал `НашаОрганизация` —
    значит, он знает, чего хочет. Параметры не мутируются на месте.

    `path` — по справке на конкретную команду; пустой означает «перебрать
    корневые объекты самим» для обратной совместимости.
    """
    if not command_accepts:
        return params
    if path:
        cur = params
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
            if not isinstance(cur, dict):
                cur = None
                break
        if isinstance(cur, dict) and cur.get("НашаОрганизация"):
            return params
        org = Settings().our_company()
        if not org:
            return params
        # Уровни создаются, а не требуются готовыми: у «СписокПодразделений»
        # без «НашаОрганизации» команда не отвечает вовсе, и пустой запрос
        # читался бы как «подразделений нет».
        return _place(params, path, {"НашаОрганизация": org})
    for root in ("Фильтр", "Параметр", "Отбор"):
        inner = params.get(root)
        if isinstance(inner, dict) and inner.get("НашаОрганизация"):
            return params
    org = Settings().our_company()
    if not org:
        return params
    for root in ("Фильтр", "Параметр", "Отбор"):
        if isinstance(params.get(root), dict):
            return _place(params, (root,), {"НашаОрганизация": org})
    return params