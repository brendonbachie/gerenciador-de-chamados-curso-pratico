# chamados-curso-pratico

Aplicação web para gerenciar os chamados dos usuários do **Curso Prático**. Os chamados chegam via WhatsApp; o atendente cola/registra o conteúdo na aplicação, que normaliza, classifica por produto, controla o ciclo de vida (estado + SLA) e mantém histórico auditável.

Produtos atendidos: **Bancos Práticos**, **Módulos Práticos** e **Simulados Práticos**.

```bash
python app.py        # sobe o servidor em localhost:8000
```

---

## Stack

- **Linguagem:** Python 3.12
- **Backend:** FastAPI + uvicorn
- **Frontend:** HTML + CSS + JavaScript puro (sem framework, sem build step)
- **Persistência:** SQLite (acesso centralizado em `core/`), com possibilidade de export JSON para backup
- **Integração:** WhatsApp Business API / webhook (entrada de mensagens)
- **Testes:** pytest + httpx

---

## Estrutura de Pastas

```
chamados-curso-pratico/
├── app.py                      # Entry point — sobe FastAPI + serve frontend
├── api/                        # Só parsing de request/response
│   ├── chamados.py             # CRUD e transições de estado de chamados
│   ├── whatsapp.py             # Webhook/entrada de mensagens do WhatsApp
│   └── relatorios.py           # Consultas, filas por produto e SLA
├── core/                       # Toda a lógica de negócio
│   ├── models.py               # Modelo do chamado e enums (produto, estado, prioridade)
│   ├── normalizer.py           # Payload do WhatsApp → chamado estruturado
│   ├── triagem.py              # Roteamento para a fila do produto correto
│   ├── fluxo.py                # Máquina de estados + cálculo de SLA
│   └── repo.py                 # Única camada que escreve/lê o SQLite
├── frontend/
│   ├── index.html              # Lista e filas de chamados
│   ├── novo.html               # Registro manual de chamado do WhatsApp
│   ├── detalhe.html            # Detalhe + transições de estado
│   └── static/ (style.css, app.js)
└── tests/
    ├── unit/  (test_models, test_fluxo, test_normalizer, test_triagem)
    └── integration/ (test_api)
```

---

## Arquitetura e Decisões Importantes

**Origem confiável antes de tudo.** O `normalizer.py` preserva remetente e timestamp **original** da mensagem do WhatsApp — nunca substitui pelo horário de processamento.

**Triagem por produto é especializada.** Cada produto (bancos, módulos, simulados) tem critérios próprios de extração de campos. Em caso de ambiguidade, o chamado é marcado para reclassificação em vez de adivinhar o produto.

**Ciclo de vida explícito.** Todo chamado percorre `aberto → em_andamento → resolvido`. Cada transição registra timestamp e motivo; estados não são pulados sem justificativa. O SLA é calculado a partir do timestamp de origem.

**Escrita centralizada.** Apenas `core/repo.py` toca o banco. `api/` não contém lógica; chama `core/`.

**Dados pessoais.** Telefone e identificação dos usuários são dados sensíveis — tratados com cuidado, sem expor em logs e revisados pelo security-reviewer.

---

## Agentes

Os subagentes ficam em `.claude/agents/*.md`. Delegue assim:

- use o agente **normalizador-whatsapp** para converter o payload bruto/colado do WhatsApp em um chamado estruturado, preservando remetente e timestamp de origem.
- use o agente **triagem-bancos-praticos** para classificar, normalizar e extrair campos de chamados sobre Bancos Práticos.
- use o agente **triagem-modulos-praticos** para classificar, normalizar e extrair campos de chamados sobre Módulos Práticos.
- use o agente **triagem-simulados-praticos** para classificar, normalizar e extrair campos de chamados sobre Simulados Práticos.
- use o agente **gestor-fluxo-chamados** para avançar estados, calcular/monitorar SLA, priorizar e rotear chamados entre as filas de produto.
- use o agente **security-reviewer** para revisar o tratamento dos dados pessoais dos usuários e a segurança dos endpoints e do webhook.
- use o agente **test-writer** para escrever testes da máquina de estados, da normalização e da triagem.

---

## Hooks Configurados

Registrados em `.claude/settings.json`:

- **PreToolUse (Bash)** — bloqueia comandos destrutivos (`rm -rf /`, `git push --force`, `curl | sh`, etc.) e barra manipulação direta do arquivo de persistência de chamados por shell, forçando escrita pela camada da aplicação.
- **PostToolUse (Write)** — roda `ruff check` em arquivos `.py` recém-escritos e devolve os problemas ao Claude para correção.
- **Stop** — ao fim do turno, roda a suíte `pytest` (se existir) e avisa no transcript quando os testes estão falhando.

---

## Convenções de Código

- `api/` só faz parsing de request/response — lógica vai em `core/`
- Toda escrita/leitura do banco passa por `core/repo.py`
- Saídas de triagem e normalização sempre estruturadas (JSON/dataclass) com campos ausentes explícitos — nunca inventar dados
- Transições de estado sempre com timestamp e motivo
- Frontend sem frameworks — HTML/CSS/JS puro

---

## Comandos

```bash
pip install -e ".[dev]"
python app.py                    # localhost:8000
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest --cov=. tests/
ruff check .
mypy .
```

---

## O que NÃO fazer

- Não substituir o timestamp de origem do WhatsApp pelo horário de processamento
- Não escrever no banco fora de `core/repo.py`
- Não preencher campos de triagem por suposição — ausências ficam explícitas
- Não pular estados do ciclo de vida sem justificativa
- Não expor telefone/identificação de usuários em logs
- Não colocar lógica nos endpoints de `api/`
- Não usar frameworks JavaScript no frontend
