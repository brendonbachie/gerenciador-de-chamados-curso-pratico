"""Testes do modelo de domínio: ``Remetente`` e cálculo de SLA.

Foco em dado sensível (número nunca em claro, hash determinístico, sufixo) e na
derivação do vencimento de SLA por prioridade a partir do ``timestamp_origem``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.models import (
    SLA_POR_PRIORIDADE,
    Chamado,
    Prioridade,
    Remetente,
)

# --------------------------------------------------------------------------- #
# Remetente.de_numero — tratamento de dado sensível
# --------------------------------------------------------------------------- #


def test_de_numero_hash_determinístico_para_mesmo_número():
    # Arrange / Act
    a = Remetente.de_numero("+55 11 99999-1234")
    b = Remetente.de_numero("5511999991234")  # mesmo número, formatação diferente

    # Assert: a normalização para dígitos torna o hash idêntico.
    assert a.hash == b.hash


def test_de_numero_hash_difere_entre_números_distintos():
    a = Remetente.de_numero("5511999991234")
    b = Remetente.de_numero("5511999995678")

    assert a.hash != b.hash


def test_de_numero_não_expõe_número_em_claro_no_hash():
    # Arrange
    numero = "5511999991234"

    # Act
    r = Remetente.de_numero(numero)

    # Assert: nenhum dígito do número em claro aparece no hash (SHA-256 hex).
    assert numero not in r.hash
    assert "99999" not in r.hash
    # Hash SHA-256 em hexadecimal tem 64 caracteres.
    assert len(r.hash) == 64


def test_de_numero_sufixo_são_os_últimos_quatro_dígitos():
    r = Remetente.de_numero("+55 (11) 98888-4321")

    assert r.sufixo == "4321"
    assert r.mascarado() == "***4321"


def test_de_numero_com_menos_de_quatro_dígitos_usa_o_que_houver():
    r = Remetente.de_numero("12")

    assert r.sufixo == "12"
    assert r.mascarado() == "***12"


def test_de_numero_sem_dígitos_resulta_em_sufixo_vazio():
    r = Remetente.de_numero("sem-numero")

    assert r.sufixo == ""
    # Sem sufixo, a máscara não expõe nada além do prefixo de ofuscação.
    assert r.mascarado() == "***"


def test_de_numero_ignora_pepper_no_sufixo_mas_usa_no_hash(monkeypatch):
    # Arrange: peppers distintos produzem hashes distintos para o mesmo número.
    monkeypatch.setenv("CHAMADOS_PEPPER", "pepper-A")
    h_a = Remetente.de_numero("5511999991234").hash

    monkeypatch.setenv("CHAMADOS_PEPPER", "pepper-B")
    h_b = Remetente.de_numero("5511999991234").hash

    # Assert
    assert h_a != h_b


# --------------------------------------------------------------------------- #
# Chamado.calcular_sla — vencimento por prioridade
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("prioridade", "horas"),
    [
        (Prioridade.URGENTE, 4),
        (Prioridade.ALTA, 8),
        (Prioridade.MEDIA, 24),
        (Prioridade.BAIXA, 48),
    ],
)
def test_calcular_sla_por_prioridade(remetente, prioridade, horas):
    # Arrange
    origem = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
    chamado = Chamado(
        remetente=remetente,
        timestamp_origem=origem,
        texto_normalizado="x",
        texto_bruto="x",
        prioridade=prioridade,
    )

    # Act
    venc = chamado.calcular_sla()

    # Assert: parte do timestamp_origem (não do criado_em).
    assert venc == origem + timedelta(hours=horas)


def test_calcular_sla_parte_do_timestamp_origem_não_do_criado_em(remetente):
    # Arrange: criado_em bem depois da origem.
    origem = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
    criado = datetime(2026, 6, 11, 20, 0, 0, tzinfo=UTC)
    chamado = Chamado(
        remetente=remetente,
        timestamp_origem=origem,
        texto_normalizado="x",
        texto_bruto="x",
        prioridade=Prioridade.MEDIA,
        criado_em=criado,
    )

    # Act / Assert: SLA ancorado na origem, ignorando o horário de processamento.
    assert chamado.calcular_sla() == origem + timedelta(hours=24)


def test_tabela_sla_cobre_todas_as_prioridades():
    # Garante que nenhuma prioridade fica sem prazo definido.
    assert set(SLA_POR_PRIORIDADE) == set(Prioridade)
