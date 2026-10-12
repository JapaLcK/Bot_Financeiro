/**
 * `/admin/funil`, cartão "8d. Meta" (fonte externa, disparos do pixel nos últimos 7 dias, janela FIXA).
 * Mesmo rigor dos cartões do GA4 e do Clarity: texto de fonte entra como TEXTO, só os eventos da lista
 * fixa aparecem (nome estranho no `dados` nunca vira linha), `reconhecido !== true` mostra a faixa âmbar e
 * "n/d" (nunca 0), cada estado aparece, o seletor 7d/30d NÃO refaz o fetch, exceção vira cartão de erro,
 * sem overflow em 1280/390/320, alvos >= 40px, contraste AA >= 4,5 e nada das APIs que o Safari 14 não tem.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { FRONTEND } from "./_painel.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const XSS = '<img src=x onerror="window.__xss=1">';
const OCUP = "Já há uma consulta em andamento; tente em instantes.";
const IDS = ["PageView", "ViewContent", "InitiateCheckout", "CompleteRegistration", "StartTrial",
  "OnboardingComplete", "TesteClick", "Purchase"];
const FUNIL = {
  gerado_em: "2026-10-08T12:00:00+00:00", viewed_pricing_desde: "2026-09-01T00:00:00+00:00",
  janelas: Object.fromEntries([7, 30].map((d) => [`${d}d`, {
    dias: d, inicio: "2026-09-01T00:00:00+00:00", viram_precos_medido: true, emails_verificacao: 1,
    etapas: ["cadastros", "viram_precos", "abriram_checkout", "concluiram"].map((id) => ({ id, n: 1, taxa_etapa: 1, taxa_acum: 1 })),
    abandono: 0, expiraram_sem_concluir: 0,
    estado_atual: { free: 1, trial: 0, paying: 0, past_due: 0, canceled: 0, granted: 0 }, canais: [], origens: [],
    checkout: { pessoas: 1, sessoes_abertas: 1, sessoes_concluidas: 1, sessoes_expiradas: 0, conversao: 1 },
    ativacao: { concluiram: 1, onboarding: 1, whatsapp: 1, lancamento: 1 },
    trial: { iniciaram: 0, em_trial: 0, pagando: 0, cancelaram: 0, outros: 0 },
    pix: { gerados: 0, pagos: 0, expirados: 0, cancelados: 0, abertos: 0, taxa_pago: null },
    ebook: { entregas: 0, enviados: 0, nao_comprou: 0, estornados: 0, pendentes: 0 },
    teste: { clicaram: 0, abriram: 0, organicos: 0, responderam: 0, no_limite: 0, clicaram_checkout: 0 },
  }])),
  atraso: { total: 0, alem_carencia: 0, carencia_dias: 3 }, links: [{ painel: "Afiliados", url: "/admin", olhar: "x" }],
};
const evs = (extra = {}) => ({ eventos: Object.fromEntries(IDS.map((e, i) => [e, i === 7 ? 0 : 12345 * (i + 1)])), reconhecido: true, ...extra });
const DADOS = evs();
const NAO_RECON = { eventos: Object.fromEntries(IDS.map((e) => [e, 0])), reconhecido: false };
const JANELA = { rotulo: "Disparos do pixel por evento, últimos 7 dias (limite da API)", fuso: "UTC" };
const env = (extra) => ({ fonte: "meta", estado: "ok", mensagem: null, falta: null, buscado_em: "2026-10-08T15:30:00+00:00",
                          janela: JANELA, dados: DADOS, ...extra });
const NC = (fonte, falta) => ({ fonte, estado: "nao_configurado", mensagem: null, falta: [falta], buscado_em: null,
                                janela: { rotulo: "x", fuso: "UTC" }, dados: null });
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function abre(responde, viewport = { width: 1280, height: 900 }, ctx) {
  const page = await (ctx ? ctx.newPage() : browser.newPage({ viewport }));
  const pedidos = { meta: 0 };
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") erros.push(m.text()); });
  await page.route("**/admin/api/funil", (r) => r.fulfill(json(FUNIL)));
  await page.route("**/admin/api/funil/fonte/stripe", (r) => r.fulfill(json(NC("stripe", "STRIPE_SECRET_KEY"))));
  await page.route("**/admin/api/funil/fonte/ga4", (r) => r.fulfill(json(NC("ga4", "GA4_SERVICE_ACCOUNT_JSON"))));
  await page.route("**/admin/api/funil/fonte/clarity", (r) => r.fulfill(json(NC("clarity", "CLARITY_API_TOKEN"))));
  await page.route("**/admin/api/funil/fonte/meta", (r) => { pedidos.meta++; return responde(r); });
  await page.goto(`${ORIGIN}/funil.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#f-meta:not([data-estado=carregando])");
  return { page, pedidos, erros };
}
const texto = (page) => page.evaluate(() => document.getElementById("f-meta").textContent);
const linhas = (page) => page.evaluate(() => [...document.querySelectorAll("#f-meta tbody tr")]
  .map((tr) => [...tr.children].map((td) => td.textContent)));
const NAO_RECONHECIDO = "Formato da resposta da Meta não reconhecido; o log do servidor tem os nomes dos campos.";
const NOTA = "São DISPAROS do pixel recebidos pela Meta (navegador + servidor), não conversões deduplicadas: podem divergir do nosso banco e do GA4. A janela de 7 dias é o limite da API da Meta.";

test("ok: um evento por linha, na ordem da lista, em pt-BR, com a nota fixa e o rodapé", async () => {
  const { page, erros } = await abre((r) => r.fulfill(json(env())));
  assert.equal(await page.getAttribute("#f-meta", "data-estado"), "ok");
  const t = await texto(page);
  for (const esperado of ["8d. Meta", "Fonte: Meta", "janela Disparos do pixel por evento, últimos 7 dias (limite da API)",
    "buscado às 12:30", NOTA, "Disparos do pixel, últimos 7 dias"]) assert.ok(t.includes(esperado), esperado);
  assert.ok(!t.includes(NAO_RECONHECIDO));
  const l = await linhas(page);
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll("#f-meta tbody .cod")].map((c) => c.textContent)), IDS);
  assert.deepEqual(l.map((x) => x[1]), ["12.345", "24.690", "37.035", "49.380", "61.725", "74.070", "86.415", "0"]);  // 0 legítimo é 0
  assert.equal(await page.locator("#f-meta .banner").count(), 0);
  assert.deepEqual(erros, []);
  await page.close();
});

test("a ordem dos cartões é 8a, 8b, 8c, 8d e o de links continua sendo o 9", async () => {
  const { page } = await abre((r) => r.fulfill(json(env())));
  const titulos = await page.evaluate(() => [...document.querySelectorAll("#main h2")].map((h) => h.textContent.split(".")[0]));
  assert.deepEqual(titulos.slice(-5), ["8a", "8b", "8c", "8d", "9"]);
  assert.ok((await page.evaluate(() => document.querySelector("#main .card:last-child h2").textContent)).startsWith("9. Onde olhar"));
  await page.close();
});

test("janela fixa: trocar 7d/30d não muda o cartão e NÃO refaz o fetch", async () => {
  const { page, pedidos, erros } = await abre((r) => r.fulfill(json(env())));
  const antes = await texto(page);
  await page.click("#b7");
  assert.equal(await texto(page), antes);
  await page.click("#b30");
  assert.equal(await texto(page), antes);
  assert.equal(pedidos.meta, 1, "trocar a janela não pode pedir a fonte de novo");
  assert.deepEqual(erros, []);
  await page.close();
});

test("reconhecido !== true: faixa âmbar e n/d em vez de zeros; só o booleano true vale", async () => {
  for (const dados of [NAO_RECON, { ...DADOS, reconhecido: "true" }, { ...DADOS, reconhecido: 1 }, { eventos: DADOS.eventos }]) {
    const c = await abre((r) => r.fulfill(json(env({ dados }))));
    assert.ok((await texto(c.page)).includes(NAO_RECONHECIDO), JSON.stringify(dados.reconhecido));
    assert.equal(await c.page.locator("#f-meta .banner").count(), 1);
    assert.equal(await c.page.locator("#f-meta .banner.err").count(), 0);  // âmbar, não vermelho
    assert.deepEqual((await linhas(c.page)).map((x) => x[1]), IDS.map(() => "n/d"));
    assert.deepEqual(c.erros, []);
    await c.page.close();
  }
});

test("eventos sem uma das chaves (payload de cache antigo) mostram 0, nunca NaN nem undefined", async () => {
  const { StartTrial, OnboardingComplete, ...faltando } = DADOS.eventos;  // eslint-disable-line no-unused-vars
  const { page, erros } = await abre((r) => r.fulfill(json(env({ dados: { eventos: faltando, reconhecido: true } }))));
  assert.equal(await page.getAttribute("#f-meta", "data-estado"), "ok");
  const l = await linhas(page);
  assert.equal(l.length, IDS.length);
  assert.deepEqual(l.map((x) => x[1]), ["12.345", "24.690", "37.035", "49.380", "0", "0", "86.415", "0"]);
  const t = await texto(page);
  assert.ok(!t.includes("NaN") && !t.includes("undefined") && !t.includes("null"));
  assert.deepEqual(erros, []);
  await page.close();
});

test("nao_configurado, erro, stale e ocupada (com e sem dados)", async () => {
  let c = await abre((r) => r.fulfill(json(env({ estado: "nao_configurado", falta: ["META_PIXEL_ID", "META_INSIGHTS_ACCESS_TOKEN"], dados: null, buscado_em: null }))));
  assert.equal(await c.page.getAttribute("#f-meta", "data-estado"), "nao_configurado");
  assert.ok((await texto(c.page)).includes("Defina META_PIXEL_ID, META_INSIGHTS_ACCESS_TOKEN no Railway"));
  await c.page.close();

  const PERM = "A Meta negou o acesso: no Business Manager, o usuário do sistema precisa ter o pixel atribuído e o token provavelmente precisa da permissão de leitura de anúncios (a permissão exata não foi confirmada).";
  c = await abre((r) => r.fulfill(json(env({ estado: "erro", mensagem: PERM, dados: null }))));
  assert.equal(await c.page.getAttribute("#f-meta", "data-estado"), "erro");
  assert.ok((await texto(c.page)).includes(PERM));
  assert.equal(await c.page.locator("#f-meta .banner.err").count(), 1);
  assert.equal(await c.page.locator("#f-meta table").count(), 0);
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "stale", mensagem: "A fonte demorou demais para responder." }))));
  assert.equal(await c.page.getAttribute("#f-meta", "data-estado"), "stale");
  let t = await texto(c.page);
  assert.ok(t.includes("Dado de 12:30, a fonte falhou: A fonte demorou demais") && t.includes("12.345"));
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "stale", mensagem: OCUP }))));
  t = await texto(c.page);
  assert.ok(t.includes("Atualizando; dado de 12:30.") && !t.includes("a fonte falhou") && t.includes("12.345"));
  assert.equal(await c.page.locator("#f-meta .banner.err").count(), 0);
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "erro", mensagem: OCUP, dados: null }))));
  assert.ok((await texto(c.page)).includes(OCUP));
  assert.equal(await c.page.locator("#f-meta .banner.err").count(), 0);
  await c.page.close();
});

test("nome de evento fora da lista nunca vira linha; números, estado e textos hostis viram texto", async () => {
  const dados = evs({ eventos: { ...DADOS.eventos, [XSS]: 5, "<script>window.__xss=1</script>": 7, PageView: XSS, Purchase: "</td><script>window.__xss=1</script>" } });
  const hostil = { mensagem: XSS, janela: { rotulo: XSS, fuso: XSS }, buscado_em: XSS };
  const casos = [
    [{ ...hostil, estado: XSS, falta: [XSS], dados }, "erro"],
    [{ ...hostil, estado: "constructor", dados }, "erro"],
    [{ ...hostil, estado: "nao_configurado", falta: [XSS], dados: null }, "nao_configurado"],
    [{ ...hostil, estado: "stale", dados }, "stale"],
    [{ ...hostil, estado: "ok", dados }, "ok"],
  ];
  for (const [corpo, esperado] of casos) {
    const { page } = await abre((r) => r.fulfill(json(env(corpo))));
    assert.equal(await page.getAttribute("#f-meta", "data-estado"), esperado);
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(await page.evaluate(() => [
      document.querySelectorAll("#main img").length, document.querySelectorAll("#main script").length,
      document.querySelectorAll("#main [onerror], #main [onmouseover], #main [onclick]").length]), [0, 0, 0]);
    if (esperado === "ok") {
      const l = await linhas(page);
      assert.equal(l.length, IDS.length);  // as chaves hostis do `dados` não geraram linha
      assert.equal(l[0][1], "NaN");  // número hostil não vira HTML nem some
    }
    await page.close();
  }
});

test("dados malformados viram cartão de erro sem apagar o painel nem os outros cartões", async () => {
  for (const dados of [null, {}, { eventos: null, reconhecido: true }, { reconhecido: true }, "x", []]) {
    const { page, erros } = await abre((r) => r.fulfill(json(env({ dados }))));
    assert.equal(await page.getAttribute("#f-meta", "data-estado"), "erro", JSON.stringify(dados));
    const painel = await page.evaluate(() => document.getElementById("main").textContent);
    assert.ok(painel.includes("1. Cadastro até o plano") && painel.includes("8a. Stripe") && painel.includes("8c. Clarity") && painel.includes("9. Onde olhar o resto"));
    assert.ok((await texto(page)).includes("Não foi possível consultar esta fonte"));
    assert.deepEqual(erros, []);
    await page.close();
  }
});

test("500 e JSON inválido na rota viram cartão de erro", async () => {
  for (const f of [(r) => r.fulfill(json({ detail: "boom" }, 500)), (r) => r.fulfill({ status: 200, contentType: "application/json", body: "{não é json" })]) {
    const { page } = await abre(f);
    assert.equal(await page.getAttribute("#f-meta", "data-estado"), "erro");
    assert.ok((await texto(page)).includes("Não foi possível consultar esta fonte"));
    await page.close();
  }
});

for (const [rotulo, viewport] of [["1280", { width: 1280, height: 900 }], ["390", { width: 390, height: 800 }], ["320", { width: 320, height: 800 }]]) {
  test(`${rotulo}px: sem overflow horizontal, alvos >= 40px`, async () => {
    const grande = evs({ eventos: Object.fromEntries(IDS.map((e) => [e, 123456789])) });
    for (const corpo of [env({ dados: grande }), env({ dados: NAO_RECON }), env({ estado: "stale", mensagem: "A fonte demorou demais para responder.", dados: grande }),
                         env({ estado: "erro", mensagem: "A fonte está indisponível no momento.", dados: null }),
                         env({ estado: "nao_configurado", falta: ["META_INSIGHTS_ACCESS_TOKEN"], dados: null })]) {
      const { page, erros } = await abre((r) => r.fulfill(json(corpo)), viewport);
      for (const w of ["#b30", "#b7"]) {
        await page.click(w);
        const m = await page.evaluate(() => ({
          pagina: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          cartao: (() => { const c = document.getElementById("f-meta"); return c.scrollWidth - c.clientWidth; })(),
          alvos: [...document.querySelectorAll("button, a.btn, .links a")].filter((e) => e.getBoundingClientRect().height < 40).length,
        }));
        assert.deepEqual([m.pagina, m.cartao, m.alvos], [0, 0, 0], `${corpo.estado} ${w}`);
      }
      assert.deepEqual(erros, []);
      await page.close();
    }
  });
}

test("contraste AA >= 4,5 em todo texto do cartão Meta (ok, não reconhecido, stale, erro, nao_configurado)", async () => {
  const corpos = [env(), env({ dados: NAO_RECON }), env({ estado: "stale", mensagem: "A fonte demorou demais para responder." }),
                  env({ estado: "erro", mensagem: "A fonte está indisponível no momento.", dados: null }),
                  env({ estado: "nao_configurado", falta: ["META_INSIGHTS_ACCESS_TOKEN"], dados: null })];
  for (const corpo of corpos) {
    const { page } = await abre((r) => r.fulfill(json(corpo)));
    const r = await page.evaluate(() => {
      const num = (s) => s.match(/[\d.]+/g).map(Number);
      const cor = (s) => { const [r, g, b, a = 1] = num(s); return { r, g, b, a }; };
      const sobre = (f, b) => ({ r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1 });
      const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
      const fundo = (el) => {
        const cadeia = []; for (let e = el; e; e = e.parentElement) cadeia.unshift(cor(getComputedStyle(e).backgroundColor));
        return cadeia.reduce((acc, c) => sobre(c, acc), { r: 255, g: 255, b: 255, a: 1 });
      };
      let pior = { ratio: 99, el: "" };
      for (const el of document.querySelectorAll("#f-meta *")) {
        if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
        const bg = fundo(el), fg = sobre(cor(getComputedStyle(el).color), bg);
        const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
        const ratio = (a + 0.05) / (b + 0.05);
        if (ratio < pior.ratio) pior = { ratio, el: `${el.tagName}.${el.className}` };
      }
      return pior;
    });
    assert.ok(r.ratio >= 4.5, `${corpo.estado}: ${r.el} ${r.ratio.toFixed(2)}`);
    await page.close();
  }
});

test("sem as APIs do Safari 15+ (Object.hasOwn, .at, structuredClone...): o cartão Meta monta e mostra os números", async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 800 } });
  await ctx.addInitScript(() => {
    const TA = Object.getPrototypeOf(Int8Array.prototype);
    for (const [o, k] of [[Array.prototype, "at"], [String.prototype, "at"], [TA, "at"], [Array.prototype, "findLast"],
      [Array.prototype, "findLastIndex"], [Array.prototype, "toSorted"], [Array.prototype, "toReversed"],
      [Array.prototype, "toSpliced"], [Object, "hasOwn"], [Object, "groupBy"], [Promise, "withResolvers"],
      [window, "structuredClone"], [window, "WeakRef"]]) delete o[k];
  });
  const { page, erros } = await abre((r) => r.fulfill(json(env())), undefined, ctx);
  assert.equal(await page.evaluate(() => [typeof Object.hasOwn, typeof structuredClone, typeof [].at].join()), "undefined,undefined,undefined");
  assert.equal(await page.getAttribute("#f-meta", "data-estado"), "ok");
  assert.ok((await texto(page)).includes("86.415"));
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("a lista de eventos do front é a mesma do backend (core/funil_fonte_meta.py)", () => {
  const html = readFileSync(join(FRONTEND, "funil.html"), "utf8");
  const front = [...html.slice(html.indexOf("const META_EVENTOS"), html.indexOf("function metaCorpo")).matchAll(/\["([A-Za-z]+)",/g)].map((m) => m[1]);
  const py = readFileSync(join(FRONTEND, "..", "core", "funil_fonte_meta.py"), "utf8");
  const back = [...py.slice(py.indexOf("EVENTOS = ("), py.indexOf(")", py.indexOf("EVENTOS = ("))).matchAll(/"([A-Za-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual(front, IDS);
  assert.deepEqual(back, IDS);
});

test("`janela` e `fuso` do payload só entram escapados no rodapé; o título e a nota são texto fixo", async () => {
  const { page } = await abre((r) => r.fulfill(json(env({ janela: { rotulo: XSS, fuso: XSS } }))));
  const t = await texto(page);
  assert.ok(t.includes("8d. Meta: disparos do pixel nos últimos 7 dias") && t.includes(NOTA));
  assert.equal(await page.locator("#f-meta img").count(), 0);
  await page.close();
});
