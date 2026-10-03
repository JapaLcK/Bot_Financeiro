/**
 * A /assinar (frontend/assinar.html) com a API e o Stripe.js falsos, para os testes
 * `assinar_*`. Tudo é atendido por uma rota só, do disco (o molde do `_painel.mjs`):
 * nenhuma requisição vai para a rede, e cada uma fica registrada em `reqs`, na ordem.
 *
 * Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não deve
 * rodá-lo como suíte.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
export const ORIGIN = "http://127.0.0.1:1";
export const CSRF = "csrf-assinar";
export const HOSPEDADO = "https://checkout.stripe.test/c/pay/cs_hosp";
export const EMBUTIDO = [200, { client_secret: "cs_emb_secret", publishable_key: "pk_test_x", trial_days: 15,
                                interval: "monthly", plan: "plus" }];
export const ME = (email) => [200, { user_id: 7, email }];
// A página própria (create-checkout com `pagina: true` e a flag ligada): o embutido + `pagina` + `extras`.
const CAPA = "https://files.stripe.com/capa.png";
export const EXTRAS = [
  { posicao: 1, nome: "Saia do vermelho", descricao: "Diagnóstico das dívidas e um roteiro para negociar.", imagem: CAPA, valor_centavos: 1190, no_carrinho: false },
  { posicao: 2, nome: "Primeira reserva", descricao: "Quanto guardar por mês até ter seis meses de folga.", imagem: null, valor_centavos: 1190, no_carrinho: false },
  { posicao: 3, nome: "Cartão sem susto", descricao: "Fatura, limite e juros do rotativo sem mistério.", imagem: CAPA, valor_centavos: 1190, no_carrinho: false },
];
export const PAGINA = (extras = EXTRAS) => [200, { ...EMBUTIDO[1], pagina: true, extras }];

// O Stripe.js falso segue a forma da prova de conceito da Etapa 0:
// Stripe(pk).createEmbeddedCheckoutPage({fetchClientSecret}) → Promise<{mount, destroy}>.
// A página própria (scratchpad e2e, Stripe de teste): Stripe(pk).initCheckoutElementsSdk({clientSecret}) →
// {on("change"), createPaymentElement().mount, loadActions() → {actions}}. O confirm e o applyPromotionCode
// falsos fazem um POST a /__stripe/* para entrarem em `reqs` NA ORDEM, junto com o /billing/checkout/bump.
// Modos da página própria: "acoes-erro", "acoes-pendura" e "acoes-rejeita" (loadActions); `sdk.semChange` suprime o `change`.
// O modo vem de window.__STRIPE (addInitScript); o registro fica em window.__stripe.
const STRIPE_FALSO = `(function () {
  var cfg = window.__STRIPE || {}, modo = cfg.modo || "ok";
  var reg = window.__stripe = { pk: null, cs: null, mount: 0, destroy: 0, run: 0 };
  if (modo === "sem-global") return;
  var sessao = cfg.sessao || { lineItems: [{ name: "PigBank Plus", total: { minorUnitsAmount: 0 } }],
    total: { total: { minorUnitsAmount: 0 } }, recurring: { interval: "month", dueNext: { total: { minorUnitsAmount: 1990 } } } };
  var ouvinte = null;
  // \`depoisBump\`/\`depoisCupom\`: a sessão NOVA que o Stripe manda num 2º \`change\` depois de um runServerUpdate
  // ou de um applyPromotionCode que deram certo (como o SDK real faz).
  var muda = function (nova) { if (nova && ouvinte) { sessao = nova; ouvinte(sessao); } };
  var depois = function (url, corpo, res) {
    return fetch(url, { method: "POST", body: JSON.stringify(corpo || {}) }).then(function () { return res || { type: "success" }; });
  };
  var acoes = {
    getSession: function () { return Object.assign({ id: "cs_test_abc" }, sessao); },
    runServerUpdate: function (fn) {
      reg.run++;
      return Promise.resolve().then(fn).then(function () { muda(cfg.depoisBump); return { type: "success" }; },
        function (e) { return { type: "error", error: { message: String(e) } }; });
    },
    confirm: function () {
      return cfg.confirma === "lanca" ? Promise.reject(new Error("rede")) : depois("/__stripe/confirm", {}, cfg.confirma);
    },
    applyPromotionCode: function (c) {
      return depois("/__stripe/cupom", { code: c }, cfg.cupom).then(function (r) { if (r.type === "success") muda(cfg.depoisCupom); return r; });
    },
  };
  window.Stripe = function (pk, op) {
    reg.pk = pk; reg.op = op;
    return { initCheckoutElementsSdk: function (o) {
      reg.init = (reg.init || 0) + 1;
      return { on: function (ev, fn) {
          if (ev === "change") ouvinte = fn;
          if (ev === "change" && !cfg.semChange) Promise.resolve(o.clientSecret).then(function (cs) { reg.cs = cs; fn(sessao); });
        },
        createPaymentElement: function (peop) {
          reg.pe = peop;
          return { mount: function (sel) {
            reg.mount++;
            if (modo !== "ok" && modo.indexOf("acoes-") !== 0) return;
            var f = document.createElement("iframe");
            f.style.cssText = "display:block;width:100%;height:" + (cfg.altura || 300) + "px;border:0";
            document.querySelector(sel).appendChild(f);
          }, destroy: function () { reg.destroy++; } };
        },
        loadActions: function () {
          if (modo === "acoes-pendura") return new Promise(function () {});
          if (modo === "acoes-rejeita") return Promise.reject(new Error("acoes"));
          return Promise.resolve(modo === "acoes-erro" ? { type: "error", error: { message: "x" } } : { type: "success", actions: acoes });
        } };
    }, createEmbeddedCheckoutPage: function (o) {
      if (modo === "lanca") throw new Error("lanca");
      return o.fetchClientSecret().then(function (cs) {
        reg.cs = cs;
        if (modo === "rejeita") throw new Error("rejeita");
        if (modo === "rejeita-tarde") return new Promise(function (_, nao) { setTimeout(function () { nao(new Error("tarde")); }, cfg.atraso); });
        return { mount: function (sel) {
          reg.mount++;
          if (modo === "mount-lanca") throw new Error("mount");
          if (modo !== "ok") return;
          var f = document.createElement("iframe");
          f.style.cssText = "display:block;width:100%;height:600px;border:0";
          document.querySelector(sel).appendChild(f);
        }, destroy: function () { reg.destroy++; } };
      });
    } };
  };
})();`;

const PADRAO = {
  "GET /auth/me": [401, { detail: "Não autenticado." }],
  "GET /billing/plans-config": [200, { pix_annual_available: false }],
  "POST /auth/logout": [200, { ok: true }],
  "POST /billing/create-checkout": (req) => (req.body.embutido ? EMBUTIDO : [200, { checkout_url: HOSPEDADO }]),
  "POST /billing/checkout/bump": [200, { ok: true }],
  "POST /__stripe/confirm": [200, {}],
  "POST /__stripe/cupom": [200, {}],
};

/**
 * Abre a assinar.html. `api`: "MÉTODO /caminho" → [status, corpo], ou uma lista
 * (uma resposta por chamada, a última se repete), ou fn(req) → [status, corpo],
 * `null` (fica pendente para sempre) ou "aborta" (falha de rede). `stripe`: o modo do falso, ou "aborta"/"pendura"
 * para o próprio script, ou "segura" (a rota do script volta em `rotaStripe`: o teste decide quando abortar). `html`: transforma o HTML antes de servir. `pagina`: outra
 * página do frontend/ no mesmo contexto falso (o login.html, no teste do `next`). `sdk`: o falso da página própria
 * (`sessao` do `change`, `confirma`/`cupom` = o que o confirm/applyPromotionCode devolvem, `altura` do iframe).
 */
