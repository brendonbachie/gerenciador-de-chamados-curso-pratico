"""Testes do dispatcher de triagem (``core/triagem``).

Cobre o roteamento por produto já definido, a classificação automática quando o
produto é ``None`` e o tratamento de ambiguidade/indeterminação (produto fica
``None``, sem adivinhação).
"""

from __future__ import annotations

import pytest

from core import triagem
from core.models import Produto

# Textos com sinal forte e único de cada produto (sem competidores empatando).
TEXTO_BANCOS = "a questao do banco esta com o enunciado errado na alternativa"
TEXTO_MODULOS = "nao consigo assistir a videoaula do modulo de hoje"
TEXTO_SIMULADOS = "meu simulado travou durante a prova e perdi o resultado"


# --------------------------------------------------------------------------- #
# Roteamento com produto já definido
# --------------------------------------------------------------------------- #


def test_dispatcher_roteia_produto_já_definido(fazer_chamado):
    # Arrange: produto definido e texto coerente com bancos.
    chamado = fazer_chamado(texto=TEXTO_BANCOS, produto=Produto.BANCOS)

    # Act
    resultado = triagem.triar(chamado)

    # Assert: delegou ao especialista de bancos (preencheu campos essenciais).
    assert resultado.produto is Produto.BANCOS
    assert "banco_citado" in resultado.campos_triagem
    assert "comportamento_observado" in resultado.campos_triagem


# --------------------------------------------------------------------------- #
# Classificação automática quando produto é None
# --------------------------------------------------------------------------- #


def test_dispatcher_classifica_quando_produto_none_bancos(fazer_chamado):
    chamado = fazer_chamado(texto=TEXTO_BANCOS, produto=None)

    resultado = triagem.triar(chamado)

    assert resultado.produto is Produto.BANCOS
    assert resultado.flags.produto_ambiguo is False


def test_dispatcher_classifica_quando_produto_none_modulos(fazer_chamado):
    chamado = fazer_chamado(texto=TEXTO_MODULOS, produto=None)

    resultado = triagem.triar(chamado)

    assert resultado.produto is Produto.MODULOS


def test_dispatcher_classifica_quando_produto_none_simulados(fazer_chamado):
    chamado = fazer_chamado(texto=TEXTO_SIMULADOS, produto=None)

    resultado = triagem.triar(chamado)

    assert resultado.produto is Produto.SIMULADOS


def test_chamado_de_bancos_confirma_bancos_e_extrai_campos(fazer_chamado):
    # Arrange
    chamado = fazer_chamado(texto=TEXTO_BANCOS, produto=None)

    # Act
    resultado = triagem.triar(chamado)

    # Assert: produto confirmado e o conjunto de campos essenciais presente.
    assert resultado.produto is Produto.BANCOS
    assert set(resultado.campos_triagem) == {
        "identificacao_usuario",
        "banco_citado",
        "comportamento_esperado",
        "comportamento_observado",
        "evidencias",
    }


# --------------------------------------------------------------------------- #
# Ambiguidade / indeterminação — não adivinha o produto
# --------------------------------------------------------------------------- #


def test_dispatcher_indeterminado_deixa_produto_none_e_marca_faltante(fazer_chamado):
    # Arrange: nenhum sinal de produto.
    chamado = fazer_chamado(texto="bom dia, tudo bem com voces?", produto=None)

    # Act
    resultado = triagem.triar(chamado)

    # Assert: indeterminado -> não confirma; "produto" fica explícito como
    # faltante e não se marca ambiguidade (não houve conflito).
    assert resultado.produto is None
    assert resultado.flags.incompleto is True
    assert "produto" in resultado.flags.campos_faltantes
    assert resultado.flags.produto_ambiguo is False


def test_dispatcher_sinais_equilibrados_deixa_produto_none(fazer_chamado):
    # Arrange: bancos x simulados empatados (1x1) -> nenhum especialista
    # confirma com dominância estrita.
    chamado = fazer_chamado(texto="o banco e o simulado", produto=None)

    # Act
    resultado = triagem.triar(chamado)

    # Assert: na dúvida, não adivinha; produto fica None para reclassificação.
    assert resultado.produto is None
    assert "produto" in resultado.flags.campos_faltantes


def test_dispatcher_não_marca_produto_quando_classificação_falha(fazer_chamado):
    # Reforça o invariante central: sem vencedor claro, produto permanece None.
    chamado = fazer_chamado(texto="o banco e o simulado", produto=None)

    resultado = triagem.triar(chamado)

    assert resultado.produto not in (Produto.BANCOS, Produto.SIMULADOS, Produto.MODULOS)


# --------------------------------------------------------------------------- #
# Idempotência leve: triar duas vezes um produto definido mantém o produto
# --------------------------------------------------------------------------- #


def test_triar_duas_vezes_mantem_produto_coerente(fazer_chamado):
    chamado = fazer_chamado(texto=TEXTO_SIMULADOS, produto=None)

    primeiro = triagem.triar(chamado)
    assert primeiro.produto is Produto.SIMULADOS

    # Com o produto agora definido, roteia direto ao especialista e mantém.
    segundo = triagem.triar(primeiro)
    assert segundo.produto is Produto.SIMULADOS


@pytest.mark.parametrize(
    ("texto", "produto"),
    [
        (TEXTO_BANCOS, Produto.BANCOS),
        (TEXTO_MODULOS, Produto.MODULOS),
        (TEXTO_SIMULADOS, Produto.SIMULADOS),
    ],
)
def test_dispatcher_roteamento_parametrizado(fazer_chamado, texto, produto):
    chamado = fazer_chamado(texto=texto, produto=None)

    resultado = triagem.triar(chamado)

    assert resultado.produto is produto
