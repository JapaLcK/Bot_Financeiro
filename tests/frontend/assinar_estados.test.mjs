/**
 * /assinar: a máquina de estados (C, L0, S1, S2, S3, F1), uma linha da tabela por
 * caso, com o POST conferido. O plano B e o embutido estão em assinar_plano_b; a PII
 * em URL, em assinar_pii.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, continuar, CSRF, EMBUTIDO, ME, FRAG, ORIGIN } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const CONTA = "POST /auth/quiz/conta";
const CHECKOUT = "/billing/create-checkout";
const texto = (page, id) => page.locator(`#${id}`).textContent();

for (const query of ["?plano=premium&ciclo=monthly", "?plano=plus"]) {
  test(`L0 com ${query}: link incompleto, sem chamar o /auth/me`, async () => {
    const { ctx, page, reqs } = await abrir(browser, { query, hash: FRAG });
    await tela(page, "l0");
    assert.equal(reqs.filter((r) => r.path === "/auth/me").length, 0);
    await ctx.close();
  });
}

test("positivo do L0: os 3 planos × 2 ciclos carregam o S1 com o nome do plano", async () => {
  for (const plano of ["essencial", "plus", "pro"]) {
    for (const ciclo of ["monthly", "annual"]) {
      const { ctx, page } = await abrir(browser, { query: `?plano=${plano}&ciclo=${ciclo}` });
      await tela(page, "s1");
      assert.match(await texto(page, "s1-titulo"), /^Falta pouco para assinar o (Essencial|Plus|Pro) · (Mensal|Anual)$/);
      await ctx.close();
    }
  }
});

test("criada: corpo e CSRF do POST, CompleteRegistration com o user_id, e S3 embutido", async () => {
  const { ctx, page, posts } = await abrir(browser, { hash: FRAG,
    api: { [CONTA]: [200, { estado: "criada", user_id: 42 }] } });
  await tela(page, "s1");
  assert.equal(await page.inputValue("#nome"), "Ana");
  assert.equal(await page.inputValue("#email"), "ana@x.com");
  assert.equal(await page.inputValue("#whatsapp"), "11987654321");
  await continuar(page);
  await tela(page, "s4");
  const [conta] = posts("/auth/quiz/conta");
  assert.deepEqual(conta.body, { nome: "Ana", email: "ana@x.com", whatsapp: "11987654321", aceitou_termos: true });
  assert.equal(conta.csrf, CSRF);
  assert.deepEqual(posts(CHECKOUT).map((r) => r.body),
    [{ plan: "plus", interval: "monthly", embutido: true, pagina: true, origem: "assinar" }]);
  const fbq = await page.evaluate(() => window.__fbq);
  assert.deepEqual(fbq[0], ["track", "CompleteRegistration", {}, { eventID: "signup_42" }]);
  assert.deepEqual((await page.evaluate(() => window.__ga))[0], ["sign_up", { method: "quiz" }]);
  assert.equal(await texto(page, "conta-email"), "a•••@x.com");
  await ctx.close();
});

for (const [estado, alvo] of [["logado", "s4"], ["tem_conta", "s2"]]) {
  test(`${estado} → ${alvo}`, async () => {
    const { ctx, page } = await abrir(browser, { hash: FRAG, api: { [CONTA]: [200, { estado }] } });
    await tela(page, "s1");
    await continuar(page);
    await tela(page, alvo);
    await ctx.close();
  });
}

test("cadastro_pendente: aviso com o link do cadastro, e nenhum checkout", async () => {
  const { ctx, page, posts } = await abrir(browser, { hash: FRAG,
    api: { [CONTA]: [200, { estado: "cadastro_pendente" }] } });
  await tela(page, "s1");
  await continuar(page);
  await tela(page, "s1-pendente");
  assert.equal(await page.locator("#s1-pendente a").getAttribute("href"), "/cadastro");
  assert.equal(posts(CHECKOUT).length, 0);
  await ctx.close();
});

test("ocupado duas vezes: exatamente 2 POSTs e a mensagem; ocupado e logado → S3", async () => {
  const ocupado = [409, { estado: "ocupado" }];
  let { ctx, page, posts } = await abrir(browser, { hash: FRAG, api: { [CONTA]: [ocupado, ocupado, ocupado] } });
  await tela(page, "s1");
  await continuar(page);
  await page.waitForFunction(() => document.getElementById("s1-erro").textContent !== "");
  assert.equal(await texto(page, "s1-erro"), "Não deu certo agora. Tente de novo em instantes.");
  await page.waitForTimeout(1300);
  assert.equal(posts("/auth/quiz/conta").length, 2);
  await ctx.close();
  ({ ctx, page, posts } = await abrir(browser, { hash: FRAG, api: { [CONTA]: [ocupado, [200, { estado: "logado" }]] } }));
  await tela(page, "s1");
  await continuar(page);
  await tela(page, "s4");
  assert.equal(posts("/auth/quiz/conta").length, 2);
  await ctx.close();
});

for (const [status, corpo, esperado] of [
  [400, { detail: "Informe um número de WhatsApp válido com DDD." }, "Informe um número de WhatsApp válido com DDD."],
  [422, { detail: [{ loc: ["body", "email"], msg: "x" }] }, "Não deu para criar sua conta. Tente de novo."],
  [403, { detail: "Token CSRF inválido ou ausente." }, "Recarregue a página e tente de novo."],
  [429, { detail: "x" }, "Muitas tentativas. Aguarde alguns minutos e tente de novo."],
  [503, { detail: "Não deu para criar a conta." }, "Não deu para criar sua conta. Tente de novo."],
]) {
  test(`S1 com ${status}: "${esperado}"`, async () => {
    const { ctx, page } = await abrir(browser, { hash: FRAG, api: { [CONTA]: [status, corpo] } });
    await tela(page, "s1");
    await continuar(page);
    await page.waitForFunction(() => document.getElementById("s1-erro").textContent !== "");
    assert.equal(await texto(page, "s1-erro"), esperado);
    await ctx.close();
  });
}

test("duplo clique e Enter com o POST em voo: 1 POST só", async () => {
  const lento = () => new Promise((ok) => setTimeout(() => ok([200, { estado: "tem_conta" }]), 400));
  const { ctx, page, posts } = await abrir(browser, { hash: FRAG, api: { [CONTA]: lento } });
  await tela(page, "s1");
  await page.check("#termos");
  await page.click("#s1-continuar");
  await page.click("#s1-continuar");
  await page.press("#email", "Enter");
  await tela(page, "s2");
  assert.equal(posts("/auth/quiz/conta").length, 1);
  await ctx.close();
});

test("recarga depois de criada (/auth/me 200 da mesma conta): direto ao S3, sem o formulário", async () => {
  for (const hash of [FRAG, ""]) {
    const { ctx, page, posts } = await abrir(browser, { hash, api: { "GET /auth/me": ME("Ana@X.com") } });
    await tela(page, "s4");
    assert.equal(await page.locator("#s1").isVisible(), false);
    assert.equal(posts("/auth/quiz/conta").length, 0);
    await ctx.close();
  }
});

/**
 * S1a → tem_conta → clica `botao`; devolve a ordem de logout e navegação. `tardia`: a
 * carga é sem sessão, e o /auth/me do clique responde isto (outra aba entrou em c@x,
 * `null` pendurado, "aborta"). `relogio`: o /auth/me do clique só vence pelo ME_MS.
 */
