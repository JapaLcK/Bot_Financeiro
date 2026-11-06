/**
 * /familia/entrar (frontend/familia-entrar.html, familia-entrar.js, familia-convite.js): a página de aceite do convite, sem React.
 *   · o token sai da URL no primeiro instante (hash vazio, guardado no sessionStorage) e nenhuma requisição o leva na URL;
 *     nada vai ao GA4/Pixel; o token só viaja no corpo do POST de aceite;
 *   · um caso por resposta do servidor (401/200/404/403/5xx no GET; 200/404/409/402/429/5xx/rede no aceite) e a regra de limpar o token;
 *   · prazo de 2 h, compra pendente vence o convite, o Google volta ao convite, login e cadastro sem convite seguem como antes;
 *   · medidas: desktop 1280x800 e celular 390x844 e 375x667.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { extname, join } from "node:path";
import { chromium } from "playwright";
import { FRONTEND, ORIGIN } from "./_painel.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(() => browser?.close());

const TOKEN = "Tok-0123456789_abcdefghijklmnopqrstuvwxyzABCDEF";
const KEY = "pb_familia_convite_v1";
const TIPOS = { ".js": "application/javascript", ".css": "text/css", ".html": "text/html; charset=utf-8", ".webp": "image/webp", ".png": "image/png", ".svg": "image/svg+xml", ".woff2": "font/woff2" };
const ERRO = (status, code) => (r) => r.fulfill({ status, json: { error: { code, message: "x" } } });
// O 401 do servidor real (resolve_dashboard_user_id) leva WWW-Authenticate: é o que aciona o refresh do auth-refresh.js.
const NAO_AUTENTICADO = (r) => r.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" }, json: { error: { code: "unauthorized", message: "x" } } });
const PAGINAS = { "/familia/entrar": "familia-entrar.html", "/login": "login.html", "/cadastro": "cadastro.html", "/completar-cadastro": "completar-cadastro.html" };

// A página como o servidor a entrega (a rota do backend é testada em test_familia_entrar.py), com a API de mentira. `s.get`/`s.aceitar`: (route) => resposta.
async function abrir({ hash = `#t=${TOKEN}`, caminho = "/familia/entrar", width = 1280, height = 800, guardado, compra = false, js = (t) => t, s = {}, api = true } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce" });
  const reqs = [], externos = [], nav = [];
  ctx.on("request", (q) => { const u = new URL(q.url()); (u.origin === ORIGIN ? reqs : externos).push({ url: q.url(), metodo: q.method(), corpo: q.postData() ?? "", csrf: q.headers()["x-csrf-token"], tipo: q.headers()["content-type"] }); });
  await ctx.addCookies([{ name: "csrf_token", value: "tok-123", url: ORIGIN }]);
  if (guardado !== undefined) await ctx.addInitScript(([k, v]) => { if (!sessionStorage.getItem(k) && v) sessionStorage.setItem(k, JSON.stringify(v)); }, [KEY, guardado]);
  if (compra) await ctx.addInitScript(() => sessionStorage.setItem("pb_purchase_intent_v1", JSON.stringify({ version: 1, plan: "plus", cycle: "monthly", method: "card", status: "awaiting_auth", createdAt: Date.now() })));
  await ctx.route("**/*", async (r) => {
    const u = new URL(r.request().url()), m = r.request().method();
    if (u.origin !== ORIGIN) return r.abort();
    const p = u.pathname;
    if (p === "/api/v2/familia" && api) return s.get ? s.get(r) : ERRO(404, "not_found")(r);
    if (p === "/api/v2/familia/convites/aceitar") return s.aceitar ? s.aceitar(r) : r.fulfill({ json: { papel: "membro" } });
    if (p.startsWith("/auth/google/pending/")) return r.fulfill({ json: { email: "g@exemplo.com", name_hint: "Gabi" } });
    if (p === "/auth/google/complete-signup") return r.fulfill({ json: { user_id: 7, dashboard_url: "/precos?escolha=1" } });
    if (p === "/auth/dashboard-profile" && s.perfil) return s.perfil(r);
    if (p === "/auth/dashboard-profile") return r.fulfill({ json: { user_id: 5, display_name: "Ana", email: "ana.teste@exemplo.com", whatsapp_linked: false } });
    if (p === "/auth/validate") return s.validate ? s.validate(r) : r.fulfill({ status: 401, json: { detail: "x" } });
    if (p === "/auth/verify-email") return r.fulfill({ json: { user_id: 5, dashboard_url: "/precos?escolha=1" } });
    if (p === "/auth/refresh") return r.fulfill({ status: 401, json: { detail: "x" } });
    if (p === "/auth/logout" && m === "POST") return r.fulfill({ status: 200, json: { ok: true } });
    if (["/painel", "/home", "/precos", "/continuar-compra", "/login", "/cadastro"].includes(p) && !PAGINAS[p]) { nav.push(p + u.search); return r.fulfill({ contentType: "text/html", body: "<title>destino</title>" }); }
    let arq = PAGINAS[p] ?? p.slice(1);
    if (p === "/static/auth-refresh.js") arq = "static/auth-refresh.js";
    const f = join(FRONTEND, arq);
    if (!existsSync(f)) return r.fulfill({ status: 404, body: "" });
    let corpo = readFileSync(f);
    if (p === "/familia-entrar.js") corpo = js(corpo.toString());
    return r.fulfill({ body: corpo, contentType: TIPOS[extname(f)] ?? "text/plain" });
  });
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", (e) => erros.push(e.message));
  await page.goto(`${ORIGIN}${caminho}${hash}`);
  return {
    ctx, page, reqs, externos, nav, erros, s,
    h1: () => page.locator("#titulo").textContent(),
    texto: async () => (await page.locator(".auth-card").innerText()).replace(/\s+/g, " ").trim(),
    guardado: () => page.evaluate((k) => sessionStorage.getItem(k), KEY).then((v) => (v ? JSON.parse(v) : null)),
    esperaH1: (t) => page.locator("#titulo", { hasText: t }).waitFor(),
    fecha: () => ctx.close(),
  };
}
// Ruído do navegador ao sair de uma página com @view-transition (/login, /cadastro): "opt-in disabled" ou, sob carga, "Transition was skipped".
const RUIDO_VT = /ViewTransition|Transition was skipped/;
const com = async (opts, fn) => { const t = await abrir(opts); try { await fn(t); assert.deepEqual(t.erros.filter((e) => !RUIDO_VT.test(e)), [], "pageerror"); } finally { await t.fecha(); } };
const aceita = (t) => t.page.getByRole("button", { name: "Entrar na família" });

