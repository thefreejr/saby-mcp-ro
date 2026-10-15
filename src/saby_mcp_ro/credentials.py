"""Хранение идентификатора сессии.

Сессия Saby — живой секрет: она даёт доступ к документам компании, пока
действует. Поэтому она лежит в файле с правами 600 вне репозитория, как и
делает уважающий себя инструмент.

Запись атомарная (mkstemp + os.replace) — из JsonStore, вместе с курсором
сводки (`digest_state.py`). Второй писатель в тот же файл означал бы второе
место, где «просто запишем», а падение посреди записи там стоило бы сессии.
"""
from __future__ import annotations

import os
from pathlib import Path

from .jsonstore import JsonStore

FILENAME = "session.json"


def store_dir() -> Path:
    """Каталог для секретов.

    SABY_MCP_HOME перекрывает всё — удобно в тестах и в CI.
    """
    override = os.environ.get("SABY_MCP_HOME")
    if override:
        return Path(override)
    return Path.home() / ".saby-mcp"


class CredentialStore(JsonStore):
    """Чтение и запись файла сессии.

    Файл общий с остальными настройками (`SABY_MCP_HOME` перекрывает каталог
    целиком), а атомарная запись и права 600 — из JsonStore: секрет не должен
    переживать падение записи вслед за обычным файлом настроек.
    """

    def __init__(self, path: Path | None = None):
        super().__init__(path or (store_dir() / FILENAME))

    # -- публичное -----------------------------------------------------------

    def load_session(self) -> str | None:
        """Идентификатор сессии или None.

        Файл важнее переменной окружения: он свежее, потому что его пишет
        команда `saby-mcp login`, тогда как переменную человек задал,
        возможно, вчера.
        """
        session = self.read().get("session_id")
        if isinstance(session, str) and session.strip():
            return session.strip()
        env = os.environ.get("SBIS_SESSION_ID", "").strip()
        return env or None

    def save_session(self, session_id: str, login: str | None = None) -> None:
        data = self.read()
        data["session_id"] = session_id.strip()
        if login:
            # Логин не секрет, и по нему видно, чья это сессия.
            data["login"] = login
        self.write(data)

    def clear_session(self) -> bool:
        """Забыть сессию. True, если была."""
        data = self.read()
        had = bool(data.get("session_id"))
        if had:
            data.pop("session_id", None)
            self.write(data)
        return had

    def describe(self) -> dict:
        """Где лежит файл и есть ли сессия. Без самого секрета."""
        data = self.read()
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "session_present": bool(data.get("session_id"))
                               or bool(os.environ.get("SBIS_SESSION_ID", "").strip()),
            "source": ("file" if data.get("session_id")
                       else "env" if os.environ.get("SBIS_SESSION_ID", "").strip()
                       else None),
            "login": data.get("login"),
        }
