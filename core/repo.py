"""Camada única de acesso ao SQLite.

Regra de arquitetura: NENHUM outro módulo escreve ou lê o banco diretamente.
``api/`` e demais componentes chamam funções daqui. Mídia (binário) nunca é
guardada — apenas metadados/referência em ``anexos``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from core.models import (
    Anexo,
    Chamado,
    Estado,
    FlagsQualidade,
    Prioridade,
    Produto,
    Remetente,
    Transicao,
)

#: Caminho padrão do banco. Pode ser sobrescrito em ``conectar`` (ex.: testes).
DB_PATH = Path(__file__).resolve().parent.parent / "chamados.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chamados (
    id                TEXT PRIMARY KEY,
    produto           TEXT,
    remetente_hash    TEXT NOT NULL,
    remetente_sufixo  TEXT NOT NULL,
    timestamp_origem  TEXT NOT NULL,
    criado_em         TEXT NOT NULL,
    texto_normalizado TEXT NOT NULL,
    texto_bruto       TEXT NOT NULL,
    estado            TEXT NOT NULL,
    prioridade        TEXT NOT NULL,
    campos_triagem    TEXT NOT NULL DEFAULT '{}',
    flags             TEXT NOT NULL DEFAULT '{}',
    sla_venc_em       TEXT
);

CREATE TABLE IF NOT EXISTS anexos (
    id          TEXT PRIMARY KEY,
    chamado_id  TEXT NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
    media_id    TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    url_ou_ref  TEXT,
    nome        TEXT
);

CREATE TABLE IF NOT EXISTS transicoes (
    id          TEXT PRIMARY KEY,
    chamado_id  TEXT NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
    de_estado   TEXT,
    para_estado TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    motivo      TEXT NOT NULL,
    responsavel TEXT
);

CREATE INDEX IF NOT EXISTS idx_chamados_produto ON chamados(produto);
CREATE INDEX IF NOT EXISTS idx_chamados_estado ON chamados(estado);
CREATE INDEX IF NOT EXISTS idx_chamados_remetente ON chamados(remetente_hash);
CREATE INDEX IF NOT EXISTS idx_transicoes_chamado ON transicoes(chamado_id);
CREATE INDEX IF NOT EXISTS idx_anexos_chamado ON anexos(chamado_id);
"""