test("o token sai da URL: hash vazio, guardado só no sessionStorage, nenhuma requisição o leva na URL, nada vai ao GA4/Pixel", async () => {
  await com({}, async (t) => {
    await aceita(t).waitFor();
    assert.equal(await t.page.evaluate(() => location.hash), "");
    assert.equal(t.page.url(), `${ORIGIN}/familia/entrar`);
    assert.equal((await t.guardado()).t, TOKEN);
    assert.equal(await t.page.evaluate((k) => localStorage.getItem(k), KEY), null); // nunca em storage persistente
    assert.equal(await t.page.evaluate(() => history.length), 2); // about:blank + a página: o replaceState não criou entrada
    await aceita(t).click();
    await t.esperaH1("Você entrou na família.");
    // o token aparece em UM lugar: o corpo do POST de aceite
    const com = [...t.reqs, ...t.externos].filter((q) => q.url.includes(TOKEN) || q.corpo.includes(TOKEN));
    assert.deepEqual(com.map((q) => [q.metodo, new URL(q.url).pathname]), [["POST", "/api/v2/familia/convites/aceitar"]]);
    assert.deepEqual(JSON.parse(com[0].corpo), { token: TOKEN });
    assert.deepEqual([com[0].csrf, com[0].tipo], ["tok-123", "application/json"]);
    assert.deepEqual(t.externos.map((q) => q.url), []); // nada saiu da origem (googletagmanager, facebook, clarity…)
  });
});

