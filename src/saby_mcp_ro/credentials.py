"""Хранение идентификатора сессии.

Сессия Saby — живой секрет: она даёт доступ к документам компании, пока
действует. Поэтому она лежит в файле с правами 600 вне репозитория, как и
делает уважающий себя инструмент.

Запись атомарная (mkstemp + os.replace), иначе падение посреди записи
оставило бы файл, который не читается, и человек потерял бы сессию молча.
"""
from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
from pathlib import Path

FILENAME = "session.json"


def store_dir() -> Path:
    """Каталог для секретов.

    SABY_MCP_HOME перекрывает всё — удобно в тестах и в CI.
    """
    override = os.environ.get("SABY_MCP_HOME")
    if override:
        return Path(override)
    return Path.home() / ".saby-mcp"


class CredentialStore:
    """Чтение и запись файла сессии."""

    def __init__(self, path: Path | None = None):
        self.path = path or (store_dir() / FILENAME)

    # -- служебное -----------------------------------------------------------

    def _ensure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.path.parent, stat.S_IRWXU)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        self._ensure_dir()
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                   prefix=self.path.name + ".", suffix=".tmp")
        try:
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False, indent=2))
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    # -- публичное -----------------------------------------------------------

    def load_session(self) -> str | None:
        """Идентификатор сессии или None.

        Файл важнее переменной окружения: он свежее, потому что его пишет
        команда `saby-mcp login`, тогда как переменную человек задал,
        возможно, вчера.
        """
        session = self._read().get("session_id")
        if isinstance(session, str) and session.strip():
            return session.strip()
        env = os.environ.get("SBIS_SESSION_ID", "").strip()
        return env or None

    def save_session(self, session_id: str, login: str | None = None) -> None:
        data = self._read()
        data["session_id"] = session_id.strip()
        if login:
            # Логин не секрет, и по нему видно, чья это сессия.
            data["login"] = login
        self._write(data)

    def clear_session(self) -> bool:
        """Забыть сессию. True, если была."""
        data = self._read()
        had = bool(data.get("session_id"))
        if had:
            data.pop("session_id", None)
            self._write(data)
        return had

    def describe(self) -> dict:
        """Где лежит файл и есть ли сессия. Без самого секрета."""
        data = self._read()
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