async function contaErrada(botao, { comMe = true, tardia, relogio = false } = {}) {
  let viva = false;
  const api = { [CONTA]: [200, { estado: "tem_conta" }] };
  if (comMe) api["GET /auth/me"] = ME("a@x.com");
  if (tardia !== undefined) api["GET /auth/me"] = () => (viva ? tardia : [401, { detail: "Não autenticado." }]);
  const r = await abrir(browser, { hash: "#e=b%40x.com&w=11987654321", api, relogio });
  await tela(r.page, "s1");
  assert.equal(r.posts(CHECKOUT).length, 0, "cobrou a conta logada (a@x) com o e-mail b@x na tela");
  await continuar(r.page);
  await tela(r.page, "s2");
  viva = true;
  await r.page.click(botao);
  if (relogio) {
    await new Promise((ok) => setTimeout(ok, 300));  // tempo real: o relógio da página está parado
    assert.equal(r.posts("/auth/logout").length, 0, "não esperou o /auth/me");
    await r.page.click("#s2-esqueci");  // a trava está com o clique: o Esqueci não sai
    await r.page.clock.runFor(5000);
  }
  await r.page.waitForURL((u) => !u.pathname.endsWith("/assinar.html"));
  const iLogout = r.reqs.findIndex((x) => x.method === "POST" && x.path === "/auth/logout");
  const iNav = r.reqs.findIndex((x) => x.navegacao && !x.path.endsWith("/assinar.html"));
  return { ...r, iLogout, iNav };
}