test("controle negativo: sem o replaceState o hash fica na URL (o teste acima mede de verdade)", async () => {
  await com({ js: (t) => t.replace("history.replaceState(", "void ("), s: {} }, async (t) => {
    await aceita(t).waitFor();
    assert.equal(await t.page.evaluate(() => location.hash), `#t=${TOKEN}`);
  });
});

test("o e-mail da sessão aparece; 'Não é você? Sair' faz o logout com CSRF e volta ao convite", async () => {
  await com({}, async (t) => {
    await aceita(t).waitFor();
    assert.match(await t.texto(), /Entrando como ana\.teste@exemplo\.com\./);
    assert.equal(await t.page.locator("#nav-entrar").isVisible(), false); // logado: só o botão do cartão é rosa
    const vai = t.page.waitForRequest((q) => q.isNavigationRequest() && new URL(q.url()).pathname === "/familia/entrar");
    await t.page.getByRole("link", { name: "Não é você? Sair" }).click();
    await vai;
    const out = t.reqs.filter((q) => q.url.endsWith("/auth/logout"));
    assert.deepEqual(out.map((q) => [q.metodo, q.csrf]), [["POST", "tok-123"]]);
    assert.ok(!t.reqs.some((q) => new URL(q.url).pathname === "/painel")); // não passa pelo /painel (o next=/painel venceria o convite)
    await aceita(t).waitFor(); // a página do convite recarregou (o mock segue com sessão) e terminou de montar
    assert.equal((await t.guardado())?.t, TOKEN); // a limpeza do logout não leva o convite pendente
  });
});

test("conta trocada em outra aba: antes do POST relê o perfil; e-mail diferente mostra o novo e exige novo clique (sem POST)", async () => {
  let n = 0;
  const perfil = (r) => r.fulfill({ json: { user_id: 5, email: ++n === 1 ? "ana.teste@exemplo.com" : "outra@exemplo.com" } });
  await com({ s: { perfil } }, async (t) => {
    await aceita(t).waitFor();
    await aceita(t).click();
    await t.page.getByText("Entrando como outra@exemplo.com.").waitFor();
    assert.equal(t.reqs.filter((q) => q.url.endsWith("/convites/aceitar")).length, 0);
    await aceita(t).click(); // confirmou a conta nova: agora envia
    await t.esperaH1("Você entrou na família.");
    assert.equal(t.reqs.filter((q) => q.url.endsWith("/convites/aceitar")).length, 1);
  });
});

test("conta igual no reler: o clique envia o aceite direto", async () => {
  await com({}, async (t) => {
    await aceita(t).waitFor();
    await aceita(t).click();
    await t.esperaH1("Você entrou na família.");
    assert.equal(t.reqs.filter((q) => q.url.endsWith("/convites/aceitar")).length, 1);
  });
});

test("sem sessão (401): Entrar e Criar conta, sem token nos links; o convite segue guardado e o destino volta a ele", async () => {
  await com({ s: { get: NAO_AUTENTICADO } }, async (t) => {
    await t.esperaH1("Entre para aceitar o convite");
    assert.equal(await t.page.locator("#nav-entrar").isVisible(), true); // sem sessão: o Entrar da barra segue
    const links = await t.page.locator("#acoes a").evaluateAll((es) => es.map((e) => [e.textContent, e.getAttribute("href")]));
    assert.deepEqual(links, [["Entrar", "/login"], ["Criar conta", "/cadastro"]]);
    assert.equal((await t.guardado()).t, TOKEN);
    assert.equal(await t.page.evaluate(() => window.PBFamiliaConvite.destino("/home")), "/familia/entrar");
    assert.equal(t.page.url().includes(TOKEN), false);
    assert.ok(t.reqs.some((q) => q.url.endsWith("/auth/refresh")), "o 401 com WWW-Authenticate passou pelo refresh (e a limpeza de conta)");
    await t.page.getByRole("link", { name: "Entrar" }).last().click();
    await t.page.waitForURL("**/login");
    assert.equal((await t.guardado()).t, TOKEN); // o convite é intenção do visitante, não dado da conta: sobrevive até o login
  });
});

