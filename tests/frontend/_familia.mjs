/**
 * O que os testes da Família da Conta (dashboard_v2_familia*.test.mjs) compartilham: o servidor de mentira da api/v2/familia.py
 * (mesmos status, códigos de envelope e corpos; as fixtures são as de api_v2_respostas.json, validadas pelos modelos Pydantic)
 * e o `abrirFamilia`. Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import assert from "node:assert/strict";
import { abrir } from "./_guia.mjs";
import { RESPOSTAS } from "./_painel.mjs";

export const F = RESPOSTAS.familia;
export const ERRO = (status, code) => (r) => r.fulfill({ status, json: { error: { code, message: "x" } } });
export const NONCE = F.dissolucao.confirmacao;
export const E422 = (campo) => (r) => r.fulfill({ status: 422, json: { error: { code: "validation_error", message: "x", details: [{ loc: ["query", campo], msg: "x", type: "value_error" }] } } });
export const MARIA = { ref: "9f1c2a40-7b1e-4a53-8d3a-0c5d6e7f8a91", nome: "Maria Aparecida Costa", status: "ativo", permissoes: ["agregados"] };
export const MES = "2026-10"; // o relógio do painel é fixo (_guia.mjs: setFixedTime 2026-10-02); mesAtual() dá este mês
export const titular = (extra = {}) => ({ ...structuredClone(F.titular), ...extra });

// `s.visao`: null (sem família, 404 not_found) | o corpo do GET. `s.reqs`: toda requisição /api/v2/familia* (método, caminho, corpo, CSRF, content-type).
// Ganchos (cada um recebe a rota e devolve a resposta, ou nada para seguir): s.get, s.criar, s.convidar, s.revogar, s.remover, s.sair, s.previa, s.dissolver,
// e os de dados (PR 6b): s.liberar, s.revogarP, s.historico, s.agregados, s.lancamentos. `holdAgregados` (uma promessa) prende as leituras de membro antes de responder.
// As permissões de escrita leem e gravam em s.visao.permissoes (membro); o titular é lido de s.visao.membros[].permissoes pelo próprio teste.
export function servidor({ visao = null } = {}) {
  const s = { visao, nonce: NONCE, reqs: [], tokens: 0, hold: null, get: null, criar: null, convidar: null, revogar: null, remover: null, sair: null, previa: null, dissolver: null, liberar: null, revogarP: null, historico: null, agregados: null, lancamentos: null, holdAgregados: null,
    // a casa (PR 6c): `s.casa` é o corpo do GET (null = sem família, 404 not_found); ganchos de leitura/escrita e `holdCasa` (uma promessa que prende o GET)
    casa: structuredClone(F.casa), casaLer: null, casaCriar: null, casaEditar: null, casaApagar: null, casaMover: null, holdCasa: null, seq: 100 };
  const vazio = (r) => r.fulfill({ status: 204, body: "" });
  s.antes = async (ctx) => {
    await ctx.route((u) => u.pathname.startsWith("/api/v2/familia"), async (r) => {
      const q = r.request(), u = new URL(q.url()), m = q.method(), p = u.pathname.slice("/api/v2/familia".length);
      s.reqs.push({ metodo: m, caminho: p, url: q.url(), corpo: q.postData(), csrf: q.headers()["x-csrf-token"], tipo: q.headers()["content-type"] });
      // Um gancho que responde (fulfill) encerra a rota; um que só espera (atraso) deixa a resposta padrão seguir.
      let feito = false;
      const rr = { fulfill: (o) => { feito = true; return r.fulfill(o); } };
      const tratou = async (g) => { if (g) await g(rr, q.postDataJSON?.()); return feito; };
      if (m === "GET" && p === "") {
        if (s.hold) await s.hold;
        if (await tratou(s.get)) return;
        return s.visao ? r.fulfill({ json: s.visao }) : ERRO(404, "not_found")(r);
      }
      if (m === "POST" && p === "") {
        if (await tratou(s.criar)) return;
        s.visao = titular({ membros: [] });
        return r.fulfill({ status: 201, json: s.visao });
      }
      if (m === "POST" && p === "/convites") {
        if (await tratou(s.convidar)) return;
        return r.fulfill({ status: 201, json: { ...F.convite, token: `tok-${++s.tokens}`, link: `https://pigbankai.com/familia/entrar#t=tok-${s.tokens}` } });
      }
      if (m === "DELETE" && p === "/convites") return (await tratou(s.revogar)) || vazio(r);
      if (m === "DELETE" && p.startsWith("/membros/")) {
        if (await tratou(s.remover)) return;
        const ref = decodeURIComponent(p.slice("/membros/".length));
        s.visao = { ...s.visao, membros: s.visao.membros.filter((x) => x.ref !== ref) };
        return vazio(r);
      }
      if (m === "POST" && p === "/sair") return (await tratou(s.sair)) || vazio(r);
      if (m === "GET" && p === "/dissolucao") {
        if (await tratou(s.previa)) return;
        return r.fulfill({ json: { ...F.dissolucao, membros: s.visao?.membros?.length ?? 0, confirmacao: s.nonce } });
      }
      if (m === "POST" && p === "/dissolucao") {
        if (await tratou(s.dissolver)) return;
        if (q.postDataJSON().confirmacao !== s.nonce) return ERRO(409, "confirmation_mismatch")(r);
        s.visao = null;
        return vazio(r);
      }
      if (p === "/permissoes" && m === "POST") {
        if (await tratou(s.liberar)) return;
        const e = q.postDataJSON().escopo;
        s.visao = { ...s.visao, permissoes: [...new Set([...s.visao.permissoes, e])] };
        return r.fulfill({ json: { permissoes: s.visao.permissoes } });
      }
      if (p.startsWith("/permissoes/") && m === "DELETE") {
        if (await tratou(s.revogarP)) return;
        const e = p.slice("/permissoes/".length);
        s.visao = { ...s.visao, permissoes: s.visao.permissoes.filter((x) => x !== e && !(e === "agregados" && x === "lancamentos")) };
        return r.fulfill({ json: { permissoes: s.visao.permissoes } });
      }
      if (p === "/historico" && m === "GET") return (await tratou(s.historico)) || r.fulfill({ json: F.historico_vazio });
      const dado = /^\/membros\/([^/]+)\/(agregados|lancamentos)$/.exec(p);
      if (dado && m === "GET") {
        if (s.holdAgregados) await s.holdAgregados;
        if (await tratou(dado[2] === "agregados" ? s.agregados : s.lancamentos)) return;
        const mes = u.searchParams.get("mes");
        return r.fulfill({ json: dado[2] === "agregados" ? { ...F.agregados, mes } : { ...F.lancamentos, mes, proximo: null } });
      }
      const c = /^\/casa(?:\/(metas|orcamentos)(?:\/([^/]+?)(\/movimentos)?)?)?$/.exec(p);
      if (c) return casaRota(s, r, rr, q, m, c, tratou);
      return r.fulfill({ status: 500, body: "rota fora do contrato: " + m + " " + p });
    });
  };
  return s;
}

const reais = (n) => Number(n).toFixed(2);
/** A casa de mentira: estado em `s.casa`, o mesmo contrato do api/v2/familia_casa.py (escrita devolve a Casa inteira; DELETE 204). Um gancho que responde encerra a rota. */
async function casaRota(s, r, rr, q, m, [, tipo, ref, mov], tratou) {
  if (!tipo) {
    if (s.holdCasa) await s.holdCasa;
    if (await tratou(s.casaLer)) return;
    return s.casa ? r.fulfill({ json: s.casa }) : ERRO(404, "not_found")(r);
  }
  const c = s.casa, lista = c[tipo], corpo = q.postDataJSON?.();
  const achar = () => lista.findIndex((x) => x.ref === decodeURIComponent(ref));
  const faz = (hook) => tratou(hook);
  if (!ref && m === "POST") {
    if (await faz(s.casaCriar)) return;
    lista.push(tipo === "metas"
      ? { ref: `m${++s.seq}`, nome: corpo.nome, alvo: reais(corpo.alvo), guardado: "0.00", falta: reais(corpo.alvo), percentual: 0, prazo: corpo.prazo, autor: "", versao: 1 }
      : { ref: `o${++s.seq}`, categoria: corpo.categoria, limite: reais(corpo.limite), gasto: "0.00", restante: reais(corpo.limite), autor: "", versao: 1 });
    return r.fulfill({ status: 201, json: c });
  }
  const i = achar();
  if (m === "POST" && mov) {
    if (await faz(s.casaMover)) return;
    if (i < 0) return ERRO(404, "not_found")(r);
    const x = lista[i], d = (corpo.tipo === "somar" ? 1 : -1) * Number(corpo.valor), campo = tipo === "metas" ? "guardado" : "gasto";
    if (Number(x[campo]) + d < 0) return ERRO(409, "insufficient")(r);
    x[campo] = reais(Number(x[campo]) + d);
    if (tipo === "metas") { x.falta = reais(Math.max(0, Number(x.alvo) - Number(x.guardado))); x.percentual = Math.floor((Number(x.guardado) * 100) / Number(x.alvo)); } else x.restante = reais(Number(x.limite) - Number(x.gasto));
    return r.fulfill({ json: c });
  }
  if (m === "PUT") {
    if (await faz(s.casaEditar)) return;
    if (i < 0) return ERRO(404, "not_found")(r);
    if (corpo.versao !== lista[i].versao) return ERRO(409, "edit_conflict")(r);
    const { versao, ...novo } = corpo;
    Object.assign(lista[i], novo, { versao: versao + 1 });
    return r.fulfill({ json: c });
  }
  if (m === "DELETE") {
    if (await faz(s.casaApagar)) return;
    if (i < 0) return ERRO(404, "not_found")(r);
    lista.splice(i, 1);
    return r.fulfill({ status: 204, body: "" });
  }
  return r.fulfill({ status: 500, body: "rota da casa fora do contrato: " + m });
}

