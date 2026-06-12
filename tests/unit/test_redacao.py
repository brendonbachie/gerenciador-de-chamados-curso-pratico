"""Testes da redação de PII no ``texto_normalizado`` (core/normalizer)."""

from __future__ import annotations

import pytest

from core.normalizer import REDIGIDO, normalizar_texto_colado

TS = "2026-06-12T09:00:00+00:00"
REMETENTE = "+55 11 99999-0000"


def _norm(texto: str) -> str:
    chamado = normalizar_texto_colado(texto=texto, remetente=REMETENTE, timestamp_origem=TS)
    return chamado.texto_normalizado


@pytest.mark.parametrize(
    "trecho",
    [
        "joao.silva@gmail.com",
        "aluno+curso@dominio.com.br",
        "123.456.789-01",  # CPF formatado
        "12.345.678/0001-99",  # CNPJ formatado
        "+55 11 97777-4321",  # telefone com DDI/DDD
        "(21) 98888-1212",  # telefone com parênteses
        "11987654321",  # telefone sem formatação
        "12345678901",  # CPF sem formatação
    ],
)
def test_pii_e_redigida(trecho: str):
    resultado = _norm(f"contato do aluno: {trecho} obrigado")
    assert trecho not in resultado
    assert REDIGIDO in resultado


@pytest.mark.parametrize(
    "texto",
    [
        "erro 500 ao abrir",
        "questao 14 do simulado",
        "modulo 2 aula 3",
        "prova em 2026",
    ],
)
def test_numeros_curtos_nao_sao_redigidos(texto: str):
    # Números curtos (< 8 dígitos) não são PII e devem ser preservados.
    assert _norm(texto) == texto
    assert REDIGIDO not in _norm(texto)


def test_texto_bruto_preserva_original_para_auditoria():
    texto = "meu cpf e 123.456.789-01"
    chamado = normalizar_texto_colado(texto=texto, remetente=REMETENTE, timestamp_origem=TS)
    # Original cru preservado; versão exibível redigida.
    assert chamado.texto_bruto == texto
    assert "123.456.789-01" not in chamado.texto_normalizado
    assert REDIGIDO in chamado.texto_normalizado


def test_multiplas_ocorrencias_na_mesma_mensagem():
    resultado = _norm("ligo no (11) 98888-7777 ou mando email para ana@x.com")
    assert resultado.count(REDIGIDO) == 2
    assert "98888-7777" not in resultado
    assert "ana@x.com" not in resultado