test("D7: sessão de a@x e e-mail b@x → S1; 'Entrar com sua senha' sai da conta ANTES de ir ao /login", async () => {
  const { ctx, iLogout, iNav } = await contaErrada("#s2-senha");
  assert.ok(iLogout !== -1 && iLogout < iNav, `logout ${iLogout}, navegação ${iNav}`);
  await ctx.close();
});

test("positivo do D7: sem sessão, 'Entrar com sua senha' navega sem logout", async () => {
  const { ctx, iLogout, iNav } = await contaErrada("#s2-senha", { comMe: false });
  assert.equal(iLogout, -1);
  assert.ok(iNav !== -1);
  await ctx.close();
});

test("Google com a sessão de outra conta: logout, depois a intenção, depois /auth/google/start", async () => {
  const { ctx, page, iLogout, iNav, reqs } = await contaErrada("#s2-google");
  assert.ok(iLogout !== -1 && iLogout < iNav);
  assert.equal(reqs[iNav].url, `${ORIGIN}/auth/google/start?next=%2Fcontinuar-compra`);
  const intent = JSON.parse(await page.evaluate(() => sessionStorage.getItem("pb_purchase_intent_v1")));
  assert.deepEqual([intent.plan, intent.cycle, intent.method, intent.status], ["plus", "monthly", "card", "awaiting_auth"]);
  await ctx.close();
});

test("D7 pela sessão tardia: carga sem sessão, outra aba entra em c@x; senha e Google saem ANTES de navegar", async () => {
  let r = await contaErrada("#s2-senha", { comMe: false, tardia: ME("c@x.com") });
  assert.ok(r.iLogout !== -1 && r.iLogout < r.iNav, `senha: logout ${r.iLogout}, navegação ${r.iNav}`);
  assert.match(r.reqs[r.iNav].url, /\/login\?next=/);
  await r.ctx.close();
  r = await contaErrada("#s2-google", { comMe: false, tardia: ME("c@x.com") });
  assert.ok(r.iLogout !== -1 && r.iLogout < r.iNav, `Google: logout ${r.iLogout}, navegação ${r.iNav}`);
  assert.equal(r.reqs[r.iNav].url, `${ORIGIN}/auth/google/start?next=%2Fcontinuar-compra`);
  const intent = JSON.parse(await r.page.evaluate(() => sessionStorage.getItem("pb_purchase_intent_v1")));
  assert.deepEqual([intent.method, intent.status], ["card", "awaiting_auth"]);
  await r.ctx.close();
});