// [nome, resposta do GET, texto do h1, o token continua guardado?]
const DO_GET = [
  ["já está numa família (200)", (r) => r.fulfill({ json: { papel: "membro" } }), "Você já está numa família.", false],
  ["v2 desligada (404 dashboard_v2_disabled)", ERRO(404, "dashboard_v2_disabled"), "Não foi possível abrir o convite", true],
  ["conta sem senha (403 password_required)", ERRO(403, "password_required"), "Defina uma senha para continuar.", true],
  ["erro do servidor (500)", ERRO(500, "internal"), "Não foi possível abrir o convite", true],
  ["rede fora", (r) => r.abort(), "Não foi possível abrir o convite", true],
];
for (const [nome, get, h1, fica] of DO_GET) {
  test(`GET /familia: ${nome}`, async () => {
    await com({ s: { get } }, async (t) => {
      await t.esperaH1(h1);
      assert.equal(!!(await t.guardado()), fica);
      assert.equal(t.reqs.filter((q) => q.metodo === "POST" && q.url.includes("aceitar")).length, 0); // nunca aceita sozinho
    });
  });
}

// [nome, resposta do aceite, h1, o token continua guardado?]
const DO_ACEITE = [
  ["200: entrou", undefined, "Você entrou na família.", false],
  ["404 invite_invalid", ERRO(404, "invite_invalid"), "Convite inválido", false],
  ["409 already_in_family", ERRO(409, "already_in_family"), "Você já está numa família.", false],
  ["409 full", ERRO(409, "full"), "Família cheia", false],
  ["402 family_coverage_required: NÃO limpa", ERRO(402, "family_coverage_required"), "Convite indisponível agora.", true],
  ["429: NÃO limpa", ERRO(429, "rate_limited"), "Muitas tentativas", true],
  ["500: NÃO limpa", ERRO(500, "internal"), "Não foi possível entrar", true],
  ["rede fora: NÃO limpa", (r) => r.abort(), "Não foi possível entrar", true],
  ["404 com outro código: NÃO limpa", ERRO(404, "not_found"), "Não foi possível entrar", true],
  ["403 password_required: NÃO limpa", ERRO(403, "password_required"), "Defina uma senha para continuar.", true],
];
for (const [nome, aceitar, h1, fica] of DO_ACEITE) {
  test(`aceite ${nome}`, async () => {
    await com({ s: { aceitar } }, async (t) => {
      await aceita(t).click();
      await t.esperaH1(h1);
      assert.equal(!!(await t.guardado()), fica);
      if (h1 === "Você entrou na família.") assert.equal(await t.page.getByRole("link", { name: "Abrir painel" }).getAttribute("href"), "/painel");
    });
  });
}

// Quem não tem plano (o convidado típico: cadastro novo) recebe 402 no GET /familia; o plano vem com o aceite.
for (const code of ["plan_selection_required", "subscription_required"]) {
  test(`GET /familia 402 ${code}: oferece o aceite, e o clique faz o POST`, async () => {
    await com({ s: { get: ERRO(402, code) } }, async (t) => {
      await aceita(t).waitFor();
      assert.equal(await t.h1(), "Entrar na família");
      assert.equal(t.reqs.filter((q) => q.metodo === "POST" && q.url.includes("aceitar")).length, 0); // o clique é do usuário
      await aceita(t).click();
      await t.esperaH1("Você entrou na família.");
      assert.equal(t.reqs.filter((q) => q.metodo === "POST" && q.url.endsWith("/convites/aceitar")).length, 1);
    });
  });
}

