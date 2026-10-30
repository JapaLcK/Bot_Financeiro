/**
 * Grupo 7, PR-5b: os botões "Conectar WhatsApp" abrem o WhatsApp com o CÓDIGO
 * (`vincular NNNNNN`), e as páginas públicas não prometem mais "mande um oi".
 *
 * O servidor monta o link (`core/dashboard_links.link_whatsapp`); o front só abre
 * o `whatsapp_link` que o `/auth/link-code` devolve. Aqui o `/auth/link-code` é
 * falso e o que se mede é o que o navegador FAZ com a resposta: desktop abre
 * `window.open(url)`, mobile navega (`location.href`). Páginas reais, a 1280 px
 * (UA desktop) e a 390 px (UA de iPhone, que é o que o `isMobile` do código lê).
 *
 * Controle NEGATIVO (medido, ver o relato): trocar o `data.whatsapp_link` por um
 * link fixo em `connectWhatsAppFromHome` deixa os casos de /home vermelhos; voltar
 * as páginas estáticas à copy antiga ("Mande um oi") deixa o último caso vermelho.
 * CEGUEIRA: o WhatsApp real abrindo com o texto preenchido, só no aparelho.
 *
 * Rodar: node --test tests/frontend/whatsapp_conectar_codigo.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";

let server, browser, origin;
before(async () => { ({ proc: server, origin } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const segura = async (fn) => { try { await fn(); } catch { /* navegou/fechou */ } };
const CODIGO = "482913";
const LINK = `https://api.whatsapp.com/send?phone=5511999990000&text=${encodeURIComponent(`vincular ${CODIGO}`)}`;
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148";
const textoDe = (url) => new URL(url).searchParams.get("text");

const VIEWS = [
  { nome: "desktop 1280", viewport: { width: 1280, height: 900 }, ua: undefined, mobile: false },
  { nome: "mobile 390", viewport: { width: 390, height: 844 }, ua: IPHONE, mobile: true },
];

// Abre `caminho` com a API falsa, roda `acionar(page)` e devolve a URL do WhatsApp que o front tentou abrir.
async function abrir(view, caminho, acionar, link = LINK) {
  const ctx = await browser.newContext({ viewport: view.viewport, userAgent: view.ua });
  const page = await ctx.newPage();
  const errs = [], chamadas = [], aberturas = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.addInitScript(() => { window.open = (u) => { (window.__abertas ||= []).push(u); return null; }; });
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "api.whatsapp.com") { aberturas.push(url.href); return segura(() => route.abort()); }
    if (url.origin !== origin) return segura(() => route.abort());
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return segura(() => route.continue());
    if (url.pathname === "/auth/link-code") {
      chamadas.push(route.request().method());
      return segura(() => route.fulfill(json({ link_code: CODIGO, whatsapp_link: link, expires_in_minutes: 15 })));
    }
    if (url.pathname === "/auth/validate") return segura(() => route.fulfill(json({ user_id: 1 })));
    if (url.pathname === "/auth/dashboard-profile") return segura(() => route.fulfill(json({ email: "a@b.c", plan: "plus" })));
    if (url.pathname.startsWith("/data/")) return segura(() => route.fulfill(json({
      balance: 0, of_bank_balance: 0, monthly_income: 0, monthly_expense: 0, credit_cards: [], pockets: [],
      investments: [], alerts: [], recent_launches: [], launches_pagination: { page: 1, limit: 25, total: 0, total_pages: 1 } })));
    if (url.pathname.startsWith("/history/")) return segura(() => route.fulfill(json({ data: [] })));
    return segura(() => route.fulfill(json({})));
  });
  // "domcontentloaded": o /dashboard.html mantém conexões abertas e o "load" nunca chega.
  await page.goto(`${origin}${caminho}`, { waitUntil: "domcontentloaded" });
  await acionar(page);
  const alerta = await page.locator(".pig-modal[role=dialog], #generic-confirm-overlay.open #generic-confirm-body").first().textContent({ timeout: 1500 }).catch(() => "");
  const navegou = page.url();
  await page.waitForFunction(() => (window.__abertas || []).length > 0, null, { timeout: 4000 }).catch(() => {});
  await page.waitForTimeout(view.mobile ? 400 : 0);
  const abertas = await page.evaluate(() => window.__abertas || []);
  await ctx.close();
  return { url: abertas[0] || aberturas[0] || null, abertas, aberturas, chamadas, errs, alerta, navegou };
}

function confere(r, view) {
  assert.deepEqual(r.chamadas, ["POST"], "devia gerar o código no clique (POST /auth/link-code)");
  assert.ok(r.url, "o front não abriu o WhatsApp");
  assert.equal(textoDe(r.url), `vincular ${CODIGO}`, r.url);
  // desktop abre pela window.open; mobile navega (a request ao api.whatsapp.com)
  assert.equal(view.mobile ? r.aberturas.length > 0 : r.abertas.length > 0, true, `${view.nome}: caminho errado`);
  assert.deepEqual(r.errs, []);
}