test("/auth/me do clique pendurado: depois do ME_MS desloga e navega (senha e Google), sem travar", async () => {
  let r = await contaErrada("#s2-senha", { comMe: false, tardia: null, relogio: true });
  assert.ok(r.iLogout !== -1 && r.iLogout < r.iNav, `senha: logout ${r.iLogout}, navegação ${r.iNav}`);
  assert.match(r.reqs[r.iNav].url, /\/login\?next=/);
  assert.equal(r.posts("/auth/forgot-password").length, 0);
  await r.ctx.close();
  r = await contaErrada("#s2-google", { comMe: false, tardia: null, relogio: true });
  assert.ok(r.iLogout !== -1 && r.iLogout < r.iNav, `Google: logout ${r.iLogout}, navegação ${r.iNav}`);
  assert.equal(r.reqs[r.iNav].url, `${ORIGIN}/auth/google/start?next=%2Fcontinuar-compra`);
  const intent = JSON.parse(await r.page.evaluate(() => sessionStorage.getItem("pb_purchase_intent_v1")));
  assert.deepEqual([intent.method, intent.status], ["card", "awaiting_auth"]);
  await r.ctx.close();
});

for (const [nome, resp] of [["falha de rede", "aborta"], ["503", [503, { detail: "x" }]]]) {
  test(`/auth/me do clique com ${nome}: sessão desconhecida, desloga ANTES de navegar`, async () => {
    const { ctx, iLogout, iNav } = await contaErrada("#s2-senha", { comMe: false, tardia: resp });
    assert.ok(iLogout !== -1 && iLogout < iNav, `logout ${iLogout}, navegação ${iNav}`);
    await ctx.close();
  });
}

test("next codificado: o /login devolve, depois do MFA, à /assinar com ciclo e UTM", async () => {
  const query = "?plano=plus&ciclo=annual&utm_source=ig";
  const { ctx, page } = await abrir(browser, { query, hash: FRAG, api: { [CONTA]: [200, { estado: "tem_conta" }] } });
  await tela(page, "s1");
  await continuar(page);
  await tela(page, "s2");
  await page.click("#s2-senha");
  await page.waitForURL(/\/login\?/);
  const next = "%2Fassinar.html%3Fplano%3Dplus%26ciclo%3Dannual%26utm_source%3Dig";
  assert.equal(new URL(page.url()).search, `?next=${next}`);
  await ctx.close();

  const login = await abrir(browser, { pagina: "login.html", query: `?next=${next}`, api: {
    "GET /auth/validate": [401, {}], "POST /auth/refresh": [401, {}],
    "POST /auth/login": [200, { mfa_required: true, mfa_challenge: "ch", email: "ana@x.com" }],
    "POST /auth/mfa/verify-login": [200, {}],
  } });
  await login.page.fill("#email", "ana@x.com");
  await login.page.fill("#senha", "segredo123");
  await login.page.click("#btn-login");
  await login.page.fill("#mfa-code", "123456");
  const volta = login.page.waitForRequest((r) => r.isNavigationRequest() && r.url().includes("/assinar"));
  await login.page.click("#btn-mfa");
  assert.equal((await volta).url(), `${ORIGIN}/assinar.html${query}`);
  await login.ctx.close();
});

test("Esqueci: POST /auth/forgot-password com o e-mail, e a mensagem do servidor", async () => {
  const msg = "Se este e-mail estiver cadastrado, você receberá as instruções em breve.";
  const { ctx, page, posts } = await abrir(browser, { hash: FRAG, api: {
    [CONTA]: [200, { estado: "tem_conta" }], "POST /auth/forgot-password": [200, { message: msg }] } });
  await tela(page, "s1");
  await continuar(page);
  await tela(page, "s2");
  await page.click("#s2-esqueci");
  await page.waitForFunction(() => document.getElementById("s2-erro").textContent !== "");
  assert.equal(await texto(page, "s2-erro"), msg);
  assert.deepEqual(posts("/auth/forgot-password").map((r) => r.body), [{ email: "ana@x.com" }]);
  await ctx.close();
});

