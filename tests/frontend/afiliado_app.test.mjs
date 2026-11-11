/**
 * A área do afiliado no /app (frontend/dashboard.js, `_renderAffiliateView`): é o que o WebView do iOS abre. Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";
import { ATIVA, COM_SAQUE, ZERO } from "./_afiliado.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body, status = 200) => (r) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

async function abrir({ corpo = ATIVA, url = "/dashboard.html", width = 1280, height = 800, clipboard, marca } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  if (marca) await ctx.addCookies([{ name: "pb_conta", value: marca, url: ORIGIN }]);
  await ctx.route("**/auth/validate", json({ ok: true, user_id: 42 }));
  await ctx.route("**/auth/me", json({ app_access: true, plan_tier: "pro", dashboard_v2_enabled: true }));
  await ctx.route("**/auth/dashboard-profile", json({ email: "ana@x.com", plan: "pro", feature_gates: {} }));
  await ctx.route("**/api/affiliate/me", corpo ? json(corpo) : json({ detail: "x" }, 404));
  await ctx.route("**cdnjs.cloudflare.com/**", (r) => r.abort());
  const page = await ctx.newPage();
  await page.addInitScript(() => { window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} }; });
  if (clipboard) await page.addInitScript(clipboard);
  await page.goto(`${ORIGIN}${url}`);
  await page.waitForFunction(() => typeof window.PBRefresh === "function", null, { timeout: 15000 });
  return { ctx, page };
}
const naArea = async (page) => {
  await page.waitForFunction(() => document.getElementById("sidenav-affiliate")?.style.display !== "none", null, { timeout: 10000 });
  await page.evaluate(() => window.navigateTo("affiliate"));
  await page.locator("#affiliate-link-input").waitFor();
};

// mutação: voltar o rótulo a "Afiliados" (dashboard.html), ou mostrar o item antes do 200
test("o item 'Afiliado' só aparece com o 200 de /api/affiliate/me", async () => {
  const a = await abrir();
  try {
    await a.page.waitForFunction(() => document.getElementById("sidenav-affiliate")?.style.display !== "none");
    assert.equal((await a.page.locator("#sidenav-affiliate .sn-label").textContent()).trim(), "Afiliado");
  } finally { await a.ctx.close(); }
  const b = await abrir({ corpo: null });
  try {
    await b.page.waitForTimeout(400);
    assert.equal(await b.page.locator("#sidenav-affiliate").evaluate((e) => e.style.display), "none");
  } finally { await b.ctx.close(); }
});

// mutação: tirar `view !== "affiliate"` do init: o deep link abriria a área antes de o item existir
test("?view=affiliate é ignorado: fica na Visão Geral", async () => {
  const { ctx, page } = await abrir({ url: "/dashboard.html?view=affiliate" });
  try {
    await page.waitForTimeout(400);
    assert.equal(await page.locator("#affiliate-view.active").count(), 0);
  } finally { await ctx.close(); }
});

// mutação: tirar o quadro "Clientes que pagaram" ou o `code` do `.map`: a tela volta a não ter os dois
test("os quadros, o código com copiar, a regra certa e os rótulos do pagamento", async () => {
  const { ctx, page } = await abrir({ clipboard: () => { window.__copiado = []; Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copiado.push(t); } } }); } });
  try {
    await naArea(page);
    const labels = await page.locator("#affiliate-stats .stat-label").allInnerTexts();
    assert.deepEqual(labels.map((l) => l.toLowerCase()), ["cadastros indicados", "clientes que pagaram", "comissões geradas", "disponível pra saque", "em carência", "em saque", "já pago"]);
    const area = await page.locator("#affiliate-view").innerText();
    assert.match(area, /10% da primeira cobrança do plano no cartão/);
    assert.match(area, /Pix não gera comissão/);
    assert.doesNotMatch(area, /por cobrança|PigBank\+/);
    assert.match(area, /libera 30 dias/);
    assert.match(area, /pagamento reembolsado/);
    assert.match(area, /reembolso parcial/);
    assert.equal(await page.locator("#affiliate-code-input").inputValue(), "ANA2026");
    await page.getByRole("button", { name: "Copiar código" }).click();
    await page.getByRole("button", { name: "Copiar link" }).click();
    await page.waitForFunction(() => window.__copiado.length === 2);
    assert.deepEqual(await page.evaluate(() => window.__copiado), ["ANA2026", ATIVA_LINK]);
  } finally { await ctx.close(); }
});
const ATIVA_LINK = ATIVA.link;

