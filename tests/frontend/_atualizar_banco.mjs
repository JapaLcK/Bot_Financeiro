/**
 * O que os testes do "Atualizar agora" do aviso de banco (dashboard_v2_atualizar_banco*.test.mjs) compartilham. Não é `*.test.mjs` de propósito.
 *
 * O relógio é o `clock.install` do Playwright (os timers também são falsos): `avancar` o anda em passos e devolve a vez ao Node para as rotas responderem.
 * Cada fetch para /open-finance é anotado DENTRO da página, com o `Date.now()` falso, então a cadência sai exata (não janela). `window.__voo` conta os fetches em voo.
 * O snapshot, o `/bancos` e o POST respondem pelas variáveis de `s` (mutáveis no meio do teste): `s.conexoes`, `s.emDia` (o `/bancos` sem motivo) e `s.refresh`.
 */
import { setTimeout as dorme } from "node:timers/promises";
import { RESPOSTAS, abrirPainel } from "./_painel.mjs";
import { LIMITE, con, servidorBancos } from "./_bancos.mjs";

export const AVISO = "#avisos-topo section.pilha";
export const REGIAO = "#avisos-topo p.sr-only[role=status]";
export const DITO = "Atualizando os dados do banco";
export const FALHA = "Não conseguimos atualizar agora. Tente de novo.";
export const ATUALIZANDO = "O banco está atualizando. Seus dados aparecem aqui sozinhos quando terminar.";
export const AINDA = "O banco está demorando. Seus dados aparecem aqui quando terminar.";
export const PARCIAL = () => con(11, "Nubank", "partial", "Dados parciais");

/** `opts`: `conexoes` (snapshot), `limite`, `limiteRes` (o `(r) => …` do /limite), `refresh` (`(r, n) => …`, n = o nº do POST), `perfil`, `width`, `rota`, `agora`. */
export async function abrirBanco(browser, { conexoes = [PARCIAL()], limite = LIMITE(), limiteRes, refresh, perfil, width = 1440, rota = "/", ...resto } = {}) {
  const s = { conexoes, emDia: false, refresh: undefined, segurar: null, falha: null }; // `segurar`: uma promise que prende a resposta do snapshot; `falha`: `(r) => r.fulfill(...)` que responde o snapshot com erro
  const srv = servidorBancos({
    limite, limiteRes, perfil,
    snap: async (r) => { if (s.segurar) await s.segurar; if (s.falha) return s.falha(r); return r.fulfill({ json: { ok: true, connections: typeof s.conexoes === "function" ? s.conexoes() : s.conexoes } }); },
    bancosRes: (r) => r.fulfill({ json: RESPOSTAS.bancos[s.emDia ? "em_dia" : "pilha_desatualizado"] }),
    refresh: (r) => (refresh ?? s.refresh)?.(r, srv.posts.length) ?? r.fulfill({ json: { ok: true, sync: { ok: true, items: [], still_updating: 0 }, connections: [] } }),
  });
  const p = await abrirPainel(browser, { width, espera: "#page-title", bancos: "pilha_desatualizado", relogio: "instalado", ...resto });
  await srv.antes(p.ctx);
  await p.page.addInitScript(() => {
    window.__voo = 0; window.__req = []; window.__emVoo = 0; window.__maxVoo = 0;
    const f = window.fetch;
    window.fetch = function (u, o) {
      const url = new URL(String((u && u.url) || u), location.href), m = ((o && o.method) || "GET").toUpperCase();
      const conta = url.pathname === "/open-finance/7" && m === "GET";
      window.__req.push([m, url.pathname, Date.now()]);
      window.__voo += 1; if (conta) window.__maxVoo = Math.max(window.__maxVoo, ++window.__emVoo);
      const fim = () => { window.__voo -= 1; if (conta) window.__emVoo -= 1; };
      return f.apply(this, arguments).then((r) => { fim(); return r; }, (e) => { fim(); throw e; });
    };
  });
  const botao = p.page.getByRole("button", { name: /^Atualiza(r agora|ndo…)$/ });
  // `comBotao`: espera a classificação chegar (o botão), para o teste só trocar o snapshot DEPOIS das leituras da carga; sem ele o aviso aparece antes, com o corpo do PR 1.
  const abrir = async (comBotao = true, comAviso = true) => { await p.ir(rota); if (comAviso) await p.page.locator(AVISO).waitFor(); if (comBotao) await botao.waitFor(); await assentar(p.page); };
  return { ...p, s, srv, abrir, botao, width };
}

/** Espera a página assentar: nenhum fetch em voo, duas vezes seguidas. */
export async function assentar(page) {
  for (let quietas = 0; quietas < 2;) {
    await dorme(10);
    quietas = (await page.evaluate(() => window.__voo)) === 0 ? quietas + 1 : 0;
  }
}
/** Anda o relógio falso em passos; a cada passo as respostas chegam antes do próximo. `preso`: há resposta presa de propósito (só devolve a vez por 15 ms). */
export async function avancar(page, ms, passo = 1000, { preso = false } = {}) {
  for (let feito = 0; feito < ms; feito += passo) {
    await page.clock.runFor(Math.min(passo, ms - feito));
    if (preso) await dorme(15); else await assentar(page);
  }
}
/** Os instantes (s, desde o 1º) dos GET do snapshot depois de `desde` (índice na lista de pedidos). */
export const gets = (page, desde = 0) => page.evaluate((d) => window.__req.slice(d).filter(([m, p]) => m === "GET" && p === "/open-finance/7").map(([, , t]) => t), desde);
export const marca = (page) => page.evaluate(() => window.__req.length);
export const pedidos = (page, desde = 0) => page.evaluate((d) => window.__req.slice(d).map(([m, p]) => `${m} ${p}`), desde);
export const texto = (page) => page.locator(`${AVISO} .pilha-corpo`).innerText().then((t) => t.replace(/\s+/g, " ").trim());
export const falado = (page) => page.locator(REGIAO).innerText();
/** Marca o nó (para provar que o texto entra NELE, e que o botão é o mesmo). */
export const marcar = (loc, v = "m") => loc.evaluate((el, x) => { el.dataset.marca = x; }, v);
/** A aba some e volta (o refetch do foco do React Query) e a janela ganha foco. */
export const focar = (page) => page.evaluate(() => {
  const v = (x) => { Object.defineProperty(document, "visibilityState", { value: x, configurable: true }); document.dispatchEvent(new Event("visibilitychange", { bubbles: true })); };
  v("hidden"); v("visible"); window.dispatchEvent(new Event("focus"));
});
/** Para o relógio onde está (os timers só andam em `avancar`): a cadência sai exata. */
export const congelar = async (page) => { const t = await page.evaluate(() => Date.now()); await page.clock.pauseAt(new Date(t + 500)); };
/** O instante (do relógio falso) do n-ésimo POST. */
export const instantePost = (page, n = 0) => page.evaluate((k) => window.__req.filter(([m]) => m === "POST")[k]?.[2], n);
/** Segura uma resposta até `soltar()`. */
export const porta = () => { let soltar; const p = new Promise((r) => { soltar = r; }); return { p, soltar }; };
/** Com o relógio parado, as notificações do React Query (setTimeout 0) só saem quando ele anda: anda de 50 em 50 ms até `pred` (avaliado na página) ser verdadeiro. */
export async function ate(page, pred, arg, { passo = 50, max = 200 } = {}) {
  for (let i = 0; i < max; i++) {
    if (await page.evaluate(pred, arg)) return;
    await page.clock.runFor(passo);
    await dorme(15);
  }
  throw new Error(`ate: a condição não veio: ${pred}`);
}
