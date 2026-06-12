"use strict";

/* Chamados — Curso Prático · frontend sem framework.
 * A API fica sob /api. Cada página declara <body data-page="..."> e o
 * despacho no final do arquivo chama o init correspondente. */

const API = "/api";

const PRODUTOS = ["bancos", "modulos", "simulados"];
const ESTADOS = ["aberto", "em_andamento", "resolvido"];
const ROTULO_ESTADO = {
  aberto: "Aberto",
  em_andamento: "Em andamento",
  resolvido: "Resolvido",
};
// Próximos estados válidos — espelha core/fluxo.TRANSICOES_VALIDAS.
const PROXIMOS_ESTADOS = {
  aberto: ["em_andamento"],
  em_andamento: ["resolvido"],
  resolvido: [],
};
const FILAS = [
  ["fila_bancos", "Bancos Práticos"],
  ["fila_modulos", "Módulos Práticos"],
  ["fila_simulados", "Simulados Práticos"],
  ["sem_classificacao", "Sem classificação"],
];

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

async function api(path, opts = {}) {
  const resp = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let dados = null;
  const txt = await resp.text();
  if (txt) {
    try {
      dados = JSON.parse(txt);
    } catch {
      dados = txt;
    }
  }
  if (!resp.ok) {
    const detalhe = dados && dados.detail ? dados.detail : resp.statusText;
    throw new Error(typeof detalhe === "string" ? detalhe : JSON.stringify(detalhe));
  }
  return dados;
}

function el(tag, attrs = {}, ...filhos) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const f of filhos) {
    if (f === null || f === undefined) continue;
    node.append(f.nodeType ? f : document.createTextNode(String(f)));
  }
  return node;
}

function dataHora(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function tempoRestante(seg) {
  if (seg === null || seg === undefined) return "";
  const venceu = seg < 0;
  let s = Math.abs(seg);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const txt = h >= 1 ? `${h}h${m.toString().padStart(2, "0")}` : `${m}min`;
  return venceu ? `vencido há ${txt}` : `faltam ${txt}`;
}

function badge(texto, classe) {
  return el("span", { class: `badge ${classe}` }, texto);
}

function badgesDoChamado(c) {
  const frag = document.createDocumentFragment();
  if (c.produto) frag.append(badge(c.produto, "produto"));
  frag.append(badge(ROTULO_ESTADO[c.estado] || c.estado, "estado"));
  frag.append(badge(c.prioridade, `prio-${c.prioridade}`));
  if (c.sla && c.sla.nivel) {
    frag.append(badge(`SLA ${c.sla.nivel}`, `sla-${c.sla.nivel}`));
  }
  if (c.flags) {
    if (c.flags.produto_ambiguo) frag.append(badge("reclassificar", "flag"));
    if (c.flags.incompleto) frag.append(badge("incompleto", "flag"));
    if (c.flags.duplicado) frag.append(badge("duplicado", "flag"));
  }
  return frag;
}

function cardChamado(c) {
  const link = el("a", { href: `detalhe.html?id=${encodeURIComponent(c.id)}` },
    (c.texto_normalizado || "(sem texto)").slice(0, 160) || "(sem texto)");
  const slaTxt = c.sla ? tempoRestante(c.sla.tempo_restante_seg) : "";
  return el("article", { class: "chamado" },
    el("div", { class: "linha" }, badgesDoChamado(c)),
    el("div", { class: "texto" }, link),
    el("div", { class: "meta" },
      el("span", {}, el("b", {}, c.remetente || "—"), " remetente"),
      el("span", {}, "origem: ", dataHora(c.timestamp_origem)),
      slaTxt ? el("span", {}, slaTxt) : null,
    ),
  );
}

function mostrarAviso(container, texto, tipo = "erro") {
  const div = el("div", { class: `aviso ${tipo}` }, texto);
  container.prepend(div);
  return div;
}

function getParam(nome) {
  return new URLSearchParams(location.search).get(nome);
}

/* ------------------------------------------------------------------ */
/* Página: index (filas + lista filtrável)                            */
/* ------------------------------------------------------------------ */

async function initIndex() {
  const boardEl = document.getElementById("filas");
  const listaEl = document.getElementById("lista");
  const fProduto = document.getElementById("f-produto");
  const fEstado = document.getElementById("f-estado");

  async function carregarFilas() {
    boardEl.innerHTML = "";
    try {
      const filas = await api("/relatorios/filas");
      for (const [chave, titulo] of FILAS) {
        const itens = filas[chave] || [];
        const col = el("div", { class: "fila" },
          el("h3", {}, titulo, el("span", { class: "conta" }, String(itens.length))));
        const lista = el("div", { class: "lista" });
        if (itens.length === 0) lista.append(el("div", { class: "vazio" }, "vazio"));
        else itens.forEach((c) => lista.append(cardChamado(c)));
        col.append(lista);
        boardEl.append(col);
      }
    } catch (e) {
      mostrarAviso(boardEl, `Falha ao carregar filas: ${e.message}`);
    }
  }

  async function carregarLista() {
    listaEl.innerHTML = "";
    const params = new URLSearchParams();
    if (fProduto.value) params.set("produto", fProduto.value);
    if (fEstado.value) params.set("estado", fEstado.value);
    try {
      const chamados = await api(`/chamados?${params.toString()}`);
      if (chamados.length === 0) {
        listaEl.append(el("div", { class: "vazio" }, "Nenhum chamado encontrado."));
        return;
      }
      chamados.forEach((c) => listaEl.append(cardChamado(c)));
    } catch (e) {
      mostrarAviso(listaEl, `Falha ao carregar chamados: ${e.message}`);
    }
  }

  fProduto.addEventListener("change", carregarLista);
  fEstado.addEventListener("change", carregarLista);
  await Promise.all([carregarFilas(), carregarLista()]);
}

/* ------------------------------------------------------------------ */
/* Página: novo (registro manual)                                     */
/* ------------------------------------------------------------------ */

function initNovo() {
  const form = document.getElementById("form-novo");
  const botao = form.querySelector("button[type=submit]");

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    form.querySelectorAll(".aviso").forEach((n) => n.remove());

    const texto = form.texto.value.trim();
    const remetente = form.remetente.value.trim();
    const tsLocal = form.timestamp_origem.value;
    if (!texto || !remetente || !tsLocal) {
      mostrarAviso(form, "Preencha texto, remetente e horário de origem.");
      return;
    }
    // datetime-local é hora local do atendente; converte para ISO-UTC.
    const timestamp_origem = new Date(tsLocal).toISOString();

    botao.disabled = true;
    try {
      const criado = await api("/chamados", {
        method: "POST",
        body: JSON.stringify({ texto, remetente, timestamp_origem }),
      });
      location.href = `detalhe.html?id=${encodeURIComponent(criado.id)}`;
    } catch (e) {
      mostrarAviso(form, `Não foi possível registrar: ${e.message}`);
      botao.disabled = false;
    }
  });
}

