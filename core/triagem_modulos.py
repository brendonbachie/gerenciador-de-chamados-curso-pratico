"""Triagem especializada de chamados de **Módulos Práticos**.

Implementa o CONTRATO COMUM de triagem compartilhado pelos três produtos
(bancos, módulos, simulados): a função pública :func:`triar` recebe um
``Chamado`` já normalizado e o muta in-place, confirmando (ou não) o produto,
extraindo os campos essenciais para ``campos_triagem``, preenchendo as
``FlagsQualidade`` e sugerindo uma prioridade inicial.

Princípios (ver ``CLAUDE.md``):

- **Nada de suposição.** Campos não encontrados ficam EXPLÍCITOS como ``None``
  (ou lista vazia, no caso de ``evidencias``) — nunca inventados.
- **Ambiguidade não força produto.** Se o texto pesa também para Bancos ou
  Simulados, marca-se ``flags.produto_ambiguo`` e deixa-se ``produto = None``
  para reclassificação, em vez de adivinhar.
- **Sem I/O.** Este módulo não toca o banco/repo; apenas transforma o chamado.

A extração é deliberadamente baseada em palavras-chave e regex simples sobre
``chamado.texto_normalizado``; serve para estruturar e roteirizar o chamado, não
para resolvê-lo tecnicamente.
"""

from __future__ import annotations

import re
import unicodedata

from core.models import (
    Anexo,
    Chamado,
    FlagsQualidade,
    Prioridade,
    Produto,
)

# --------------------------------------------------------------------------- #
# Campos essenciais do contrato de Módulos Práticos
# --------------------------------------------------------------------------- #

#: Nomes dos campos essenciais extraídos para ``chamado.campos_triagem``.
#: ``evidencias`` é o único campo de coleção (lista); os demais são escalares.
#: A ordem aqui é a ordem reportada em ``flags.campos_faltantes``.
CAMPOS_ESSENCIAIS: tuple[str, ...] = (
    "identificacao_usuario",
    "modulo_citado",
    "etapa_ou_aula",
    "comportamento_esperado",
    "comportamento_observado",
    "evidencias",
)


# --------------------------------------------------------------------------- #
# Vocabulário de classificação de produto
# --------------------------------------------------------------------------- #

#: Sinais (sem acento, minúsculo) que indicam Módulos Práticos.
_SINAIS_MODULOS: frozenset[str] = frozenset(
    {"modulo", "modulos", "aula", "aulas", "videoaula", "videoaulas", "licao", "licoes"}
)

#: Sinais que indicam Bancos Práticos — usados só para detectar ambiguidade.
_SINAIS_BANCOS: frozenset[str] = frozenset(
    {"banco", "bancos", "questao", "questoes"}
)

#: Sinais que indicam Simulados Práticos — usados só para detectar ambiguidade.
_SINAIS_SIMULADOS: frozenset[str] = frozenset(
    {"simulado", "simulados", "prova", "gabarito"}
)

_TOKENS = re.compile(r"[a-z0-9]+")


def _sem_acentos_lower(texto: str) -> str:
    """Retorna o texto em minúsculas e sem acentos (para casar palavras-chave).

    Args:
        texto: Texto de entrada (pode conter acentuação e maiúsculas).

    Returns:
        Texto normalizado para comparação por palavra-chave.
    """

    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acentos = "".join(c for c in decomposto if not unicodedata.combining(c))
    return sem_acentos.lower()


def _tokens(texto: str) -> set[str]:
    """Tokeniza o texto em palavras alfanuméricas sem acento.

    Args:
        texto: Texto de entrada.

    Returns:
        Conjunto de tokens em minúsculas, sem acentos.
    """

    return set(_TOKENS.findall(_sem_acentos_lower(texto)))


# --------------------------------------------------------------------------- #
# Confirmação de produto
# --------------------------------------------------------------------------- #


