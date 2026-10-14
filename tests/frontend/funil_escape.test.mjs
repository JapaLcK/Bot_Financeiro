/**
 * `/admin/funil`: todo texto que vem da API entra no DOM como TEXTO, nunca como HTML.
 *
 * A API devolve strings livres em `canal`/`origem` e nos links (`painel`, `olhar`,
 * `url`). Aqui a resposta é substituída por uma com payload hostil em todas elas e
 * em campos que deveriam ser número/data; o teste mede o DOM renderizado (nenhum
 * <img> injetado, nenhum handler disparado, nenhum href `javascript:`), não o texto
 * do arquivo.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const XSS = '<img src=x onerror="window.__xss=1">';
const linha = (extra) => ({ cadastros: 1, viram_precos: 1, abriram_checkout: 1, concluiram: 1,
                            taxa_conclusao: 1, ...extra });
const janela = (dias) => ({
  dias, inicio: XSS, viram_precos_medido: false, emails_verificacao: XSS,
  etapas: ["cadastros", "viram_precos", "abriram_checkout", "concluiram"]
    .map((id) => ({ id, n: 1, taxa_etapa: 1, taxa_acum: 1 })),
  abandono: XSS, expiraram_sem_concluir: XSS,
  estado_atual: { free: XSS, trial: 0, paying: 0, past_due: 0, canceled: 0, granted: 0 },
  canais: [linha({ canal: XSS })], origens: [linha({ origem: XSS })],
  checkout: { pessoas: XSS, sessoes_abertas: 0, sessoes_concluidas: 0, sessoes_expiradas: 0, conversao: null },
  ativacao: { concluiram: 0, onboarding: 0, whatsapp: 0, lancamento: 0 },
  trial: { iniciaram: 0, em_trial: 0, pagando: 0, cancelaram: 0, outros: 0 },
  pix: { gerados: 0, pagos: 0, expirados: 0, cancelados: 0, abertos: 0, taxa_pago: null },
  ebook: { entregas: 0, enviados: 0, nao_comprou: 0, estornados: 0, pendentes: 0 },
  teste: { clicaram: 0, abriram: 0, organicos: 0, responderam: 0, no_limite: 0, clicaram_checkout: 0 },
});
const RESPOSTA = {
  gerado_em: "2026-10-08T12:00:00+00:00", viewed_pricing_desde: XSS,
  janelas: { "7d": janela(7), "30d": janela(30) },
  atraso: { total: XSS, alem_carencia: 0, carencia_dias: XSS },
  links: [
    { painel: XSS, url: "https://x.test/", olhar: XSS },
    { painel: XSS, url: 'javascript:window.__xss=1', olhar: XSS },
    { painel: "aspas", url: 'https://x.test/" onmouseover="window.__xss=1', olhar: XSS },
  ],
};

test("texto hostil da API vira texto, não HTML", async () => {
  const page = await browser.newPage();
  await page.route("**/admin/api/funil", (r) => r.fulfill({
    contentType: "application/json", body: JSON.stringify(RESPOSTA) }));
  // as fontes externas têm teste próprio (funil_fontes.test.mjs); aqui só não vão ao servidor
  await page.route("**/admin/api/funil/fonte/*", (r) => r.fulfill({
    contentType: "application/json", body: JSON.stringify({ estado: XSS, mensagem: XSS }) }));
  await page.goto(`${ORIGIN}/funil.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".card");
  const v = await page.evaluate(() => ({
    imgs: document.querySelectorAll("#main img").length,
    js: document.querySelectorAll('a[href^="javascript"]').length,
    handlers: document.querySelectorAll("#main [onerror], #main [onmouseover]").length,
    texto: document.getElementById("main").textContent,
  }));
  await page.waitForTimeout(200); // dá tempo de um onerror disparar, se existisse
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  assert.deepEqual([v.imgs, v.js, v.handlers], [0, 0, 0]);
  // o payload aparece como texto literal onde era texto livre (links e tabelas)
  assert.ok(v.texto.includes("<img src=x"), "o texto hostil deveria aparecer escapado");
  await page.close();
});