// mutação: voltar `data.commission_percent || 10`: 0% vira 10%
test("afiliado 0%: 'sem comissão neste programa', sem nenhum percentual", async () => {
  const { ctx, page } = await abrir({ corpo: ZERO });
  try {
    await naArea(page);
    const area = await page.locator("#affiliate-view").innerText();
    assert.match(area, /sem comissão neste programa/);
    assert.doesNotMatch(area, /10\s*%/);
  } finally { await ctx.close(); }
});

const CHAVE = "ana@pix.com";
async function pedir(page, ctx, posts) {
  await ctx.route("**/api/affiliate/payout", (r) => { posts.push(r.request().postData()); return r.fulfill({ json: { ok: true, payout: { id: 9 } } }); });
  await naArea(page);
  await page.locator("#affiliate-pix-input").fill(CHAVE);
  await page.locator("#affiliate-payout-btn").click();
  await page.locator("#generic-confirm-overlay.open").waitFor();
}

// mutação: tirar o `confirmModal` de requestAffiliatePayout: o POST sai no primeiro clique, sem valor nem chave à vista
test("saque no /app: a confirmação mostra valor e chave antes do POST; o corpo é exatamente {pix_key}", async () => {
  const { ctx, page } = await abrir();
  try {
    const posts = [];
    await pedir(page, ctx, posts);
    const texto = await page.locator("#generic-confirm-body").innerText();
    assert.match(texto, /R\$\s*120,00/);
    assert.ok(texto.includes(CHAVE));
    assert.deepEqual(posts, [], "nada sai antes de confirmar");
    await page.locator("#generic-confirm-ok").click();
    await page.waitForFunction(() => !document.querySelector("#generic-confirm-overlay.open"));
    await page.waitForTimeout(150);
    assert.deepEqual(posts, [JSON.stringify({ pix_key: CHAVE })]);
  } finally { await ctx.close(); }
});

// mutação: ignorar a resposta do `confirmModal`: cancelar manda o POST
test("saque no /app: cancelar não manda nada, mantém a chave e libera o botão", async () => {
  const { ctx, page } = await abrir();
  try {
    const posts = [];
    await pedir(page, ctx, posts);
    await page.locator("#generic-confirm-cancel").click();
    await page.waitForTimeout(150);
    assert.deepEqual(posts, []);
    assert.equal(await page.locator("#affiliate-pix-input").inputValue(), CHAVE);
    assert.equal(await page.locator("#affiliate-payout-btn").isEnabled(), true);
  } finally { await ctx.close(); }
});

// mutação: tirar o `btn.disabled = true` de requestAffiliatePayout: com o POST em voo, o Sacar abre outra confirmação
test("saque no /app: com o POST em voo o botão fica inerte, e sai 1 POST só", async () => {
  const { ctx, page } = await abrir();
  try {
    const posts = [];
    let solta;
    const preso = new Promise((r) => { solta = r; });
    await ctx.route("**/api/affiliate/payout", async (r) => { posts.push(r.request().postData()); await preso; return r.fulfill({ json: { ok: true, payout: { id: 9 } } }); });
    await naArea(page);
    await page.locator("#affiliate-pix-input").fill(CHAVE);
    await page.locator("#affiliate-payout-btn").click();
    await page.locator("#generic-confirm-ok").click();
    await page.waitForFunction(() => !document.querySelector("#generic-confirm-overlay.open"));
    await page.locator("#affiliate-payout-btn").evaluate((b) => b.click());
    await page.waitForTimeout(150);
    assert.equal(await page.locator("#generic-confirm-overlay.open").count(), 0);
    solta();
    await page.waitForTimeout(150);
    assert.equal(posts.length, 1);
  } finally { await ctx.close(); }
});

