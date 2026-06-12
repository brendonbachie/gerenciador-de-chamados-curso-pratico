"""Triagem especializada de chamados de **Bancos Práticos**.

Segue o CONTRATO COMUM dos três produtos (bancos, módulos, simulados): expõe
``triar(chamado) -> Chamado``, que confirma o produto, extrai os campos
essenciais para ``chamado.campos_triagem``, preenche as ``FlagsQualidade`` e
sugere uma prioridade inicial.

Princípios (ver ``CLAUDE.md``):

- Saída sempre estruturada e com ausências EXPLÍCITAS: campo essencial não
  encontrado fica ``None`` (ou lista vazia) — nunca se inventa conteúdo.
- Ambiguidade real de produto não força escolha: marca-se
  ``flags.produto_ambiguo`` e deixa-se ``produto = None`` para reclassificação.
- Este módulo não toca o banco nem chama ``core/repo.py``; apenas muta o
  ``Chamado`` em memória e o devolve.
- A extração parte de ``chamado.texto_normalizado`` (conteúdo textual) e de
  ``chamado.anexos`` (evidências/prints).
"""

from __future__ import annotations

import re
import unicodedata

from core.models import (
    Chamado,
    Prioridade,
    Produto,
)

#: Nomes dos campos essenciais de triagem para Bancos Práticos. A ordem é
#: estável e usada tanto na extração quanto no cálculo de ``campos_faltantes``.
CAMPOS_ESSENCIAIS: tuple[str, ...] = (
    "identificacao_usuario",
    "banco_citado",
    "comportamento_esperado",
    "comportamento_observado",
    "evidencias",
)

#: Campo essencial cujo valor "ausente" é uma lista vazia, não ``None``.
_CAMPOS_LISTA: frozenset[str] = frozenset({"evidencias"})


# --------------------------------------------------------------------------- #
# Vocabulário de classificação de produto
# --------------------------------------------------------------------------- #

#: Sinais fortes de que o chamado é de Bancos Práticos.
_SINAIS_BANCOS: frozenset[str] = frozenset(
    {
        "banco",
        "bancos",
        "questao",
        "questoes",
        "enunciado",
        "alternativa",
        "alternativas",
        "comentario",
        "comentarios",
    }
)

#: Sinais de produtos concorrentes — usados só para detectar ambiguidade.
_SINAIS_MODULOS: frozenset[str] = frozenset(
    {"modulo", "modulos", "aula", "aulas", "videoaula", "videoaulas"}
)
_SINAIS_SIMULADOS: frozenset[str] = frozenset(
    {"simulado", "simulados", "prova", "gabarito", "cronometro", "cronometrado"}
)


# --------------------------------------------------------------------------- #
# Heurísticas de prioridade
# --------------------------------------------------------------------------- #

#: Termos que indicam bloqueio total / impacto amplo → prioridade URGENTE.
_TERMOS_URGENTE: frozenset[str] = frozenset(
    {
        "fora do ar",
        "indisponivel",
        "indisponivelidade",
        "nao carrega",
        "nao abre",
        "nao consigo acessar",
        "sem acesso",
        "travado",
        "travou",
        "todos os bancos",
        "nenhum banco",
        "erro 500",
        "perdi tudo",
        "sumiram",
        "sumiu tudo",
    }
)

#: Termos de impacto relevante porém não bloqueante total → prioridade ALTA.
_TERMOS_ALTA: frozenset[str] = frozenset(
    {
        "erro",
        "errado",
        "incorreto",
        "bug",
        "falha",
        "nao salva",
        "nao funciona",
        "gabarito errado",
        "resposta errada",
        "urgente",
        "prova amanha",
        "prova hoje",
    }
)

#: Termos de baixa urgência (dúvida/sugestão) → prioridade BAIXA.
_TERMOS_BAIXA: frozenset[str] = frozenset(
    {
        "duvida",
        "sugestao",
        "sugiro",
        "seria possivel",
        "gostaria de saber",
        "como faco",
        "como faço",
    }
)


