"""Файл с JSON, который переживает падение посреди записи.

Вынесено из credentials.py, потому что файлов теперь два: сессия и курсор
сводки. Атомарность (mkstemp + os.replace), права 600 на каталог и на файл,
терпимость к битому JSON — всё это нужно обоим, и дублировать такое нельзя:
второе место, где «просто запишем», — это место, где завтра потеряется курсор
или, что хуже, живая сессия.

Ничего секретного здесь не шифруется и не подписывается: файл лежит вне
репозитория, с правами только для владельца, как и положено.
"""
from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
from pathlib import Path


class JsonStore:
    """Чтение и запись одного файла с JSON.

    Чтение никогда не бросает: отсутствующий, пустой или битый файл — это
    пустой словарь. Иначе испорченный курсор не дал бы запустить сервер,
    а человек не понял бы, что случилось.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    # -- служебное -----------------------------------------------------------

    def _ensure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.path.parent, stat.S_IRWXU)

    def read(self) -> dict:
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

    def write(self, data: dict) -> None:
        """Записать атомарно.

        mkstemp + os.replace: падение посреди записи оставило бы файл, который
        не читается, и человек потерял бы содержимое молча.
        """
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

    def describe(self) -> dict:
        """Где лежит файл и есть ли он. Без содержимого."""
        data = self.read()
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "keys": sorted(data),
        }