def _confirmar_produto(chamado: Chamado) -> tuple[Produto | None, bool]:
    """Confirma se o chamado é de Módulos Práticos, detectando ambiguidade.

    Estratégia: conta quantos sinais (tokens distintos) de cada produto
    aparecem no texto normalizado e compara a força de Módulos com a do
    concorrente mais forte (Bancos ou Simulados).

    - Sem nenhum sinal de Módulos: não é deste produto (``None``, sem
      ambiguidade) — fica para outra triagem/reclassificação.
    - Módulos estritamente mais forte que qualquer concorrente: confirma
      ``MODULOS``. Isso evita falso-ambíguo quando um termo isolado de outro
      produto aparece de passagem (ex.: "prova amanhã" num chamado de acesso ao
      módulo).
    - Concorrente com força igual ou maior à de Módulos: ambiguidade real; não
      adivinha — devolve ``None`` com ``ambiguo=True`` para reclassificação.

    Args:
        chamado: Chamado já normalizado a avaliar.

    Returns:
        Tupla ``(produto, ambiguo)``. ``produto`` é ``Produto.MODULOS`` quando
        confirmado, ou ``None`` quando indeterminado/ambíguo. ``ambiguo`` é
        ``True`` apenas quando um concorrente empata ou supera Módulos.
    """

    tokens = _tokens(chamado.texto_normalizado)

    forca_modulos = len(tokens & _SINAIS_MODULOS)
    if forca_modulos == 0:
        # Nenhum sinal de Módulos: indeterminado para esta triagem (não ambíguo).
        return None, False

    forca_concorrente = max(
        len(tokens & _SINAIS_BANCOS),
        len(tokens & _SINAIS_SIMULADOS),
    )

    if forca_concorrente >= forca_modulos:
        # Empate ou concorrente mais forte: não adivinha, marca ambiguidade.
        return None, True

    return Produto.MODULOS, False


# --------------------------------------------------------------------------- #
# Extração dos campos essenciais
# --------------------------------------------------------------------------- #

#: Captura o nome/número do módulo citado (ex.: "módulo 3", "modulo de redação").
_RE_MODULO = re.compile(
    r"\bm[oó]dulo\s+(?:de\s+)?([\wçãõáéíóúâêô]+(?:\s+[\wçãõáéíóúâêô]+)?)",
    re.IGNORECASE,
)

#: Captura a etapa/aula citada (ex.: "aula 5", "lição 2", "etapa final").
_RE_ETAPA = re.compile(
    r"\b(?:aula|li[cç][aã]o|etapa|videoaula)\s+(?:de\s+)?([\wçãõáéíóúâêô]+)",
    re.IGNORECASE,
)

#: Marcadores que costumam introduzir a identificação do usuário/aluno.
_RE_IDENTIFICACAO = re.compile(
    r"\b(?:aluno|aluna|usu[aá]rio|matr[ií]cula|cpf|e-?mail|login)\b[:\s-]*"
    r"([^\n,.;]{2,80})",
    re.IGNORECASE,
)

#: Marcadores de comportamento ESPERADO (o que deveria acontecer).
_RE_ESPERADO = re.compile(
    r"\b(?:deveria|esperava|esperado|deveria\s+(?:abrir|aparecer|carregar|"
    r"liberar)|deveria ter)\b[:\s-]*([^\n.;]{3,140})",
    re.IGNORECASE,
)

#: Marcadores de comportamento OBSERVADO (o que de fato aconteceu / erro).
_RE_OBSERVADO = re.compile(
    r"\b(?:n[aã]o\s+(?:abre|carrega|aparece|libera|consigo|consegue)|"
    r"erro|trava|travou|bloqueado|bloqueada|aparece|apresenta|mostra)\b"
    r"[:\s-]*([^\n.;]{0,140})",
    re.IGNORECASE,
)


def _primeiro_grupo(padrao: re.Pattern[str], texto: str) -> str | None:
    """Aplica um regex e devolve o primeiro grupo capturado, se houver.

    Args:
        padrao: Expressão regular com ao menos um grupo de captura.
        texto: Texto onde buscar.

    Returns:
        Conteúdo do primeiro grupo (aparado), ou ``None`` se não houver match
        ou se o grupo ficar vazio após o ``strip``.
    """

    match = padrao.search(texto)
    if not match:
        return None
    valor = (match.group(1) or "").strip(" .,:;-")
    return valor or None


def _extrair_evidencias(texto: str, anexos: list[Anexo]) -> list[str]:
    """Coleta evidências do chamado a partir de anexos e links no texto.

    Não infere nada: cada evidência corresponde a um anexo real (referenciado
    por ``media_id``/tipo) ou a uma URL explicitamente presente no texto.

    Args:
        texto: Texto normalizado do chamado.
        anexos: Anexos já extraídos pelo normalizador.

    Returns:
        Lista de evidências como strings legíveis. Vazia quando não há nenhuma.
    """

    evidencias: list[str] = []
    for anexo in anexos:
        ref = anexo.url_ou_ref or anexo.media_id
        nome = f" ({anexo.nome})" if anexo.nome else ""
        evidencias.append(f"anexo:{anexo.tipo}:{ref}{nome}")

    for url in re.findall(r"https?://\S+", texto):
        evidencias.append(f"link:{url}")

    return evidencias