export async function abrirFamilia({ width = 1280, height = 800, plano = "plus", visao = null, esperar = true, ...opts } = {}) {
  const s = servidor({ visao });
  const p = await abrir({ guia: "concluido", width, height, plano, rota: "/conta/familia", antes: async (ctx) => { await opts.antes?.(ctx, s); await s.antes(ctx); } });
  const { page } = p;
  page.setDefaultTimeout(8000);
  if (esperar) {
    await page.locator(".conta-familia > *").first().waitFor();
    // o 1º filho pode ser o "Carregando…": sob carga o teste lia a seção antes de a família chegar
    await page.locator(".conta-familia [role=status]", { hasText: "Carregando" }).waitFor({ state: "detached" });
  }
  return {
    ...p, s,
    secao: page.locator(".conta-familia"),
    bt: (nome) => page.getByRole("button", { name: nome, exact: true }),
    dlg: () => page.getByRole("dialog"),
    // o que se lê da tela: o texto visível da seção
    texto: async () => (await page.locator(".conta-familia").innerText()).replace(/\s+/g, " ").trim(),
    escritas: () => s.reqs.filter((q) => q.metodo !== "GET"),
  };
}

/** O titular com a Maria (resumo liberado) e o painel dela aberto: o 1º GET de agregados já chegou. */
export async function abrirVer({ membro = MARIA, ...opts } = {}) {
  const t = await abrirFamilia({ visao: titular({ membros: [membro] }), ...opts });
  await t.bt("Ver").click();
  await t.page.getByText("R$ 1.234,56").first().waitFor();
  t.dados = (assunto = "") => t.s.reqs.filter((q) => q.metodo === "GET" && q.caminho.startsWith("/membros/") && q.caminho.endsWith(assunto));
  return t;
}

