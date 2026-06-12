"""Fixtures de integração da API.

Cada teste roda contra um banco SQLite temporário e isolado (``tmp_path``),
resolvido pela API via a variável de ambiente ``CHAMADOS_DB``. O banco é
configurado ANTES de instanciar o ``TestClient`` para que o ``lifespan`` da
aplicação rode ``repo.init_db`` sobre o arquivo correto.

Tudo é determinístico: timestamps de origem são fixos (ou ancorados a deltas
explícitos) e os segredos do webhook são injetados por ``monkeypatch``, nunca
lidos do ambiente real.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

#: Segredos de teste do webhook do WhatsApp (injetados via env nos testes).
APP_SECRET = "segredo-de-teste-do-app"
VERIFY_TOKEN = "token-de-verificacao-de-teste"


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """``TestClient`` com banco temporário e segredos do webhook configurados.

    Define ``CHAMADOS_DB`` para um arquivo dentro de ``tmp_path`` e os segredos
    do webhook ANTES de importar/instanciar o app, garantindo que o ``lifespan``
    inicialize o schema no banco isolado. Garante também que o ambiente não seja
    tratado como produção.
    """

    monkeypatch.setenv("CHAMADOS_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("CHAMADOS_WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("CHAMADOS_WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.delenv("CHAMADOS_ENV", raising=False)

    from app import app

    with TestClient(app) as c:
        yield c


def assinar(corpo_cru: bytes, secret: str = APP_SECRET) -> str:
    """Calcula o header ``X-Hub-Signature-256`` para um corpo cru.

    Args:
        corpo_cru: Bytes exatos que serão enviados no corpo da requisição.
        secret: App secret usado como chave HMAC.

    Returns:
        Valor do header no formato ``sha256=<hexdigest>``.
    """

    digest = hmac.new(secret.encode(), corpo_cru, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def payload_whatsapp(
    *,
    numero: str = "5511988887777",
    timestamp: str = "1749643200",  # 2025-06-11T12:00:00Z (epoch determinístico)
    texto: str = "duvida sobre o banco de questoes com gabarito errado",
) -> dict:
    """Monta um envelope de webhook do WhatsApp com uma mensagem de texto.

    Args:
        numero: Número de origem (``from``) — anonimizado no core.
        timestamp: Epoch em segundos (string), preservado como origem.
        texto: Corpo da mensagem.

    Returns:
        Dicionário no formato ``entry/changes/value/messages``.
    """

    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": numero,
                                    "timestamp": timestamp,
                                    "text": {"body": texto},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }


def payload_whatsapp_com_imagem(
    *,
    numero: str = "5511988887777",
    timestamp: str = "1749643200",
    media_id: str = "MID.IMG.123",
    caption: str = "print do erro no banco de questoes",
) -> dict:
    """Monta um webhook com uma mensagem de imagem (anexo com legenda).

    Args:
        numero: Número de origem (``from``).
        timestamp: Epoch em segundos (string).
        media_id: Id da mídia (referência; nunca o binário).
        caption: Legenda da imagem (vira texto do chamado).

    Returns:
        Envelope ``entry/changes/value/messages`` com bloco ``image``.
    """

    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": numero,
                                    "timestamp": timestamp,
                                    "image": {
                                        "id": media_id,
                                        "sha256": "abc123",
                                        "caption": caption,
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }


def corpo_cru(payload: dict) -> bytes:
    """Serializa um payload em bytes estáveis (para assinar e enviar o MESMO).

    Args:
        payload: Objeto a serializar.

    Returns:
        Bytes JSON sem espaços supérfluos, prontos para assinar e enviar.
    """

    return json.dumps(payload, separators=(",", ":")).encode()