def _extrair_campos(chamado: Chamado) -> dict:
    """Extrai os campos essenciais de Módulos para ``campos_triagem``.

    Todos os campos do contrato são sempre incluídos no dicionário; os que não
    puderem ser extraídos ficam EXPLÍCITOS como ``None`` (ou ``[]`` para
    ``evidencias``). Nada é preenchido por suposição.

    Args:
        chamado: Chamado já normalizado a inspecionar.

    Returns:
        Dicionário com exatamente as chaves de :data:`CAMPOS_ESSENCIAIS`.
    """

    texto = chamado.texto_normalizado

    return {
        "identificacao_usuario": _primeiro_grupo(_RE_IDENTIFICACAO, texto),
        "modulo_citado": _primeiro_grupo(_RE_MODULO, texto),
        "etapa_ou_aula": _primeiro_grupo(_RE_ETAPA, texto),
        "comportamento_esperado": _primeiro_grupo(_RE_ESPERADO, texto),
        "comportamento_observado": _primeiro_grupo(_RE_OBSERVADO, texto),
        "evidencias": _extrair_evidencias(texto, chamado.anexos),
    }


def _campos_faltantes(campos: dict) -> list[str]:
    """Lista, na ordem do contrato, os campos essenciais ausentes.

    Um campo escalar é considerado ausente quando ``None``; ``evidencias`` é
    considerado ausente quando a lista está vazia.

    Args:
        campos: Dicionário produzido por :func:`_extrair_campos`.

    Returns:
        Nomes dos campos essenciais ausentes, preservando a ordem de
        :data:`CAMPOS_ESSENCIAIS`.
    """

    faltantes: list[str] = []
    for nome in CAMPOS_ESSENCIAIS:
        valor = campos.get(nome)
        if nome == "evidencias":
            if not valor:
                faltantes.append(nome)
        elif valor is None:
            faltantes.append(nome)
    return faltantes


# --------------------------------------------------------------------------- #
# Dedup (gancho simples)
# --------------------------------------------------------------------------- #


def chave_dedup(chamado: Chamado) -> str:
    """Gera uma chave estável de deduplicação para o chamado.

    Gancho simples e documentado: combina o ``hash`` do remetente (já
    anonimizado) com o ``timestamp_origem`` em segundos e um recorte do texto
    normalizado. A intenção é detectar o MESMO chamado registrado duas vezes
    (ex.: webhook + registro manual da mesma mensagem), não chamados parecidos.

    A decisão final de duplicidade depende do repositório (comparar esta chave
    com chamados já gravados). Aqui só se produz a chave; a triagem ainda não
    tem acesso ao banco, portanto ``flags.duplicado`` permanece ``False`` por
    padrão e deve ser confirmado pela camada de persistência/fluxo.

    Args:
        chamado: Chamado a identificar.

    Returns:
        Chave determinística no formato ``"<hash>:<epoch>:<trecho>"``.
    """

    epoch = int(chamado.timestamp_origem.timestamp())
    trecho = _sem_acentos_lower(chamado.texto_normalizado)[:64]
    return f"{chamado.remetente.hash}:{epoch}:{trecho}"


# --------------------------------------------------------------------------- #
# Prioridade inicial
# --------------------------------------------------------------------------- #

#: Termos que indicam BLOQUEIO total de acesso ao módulo (impacto máximo).
_TERMOS_BLOQUEIO: frozenset[str] = frozenset(
    {
        "bloqueado",
        "bloqueada",
        "sem acesso",
        "nao acesso",
        "nao consigo acessar",
        "nao abre",
        "nao carrega",
        "acesso negado",
        "expirou",
        "travado",
    }
)

#: Termos de urgência declarada pelo usuário (sobe um patamar).
_TERMOS_URGENCIA: frozenset[str] = frozenset(
    {"urgente", "prova amanha", "prazo", "hoje", "agora", "imediato"}
)

#: Termos de impacto parcial (degradação, mas com acesso ao módulo).
_TERMOS_PARCIAL: frozenset[str] = frozenset(
    {"lento", "travando", "as vezes", "intermitente", "audio", "legenda"}
)