@contextmanager
def conectar(db_path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Abre uma conexão configurada (FK on, row factory) e garante o schema.

    Args:
        db_path: Caminho do arquivo SQLite. Use ``":memory:"`` em testes.

    Yields:
        Conexão SQLite pronta para uso; commit no sucesso, rollback em exceção.
    """

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str = DB_PATH) -> None:
    """Cria o schema caso ainda não exista."""

    with conectar(db_path):
        pass


# --------------------------------------------------------------------------- #
# Serialização datetime <-> ISO-8601
# --------------------------------------------------------------------------- #


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _dt(valor: str | None) -> datetime | None:
    return datetime.fromisoformat(valor) if valor else None


# --------------------------------------------------------------------------- #
# Escrita
# --------------------------------------------------------------------------- #


def salvar_chamado(chamado: Chamado, db_path: Path | str = DB_PATH) -> Chamado:
    """Insere ou atualiza um chamado e seus anexos/transições (upsert).

    Toda a operação ocorre em uma transação. O número de telefone em claro
    nunca é persistido — apenas ``remetente.hash`` e ``remetente.sufixo``.

    Args:
        chamado: Chamado a persistir.
        db_path: Banco alvo.

    Returns:
        O mesmo chamado, após a escrita.
    """

    with conectar(db_path) as conn:
        conn.execute(
            """
            INSERT INTO chamados (
                id, produto, remetente_hash, remetente_sufixo,
                timestamp_origem, criado_em, texto_normalizado, texto_bruto,
                estado, prioridade, campos_triagem, flags, sla_venc_em
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                produto=excluded.produto,
                texto_normalizado=excluded.texto_normalizado,
                texto_bruto=excluded.texto_bruto,
                estado=excluded.estado,
                prioridade=excluded.prioridade,
                campos_triagem=excluded.campos_triagem,
                flags=excluded.flags,
                sla_venc_em=excluded.sla_venc_em
            """,
            (
                chamado.id,
                chamado.produto.value if chamado.produto else None,
                chamado.remetente.hash,
                chamado.remetente.sufixo,
                _iso(chamado.timestamp_origem),
                _iso(chamado.criado_em),
                chamado.texto_normalizado,
                chamado.texto_bruto,
                chamado.estado.value,
                chamado.prioridade.value,
                json.dumps(chamado.campos_triagem, ensure_ascii=False),
                json.dumps(_flags_to_dict(chamado.flags), ensure_ascii=False),
                _iso(chamado.sla_venc_em),
            ),
        )

        # Anexos e transições: regravados a partir do estado em memória.
        conn.execute("DELETE FROM anexos WHERE chamado_id = ?", (chamado.id,))
        for ax in chamado.anexos:
            conn.execute(
                "INSERT INTO anexos (id, chamado_id, media_id, tipo, url_ou_ref, nome) "
                "VALUES (?,?,?,?,?,?)",
                (ax.id, chamado.id, ax.media_id, ax.tipo, ax.url_ou_ref, ax.nome),
            )

        conn.execute("DELETE FROM transicoes WHERE chamado_id = ?", (chamado.id,))
        for tr in chamado.transicoes:
            conn.execute(
                "INSERT INTO transicoes "
                "(id, chamado_id, de_estado, para_estado, timestamp, motivo, responsavel) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    tr.id,
                    chamado.id,
                    tr.de_estado.value if tr.de_estado else None,
                    tr.para_estado.value,
                    _iso(tr.timestamp),
                    tr.motivo,
                    tr.responsavel,
                ),
            )

    return chamado


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #


def buscar_chamado(chamado_id: str, db_path: Path | str = DB_PATH) -> Chamado | None:
    """Carrega um chamado completo (com anexos e transições) por id."""

    with conectar(db_path) as conn:
        linha = conn.execute("SELECT * FROM chamados WHERE id = ?", (chamado_id,)).fetchone()
        if linha is None:
            return None
        anexos = conn.execute(
            "SELECT * FROM anexos WHERE chamado_id = ?", (chamado_id,)
        ).fetchall()
        transicoes = conn.execute(
            "SELECT * FROM transicoes WHERE chamado_id = ? ORDER BY timestamp", (chamado_id,)
        ).fetchall()
    return _chamado_de_linhas(linha, anexos, transicoes)


def listar_chamados(
    *,
    produto: Produto | None = None,
    estado: Estado | None = None,
    db_path: Path | str = DB_PATH,
) -> list[Chamado]:
    """Lista chamados, opcionalmente filtrando por produto e/ou estado."""

    clausulas: list[str] = []
    params: list[str] = []
    if produto is not None:
        clausulas.append("produto = ?")
        params.append(produto.value)
    if estado is not None:
        clausulas.append("estado = ?")
        params.append(estado.value)
    where = f"WHERE {' AND '.join(clausulas)}" if clausulas else ""

    with conectar(db_path) as conn:
        linhas = conn.execute(
            f"SELECT * FROM chamados {where} ORDER BY timestamp_origem", params
        ).fetchall()
        resultado: list[Chamado] = []
        for linha in linhas:
            anexos = conn.execute(
                "SELECT * FROM anexos WHERE chamado_id = ?", (linha["id"],)
            ).fetchall()
            transicoes = conn.execute(
                "SELECT * FROM transicoes WHERE chamado_id = ? ORDER BY timestamp",
                (linha["id"],),
            ).fetchall()
            resultado.append(_chamado_de_linhas(linha, anexos, transicoes))
    return resultado


# --------------------------------------------------------------------------- #
# Mapeamento linha -> dataclass
# --------------------------------------------------------------------------- #


def _flags_to_dict(flags: FlagsQualidade) -> dict:
    return {
        "duplicado": flags.duplicado,
        "incompleto": flags.incompleto,
        "produto_ambiguo": flags.produto_ambiguo,
        "campos_faltantes": flags.campos_faltantes,
    }


def _flags_de_dict(dados: dict) -> FlagsQualidade:
    return FlagsQualidade(
        duplicado=dados.get("duplicado", False),
        incompleto=dados.get("incompleto", False),
        produto_ambiguo=dados.get("produto_ambiguo", False),
        campos_faltantes=dados.get("campos_faltantes", []),
    )


def _chamado_de_linhas(
    linha: sqlite3.Row,
    anexos: list[sqlite3.Row],
    transicoes: list[sqlite3.Row],
) -> Chamado:
    return Chamado(
        id=linha["id"],
        produto=Produto(linha["produto"]) if linha["produto"] else None,
        remetente=Remetente(hash=linha["remetente_hash"], sufixo=linha["remetente_sufixo"]),
        timestamp_origem=_dt(linha["timestamp_origem"]),
        criado_em=_dt(linha["criado_em"]),
        texto_normalizado=linha["texto_normalizado"],
        texto_bruto=linha["texto_bruto"],
        estado=Estado(linha["estado"]),
        prioridade=Prioridade(linha["prioridade"]),
        campos_triagem=json.loads(linha["campos_triagem"]),
        flags=_flags_de_dict(json.loads(linha["flags"])),
        sla_venc_em=_dt(linha["sla_venc_em"]),
        anexos=[
            Anexo(
                id=a["id"],
                media_id=a["media_id"],
                tipo=a["tipo"],
                url_ou_ref=a["url_ou_ref"],
                nome=a["nome"],
            )
            for a in anexos
        ],
        transicoes=[
            Transicao(
                id=t["id"],
                de_estado=Estado(t["de_estado"]) if t["de_estado"] else None,
                para_estado=Estado(t["para_estado"]),
                timestamp=_dt(t["timestamp"]),
                motivo=t["motivo"],
                responsavel=t["responsavel"],
            )
            for t in transicoes
        ],
    )