for (const pix of [true, false]) {
  test(`Pix com pix_annual_available=${pix}`, async () => {
    const { ctx, page } = await abrir(browser, { api: { "GET /auth/me": ME("ana@x.com"),
      "GET /billing/plans-config": [200, { pix_annual_available: pix }] } });
    await tela(page, "s4");
    await page.locator("#stripe-checkout iframe").waitFor();
    assert.equal(await page.locator("#s4-pix").isVisible(), pix);
    if (pix) {
      await page.click("#s4-pix");
      await page.waitForURL(/\/continuar-compra$/);
      const intent = JSON.parse(await page.evaluate(() => sessionStorage.getItem("pb_purchase_intent_v1")));
      assert.deepEqual([intent.plan, intent.cycle, intent.method, intent.status], ["plus", "annual", "pix", "awaiting_auth"]);
    }
    await ctx.close();
  });
}

for (const [nome, resp, alvo, esperado] of [
  ["already_subscribed", [409, { detail: { error: "already_subscribed", message: "x" } }], "f1", "Seu plano já está ativo nesta conta."],
  ["lifetime", [409, { detail: { error: "lifetime", message: "x" } }], "f1", "Seu plano já está ativo nesta conta."],
  ["pix_active", [409, { detail: { error: "pix_active", message: "Você já tem o plano anual pago no Pix." } }], "f1", "Você já tem o plano anual pago no Pix."],
  ["401", [401, { detail: "x" }], "s1", "Sua sessão expirou. Entre de novo para continuar."],
  ["429", [429, { detail: "x" }], "s3", "Muitas tentativas. Aguarde alguns minutos e tente de novo."],
]) {
  test(`S3 com ${nome} → ${alvo}`, async () => {
    const { ctx, page } = await abrir(browser, { api: { "GET /auth/me": ME("ana@x.com"), [`POST ${CHECKOUT}`]: resp } });
    await tela(page, alvo === "s3" ? "s3-retry" : alvo);  // o S3 já aparece antes da resposta
    const id = { f1: "f1-msg", s1: "s1-erro", s3: "s3-erro" }[alvo];
    assert.equal(await texto(page, id), esperado);
    if (alvo === "f1") assert.equal(await page.locator("#f1 a").getAttribute("href"), "/home");
    await ctx.close();
  });
}

test("S3 com 503: S3e, e o 'Tentar de novo' refaz o POST embutido", async () => {
  const { ctx, page, posts } = await abrir(browser, { api: { "GET /auth/me": ME("ana@x.com"),
    [`POST ${CHECKOUT}`]: [[503, { detail: "Pagamentos ainda não configurados." }], EMBUTIDO] } });
  await tela(page, "s3-retry");
  assert.equal(await texto(page, "s3-titulo"), "Não deu para abrir o pagamento agora.");
  await page.click("#s3-retry");
  await tela(page, "s4");
  assert.deepEqual(posts(CHECKOUT).map((r) => r.body.embutido), [true, true]);
  await ctx.close();
});

for (const [td, esperado] of [[15, /^Você tem 15 dias grátis\./], [0, /^Esta assinatura não tem período grátis/]]) {
  test(`trial_days=${td}`, async () => {
    const { ctx, page } = await abrir(browser, { api: { "GET /auth/me": ME("ana@x.com"),
      [`POST ${CHECKOUT}`]: [200, { ...EMBUTIDO[1], trial_days: td }] } });
    await tela(page, "s4");
    assert.match(await texto(page, "s4-trial"), esperado);
    await ctx.close();
  });
}

test("Sair: POST /auth/logout, e o S1 volta com os campos do fragmento", async () => {
  const { ctx, page, posts } = await abrir(browser, { hash: FRAG, api: { "GET /auth/me": ME("ana@x.com") } });
  await page.locator("#stripe-checkout iframe").waitFor();
  await page.click("#sair");
  await tela(page, "s1");
  assert.equal(posts("/auth/logout").length, 1);
  assert.equal(await page.inputValue("#email"), "ana@x.com");
  assert.equal(await page.evaluate(() => window.__stripe.destroy), 1);
  await ctx.close();
});