def _contem(texto_sem_acento: str, termos: frozenset[str]) -> bool:
    """Indica se algum dos termos aparece no texto (já sem acento/minúsculo).

    Args:
        texto_sem_acento: Texto normalizado por :func:`_sem_acentos_lower`.
        termos: Conjunto de termos/expressões a procurar.

    Returns:
        ``True`` se ao menos um termo for substring do texto.
    """

    return any(termo in texto_sem_acento for termo in termos)


def _sugerir_prioridade(chamado: Chamado) -> Prioridade:
    """Sugere a prioridade inicial com base em impacto e urgência.

    Heurística (do maior para o menor impacto):

    - **URGENTE**: bloqueio total de acesso ao módulo E urgência declarada
      (ex.: "não consigo acessar o módulo, prova amanhã").
    - **ALTA**: bloqueio total de acesso ao módulo (sem urgência declarada) —
      o aluno está impedido de avançar.
    - **MEDIA** (padrão): impacto parcial/degradação com acesso preservado
      (ex.: aula lenta, áudio falhando) ou impacto não identificável.
    - **BAIXA**: chamado sem conteúdo acionável (marcado incompleto) e sem
      qualquer sinal de impacto — provável dúvida/registro vago.

    Args:
        chamado: Chamado já normalizado e (idealmente) com campos extraídos.

    Returns:
        Prioridade sugerida. Não recalcula SLA — isso é responsabilidade de
        ``core/fluxo.recalcular_sla`` após a triagem.
    """

    texto = _sem_acentos_lower(chamado.texto_normalizado)

    bloqueio = _contem(texto, _TERMOS_BLOQUEIO)
    urgencia = _contem(texto, _TERMOS_URGENCIA)
    parcial = _contem(texto, _TERMOS_PARCIAL)

    if bloqueio and urgencia:
        return Prioridade.URGENTE
    if bloqueio:
        return Prioridade.ALTA
    if parcial or urgencia:
        return Prioridade.MEDIA
    if not chamado.texto_normalizado.strip():
        return Prioridade.BAIXA
    return Prioridade.MEDIA


# --------------------------------------------------------------------------- #
# Função pública (contrato comum)
# --------------------------------------------------------------------------- #


def triar(chamado: Chamado) -> Chamado:
    """Tria um chamado de Módulos Práticos, mutando-o in-place.

    Cumpre o contrato comum de triagem:

    1. Confirma o produto. Em ambiguidade real com Bancos/Simulados, marca
       ``flags.produto_ambiguo`` e deixa ``produto = None`` (reclassificação).
       Quando confirmado, define ``produto = Produto.MODULOS``.
    2. Extrai os campos essenciais para ``campos_triagem`` — ausências ficam
       explícitas como ``None``/``[]``, nunca inventadas.
    3. Atualiza ``flags.incompleto`` e ``flags.campos_faltantes`` com os
       essenciais ausentes. ``flags.duplicado`` usa o gancho de
       :func:`chave_dedup` (confirmação final cabe à camada de persistência).
    4. Sugere ``prioridade`` por impacto/urgência (ver :func:`_sugerir_prioridade`).

    Args:
        chamado: Chamado normalizado a triar (mutado in-place).

    Returns:
        O mesmo ``chamado``, agora triado.
    """

    if chamado.flags is None:  # type: ignore[redundant-expr]
        chamado.flags = FlagsQualidade()

    # 1. Confirmação de produto (não força em caso de ambiguidade real).
    produto, ambiguo = _confirmar_produto(chamado)
    chamado.flags.produto_ambiguo = ambiguo
    chamado.produto = None if ambiguo else produto

    # 2. Extração dos campos essenciais (ausências explícitas).
    campos = _extrair_campos(chamado)
    chamado.campos_triagem = campos

    # 3. Qualidade: incompletude e campos faltantes.
    faltantes = _campos_faltantes(campos)
    chamado.flags.campos_faltantes = faltantes
    chamado.flags.incompleto = bool(faltantes)

    # Gancho de dedup: chave determinística disponível para a camada de
    # persistência confirmar duplicidade. Sem acesso ao banco aqui, mantém-se
    # ``duplicado`` como já estava (default ``False``).
    chamado.campos_triagem["_chave_dedup"] = chave_dedup(chamado)

    # 4. Prioridade inicial sugerida.
    chamado.prioridade = _sugerir_prioridade(chamado)

    return chamado