test("GET /familia 402 + aceite 409 already_in_family: 'Você já está numa família.' e limpa o token", async () => {
  await com({ s: { get: ERRO(402, "plan_selection_required"), aceitar: ERRO(409, "already_in_family") } }, async (t) => {
    await aceita(t).click();
    await t.esperaH1("Você já está numa família.");
    assert.equal(await t.guardado(), null);
  });
});

test("aceite: clique duplo = 1 POST; o clique é do usuário (sem POST antes dele)", async () => {
  await com({ s: { aceitar: async (r) => { await new Promise((ok) => setTimeout(ok, 300)); return r.fulfill({ json: { papel: "membro" } }); } } }, async (t) => {
    await aceita(t).waitFor();
    assert.equal(t.reqs.filter((q) => q.metodo === "POST").length, 0);
    await aceita(t).dblclick();
    await t.esperaH1("Você entrou na família.");
    assert.equal(t.reqs.filter((q) => q.url.endsWith("/convites/aceitar")).length, 1);
  });
});

test("sem e-mail da sessão (dashboard-profile falha) não se oferece o aceite: mensagem e 'Tentar de novo'", async () => {
  for (const perfil of [ERRO(500, "internal"), (r) => r.abort(), (r) => r.fulfill({ json: { user_id: 5 } })]) {
    await com({ s: { perfil } }, async (t) => {
      await t.esperaH1("Não foi possível abrir o convite");
      assert.equal(await aceita(t).count(), 0);
      await t.page.getByRole("button", { name: "Tentar de novo" }).waitFor();
      assert.equal((await t.guardado()).t, TOKEN);
    });
  }
});

test("fragmento inválido com convite antigo guardado: apaga o antigo e mostra 'Link de convite inválido.'", async () => {
  await com({ hash: "#t=abc&x=1", guardado: { t: TOKEN, criadoEm: Date.now() } }, async (t) => {
    await t.esperaH1("Link de convite inválido.");
    assert.equal(await t.guardado(), null);
    assert.equal(t.reqs.filter((q) => q.url.includes("/api/")).length, 0);
  });
  await com({ hash: "#t=NOVO-123", guardado: { t: TOKEN, criadoEm: Date.now() } }, async (t) => { // fragmento válido substitui o antigo
    await aceita(t).waitFor();
    assert.equal((await t.guardado()).t, "NOVO-123");
  });
});

test("sem token: 'Link de convite inválido.' e nenhuma chamada à API", async () => {
  await com({ hash: "" }, async (t) => {
    await t.esperaH1("Link de convite inválido.");
    assert.equal(t.reqs.filter((q) => q.url.includes("/api/")).length, 0);
  });
  await com({ hash: "#t=tem espaço/e$" }, async (t) => { // fora do formato: nunca guardado
    await t.esperaH1("Link de convite inválido.");
    assert.equal(await t.guardado(), null);
    assert.equal(await t.page.evaluate(() => location.hash), "");
  });
});

test("recarregar a página sem o fragmento continua no convite (vem do sessionStorage)", async () => {
  await com({}, async (t) => {
    await aceita(t).waitFor();
    await t.page.reload();
    await aceita(t).waitFor();
    assert.equal(t.page.url(), `${ORIGIN}/familia/entrar`);
  });
});