# --------------------------------------------------------------------------- #
# Utilitários de texto
# --------------------------------------------------------------------------- #

_TOKENS = re.compile(r"[a-z0-9]+")


def _sem_acentos_lower(texto: str) -> str:
    """Retorna o texto em minúsculas e sem acentos.

    Normaliza para facilitar a comparação com vocabulários de palavras-chave,
    sem alterar o conteúdo armazenado no chamado.

    Args:
        texto: Texto de entrada (pode conter acentos e maiúsculas).

    Returns:
        Texto decomposto (NFKD), sem marcas de combinação, em caixa baixa.
    """

    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acentos = "".join(c for c in decomposto if not unicodedata.combining(c))
    return sem_acentos.lower()


def _tokens(texto_normalizado: str) -> set[str]:
    """Extrai o conjunto de tokens alfanuméricos do texto (sem acento/caixa).

    Args:
        texto_normalizado: Texto já normalizado do chamado.

    Returns:
        Conjunto de tokens em caixa baixa e sem acentos.
    """

    return set(_TOKENS.findall(_sem_acentos_lower(texto_normalizado)))


# --------------------------------------------------------------------------- #
# 1. Confirmação de produto
# --------------------------------------------------------------------------- #


def _classificar_produto(
    chamado: Chamado,
) -> tuple[Produto | None, bool]:
    """Decide se o chamado é de Bancos Práticos, detectando ambiguidade real.

    Conta os sinais (palavras-chave) de cada produto no texto. Há ambiguidade
    real quando Bancos pontua, mas algum produto concorrente (Módulos ou
    Simulados) empata com Bancos — nesse caso não se força a escolha.

    Args:
        chamado: Chamado a classificar (usa ``texto_normalizado``).

    Returns:
        Tupla ``(produto, ambiguo)``:

        - ``(Produto.BANCOS, False)`` quando Bancos é o vencedor claro;
        - ``(None, True)`` quando há empate entre Bancos e outro produto;
        - ``(None, False)`` quando não há nenhum sinal de Bancos (indeterminado;
          fica a cargo de outra triagem/reclassificação).
    """

    tokens = _tokens(chamado.texto_normalizado)

    pontos_bancos = len(tokens & _SINAIS_BANCOS)
    pontos_modulos = len(tokens & _SINAIS_MODULOS)
    pontos_simulados = len(tokens & _SINAIS_SIMULADOS)

    if pontos_bancos == 0:
        # Nenhum sinal de Bancos: não é nosso produto (ou é indeterminado).
        # Só sinalizamos ambiguidade se houver disputa COM Bancos.
        return None, False

    # Bancos empatado ou superado por um concorrente → ambiguidade real.
    if pontos_bancos <= pontos_modulos or pontos_bancos <= pontos_simulados:
        return None, True

    return Produto.BANCOS, False


# --------------------------------------------------------------------------- #
# 2. Extração de campos essenciais
# --------------------------------------------------------------------------- #

#: Rótulos que o atendente/usuário costuma usar para nomear o banco citado.
#: Casa preferencialmente nomes entre aspas (mais confiáveis) e, como
#: alternativa, uma sequência curta logo após "banco [de questões/dados]".
_PADRAO_BANCO = re.compile(
    r"banco(?:\s+de\s+(?:quest[oõ]es|dados))?\s*"
    r"(?:chamado|nomeado)?\s*[:]?\s*"
    r"(?:"
    r"[\"“'](?P<nome_aspas>[^\"”'\n]{2,60})[\"”']"
    r"|"
    r"(?P<nome>[A-Za-zÀ-ÿ0-9][\w .\-/&]{1,40}?)"
    r"(?=[.,;\n]|\s+(?:o|a|esta|está|nao|não|com|que|do|da)\b|$)"
    r")",
    re.IGNORECASE,
)

