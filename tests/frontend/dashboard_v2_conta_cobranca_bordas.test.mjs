/**
 * /painel#/conta/cobranca (conta/Cobranca.tsx), as bordas: desfazer a troca agendada, a leitura que falha, o bfcache e o layout.
 *   · desfazer: UM POST (duplo clique e rota deixada/reaberta: a trava é do módulo), a linha some antes da releitura, 400 no_change relê sem erro;
 *   · contrato quebrado = erro de carga com "Tentar de novo"; 401 numa releitura esconde o dado; `pageshow` persistido relê; foco da aba não;
 *   · a trava do portal: navegação interrompida solta no prazo de segurança, o bfcache solta com a página desmontada e nunca solta um desfazer em voo;
 *   · layout em 1280×800 e 390×844.
 * Os estados, a data e o portal: dashboard_v2_conta_cobranca.test.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { navegador } from "./_guia.mjs";
import { JSON_ERRO, SESSAO, ate, portao } from "./_conta_perfil.mjs";
import { cobPadrao } from "./_conta_seguranca.mjs";
import { T4, T26, T27, T28, TROCA, abrirCobranca, encurtaSeguranca } from "./_conta_cobranca.mjs";

const browser = navegador();
void browser;

// ─── desfazer a troca agendada ───────────────────────────────────────────────

const comTroca = (extra = {}) => ({ cob: { ...cobPadrao(), scheduled_change: TROCA }, ...extra });
const desfazer = (p) => p.bt("Desfazer a troca para Pro");

test("desfazer: UM POST com o CSRF; a linha da troca some ANTES da releitura, o status diz T28 e sai um GET depois", async () => {
  const p = await abrirCobranca(comTroca());
  const [segura, solta] = portao();
  p.s.cobHold = segura; // a releitura fica presa: o que se vê agora é o setQueryData
  const antes = p.s.cobGets;
  await desfazer(p).click();
  await p.page.getByText("Muda para Pro").waitFor({ state: "detached" });
  await ate(p.page, () => p.s.cobGets === antes + 1, "a releitura saiu");
  const [aviso, texto] = [await p.aviso.innerText(), (await p.tela()).texto];
  solta();
  await p.fecha();
  assert.equal(aviso, T28);
  assert.equal(texto, "Plus Mensal Período atual até 12/11/2026");
  assert.equal(p.s.desfazem.length, 1);
  assert.deepEqual([p.s.desfazem[0].metodo, new URL(p.s.desfazem[0].url).pathname, p.s.desfazem[0].csrf, p.s.desfazem[0].corpo], ["POST", "/billing/cancel-change", "tok-123", null]);
});

test("desfazer: duplo clique e dois click() no mesmo tick = UM POST", async () => {
  const p = await abrirCobranca(comTroca());
  const [segura, solta] = portao();
  p.s.desfaz = async (r) => { await segura; p.s.cob.scheduled_change = null; return r.fulfill({ json: { ok: true } }); };
  await desfazer(p).evaluate((e) => { e.click(); e.click(); });
  await desfazer(p).dblclick({ force: true });
  await p.page.waitForTimeout(300);
  solta();
  await p.page.getByText("Muda para Pro").waitFor({ state: "detached" });
  await p.fecha();
  assert.equal(p.s.desfazem.length, 1);
});

test("desfazer: sair da rota e voltar com o POST em voo continua UM POST (a trava é do módulo, não da instância)", async () => {
  const p = await abrirCobranca(comTroca());
  const [segura, solta] = portao();
  p.s.desfaz = async (r) => { await segura; p.s.cob.scheduled_change = null; return r.fulfill({ json: { ok: true } }); };
  await desfazer(p).click();
  await ate(p.page, () => p.s.desfazem.length === 1, "o POST saiu");
  await p.page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await p.page.locator("#page-title", { hasText: "Bancos conectados" }).waitFor();
  await p.page.evaluate(() => { location.hash = "#/conta/cobranca"; });
  await desfazer(p).waitFor();
  await desfazer(p).click({ force: true });
  await p.page.waitForTimeout(400);
  const n = p.s.desfazem.length;
  solta();
  await p.fecha();
  assert.equal(n, 1);
});

test("desfazer: remontar a página com o POST em voo = botão aria-disabled na instância nova, e a falha do POST aparece nela", async () => {
  const p = await abrirCobranca(comTroca());
  const [segura, solta] = portao();
  const TEXTO = "O Stripe está instável agora. Tenta de novo em instantes.";
  p.s.desfaz = async (r) => { await segura; return JSON_ERRO(503, TEXTO)(r); };
  await desfazer(p).click();
  await ate(p.page, () => p.s.desfazem.length === 1, "o POST saiu");
  await p.page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await p.page.locator("#page-title", { hasText: "Bancos conectados" }).waitFor();
  await p.page.evaluate(() => { location.hash = "#/conta/cobranca"; });
  await desfazer(p).waitFor();
  const [preso, portal] = [await desfazer(p).getAttribute("aria-disabled"), await p.bt("Gerenciar assinatura").getAttribute("aria-disabled")];
  solta();
  await p.alerta.waitFor();
  const msg = await p.alerta.innerText();
  await p.soltou('.conta-cobranca button[aria-label^="Desfazer"]'); // o botão voltou
  await p.fecha();
  assert.deepEqual([preso, portal, msg], ["true", "true", TEXTO]);
});

test("desfazer: remontar DEPOIS da falha não traz o erro velho de volta", async () => {
  const p = await abrirCobranca(comTroca());
  p.s.desfaz = JSON_ERRO(503, "O Stripe está instável agora. Tenta de novo em instantes.");
  await desfazer(p).click();
  await p.alerta.waitFor();
  await p.soltou('.conta-cobranca button[aria-label^="Desfazer"]'); // o botão voltou
  await p.page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await p.page.locator("#page-title", { hasText: "Bancos conectados" }).waitFor();
  await p.page.evaluate(() => { location.hash = "#/conta/cobranca"; });
  await desfazer(p).waitFor();
  const n = await p.alerta.count();
  await p.fecha();
  assert.equal(n, 0);
});

test("desfazer: 400 no_change (já desfeito em outra aba) relê SEM erro e a linha some", async () => {
  const p = await abrirCobranca(comTroca());
  p.s.desfaz = (r) => { p.s.cob.scheduled_change = null; return JSON_ERRO(400, { error: "no_change", message: "Não há troca agendada." })(r); };
  const antes = p.s.cobGets;
  await desfazer(p).click();
  await p.page.getByText("Muda para Pro").waitFor({ state: "detached" });
  await ate(p.page, () => p.s.cobGets === antes + 1, "releitura");
  const [alertas, aviso] = [await p.alerta.count(), await p.aviso.innerText()];
  await p.fecha();
  assert.deepEqual([alertas, aviso], [0, ""]);
});

test("desfazer: 409 no_subscription também relê sem erro", async () => {
  const p = await abrirCobranca(comTroca());
  p.s.desfaz = (r) => { p.s.cob = { active: false }; return JSON_ERRO(409, { error: "no_subscription" })(r); };
  await desfazer(p).click();
  await p.page.getByText("Sem plano", { exact: false }).or(p.page.getByText("Plus", { exact: true })).first().waitFor();
  const alertas = await p.alerta.count();
  await p.fecha();
  assert.equal(alertas, 0);
});

test("desfazer: 503 com texto sai como chegou e a linha da troca fica; 403 = T27; 500 = o texto reserva; o botão volta", async () => {
  for (const [gancho, texto] of [[JSON_ERRO(503, "O Stripe está instável agora. Tenta de novo em instantes."), "O Stripe está instável agora. Tenta de novo em instantes."],
    [JSON_ERRO(403, "CSRF token missing or invalid"), T27], [JSON_ERRO(500, "x"), T26], [JSON_ERRO(502, T26), T26]]) {
    const p = await abrirCobranca(comTroca());
    p.s.desfaz = gancho;
    await desfazer(p).click();
    await p.alerta.waitFor();
    await ate(p.page, () => p.s.cobGets >= 2, "releitura");
    const [msg, linha, desab] = [await p.alerta.innerText(), await p.page.getByText("Muda para Pro em 12/12/2026").count(), await desfazer(p).getAttribute("aria-disabled")];
    await p.fecha();
    assert.equal(msg, texto);
    assert.equal(linha, 1);
    assert.equal(desab, null);
  }
});

// ─── leitura que falha ───────────────────────────────────────────────────────

test("contrato quebrado ([], {}, active não boolean, HTML, degraded) = erro de carga com 'Tentar de novo', que relê", async () => {
  const corpos = [(r) => r.fulfill({ json: [] }), (r) => r.fulfill({ json: {} }), (r) => r.fulfill({ json: { active: "sim" } }),
    (r) => r.fulfill({ status: 200, contentType: "text/html", body: "<html>oi</html>" }), (r) => r.fulfill({ json: { active: false, degraded: true } })];
  for (const gancho of corpos) {
    const p = await abrirCobranca({ esperar: false });
    p.s.cobGetErro = gancho;
    await p.page.reload();
    await p.alerta.waitFor();
    const [msg, vistos] = [await p.alerta.innerText(), await p.page.locator(".conta-cobranca dl").count()];
    const antes = p.s.cobGets;
    p.s.cobGetErro = null;
    await p.bt("Tentar de novo").click();
    await p.page.locator(".conta-cobranca dl").waitFor();
    await p.fecha();
    assert.ok(msg.startsWith(T4), msg);
    assert.equal(vistos, 0);
    assert.ok(p.s.cobGets > antes);
  }
});

test("carregou e a releitura falha (403, 500): a tela NÃO mostra o dado velho, mostra o erro de carga com 'Tentar de novo'", async () => {
  for (const gancho of [JSON_ERRO(403, "x"), JSON_ERRO(500, "x")]) {
    const p = await abrirCobranca();
    p.s.cobGetErro = gancho;
    await p.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
    await p.alerta.waitFor({ timeout: 20000 });
    const [msg, dl, tentar] = [await p.alerta.innerText(), await p.page.locator(".conta-cobranca dl").count(), await p.bt("Tentar de novo").count()];
    await p.fecha();
    assert.ok(msg.startsWith(T4), msg);
    assert.deepEqual([dl, tentar], [0, 1]);
  }
});

test("5xx na carga repete e depois mostra o erro; 401 = sessão terminou + Recarregar; 403 = erro de carga", async () => {
  for (const [gancho, texto, rec] of [[JSON_ERRO(500, "x"), T4, false], [JSON_ERRO(401, "expirou", SESSAO), "Sua sessão terminou.", true], [JSON_ERRO(403, "x"), T4, false]]) {
    const p = await abrirCobranca({ esperar: false });
    p.s.cobGetErro = gancho;
    await p.page.reload();
    await p.alerta.waitFor({ timeout: 20000 });
    const [msg, r] = [await p.alerta.innerText(), await p.alerta.getByRole("button", { name: "Recarregar", exact: true }).count()];
    await p.fecha();
    assert.ok(msg.startsWith(texto), msg);
    assert.equal(r > 0, rec);
  }
});

test("401 numa releitura depois de carregar: o dado anterior some e aparece 'Sua sessão terminou.'", async () => {
  const p = await abrirCobranca();
  p.s.cobGetErro = JSON_ERRO(401, "expirou", SESSAO);
  await p.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await p.alerta.waitFor();
  const [msg, dl] = [await p.alerta.innerText(), await p.page.locator(".conta-cobranca dl").count()];
  await p.fecha();
  assert.ok(msg.startsWith("Sua sessão terminou."), msg);
  assert.equal(dl, 0);
});

test("voltar do Stripe pelo bfcache (pageshow persisted) relê; pageshow comum não", async () => {
  const p = await abrirCobranca();
  const n0 = p.s.cobGets;
  await p.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: false })));
  await p.page.waitForTimeout(300);
  const comum = p.s.cobGets;
  p.s.cob.plan = "pro";
  await p.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await ate(p.page, () => p.s.cobGets === n0 + 1, "a releitura do bfcache");
  await p.page.getByText("Pro", { exact: false }).first().waitFor();
  const texto = (await p.tela()).texto;
  await p.fecha();
  assert.equal(comum, n0);
  assert.match(texto, /^Pro Mensal/);
});

test("foco da aba não relê (cada leitura chama o Stripe de 1 a 4 vezes)", async () => {
  const p = await abrirCobranca();
  const n0 = p.s.cobGets;
  await p.page.evaluate(() => { document.dispatchEvent(new Event("visibilitychange", { bubbles: true })); window.dispatchEvent(new Event("focus")); });
  await p.page.waitForTimeout(500);
  await p.fecha();
  assert.equal(p.s.cobGets, n0);
});

// ─── a trava do portal ───────────────────────────────────────────────────────

const pageshow = (p) => p.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
const irPara = async (p, hash, titulo) => { await p.page.evaluate((h) => { location.hash = h; }, hash); await p.page.locator("#page-title", { hasText: titulo }).waitFor(); };

test("portal: navegação ao Stripe interrompida (sem erro nem pageshow) solta a trava no prazo de segurança (10 s, encurtado); o clique seguinte faz outro POST", async () => {
  const p = await abrirCobranca();
  await encurtaSeguranca(p.page, 600);
  await p.ctx.route("https://billing.stripe.test/**", (r) => r.fulfill({ status: 204 })); // a navegação não completa (Parar / window.stop())
  const b = p.bt("Gerenciar assinatura");
  await b.click();
  await ate(p.page, () => p.s.portais.length === 1, "o POST saiu");
  const preso = await b.getAttribute("aria-disabled");
  await p.soltou(".conta-cobranca .btn-primary");
  await b.click();
  await ate(p.page, () => p.s.portais.length === 2, "o 2º POST");
  const pedido = await p.page.evaluate(() => window.__seg);
  await p.fecha();
  assert.equal(preso, "true");
  assert.deepEqual(pedido[0], 10000);
});

test("portal: o bfcache solta a trava mesmo com a Cobrança desmontada (o ouvinte é do módulo)", async () => {
  const p = await abrirCobranca();
  await p.ctx.route("https://billing.stripe.test/**", (r) => r.fulfill({ status: 204 }));
  await p.bt("Gerenciar assinatura").click();
  await ate(p.page, () => p.s.portais.length === 1, "o POST saiu");
  await irPara(p, "#/conta/bancos", "Bancos conectados");
  await pageshow(p);
  await irPara(p, "#/conta/cobranca", "Cobrança");
  const solto = await p.bt("Gerenciar assinatura").getAttribute("aria-disabled"); // lido na hora: o prazo de 10 s não pode ter soltado
  await p.fecha();
  assert.equal(solto, null);
});

test("desfazer: o pageshow persistido NÃO solta um desfazer em voo (um 2º POST seria possível)", async () => {
  const p = await abrirCobranca(comTroca());
  const [segura, solta] = portao();
  p.s.desfaz = async (r) => { await segura; p.s.cob.scheduled_change = null; return r.fulfill({ json: { ok: true } }); };
  await desfazer(p).click();
  await ate(p.page, () => p.s.desfazem.length === 1, "o POST saiu");
  await pageshow(p);
  await p.page.waitForTimeout(300);
  const preso = await desfazer(p).getAttribute("aria-disabled");
  await desfazer(p).click({ force: true });
  await p.page.waitForTimeout(300);
  const n = p.s.desfazem.length;
  solta();
  await p.page.getByText("Muda para Pro").waitFor({ state: "detached" });
  await p.fecha();
  assert.deepEqual([preso, n], ["true", 1]);
});

test("desfazer 503 -> a releitura falha (erro de carga) -> 'Tentar de novo' com leitura boa: o alerta velho do 503 não volta", async () => {
  const p = await abrirCobranca(comTroca());
  p.s.desfaz = JSON_ERRO(503, "O Stripe está instável agora. Tenta de novo em instantes.");
  p.s.cobGetErro = JSON_ERRO(403, "x");
  await desfazer(p).click();
  await p.alerta.filter({ hasText: T4 }).waitFor();
  p.s.cobGetErro = null;
  await p.bt("Tentar de novo").click();
  await p.page.locator(".conta-cobranca dl").waitFor();
  const alertas = await p.alerta.count();
  await p.fecha();
  assert.equal(alertas, 0);
});

// ─── layout ──────────────────────────────────────────────────────────────────

for (const [width, height] of [[1280, 800], [390, 844]]) {
  test(`layout em ${width}×${height}: troca agendada + carência ao mesmo tempo, sem rolagem lateral, alvos de 44px, tudo dentro do painel`, async () => {
    const p = await abrirCobranca({ width, height, plano: "free", cob: { ...cobPadrao(), plan: "essencial", interval: "annual", scheduled_change: { plan: "pro", interval: "annual", effective_at: "2026-12-12" } } });
    const m = await p.page.evaluate(() => {
      const painel = document.querySelector(".conta-cobranca").getBoundingClientRect();
      const alvos = [...document.querySelectorAll(".conta-cobranca .btn")].map((e) => e.getBoundingClientRect());
      return { lateral: document.documentElement.scrollWidth - innerWidth, secao: document.querySelector(".conta-cobranca").scrollWidth - document.querySelector(".conta-cobranca").clientWidth,
        pequenos: alvos.filter((a) => a.height < 43.5).length, alvos: alvos.length,
        fora: [...document.querySelectorAll(".conta-cobranca *")].filter((e) => { const r = e.getBoundingClientRect(); return r.width && (r.right > painel.right + 0.5 || r.left < painel.left - 0.5); }).length };
    });
    await p.page.screenshot({ path: join(process.env.TMPDIR ?? "/tmp", `cobranca-${width}.png`), fullPage: true });
    const texto = (await p.tela()).texto;
    await p.fecha();
    assert.deepEqual({ lateral: m.lateral, secao: m.secao, pequenos: m.pequenos, alvos: m.alvos, fora: m.fora }, { lateral: 0, secao: 0, pequenos: 0, alvos: 3, fora: 0 }, JSON.stringify(m));
    assert.equal(texto, "Essencial Anual Período atual até 12/11/2026 Muda para Pro em 12/12/2026 Desfazer");
  });
}