for (const view of VIEWS) {
  test(`${view.nome}: /home, menu da conta, abre o WhatsApp com "vincular ${CODIGO}"`, async () => {
    const r = await abrir(view, "/home.html", async (page) => {
      await page.waitForFunction(() => document.querySelector("#stat-balance-sub")?.textContent.trim());
      await page.locator("#user-menu-btn").click();
      await page.locator("#user-dropdown").getByRole("button", { name: "Conectar WhatsApp" }).click();
    });
    confere(r, view);
  });

  // O menu lateral só é alcançável por toque no desktop (>=901px); no navegador mobile ele
  // fica fora da tela e o hambúrguer só existe no app nativo (`html.pb-app`). No mobile o
  // caminho de um toque é o item do checklist da Início.
  test(`${view.nome}: /home, ${view.mobile ? "item do checklist" : "botão WhatsApp do menu lateral"}`, async () => {
    const r = await abrir(view, "/home.html", async (page) => {
      await page.waitForFunction(() => document.querySelector("#stat-balance-sub")?.textContent.trim());
      const alvo = view.mobile
        ? page.locator(".ob-item.todo").filter({ hasText: "Conectar WhatsApp" })
        : page.locator('.sidenav-item[onclick*="connectWhatsAppFromHome"]');
      await alvo.click();
    });
    confere(r, view);
  });

  test(`${view.nome}: menu da conta das páginas públicas (nav-auth.js)`, async () => {
    const r = await abrir(view, "/contato.html", async (page) => {
      await page.locator("#pb-acct-btn").click();
      await page.getByRole("button", { name: "Conectar WhatsApp" }).click();
    });
    confere(r, view);
  });
}

test("páginas públicas: sem a promessa de 'mande um oi', com o código", async () => {
  const lerHtml = (f) => readFileSync(`frontend/${f}`, "utf8");
  const velhas = ["primeiro oi", "Um oi. E a organização", "mesmo número</strong> do seu WhatsApp",
    "Mande um oi", "Seu número conecta a conversa à conta", "Use o mesmo número para reunir",
    "Sim. Use no cadastro o mesmo número", "Use no cadastro o mesmo número do seu WhatsApp. Abra", "Use no cadastro o mesmo número do seu WhatsApp. Depois"];
  for (const f of ["whatsapp.html", "como-funciona.html", "suporte.html"]) {
    for (const v of velhas) assert.ok(!lerHtml(f).includes(v), `${f} ainda diz: ${v}`);
  }
  const ctx = await browser.newContext();
  try {
    for (const [caminho, trechos] of [
      ["/whatsapp.html", ["Conecte pelo site", "Envie o código", "Pode ser outro número", "gerado por você, no site", "Antes de conectar", "conecte o WhatsApp."]],
      ["/como-funciona.html", ["vincular pbl…", "Conectar WhatsApp"]],
      ["/suporte.html", ["Conectar WhatsApp", "mensagem que já vem pronta com o seu código"]],
    ]) {
      const page = await ctx.newPage();
      await page.goto(`${origin}${caminho}`);
      const corpo = (await page.locator("body").textContent()).replace(/\s+/g, " ");
      for (const t of trechos) assert.ok(corpo.includes(t), `${caminho} não mostra: ${t}`);
      await page.close();
    }
  } finally { await ctx.close(); }
});

// Sem WHATSAPP_NUMBER o servidor devolve `whatsapp_link: ""` (falha fechada). O front NÃO pode
// abrir aba em branco nem navegar para "": `window.open("")` abre about:blank.
for (const view of VIEWS) {
  test(`${view.nome}: link vazio (sem número no servidor) não abre aba em branco no /home`, async () => {
    const r = await abrir(view, "/home.html", async (page) => {
      await page.waitForFunction(() => document.querySelector("#stat-balance-sub")?.textContent.trim());
      await page.locator("#user-menu-btn").click();
      await page.locator("#user-dropdown").getByRole("button", { name: "Conectar WhatsApp" }).click();
      await page.waitForTimeout(500);
    }, "");
    assert.deepEqual(r.chamadas, ["POST"]);
    assert.deepEqual(r.abertas, [], "abriu janela com link vazio");
    assert.deepEqual(r.aberturas, []);
    assert.match(r.alerta, /ainda não configurado/, "devia avisar em vez de ficar mudo");
    assert.deepEqual(r.errs, []);
  });

  test(`${view.nome}: painel (dashboard.html) abre o WhatsApp com "vincular ${CODIGO}"`, async () => {
    const r = await abrir(view, "/dashboard.html", async (page) => {
      await page.waitForFunction(() => typeof connectWhatsAppFromDashboard === "function");
      await page.evaluate(() => { connectWhatsAppFromDashboard(); });  // sem await: o alerta só resolve no OK
    });
    confere(r, view);
  });

  test(`${view.nome}: link vazio não abre aba em branco no painel (dashboard.html)`, async () => {
    const r = await abrir(view, "/dashboard.html", async (page) => {
      await page.waitForFunction(() => typeof connectWhatsAppFromDashboard === "function");
      await page.evaluate(() => { connectWhatsAppFromDashboard(); });  // sem await: o alerta só resolve no OK
      await page.waitForTimeout(500);
    }, "");
    assert.deepEqual(r.chamadas, ["POST"]);
    assert.deepEqual(r.abertas, [], "abriu janela com link vazio");
    assert.deepEqual(r.aberturas, []);
    assert.match(r.alerta, /ainda não configurado/, "a guarda do painel sumiu");
  });
}
