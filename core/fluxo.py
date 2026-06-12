"""Máquina de estados do chamado e cálculo/monitoramento de SLA.

Este módulo concentra a lógica de ciclo de vida (``aberto → em_andamento →
resolvido``), o cálculo do vencimento de SLA a partir do ``timestamp_origem`` e
a priorização de filas. Não faz triagem de conteúdo do produto — isso é
responsabilidade dos agentes de triagem específicos. Não toca o banco
diretamente: a persistência é delegada a ``core/repo.py`` (opcionalmente, via o
parâmetro ``persistir`` de :func:`transicionar`).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from core.models import (
    Chamado,
    Estado,
    Produto,
    Transicao,
    agora_utc,
)

#: Transições de estado permitidas. Mapeia o estado atual ao conjunto de
#: estados de destino válidos. Pular estados (ex.: ``aberto → resolvido``) e
#: retroceder NÃO são permitidos.
TRANSICOES_VALIDAS: dict[Estado, frozenset[Estado]] = {
    Estado.ABERTO: frozenset({Estado.EM_ANDAMENTO}),
    Estado.EM_ANDAMENTO: frozenset({Estado.RESOLVIDO}),
    Estado.RESOLVIDO: frozenset(),
}

#: Roteamento direto produto → fila de atendimento.
FILA_POR_PRODUTO: dict[Produto, str] = {
    Produto.BANCOS: "fila_bancos",
    Produto.MODULOS: "fila_modulos",
    Produto.SIMULADOS: "fila_simulados",
}


class NivelSla(StrEnum):
    """Severidade do estado de SLA de um chamado.

    Attributes:
        OK: Dentro do prazo, sem alerta.
        ATENCAO: Próximo do vencimento (consumo alto do prazo).
        ESTOURADO: Prazo de SLA vencido com o chamado ainda em aberto.
    """

    OK = "ok"
    ATENCAO = "atencao"
    ESTOURADO = "estourado"


#: Fração do prazo de SLA a partir da qual um chamado entra em ``atencao``.
LIMIAR_ATENCAO: float = 0.8


class TransicaoInvalida(Exception):
    """Erro levantado quando uma transição de estado não é permitida.

    Cobre tanto transições que pulam estados (ex.: ``aberto → resolvido``)
    quanto transições inexistentes ou retrocessos.
    """


class MotivoObrigatorio(Exception):
    """Erro levantado quando uma transição é solicitada sem motivo válido."""


# --------------------------------------------------------------------------- #
# Máquina de estados
# --------------------------------------------------------------------------- #


def transicao_permitida(de_estado: Estado, para_estado: Estado) -> bool:
    """Indica se a transição entre dois estados é permitida.

    Args:
        de_estado: Estado de origem.
        para_estado: Estado de destino pretendido.

    Returns:
        ``True`` se a transição constar de :data:`TRANSICOES_VALIDAS`.
    """

    return para_estado in TRANSICOES_VALIDAS.get(de_estado, frozenset())


def proximos_estados(estado: Estado) -> frozenset[Estado]:
    """Retorna os estados de destino válidos a partir de ``estado``.

    Args:
        estado: Estado atual do chamado.

    Returns:
        Conjunto (possivelmente vazio) de estados alcançáveis em uma transição.
    """

    return TRANSICOES_VALIDAS.get(estado, frozenset())


def transicionar(
    chamado: Chamado,
    novo_estado: Estado,
    motivo: str,
    responsavel: str | None = None,
    *,
    persistir: bool = False,
) -> Chamado:
    """Aplica uma transição de estado ao chamado, com rastro auditável.

    Valida a transição contra :data:`TRANSICOES_VALIDAS`, exige um motivo não
    vazio, registra uma :class:`~core.models.Transicao` (com ``timestamp`` via
    :func:`~core.models.agora_utc`) e atualiza ``chamado.estado``.

    A função não persiste por padrão — a gravação é responsabilidade de quem
    chamar, via ``core/repo.py``. Passe ``persistir=True`` para gravar pela
    camada de repositório imediatamente após a transição.

    Args:
        chamado: Chamado a transicionar (mutado in-place).
        novo_estado: Estado de destino.
        motivo: Justificativa da transição. Não pode ser vazio/só espaços.
        responsavel: Identificação de quem executou a transição, se houver.
        persistir: Se ``True``, grava o chamado via ``repo.salvar_chamado``.

    Returns:
        O mesmo ``chamado``, já no novo estado e com a transição registrada.

    Raises:
        MotivoObrigatorio: Se ``motivo`` for vazio ou apenas espaços.
        TransicaoInvalida: Se a transição não for permitida.
    """

    if motivo is None or not motivo.strip():
        raise MotivoObrigatorio(
            "Toda transição de estado exige um motivo não vazio."
        )

    estado_atual = chamado.estado
    if not transicao_permitida(estado_atual, novo_estado):
        raise TransicaoInvalida(
            f"Transição inválida: {estado_atual.value} → {novo_estado.value}. "
            f"Destinos permitidos a partir de {estado_atual.value}: "
            f"{sorted(e.value for e in proximos_estados(estado_atual)) or 'nenhum'}."
        )

    transicao = Transicao(
        de_estado=estado_atual,
        para_estado=novo_estado,
        motivo=motivo.strip(),
        timestamp=agora_utc(),
        responsavel=responsavel,
    )
    chamado.transicoes.append(transicao)
    chamado.estado = novo_estado

    if persistir:
        from core import repo

        repo.salvar_chamado(chamado)

    return chamado


# --------------------------------------------------------------------------- #
# SLA
# --------------------------------------------------------------------------- #


def recalcular_sla(chamado: Chamado) -> Chamado:
    """Recalcula e grava ``chamado.sla_venc_em`` em memória.

    Usa :meth:`~core.models.Chamado.calcular_sla`, que parte do
    ``timestamp_origem`` somado ao prazo da prioridade atual. Deve ser chamado
    após qualquer mudança de prioridade. Não persiste.

    Args:
        chamado: Chamado a atualizar (mutado in-place).

    Returns:
        O mesmo ``chamado``, com ``sla_venc_em`` atualizado.
    """

    chamado.sla_venc_em = chamado.calcular_sla()
    return chamado


def _instante_resolucao(chamado: Chamado) -> datetime | None:
    """Retorna o instante em que o chamado entrou em ``resolvido``, se houver.

    Args:
        chamado: Chamado a inspecionar.

    Returns:
        Timestamp da última transição para ``resolvido``, ou ``None`` se o
        chamado ainda não foi resolvido.
    """

    if chamado.estado is not Estado.RESOLVIDO:
        return None
    resolucoes = [
        t.timestamp for t in chamado.transicoes if t.para_estado is Estado.RESOLVIDO
    ]
    return max(resolucoes) if resolucoes else None


def status_sla(chamado: Chamado, referencia: datetime | None = None) -> dict:
    """Avalia o estado de SLA do chamado em um instante de referência.

    O vencimento usado é ``chamado.sla_venc_em`` quando definido; caso
    contrário, é derivado on-the-fly de :meth:`~core.models.Chamado.calcular_sla`
    (sem mutar o chamado). Para chamados resolvidos, a avaliação é congelada no
    instante de resolução: um chamado resolvido dentro do prazo nunca aparece
    como ``estourado``.

    Args:
        chamado: Chamado a avaliar.
        referencia: Instante de avaliação (UTC). Padrão: ``agora_utc()``.

    Returns:
        Dicionário estruturado com campos sempre presentes:

        - ``vencido`` (bool): ``True`` se o instante avaliado passou do
          vencimento (e o chamado não foi resolvido a tempo).
        - ``nivel`` (str): ``"ok" | "atencao" | "estourado"`` — ver
          :class:`NivelSla`.
        - ``sla_venc_em`` (datetime | None): Vencimento considerado.
        - ``tempo_restante_seg`` (float | None): Segundos até o vencimento;
          negativo se já vencido; ``None`` se não há vencimento calculável.
        - ``percentual_consumido`` (float | None): Fração [0.0, 1.0+] do prazo
          consumida no instante avaliado; ``None`` se incalculável.
        - ``resolvido`` (bool): Se o chamado já está em ``resolvido``.
        - ``avaliado_em`` (datetime): Instante usado na avaliação.
    """

    ref = referencia or agora_utc()
    venc = chamado.sla_venc_em or chamado.calcular_sla()
    resolvido_em = _instante_resolucao(chamado)
    resolvido = chamado.estado is Estado.RESOLVIDO

    # Para chamados resolvidos, congela a avaliação no instante de resolução.
    instante_aval = resolvido_em if resolvido_em is not None else ref

    base = {
        "vencido": False,
        "nivel": NivelSla.OK.value,
        "sla_venc_em": venc,
        "tempo_restante_seg": None,
        "percentual_consumido": None,
        "resolvido": resolvido,
        "avaliado_em": instante_aval,
    }

    if venc is None:
        return base

    tempo_restante = (venc - instante_aval).total_seconds()
    base["tempo_restante_seg"] = tempo_restante

    prazo_total = (venc - chamado.timestamp_origem).total_seconds()
    if prazo_total > 0:
        consumido = (instante_aval - chamado.timestamp_origem).total_seconds()
        base["percentual_consumido"] = consumido / prazo_total

    vencido = tempo_restante < 0
    base["vencido"] = vencido

    if resolvido:
        # Resolvido: nunca conta como estourado em aberto. Apenas sinaliza se
        # foi resolvido fora do prazo (informativo, via ``vencido``).
        base["nivel"] = NivelSla.OK.value
    elif vencido:
        base["nivel"] = NivelSla.ESTOURADO.value
    else:
        pct = base["percentual_consumido"]
        if pct is not None and pct >= LIMIAR_ATENCAO:
            base["nivel"] = NivelSla.ATENCAO.value
        else:
            base["nivel"] = NivelSla.OK.value

    return base


def priorizar(
    chamados: list[Chamado], referencia: datetime | None = None
) -> list[Chamado]:
    """Ordena chamados por urgência de SLA, para montar a fila de atendimento.

    Critério: estourados e mais próximos do vencimento vêm primeiro (menor
    ``tempo_restante_seg``). Chamados já resolvidos vão para o fim, pois não
    competem por atenção imediata.

    A lista original não é mutada — retorna-se uma nova lista ordenada.

    Args:
        chamados: Chamados a priorizar.
        referencia: Instante de avaliação (UTC) repassado a :func:`status_sla`.

    Returns:
        Nova lista de chamados ordenada da maior para a menor urgência.
    """

    ref = referencia or agora_utc()

    def chave(chamado: Chamado) -> tuple[int, float]:
        status = status_sla(chamado, ref)
        resolvido_peso = 1 if status["resolvido"] else 0
        restante = status["tempo_restante_seg"]
        # Sem vencimento calculável: tratado como menos urgente (infinito).
        restante = float("inf") if restante is None else restante
        return (resolvido_peso, restante)

    return sorted(chamados, key=chave)


# --------------------------------------------------------------------------- #
# Roteamento
# --------------------------------------------------------------------------- #


def fila_do_produto(chamado: Chamado) -> str | None:
    """Retorna a fila de destino conforme o produto do chamado.

    Não realiza triagem de conteúdo: apenas mapeia o ``produto`` já
    classificado para sua fila. Chamados ainda não classificados retornam
    ``None`` (aguardando triagem/reclassificação).

    Args:
        chamado: Chamado a rotear.

    Returns:
        Nome da fila (ex.: ``"fila_bancos"``) ou ``None`` se ``produto`` é
        ``None``.
    """

    if chamado.produto is None:
        return None
    return FILA_POR_PRODUTO.get(chamado.produto)