test("prazo de 2 h: convite guardado há 2 h e 1 min é ignorado e apagado; há 1 h 59 min ainda vale", async () => {
  await com({ hash: "", guardado: { t: TOKEN, criadoEm: Date.now() - (2 * 60 * 60 * 1000 + 60000) } }, async (t) => {
    await t.esperaH1("Link de convite inválido.");
    assert.equal(await t.guardado(), null);
    assert.equal(await t.page.evaluate(() => window.PBFamiliaConvite.destino("/home")), "/home");
  });
  await com({ hash: "", guardado: { t: TOKEN, criadoEm: Date.now() - (2 * 60 * 60 * 1000 - 60000) } }, async (t) => {
    await aceita(t).waitFor();
  });
  await com({ hash: "", guardado: { t: "tem espaço", criadoEm: Date.now() } }, async (t) => { // token fora do formato guardado por outra mão
    await t.esperaH1("Link de convite inválido.");
  });
});

// A página de teste do destino: os dois guardadores e um link do Google, como login e cadastro os têm.
const GANCHO = `<!doctype html><div class="auth-card"><p class="auth-sub"></p><a href="/auth/google/start">Google</a></div><script src="/purchase-intent.js"></script><script src="/familia-convite.js"></script>`;
const comGancho = async (guardado, fn, antes) => {
  const ctx = await browser.newContext();
  try {
    await ctx.route("**/*", (r) => {
      const p = new URL(r.request().url()).pathname;
      if (p === "/gancho") return r.fulfill({ contentType: "text/html", body: GANCHO });
      return r.fulfill({ contentType: "application/javascript", body: readFileSync(join(FRONTEND, p.slice(1))) });
    });
    if (guardado) await ctx.addInitScript(([k, v]) => sessionStorage.setItem(k, JSON.stringify(v)), [KEY, guardado]);
    const page = await ctx.newPage();
    await page.goto(`${ORIGIN}/gancho`);
    await antes?.(page);
    await fn(page);
  } finally { await ctx.close(); }
};
const VALIDO = () => ({ t: TOKEN, criadoEm: Date.now() });
const href = (page) => page.locator("a").getAttribute("href");
const COMPRA = (page) => page.evaluate(() => { PBPurchaseIntent.begin("plus", "monthly", "card"); PBPurchaseIntent.markAwaitingAuth(); });

test("destino(): com convite vai ao convite; sem convite devolve o fallback; compra pendente vence o convite", async () => {
  await comGancho(VALIDO(), async (page) => {
    assert.equal(await page.evaluate(() => PBFamiliaConvite.destino("/home")), "/familia/entrar");
    await COMPRA(page);
    assert.equal(await page.evaluate(() => PBFamiliaConvite.destino("/continuar-compra")), "/continuar-compra"); // o convite espera, guardado
    assert.equal((await page.evaluate((k) => sessionStorage.getItem(k), KEY)) !== null, true);
  });
  await comGancho(undefined, async (page) => {
    assert.equal(await page.evaluate(() => PBFamiliaConvite.destino("/painel")), "/painel");
  });
});

test("o link do Google volta ao convite; sem convite ou com compra pendente fica como estava", async () => {
  await comGancho(VALIDO(), async (page) => { assert.equal(await href(page), "/auth/google/start?next=%2Ffamilia%2Fentrar"); });
  await comGancho(undefined, async (page) => { assert.equal(await href(page), "/auth/google/start"); });
  // compra pendente ANTES de os scripts rodarem (como numa visita real a /cadastro?compra=1): o purchase-intent reescreve, o convite não
  await comGancho(VALIDO(), async (page) => {
    assert.equal(await href(page), "/auth/google/start?next=%2Fcontinuar-compra");
  }, async (page) => { await COMPRA(page); await page.reload(); });
});