// mutação: gravar a chave em sessionStorage no requestAffiliatePayout, ou logar no console
test("saque no /app: a chave não vai a storage, cookie, URL nem console", async () => {
  const { ctx, page } = await abrir();
  try {
    const logs = [];
    page.on("console", (m) => logs.push(m.text()));
    await pedir(page, ctx, []);
    await page.locator("#generic-confirm-ok").click();
    await page.waitForTimeout(200);
    const onde = await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }, document.cookie, location.href]));
    assert.ok(!onde.includes(CHAVE), onde);
    assert.ok(!logs.some((l) => l.includes(CHAVE)), "console");
  } finally { await ctx.close(); }
});

// mutação: tirar `word-break` do `#generic-confirm-body`: a chave sem espaço sai do modal e o fim dela some, justo o que a pessoa precisa conferir
for (const [width, height, chave] of [[320, 568, `${"a".repeat(35)}@pigbank.dev`], [320, 568, "k".repeat(140)], [1280, 800, "k".repeat(140)]]) {
  test(`${width}: a confirmação do /app mostra a chave de ${chave.length} caracteres inteira, dentro do modal`, async () => {
    const { ctx, page } = await abrir({ width, height });
    try {
      await naArea(page);
      await page.locator("#affiliate-pix-input").fill(chave);
      await page.locator("#affiliate-payout-btn").click();
      await page.locator("#generic-confirm-overlay.open").waitFor();
      const m = await page.locator("#generic-confirm-body").evaluate((b) => {
        const r = document.createRange(); r.selectNodeContents(b);
        const t = r.getBoundingClientRect(), caixa = b.closest(".modal").getBoundingClientRect();
        return { texto: b.textContent, fim: t.right, modal: caixa.right, baixo: t.bottom, jan: innerHeight, janL: innerWidth, sw: b.scrollWidth, cw: b.clientWidth };
      });
      assert.ok(m.texto.includes(chave), "a chave inteira");
      assert.ok(m.fim <= m.modal + 0.5 && m.sw <= m.cw && m.modal <= m.janL && m.baixo <= m.jan, JSON.stringify({ ...m, texto: undefined }));
      const ok = await page.locator("#generic-confirm-ok").boundingBox();
      assert.ok(ok.y + ok.height <= height, "o Confirmar à vista");
    } finally { await ctx.close(); }
  });
}

// mutação: desligar o botão antes do `confirmModal`: o modal guarda o BODY como foco, e o Esc não volta ao botão
test("Esc na confirmação do saque devolve o foco ao botão Sacar", async () => {
  const { ctx, page } = await abrir();
  try {
    await pedir(page, ctx, []);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.querySelector("#generic-confirm-overlay.open"));
    assert.equal(await page.evaluate(() => document.activeElement?.id), "affiliate-payout-btn");
  } finally { await ctx.close(); }
});

// mutação: tirar a conferência de `pb_conta` de requestAffiliatePayout: a chave digitada na tela da conta A vira saque da conta B
test("a conta trocou com a confirmação aberta: nenhum POST, e a página recarrega", async () => {
  const { ctx, page } = await abrir({ marca: "7" });
  try {
    const posts = [];
    await pedir(page, ctx, posts);
    let recargas = 0;
    page.on("framenavigated", (f) => { if (f === page.mainFrame()) recargas += 1; });
    await page.evaluate(() => { document.cookie = "pb_conta=8; path=/"; });
    await page.locator("#generic-confirm-ok").click();
    await page.waitForTimeout(400);
    assert.deepEqual(posts, [], "nenhum POST com a conta de outra pessoa");
    assert.ok(recargas >= 1, "a página recarrega");
  } finally { await ctx.close(); }
});

