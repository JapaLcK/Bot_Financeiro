/**
 * /painel#/conta/cobranca (conta/Cobranca.tsx, conta/api.ts): a Cobrança da Conta, lida de GET /billing/subscription (fora da /api/v2).
 *   · um caso por estado (cartão, anual, carência, troca agendada, Pix, vitalício, cortesia, sem plano, plano desconhecido): texto, botões
 *     presentes e ausentes; nenhum texto que a rota não garante ("Renova em", "Teste grátis", "Termina em", "Pagamento pendente");
 *   · a data sai por split ("2026-11-01" = 01/11/2026 no fuso de Brasília); o GET vai a /billing/subscription, nunca a /api/v2/billing;
 *   · o portal: UM POST em /billing/portal?voltar=%2Fpainel com o CSRF e a página navega para o https devolvido (javascript:, http:, vazio e
 *     ausente não navegam); erros em texto (502 do servidor, 401 sessão, 403 CSRF, rede, prazo) e o botão volta.
 * O desfazer da troca, a leitura que falha, o bfcache e o layout: dashboard_v2_conta_cobranca_bordas.test.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { JSON_ERRO, SESSAO, ate, portao } from "./_conta_perfil.mjs";
import { STRIPE_URL, cobPadrao, encurtaPrazo } from "./_conta_seguranca.mjs";
import { PROIBIDOS, T25, T27, TROCA, abrirCobranca } from "./_conta_cobranca.mjs";

const browser = navegador();
void browser;

const ESTADOS = [
  ["cartão mensal", {}, "Plus Mensal Período atual até 12/11/2026", ["Gerenciar assinatura", "Ver planos"]],
  ["cartão anual", { cob: { ...cobPadrao(), plan: "pro", interval: "annual" }, plano: "pro" }, "Pro Anual Período atual até 12/11/2026", ["Gerenciar assinatura", "Ver planos"]],
  ["essencial", { cob: { ...cobPadrao(), plan: "essencial" }, plano: "essencial" }, "Essencial Mensal Período atual até 12/11/2026", ["Gerenciar assinatura", "Ver planos"]],
  ["carência (o /me diz free, a assinatura segue viva)", { plano: "free" }, "Plus Mensal Período atual até 12/11/2026", ["Gerenciar assinatura", "Ver planos"]],
  ["cartão sem data de fim", { cob: { ...cobPadrao(), current_period_end: null } }, "Plus Mensal", ["Gerenciar assinatura", "Ver planos"]],
  ["troca agendada", { cob: { ...cobPadrao(), scheduled_change: TROCA } }, "Plus Mensal Período atual até 12/11/2026 Muda para Pro em 12/12/2026 Desfazer",
    ["Desfazer a troca para Pro", "Gerenciar assinatura", "Ver planos"]],
  ["Pix anual", { cob: { active: true, lifetime: false, gateway: "pix", plan: "plus", interval: "annual", current_period_end: "2027-03-05", scheduled_change: null } },
    "Plus Anual Pix Válido até 05/03/2027", ["Ver planos"]],
  ["vitalício", { cob: { active: true, lifetime: true, plan: "plus", interval: null, current_period_end: null, scheduled_change: null } }, "Plus Vitalício", []],
  ["cortesia (admin/legacy)", { cob: { active: false }, plano: "pro" }, "Pro", ["Ver planos"]],
  ["sem plano", { cob: { active: false }, plano: "free" }, "Sem plano", ["Ver planos"]],
  ["plano desconhecido do servidor (nunca o texto cru)", { cob: { ...cobPadrao(), plan: "pro_max" }, plano: "free" }, "Mensal Período atual até 12/11/2026", ["Gerenciar assinatura", "Ver planos"]],
  ["plano desconhecido com o /me conhecido: cai no nome do tier", { cob: { ...cobPadrao(), plan: "pro_max" }, plano: "plus" }, "Plus Mensal Período atual até 12/11/2026", ["Gerenciar assinatura", "Ver planos"]],
  ["troca para plano desconhecido: a linha não aparece (nunca 'Muda para undefined')", { cob: { ...cobPadrao(), scheduled_change: { ...TROCA, plan: "pro_max" } } }, "Plus Mensal Período atual até 12/11/2026", ["Gerenciar assinatura", "Ver planos"]],
  ["troca para o MESMO plano com outro intervalo: nome + selo", { cob: { ...cobPadrao(), scheduled_change: { ...TROCA, plan: "plus", interval: "annual" } } },
    "Plus Mensal Período atual até 12/11/2026 Muda para Plus Anual em 12/12/2026 Desfazer", ["Desfazer a troca para Plus Anual", "Gerenciar assinatura", "Ver planos"]],
  ["troca para OUTRO plano com outro intervalo: só o nome", { cob: { ...cobPadrao(), scheduled_change: { ...TROCA, interval: "annual" } } },
    "Plus Mensal Período atual até 12/11/2026 Muda para Pro em 12/12/2026 Desfazer", ["Desfazer a troca para Pro", "Gerenciar assinatura", "Ver planos"]],
];
for (const [nome, opts, texto, acoes] of ESTADOS) {
  test(`estado: ${nome}`, async () => {
    const p = await abrirCobranca(opts);
    const t = await p.tela();
    const gets = p.s.reqs.filter((q) => new URL(q.url).pathname.includes("billing")).map((q) => `${q.metodo} ${new URL(q.url).pathname}`);
    await p.fecha();
    assert.equal(t.texto, texto);
    assert.deepEqual(t.acoes, acoes);
    assert.ok(!PROIBIDOS.test(t.texto), t.texto);
    assert.ok(!/pro_max|undefined/i.test(t.texto), t.texto);
    assert.deepEqual(gets, ["GET /billing/subscription"]); // nunca /api/v2/billing/...
  });
}

test("a data é a do servidor, por split: 2026-11-12 é 12/11/2026 com o navegador no fuso de Brasília (new Date(iso) daria 11/11)", async () => {
  const p = await abrirCobranca({ cob: { ...cobPadrao(), current_period_end: "2026-11-01" } });
  const t = await p.tela();
  await p.fecha();
  assert.match(t.texto, /Período atual até 01\/11\/2026/);
});

test("data fora de AAAA-MM-DD some (a linha não aparece), sem quebrar a página", async () => {
  for (const fim of ["12/11/2026", "2026-13-40", 1789000000, "", {}]) {
    const p = await abrirCobranca({ cob: { ...cobPadrao(), current_period_end: fim } });
    const t = await p.tela();
    await p.fecha();
    assert.equal(t.texto, "Plus Mensal", JSON.stringify(fim));
  }
});

// ─── o portal ────────────────────────────────────────────────────────────────

test("portal: UM POST em /billing/portal?voltar=%2Fpainel com o CSRF, sem corpo, e a página vai para o https devolvido", async () => {
  const p = await abrirCobranca();
  await p.bt("Gerenciar assinatura").click();
  await p.page.waitForURL(STRIPE_URL);
  await p.fecha();
  assert.equal(p.s.portais.length, 1);
  const { metodo, url, csrf, corpo } = p.s.portais[0];
  assert.deepEqual([metodo, new URL(url).pathname + new URL(url).search, csrf, corpo], ["POST", "/billing/portal?voltar=%2Fpainel", "tok-123", null]);
});

test("portal: com a navegação ao Stripe sem completar, o 2º clique NÃO faz outro POST e o botão fica aria-disabled; a volta do bfcache solta", async () => {
  const p = await abrirCobranca();
  // 204 = o navegador fica na página (navegação que não completa): o POST respondeu 200 com o https e a página segue aqui
  await p.ctx.route("https://billing.stripe.test/**", (r) => r.fulfill({ status: 204 }));
  const b = p.bt("Gerenciar assinatura");
  await b.click();
  await ate(p.page, () => p.s.portais.length === 1, "o POST saiu");
  await p.page.waitForTimeout(300);
  await b.click({ force: true });
  await b.dblclick({ force: true });
  await p.page.waitForTimeout(300);
  const [posts, preso, url] = [p.s.portais.length, await b.getAttribute("aria-disabled"), p.page.url()];
  await p.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await p.soltou(".conta-cobranca .btn-primary"); // a volta do bfcache soltou o botão
  await p.fecha();
  assert.equal(posts, 1);
  assert.equal(preso, "true");
  assert.match(url, /#\/conta\/cobranca$/);
});

test("portal: duplo clique, dois click() no mesmo tick e clique com o POST em voo = UM POST", async () => {
  const p = await abrirCobranca();
  const [segura, solta] = portao();
  p.s.portal = async (r) => { await segura; return r.fulfill({ status: 502, json: { detail: T25 } }); };
  const b = p.bt("Gerenciar assinatura");
  await b.evaluate((e) => { e.click(); e.click(); });
  await b.dblclick({ force: true });
  assert.equal(await b.getAttribute("aria-disabled"), "true");
  await p.page.waitForTimeout(300);
  assert.equal(p.s.portais.length, 1);
  solta();
  await p.alerta.waitFor();
  await p.fecha();
  assert.equal(p.s.portais.length, 1);
});

for (const [rotulo, url] of [["javascript:", "javascript:alert(1)"], ["http:", "http://billing.stripe.test/p/x"], ["vazio", ""], ["ausente", undefined], ["não é texto", { a: 1 }]]) {
  test(`portal: portal_url ${rotulo} não navega e mostra o erro`, async () => {
    const p = await abrirCobranca();
    p.s.portal = (r) => r.fulfill({ json: url === undefined ? {} : { portal_url: url } });
    await p.bt("Gerenciar assinatura").click();
    await p.alerta.waitFor();
    const [texto, href, acoes] = [await p.alerta.innerText(), p.page.url(), (await p.tela()).acoes];
    await p.fecha();
    assert.equal(texto, T25);
    assert.match(href, /#\/conta\/cobranca$/);
    assert.ok(acoes.includes("Gerenciar assinatura"));
  });
}

test("portal: 502 com texto do servidor sai como chegou; 404 'Sem assinatura ativa.' também; o botão volta e a página relê", async () => {
  for (const [status, texto] of [[502, T25], [404, "Sem assinatura ativa."], [429, "Muitas tentativas. Aguarde alguns minutos e tente novamente."], [503, "Pagamentos ainda não configurados."]]) {
    const p = await abrirCobranca();
    p.s.portal = JSON_ERRO(status, texto);
    await p.bt("Gerenciar assinatura").click();
    await p.alerta.waitFor();
    const lidoAntes = p.s.cobGets;
    await ate(p.page, () => p.s.cobGets > lidoAntes - 1 && p.s.cobGets >= 2, `releitura (${status})`);
    const [msg, desab] = [await p.alerta.innerText(), await p.bt("Gerenciar assinatura").getAttribute("aria-disabled")];
    await p.fecha();
    assert.equal(msg, texto);
    assert.equal(desab, null, "o botão não voltou");
  }
});

test("portal: 403 (CSRF) = 'Recarregue a página e tente de novo.'; 401 de sessão = 'Sua sessão terminou.' + Recarregar; rede e 500 = o texto reserva", async () => {
  const casos = [
    [JSON_ERRO(403, "CSRF token missing or invalid"), T27, false],
    [JSON_ERRO(401, "expirou", SESSAO), "Sua sessão terminou.", true],
    [(r) => r.abort("internetdisconnected"), T25, false],
    [JSON_ERRO(500, "Internal Server Error"), T25, false],
  ];
  for (const [gancho, texto, recarregar] of casos) {
    const p = await abrirCobranca();
    p.s.portal = gancho;
    await p.bt("Gerenciar assinatura").click();
    await p.alerta.waitFor();
    const [msg, rec] = [await p.alerta.innerText(), await p.alerta.getByRole("button", { name: "Recarregar", exact: true }).count()];
    await p.fecha();
    assert.ok(msg.startsWith(texto), `${msg} / ${texto}`);
    assert.equal(rec > 0, recarregar, texto);
  }
});

test("portal: POST que nunca responde (prazo de 30 s, encurtado) = o texto reserva e o botão volta a funcionar", async () => {
  const p = await abrirCobranca();
  await encurtaPrazo(p.page, 400);
  const [segura, solta] = portao();
  p.s.portal = async (r) => { await segura; return r.fulfill({ json: { portal_url: STRIPE_URL } }).catch(() => {}); };
  await p.bt("Gerenciar assinatura").click();
  await p.alerta.waitFor();
  const [msg, desab] = [await p.alerta.innerText(), await p.bt("Gerenciar assinatura").getAttribute("aria-disabled")];
  p.s.portal = null; // o 2º clique responde na hora
  await p.bt("Gerenciar assinatura").click();
  await p.page.waitForURL(STRIPE_URL);
  solta();
  await p.fecha();
  assert.equal(msg, T25);
  assert.equal(desab, null);
  assert.equal(p.s.portais.length, 2);
});
