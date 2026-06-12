"""Testes da normalização do WhatsApp em ``Chamado``.

Cobre as duas portas de entrada (webhook e texto colado), a preservação da
origem confiável (``timestamp_origem`` distinto de ``criado_em``), o
mascaramento do remetente, anexos como metadados e o tratamento explícito de
ausências (erro fatal vs. flags).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.models import Produto
from core.normalizer import (
    PayloadInvalidoError,
    normalizar_payload,
    normalizar_texto_colado,
)

#: Epoch fixo: 2026-06-11 12:00:00 UTC.
EPOCH_ORIGEM = 1781179200
DT_ORIGEM = datetime.fromtimestamp(EPOCH_ORIGEM, tz=UTC)


def _mensagem_texto(
    *, numero: str = "5511999991234", texto: str = "tenho uma duvida"
) -> dict:
    """Monta uma mensagem isolada de texto do WhatsApp."""

    return {
        "from": numero,
        "timestamp": str(EPOCH_ORIGEM),
        "type": "text",
        "text": {"body": texto},
    }


def _envelope(mensagem: dict) -> dict:
    """Embrulha uma mensagem no envelope completo do webhook."""

    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"wa_id": mensagem["from"]}],
                            "messages": [mensagem],
                        },
                    }
                ],
            }
        ],
    }


# --------------------------------------------------------------------------- #
# Caminho feliz — webhook
# --------------------------------------------------------------------------- #


def test_normalizar_payload_envelope_completo():
    # Arrange
    payload = _envelope(_mensagem_texto(texto="duvida sobre a aula"))

    # Act
    chamado = normalizar_payload(payload)

    # Assert
    assert chamado.texto_normalizado == "duvida sobre a aula"
    assert chamado.timestamp_origem == DT_ORIGEM


def test_normalizar_payload_mensagem_isolada():
    # Arrange: mensagem no nível raiz (from + timestamp).
    mensagem = _mensagem_texto(texto="problema no modulo")

    # Act
    chamado = normalizar_payload(mensagem)

    # Assert
    assert chamado.texto_normalizado == "problema no modulo"
    assert chamado.timestamp_origem == DT_ORIGEM


def test_normalizar_payload_value_desembrulhado():
    # Arrange: o "value" já desembrulhado, sem entry/changes.
    value = {"messages": [_mensagem_texto(texto="erro no simulado")]}

    # Act
    chamado = normalizar_payload(value)

    # Assert
    assert chamado.texto_normalizado == "erro no simulado"


# --------------------------------------------------------------------------- #
# Preservação da origem confiável
# --------------------------------------------------------------------------- #


def test_timestamp_origem_preservado_e_distinto_de_criado_em():
    # Arrange
    chamado = normalizar_payload(_mensagem_texto())

    # Assert: origem ancorada no epoch da mensagem, criado_em é o processamento.
    assert chamado.timestamp_origem == DT_ORIGEM
    assert chamado.criado_em != chamado.timestamp_origem
    # criado_em (processamento) é posterior à origem fixa de 2026-06-11.
    assert chamado.criado_em > chamado.timestamp_origem


def test_timestamp_origem_iso_com_timezone_convertido_para_utc():
    # Arrange: 09:00 em -03:00 == 12:00 UTC.
    mensagem = _mensagem_texto()
    mensagem["timestamp"] = "2026-06-11T09:00:00-03:00"

    # Act
    chamado = normalizar_payload(mensagem)

    # Assert
    assert chamado.timestamp_origem == datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


def test_timestamp_origem_iso_sem_timezone_interpretado_como_utc():
    mensagem = _mensagem_texto()
    mensagem["timestamp"] = "2026-06-11T12:00:00"

    chamado = normalizar_payload(mensagem)

    assert chamado.timestamp_origem == datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Mascaramento do remetente (dado sensível)
# --------------------------------------------------------------------------- #


def test_remetente_mascarado_sem_número_em_claro():
    # Arrange
    numero = "5511999991234"
    chamado = normalizar_payload(_mensagem_texto(numero=numero))

    # Assert: sufixo exibível, número nunca em claro no hash.
    assert chamado.remetente.sufixo == "1234"
    assert chamado.remetente.mascarado() == "***1234"
    assert numero not in chamado.remetente.hash


# --------------------------------------------------------------------------- #
# Anexos como metadados
# --------------------------------------------------------------------------- #


def test_anexo_extraído_como_metadado_com_legenda_como_texto():
    # Arrange: imagem com caption e media_id.
    mensagem = {
        "from": "5511999991234",
        "timestamp": str(EPOCH_ORIGEM),
        "type": "image",
        "image": {
            "id": "MEDIA-123",
            "mime_type": "image/jpeg",
            "sha256": "abc123",
            "caption": "print do erro na questao",
        },
    }

    # Act
    chamado = normalizar_payload(mensagem)

    # Assert: anexo carrega referência (nunca binário); caption vira texto.
    assert len(chamado.anexos) == 1
    anexo = chamado.anexos[0]
    assert anexo.media_id == "MEDIA-123"
    assert anexo.tipo == "imagem"
    assert anexo.url_ou_ref == "abc123"
    assert chamado.texto_normalizado == "print do erro na questao"
    assert chamado.flags.incompleto is False


def test_mídia_sem_id_é_ignorada():
    # Arrange: bloco de imagem sem id de referência -> não acionável.
    mensagem = {
        "from": "5511999991234",
        "timestamp": str(EPOCH_ORIGEM),
        "type": "image",
        "image": {"mime_type": "image/jpeg", "caption": "print"},
    }

    # Act
    chamado = normalizar_payload(mensagem)

    # Assert: sem anexos, mas a legenda ainda é aproveitada como texto.
    assert chamado.anexos == []
    assert chamado.texto_normalizado == "print"


# --------------------------------------------------------------------------- #
# Ausências essenciais — erro fatal
# --------------------------------------------------------------------------- #


def test_payload_sem_remetente_levanta_erro():
    mensagem = {"timestamp": str(EPOCH_ORIGEM), "text": {"body": "oi"}}

    with pytest.raises(PayloadInvalidoError):
        normalizar_payload(mensagem)


def test_payload_sem_timestamp_levanta_erro():
    mensagem = {"from": "5511999991234", "text": {"body": "oi"}}

    with pytest.raises(PayloadInvalidoError):
        normalizar_payload(mensagem)


def test_payload_sem_mensagens_levanta_erro():
    with pytest.raises(PayloadInvalidoError):
        normalizar_payload({"object": "whatsapp_business_account", "entry": []})


def test_payload_não_dict_levanta_erro():
    with pytest.raises(PayloadInvalidoError):
        normalizar_payload([1, 2, 3])  # type: ignore[arg-type]


def test_timestamp_em_formato_inválido_levanta_erro():
    mensagem = _mensagem_texto()
    mensagem["timestamp"] = "ontem à tarde"

    with pytest.raises(PayloadInvalidoError):
        normalizar_payload(mensagem)


# --------------------------------------------------------------------------- #
# Ausência de conteúdo — flags (não é erro fatal)
# --------------------------------------------------------------------------- #


def test_sem_conteúdo_marca_flags_incompleto():
    # Arrange: mensagem sem texto e sem mídia.
    mensagem = {"from": "5511999991234", "timestamp": str(EPOCH_ORIGEM), "type": "text"}

    # Act
    chamado = normalizar_payload(mensagem)

    # Assert
    assert chamado.flags.incompleto is True
    assert "conteudo" in chamado.flags.campos_faltantes
    assert chamado.texto_normalizado == ""


# --------------------------------------------------------------------------- #
# Sugestão de produto / ambiguidade
# --------------------------------------------------------------------------- #


def test_produto_sugerido_quando_sinal_único():
    chamado = normalizar_payload(_mensagem_texto(texto="problema no meu simulado de hoje"))

    assert chamado.produto is Produto.SIMULADOS
    assert chamado.flags.produto_ambiguo is False


def test_produto_ambíguo_deixa_produto_none_e_marca_flag():
    # Arrange: empate 1x1 entre bancos ("banco") e simulados ("simulado").
    chamado = normalizar_payload(
        _mensagem_texto(texto="o banco e o simulado")
    )

    # Assert: na dúvida não adivinha o produto.
    assert chamado.produto is None
    assert chamado.flags.produto_ambiguo is True


def test_produto_indeterminado_não_é_ambíguo():
    # Arrange: nenhum sinal de produto.
    chamado = normalizar_payload(_mensagem_texto(texto="bom dia, tudo bem?"))

    # Assert: indeterminado != ambíguo.
    assert chamado.produto is None
    assert chamado.flags.produto_ambiguo is False


# --------------------------------------------------------------------------- #
# Texto colado pelo atendente
# --------------------------------------------------------------------------- #


def test_texto_colado_preserva_timestamp_origem_epoch():
    chamado = normalizar_texto_colado(
        "duvida sobre o modulo", remetente="5511999991234", timestamp_origem=EPOCH_ORIGEM
    )

    assert chamado.timestamp_origem == DT_ORIGEM
    assert chamado.texto_normalizado == "duvida sobre o modulo"
    assert chamado.anexos == []


def test_texto_colado_aceita_datetime_aware():
    origem = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

    chamado = normalizar_texto_colado(
        "oi", remetente="5511999991234", timestamp_origem=origem
    )

    assert chamado.timestamp_origem == origem


def test_texto_colado_normaliza_espaços_e_quebras():
    chamado = normalizar_texto_colado(
        "  linha   um \n\n\n\n   linha    dois  ",
        remetente="5511999991234",
        timestamp_origem=EPOCH_ORIGEM,
    )

    assert chamado.texto_normalizado == "linha um\n\nlinha dois"


def test_texto_colado_sem_remetente_levanta_erro():
    with pytest.raises(PayloadInvalidoError):
        normalizar_texto_colado("oi", remetente="", timestamp_origem=EPOCH_ORIGEM)


def test_texto_colado_sem_timestamp_levanta_erro():
    with pytest.raises(PayloadInvalidoError):
        normalizar_texto_colado("oi", remetente="5511999991234", timestamp_origem="")
