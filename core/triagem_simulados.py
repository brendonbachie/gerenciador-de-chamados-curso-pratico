"""Triagem especializada de chamados de **Simulados Práticos**.

Este módulo implementa a etapa de triagem para o produto *Simulados Práticos*,
seguindo o CONTRATO COMUM compartilhado pelos três produtos (bancos, módulos,
simulados):

- Ponto de entrada único :func:`triar`, que recebe e devolve um
  :class:`~core.models.Chamado` (mutado in-place).
- Confirmação de produto sem adivinhação: em caso de ambiguidade real com
  Bancos ou Módulos, o produto NÃO é forçado — marca-se
  ``flags.produto_ambiguo`` e mantém-se ``produto = None``.
- Extração de campos essenciais para ``chamado.campos_triagem``, com ausências
  SEMPRE explícitas (``None`` ou lista vazia) — nunca se inventa conteúdo.
- Sinalização de qualidade (incompletude, campos faltantes, duplicidade).
- Sugestão de prioridade inicial com heurística documentada.

Princípios herdados de ``CLAUDE.md``:

- Não toca o banco/``repo`` — apenas triagem e normalização.
- Não substitui ``timestamp_origem``.
- Não preenche campos por suposição.

A extração de conteúdo parte de ``chamado.texto_normalizado``; as evidências
consideram também os ``chamado.anexos`` (mídias do WhatsApp).
"""

from __future__ import annotations

import re
import unicodedata

from core.models import (
    Chamado,
    Prioridade,
    Produto,
)

# --------------------------------------------------------------------------- #
# Contrato comum: campos essenciais de triagem
# --------------------------------------------------------------------------- #

#: Campos essenciais esperados na triagem de Simulados Práticos. A ausência de
#: qualquer um destes alimenta ``flags.campos_faltantes`` e ``flags.incompleto``.
#: ``evidencias`` é tratado à parte (lista; não conta como faltante por ser
#: complementar, mas é sempre preenchido explicitamente).
CAMPOS_ESSENCIAIS: tuple[str, ...] = (
    "identificacao_usuario",
    "simulado_citado",
    "questao_ou_etapa",
    "comportamento_esperado",
    "comportamento_observado",
)


# --------------------------------------------------------------------------- #
# Vocabulário de classificação (confirmação de produto)
# --------------------------------------------------------------------------- #

#: Termos que apontam para Simulados Práticos.
_TERMOS_SIMULADOS: frozenset[str] = frozenset(
    {
        "simulado",
        "simulados",
        "prova",
        "provas",
        "gabarito",
        "gabaritos",
        "correcao",
        "resultado",
        "resultados",
        "nota",
        "desempenho",
        "tentativa",
        "cronometro",
        "tempo",
    }
)

#: Termos que apontam para Bancos Práticos (concorrentes na desambiguação).
_TERMOS_BANCOS: frozenset[str] = frozenset(
    {
        "banco",
        "bancos",
    }
)

#: Termos que apontam para Módulos Práticos (concorrentes na desambiguação).
_TERMOS_MODULOS: frozenset[str] = frozenset(
    {
        "modulo",
        "modulos",
        "aula",
        "aulas",
        "videoaula",
        "videoaulas",
    }
)


# --------------------------------------------------------------------------- #
# Vocabulário de extração de campos
# --------------------------------------------------------------------------- #

#: Sinais de que o simulado está em andamento / com resultado comprometido,
#: usados na heurística de prioridade.
_TERMOS_IMPACTO_ALTO: frozenset[str] = frozenset(
    {
        "andamento",
        "durante",
        "fazendo",
        "travou",
        "travado",
        "congelou",
        "perdi",
        "perdeu",
        "sumiu",
        "zerou",
        "zerado",
        "correcao",
        "resultado",
        "nota",
        "gabarito",
        "errado",
        "incorreta",
        "incorreto",
        "nao salvou",
        "nao registrou",
        "nao computou",
    }
)

#: Sinais de urgência temporal (prazo, último dia), elevam a prioridade.
_TERMOS_URGENCIA: frozenset[str] = frozenset(
    {
        "urgente",
        "prazo",
        "hoje",
        "agora",
        "amanha",
        "vencendo",
        "ultimo",
        "fechando",
    }
)

_TOKENS = re.compile(r"[a-z0-9]+")


def _sem_acentos_lower(texto: str) -> str:
    """Retorna o texto em minúsculas e sem acentos (para casar palavras-chave).

    Args:
        texto: Texto de entrada (pode conter acentos e maiúsculas).

    Returns:
        Texto normalizado: minúsculo e sem diacríticos.
    """

    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acentos = "".join(c for c in decomposto if not unicodedata.combining(c))
    return sem_acentos.lower()


