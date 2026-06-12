"""Normalização da entrada do WhatsApp em um ``Chamado`` estruturado.

Duas portas de entrada:

- ``normalizar_payload``: payload bruto do webhook do WhatsApp Business API.
- ``normalizar_texto_colado``: texto colado manualmente pelo atendente.

Princípios (ver ``CLAUDE.md``):

- O ``timestamp_origem`` preserva o horário ORIGINAL da mensagem — nunca o
  horário de processamento (esse vai em ``criado_em``, default do dataclass).
- O número em claro nunca é retornado nem logado: usa-se ``Remetente.de_numero``.
- Campos ausentes são tratados explicitamente (erro ou ``FlagsQualidade``),
  sem heurísticas frágeis e sem inventar conteúdo.
- Anexos guardam apenas metadados/referência — nunca o binário.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from core.models import (
    Anexo,
    Chamado,
    FlagsQualidade,
    Produto,
    Remetente,
)


class PayloadInvalidoError(ValueError):
    """Erro de payload do WhatsApp sem os campos essenciais (remetente/timestamp).

    Lançado quando não é possível construir um chamado com origem confiável.
    A mensagem não inclui o número em claro do remetente.
    """


# --------------------------------------------------------------------------- #
# Classificação leve de produto (apenas sugestão; ambiguidade não força nada)
# --------------------------------------------------------------------------- #

#: Palavras-chave por produto. Usadas só como sinal fraco de sugestão.
_PALAVRAS_PRODUTO: dict[Produto, frozenset[str]] = {
    Produto.BANCOS: frozenset(
        {"banco", "bancos", "questao", "questoes", "questão", "questões"}
    ),
    Produto.MODULOS: frozenset({"modulo", "modulos", "módulo", "módulos", "aula", "aulas"}),
    Produto.SIMULADOS: frozenset({"simulado", "simulados", "prova", "gabarito"}),
}

#: Tipos de mídia conhecidos do WhatsApp mapeados para o vocabulário interno.
_TIPOS_MIDIA: dict[str, str] = {
    "image": "imagem",
    "audio": "audio",
    "voice": "audio",
    "video": "video",
    "document": "documento",
    "sticker": "imagem",
}


# --------------------------------------------------------------------------- #
# Normalização de texto
# --------------------------------------------------------------------------- #

_ESPACOS_HORIZONTAIS = re.compile(r"[^\S\n]+")
_QUEBRAS_MULTIPLAS = re.compile(r"\n{3,}")
#: Caracteres de controle C0/C1, exceto ``\t`` e ``\n`` (preservados).
_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_TOKENS = re.compile(r"[a-z0-9]+")


def _normalizar_texto(bruto: str) -> str:
    """Normaliza o texto da mensagem preservando o conteúdo útil.

    Aplica normalização Unicode (NFC), remove caracteres de controle (mantendo
    a quebra de linha), colapsa espaços horizontais e limita sequências longas
    de linhas em branco. Emojis e pontuação são preservados — não se remove
    conteúdo que possa ser relevante para a triagem.

    Args:
        bruto: Texto como recebido (pode conter ruído de encoding/espaços).

    Returns:
        Texto normalizado e aparado. String vazia se não houver conteúdo útil.
    """

    texto = unicodedata.normalize("NFC", bruto)
    texto = _CONTROLE.sub("", texto)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    # Colapsa espaços/tabs em um único espaço, sem tocar nas quebras de linha.
    texto = _ESPACOS_HORIZONTAIS.sub(" ", texto)
    # Apara espaços no início/fim de cada linha.
    texto = "\n".join(linha.strip() for linha in texto.split("\n"))
    # Reduz blocos de 3+ quebras para no máximo uma linha em branco.
    texto = _QUEBRAS_MULTIPLAS.sub("\n\n", texto)
    return texto.strip()


#: Marcador que substitui a PII redigida no texto exibível.
REDIGIDO = "[redigido]"

# Padrões de PII redigidos em ``texto_normalizado`` (não em ``texto_bruto``).
# A ordem importa: trechos com pontuação são tratados antes das corridas de
# dígitos "cruas", para não deixar separadores órfãos.
_PII_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PII_CPF_CNPJ = re.compile(
    r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"  # CPF formatado
    r"|\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"  # CNPJ formatado
)
_PII_TELEFONE = re.compile(
    r"(?<![\w@.])(?:\+?55[\s.-]?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?!\d)"
)
#: Corridas longas de dígitos (telefone/CPF/CNPJ sem formatação). Números curtos
#: (ex.: "erro 500", "questão 14", anos) ficam de fora por terem < 8 dígitos.
_PII_DIGITOS = re.compile(r"(?<!\d)\d{8,14}(?!\d)")


def _redigir_pii(texto: str) -> str:
    """Redige dados pessoais comuns do texto exibível.

    Substitui e-mails, CPF/CNPJ e telefones por :data:`REDIGIDO`. Aplica-se ao
    ``texto_normalizado`` (visível em listagens/filas), nunca ao ``texto_bruto``,
    que preserva o conteúdo original para auditoria com acesso restrito.

    A heurística é deliberadamente conservadora: prioriza não vazar PII, ao custo
    de eventualmente redigir uma sequência longa de dígitos que não seja PII.

    Args:
        texto: Texto já normalizado.

    Returns:
        Texto com a PII reconhecível substituída por ``[redigido]``.
    """

    texto = _PII_EMAIL.sub(REDIGIDO, texto)
    texto = _PII_CPF_CNPJ.sub(REDIGIDO, texto)
    texto = _PII_TELEFONE.sub(REDIGIDO, texto)
    texto = _PII_DIGITOS.sub(REDIGIDO, texto)
    return texto


def _sem_acentos_lower(texto: str) -> str:
    """Retorna o texto em minúsculas e sem acentos (para casar palavras-chave)."""

    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acentos = "".join(c for c in decomposto if not unicodedata.combining(c))
    return sem_acentos.lower()


def _sugerir_produto(texto_normalizado: str) -> tuple[Produto | None, bool]:
    """Sugere um produto por palavras-chave, sinalizando ambiguidade.

    Classificação propositalmente leve: serve apenas como dica para a triagem
    especializada. Quando nenhum ou mais de um produto pontua, não se força a
    escolha — devolve-se ``None`` e marca-se ambiguidade.

    Args:
        texto_normalizado: Texto já normalizado da mensagem.

    Returns:
        Tupla ``(produto, ambiguo)``. ``produto`` é ``None`` quando nenhum
        produto pontuou ou quando houve empate entre dois ou mais; ``ambiguo``
        é ``True`` quando há mais de um produto candidato com pontuação máxima.
    """

    tokens = set(_TOKENS.findall(_sem_acentos_lower(texto_normalizado)))
    if not tokens:
        return None, False

    pontuacao = {
        produto: len(tokens & {_sem_acentos_lower(p) for p in palavras})
        for produto, palavras in _PALAVRAS_PRODUTO.items()
    }
    melhor = max(pontuacao.values())
    if melhor == 0:
        # Nenhum sinal: não é ambiguidade, é simplesmente indeterminado.
        return None, False

    candidatos = [produto for produto, n in pontuacao.items() if n == melhor]
    if len(candidatos) == 1:
        return candidatos[0], False
    # Empate entre dois ou mais produtos: ambíguo, não adivinha.
    return None, True


# --------------------------------------------------------------------------- #
# Timestamp de origem
# --------------------------------------------------------------------------- #


def _coagir_timestamp_origem(valor: object) -> datetime:
    """Converte o timestamp de origem do WhatsApp em ``datetime`` UTC com tz.

    Aceita os formatos que aparecem na prática:

    - epoch em segundos (``int``/``str`` numérica), como no webhook oficial;
    - string ISO-8601 (com ou sem timezone).

    Um ``datetime`` ingênuo (sem timezone) é interpretado como UTC. Um
    ``datetime`` com timezone é convertido para UTC, preservando o instante.

    Args:
        valor: Timestamp como recebido no payload ou informado pelo atendente.

    Returns:
        O instante de origem em UTC, com timezone explícito.

    Raises:
        PayloadInvalidoError: Se o valor estiver ausente ou for irreconhecível.
    """

    if valor is None or valor == "":
        raise PayloadInvalidoError("timestamp de origem ausente")

    if isinstance(valor, datetime):
        return valor.astimezone(UTC) if valor.tzinfo else valor.replace(tzinfo=UTC)

    # Epoch em segundos (formato do webhook do WhatsApp Business API).
    if isinstance(valor, int | float) or (isinstance(valor, str) and valor.strip().isdigit()):
        return datetime.fromtimestamp(int(valor), tz=UTC)

    if isinstance(valor, str):
        texto = valor.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(texto)
        except ValueError as exc:
            raise PayloadInvalidoError("timestamp de origem em formato inválido") from exc
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)

    raise PayloadInvalidoError("timestamp de origem em formato inválido")


# --------------------------------------------------------------------------- #
# Extração de anexos do payload do WhatsApp
# --------------------------------------------------------------------------- #


def _extrair_anexos(mensagem: dict) -> list[Anexo]:
    """Extrai metadados de mídia de uma mensagem do WhatsApp.

    Reconhece os blocos de mídia padrão (``image``, ``audio``, ``video``,
    ``document``, ``sticker``, ``voice``). Apenas referências são guardadas:
    ``media_id``, MIME e nome de arquivo — nunca o binário.

    Args:
        mensagem: Objeto ``messages[i]`` do payload do WhatsApp.

    Returns:
        Lista de ``Anexo``. Vazia quando a mensagem não tem mídia.
    """

    anexos: list[Anexo] = []
    for chave_midia, tipo_interno in _TIPOS_MIDIA.items():
        bloco = mensagem.get(chave_midia)
        if not isinstance(bloco, dict):
            continue
        media_id = bloco.get("id")
        if not media_id:
            # Mídia sem id de referência não é acionável; ignora silenciosamente.
            continue
        anexos.append(
            Anexo(
                media_id=str(media_id),
                tipo=tipo_interno,
                url_ou_ref=bloco.get("link") or bloco.get("sha256"),
                nome=bloco.get("filename") or bloco.get("caption"),
            )
        )
    return anexos


def _extrair_texto_mensagem(mensagem: dict) -> str:
    """Extrai o texto útil de uma mensagem do WhatsApp.

    Cobre mensagens de texto puro e a legenda (``caption``) de mídias. Não
    inventa conteúdo: se não houver texto nem legenda, devolve string vazia.

    Args:
        mensagem: Objeto ``messages[i]`` do payload do WhatsApp.

    Returns:
        Texto bruto da mensagem, ou string vazia quando não há texto.
    """

    corpo = mensagem.get("text")
    if isinstance(corpo, dict) and corpo.get("body"):
        return str(corpo["body"])

    for chave in ("image", "video", "document", "audio"):
        bloco = mensagem.get(chave)
        if isinstance(bloco, dict) and bloco.get("caption"):
            return str(bloco["caption"])

    return ""


# --------------------------------------------------------------------------- #
# Montagem do chamado (núcleo comum às duas portas de entrada)
# --------------------------------------------------------------------------- #


def _montar_chamado(
    *,
    remetente: Remetente,
    timestamp_origem: datetime,
    texto_bruto: str,
    anexos: list[Anexo],
) -> Chamado:
    """Monta o ``Chamado`` a partir das partes já validadas e normalizadas.

    Centraliza a normalização de texto, a sugestão de produto e o preenchimento
    explícito das ``FlagsQualidade`` (campos faltantes, incompletude,
    ambiguidade de produto). ``criado_em`` permanece com o default do dataclass
    (horário de processamento) — distinto de ``timestamp_origem``.

    Args:
        remetente: Identificação já anonimizada do remetente.
        timestamp_origem: Instante original da mensagem, em UTC.
        texto_bruto: Texto original (preservado em ``texto_bruto``).
        anexos: Metadados de mídia já extraídos.

    Returns:
        Chamado estruturado, pronto para triagem/persistência.
    """

    # ``texto_normalizado`` é exibido em listagens/filas → redige PII.
    # ``texto_bruto`` mantém o conteúdo original (auditoria, acesso restrito).
    texto_normalizado = _redigir_pii(_normalizar_texto(texto_bruto))
    produto, ambiguo = _sugerir_produto(texto_normalizado)

    campos_faltantes: list[str] = []
    if not texto_normalizado and not anexos:
        # Sem texto e sem mídia não há conteúdo acionável.
        campos_faltantes.append("conteudo")

    flags = FlagsQualidade(
        incompleto=bool(campos_faltantes),
        produto_ambiguo=ambiguo,
        campos_faltantes=campos_faltantes,
    )

    return Chamado(
        remetente=remetente,
        timestamp_origem=timestamp_origem,
        texto_normalizado=texto_normalizado,
        texto_bruto=texto_bruto,
        produto=produto,
        anexos=anexos,
        flags=flags,
    )


# --------------------------------------------------------------------------- #
# Porta 1: webhook do WhatsApp Business API
# --------------------------------------------------------------------------- #


def _localizar_mensagem(payload: dict) -> tuple[dict, dict]:
    """Localiza a primeira mensagem e o ``value`` no payload do webhook.

    O webhook do WhatsApp aninha os dados em
    ``entry[].changes[].value.messages[]``. Esta função aceita tanto o envelope
    completo quanto um ``value`` já desembrulhado, e ainda uma mensagem isolada.

    Args:
        payload: Corpo do webhook (ou um recorte equivalente).

    Returns:
        Tupla ``(value, mensagem)`` onde ``value`` contém ``contacts`` e
        ``mensagem`` é o objeto da primeira mensagem.

    Raises:
        PayloadInvalidoError: Se nenhuma mensagem for encontrada.
    """

    # Envelope completo: entry[].changes[].value
    entries = payload.get("entry")
    if isinstance(entries, list):
        for entry in entries:
            for change in entry.get("changes", []) if isinstance(entry, dict) else []:
                value = change.get("value") if isinstance(change, dict) else None
                if isinstance(value, dict) and value.get("messages"):
                    return value, value["messages"][0]

    # ``value`` já desembrulhado.
    if isinstance(payload.get("messages"), list) and payload["messages"]:
        return payload, payload["messages"][0]

    # Mensagem isolada (tem ``from`` e ``timestamp`` no nível raiz).
    if "from" in payload and "timestamp" in payload:
        return {}, payload

    raise PayloadInvalidoError("payload sem mensagens do WhatsApp")


def normalizar_payload(payload: dict) -> Chamado:
    """Converte o payload bruto do webhook do WhatsApp em um ``Chamado``.

    Preserva a origem confiável (remetente anonimizado e ``timestamp_origem``
    original) e normaliza o texto, extraindo metadados de anexos. Campos
    ausentes não-essenciais viram ``FlagsQualidade``; a falta de remetente ou
    timestamp é fatal (origem não confiável).

    Args:
        payload: Corpo do webhook do WhatsApp Business API. Aceita o envelope
            completo (``entry/changes/value``), um ``value`` desembrulhado ou
            uma mensagem isolada.

    Returns:
        Chamado estruturado, com ``produto`` possivelmente ``None`` quando a
        sugestão por palavras-chave for ambígua ou indeterminada.

    Raises:
        PayloadInvalidoError: Se faltar mensagem, remetente (``from``) ou
            ``timestamp`` — casos em que a origem não pode ser confiada.
    """

    if not isinstance(payload, dict):
        raise PayloadInvalidoError("payload deve ser um objeto JSON")

    _value, mensagem = _localizar_mensagem(payload)

    numero_bruto = mensagem.get("from")
    if not numero_bruto:
        raise PayloadInvalidoError("mensagem sem remetente (campo 'from')")

    timestamp_origem = _coagir_timestamp_origem(mensagem.get("timestamp"))

    return _montar_chamado(
        remetente=Remetente.de_numero(str(numero_bruto)),
        timestamp_origem=timestamp_origem,
        texto_bruto=_extrair_texto_mensagem(mensagem),
        anexos=_extrair_anexos(mensagem),
    )


# --------------------------------------------------------------------------- #
# Porta 2: registro manual pelo atendente
# --------------------------------------------------------------------------- #


def normalizar_texto_colado(
    texto: str,
    remetente: str,
    timestamp_origem: datetime | str | int,
) -> Chamado:
    """Converte um registro manual do atendente em um ``Chamado``.

    Usado quando o atendente cola o conteúdo recebido no WhatsApp. O atendente
    é responsável por informar o número de origem e o horário ORIGINAL da
    mensagem; este último é preservado em ``timestamp_origem`` e nunca trocado
    pelo horário de processamento.

    Args:
        texto: Conteúdo colado pelo atendente (texto da mensagem original).
        remetente: Número de origem como recebido — anonimizado em seguida.
        timestamp_origem: Horário original da mensagem (epoch, ISO ou
            ``datetime``).

    Returns:
        Chamado estruturado, sem anexos (registro manual não carrega mídia).

    Raises:
        PayloadInvalidoError: Se ``remetente`` for vazio ou o timestamp for
            ausente/irreconhecível.
    """

    if not remetente or not str(remetente).strip():
        raise PayloadInvalidoError("remetente ausente no registro manual")

    ts = _coagir_timestamp_origem(timestamp_origem)

    return _montar_chamado(
        remetente=Remetente.de_numero(str(remetente)),
        timestamp_origem=ts,
        texto_bruto=texto or "",
        anexos=[],
    )
