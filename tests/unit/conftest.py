"""Fixtures reutilizáveis para os testes unitários.

Tudo aqui é determinístico: timestamps são fixos e o controle de tempo nos
testes de SLA é feito sempre via parâmetro ``referencia``/``timestamp_origem``,
nunca dependendo de ``datetime.now``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.models import (
    Chamado,
    Estado,
    Prioridade,
    Produto,
    Remetente,
)

#: Instante de origem fixo usado como âncora temporal em toda a suíte.
TS_ORIGEM = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def ts_origem() -> datetime:
    """Timestamp de origem fixo (UTC) para SLA determinístico."""

    return TS_ORIGEM


@pytest.fixture
def remetente() -> Remetente:
    """Remetente anonimizado de referência (sufixo ``1234``)."""

    return Remetente.de_numero("+55 11 99999-1234")


@pytest.fixture
def fazer_chamado(remetente: Remetente, ts_origem: datetime):
    """Fábrica de ``Chamado`` com defaults sensatos e overrides explícitos.

    Mantém o ``timestamp_origem`` fixo (origem confiável) por padrão e permite
    sobrescrever qualquer campo relevante para o cenário do teste.
    """

    def _fazer(
        *,
        texto: str = "tenho uma duvida sobre o conteudo",
        produto: Produto | None = None,
        estado: Estado = Estado.ABERTO,
        prioridade: Prioridade = Prioridade.MEDIA,
        timestamp_origem: datetime | None = None,
    ) -> Chamado:
        return Chamado(
            remetente=remetente,
            timestamp_origem=timestamp_origem or ts_origem,
            texto_normalizado=texto,
            texto_bruto=texto,
            produto=produto,
            estado=estado,
            prioridade=prioridade,
        )

    return _fazer