def _tokens(texto: str) -> set[str]:
    """Tokeniza o texto em palavras alfanuméricas sem acento.

    Args:
        texto: Texto de origem.

    Returns:
        Conjunto de tokens (minúsculos, sem acento).
    """

    return set(_TOKENS.findall(_sem_acentos_lower(texto)))


# --------------------------------------------------------------------------- #
# Confirmação de produto
# --------------------------------------------------------------------------- #


def _confirmar_produto(chamado: Chamado) -> tuple[Produto | None, bool]:
    """Confirma se o chamado pertence a Simulados Práticos.

    A decisão parte de uma pontuação por palavras-chave sobre
    ``texto_normalizado``. Há ambiguidade real quando Simulados pontua, mas
    Bancos ou Módulos pontuam de forma igual ou superior — nesse caso o produto
    NÃO é forçado.

    Regras:

    - Sem nenhum sinal de Simulados: não confirma (``None``) e não marca
      ambiguidade — é apenas indeterminado para este triador.
    - Simulados pontua e domina os concorrentes: confirma ``SIMULADOS``.
    - Simulados pontua mas empata/perde para um concorrente: ambíguo, devolve
      ``None`` com ``ambiguo = True``.

    Args:
        chamado: Chamado a inspecionar.

    Returns:
        Tupla ``(produto, ambiguo)``: ``produto`` é ``Produto.SIMULADOS`` ou
        ``None``; ``ambiguo`` indica conflito real com outro produto.
    """

    tokens = _tokens(chamado.texto_normalizado)

    pontos_sim = len(tokens & _TERMOS_SIMULADOS)
    pontos_bancos = len(tokens & _TERMOS_BANCOS)
    pontos_modulos = len(tokens & _TERMOS_MODULOS)

    if pontos_sim == 0:
        # Nenhum sinal de Simulados: indeterminado, não é ambiguidade.
        return None, False

    concorrente = max(pontos_bancos, pontos_modulos)
    if concorrente >= pontos_sim:
        # Outro produto empata ou supera: ambiguidade real, não adivinha.
        return None, True

    return Produto.SIMULADOS, False


# --------------------------------------------------------------------------- #
# Extração de campos essenciais
# --------------------------------------------------------------------------- #

#: Rótulos comuns usados pelo atendente/usuário ao estruturar a mensagem.
#: Mapeiam um campo de triagem a uma lista de prefixos reconhecíveis.
_ROTULOS: dict[str, tuple[str, ...]] = {
    "identificacao_usuario": ("usuario", "aluno", "nome", "matricula", "email", "cpf"),
    "simulado_citado": ("simulado", "prova", "exame", "teste"),
    "questao_ou_etapa": ("questao", "etapa", "pergunta", "item", "passo"),
    "comportamento_esperado": ("esperado", "deveria", "esperava"),
    "comportamento_observado": ("observado", "aconteceu", "ocorreu", "erro", "problema"),
}


def _extrair_por_rotulo(texto: str, prefixos: tuple[str, ...]) -> str | None:
    """Extrai o valor de uma linha rotulada do tipo ``Rótulo: valor``.

    Procura, linha a linha, uma cujo início (sem acento, minúsculo) case com um
    dos ``prefixos`` seguido de ``:`` e devolve o conteúdo após os dois-pontos.
    Não infere valor algum quando nenhum rótulo casa.

    Args:
        texto: Texto normalizado do chamado.
        prefixos: Rótulos aceitos para o campo (sem acento, minúsculos).

    Returns:
        O valor após ``:`` (aparado) quando encontrado e não vazio; caso
        contrário, ``None``.
    """

    for linha in texto.split("\n"):
        if ":" not in linha:
            continue
        rotulo_bruto, _, valor = linha.partition(":")
        rotulo = _sem_acentos_lower(rotulo_bruto).strip()
        valor = valor.strip()
        if not valor:
            continue
        if any(rotulo == p or rotulo.startswith(p) for p in prefixos):
            return valor
    return None