/** A tela da casa (#/casa). `casa`: o corpo do GET (null = sem família). */
export async function abrirCasa({ width = 1280, height = 800, plano = "plus", casa = F.casa, visao = null, esperar = true, ...opts } = {}) {
  const s = servidor({ visao });
  s.casa = casa === null ? null : structuredClone(casa);
  const p = await abrir({ guia: "concluido", width, height, plano, rota: "/casa", antes: async (ctx) => { await opts.antes?.(ctx, s); await s.antes(ctx); } });
  const { page } = p;
  page.setDefaultTimeout(8000);
  if (esperar) {
    await page.locator(".fam-casa > *").first().waitFor();
    await page.locator(".fam-casa [role=status]", { hasText: "Carregando" }).waitFor({ state: "detached" });
  }
  return {
    ...p, s,
    secao: page.locator(".fam-casa"),
    bt: (nome) => page.getByRole("button", { name: nome, exact: true }),
    dlg: () => page.getByRole("dialog"),
    texto: async () => (await page.locator(".fam-casa").innerText()).replace(/\s+/g, " ").trim(),
    escritas: () => s.reqs.filter((q) => q.metodo !== "GET"),
    leituras: () => s.reqs.filter((q) => q.metodo === "GET" && q.caminho === "/casa"),
  };
}

// O SSE de mentira: a 1ª conexão fica presa até `solta()`; as duas primeiras respostas terminam (o navegador reconecta em 300 ms), a 3ª não.
export function sse() {
  const e = { hits: 0 };
  const preso = new Promise((ok) => { e.solta = ok; });
  e.antes = (ctx) => ctx.route("**/api/v2/eventos", async (r) => {
    const n = ++e.hits;
    if (n === 1) await preso;
    r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: `retry: ${n < 3 ? 300 : 600000}\ndata: {"recurso":"tudo"}\n\n` }).catch(() => {});
  });
  return e;
}

/** Roda `fn` com a tela da casa aberta e fecha o contexto; nenhum pageerror. */
export const comCasa = async (opts, fn) => {
  const t = await abrirCasa(opts);
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};
/** Espera (no Node, sem relógio do navegador) a condição ficar verdadeira. */
export async function ate(cond, ms = 8000) {
  const fim = Date.now() + ms;
  while (!cond()) { if (Date.now() > fim) throw new Error("tempo esgotado: " + cond); await new Promise((r) => setTimeout(r, 50)); }
}