// positivo do mesmo conserto: com o marcador igual, o saque segue saindo
test("com o marcador da conta igual ao da carga, o saque sai", async () => {
  const { ctx, page } = await abrir({ marca: "7" });
  try {
    const posts = [];
    await pedir(page, ctx, posts);
    await page.locator("#generic-confirm-ok").click();
    await page.waitForTimeout(200);
    assert.equal(posts.length, 1);
  } finally { await ctx.close(); }
});

// mutação: voltar a `${data.commission_percent}%`: o /app mostra "12.5%", com ponto
test("percentual fracionado sai em português, igual ao /painel", async () => {
  const { ctx, page } = await abrir({ corpo: { ...ATIVA, commission_percent: 12.5 } });
  try {
    await naArea(page);
    const area = await page.locator("#affiliate-view").innerText();
    assert.match(area, /12,5% da primeira cobrança/);
    assert.doesNotMatch(area, /12\.5/);
  } finally { await ctx.close(); }
});

// mutação: tirar `word-break` da linha do saque: a nota sem espaço empurra a página para o lado
for (const width of [320, 1280]) {
  test(`${width}: nota de rejeição sem espaço quebra no /app`, async () => {
    const corpo = { ...ATIVA, payouts: [{ id: 7, amount: 90, status: "rejected", requested_at: "2026-09-10T12:00:00+00:00", paid_at: null, note: "x".repeat(240) }] };
    const { ctx, page } = await abrir({ width, height: 800, corpo });
    try {
      await naArea(page);
      const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      assert.ok(m.sw <= m.cw, JSON.stringify(m));
      const dentro = await page.getByText("Motivo da rejeição").evaluate((n) => { const c = n.closest(".mock-card").getBoundingClientRect(); return n.getBoundingClientRect().right <= c.right + 0.5; });
      assert.equal(dentro, true);
    } finally { await ctx.close(); }
  });
}

// mutação: voltar o subtítulo a "reembolsos: …": `stats.refunded_clients` some do /app e ele diverge do /painel
test("clientes reembolsados: a mesma unidade do /painel, e os pagamentos total e parcial à parte", async () => {
  const { ctx, page } = await abrir();
  try {
    await naArea(page);
    const tile = page.locator("#affiliate-stats .stat-tile", { hasText: /clientes que pagaram/i });
    assert.match(await tile.innerText(), /1 reembolsado\b/);
    assert.match(await page.locator("#affiliate-body").innerText(), /Reembolsos: 2 totais · 1 parcial/);
  } finally { await ctx.close(); }
  const b = await abrir({ corpo: { ...ATIVA, stats: { ...ATIVA.stats, refunded_clients: 0 } } });
  try {
    await naArea(b.page);
    assert.match(await b.page.locator("#affiliate-stats .stat-tile", { hasText: /clientes que pagaram/i }).innerText(), /pagamento confirmado/);
  } finally { await b.ctx.close(); }
  const c = await abrir({ corpo: { ...ATIVA, stats: { ...ATIVA.stats, refunded_clients: 3 } } });
  try {
    await naArea(c.page);
    assert.match(await c.page.locator("#affiliate-stats").innerText(), /3 reembolsados/);
  } finally { await c.ctx.close(); }
});