def _extrair_simulado_citado(texto: str) -> str | None:
    """Extrai o nome/identificação do simulado citado em texto livre.

    Primeiro tenta um rótulo explícito (``Simulado: ...``). Se não houver, busca
    o padrão ``simulado <identificador>`` em prosa (ex.: ``simulado 03``,
    ``simulado de matemática``). Não inventa: sem casamento, devolve ``None``.

    Args:
        texto: Texto normalizado do chamado.

    Returns:
        Identificação do simulado, ou ``None`` se não for possível extrair.
    """

    rotulado = _extrair_por_rotulo(texto, _ROTULOS["simulado_citado"])
    if rotulado:
        return rotulado

    # ``simulado <algo>`` em prosa — captura número, código ou tema curto.
    padrao = re.compile(
        r"simulad[oa]s?\s+(?:de\s+|da\s+|do\s+|n[º°o]?\.?\s*)?"
        r"([\wçáàâãéêíóôõúü\-]{1,40})",
        re.IGNORECASE,
    )
    m = padrao.search(texto)
    if m:
        candidato = m.group(1).strip(" .,-")
        # Evita capturar palavras genéricas que não identificam o simulado.
        if candidato and _sem_acentos_lower(candidato) not in {
            "esta",
            "este",
            "esse",
            "essa",
            "que",
            "do",
            "da",
            "de",
        }:
            return candidato
    return None


def _extrair_evidencias(chamado: Chamado) -> list[str]:
    """Coleta evidências do chamado a partir dos anexos e de URLs no texto.

    Evidências NÃO são inventadas: derivam de referências reais de mídia
    (``Anexo``) e de URLs presentes no texto. Cada anexo vira uma string
    ``"<tipo>:<referência>"`` legível, preservando apenas metadados (nunca o
    binário).

    Args:
        chamado: Chamado com possíveis anexos e texto normalizado.

    Returns:
        Lista de evidências (possivelmente vazia). Ordem: anexos, depois URLs.
    """

    evidencias: list[str] = []

    for anexo in chamado.anexos:
        ref = anexo.url_ou_ref or anexo.nome or anexo.media_id
        evidencias.append(f"{anexo.tipo}:{ref}")

    # URLs em texto livre também são evidências citadas pelo usuário.
    for url in re.findall(r"https?://\S+", chamado.texto_normalizado):
        url_limpa = url.rstrip(").,;")
        if url_limpa not in evidencias:
            evidencias.append(url_limpa)

    return evidencias


def _extrair_campos(chamado: Chamado) -> dict:
    """Extrai os campos essenciais de Simulados para ``campos_triagem``.

    Cada campo é preenchido apenas quando há evidência textual/anexo real.
    Ausências ficam EXPLÍCITAS: ``None`` para campos escalares e lista vazia
    para ``evidencias``.

    Args:
        chamado: Chamado já normalizado e (idealmente) com produto confirmado.

    Returns:
        Dicionário com as chaves: ``identificacao_usuario``,
        ``simulado_citado``, ``questao_ou_etapa``, ``comportamento_esperado``,
        ``comportamento_observado`` e ``evidencias``.
    """

    texto = chamado.texto_normalizado

    return {
        "identificacao_usuario": _extrair_por_rotulo(
            texto, _ROTULOS["identificacao_usuario"]
        ),
        "simulado_citado": _extrair_simulado_citado(texto),
        "questao_ou_etapa": _extrair_por_rotulo(texto, _ROTULOS["questao_ou_etapa"]),
        "comportamento_esperado": _extrair_por_rotulo(
            texto, _ROTULOS["comportamento_esperado"]
        ),
        "comportamento_observado": _extrair_por_rotulo(
            texto, _ROTULOS["comportamento_observado"]
        ),
        "evidencias": _extrair_evidencias(chamado),
    }


# --------------------------------------------------------------------------- #
# Qualidade: incompletude, campos faltantes e duplicidade
# --------------------------------------------------------------------------- #


def _campos_faltantes(campos: dict) -> list[str]:
    """Lista os campos essenciais ausentes (valor ``None``).

    ``evidencias`` é complementar (lista) e não entra como faltante; apenas os
    campos de :data:`CAMPOS_ESSENCIAIS` são avaliados.

    Args:
        campos: Dicionário de campos já extraído.

    Returns:
        Nomes dos campos essenciais cujo valor é ``None``, em ordem estável.
    """

    return [nome for nome in CAMPOS_ESSENCIAIS if campos.get(nome) is None]


def _marcar_duplicado(chamado: Chamado) -> bool:
    """Gancho simples de detecção de duplicidade.

    GANCHO (documentado): este triador NÃO consulta o repositório (regra de
    arquitetura — triagem não toca o banco). A deduplicação definitiva pertence
    à camada que tem acesso ao histórico (via ``repo``), correlacionando pelo
    ``remetente.hash`` + ``timestamp_origem`` + similaridade de texto.

    Aqui apenas expomos um ponto de extensão e respeitamos uma marcação prévia
    eventualmente feita na normalização: nunca rebaixamos um ``duplicado=True``
    já existente para ``False``.

    Args:
        chamado: Chamado em triagem.

    Returns:
        O valor atual de ``chamado.flags.duplicado`` (preservado).
    """

    # Sem acesso ao histórico, mantém-se a marcação existente sem inventar.
    return chamado.flags.duplicado


