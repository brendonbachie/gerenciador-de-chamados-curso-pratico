"""Entry point da aplicação: sobe o FastAPI, monta a API e serve o frontend.

A API fica sob ``/api`` (chamados, webhook do WhatsApp e relatórios). O frontend
estático (HTML/CSS/JS puro) é servido a partir de ``frontend/`` montado em ``/``
com ``html=True`` — ``index.html`` responde em ``/`` e as demais páginas pelos
próprios nomes (``/novo.html``, ``/detalhe.html``). As rotas ``/api`` e
``/health`` têm precedência por serem registradas antes do mount.

    python app.py        # sobe em localhost:8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api import chamados, relatorios, whatsapp
from api.config import db_path
from core import repo

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Garante o schema do banco antes de aceitar requisições."""

    repo.init_db(db_path())
    yield


app = FastAPI(title="Chamados — Curso Prático", version="0.1.0", lifespan=lifespan)

app.include_router(chamados.router, prefix="/api")
app.include_router(whatsapp.router, prefix="/api")
app.include_router(relatorios.router, prefix="/api")


@app.get("/health", tags=["infra"])
def health() -> dict:
    """Verificação simples de saúde da aplicação."""

    return {"status": "ok"}


# Frontend estático (HTML/CSS/JS). Mount em "/" com html=True deve ser o ÚLTIMO
# registro, pois é greedy e captura tudo que não casou com /api ou /health.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
