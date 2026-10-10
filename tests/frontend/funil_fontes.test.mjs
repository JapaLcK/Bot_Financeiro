/**
 * `/admin/funil`, cartões das FONTES externas (Stripe e, depois, GA4/Clarity/Meta).
 *
 * Tudo o que vem da fonte é dado de fora: `estado`, `mensagem`, `falta`, `janela.rotulo`,
 * `buscado_em`, os códigos de recusa e até os números podem chegar hostis. O teste mede o
 * DOM renderizado (nenhum <img> injetado, nenhum handler disparado, `data-estado` só com um
 * dos quatro valores), e que cada estado aparece, que o seletor 7d/30d não refaz o fetch e
 * que uma fonte com 500 vira cartão de erro sem apagar o resto do painel.
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
const FUNIL = {
  gerado_em: "2026-10-08T12:00:00+00:00", viewed_pricing_desde: "2026-09-01T00:00:00+00:00",
  janelas: Object.fromEntries([7, 30].map((d) => [`${d}d`, {
    dias: d, inicio: "2026-09-01T00:00:00+00:00", viram_precos_medido: true, emails_verificacao: 1,
    etapas: ["cadastros", "viram_precos", "abriram_checkout", "concluiram"]
      .map((id) => ({ id, n: 1, taxa_etapa: 1, taxa_acum: 1 })),
    abandono: 0, expiraram_sem_concluir: 0,
    estado_atual: { free: 1, trial: 0, paying: 0, past_due: 0, canceled: 0, granted: 0 },
    canais: [], origens: [],
    checkout: { pessoas: 1, sessoes_abertas: 1, sessoes_concluidas: 1, sessoes_expiradas: 0, conversao: 1 },
    ativacao: { concluiram: 1, onboarding: 1, whatsapp: 1, lancamento: 1 },
    trial: { iniciaram: 0, em_trial: 0, pagando: 0, cancelaram: 0, outros: 0 },
    pix: { gerados: 0, pagos: 0, expirados: 0, cancelados: 0, abertos: 0, taxa_pago: null },
    ebook: { entregas: 0, enviados: 0, nao_comprou: 0, estornados: 0, pendentes: 0 },
    teste: { clicaram: 0, abriram: 0, organicos: 0, responderam: 0, no_limite: 0, clicaram_checkout: 0 },
  }])),
  atraso: { total: 0, alem_carencia: 0, carencia_dias: 3 },
  links: [{ painel: "Afiliados", url: "/admin", olhar: "x" }],
};

const cobr = (a, r, rec, motivos) => ({ aprovadas: a, recusadas: r, receita_liquida: rec, motivos_recusa: motivos });
const DADOS = {
  assinaturas: { ativas: 3, em_trial: 2, em_atraso: 1, canceladas: 9, outras: 4 },
  mrr: 59.7, ticket_medio: 19.9, mrr_trial_potencial: 39.8,
  cobrancas: { "7d": cobr(5, 1, 149.5, [{ codigo: "card_declined", n: 1 }]),
               "30d": cobr(20, 7, 1234.56, [{ codigo: "insufficient_funds", n: 4 }, { codigo: "outros", n: 3 }]),
               truncado: false },
};
const JANELA = { rotulo: "Assinaturas agora; cobranças dos últimos 7 e 30 dias", fuso: "UTC" };
const env = (extra) => ({ fonte: "stripe", estado: "ok", mensagem: null, falta: null,
                          buscado_em: "2026-10-08T15:30:00+00:00", janela: JANELA, dados: DADOS, ...extra });

// O cartão do GA4 tem teste próprio (funil_ga4.test.mjs): aqui ele só precisa responder.
const GA4_NC = { fonte: "ga4", estado: "nao_configurado", mensagem: null, falta: ["GA4_SERVICE_ACCOUNT_JSON"],
                 buscado_em: null, janela: { rotulo: "x", fuso: "y" }, dados: null };
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function abre(responde, viewport = { width: 1280, height: 900 }) {
  const page = await browser.newPage({ viewport });
  const pedidos = { fonte: 0 };
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") erros.push(m.text()); });
  await page.route("**/admin/api/funil", (r) => r.fulfill(json(FUNIL)));
  await page.route("**/admin/api/funil/fonte/stripe", (r) => { pedidos.fonte++; return responde(r); });
  await page.route("**/admin/api/funil/fonte/ga4", (r) => r.fulfill(json(GA4_NC)));
  await page.goto(`${ORIGIN}/funil.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#f-stripe:not([data-estado=carregando])");
  return { page, pedidos, erros };
}

const texto = (page) => page.evaluate(() => document.getElementById("f-stripe").textContent);
const limpo = async (page) => {
  await page.waitForTimeout(150); // dá tempo de um onerror disparar, se existisse
  assert.equal(await page.evaluate(() => window.__xss), undefined);
  assert.deepEqual(await page.evaluate(() => [
    document.querySelectorAll("#main img").length,
    document.querySelectorAll("#main [onerror], #main [onmouseover], #main [onclick]").length,
    document.querySelectorAll('a[href^="javascript"]').length,
  ]), [0, 0, 0]);
};

test("ok: números, rodapé com fonte, janela e hora; 7d/30d não refaz o fetch", async () => {
  const { page, pedidos, erros } = await abre((r) => r.fulfill(json(env())));
  assert.equal(await page.getAttribute("#f-stripe", "data-estado"), "ok");
  let t = await texto(page);
  for (const esperado of ["8a. Stripe", "1.234,56", "insufficient_funds", "Pix anual não entra",
                          "Fonte: Stripe", "Assinaturas agora", "buscado às 12:30"]) assert.ok(t.includes(esperado), esperado);
  await page.click("#b7");
  t = await texto(page);
  assert.ok(t.includes("149,50") && t.includes("card_declined") && !t.includes("insufficient_funds"));
  assert.equal(pedidos.fonte, 1, "trocar a janela não pode pedir a fonte de novo");
  assert.deepEqual(erros, []);
  await page.close();
});

test("nao_configurado mostra o que definir; erro mostra a mensagem; stale mostra banner e números", async () => {
  let c = await abre((r) => r.fulfill(json(env({ estado: "nao_configurado", falta: ["STRIPE_SECRET_KEY"], dados: null, buscado_em: null }))));
  assert.equal(await c.page.getAttribute("#f-stripe", "data-estado"), "nao_configurado");
  assert.ok((await texto(c.page)).includes("Defina STRIPE_SECRET_KEY no Railway"));
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "erro", mensagem: "A fonte recusou as credenciais configuradas.", dados: null }))));
  assert.equal(await c.page.getAttribute("#f-stripe", "data-estado"), "erro");
  assert.ok((await texto(c.page)).includes("A fonte recusou as credenciais"));
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "stale", mensagem: "A fonte demorou demais para responder." }))));
  assert.equal(await c.page.getAttribute("#f-stripe", "data-estado"), "stale");
  const t = await texto(c.page);
  assert.ok(t.includes("Dado de 12:30, a fonte falhou: A fonte demorou demais") && t.includes("1.234,56"));
  await c.page.close();
});

test("texto hostil da fonte vira texto, em todos os campos e em todos os estados", async () => {
  const dadosHostis = { ...DADOS, mrr: XSS, assinaturas: { ...DADOS.assinaturas, ativas: XSS },
    cobrancas: { ...DADOS.cobrancas, "30d": cobr(XSS, XSS, XSS, [{ codigo: XSS, n: XSS }]) } };
  const hostil = { mensagem: XSS, janela: { rotulo: XSS, fuso: XSS }, buscado_em: XSS };
  const casos = [
    [{ ...hostil, estado: XSS, falta: [XSS], dados: dadosHostis }, "erro"],          // estado fora da whitelist
    [{ ...hostil, estado: "constructor", dados: dadosHostis }, "erro"],               // chave herdada do Object
    [{ ...hostil, estado: "nao_configurado", falta: [XSS, XSS], dados: null }, "nao_configurado"],
    [{ ...hostil, estado: "erro", dados: null }, "erro"],
    [{ ...hostil, estado: "stale", dados: dadosHostis }, "stale"],
    [{ ...hostil, estado: "ok", dados: dadosHostis }, "ok"],
  ];
  for (const [corpo, esperado] of casos) {
    const { page } = await abre((r) => r.fulfill(json(env(corpo))));
    assert.equal(await page.getAttribute("#f-stripe", "data-estado"), esperado, JSON.stringify(corpo.estado));
    await limpo(page);
    assert.ok((await texto(page)).includes("<img src=x"), "o texto hostil deveria aparecer como texto");
    await page.close();
  }
});

test("dados malformados, 500 e JSON inválido viram cartão de erro sem apagar o painel", async () => {
  const falhas = [
    (r) => r.fulfill(json(env({ dados: { assinaturas: null } }))),                  // ok mas sem os campos
    (r) => r.fulfill(json({ detail: "boom" }, 500)),
    (r) => r.fulfill({ status: 200, contentType: "application/json", body: "{não é json" }),
    (r) => r.fulfill(json("texto solto")),
  ];
  for (const f of falhas) {
    const { page, erros } = await abre(f);
    assert.equal(await page.getAttribute("#f-stripe", "data-estado"), "erro");
    const painel = await page.evaluate(() => document.getElementById("main").textContent);
    assert.ok(painel.includes("1. Cadastro até o plano") && painel.includes("9. Onde olhar o resto"));
    assert.deepEqual(erros.filter((e) => !/status of 500/.test(e)), []);
    await page.close();
  }
});

test("401 na fonte leva ao login", async () => {
  const page = await browser.newPage();
  await page.route("**/admin/api/funil", (r) => r.fulfill(json(FUNIL)));
  await page.route("**/admin/api/funil/fonte/stripe", (r) => r.fulfill(json({ detail: "x" }, 401)));
  await page.route("**/admin/api/funil/fonte/ga4", (r) => r.fulfill(json(GA4_NC)));  // um 401 só: dois redirecionamentos se abortam
  await page.route("**/admin/login", (r) => r.fulfill({ status: 200, contentType: "text/html", body: "login" }));
  await page.goto(`${ORIGIN}/funil.html`, { waitUntil: "domcontentloaded" });
  await page.waitForURL("**/admin/login");
  await page.close();
});

for (const [rotulo, viewport] of [["desktop 1280", { width: 1280, height: 900 }], ["mobile 390", { width: 390, height: 800 }]]) {
  test(`${rotulo}: sem overflow horizontal e alvos >= 40px nos quatro estados`, async () => {
    const corpos = [env(), env({ estado: "stale", mensagem: "A fonte demorou demais para responder." }),
                    env({ estado: "erro", mensagem: "A fonte está indisponível no momento.", dados: null }),
                    env({ estado: "nao_configurado", falta: ["STRIPE_SECRET_KEY"], dados: null })];
    for (const corpo of corpos) {
      const { page, erros } = await abre((r) => r.fulfill(json(corpo)), viewport);
      const m = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        alvos: [...document.querySelectorAll("button, a.btn, .links a")].map((e) => e.getBoundingClientRect().height).filter((h) => h < 40).length,
      }));
      assert.deepEqual([m.overflow, m.alvos], [0, 0], corpo.estado);
      assert.deepEqual(erros, []);
      await page.close();
    }
  });
}

test("aviso de truncado e de disputas aparecem", async () => {
  const dados = { ...DADOS, cobrancas: { ...DADOS.cobrancas, truncado: true } };
  const { page } = await abre((r) => r.fulfill(json(env({ dados }))));
  const t = await texto(page);
  assert.ok(t.includes("Mais de 1.000 cobranças") && t.includes("disputas não são abatidas"));
  await page.close();
  const sem = await abre((r) => r.fulfill(json(env())));
  assert.ok(!(await texto(sem.page)).includes("Mais de 1.000 cobranças"));
  await sem.page.close();
});

test("cliques rápidos em Atualizar: a resposta antiga não sobrescreve a nova", async () => {
  let n = 0;
  const mrr = (v) => env({ dados: { ...DADOS, mrr: v } });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.route("**/admin/api/funil", (r) => r.fulfill(json(FUNIL)));
  await page.route("**/admin/api/funil/fonte/ga4", (r) => r.fulfill(json(GA4_NC)));
  await page.route("**/admin/api/funil/fonte/stripe", async (r) => {
    if (++n === 1) { await new Promise((ok) => setTimeout(ok, 900)); return r.fulfill(json(mrr(111))); }
    return r.fulfill(json(mrr(222)));
  });
  await page.goto(`${ORIGIN}/funil.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#main .card");
  await page.click("#refresh");               // 2º pedido: rápido, devolve 222
  await page.waitForSelector("#f-stripe[data-estado=ok]");
  assert.ok((await texto(page)).includes("222,00"));
  await page.waitForTimeout(1200);            // a resposta lenta (111) chega depois
  const t = await texto(page);
  assert.ok(t.includes("222,00") && !t.includes("111,00"), t.slice(0, 200));
  await page.close();
});