# --------------------------------------------------------------------------- #
# Heurística de prioridade
# --------------------------------------------------------------------------- #


def _sugerir_prioridade(chamado: Chamado, campos: dict) -> Prioridade:
    """Sugere a prioridade inicial do chamado de Simulados.

    Heurística (impacto x urgência), do mais grave ao mais leve:

    - **URGENTE**: há sinal de impacto alto (simulado em andamento, resultado/
      correção/gabarito comprometido, nota não computada) E sinal de urgência
      temporal (prazo, hoje, vencendo). É o pior caso: o usuário pode perder a
      janela do simulado.
    - **ALTA**: há sinal de impacto alto OU urgência temporal — resultado/
      correção em risco, ou prazo curto, isoladamente.
    - **MEDIA**: chamado de Simulados sem sinais de impacto/urgência (default
      neutro), ou chamado incompleto demais para avaliar o impacto (faltam os
      essenciais) — evita superpriorizar sem informação.
    - **BAIXA**: reservada a dúvidas/relatos sem comportamento observado nem
      esperado e sem qualquer sinal de impacto — baixo risco operacional.

    Args:
        chamado: Chamado em triagem (usa ``texto_normalizado``).
        campos: Campos essenciais já extraídos (para medir completude/impacto).

    Returns:
        A :class:`~core.models.Prioridade` sugerida.
    """

    tokens = _tokens(chamado.texto_normalizado)
    texto_normalizado = _sem_acentos_lower(chamado.texto_normalizado)

    def tem(termos: frozenset[str]) -> bool:
        # Casa por token e também por expressões com espaço (ex.: "nao salvou").
        if tokens & termos:
            return True
        return any(" " in t and t in texto_normalizado for t in termos)

    impacto_alto = tem(_TERMOS_IMPACTO_ALTO)
    urgencia = tem(_TERMOS_URGENCIA)

    if impacto_alto and urgencia:
        return Prioridade.URGENTE
    if impacto_alto or urgencia:
        return Prioridade.ALTA

    # Sem sinais fortes: distingue dúvida leve de relato neutro.
    sem_relato = (
        campos.get("comportamento_observado") is None
        and campos.get("comportamento_esperado") is None
    )
    if sem_relato:
        return Prioridade.BAIXA
    return Prioridade.MEDIA


# --------------------------------------------------------------------------- #
# Ponto de entrada do contrato comum
# --------------------------------------------------------------------------- #


def triar(chamado: Chamado) -> Chamado:
    """Tria um chamado de **Simulados Práticos** (contrato comum).

    Executa, em ordem, as etapas do contrato compartilhado pelos três produtos:

    1. **Confirmação de produto**: confirma ``Produto.SIMULADOS`` ou, havendo
       ambiguidade real com Bancos/Módulos, mantém ``produto = None`` e marca
       ``flags.produto_ambiguo = True`` (não força a escolha).
    2. **Extração de campos** essenciais para ``campos_triagem``, com ausências
       explícitas (``None`` / lista vazia) — nunca inventa dados.
    3. **Qualidade**: preenche ``flags.campos_faltantes`` e
       ``flags.incompleto``, e aplica o gancho de ``flags.duplicado``.
    4. **Prioridade**: sugere ``chamado.prioridade`` por impacto x urgência (ver
       :func:`_sugerir_prioridade`).

    A função muta o chamado in-place e o devolve. Não persiste nem toca o banco
    (responsabilidade de ``core/repo.py``) e não altera ``timestamp_origem``.

    Args:
        chamado: Chamado já normalizado (saída de ``core/normalizer.py``).

    Returns:
        O mesmo ``chamado``, com ``produto``, ``campos_triagem``, ``flags`` e
        ``prioridade`` atualizados pela triagem de Simulados.
    """

    # 1. Confirmação de produto (sem adivinhação).
    produto, ambiguo = _confirmar_produto(chamado)
    if ambiguo:
        chamado.produto = None
        chamado.flags.produto_ambiguo = True
    elif produto is not None:
        chamado.produto = Produto.SIMULADOS
        chamado.flags.produto_ambiguo = False

    # 2. Extração dos campos essenciais (ausências explícitas).
    campos = _extrair_campos(chamado)
    chamado.campos_triagem = campos

    # 3. Qualidade: faltantes, incompletude e duplicidade.
    faltantes = _campos_faltantes(campos)
    chamado.flags.campos_faltantes = faltantes
    chamado.flags.incompleto = bool(faltantes)
    chamado.flags.duplicado = _marcar_duplicado(chamado)

    # 4. Prioridade sugerida (impacto x urgência).
    chamado.prioridade = _sugerir_prioridade(chamado, campos)

    return chamado