export async function abrir(browser, {
  query = "?plano=plus&ciclo=monthly", hash = "", api = {}, stripe = "ok", atraso = 0,
  viewport, userAgent, html = (t) => t, relogio = false, pagina = "assinar.html", sdk = {},
} = {}) {
  const ctx = await browser.newContext({ viewport, userAgent });
  await ctx.addCookies([{ name: "csrf_token", value: CSRF, url: ORIGIN }]);
  await ctx.addInitScript((cfg) => {
    window.__STRIPE = cfg;
    window.fbq = (...a) => (window.__fbq = window.__fbq || []).push(a);
    window.pbTrack = (nome, params, depois) => { (window.__ga = window.__ga || []).push([nome, params]); if (depois) depois(); };
  }, { modo: stripe, atraso, ...sdk });
  const reqs = [];
  const respostas = { ...PADRAO, ...api };
  const contagem = {};
  let pegaRota;
  const rotaStripe = new Promise((ok) => { pegaRota = ok; });
  await ctx.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    let body = null;
    try { body = req.postDataJSON(); } catch { body = req.postData(); }
    const r = { method: req.method(), url: req.url(), path: url.pathname, body,
                csrf: req.headers()["x-csrf-token"], referer: req.headers().referer || "",
                navegacao: req.isNavigationRequest() };
    reqs.push(r);
    if (url.hostname === "js.stripe.com") {
      if (stripe === "aborta") return route.abort();
      if (stripe === "pendura") return;
      if (stripe === "segura") return pegaRota(route);
      return route.fulfill({ contentType: "application/javascript", body: STRIPE_FALSO });
    }
    if (url.origin !== ORIGIN) return route.fulfill({ status: 200, contentType: "text/html", body: "fora" });
    const chave = `${r.method} ${url.pathname}`;
    if (respostas[chave] !== undefined) {
      let resp = respostas[chave];
      const n = contagem[chave] = (contagem[chave] || 0) + 1;
      if (Array.isArray(resp) && Array.isArray(resp[0])) resp = resp[Math.min(n, resp.length) - 1];
      if (typeof resp === "function") resp = await resp(r);
      if (resp === null) return;  // pendente para sempre
      if (resp === "aborta") return route.abort();  // falha de rede
      return route.fulfill({ status: resp[0], contentType: "application/json", body: JSON.stringify(resp[1]) });
    }
    if (url.pathname === "/assinar.html") {
      return route.fulfill({ contentType: "text/html", body: html(readFileSync(join(FRONTEND, "assinar.html"), "utf8")) });
    }
    const arquivo = join(FRONTEND, decodeURIComponent(url.pathname));
    return route.fulfill({ path: arquivo }).catch(() => route.fulfill({ status: 404, body: "" }));
  });
  const page = await ctx.newPage();
  if (relogio) {
    // Parado, e não "andando em tempo real": numa máquina carregada o relógio de 10 s
    // venceria sozinho antes de o teste chegar ao S4. Só o runFor o adianta.
    await page.clock.install({ time: new Date("2026-10-01T12:00:00Z") });
    await page.clock.pauseAt(new Date("2026-10-01T12:00:01Z"));
  }
  // domcontentloaded: o Stripe.js "pendurado" seguraria o `load` para sempre.
  await page.goto(`${ORIGIN}/${pagina}${query}${hash}`, { waitUntil: "domcontentloaded" });
  return { ctx, page, reqs, rotaStripe, posts: (path) => reqs.filter((x) => x.method === "POST" && x.path === path) };
}

