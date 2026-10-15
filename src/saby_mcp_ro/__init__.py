"""MCP-сервер только для чтения по API Saby (СБИС).

Пакет экспортирует только то, чему есть смысл импортировать снаружи.
"""

__version__ = "0.1.0"

from .client import SabyClient, SabyError  # noqa: F401
from .credentials import CredentialStore  # noqa: F401
from .digest_state import DigestState  # noqa: F401

__all__ = ["SabyClient", "SabyError", "CredentialStore", "DigestState", "__version__"]
