"""Modelo de domínio do chamado e enums associados.

Este módulo é o contrato central da aplicação: normalizador, triagem, fluxo e
repositório operam sobre estas estruturas. Mantém-se livre de I/O — nada aqui
toca o banco (isso é responsabilidade de ``core/repo.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class Produto(StrEnum):
    """Produtos atendidos pelo Curso Prático."""

    BANCOS = "bancos"
    MODULOS = "modulos"
    SIMULADOS = "simulados"


class Estado(StrEnum):
    """Estados do ciclo de vida do chamado.

    A ordem das transições válidas é definida em ``core/fluxo.py``; aqui só
    enumeramos os valores possíveis.
    """

    ABERTO = "aberto"
    EM_ANDAMENTO = "em_andamento"
    RESOLVIDO = "resolvido"


class Prioridade(StrEnum):
    """Níveis de prioridade. Base para o cálculo de SLA."""

    BAIXA = "baixa"
    MEDIA = "media"
    ALTA = "alta"
    URGENTE = "urgente"


#: Prazo de SLA por prioridade, contado a partir do ``timestamp_origem``.
SLA_POR_PRIORIDADE: dict[Prioridade, timedelta] = {
    Prioridade.URGENTE: timedelta(hours=4),
    Prioridade.ALTA: timedelta(hours=8),
    Prioridade.MEDIA: timedelta(hours=24),
    Prioridade.BAIXA: timedelta(hours=48),
}


def novo_id() -> str:
    """Gera um identificador único (UUID4) para um chamado ou registro."""

    return str(uuid.uuid4())


def agora_utc() -> datetime:
    """Retorna o instante atual em UTC, com timezone explícito."""

    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Tratamento de dado sensível: telefone/identificação do remetente
# --------------------------------------------------------------------------- #

_SO_DIGITOS = re.compile(r"\D+")

#: Pepper de desenvolvimento. NUNCA usado quando ``CHAMADOS_ENV`` indica produção.
_PEPPER_DEV = "dev-pepper-trocar-em-producao"

#: Valores de ``CHAMADOS_ENV`` que caracterizam ambiente produtivo.
_AMBIENTES_PROD = frozenset({"prod", "producao", "production"})


def _em_producao() -> bool:
    """Indica se a aplicação roda em ambiente produtivo (via ``CHAMADOS_ENV``)."""

    return os.environ.get("CHAMADOS_ENV", "").strip().lower() in _AMBIENTES_PROD


def _chave_segredo() -> bytes:
    """Segredo de aplicação (chave HMAC) usado no hash do remetente.

    Lido de ``CHAMADOS_PEPPER``. Em produção (``CHAMADOS_ENV`` produtivo) o
    segredo é OBRIGATÓRIO: a ausência — ou o uso do valor de desenvolvimento —
    aborta a operação, em vez de gerar hashes fracos/reversíveis.

    O segredo é usado como CHAVE de HMAC-SHA256, não como prefixo de hash: isso
    impede ataque de dicionário offline sobre o espaço (pequeno) de telefones
    E.164 caso o banco vaze, desde que a chave permaneça secreta.

    Returns:
        A chave secreta em bytes.

    Raises:
        RuntimeError: Em produção, se ``CHAMADOS_PEPPER`` estiver ausente, vazio
            ou igual ao valor de desenvolvimento.
    """

    segredo = os.environ.get("CHAMADOS_PEPPER")
    if _em_producao():
        if not segredo or segredo == _PEPPER_DEV:
            raise RuntimeError(
                "CHAMADOS_PEPPER ausente ou inseguro em produção: defina um "
                "segredo forte e exclusivo (>= 32 bytes aleatórios)."
            )
        return segredo.encode()
    return (segredo or _PEPPER_DEV).encode()


@dataclass(frozen=True)
class Remetente:
    """Identificação do remetente do WhatsApp tratada como dado sensível.

    O número em claro nunca é persistido. Guardamos apenas:
    - ``hash``: HMAC-SHA256 do número normalizado, com chave secreta de aplicação
      (``CHAMADOS_PEPPER``), para correlação/dedup determinística entre registros.
    - ``sufixo``: últimos 4 dígitos, exclusivamente para exibição (ex: ``***1234``).
    """

    hash: str
    sufixo: str

    @classmethod
    def de_numero(cls, numero_bruto: str) -> Remetente:
        """Constrói a identificação a partir do número bruto do WhatsApp.

        Args:
            numero_bruto: Número como recebido (pode conter ``+``, espaços, etc.).

        Returns:
            ``Remetente`` com hash e sufixo derivados. O número bruto é
            descartado após a derivação e não fica retido.
        """

        digitos = _SO_DIGITOS.sub("", numero_bruto)
        h = hmac.new(_chave_segredo(), digitos.encode(), hashlib.sha256).hexdigest()
        sufixo = digitos[-4:] if len(digitos) >= 4 else digitos
        return cls(hash=h, sufixo=sufixo)

    def mascarado(self) -> str:
        """Representação segura para UI/log: ``***1234``."""

        return f"***{self.sufixo}" if self.sufixo else "***"


@dataclass
class Anexo:
    """Metadados de uma mídia anexada ao chamado.

    Guardamos referência (``media_id``/URL), nunca o binário.
    """

    media_id: str
    tipo: str  # imagem | audio | video | documento | desconhecido
    url_ou_ref: str | None = None
    nome: str | None = None
    id: str = field(default_factory=novo_id)


@dataclass
class Transicao:
    """Registro auditável de uma mudança de estado.

    Toda transição carrega timestamp e motivo — estados não mudam sem rastro.
    """

    de_estado: Estado | None
    para_estado: Estado
    motivo: str
    timestamp: datetime = field(default_factory=agora_utc)
    responsavel: str | None = None
    id: str = field(default_factory=novo_id)


@dataclass
class FlagsQualidade:
    """Sinais de qualidade produzidos por normalização e triagem.

    ``campos_faltantes`` lista, de forma explícita, os campos esperados que o
    usuário não forneceu — nunca preenchidos por suposição.
    """

    duplicado: bool = False
    incompleto: bool = False
    produto_ambiguo: bool = False
    campos_faltantes: list[str] = field(default_factory=list)


@dataclass
class Chamado:
    """Chamado estruturado — unidade central da aplicação.

    ``timestamp_origem`` é o horário ORIGINAL da mensagem do WhatsApp e nunca
    deve ser substituído pelo horário de processamento (``criado_em``).
    ``produto`` pode ser ``None`` enquanto aguarda triagem/reclassificação.
    """

    remetente: Remetente
    timestamp_origem: datetime
    texto_normalizado: str
    texto_bruto: str
    produto: Produto | None = None
    estado: Estado = Estado.ABERTO
    prioridade: Prioridade = Prioridade.MEDIA
    anexos: list[Anexo] = field(default_factory=list)
    transicoes: list[Transicao] = field(default_factory=list)
    flags: FlagsQualidade = field(default_factory=FlagsQualidade)
    campos_triagem: dict = field(default_factory=dict)
    criado_em: datetime = field(default_factory=agora_utc)
    sla_venc_em: datetime | None = None
    id: str = field(default_factory=novo_id)

    def calcular_sla(self) -> datetime:
        """Calcula o vencimento do SLA a partir do ``timestamp_origem``.

        Returns:
            Instante (UTC) em que o SLA vence, segundo a prioridade atual.
        """

        return self.timestamp_origem + SLA_POR_PRIORIDADE[self.prioridade]