/** Espera a seção `id` ficar visível. */
export const tela = (page, id) => page.locator(`#${id}`).waitFor({ state: "visible" });

/** Preenche o S1 (os campos que o fragmento não trouxe) e clica em Continuar. */
export async function continuar(page, { nome, email, whatsapp } = {}) {
  if (nome !== undefined) await page.fill("#nome", nome);
  if (email !== undefined) await page.fill("#email", email);
  if (whatsapp !== undefined) await page.fill("#whatsapp", whatsapp);
  await page.check("#termos");
  await page.click("#s1-continuar");
}

export const FRAG = "#n=Ana&e=ana%40x.com&w=11987654321";

// ── Página própria ──────────────────────────────────────────────────────────
const LOGADO = { "GET /auth/me": ME("ana@x.com") };
/** API logada cujo create-checkout embutido responde a página própria com `extras`. */
export const comPagina = (extras = EXTRAS, hosp = [200, { checkout_url: HOSPEDADO }]) =>
  ({ ...LOGADO, "POST /billing/create-checkout": (req) => (req.body.embutido ? PAGINA(extras) : hosp) });

/** Abre na página própria e espera as ações (o botão de pagar destrava com o loadActions e o 1º `change`). */
export async function abrirPagina(browser, opts = {}) {
  const r = await abrir(browser, { ...opts, api: { ...comPagina(opts.extras), ...(opts.api || {}) } });
  await tela(r.page, "s4");
  await r.page.locator("#pagamento iframe").waitFor();
  await r.page.waitForFunction(() => !document.getElementById("pp-pagar").disabled);
  return r;
}
export const caixas = (page) => page.locator("#pp-bump input[type=checkbox]");
export const marcadas = (page) => page.$$eval("#pp-bump input", (l) => l.map((c) => c.checked));

/** Espera `cond()` (do lado do Node) ficar verdadeira; lança em `ms` em vez de pendurar a suíte. */
export async function ate(cond, ms = 5000) {
  const fim = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > fim) throw new Error("ate: a condição não chegou em " + ms + " ms");
    await new Promise((ok) => setTimeout(ok, 20));
  }
}
