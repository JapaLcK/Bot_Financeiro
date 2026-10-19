/**
 * `/admin/feedback`: pergunta, resposta, comentário, rótulo e chaves de motivo vêm de
 * TEXTO DE USUÁRIO. Aqui a resposta da API é trocada por uma com payload hostil em todos
 * eles; o teste mede o DOM renderizado (nenhum <img> injetado, nenhum handler disparado),
 * não o texto do arquivo. Também cobre o 401 -> /admin/login e o "carregar mais".
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
const item = (id) => ({ id, created_at: "2026-10-12T15:00:00+00:00", pergunta: XSS, resposta: XSS,
                        motivo: "outro", rotulo: XSS, texto: XSS });
const RESPOSTA = {
  motivos: { outro: XSS, numero_errado: "Número errado" },
  semanas: [{ semana: "2026-10-12", positivos: 1, negativos: 2, sem_motivo: 0, por_motivo: { outro: 2 } }],
  total: 2, itens: [item(1)],
};

test("texto hostil da API vira texto, não HTML", async () => {
  const page = await browser.newPage();
  await page.route("**/admin/api/feedback*", (r) => r.fulfill({
    contentType: "application/json", body: JSON.stringify(RESPOSTA) }));
  await page.goto(`${ORIGIN}/admin-feedback.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".item");
  await page.waitForTimeout(200); // dá tempo de um onerror disparar, se existisse
  const v = await page.evaluate(() => ({
    imgs: document.querySelectorAll("#main img").length,
    handlers: document.querySelectorAll("#main [onerror]").length,
    texto: document.getElementById("main").textContent,
    xss: window.__xss,
  }));
  assert.equal(v.xss, undefined);
  assert.deepEqual([v.imgs, v.handlers], [0, 0]);
  assert.ok(v.texto.includes("<img src=x"), "o texto hostil deveria aparecer escapado");
  await page.close();
});

test("carregar mais acrescenta a próxima página pelo offset e some no fim", async () => {
  const page = await browser.newPage();
  const pedidos = [];
  await page.route("**/admin/api/feedback*", (r) => {
    const off = Number(new URL(r.request().url()).searchParams.get("offset"));
    pedidos.push(off);
    r.fulfill({ contentType: "application/json",
                body: JSON.stringify({ ...RESPOSTA, semanas: off ? null : RESPOSTA.semanas,  // o servidor só manda o resumo com offset 0
                                    itens: [item(off + 1)], total: 2 }) });
  });
  await page.goto(`${ORIGIN}/admin-feedback.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".item");
  assert.equal(await page.locator(".item").count(), 1);
  await page.click("#mais");
  await page.waitForFunction(() => document.querySelectorAll(".item").length === 2);
  assert.deepEqual(pedidos, [0, 1]);
  assert.equal(await page.locator("#mais").isHidden(), true);
  await page.close();
});

test("401 redireciona para o login do admin", async () => {
  const page = await browser.newPage();
  await page.route("**/admin/api/feedback*", (r) => r.fulfill({ status: 401, body: "{}" }));
  await page.route("**/admin/login", (r) => r.fulfill({ contentType: "text/html", body: "login" }));
  await page.goto(`${ORIGIN}/admin-feedback.html`, { waitUntil: "domcontentloaded" });
  await page.waitForURL("**/admin/login");
  await page.close();
});

test("motivo fora da lista aparece: chave crua na lista e coluna 'Fora da lista' na tabela", async () => {
  const page = await browser.newPage();
  await page.route("**/admin/api/feedback*", (r) => r.fulfill({
    contentType: "application/json", body: JSON.stringify({
      motivos: { outro: "Outro" },
      semanas: [{ semana: "2026-10-12", positivos: 0, negativos: 3, sem_motivo: 0, outros_motivos: 3, por_motivo: {} }],
      total: 1, itens: [{ id: 7, created_at: "2026-10-12T15:00:00+00:00", pergunta: "p", resposta: "r",
                          motivo: "motivo_antigo", rotulo: null, texto: null }] }) }));
  await page.goto(`${ORIGIN}/admin-feedback.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".item");
  assert.match(await page.locator(".item .meta").innerText(), /motivo_antigo/);
  assert.doesNotMatch(await page.locator(".item .meta").innerText(), /Sem motivo/);
  const cab = await page.locator("#semanas th").allInnerTexts();
  const linha = await page.locator("#semanas tr:nth-child(2) td").allInnerTexts();
  assert.equal(linha[cab.findIndex((h) => /fora da lista/i.test(h))], "3");
  await page.close();
});

test("página vazia no 'carregar mais' esconde o botão (não trava)", async () => {
  const page = await browser.newPage();
  await page.route("**/admin/api/feedback*", (r) => {
    const off = Number(new URL(r.request().url()).searchParams.get("offset"));
    r.fulfill({ contentType: "application/json",
                body: JSON.stringify({ ...RESPOSTA, itens: off ? [] : [item(1)], total: 5 }) });
  });
  await page.goto(`${ORIGIN}/admin-feedback.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".item");
  assert.equal(await page.locator("#mais").isVisible(), true);  // 1 de 5: ainda há o que carregar
  await page.click("#mais");
  await page.waitForFunction(() => !document.getElementById("status").textContent);
  assert.equal(await page.locator("#mais").isHidden(), true);
  await page.close();
});
