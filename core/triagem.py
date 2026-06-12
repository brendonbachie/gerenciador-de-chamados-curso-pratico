"""Dispatcher de triagem por produto.

Roteia um chamado para a triagem especializada do produto correto
(``bancos`` / ``modulos`` / ``simulados``). Quando o produto ainda não está
definido, consulta os classificadores especializados e só roteia se houver um
vencedor claro — em caso de ambiguidade, marca o chamado para reclassificação
em vez de adivinhar.

Este módulo não toca o banco; persistência é responsabilidade de
``core/repo.py``. A triagem apenas estrutura campos e flags do chamado.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

from core import triagem_bancos, triagem_modulos, triagem_simulados
from core.models import Chamado, Produto

#: Mapa produto -> função de triagem especializada (contrato ``triar``).
TRIAGEM_POR_PRODUTO: dict[Produto, Callable[[Chamado], Chamado]] = {
    Produto.BANCOS: triagem_bancos.triar,
    Produto.MODULOS: triagem_modulos.triar,
    Produto.SIMULADOS: triagem_simulados.triar,
}


def triar(chamado: Chamado) -> Chamado:
    """Tria um chamado, roteando para o especialista do produto.

    Fluxo:
        1. Se ``chamado.produto`` já está definido, delega direto ao
           especialista correspondente.
        2. Se ``produto`` é ``None``, executa cada classificador especializado
           sobre uma cópia para descobrir quem reivindica o chamado:
           - exatamente um confirma  → aplica a triagem desse produto;
           - nenhum ou mais de um    → deixa ``produto = None`` e marca
             ``flags.produto_ambiguo`` para reclassificação manual.

    Args:
        chamado: Chamado normalizado (tipicamente vindo do ``normalizer``).

    Returns:
        O mesmo chamado, mutado in-place, com ``campos_triagem``, ``flags`` e
        (quando aplicável) ``produto``/``prioridade`` preenchidos.
    """

    if chamado.produto is not None:
        return TRIAGEM_POR_PRODUTO[chamado.produto](chamado)

    confirmados = _classificar(chamado)

    if len(confirmados) == 1:
        produto = confirmados[0]
        return TRIAGEM_POR_PRODUTO[produto](chamado)

    # Nenhum especialista confirmou (indeterminado) ou mais de um confirmou
    # (conflito): em ambos os casos não adivinhamos o produto.
    chamado.produto = None
    chamado.flags.produto_ambiguo = len(confirmados) > 1
    if not confirmados:
        chamado.flags.incompleto = True
        if "produto" not in chamado.flags.campos_faltantes:
            chamado.flags.campos_faltantes.append("produto")
    return chamado


def _classificar(chamado: Chamado) -> list[Produto]:
    """Descobre quais produtos reivindicam o chamado, sem mutá-lo.

    Executa cada triagem especializada sobre uma cópia profunda e coleta os
    produtos cujo especialista confirmou a posse (ou seja, setou o próprio
    ``produto`` na cópia).

    Args:
        chamado: Chamado a classificar.

    Returns:
        Lista de produtos que confirmaram o chamado (0, 1 ou mais).
    """

    confirmados: list[Produto] = []
    for produto, triar_produto in TRIAGEM_POR_PRODUTO.items():
        sonda = triar_produto(copy.deepcopy(chamado))
        if sonda.produto == produto:
            confirmados.append(produto)
    return confirmados