test("320px: código de recusa de 40 caracteres não estoura a largura", async () => {
  const longo = "a".repeat(40);
  const dados = { ...DADOS, cobrancas: { ...DADOS.cobrancas, "30d": cobr(1, 1, 1, [{ codigo: longo, n: 1 }]) } };
  const { page } = await abre((r) => r.fulfill(json(env({ dados }))), { width: 320, height: 800 });
  const m = await page.evaluate(() => ({
    over: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    chip: [...document.querySelectorAll("#f-stripe .chip")].every((c) => c.getBoundingClientRect().right <= document.documentElement.clientWidth),
  }));
  assert.deepEqual([m.over, m.chip], [0, true]);
  await page.close();
});

test("ocupada não é falha: com dados é aviso neutro, sem dados é aviso (não vermelho)", async () => {
  const OCUP = "Já há uma consulta em andamento; tente em instantes.";
  let c = await abre((r) => r.fulfill(json(env({ estado: "stale", mensagem: OCUP }))));
  let t = await texto(c.page);
  assert.ok(t.includes("Atualizando; dado de 12:30.") && t.includes("1.234,56"));
  assert.ok(!t.includes("a fonte falhou") && !t.includes(OCUP));
  assert.equal(await c.page.locator("#f-stripe .banner.err").count(), 0);
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "erro", mensagem: OCUP, dados: null }))));
  t = await texto(c.page);
  assert.ok(t.includes(OCUP));
  assert.equal(await c.page.locator("#f-stripe .banner").count(), 1);
  assert.equal(await c.page.locator("#f-stripe .banner.err").count(), 0, "ocupada não é vermelho de erro");
  await c.page.close();

  // controle positivo: outra mensagem continua sendo falha (erro vermelho / "a fonte falhou")
  c = await abre((r) => r.fulfill(json(env({ estado: "erro", mensagem: "A fonte está indisponível no momento.", dados: null }))));
  assert.equal(await c.page.locator("#f-stripe .banner.err").count(), 1);
  await c.page.close();
  c = await abre((r) => r.fulfill(json(env({ estado: "stale", mensagem: "A fonte demorou demais para responder." }))));
  assert.ok((await texto(c.page)).includes("a fonte falhou"));
  await c.page.close();
});
