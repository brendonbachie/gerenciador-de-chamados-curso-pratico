# chamados-curso-pratico

Aplicação web para gerenciar chamados de suporte do Curso Prático, recebidos via WhatsApp e triados por produto até a resolução.

## Funcionalidades

- **Registro de chamados**: manual, colando o texto recebido no WhatsApp (`POST /api/chamados`), ou automático via webhook do WhatsApp Business API (`POST /api/whatsapp/webhook`, com verificação de assinatura `X-Hub-Signature-256` e handshake `GET /api/whatsapp/webhook`).
- **Normalização com origem preservada**: o conteúdo colado/recebido é convertido em chamado estruturado sem nunca substituir o remetente e o timestamp originais da mensagem.
- **Triagem por produto**: classificação e extração de campos específicos para **Bancos Práticos**, **Módulos Práticos** e **Simulados Práticos**; em caso de ambiguidade ou campos ausentes, o chamado é sinalizado (`produto_ambiguo`, `campos_faltantes`) em vez de ter dados inventados.
- **Ciclo de vida com SLA**: máquina de estados `aberto → em_andamento → resolvido` (`POST /api/chamados/{id}/transicao`), com motivo e timestamp obrigatórios em cada transição; SLA calculado por prioridade a partir do timestamp de origem, com níveis de urgência.
- **Consulta e relatórios**: listagem/detalhe de chamados (`GET /api/chamados`, `GET /api/chamados/{id}`), filas priorizadas por produto (`GET /api/relatorios/filas`) e monitoramento de SLA (`GET /api/relatorios/sla`).
- **Privacidade**: o telefone do remetente é sempre mascarado nas respostas da API; o texto bruto (que pode conter dados pessoais) só é exposto no detalhe do chamado.
- **Frontend simples**: listagem/filas (`index.html`), registro manual (`novo.html`) e detalhe com transições de estado (`detalhe.html`).

## Stack

- **Linguagem:** Python 3.12
- **Backend:** FastAPI + uvicorn
- **Frontend:** HTML + CSS + JavaScript puro (sem framework, sem build step)
- **Persistência:** SQLite, acessado exclusivamente por `core/repo.py`
- **Testes:** pytest + httpx + pytest-cov

## Estrutura de pastas

```
app.py                 # entry point — sobe o FastAPI e serve o frontend
api/                    # endpoints (parsing de request/response)
  chamados.py           # registro, listagem, detalhe e transição de estado
  whatsapp.py           # webhook do WhatsApp (handshake + recebimento)
  relatorios.py         # filas por produto e monitoramento de SLA
  serializers.py        # serialização de Chamado para respostas da API
  config.py             # caminho do banco e segredos do webhook
core/                   # lógica de negócio
  models.py             # modelo do chamado e enums (produto, estado, prioridade)
  normalizer.py         # payload/texto do WhatsApp → chamado estruturado
  triagem.py + triagem_*.py  # roteamento e extração por produto
  fluxo.py              # máquina de estados e cálculo de SLA
  repo.py               # única camada que lê/escreve o SQLite
frontend/               # index.html, novo.html, detalhe.html + static/
tests/                  # unit/ e integration/
```

## Como instalar e rodar

```bash
pip install -e ".[dev]"
python app.py        # sobe em localhost:8000
```

A API fica sob `/api` e o frontend estático é servido em `/`.

## Testes

```bash
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest --cov=. tests/
```

## Licença

MIT, see LICENSE.
