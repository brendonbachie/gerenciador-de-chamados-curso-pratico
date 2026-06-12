"""Endpoints de chamados: registro manual, consulta e transições de estado.

Esta camada só faz parsing de request/response. Toda a lógica vive em ``core/``:
normalização (``core/normalizer``), triagem (``core/triagem``), ciclo de vida e
SLA (``core/fluxo``) e persistência (``core/repo``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api import serializers
from api.config import db_path
from core import fluxo, repo, triagem
from core.models import Estado, Produto
from core.normalizer import PayloadInvalidoError, normalizar_texto_colado

router = APIRouter(prefix="/chamados", tags=["chamados"])


class RegistroManual(BaseModel):
    """Corpo do registro manual de um chamado colado do WhatsApp."""

    texto: str = Field(..., description="Conteúdo da mensagem original do WhatsApp.")
    remetente: str = Field(..., description="Número de origem; anonimizado no core.")
    timestamp_origem: str | int = Field(
        ..., description="Horário ORIGINAL da mensagem (epoch ou ISO-8601)."
    )


class TransicaoEstado(BaseModel):
    """Corpo de uma transição de estado do chamado."""

    novo_estado: Estado
    motivo: str = Field(..., min_length=1, description="Justificativa obrigatória.")
    responsavel: str | None = None


@router.post("", status_code=201)
def registrar_chamado(corpo: RegistroManual) -> dict:
    """Registra um chamado a partir de texto colado pelo atendente.

    Pipeline: normaliza (preservando origem) → tria por produto → calcula SLA →
    persiste. Retorna o detalhe do chamado criado.
    """

    try:
        chamado = normalizar_texto_colado(
            texto=corpo.texto,
            remetente=corpo.remetente,
            timestamp_origem=corpo.timestamp_origem,
        )
    except PayloadInvalidoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    triagem.triar(chamado)
    fluxo.recalcular_sla(chamado)
    repo.salvar_chamado(chamado, db_path())
    return serializers.detalhe(chamado)


@router.get("")
def listar(
    produto: Annotated[Produto | None, Query()] = None,
    estado: Annotated[Estado | None, Query()] = None,
) -> list[dict]:
    """Lista chamados (resumo, sem ``texto_bruto``), filtrando por produto/estado."""

    chamados = repo.listar_chamados(produto=produto, estado=estado, db_path=db_path())
    return [serializers.resumo(c) for c in chamados]


@router.get("/{chamado_id}")
def obter(chamado_id: str) -> dict:
    """Retorna o detalhe completo de um chamado."""

    chamado = repo.buscar_chamado(chamado_id, db_path())
    if chamado is None:
        raise HTTPException(status_code=404, detail="Chamado não encontrado.")
    return serializers.detalhe(chamado)


@router.post("/{chamado_id}/transicao")
def transicionar_estado(chamado_id: str, corpo: TransicaoEstado) -> dict:
    """Avança o estado do chamado, registrando timestamp e motivo."""

    chamado = repo.buscar_chamado(chamado_id, db_path())
    if chamado is None:
        raise HTTPException(status_code=404, detail="Chamado não encontrado.")

    try:
        fluxo.transicionar(
            chamado,
            corpo.novo_estado,
            motivo=corpo.motivo,
            responsavel=corpo.responsavel,
        )
    except fluxo.MotivoObrigatorio as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except fluxo.TransicaoInvalida as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    repo.salvar_chamado(chamado, db_path())
    return serializers.detalhe(chamado)