#: Primeiras palavras que indicam que a captura (sem aspas) não é o nome de um
#: banco, mas conectivo/ruído — nesses casos preferimos ``None``.
_RUIDO_BANCO: frozenset[str] = frozenset(
    {
        "de",
        "do",
        "da",
        "e",
        "o",
        "a",
        "os",
        "as",
        "que",
        "com",
        "esta",
        "nao",
        "questoes",
        "dados",
    }
)

#: Trechos que iniciam o "comportamento observado"; usados como fronteira para
#: não deixar o "esperado" invadir o "observado" na mesma frase.
_FRONTEIRA_OBSERVADO = (
    r"mas|por[eé]m|no\s+entanto|entretanto|por[eé]m|todavia|s[oó]\s+que"
)

#: Marcadores frequentes de "comportamento esperado". O trecho para na
#: fronteira do observado (ex.: "... mas ...") para não absorvê-lo.
_PADRAO_ESPERADO = re.compile(
    r"(?:era\s+(?:pra|para)|deveria|esperava(?:-se)?|o\s+esperado\s+[eé]|"
    r"o\s+correto\s+seria)\b[:\- ]*"
    rf"(?P<trecho>(?:(?!\b(?:{_FRONTEIRA_OBSERVADO})\b)[^\n.]){{3,200}})",
    re.IGNORECASE,
)

#: Marcadores frequentes de "comportamento observado".
_PADRAO_OBSERVADO = re.compile(
    rf"(?:{_FRONTEIRA_OBSERVADO}|est[aá]\s+(?:dando|aparecendo)|"
    r"apareceu|aparece|aconteceu|deu|mostra|exibe|retorna)\b[:\- ]*"
    r"(?P<trecho>[^\n.]{3,200})",
    re.IGNORECASE,
)


def _extrair_identificacao_usuario(chamado: Chamado) -> str | None:
    """Extrai a identificação do usuário sem reexpor dado sensível em claro.

    A identificação confiável vem do remetente já anonimizado pelo normalizador
    (``Remetente.mascarado()``, ex.: ``***1234``). Não se tenta extrair CPF,
    e-mail ou telefone do corpo do texto para evitar reter dado sensível em
    ``campos_triagem``.

    Args:
        chamado: Chamado em triagem.

    Returns:
        Representação mascarada do remetente (ex.: ``***1234``), ou ``None`` se
        não houver sufixo disponível.
    """

    mascarado = chamado.remetente.mascarado()
    # ``mascarado`` devolve ``***`` quando não há sufixo: tratamos como ausência.
    return mascarado if mascarado != "***" else None


def _extrair_banco_citado(texto: str) -> str | None:
    """Tenta identificar o nome do banco citado no texto.

    Procura padrões como ``banco de questões "X"`` ou ``banco X``. Heurística
    deliberadamente conservadora: se nada casar com confiança, devolve ``None``
    em vez de chutar um trecho qualquer.

    Args:
        texto: Texto normalizado do chamado.

    Returns:
        Nome do banco citado (aparado), ou ``None`` se não identificado.
    """

    match = _PADRAO_BANCO.search(texto)
    if not match:
        return None
    bruto = match.group("nome_aspas") or match.group("nome") or ""
    nome = bruto.strip(" .-/&\"'“”")
    if not nome:
        return None
    # Sem aspas, descartamos capturas que não nomeiam um banco (conectivos,
    # tails de "de questões/dados"). Conservador: na dúvida, devolve None.
    if match.group("nome_aspas") is None:
        primeira = _sem_acentos_lower(nome).split()[0] if nome.split() else ""
        if primeira in _RUIDO_BANCO:
            return None
    return nome


def _extrair_trecho(padrao: re.Pattern[str], texto: str) -> str | None:
    """Extrai o primeiro trecho que casar com ``padrao``, ou ``None``.

    Args:
        padrao: Expressão regular com grupo nomeado ``trecho``.
        texto: Texto normalizado do chamado.

    Returns:
        Trecho aparado, ou ``None`` quando não há correspondência.
    """

    match = padrao.search(texto)
    if not match:
        return None
    trecho = match.group("trecho").strip(" .,-:;")
    return trecho or None