/* ------------------------------------------------------------------ */
/* Página: detalhe (+ transição de estado)                            */
/* ------------------------------------------------------------------ */

async function initDetalhe() {
  const raiz = document.getElementById("detalhe");
  const id = getParam("id");
  if (!id) {
    mostrarAviso(raiz, "Chamado não informado na URL (?id=).");
    return;
  }

  async function carregar() {
    raiz.innerHTML = "";
    let c;
    try {
      c = await api(`/chamados/${encodeURIComponent(id)}`);
    } catch (e) {
      mostrarAviso(raiz, `Falha ao carregar: ${e.message}`);
      return;
    }
    renderDetalhe(raiz, c, carregar);
  }

  await carregar();
}

function renderDetalhe(raiz, c, recarregar) {
  const cabecalho = el("div", { class: "linha", style: "margin-bottom:14px" }, badgesDoChamado(c));

  const dados = el("dl", { class: "dados" });
  const par = (rotulo, valor) => { dados.append(el("dt", {}, rotulo), el("dd", {}, valor)); };
  par("ID", c.id);
  par("Remetente", c.remetente);
  par("Origem (WhatsApp)", dataHora(c.timestamp_origem));
  par("Registrado em", dataHora(c.criado_em));
  par("SLA vence em", dataHora(c.sla_venc_em));
  if (c.sla) par("SLA", `${c.sla.nivel} · ${tempoRestante(c.sla.tempo_restante_seg)}`);
  if (c.flags && c.flags.campos_faltantes && c.flags.campos_faltantes.length) {
    par("Campos faltantes", c.flags.campos_faltantes.join(", "));
  }

  const painelEsq = el("div", {},
    el("div", { class: "painel" },
      el("h2", {}, "Conteúdo normalizado"),
      el("div", { class: "bloco-texto" }, c.texto_normalizado || "(vazio)")),
    el("div", { class: "painel" },
      el("h2", {}, "Conteúdo original"),
      el("div", { class: "bloco-texto" }, c.texto_bruto || "(vazio)")),
    secaoCamposTriagem(c),
    secaoAnexos(c),
  );

  const painelDir = el("div", {},
    el("div", { class: "painel" }, el("h2", {}, "Dados"), dados),
    secaoTransicao(c, recarregar),
    secaoHistorico(c),
  );

  raiz.append(cabecalho, el("div", { class: "grid-2" }, painelEsq, painelDir));
}

