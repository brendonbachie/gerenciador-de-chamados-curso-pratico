"""Testes de integração da camada ``api/`` (FastAPI).

Cobrem todas as rotas sob ``/api`` — chamados, webhook do WhatsApp e relatórios
— exercitando caminho feliz, edge cases e erros, além das garantias de
privacidade:

- listagens/filas/SLA nunca expõem ``texto_bruto``;
- o remetente é SEMPRE mascarado (``***1234``);
- o número de telefone em claro NUNCA aparece na resposta.

Cada teste usa um banco temporário isolado (fixture ``client``); nada depende de
estado externo, e os timestamps de origem são fixos ou ancorados a deltas
explícitos para um SLA determinístico.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.integration.conftest import (
    VERIFY_TOKEN,
    assinar,
    corpo_cru,
    payload_whatsapp,
    payload_whatsapp_com_imagem,
)

#: Timestamp de origem fixo (UTC) reutilizado nos registros manuais.
TS_ORIGEM = "2026-06-11T12:00:00+00:00"

#: Número em claro usado nos testes — NUNCA deve aparecer nas respostas.
NUMERO_CLARO = "+55 11 98888-7777"
SUFIXO_ESPERADO = "7777"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _criar(client, *, texto: str, numero: str = NUMERO_CLARO, ts: str = TS_ORIGEM) -> dict:
    """Cria um chamado via API e devolve o detalhe (assertando 201)."""

    resp = client.post(
        "/api/chamados",
        json={"texto": texto, "remetente": numero, "timestamp_origem": ts},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _texto_resposta_sem_numero(resp_json: object) -> None:
    """Garante que o número em claro/dígitos brutos não vazaram na resposta."""

    blob = json.dumps(resp_json, ensure_ascii=False)
    assert "988887777" not in blob
    assert "98888-7777" not in blob
    assert "+55" not in blob


# --------------------------------------------------------------------------- #
# Infra
# --------------------------------------------------------------------------- #


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# POST /api/chamados — registro manual
# --------------------------------------------------------------------------- #


def test_registrar_chamado_retorna_201_com_detalhe(client):
    detalhe = _criar(client, texto="duvida sobre o banco de questoes com gabarito errado")

    assert detalhe["id"]
    assert detalhe["produto"] == "bancos"
    assert detalhe["fila"] == "fila_bancos"
    assert detalhe["estado"] == "aberto"
    # Detalhe carrega os campos completos.
    assert "texto_bruto" in detalhe
    assert "campos_triagem" in detalhe
    assert "anexos" in detalhe
    assert "transicoes" in detalhe


def test_registrar_preserva_timestamp_de_origem(client):
    detalhe = _criar(client, texto="duvida sobre o banco de questoes")
    # timestamp_origem é o informado, não o de processamento (criado_em).
    assert detalhe["timestamp_origem"] == TS_ORIGEM
    assert detalhe["criado_em"] != detalhe["timestamp_origem"]


def test_registrar_aceita_epoch_como_timestamp(client):
    resp = client.post(
        "/api/chamados",
        json={
            "texto": "duvida sobre o banco de questoes",
            "remetente": NUMERO_CLARO,
            "timestamp_origem": 1749643200,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["timestamp_origem"].startswith("2025-06-11T12:00:00")


def test_registrar_remetente_mascarado_e_sem_numero_claro(client):
    detalhe = _criar(client, texto="duvida sobre o banco de questoes")
    assert detalhe["remetente"] == f"***{SUFIXO_ESPERADO}"
    _texto_resposta_sem_numero(detalhe)


def test_registrar_payload_invalido_retorna_400(client):
    # Remetente vazio => PayloadInvalidoError => 400.
    resp = client.post(
        "/api/chamados",
        json={"texto": "ola", "remetente": "   ", "timestamp_origem": TS_ORIGEM},
    )
    assert resp.status_code == 400


def test_registrar_timestamp_invalido_retorna_400(client):
    resp = client.post(
        "/api/chamados",
        json={
            "texto": "ola",
            "remetente": NUMERO_CLARO,
            "timestamp_origem": "nao-e-data",
        },
    )
    assert resp.status_code == 400


def test_registrar_corpo_incompleto_retorna_422(client):
    # Falta ``remetente`` e ``timestamp_origem`` => validação do pydantic.
    resp = client.post("/api/chamados", json={"texto": "ola"})
    assert resp.status_code == 422


def test_registrar_chamado_sem_sinal_fica_sem_produto(client):
    detalhe = _criar(client, texto="bom dia preciso de ajuda com uma coisa")
    assert detalhe["produto"] is None
    assert detalhe["fila"] is None


# --------------------------------------------------------------------------- #
# GET /api/chamados — listagem (resumo)
# --------------------------------------------------------------------------- #


def test_listar_vazio_retorna_lista_vazia(client):
    resp = client.get("/api/chamados")
    assert resp.status_code == 200
    assert resp.json() == []


def test_listar_nao_expoe_texto_bruto_e_mascara_remetente(client):
    _criar(client, texto="duvida sobre o banco de questoes com gabarito errado")

    resp = client.get("/api/chamados")
    assert resp.status_code == 200
    lista = resp.json()
    assert len(lista) == 1

    resumo = lista[0]
    # Garantia de privacidade: resumo NÃO traz texto_bruto.
    assert "texto_bruto" not in resumo
    assert "campos_triagem" not in resumo
    assert resumo["remetente"] == f"***{SUFIXO_ESPERADO}"
    _texto_resposta_sem_numero(lista)


def test_listar_filtra_por_produto(client):
    _criar(client, texto="duvida sobre o banco de questoes com gabarito errado")
    _criar(client, texto="nao consigo acessar o modulo 3, a aula 5 nao abre")

    resp = client.get("/api/chamados", params={"produto": "bancos"})
    assert resp.status_code == 200
    lista = resp.json()
    assert len(lista) == 1
    assert lista[0]["produto"] == "bancos"


def test_listar_filtra_por_estado(client):
    _criar(client, texto="duvida sobre o banco de questoes")
    detalhe = _criar(client, texto="nao consigo acessar o modulo 3 aula 5")
    _transicionar(client, detalhe["id"], "em_andamento", "iniciando atendimento")

    resp = client.get("/api/chamados", params={"estado": "em_andamento"})
    assert resp.status_code == 200
    lista = resp.json()
    assert len(lista) == 1
    assert lista[0]["estado"] == "em_andamento"


def test_listar_produto_invalido_retorna_422(client):
    resp = client.get("/api/chamados", params={"produto": "inexistente"})
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# GET /api/chamados/{id} — detalhe
# --------------------------------------------------------------------------- #


def test_obter_detalhe_retorna_200(client):
    criado = _criar(client, texto="duvida sobre o banco de questoes")
    resp = client.get(f"/api/chamados/{criado['id']}")
    assert resp.status_code == 200
    detalhe = resp.json()
    assert detalhe["id"] == criado["id"]
    assert "texto_bruto" in detalhe
    assert detalhe["remetente"] == f"***{SUFIXO_ESPERADO}"
    _texto_resposta_sem_numero(detalhe)


def test_obter_inexistente_retorna_404(client):
    resp = client.get("/api/chamados/nao-existe")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST /api/chamados/{id}/transicao
# --------------------------------------------------------------------------- #


def _transicionar(client, chamado_id: str, novo_estado: str, motivo: str, **extra):
    return client.post(
        f"/api/chamados/{chamado_id}/transicao",
        json={"novo_estado": novo_estado, "motivo": motivo, **extra},
    )


def test_transicao_valida_retorna_200_e_registra_historico(client):
    criado = _criar(client, texto="duvida sobre o banco de questoes")

    resp = _transicionar(
        client, criado["id"], "em_andamento", "iniciando atendimento", responsavel="ana"
    )
    assert resp.status_code == 200
    detalhe = resp.json()
    assert detalhe["estado"] == "em_andamento"

    transicoes = detalhe["transicoes"]
    assert len(transicoes) == 1
    assert transicoes[0]["de_estado"] == "aberto"
    assert transicoes[0]["para_estado"] == "em_andamento"
    assert transicoes[0]["motivo"] == "iniciando atendimento"
    assert transicoes[0]["responsavel"] == "ana"
    assert transicoes[0]["timestamp"] is not None


def test_transicao_persiste_entre_requisicoes(client):
    criado = _criar(client, texto="duvida sobre o banco de questoes")
    _transicionar(client, criado["id"], "em_andamento", "iniciando")

    # Releitura confirma persistência do novo estado.
    resp = client.get(f"/api/chamados/{criado['id']}")
    assert resp.json()["estado"] == "em_andamento"


def test_transicao_ciclo_completo_ate_resolvido(client):
    criado = _criar(client, texto="duvida sobre o banco de questoes")
    assert _transicionar(client, criado["id"], "em_andamento", "iniciando").status_code == 200
    resp = _transicionar(client, criado["id"], "resolvido", "resolvido com o usuario")
    assert resp.status_code == 200
    detalhe = resp.json()
    assert detalhe["estado"] == "resolvido"
    assert len(detalhe["transicoes"]) == 2


def test_transicao_pulando_estado_retorna_409(client):
    criado = _criar(client, texto="duvida sobre o banco de questoes")
    # aberto -> resolvido pula em_andamento.
    resp = _transicionar(client, criado["id"], "resolvido", "querendo pular")
    assert resp.status_code == 409


def test_transicao_invalida_nao_altera_estado(client):
    criado = _criar(client, texto="duvida sobre o banco de questoes")
    _transicionar(client, criado["id"], "resolvido", "querendo pular")
    resp = client.get(f"/api/chamados/{criado['id']}")
    assert resp.json()["estado"] == "aberto"


def test_transicao_motivo_vazio_retorna_422(client):
    # pydantic Field(min_length=1) barra antes de chegar ao core => 422.
    criado = _criar(client, texto="duvida sobre o banco de questoes")
    resp = _transicionar(client, criado["id"], "em_andamento", "")
    assert resp.status_code == 422


def test_transicao_motivo_so_espacos_retorna_400(client):
    # Passa o min_length do pydantic, mas o core exige motivo não-vazio => 400.
    criado = _criar(client, texto="duvida sobre o banco de questoes")
    resp = _transicionar(client, criado["id"], "em_andamento", "   ")
    assert resp.status_code == 400


def test_transicao_chamado_inexistente_retorna_404(client):
    resp = _transicionar(client, "nao-existe", "em_andamento", "motivo")
    assert resp.status_code == 404


def test_transicao_estado_invalido_retorna_422(client):
    criado = _criar(client, texto="duvida sobre o banco de questoes")
    resp = _transicionar(client, criado["id"], "estado_inexistente", "motivo")
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Webhook do WhatsApp — GET (handshake)
# --------------------------------------------------------------------------- #


def test_webhook_get_token_correto_ecoa_challenge(client):
    resp = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "desafio-123",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "desafio-123"


def test_webhook_get_token_errado_retorna_403(client):
    resp = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "token-errado",
            "hub.challenge": "desafio-123",
        },
    )
    assert resp.status_code == 403


def test_webhook_get_sem_modo_subscribe_retorna_403(client):
    resp = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "unsubscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "desafio-123",
        },
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Webhook do WhatsApp — POST (entrada de mensagens)
# --------------------------------------------------------------------------- #


def test_webhook_post_mensagem_valida_processa(client):
    payload = payload_whatsapp()
    bruto = corpo_cru(payload)
    resp = client.post(
        "/api/whatsapp/webhook",
        content=bruto,
        headers={
            "X-Hub-Signature-256": assinar(bruto),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["status"] == "processado"
    assert corpo["id"]

    # O chamado realmente foi persistido e é recuperável.
    detalhe = client.get(f"/api/chamados/{corpo['id']}").json()
    assert detalhe["produto"] == "bancos"
    # Privacidade preservada também na entrada via webhook.
    assert detalhe["remetente"].startswith("***")
    _texto_resposta_sem_numero(detalhe)


def test_webhook_post_mensagem_com_anexo_serializa_anexos(client):
    payload = payload_whatsapp_com_imagem()
    bruto = corpo_cru(payload)
    resp = client.post(
        "/api/whatsapp/webhook",
        content=bruto,
        headers={
            "X-Hub-Signature-256": assinar(bruto),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    chamado_id = resp.json()["id"]

    detalhe = client.get(f"/api/chamados/{chamado_id}").json()
    assert len(detalhe["anexos"]) == 1
    anexo = detalhe["anexos"][0]
    assert anexo["media_id"] == "MID.IMG.123"
    assert anexo["tipo"] == "imagem"
    # Apenas referência/metadado — nunca binário.
    assert anexo["url_ou_ref"] == "abc123"
    assert "id" in anexo


def test_webhook_post_assinatura_invalida_retorna_403(client):
    payload = payload_whatsapp()
    bruto = corpo_cru(payload)
    resp = client.post(
        "/api/whatsapp/webhook",
        content=bruto,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 403


def test_webhook_post_sem_assinatura_retorna_403(client):
    payload = payload_whatsapp()
    bruto = corpo_cru(payload)
    resp = client.post(
        "/api/whatsapp/webhook",
        content=bruto,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 403


def test_webhook_post_assinatura_de_outro_corpo_retorna_403(client):
    # Assinatura calculada sobre um corpo diferente do enviado não confere.
    enviado = corpo_cru(payload_whatsapp(texto="mensagem A"))
    outro = corpo_cru(payload_whatsapp(texto="mensagem B"))
    resp = client.post(
        "/api/whatsapp/webhook",
        content=enviado,
        headers={
            "X-Hub-Signature-256": assinar(outro),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 403


def test_webhook_post_evento_sem_mensagem_e_ignorado(client):
    # Callback de status (sem ``messages``) => PayloadInvalidoError => ignorado.
    payload = {
        "entry": [
            {"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}
        ]
    }
    bruto = corpo_cru(payload)
    resp = client.post(
        "/api/whatsapp/webhook",
        content=bruto,
        headers={
            "X-Hub-Signature-256": assinar(bruto),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignorado"}


def test_webhook_post_sem_app_secret_em_dev_libera(tmp_path, monkeypatch):
    # Fail-OPEN apenas em desenvolvimento: sem app secret e fora de produção, a
    # assinatura não é exigida (facilita testes locais). Em produção é o oposto.
    monkeypatch.setenv("CHAMADOS_DB", str(tmp_path / "dev.db"))
    monkeypatch.delenv("CHAMADOS_WHATSAPP_APP_SECRET", raising=False)
    monkeypatch.setenv("CHAMADOS_WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.delenv("CHAMADOS_ENV", raising=False)

    from app import app

    with TestClient(app) as c:
        bruto = corpo_cru(payload_whatsapp())
        resp = c.post(
            "/api/whatsapp/webhook",
            content=bruto,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "processado"


def test_webhook_post_json_invalido_retorna_400(client):
    bruto = b"{nao e json valido"
    resp = client.post(
        "/api/whatsapp/webhook",
        content=bruto,
        headers={
            "X-Hub-Signature-256": assinar(bruto),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 400


def test_webhook_post_assinatura_valida_mas_json_invalido_nao_vaza_403(client):
    # Garante a ordem: assinatura é checada ANTES do parse; aqui assina certo.
    bruto = b"   "
    resp = client.post(
        "/api/whatsapp/webhook",
        content=bruto,
        headers={
            "X-Hub-Signature-256": assinar(bruto),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# GET /api/relatorios/filas
# --------------------------------------------------------------------------- #


def test_filas_vazias_retorna_chaves_padrao(client):
    resp = client.get("/api/relatorios/filas")
    assert resp.status_code == 200
    dados = resp.json()
    assert set(dados) == {
        "fila_bancos",
        "fila_modulos",
        "fila_simulados",
        "sem_classificacao",
    }
    assert all(v == [] for v in dados.values())


def test_filas_agrupa_por_produto(client):
    _criar(client, texto="duvida sobre o banco de questoes com gabarito errado")
    _criar(client, texto="nao consigo acessar o modulo 3, a aula 5 nao abre")
    _criar(client, texto="o simulado 03 nao computou minha nota no resultado")
    _criar(client, texto="bom dia preciso de ajuda")  # sem produto

    dados = client.get("/api/relatorios/filas").json()
    assert len(dados["fila_bancos"]) == 1
    assert len(dados["fila_modulos"]) == 1
    assert len(dados["fila_simulados"]) == 1
    assert len(dados["sem_classificacao"]) == 1

    # Filas usam resumo: sem texto_bruto, remetente mascarado.
    for fila in dados.values():
        for item in fila:
            assert "texto_bruto" not in item
            assert item["remetente"].startswith("***")
    _texto_resposta_sem_numero(dados)


def test_filas_excluem_resolvidos(client):
    criado = _criar(client, texto="duvida sobre o banco de questoes")
    _transicionar(client, criado["id"], "em_andamento", "iniciando")
    _transicionar(client, criado["id"], "resolvido", "finalizado")

    dados = client.get("/api/relatorios/filas").json()
    assert dados["fila_bancos"] == []


# --------------------------------------------------------------------------- #
# GET /api/relatorios/sla
# --------------------------------------------------------------------------- #


def _ts_passado(dias: int) -> str:
    return (datetime.now(UTC) - timedelta(days=dias)).isoformat()


def _ts_agora() -> str:
    return datetime.now(UTC).isoformat()


def test_sla_lista_todos_nao_resolvidos(client):
    _criar(client, texto="duvida sobre o banco de questoes", ts=_ts_agora())
    _criar(client, texto="nao consigo acessar o modulo 3 aula 5", ts=_ts_agora())

    resp = client.get("/api/relatorios/sla")
    assert resp.status_code == 200
    lista = resp.json()
    assert len(lista) == 2
    # Resumo: sem texto_bruto, remetente mascarado.
    for item in lista:
        assert "texto_bruto" not in item
        assert item["remetente"].startswith("***")
    _texto_resposta_sem_numero(lista)


def test_sla_apenas_em_risco_filtra_dentro_do_prazo(client):
    # Antigo => estourado; recente => ok.
    _criar(client, texto="duvida sobre o banco de questoes", ts=_ts_passado(10))
    _criar(client, texto="nao consigo acessar o modulo 3 aula 5", ts=_ts_agora())

    todos = client.get("/api/relatorios/sla", params={"apenas_em_risco": "false"}).json()
    assert len(todos) == 2

    em_risco = client.get("/api/relatorios/sla", params={"apenas_em_risco": "true"}).json()
    assert len(em_risco) == 1
    assert em_risco[0]["sla"]["nivel"] in {"atencao", "estourado"}


def test_sla_excede_resolvidos(client):
    criado = _criar(client, texto="duvida sobre o banco de questoes", ts=_ts_passado(10))
    _transicionar(client, criado["id"], "em_andamento", "iniciando")
    _transicionar(client, criado["id"], "resolvido", "finalizado")

    lista = client.get("/api/relatorios/sla").json()
    assert lista == []


def test_sla_prioriza_estourados_primeiro(client):
    # Estourado (origem antiga, prazo curto) deve vir antes do recente.
    _criar(client, texto="nao consigo acessar o modulo 3 aula 5", ts=_ts_agora())
    estourado = _criar(
        client, texto="o banco de questoes esta com gabarito errado", ts=_ts_passado(10)
    )

    lista = client.get("/api/relatorios/sla").json()
    assert lista[0]["id"] == estourado["id"]
    assert lista[0]["sla"]["nivel"] == "estourado"