def _extrair_evidencias(chamado: Chamado) -> list[str]:
    """Coleta referências de evidências (prints/anexos) do chamado.

    Usa apenas referências de mídia já normalizadas (``media_id``/``url_ou_ref``)
    — nunca binários. Cada item é uma string curta e auditável.

    Args:
        chamado: Chamado em triagem.

    Returns:
        Lista de referências de evidências. Vazia quando não há anexos.
    """

    evidencias: list[str] = []
    for anexo in chamado.anexos:
        ref = anexo.url_ou_ref or anexo.media_id
        rotulo = anexo.nome or anexo.tipo
        evidencias.append(f"{rotulo}:{ref}")
    return evidencias


def _extrair_campos(chamado: Chamado) -> dict[str, object]:
    """Extrai todos os campos essenciais de Bancos, com ausências explícitas.

    Campos de texto não encontrados ficam ``None``; ``evidencias`` ausentes
    ficam como lista vazia. Nada é inventado.

    Args:
        chamado: Chamado em triagem.

    Returns:
        Dicionário com exatamente as chaves de :data:`CAMPOS_ESSENCIAIS`.
    """

    texto = chamado.texto_normalizado

    return {
        "identificacao_usuario": _extrair_identificacao_usuario(chamado),
        "banco_citado": _extrair_banco_citado(texto),
        "comportamento_esperado": _extrair_trecho(_PADRAO_ESPERADO, texto),
        "comportamento_observado": _extrair_trecho(_PADRAO_OBSERVADO, texto),
        "evidencias": _extrair_evidencias(chamado),
    }


# --------------------------------------------------------------------------- #
# 3. Qualidade: campos faltantes, incompletude e duplicidade
# --------------------------------------------------------------------------- #


def _campos_faltantes(campos: dict[str, object]) -> list[str]:
    """Lista, de forma explícita, os campos essenciais ausentes.

    Um campo de texto é considerado ausente quando ``None``; ``evidencias`` é
    ausente quando a lista está vazia.

    Args:
        campos: Campos extraídos por :func:`_extrair_campos`.

    Returns:
        Nomes dos campos essenciais ausentes, na ordem de
        :data:`CAMPOS_ESSENCIAIS`.
    """

    faltantes: list[str] = []
    for nome in CAMPOS_ESSENCIAIS:
        valor = campos.get(nome)
        if nome in _CAMPOS_LISTA:
            if not valor:
                faltantes.append(nome)
        elif valor is None:
            faltantes.append(nome)
    return faltantes


def _detectar_duplicado(chamado: Chamado) -> bool:
    """Gancho heurístico (placeholder) para detecção de duplicidade.

    A detecção robusta de duplicatas depende de consultar o histórico recente do
    mesmo remetente (``remetente.hash``) no repositório — o que está fora do
    escopo deste módulo, que NÃO acessa o banco. Aqui mantemos apenas um gancho
    documentado: o ``gestor-fluxo-chamados`` (com acesso ao repo) deve confirmar
    a duplicidade comparando ``remetente.hash`` + janela de tempo + similaridade
    de ``texto_normalizado``.

    Args:
        chamado: Chamado em triagem.

    Returns:
        Sempre ``False`` neste estágio — a confirmação ocorre no fluxo, com
        acesso ao histórico.
    """

    return False


# --------------------------------------------------------------------------- #
# 4. Prioridade inicial
# --------------------------------------------------------------------------- #


def _contem_algum(texto_sem_acento: str, termos: frozenset[str]) -> bool:
    """Indica se algum dos ``termos`` aparece como substring no texto.

    Os termos podem conter espaços (expressões), por isso a checagem é por
    substring sobre o texto já sem acentos/caixa, e não por token isolado.

    Args:
        texto_sem_acento: Texto normalizado, em caixa baixa e sem acentos.
        termos: Conjunto de termos/expressões a procurar.

    Returns:
        ``True`` se ao menos um termo for encontrado.
    """

    return any(termo in texto_sem_acento for termo in termos)


