/**
 * Cartão do banco (Open Finance) não tem "Pagar fatura" manual (B2, 2b): o detalhe da fatura troca o
 * botão pela frase (`aviso_pagamento`, que a rota devolve) e o modal de pagar só lista fatura de cartão manual.
 *
 * Markup REAL (dashboard.html) + dashboard.css real; fetch mockado; desktop 1280x800 e mobile 375x812.
 * NEGATIVOS: sem o `style.display` do botão no detalhe -> (a) vermelho; sem devolver o `display` do botão quando não há aviso -> (c) vermelho; sem o `.filter` em `openPayBillModal` -> (d) e (e) vermelhos; sem `payBillState.aviso` em `renderPayBillList` -> (e) vermelho.
 * POSITIVOS: (b) fatura de cartão manual mantém o botão e esconde a frase; (d) a manual segue na lista.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { abrirBrowser, fecharBrowser, loadDashboardJs, DASHBOARD_JS } from "./_dashboard_loader.mjs";

before(abrirBrowser);
after(fecharBrowser);

const HTML = readFileSync(DASHBOARD_JS.replace("dashboard.js", "dashboard.html"), "utf8");
const DETALHE = HTML.slice(HTML.indexOf('<div class="overlay" id="bill-detail-overlay">'), HTML.indexOf("<!-- ─── Pagar fatura"));
const PAGAR = HTML.match(/<div class="overlay" id="pay-bill-overlay">[\s\S]*?\n<\/div>\n/)[0];
const CSS = DASHBOARD_JS.replace("dashboard.js", "dashboard.css");
const FRASE = "Cartão do banco (Open Finance) não tem pagamento manual de fatura: quando você paga no banco, a fatura atualiza aqui sozinha na próxima sincronização.";

const bill = (id, banco) => ({ id, card_name: banco ? "Nubank" : "Manual", label: "Out/2026", status: "open",
  total: 140, paid_amount: 0, due_amount: 140, aviso_pagamento: banco ? FRASE : null });

async function pagina(viewport) {
  const page = await loadDashboardJs({ pageOptions: { viewport } });
  // o loader já põe um stub para cada id do dashboard: sai o stub, entra o markup real
  await page.evaluate((html) => {
    const t = document.createElement("template");
    t.innerHTML = html;
    for (const el of t.content.querySelectorAll("[id]")) document.getElementById(el.id)?.remove();
    document.body.append(t.content);
  }, DETALHE + PAGAR);
  await page.addStyleTag({ path: CSS });
  return page;
}

/** Abre o detalhe de `b` e devolve o que o usuário vê. */
async function detalhe(page, b) {
  return page.evaluate(async (b) => {
    csrfHeaders = () => ({});
    window.fetch = () => Promise.resolve({ ok: true, json: async () => ({ ok: true, bill: b, transactions: [] }) });
    await openBillDetailModal(b.id);
    const btn = document.getElementById("bill-detail-pay-btn");
    const av = document.getElementById("bill-detail-pay-aviso");
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const m = document.querySelector("#bill-detail-overlay .modal");
    return { botao: vis(btn), aviso: vis(av), textoAviso: av.textContent, overflow: m.scrollWidth > m.clientWidth};
  }, b);
}

async function modalPagar(page, bills) {
  return page.evaluate(async (bills) => {
    csrfHeaders = () => ({});
    window.fetch = () => Promise.resolve({ ok: true, json: async () => ({ ok: true, balance: 1000, bills }) });
    await openPayBillModal();
    const vazio = document.getElementById("pay-bill-empty");
    return { ids: [...document.querySelectorAll("#pay-bill-list .bill-opt")].map((e) => Number(e.dataset.id)),
             vazioVisivel: vazio.style.display !== "none", textoVazio: document.getElementById("pay-bill-empty-txt").textContent };
  }, bills);
}

for (const [rotulo, viewport] of [["desktop 1280x800", { width: 1280, height: 800 }], ["mobile 375x812", { width: 375, height: 812 }]]) {
  const caso = (nome, fn) => test(`${rotulo}: ${nome}`, async () => {
    const page = await pagina(viewport);
    await fn(page);
    assert.deepEqual(page.__errs, []);
    await page.close();
  });

  caso("(a) detalhe de fatura do banco: sem botão, com a frase, sem overflow", async (page) => {
    const d = await detalhe(page, bill(1, true));
    assert.deepEqual([d.botao, d.aviso, d.textoAviso, d.overflow], [false, true, FRASE, false]);
  });

  caso("(b) detalhe de fatura manual: botão visível e sem frase (controle positivo)", async (page) => {
    const d = await detalhe(page, bill(2, false));
    assert.deepEqual([d.botao, d.aviso], [true, false]);
  });

  caso("(c) banco e depois manual na mesma página: o botão volta", async (page) => {
    await detalhe(page, bill(1, true));
    const d = await detalhe(page, bill(2, false));
    assert.deepEqual([d.botao, d.aviso], [true, false]);
  });

  caso("(d) modal de pagar lista só a fatura manual", async (page) => {
    const m = await modalPagar(page, [bill(1, true), bill(2, false)]);
    assert.deepEqual(m.ids, [2]);
  });

  caso("(e) só cartão do banco: lista vazia com o estado vazio visível", async (page) => {
    const m = await modalPagar(page, [bill(1, true)]);
    assert.deepEqual([m.ids, m.vazioVisivel, m.textoVazio], [[], true, FRASE]);
  });

  caso("(f) sem nenhuma fatura: o estado vazio mantém o texto de sempre (controle positivo)", async (page) => {
    const m = await modalPagar(page, []);
    assert.deepEqual([m.vazioVisivel, m.textoVazio], [true, "Nenhuma fatura em aberto."]);
  });
}