function secaoCamposTriagem(c) {
  const campos = c.campos_triagem || {};
  const chaves = Object.keys(campos).filter((k) => !k.startsWith("_"));
  if (chaves.length === 0) return null;
  const dl = el("dl", { class: "dados" });
  for (const k of chaves) {
    let v = campos[k];
    if (v === null || v === undefined || (Array.isArray(v) && v.length === 0)) v = "—";
    else if (Array.isArray(v)) v = v.join(", ");
    dl.append(el("dt", {}, k), el("dd", {}, String(v)));
  }
  return el("div", { class: "painel" }, el("h2", {}, "Campos de triagem"), dl);
}

function secaoAnexos(c) {
  if (!c.anexos || c.anexos.length === 0) return null;
  const lista = el("ul", {});
  c.anexos.forEach((a) => {
    const ref = a.url_ou_ref ? el("a", { href: a.url_ou_ref, target: "_blank", rel: "noopener" }, a.nome || a.media_id) : (a.nome || a.media_id);
    lista.append(el("li", {}, `[${a.tipo}] `, ref));
  });
  return el("div", { class: "painel" }, el("h2", {}, "Anexos"), lista);
}

function secaoHistorico(c) {
  const ul = el("ul", { class: "timeline" });
  if (!c.transicoes || c.transicoes.length === 0) {
    ul.append(el("li", {}, el("div", { class: "o-que" }, "Aberto"), el("div", { class: "quando" }, dataHora(c.criado_em))));
  } else {
    c.transicoes.forEach((t) => {
      ul.append(el("li", {},
        el("div", { class: "o-que" }, `${ROTULO_ESTADO[t.de_estado] || "—"} → ${ROTULO_ESTADO[t.para_estado] || t.para_estado}`),
        el("div", { class: "quando" }, `${dataHora(t.timestamp)}${t.responsavel ? " · " + t.responsavel : ""}`),
        el("div", { class: "texto" }, t.motivo)));
    });
  }
  return el("div", { class: "painel" }, el("h2", {}, "Histórico"), ul);
}

function secaoTransicao(c, recarregar) {
  const proximos = PROXIMOS_ESTADOS[c.estado] || [];
  if (proximos.length === 0) {
    return el("div", { class: "painel" }, el("h2", {}, "Ciclo de vida"),
      el("p", { class: "texto" }, "Chamado resolvido — sem próximas transições."));
  }

  const selEstado = el("select", { name: "novo_estado" },
    ...proximos.map((e) => el("option", { value: e }, ROTULO_ESTADO[e] || e)));
  const inpMotivo = el("input", { name: "motivo", required: "", placeholder: "Motivo da transição" });
  const inpResp = el("input", { name: "responsavel", placeholder: "Responsável (opcional)" });
  const botao = el("button", { type: "submit" }, "Aplicar transição");

  const form = el("form", { class: "" },
    el("div", { class: "campo" }, el("label", {}, "Novo estado"), selEstado),
    el("div", { class: "campo" }, el("label", {}, "Motivo"), inpMotivo),
    el("div", { class: "campo" }, el("label", {}, "Responsável"), inpResp),
    botao);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!inpMotivo.value.trim()) return;
    botao.disabled = true;
    try {
      await api(`/chamados/${encodeURIComponent(c.id)}/transicao`, {
        method: "POST",
        body: JSON.stringify({
          novo_estado: selEstado.value,
          motivo: inpMotivo.value.trim(),
          responsavel: inpResp.value.trim() || null,
        }),
      });
      await recarregar();
    } catch (e) {
      const painel = form.parentElement;
      mostrarAviso(painel, `Transição recusada: ${e.message}`);
      botao.disabled = false;
    }
  });

  return el("div", { class: "painel" }, el("h2", {}, "Avançar estado"), form);
}

/* ------------------------------------------------------------------ */
/* Despacho por página                                                */
/* ------------------------------------------------------------------ */

document.addEventListener("DOMContentLoaded", () => {
  const pagina = document.body.dataset.page;
  if (pagina === "index") initIndex();
  else if (pagina === "novo") initNovo();
  else if (pagina === "detalhe") initDetalhe();
});