test("/completar-cadastro (cadastro novo pelo Google): com convite vai ao convite; sem convite, ao destino do servidor; compra pendente vence", async () => {
  const envia = async (t) => {
    await t.page.fill("#phone", "11999998888");
    await t.page.check("#accept-terms");
    await t.page.click("#submit-btn");
  };
  const c = "/completar-cadastro?token=sg";
  await com({ hash: "", caminho: c }, async (t) => { await envia(t); await vaiPara(t, "/precos?escolha=1"); });
  await com({ hash: "", caminho: c, guardado: VALIDO() }, async (t) => { await envia(t); await vaiPara(t, "/familia/entrar"); });
  await com({ hash: "", caminho: c, guardado: VALIDO(), compra: true }, async (t) => { await envia(t); await vaiPara(t, "/continuar-compra"); });
});

const sessaoViva = (r) => r.fulfill({ json: { dashboard_url: "/painel" } });
// "ViewTransition opt-in disabled": ruído do navegador ao sair de uma página com @view-transition (o /login e o /cadastro, com ou sem convite).
const vaiPara = async (t, destino) => { await t.page.waitForURL(`${ORIGIN}${destino}`); t.erros = t.erros.filter((e) => !RUIDO_VT.test(e)); };

test("/login: sessão viva sem convite segue o destino de sempre; com convite vai ao convite; ?next= interno manda mais", async () => {
  await com({ hash: "", caminho: "/login", s: { validate: sessaoViva } }, (t) => vaiPara(t, "/painel"));
  await com({ hash: "", caminho: "/login", s: { validate: sessaoViva }, guardado: VALIDO() }, (t) => vaiPara(t, "/familia/entrar"));
  await com({ hash: "", caminho: "/login?next=/home", s: { validate: sessaoViva }, guardado: VALIDO() }, (t) => vaiPara(t, "/home"));
  await com({ hash: "", caminho: "/login", s: { validate: sessaoViva }, guardado: VALIDO(), compra: true }, (t) => vaiPara(t, "/continuar-compra"));
});

test("/cadastro: o destino depois do código vem do servidor, e do convite quando há um; compra pendente vence", async () => {
  const verifica = async (t) => {
    await t.page.evaluate(() => { _email = "a@b.co"; document.getElementById("verify-code").value = "123456"; return doVerify({ preventDefault() {} }); });
  };
  await com({ hash: "", caminho: "/cadastro" }, async (t) => { await verifica(t); await vaiPara(t, "/precos?escolha=1"); });
  await com({ hash: "", caminho: "/cadastro", guardado: VALIDO() }, async (t) => { await verifica(t); await vaiPara(t, "/familia/entrar"); });
  await com({ hash: "", caminho: "/cadastro", guardado: VALIDO(), compra: true }, async (t) => { await verifica(t); await vaiPara(t, "/continuar-compra"); });
});

for (const [width, height] of [[1280, 800], [390, 844], [375, 667]]) {
  test(`${width}x${height}: sem rolagem lateral, botões >= 44px, o cartão cabe na janela`, async () => {
    await com({ width, height }, async (t) => {
      await aceita(t).waitFor();
      const m = await t.page.evaluate(() => {
        const r = (e) => { const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, h: b.height }; };
        return { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, card: r(document.querySelector(".auth-card")), botoes: [...document.querySelectorAll("#acoes .btn")].map((b) => b.getBoundingClientRect().height), h1: parseFloat(getComputedStyle(document.querySelector("#titulo")).fontSize) };
      });
      console.log(`  ${width}x${height}: scrollWidth ${m.sw} <= clientWidth ${m.cw}; cartão ${Math.round(m.card.l)}..${Math.round(m.card.r)} x ${Math.round(m.card.t)}..${Math.round(m.card.b)}; botões ${m.botoes.map(Math.round)}px; h1 ${m.h1}px`);
      assert.ok(m.sw <= m.cw);
      assert.ok(m.card.l >= 0 && m.card.r <= width, "cartão fora da largura");
      assert.ok(Math.min(...m.botoes) >= 44, `botão de ${Math.min(...m.botoes)}px`);
      assert.ok(m.card.b <= height, `o botão está abaixo da dobra: cartão até ${m.card.b} em ${height}`);
    });
  });
}
