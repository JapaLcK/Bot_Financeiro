/**
 * `/admin/funil`, cartão "8b. GA4" (fonte externa). Mesmo rigor do cartão do Stripe
 * (`funil_fontes.test.mjs`): texto de fonte entra como TEXTO, cada estado aparece, o seletor
 * 7d/30d reaproveita o envelope (sem novo fetch), exceção vira cartão de erro, sem overflow em
 * 1280/390/320, alvos >= 40px e contraste AA >= 4,5 em todo texto do cartão.
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
const IDS = ["page_view", "view_item_list", "begin_checkout", "sign_up", "start_trial",
  "onboarding_complete", "vsl_play", "vsl_progress", "teste_click", "purchase"];
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
const evs = (k) => Object.fromEntries(IDS.map((e, i) => [e, { eventos: 1000 * k + i, usuarios: 100 * k + i }]));
const DADOS = {
  eventos: { "7d": evs(1), "30d": evs(2) },
  origens: { "7d": [{ canal: "Direct", sessoes: 1234, usuarios: 987 }, { canal: "Organic Search", sessoes: 20, usuarios: 15 }],
             "30d": [{ canal: "Paid Social", sessoes: 99999, usuarios: 5555 }] },
};
const JANELA = { rotulo: "Eventos do funil e origem das sessões, 7 e 30 dias mais hoje (parcial)", fuso: "o da propriedade GA4" };
const env = (extra) => ({ fonte: "ga4", estado: "ok", mensagem: null, falta: null, buscado_em: "2026-10-08T15:30:00+00:00",
                          janela: JANELA, dados: DADOS, ...extra });
const STRIPE_NC = { fonte: "stripe", estado: "nao_configurado", mensagem: null, falta: ["STRIPE_SECRET_KEY"],
                    buscado_em: null, janela: { rotulo: "x", fuso: "UTC" }, dados: null };
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function abre(responde, viewport = { width: 1280, height: 900 }) {
  const page = await browser.newPage({ viewport });
  const pedidos = { ga4: 0 };
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") erros.push(m.text()); });
  await page.route("**/admin/api/funil", (r) => r.fulfill(json(FUNIL)));
  await page.route("**/admin/api/funil/fonte/stripe", (r) => r.fulfill(json(STRIPE_NC)));
  await page.route("**/admin/api/funil/fonte/ga4", (r) => { pedidos.ga4++; return responde(r); });
  await page.goto(`${ORIGIN}/funil.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#f-ga4:not([data-estado=carregando])");
  return { page, pedidos, erros };
}
const texto = (page) => page.evaluate(() => document.getElementById("f-ga4").textContent);
const linhas = (page, i) => page.evaluate((i) => [...document.querySelectorAll("#f-ga4 table")[i].querySelectorAll("tbody tr")]
  .map((tr) => [...tr.children].map((c) => c.textContent)), i);

test("ok: eventos na ordem do funil, números pt-BR, origens, avisos fixos e rodapé", async () => {
  const { page, erros } = await abre((r) => r.fulfill(json(env())));
  assert.equal(await page.getAttribute("#f-ga4", "data-estado"), "ok");
  const t = await texto(page);
  for (const esperado of ["8b. GA4", "Fonte: GA4", "buscado às 12:30", "Página vista", "Compra (inclui as do servidor)",
    "não batem com os do nosso banco", "bloqueador de anúncio, consentimento", "dia de hoje é parcial",
    "fuso é o da propriedade GA4", "inclui os eventos enviados pelo servidor"]) assert.ok(t.includes(esperado), esperado);
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll("#f-ga4 .cod")].map((e) => e.textContent)), IDS);
  assert.deepEqual((await linhas(page, 0))[8].slice(1), ["208", "2.008"]);  // purchase, 30d (padrão)
  await page.click("#b7");
  assert.ok((await texto(page)).includes("Direct") && (await texto(page)).includes("1.234") && (await texto(page)).includes("987"));
  assert.deepEqual(erros, []);
  await page.close();
});

test("ok: janela padrão 30d; trocar para 7d troca os números e NÃO refaz o fetch", async () => {
  const { page, pedidos, erros } = await abre((r) => r.fulfill(json(env())));
  assert.deepEqual((await linhas(page, 0))[0].slice(1), ["200", "2.000"]);
  assert.deepEqual(await linhas(page, 1), [["Paid Social", "99.999", "5.555"]]);
  await page.click("#b7");
  assert.deepEqual((await linhas(page, 0))[0].slice(1), ["100", "1.000"]);
  assert.deepEqual((await linhas(page, 1)).map((l) => l[0]), ["Direct", "Organic Search"]);
  assert.ok((await texto(page)).includes("últimos 7 dias e hoje"));
  await page.click("#b30");
  assert.ok((await texto(page)).includes("últimos 30 dias e hoje"));
  assert.equal(pedidos.ga4, 1, "trocar a janela não pode pedir a fonte de novo");
  assert.deepEqual(erros, []);
  await page.close();
});

test("nao_configurado, erro, stale e ocupada (com e sem dados)", async () => {
  let c = await abre((r) => r.fulfill(json(env({ estado: "nao_configurado", falta: ["GA4_SERVICE_ACCOUNT_JSON"], dados: null, buscado_em: null }))));
  assert.equal(await c.page.getAttribute("#f-ga4", "data-estado"), "nao_configurado");
  assert.ok((await texto(c.page)).includes("Defina GA4_SERVICE_ACCOUNT_JSON no Railway"));
  await c.page.close();
  c = await abre((r) => r.fulfill(json(env({ estado: "nao_configurado", falta: ["GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON"], dados: null }))));
  assert.ok((await texto(c.page)).includes("Defina GA4_PROPERTY_ID, GA4_SERVICE_ACCOUNT_JSON no Railway"));
  await c.page.close();

  const MSG = "O GA4 negou o acesso: habilite a Google Analytics Data API no projeto, dê papel de Leitor à conta de serviço na propriedade e confira se GA4_PROPERTY_ID é só o número da propriedade.";
  c = await abre((r) => r.fulfill(json(env({ estado: "erro", mensagem: MSG, dados: null }))));
  assert.equal(await c.page.getAttribute("#f-ga4", "data-estado"), "erro");
  assert.ok((await texto(c.page)).includes("Google Analytics Data API"));
  assert.equal(await c.page.locator("#f-ga4 .banner.err").count(), 1);
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "stale", mensagem: "A fonte demorou demais para responder." }))));
  assert.equal(await c.page.getAttribute("#f-ga4", "data-estado"), "stale");
  let t = await texto(c.page);
  assert.ok(t.includes("Dado de 12:30, a fonte falhou: A fonte demorou") && t.includes("Paid Social") && t.includes("2.008"));
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "stale", mensagem: OCUP }))));
  t = await texto(c.page);
  assert.ok(t.includes("Atualizando; dado de 12:30.") && !t.includes("a fonte falhou") && t.includes("Paid Social"));
  assert.equal(await c.page.locator("#f-ga4 .banner.err").count(), 0);
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "erro", mensagem: OCUP, dados: null }))));
  assert.ok((await texto(c.page)).includes(OCUP));
  assert.equal(await c.page.locator("#f-ga4 .banner").count(), 1);
  assert.equal(await c.page.locator("#f-ga4 .banner.err").count(), 0);
  await c.page.close();
});

test("canal e números hostis viram texto, em todos os estados; nada injetado", async () => {
  const dados = {
    eventos: { "7d": { ...evs(1), page_view: { eventos: XSS, usuarios: XSS }, [XSS]: { eventos: 1, usuarios: 1 } }, "30d": evs(2) },
    origens: { "7d": [{ canal: XSS, sessoes: XSS, usuarios: XSS }, { canal: "</td><script>window.__xss=1</script>", sessoes: 1, usuarios: 1 }],
               "30d": [{ canal: XSS, sessoes: 2, usuarios: 2 }] },
  };
  const hostil = { mensagem: XSS, janela: { rotulo: XSS, fuso: XSS }, buscado_em: XSS };
  const casos = [
    [{ ...hostil, estado: XSS, falta: [XSS], dados }, "erro"],
    [{ ...hostil, estado: "constructor", dados }, "erro"],
    [{ ...hostil, estado: "nao_configurado", falta: [XSS], dados: null }, "nao_configurado"],
    [{ ...hostil, estado: "stale", dados }, "stale"],
    [{ ...hostil, estado: "ok", dados }, "ok"],
  ];
  for (const win of ["#b30", "#b7"]) {
    for (const [corpo, esperado] of casos) {
      const { page } = await abre((r) => r.fulfill(json(env(corpo))));
      await page.click(win);
      assert.equal(await page.getAttribute("#f-ga4", "data-estado"), esperado);
      await page.waitForTimeout(100);
      assert.equal(await page.evaluate(() => window.__xss), undefined);
      assert.deepEqual(await page.evaluate(() => [
        document.querySelectorAll("#main img").length, document.querySelectorAll("#main script").length,
        document.querySelectorAll("#main [onerror], #main [onmouseover], #main [onclick]").length]), [0, 0, 0]);
      if (esperado === "ok") assert.ok((await texto(page)).includes("<img src=x") && (await texto(page)).includes("NaN") === (win === "#b7"));
      await page.close();
    }
  }
});

test("dados malformados viram cartão de erro sem apagar o painel nem o cartão do Stripe", async () => {
  for (const dados of [null, {}, { eventos: null, origens: null }, { eventos: { "7d": {} }, origens: {} },
                       { eventos: { "7d": evs(1), "30d": evs(2) }, origens: "x" }, { eventos: "x", origens: [] }]) {
    const { page, erros } = await abre((r) => r.fulfill(json(env({ dados }))));
    assert.equal(await page.getAttribute("#f-ga4", "data-estado"), "erro", JSON.stringify(dados));
    const painel = await page.evaluate(() => document.getElementById("main").textContent);
    assert.ok(painel.includes("1. Cadastro até o plano") && painel.includes("8a. Stripe") && painel.includes("9. Onde olhar o resto"));
    assert.ok((await texto(page)).includes("Não foi possível consultar esta fonte"));
    assert.deepEqual(erros, []);
    await page.close();
  }
});

test("evento que o GA4 não devolveu aparece com zeros; origem vazia tem aviso", async () => {
  const dados = { eventos: { "7d": { page_view: { eventos: 5, usuarios: 4 } }, "30d": {} }, origens: { "7d": [], "30d": [] } };
  const { page } = await abre((r) => r.fulfill(json(env({ dados }))));
  const ev = await linhas(page, 0);
  assert.equal(ev.length, IDS.length);
  assert.ok(ev.every((l) => l[1] === "0" && l[2] === "0"));
  assert.ok((await texto(page)).includes("Nenhuma sessão na janela."));
  await page.click("#b7");
  assert.deepEqual((await linhas(page, 0))[0].slice(1), ["4", "5"]);
  await page.close();
});

const CANAL_LONGO = "a".repeat(40);
for (const [rotulo, viewport] of [["1280", { width: 1280, height: 900 }], ["390", { width: 390, height: 800 }], ["320", { width: 320, height: 800 }]]) {
  test(`${rotulo}px: sem overflow horizontal (página e tabelas), alvos >= 40px, texto longo quebra`, async () => {
    const dados = { ...DADOS, origens: { "7d": DADOS.origens["7d"], "30d": [{ canal: CANAL_LONGO, sessoes: 123456789, usuarios: 98765432 }] } };
    for (const corpo of [env({ dados }), env({ estado: "stale", mensagem: "A fonte demorou demais para responder.", dados }),
                         env({ estado: "erro", mensagem: "A fonte está indisponível no momento.", dados: null }),
                         env({ estado: "nao_configurado", falta: ["GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON"], dados: null })]) {
      const { page, erros } = await abre((r) => r.fulfill(json(corpo)), viewport);
      for (const w of ["#b30", "#b7"]) {
        await page.click(w);
        const m = await page.evaluate(() => ({
          pagina: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          tabelas: [...document.querySelectorAll("#f-ga4 .tblwrap")].filter((t) => t.scrollWidth > t.clientWidth)
            .map((t) => `${t.scrollWidth}>${t.clientWidth}: ${t.textContent.slice(0, 30)}`),
          alvos: [...document.querySelectorAll("button, a.btn, .links a")].filter((e) => e.getBoundingClientRect().height < 40).length,
        }));
        assert.deepEqual([m.pagina, m.tabelas, m.alvos], [0, [], 0], `${corpo.estado} ${w}`);
      }
      assert.deepEqual(erros, []);
      await page.close();
    }
  });
}

test("contraste AA >= 4,5 em todo texto do cartão GA4 (ok, stale, erro, nao_configurado)", async () => {
  const corpos = [env(), env({ estado: "stale", mensagem: "A fonte demorou demais para responder." }),
                  env({ estado: "erro", mensagem: "A fonte está indisponível no momento.", dados: null }),
                  env({ estado: "nao_configurado", falta: ["GA4_SERVICE_ACCOUNT_JSON"], dados: null })];
  for (const corpo of corpos) {
    const { page } = await abre((r) => r.fulfill(json(corpo)));
    const r = await page.evaluate(() => {
      const num = (s) => s.match(/[\d.]+/g).map(Number);
      const cor = (s) => { const [r, g, b, a = 1] = num(s); return { r, g, b, a }; };
      const sobre = (f, b) => ({ r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1 });
      const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
      const fundo = (el) => {  // compõe os fundos translúcidos, do mais externo para o elemento
        const cadeia = []; for (let e = el; e; e = e.parentElement) cadeia.unshift(cor(getComputedStyle(e).backgroundColor));
        return cadeia.reduce((acc, c) => sobre(c, acc), { r: 255, g: 255, b: 255, a: 1 });
      };
      let pior = { ratio: 99, el: "" };
      for (const el of document.querySelectorAll("#f-ga4 *")) {
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

test("a lista de eventos do front é a mesma do backend (core/funil_fonte_ga4.py)", () => {
  const html = readFileSync(join(FRONTEND, "funil.html"), "utf8");
  const front = [...html.slice(html.indexOf("const GA4_EVENTOS"), html.indexOf("function ga4Corpo")).matchAll(/\["([a-z_]+)",/g)].map((m) => m[1]);
  const py = readFileSync(join(FRONTEND, "..", "core", "funil_fonte_ga4.py"), "utf8");
  const back = [...py.slice(py.indexOf("EVENTOS = ("), py.indexOf(")", py.indexOf("EVENTOS = (")) ).matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
  assert.deepEqual(front, IDS);
  assert.deepEqual(back, IDS);
});