// mutação: tirar a condição do bloco de saque: 0% sem saldo volta a ver "Sacar R$ 0,00"
test("afiliado 0% sem saldo: sem bloco de saque; com saldo antigo ou saque aberto, o bloco aparece", async () => {
  const a = await abrir({ corpo: ZERO });
  try {
    await naArea(a.page);
    assert.equal(await a.page.locator("#affiliate-payout-btn").count(), 0);
    assert.equal(await a.page.locator("#affiliate-pix-input").count(), 0);
    assert.doesNotMatch(await a.page.locator("#affiliate-body").innerText(), /Solicitar saque|Divulgue seu link/i);
  } finally { await a.ctx.close(); }
  for (const extra of [{ available: 80 }, { requested: 80 }]) {
    const b = await abrir({ corpo: { ...ZERO, stats: { ...ZERO.stats, ...extra } } });
    try {
      await naArea(b.page);
      assert.equal(await b.page.locator("#affiliate-payout-btn").count(), 1, JSON.stringify(extra));
    } finally { await b.ctx.close(); }
  }
  const c = await abrir({ corpo: { ...ATIVA, stats: { ...ATIVA.stats, available: 0 } } }); // quem ganha comissão vê o bloco com saldo zero
  try {
    await naArea(c.page);
    assert.equal(await c.page.locator("#affiliate-payout-btn").count(), 1);
  } finally { await c.ctx.close(); }
});

// mutação: tirar `_notaDoLimite`, ou deixá-la sem o limite: a lista cortada em 50 fica sem aviso, ou a curta ganha um falso
test("'Mostrando as mais recentes' só no limite da API: 50 comissões e 20 saques", async () => {
  const a = await abrir();
  try {
    await naArea(a.page);
    assert.doesNotMatch(await a.page.locator("#affiliate-body").innerText(), /Mostrando (as|os) mais recentes/);
  } finally { await a.ctx.close(); }
  const c0 = ATIVA.commissions[0], p0 = ATIVA.payouts[0];
  const b = await abrir({ corpo: { ...ATIVA, commissions: Array.from({ length: 50 }, (_, i) => ({ ...c0, id: i + 1 })), payouts: Array.from({ length: 20 }, (_, i) => ({ ...p0, id: i + 1 })) } });
  try {
    await naArea(b.page);
    const area = await b.page.locator("#affiliate-body").innerText();
    assert.match(area, /Mostrando as mais recentes/);
    assert.match(area, /Mostrando os mais recentes/);
  } finally { await b.ctx.close(); }
});

// mutação: tirar o quadro "Em saque" de `_renderAffiliateView`
test("o quadro 'Em saque' traz o valor", async () => {
  const { ctx, page } = await abrir({ corpo: COM_SAQUE });
  try {
    await naArea(page);
    const tile = page.locator("#affiliate-stats .stat-tile", { hasText: /em saque/i });
    assert.match(await tile.locator(".stat-value").innerText(), /120,00/);
  } finally { await ctx.close(); }
});

// mutação: tirar `payout_id` do `_affiliateCommissionStatus`: a comissão em saque aparece como disponível; tirar o `!Number(s.requested)`: o botão libera
test("comissão em saque mostra 'em saque' e o botão fica desligado", async () => {
  const { ctx, page } = await abrir({ corpo: { ...COM_SAQUE, stats: { ...COM_SAQUE.stats, available: 200 } } });
  try {
    await naArea(page);
    assert.match(await page.locator("#affiliate-body").innerText(), /em saque/);
    assert.equal(await page.locator("#affiliate-payout-btn").isDisabled(), true);
  } finally { await ctx.close(); }
});

// mutação: tirar o `flex-wrap`/`min-width:0` dos inputs: os 7 quadros e o link longo passam da tela
for (const [width, height] of [[390, 844], [320, 568]]) {
  test(`${width}x${height}: os 7 quadros e o link longo cabem, sem rolagem lateral`, async () => {
    const { ctx, page } = await abrir({ width, height, corpo: { ...ATIVA, link: `${ATIVA.link}/${"x".repeat(60)}` } });
    try {
      await naArea(page);
      assert.equal(await page.locator("#affiliate-stats .stat-tile").count(), 7);
      const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, tiles: [...document.querySelectorAll("#affiliate-stats .stat-tile")].filter((t) => t.getBoundingClientRect().right > innerWidth + 0.5).length }));
      assert.ok(m.sw <= m.cw, JSON.stringify(m));
      assert.equal(m.tiles, 0);
    } finally { await ctx.close(); }
  });
}
