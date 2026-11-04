/**
 * O que os testes da Família da Conta (dashboard_v2_familia*.test.mjs) compartilham: o servidor de mentira da api/v2/familia.py
 * (mesmos status, códigos de envelope e corpos; as fixtures são as de api_v2_respostas.json, validadas pelos modelos Pydantic)
 * e o `abrirFamilia`. Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
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
  const s = { visao, nonce: NONCE, reqs: [], tokens: 0, hold: null, get: null, criar: null, convidar: null, revogar: null, remover: null, sair: null, previa: null, dissolver: null, liberar: null, revogarP: null, historico: null, agregados: null, lancamentos: null, holdAgregados: null };
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
      return r.fulfill({ status: 500, body: "rota fora do contrato: " + m + " " + p });
    });
  };
  return s;
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
