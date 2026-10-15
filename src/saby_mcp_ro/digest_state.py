"""Курсор сводки: где в прошлый раз остановились.

Зачем он нужен
--------------
Сводка за вчера и сводка за неделю отвечают на разные вопросы. Ежедневная —
«что изменилось с утра вчера до сейчас», и для этого нужен сдвиг, а не дата:
`СБИС.СписокИзменений` отдаёт события по `ДатаВремяС`, и без курсора каждый
запуск перечитывал бы всю историю заново.

Правило, ради которого файл и написан
-------------------------------------
Курсор двигается ТОЛЬКО по полному ответу. Если Saby обрезала выдачу, хвост
ещё не виден — сдвиг вперёд означал бы, что эти изменения никто никогда не
увидит. Молча потерянный день хуже, чем повторный день в сводке.

Файл лежит рядом с сессией (`~/.saby-mcp/digest-state.json`, перекрывается
`SABY_MCP_HOME`) и пишется тем же атомарным writer, что и сессия: падение
посреди записи не должно оставлять ни курсор, ни секрет в нечитаемом виде.

Секретов здесь нет — только время и счётчики, — но права 600 достаются
бесплатно вместе с остальным хранилищем, и лишними они не мешают.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .credentials import store_dir
from .jsonstore import JsonStore

FILENAME = "digest-state.json"
VERSION = 1


class DigestState(JsonStore):
    """Курсор и сведения о последнем запуске сводки."""

    def __init__(self, path: Path | None = None):
        # Каталог берётся у хранилища сессии: `SABY_MCP_HOME` перекрывает оба
        # файла разом, иначе в тестах один уехал бы в tmp, а другой в ~/.saby-mcp.
        super().__init__(path or (store_dir() / FILENAME))

    # -- чтение --------------------------------------------------------------

    def cursor(self) -> str | None:
        """Время, с которого ждём изменения. None — курсора ещё нет."""
        value = self.read().get("курсор")
        return str(value).strip() if isinstance(value, str) and value.strip() else None

    def describe(self) -> dict[str, Any]:
        """Что известно о прошлом запуске. Секретов тут не бывает по построению."""
        data = self.read()
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "курсор": data.get("курсор"),
            "обновлён": data.get("обновлён"),
            "вызовов": data.get("вызовов"),
            "полно": data.get("полно"),
            "версия": data.get("версия"),
        }

    # -- запись --------------------------------------------------------------

    def save_cursor(
        self, cursor: str, *, until: str = "", calls: int = 0, complete: bool = True,
        updated: datetime | None = None,
    ) -> dict[str, Any]:
        """Запомнить новый курсор.

        Неполная выдача (`complete=False`) курсор не двигает: сохраняется
        прежний, а в файл кладётся пометка, почему он не сдвинулся.
        """
        data = self.read()
        data["версия"] = VERSION
        data["обновлён"] = (updated or datetime.now()).isoformat(timespec="seconds")
        data["вызовов"] = calls
        data["полно"] = bool(complete)
        if complete:
            data["курсор"] = str(cursor).strip()
            if until:
                data["окно_по"] = str(until).strip()
        else:
            data["курсор_прежний"] = data.get("курсор")
        self.write(data)
        return data

    def clear(self) -> bool:
        """Сбросить курсор. True, если он был."""
        data = self.read()
        had = bool(data.get("курсор"))
        if had:
            data.pop("курсор", None)
            self.write(data)
        return had
