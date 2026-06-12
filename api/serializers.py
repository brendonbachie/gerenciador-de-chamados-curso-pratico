"""Serialização de ``Chamado`` para respostas da API.

Responsabilidade exclusiva de parsing de resposta (sem lógica de negócio).
Regras de privacidade aplicadas aqui:
- O telefone em claro nunca sai: expomos apenas ``remetente.mascarado()``.
- ``texto_bruto`` (conteúdo original, pode conter PII digitada pelo usuário)
  só aparece no detalhe, nunca em listagens.
"""

from __future__ import annotations

from datetime import datetime

from core import fluxo
from core.models import Anexo, Chamado, Transicao


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _flags(chamado: Chamado) -> dict:
    f = chamado.flags
    return {
        "duplicado": f.duplicado,
        "incompleto": f.incompleto,
        "produto_ambiguo": f.produto_ambiguo,
        "campos_faltantes": list(f.campos_faltantes),
    }


def _anexo(anexo: Anexo) -> dict:
    return {
        "id": anexo.id,
        "media_id": anexo.media_id,
        "tipo": anexo.tipo,
        "url_ou_ref": anexo.url_ou_ref,
        "nome": anexo.nome,
    }


def _transicao(transicao: Transicao) -> dict:
    return {
        "id": transicao.id,
        "de_estado": transicao.de_estado.value if transicao.de_estado else None,
        "para_estado": transicao.para_estado.value,
        "timestamp": _iso(transicao.timestamp),
        "motivo": transicao.motivo,
        "responsavel": transicao.responsavel,
    }


def resumo(chamado: Chamado) -> dict:
    """Visão de listagem/fila — sem ``texto_bruto``.

    Inclui o ``status_sla`` calculado para alimentar filas e alertas, e o
    remetente sempre mascarado.
    """

    return {
        "id": chamado.id,
        "produto": chamado.produto.value if chamado.produto else None,
        "fila": fluxo.fila_do_produto(chamado),
        "estado": chamado.estado.value,
        "prioridade": chamado.prioridade.value,
        "remetente": chamado.remetente.mascarado(),
        "timestamp_origem": _iso(chamado.timestamp_origem),
        "criado_em": _iso(chamado.criado_em),
        "sla_venc_em": _iso(chamado.sla_venc_em),
        "sla": _serializar_sla(fluxo.status_sla(chamado)),
        "flags": _flags(chamado),
        "texto_normalizado": chamado.texto_normalizado,
    }


def detalhe(chamado: Chamado) -> dict:
    """Visão completa do chamado, incluindo ``texto_bruto``, anexos e histórico.

    Endpoint de detalhe é o único ponto que expõe ``texto_bruto`` — a API deve
    exigir autorização para esta rota quando a autenticação for adicionada.
    """

    dados = resumo(chamado)
    dados.update(
        {
            "texto_bruto": chamado.texto_bruto,
            "campos_triagem": chamado.campos_triagem,
            "anexos": [_anexo(a) for a in chamado.anexos],
            "transicoes": [_transicao(t) for t in chamado.transicoes],
        }
    )
    return dados


def _serializar_sla(status: dict) -> dict:
    """Converte o dict de ``fluxo.status_sla`` para JSON (datetimes → ISO)."""

    return {
        "vencido": status["vencido"],
        "nivel": status["nivel"],
        "resolvido": status["resolvido"],
        "sla_venc_em": _iso(status["sla_venc_em"]),
        "tempo_restante_seg": status["tempo_restante_seg"],
        "percentual_consumido": status["percentual_consumido"],
        "avaliado_em": _iso(status["avaliado_em"]),
    }