def _sugerir_prioridade(chamado: Chamado, incompleto: bool) -> Prioridade:
    """Sugere a prioridade inicial a partir de impacto/urgência no texto.

    Heurística (a primeira faixa que casar prevalece):

    1. **URGENTE** — sinais de bloqueio total ou impacto amplo (ex.: "fora do
       ar", "não consigo acessar", "todos os bancos", "perdi tudo").
    2. **ALTA** — defeito relevante porém não-bloqueante, ou urgência declarada
       (ex.: "erro", "gabarito errado", "não salva", "prova amanhã").
    3. **BAIXA** — dúvida/sugestão sem impacto operacional (ex.: "dúvida",
       "sugestão", "gostaria de saber").
    4. **MEDIA** — padrão, quando nenhum sinal claro é detectado.

    Observação: um chamado incompleto (faltando campos essenciais) nunca é
    rebaixado abaixo de ``MEDIA`` automaticamente — falta de informação não
    deve mascarar um possível impacto alto; por isso, se a heurística cairia em
    ``BAIXA`` mas o chamado está incompleto, mantém-se ``MEDIA``.

    Args:
        chamado: Chamado em triagem (usa ``texto_normalizado``).
        incompleto: Se o chamado está faltando campos essenciais.

    Returns:
        Prioridade sugerida.
    """

    texto = _sem_acentos_lower(chamado.texto_normalizado)

    if _contem_algum(texto, _TERMOS_URGENTE):
        return Prioridade.URGENTE
    if _contem_algum(texto, _TERMOS_ALTA):
        return Prioridade.ALTA
    if _contem_algum(texto, _TERMOS_BAIXA):
        # Não rebaixa abaixo de MEDIA quando há lacuna de informação.
        return Prioridade.MEDIA if incompleto else Prioridade.BAIXA
    return Prioridade.MEDIA


# --------------------------------------------------------------------------- #
# Função pública (contrato comum)
# --------------------------------------------------------------------------- #


def triar(chamado: Chamado) -> Chamado:
    """Tria um chamado de Bancos Práticos, mutando-o in-place.

    Cumpre o contrato comum de triagem:

    1. Confirma o produto. Em ambiguidade real com Módulos/Simulados, marca
       ``flags.produto_ambiguo = True`` e deixa ``produto = None`` (para
       reclassificação) — não força a escolha.
    2. Extrai os campos essenciais para ``chamado.campos_triagem`` (ver
       :data:`CAMPOS_ESSENCIAIS`), com ausências explícitas (``None`` ou lista
       vazia) — sem inventar dados.
    3. Preenche qualidade: ``flags.incompleto``, ``flags.campos_faltantes`` e o
       gancho ``flags.duplicado`` (ver :func:`_detectar_duplicado`).
    4. Sugere ``chamado.prioridade`` por impacto/urgência (ver
       :func:`_sugerir_prioridade`).

    O módulo não persiste nada nem acessa ``core/repo.py``.

    Args:
        chamado: Chamado já normalizado a ser triado (mutado in-place).

    Returns:
        O mesmo ``chamado``, agora com produto/campos/flags/prioridade
        preenchidos segundo a triagem de Bancos.
    """

    produto, ambiguo = _classificar_produto(chamado)
    chamado.produto = produto
    chamado.flags.produto_ambiguo = ambiguo

    campos = _extrair_campos(chamado)
    chamado.campos_triagem = campos

    faltantes = _campos_faltantes(campos)
    chamado.flags.campos_faltantes = faltantes
    chamado.flags.incompleto = bool(faltantes)
    chamado.flags.duplicado = _detectar_duplicado(chamado)

    chamado.prioridade = _sugerir_prioridade(chamado, chamado.flags.incompleto)

    return chamado
