"""Configuração de ambiente lida pela camada de API.

Mantém em um só lugar a resolução do caminho do banco (tornando a API testável
com um banco temporário) e os segredos do webhook do WhatsApp.
"""

from __future__ import annotations

import os
from pathlib import Path

from core import repo


def db_path() -> Path | str:
    """Caminho do SQLite usado pela API.

    Lê ``CHAMADOS_DB`` (útil para testes/integração) e cai no padrão do
    repositório. Resolvido em tempo de chamada para permitir override.
    """

    return os.environ.get("CHAMADOS_DB") or repo.DB_PATH


def ambiente_producao() -> bool:
    """Indica se a aplicação roda em produção (``CHAMADOS_ENV``)."""

    return os.environ.get("CHAMADOS_ENV", "").lower() in {"prod", "producao", "production"}


def whatsapp_app_secret() -> str | None:
    """App secret do WhatsApp para validar a assinatura do webhook."""

    return os.environ.get("CHAMADOS_WHATSAPP_APP_SECRET") or None


def whatsapp_verify_token() -> str | None:
    """Token de verificação do handshake GET do webhook."""

    return os.environ.get("CHAMADOS_WHATSAPP_VERIFY_TOKEN") or None
