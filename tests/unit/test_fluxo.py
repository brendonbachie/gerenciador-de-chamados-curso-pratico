"""Testes da máquina de estados, do SLA e da priorização (``core/fluxo``).

Controle de tempo 100% determinístico: SLA é avaliado sempre passando
``referencia`` explícita; nunca se depende de ``agora_utc``/``datetime.now``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.fluxo import (
    MotivoObrigatorio,
    TransicaoInvalida,
    fila_do_produto,
    priorizar,
    recalcular_sla,
    status_sla,
    transicionar,
)
from core.models import Estado, Prioridade, Produto

ORIGEM = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Transições válidas — registram rastro auditável
# --------------------------------------------------------------------------- #


def test_transicao_valida_registra_transicao_com_timestamp_e_motivo(fazer_chamado):
    # Arrange
    chamado = fazer_chamado(estado=Estado.ABERTO)

    # Act
    transicionar(chamado, Estado.EM_ANDAMENTO, motivo="atendente assumiu", responsavel="ana")

    # Assert
    assert chamado.estado is Estado.EM_ANDAMENTO
    assert len(chamado.transicoes) == 1
    t = chamado.transicoes[0]
    assert t.de_estado is Estado.ABERTO
    assert t.para_estado is Estado.EM_ANDAMENTO
    assert t.motivo == "atendente assumiu"
    assert t.responsavel == "ana"
    assert t.timestamp.tzinfo is not None


def test_fluxo_completo_aberto_em_andamento_resolvido(fazer_chamado):
    # Arrange
    chamado = fazer_chamado(estado=Estado.ABERTO)

    # Act
    transicionar(chamado, Estado.EM_ANDAMENTO, motivo="iniciado")
    transicionar(chamado, Estado.RESOLVIDO, motivo="corrigido")

    # Assert: dois registros, na ordem correta.
    assert chamado.estado is Estado.RESOLVIDO
    assert [t.para_estado for t in chamado.transicoes] == [
        Estado.EM_ANDAMENTO,
        Estado.RESOLVIDO,
    ]


def test_motivo_é_aparado(fazer_chamado):
    chamado = fazer_chamado(estado=Estado.ABERTO)

    transicionar(chamado, Estado.EM_ANDAMENTO, motivo="   com espaços   ")

    assert chamado.transicoes[0].motivo == "com espaços"


# --------------------------------------------------------------------------- #
# Transições inválidas
# --------------------------------------------------------------------------- #


def test_pular_estado_aberto_para_resolvido_levanta(fazer_chamado):
    chamado = fazer_chamado(estado=Estado.ABERTO)

    with pytest.raises(TransicaoInvalida):
        transicionar(chamado, Estado.RESOLVIDO, motivo="quero pular")

    # Estado não muda e nada é registrado.
    assert chamado.estado is Estado.ABERTO
    assert chamado.transicoes == []


def test_retroceder_resolvido_para_aberto_levanta(fazer_chamado):
    chamado = fazer_chamado(estado=Estado.RESOLVIDO)

    with pytest.raises(TransicaoInvalida):
        transicionar(chamado, Estado.ABERTO, motivo="reabrir")


def test_transicao_para_o_mesmo_estado_levanta(fazer_chamado):
    chamado = fazer_chamado(estado=Estado.ABERTO)

    with pytest.raises(TransicaoInvalida):
        transicionar(chamado, Estado.ABERTO, motivo="sem mudança")


# --------------------------------------------------------------------------- #
# Motivo obrigatório
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("motivo", ["", "   ", "\n\t"])
def test_motivo_vazio_levanta(fazer_chamado, motivo):
    chamado = fazer_chamado(estado=Estado.ABERTO)

    with pytest.raises(MotivoObrigatorio):
        transicionar(chamado, Estado.EM_ANDAMENTO, motivo=motivo)

    assert chamado.transicoes == []


# --------------------------------------------------------------------------- #
# recalcular_sla
# --------------------------------------------------------------------------- #


def test_recalcular_sla_grava_vencimento_a_partir_da_origem(fazer_chamado):
    # Arrange
    chamado = fazer_chamado(prioridade=Prioridade.ALTA, timestamp_origem=ORIGEM)
    assert chamado.sla_venc_em is None

    # Act
    recalcular_sla(chamado)

    # Assert: ALTA = 8h após a origem.
    assert chamado.sla_venc_em == ORIGEM + timedelta(hours=8)


def test_recalcular_sla_reflete_nova_prioridade(fazer_chamado):
    chamado = fazer_chamado(prioridade=Prioridade.BAIXA, timestamp_origem=ORIGEM)
    recalcular_sla(chamado)
    assert chamado.sla_venc_em == ORIGEM + timedelta(hours=48)

    chamado.prioridade = Prioridade.URGENTE
    recalcular_sla(chamado)
    assert chamado.sla_venc_em == ORIGEM + timedelta(hours=4)


# --------------------------------------------------------------------------- #
# status_sla — níveis ok / atencao / estourado
# --------------------------------------------------------------------------- #


def test_status_sla_nivel_ok(fazer_chamado):
    # Arrange: MEDIA = 24h. 1h após a origem -> bem dentro do prazo.
    chamado = fazer_chamado(prioridade=Prioridade.MEDIA, timestamp_origem=ORIGEM)
    ref = ORIGEM + timedelta(hours=1)

    # Act
    status = status_sla(chamado, referencia=ref)

    # Assert
    assert status["nivel"] == "ok"
    assert status["vencido"] is False
    assert status["resolvido"] is False
    assert status["tempo_restante_seg"] == pytest.approx(23 * 3600)
    assert status["percentual_consumido"] == pytest.approx(1 / 24)
    assert status["sla_venc_em"] == ORIGEM + timedelta(hours=24)
    assert status["avaliado_em"] == ref


def test_status_sla_nivel_atencao(fazer_chamado):
    # Arrange: MEDIA = 24h. 80% do prazo == 19.2h consumidas -> atenção.
    chamado = fazer_chamado(prioridade=Prioridade.MEDIA, timestamp_origem=ORIGEM)
    ref = ORIGEM + timedelta(hours=20)

    # Act
    status = status_sla(chamado, referencia=ref)

    # Assert
    assert status["nivel"] == "atencao"
    assert status["vencido"] is False
    assert status["percentual_consumido"] == pytest.approx(20 / 24)


def test_status_sla_nivel_estourado(fazer_chamado):
    # Arrange: MEDIA = 24h. 25h após a origem -> vencido.
    chamado = fazer_chamado(prioridade=Prioridade.MEDIA, timestamp_origem=ORIGEM)
    ref = ORIGEM + timedelta(hours=25)

    # Act
    status = status_sla(chamado, referencia=ref)

    # Assert
    assert status["nivel"] == "estourado"
    assert status["vencido"] is True
    assert status["tempo_restante_seg"] == pytest.approx(-3600)


def test_status_sla_usa_venc_calculado_quando_nao_persistido(fazer_chamado):
    # sla_venc_em é None mas a avaliação deve derivar on-the-fly sem mutar.
    chamado = fazer_chamado(prioridade=Prioridade.MEDIA, timestamp_origem=ORIGEM)
    assert chamado.sla_venc_em is None

    status = status_sla(chamado, referencia=ORIGEM + timedelta(hours=1))

    assert status["sla_venc_em"] == ORIGEM + timedelta(hours=24)
    assert chamado.sla_venc_em is None  # não mutou


# --------------------------------------------------------------------------- #
# status_sla — resolvido congela a avaliação
# --------------------------------------------------------------------------- #


def test_resolvido_dentro_do_prazo_nunca_aparece_estourado(fazer_chamado):
    # Arrange: resolvido às +2h (dentro das 24h). Avaliado muito depois (+50h).
    chamado = fazer_chamado(
        prioridade=Prioridade.MEDIA, estado=Estado.ABERTO, timestamp_origem=ORIGEM
    )
    transicionar(chamado, Estado.EM_ANDAMENTO, motivo="iniciado")
    transicionar(chamado, Estado.RESOLVIDO, motivo="ok")
    # Força o instante de resolução para um ponto controlado.
    chamado.transicoes[-1].timestamp = ORIGEM + timedelta(hours=2)

    # Act
    status = status_sla(chamado, referencia=ORIGEM + timedelta(hours=50))

    # Assert: avaliação congelada na resolução; nunca estourado.
    assert status["resolvido"] is True
    assert status["nivel"] == "ok"
    assert status["vencido"] is False
    assert status["avaliado_em"] == ORIGEM + timedelta(hours=2)


def test_resolvido_fora_do_prazo_sinaliza_vencido_mas_nivel_ok(fazer_chamado):
    # Arrange: resolvido às +30h (depois das 24h).
    chamado = fazer_chamado(prioridade=Prioridade.MEDIA, timestamp_origem=ORIGEM)
    transicionar(chamado, Estado.EM_ANDAMENTO, motivo="iniciado")
    transicionar(chamado, Estado.RESOLVIDO, motivo="ok")
    chamado.transicoes[-1].timestamp = ORIGEM + timedelta(hours=30)

    # Act
    status = status_sla(chamado, referencia=ORIGEM + timedelta(hours=100))

    # Assert: informa que foi resolvido fora do prazo, mas não conta como
    # estourado em aberto.
    assert status["resolvido"] is True
    assert status["vencido"] is True
    assert status["nivel"] == "ok"


# --------------------------------------------------------------------------- #
# priorizar
# --------------------------------------------------------------------------- #


def test_priorizar_ordena_por_urgencia_e_joga_resolvido_para_o_fim(fazer_chamado):
    # Arrange
    ref = ORIGEM + timedelta(hours=1)

    urgente = fazer_chamado(prioridade=Prioridade.URGENTE, timestamp_origem=ORIGEM)  # 4h
    media = fazer_chamado(prioridade=Prioridade.MEDIA, timestamp_origem=ORIGEM)  # 24h
    baixa = fazer_chamado(prioridade=Prioridade.BAIXA, timestamp_origem=ORIGEM)  # 48h

    resolvido = fazer_chamado(prioridade=Prioridade.URGENTE, timestamp_origem=ORIGEM)
    transicionar(resolvido, Estado.EM_ANDAMENTO, motivo="x")
    transicionar(resolvido, Estado.RESOLVIDO, motivo="x")

    # Act: ordem de entrada embaralhada propositalmente.
    ordenados = priorizar([baixa, resolvido, media, urgente], referencia=ref)

    # Assert: menor tempo restante primeiro; resolvido por último.
    assert ordenados[0] is urgente
    assert ordenados[1] is media
    assert ordenados[2] is baixa
    assert ordenados[3] is resolvido


def test_priorizar_nao_muta_lista_original(fazer_chamado):
    a = fazer_chamado(prioridade=Prioridade.BAIXA, timestamp_origem=ORIGEM)
    b = fazer_chamado(prioridade=Prioridade.URGENTE, timestamp_origem=ORIGEM)
    original = [a, b]

    priorizar(original, referencia=ORIGEM + timedelta(hours=1))

    assert original == [a, b]


# --------------------------------------------------------------------------- #
# fila_do_produto
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("produto", "fila"),
    [
        (Produto.BANCOS, "fila_bancos"),
        (Produto.MODULOS, "fila_modulos"),
        (Produto.SIMULADOS, "fila_simulados"),
    ],
)
def test_fila_do_produto_mapeia_produto(fazer_chamado, produto, fila):
    chamado = fazer_chamado(produto=produto)

    assert fila_do_produto(chamado) == fila


def test_fila_do_produto_none_quando_sem_produto(fazer_chamado):
    chamado = fazer_chamado(produto=None)

    assert fila_do_produto(chamado) is None
