"""Webhook do WhatsApp Business API: handshake e entrada de mensagens.

Camada de borda, só parsing. A conversão do payload em chamado é feita por
``core/normalizer`` e a triagem/SLA/persistência pelo restante do ``core/``.

Segurança (ver revisão do security-reviewer):
- POST: valida a assinatura ``X-Hub-Signature-256`` (HMAC-SHA256 do corpo CRU
  com o app secret) em tempo constante ANTES de processar qualquer conteúdo.
- GET: valida o ``hub.verify_token`` do handshake.
- Em produção, segredos ausentes fazem o webhook falhar fechado (fail-closed).
"""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request, Response

from api.config import (
    ambiente_producao,
    db_path,
    whatsapp_app_secret,
    whatsapp_verify_token,
)
from core import fluxo, repo, triagem
from core.normalizer import PayloadInvalidoError, normalizar_payload

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


def _assinatura_valida(corpo_cru: bytes, cabecalho: str | None) -> bool:
    """Valida a assinatura ``X-Hub-Signature-256`` do corpo cru.

    Args:
        corpo_cru: Bytes exatos do corpo recebido (não re-serializado).
        cabecalho: Valor do header, no formato ``sha256=<hex>``.

    Returns:
        ``True`` se a assinatura confere. Se não houver app secret configurado:
        ``False`` em produção (fail-closed) e ``True`` em desenvolvimento.
    """

    secret = whatsapp_app_secret()
    if secret is None:
        # Sem segredo: barra em produção, libera em dev para testes locais.
        return not ambiente_producao()
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(secret.encode(), corpo_cru, hashlib.sha256).hexdigest()
    recebido = cabecalho.split("=", 1)[1]
    return hmac.compare_digest(esperado, recebido)


@router.get("/webhook")
def verificar_webhook(request: Request) -> Response:
    """Handshake de verificação do webhook (Meta/WhatsApp).

    Responde ``hub.challenge`` apenas se ``hub.verify_token`` conferir.
    """

    params = request.query_params
    modo = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    esperado = whatsapp_verify_token()
    if esperado is None and ambiente_producao():
        raise HTTPException(status_code=503, detail="verify token não configurado")

    token_ok = esperado is not None and token is not None and hmac.compare_digest(token, esperado)
    if modo == "subscribe" and token_ok and challenge is not None:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="verificação do webhook falhou")


@router.post("/webhook")
async def receber_webhook(request: Request) -> dict:
    """Recebe um evento do WhatsApp, valida a assinatura e cria o chamado.

    Eventos que não são mensagens (callbacks de status, etc.) são ignorados com
    ``200`` para evitar reentregas. Nunca loga remetente ou conteúdo.
    """

    corpo_cru = await request.body()
    if not _assinatura_valida(corpo_cru, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="assinatura inválida")

    try:
        payload = json.loads(corpo_cru)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="payload não é JSON válido") from exc

    try:
        chamado = normalizar_payload(payload)
    except PayloadInvalidoError:
        # Evento sem mensagem processável (ex.: status de entrega) — ignorado.
        return {"status": "ignorado"}

    triagem.triar(chamado)
    fluxo.recalcular_sla(chamado)
    repo.salvar_chamado(chamado, db_path())
    return {"status": "processado", "id": chamado.id}
