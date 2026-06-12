"""Consultas de apoio: filas por produto e monitoramento de SLA.

Só parsing de response — usa ``core/repo`` para ler e ``core/fluxo`` para
priorizar e calcular o status de SLA. Não escreve nada.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from api import serializers
from api.config import db_path
from core import fluxo, repo
from core.models import Estado, Produto

router = APIRouter(prefix="/relatorios", tags=["relatorios"])


@router.get("/filas")
def filas() -> dict:
    """Agrupa os chamados não resolvidos por fila de produto, já priorizados.

    Chamados sem produto definido (aguardando triagem/reclassificação) vão para
    a chave ``sem_classificacao``.
    """

    resultado: dict[str, list[dict]] = {
        "fila_bancos": [],
        "fila_modulos": [],
        "fila_simulados": [],
        "sem_classificacao": [],
    }
    for produto in Produto:
        chamados = repo.listar_chamados(produto=produto, db_path=db_path())
        abertos = [c for c in chamados if c.estado is not Estado.RESOLVIDO]
        fila = fluxo.FILA_POR_PRODUTO[produto]
        resultado[fila] = [serializers.resumo(c) for c in fluxo.priorizar(abertos)]

    todos = repo.listar_chamados(db_path=db_path())
    sem_produto = [
        c for c in todos if c.produto is None and c.estado is not Estado.RESOLVIDO
    ]
    resultado["sem_classificacao"] = [
        serializers.resumo(c) for c in fluxo.priorizar(sem_produto)
    ]
    return resultado


@router.get("/sla")
def monitorar_sla(
    apenas_em_risco: Annotated[
        bool,
        Query(description="Se verdadeiro, retorna só chamados em atenção ou estourados."),
    ] = False,
) -> list[dict]:
    """Lista chamados não resolvidos priorizados por urgência de SLA."""

    chamados = repo.listar_chamados(db_path=db_path())
    abertos = [c for c in chamados if c.estado is not Estado.RESOLVIDO]
    priorizados = fluxo.priorizar(abertos)

    if apenas_em_risco:
        priorizados = [
            c
            for c in priorizados
            if fluxo.status_sla(c)["nivel"] != fluxo.NivelSla.OK
        ]
    return [serializers.resumo(c) for c in priorizados]
