/**
 * `/admin/funil`, cartão "8c. Clarity" (fonte externa, página /precos, janela FIXA de 3 dias).
 * Mesmo rigor dos cartões do Stripe e do GA4: texto de fonte entra como TEXTO, `null` aparece como
 * "n/d" (nunca 0), cada estado aparece, o seletor 7d/30d NÃO mexe no cartão (nem refaz fetch),
 * exceção vira cartão de erro, sem overflow em 1280/390/320, alvos >= 40px e contraste AA >= 4,5.
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
const OCUP = "Já há uma consulta em andamento; tente em instantes.";
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
const DADOS = {
  pagina: "/precos", janela_dias: 3, trafego: { sessoes: 1234, usuarios: 987 }, rolagem_media_pct: 54.5,
  cliques_mortos: { pct_sessoes: 12.3 }, cliques_raiva: { pct_sessoes: 1.2 },
  truncado: false, reconhecido: true,
};
const NULOS = { ...DADOS, trafego: { sessoes: null, usuarios: null }, rolagem_media_pct: null,
  cliques_mortos: { pct_sessoes: null }, cliques_raiva: { pct_sessoes: null }, reconhecido: false };
const JANELA = { rotulo: "últimos 3 dias (UTC)", fuso: "UTC" };
const env = (extra) => ({ fonte: "clarity", estado: "ok", mensagem: null, falta: null, buscado_em: "2026-10-08T15:30:00+00:00",
                          janela: JANELA, dados: DADOS, ...extra });
const NC = (fonte, falta) => ({ fonte, estado: "nao_configurado", mensagem: null, falta: [falta], buscado_em: null,
                                janela: { rotulo: "x", fuso: "UTC" }, dados: null });
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function abre(responde, viewport = { width: 1280, height: 900 }) {
  const page = await browser.newPage({ viewport });
  const pedidos = { clarity: 0 };
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") erros.push(m.text()); });
  await page.route("**/admin/api/funil", (r) => r.fulfill(json(FUNIL)));
  await page.route("**/admin/api/funil/fonte/stripe", (r) => r.fulfill(json(NC("stripe", "STRIPE_SECRET_KEY"))));
  await page.route("**/admin/api/funil/fonte/ga4", (r) => r.fulfill(json(NC("ga4", "GA4_SERVICE_ACCOUNT_JSON"))));
  await page.route("**/admin/api/funil/fonte/clarity", (r) => { pedidos.clarity++; return responde(r); });
  await page.goto(`${ORIGIN}/funil.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#f-clarity:not([data-estado=carregando])");
  return { page, pedidos, erros };
}
const texto = (page) => page.evaluate(() => document.getElementById("f-clarity").textContent);
const fatos = (page) => page.evaluate(() => Object.fromEntries([...document.querySelectorAll("#f-clarity .fact")]
  .map((f) => [f.querySelector(".l").textContent, [f.querySelector(".v").textContent, f.querySelector(".s")?.textContent ?? ""]])));
const AVISO = "Só a página /precos; janela máxima de 3 dias; sessões sem bots quando o Clarity informa; o Clarity pode truncar em 1.000 linhas (“números parciais”); a cota do Clarity é de 10 consultas por dia, por isso o cartão atualiza no máximo a cada 3 h.";
const NAO_RECONHECIDO = "Formato da resposta do Clarity não reconhecido; o log do servidor tem os nomes dos campos.";

test("ok: sessões, usuários, rolagem, cliques mortos e de raiva em pt-BR, avisos fixos e rodapé", async () => {
  const { page, erros } = await abre((r) => r.fulfill(json(env())));
  assert.equal(await page.getAttribute("#f-clarity", "data-estado"), "ok");
  const t = await texto(page);
  for (const esperado of ["8c. Clarity", "Fonte: Clarity", "janela últimos 3 dias (UTC)", "buscado às 12:30", AVISO,
    "Página /precos, últimos 3 dias (UTC)"]) assert.ok(t.includes(esperado), esperado);
  assert.ok(!t.includes(NAO_RECONHECIDO) && !t.includes("Números parciais") && !t.includes("Sem visitas"));
  const f = await fatos(page);
  assert.deepEqual(f["Sessões"], ["1.234", "sem bots, quando o Clarity informa"]);
  assert.equal(f["Usuários"][0], "987");
  assert.equal(f["Rolagem média"][0], "54,5%");
  assert.equal(f["Rolagem média"][1], "média das variantes da URL; ponderada pelas sessões só quando o Clarity informa a contagem");
  assert.notEqual(f["Rolagem média"][1], "ponderada pelas sessões");  // nunca afirma ponderação incondicional
  assert.ok(t.includes("Confira com o painel do Clarity: se a rolagem ou os percentuais parecerem pequenos demais (ex.: 0,1%), a escala pode ser 0 a 1."));
  assert.deepEqual(f["Cliques mortos"], ["12,3%", "das sessões"]);
  assert.deepEqual(f["Cliques de raiva"], ["1,2%", "das sessões"]);
  assert.ok(!t.includes("com o evento"));  // a contagem de sessões dos cliques não é exibida (ambígua)
  assert.deepEqual(erros, []);
  await page.close();
});

test("a ordem dos cartões é 8a, 8b, 8c e o de links continua sendo o 9", async () => {
  const { page } = await abre((r) => r.fulfill(json(env())));
  const titulos = await page.evaluate(() => [...document.querySelectorAll("#main h2")].map((h) => h.textContent.split(".")[0]));
  assert.deepEqual(titulos.slice(-4), ["8a", "8b", "8c", "9"]);
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
  assert.ok(!(await texto(page)).includes("7 dias") && !(await texto(page)).includes("30 dias"));
  assert.equal(pedidos.clarity, 1, "trocar a janela não pode pedir a fonte de novo");
  assert.deepEqual(erros, []);
  await page.close();
});

test("null aparece como n/d (nunca 0); zero legítimo aparece como 0", async () => {
  let c = await abre((r) => r.fulfill(json(env({ dados: NULOS }))));
  let f = await fatos(c.page);
  assert.deepEqual([f["Sessões"][0], f["Usuários"][0], f["Rolagem média"][0], f["Cliques mortos"][0], f["Cliques de raiva"][0]],
    ["n/d", "n/d", "n/d", "n/d", "n/d"]);
  assert.deepEqual(f["Cliques mortos"], ["n/d", "das sessões"]);
  assert.ok(!(await texto(c.page)).includes("0%"));
  await c.page.close();
  // sem linha de /precos (formato reconhecido): sessões e usuários 0, as MÉDIAS são null ("n/d")
  const zeros = { ...DADOS, trafego: { sessoes: 0, usuarios: 0 }, rolagem_media_pct: null,
                  cliques_mortos: { pct_sessoes: null }, cliques_raiva: { pct_sessoes: null } };
  c = await abre((r) => r.fulfill(json(env({ dados: zeros }))));
  f = await fatos(c.page);
  assert.deepEqual([f["Sessões"][0], f["Usuários"][0], f["Rolagem média"][0], f["Cliques mortos"][0], f["Cliques de raiva"][0]], ["0", "0", "n/d", "n/d", "n/d"]);
  assert.ok((await texto(c.page)).includes("Sem visitas em /precos nos últimos 3 dias."));
  await c.page.close();
  // zero legítimo de uma média existente continua 0,0%
  c = await abre((r) => r.fulfill(json(env({ dados: { ...zeros, rolagem_media_pct: 0, cliques_mortos: { pct_sessoes: 0 } } }))));
  f = await fatos(c.page);
  assert.deepEqual([f["Rolagem média"][0], f["Cliques mortos"][0]], ["0,0%", "0,0%"]);
  await c.page.close();
});

test("'Sem visitas' só quando se sabe: nunca com truncado, com sessões null ou formato não reconhecido", async () => {
  const SEM = "Sem visitas em /precos";
  const base = { ...DADOS, trafego: { sessoes: 0, usuarios: 0 } };
  for (const [dados, diz] of [
    [base, true],
    [{ ...base, truncado: true }, false],  // a linha de /precos pode estar na parte cortada
    [{ ...base, trafego: { sessoes: null, usuarios: null }, truncado: true }, false],
    [{ ...base, trafego: { sessoes: null, usuarios: null } }, false],
    [{ ...base, reconhecido: false }, false],
    [{ ...base, reconhecido: "true" }, false],
    [{ ...base, trafego: { sessoes: "0", usuarios: 0 } }, false],  // só o número 0 vale
    [{ ...base, trafego: { sessoes: false, usuarios: 0 } }, false],
    [{ ...base, trafego: { sessoes: "", usuarios: 0 } }, false],
  ]) {
    const c = await abre((r) => r.fulfill(json(env({ dados }))));
    const t = await texto(c.page);
    assert.equal(t.includes(SEM), diz, JSON.stringify(dados));
    assert.equal(t.includes("Números parciais"), !!dados.truncado, JSON.stringify(dados));
    await c.page.close();
  }
});

test("reconhecido=false mostra o aviso do formato; truncado mostra 'números parciais'", async () => {
  let c = await abre((r) => r.fulfill(json(env({ dados: NULOS }))));
  assert.ok((await texto(c.page)).includes(NAO_RECONHECIDO));
  assert.equal(await c.page.locator("#f-clarity .banner").count(), 1);
  assert.ok(!(await texto(c.page)).includes("Sem visitas"));  // n/d não é "sem visitas"
  await c.page.close();
  c = await abre((r) => r.fulfill(json(env({ dados: { ...DADOS, truncado: true } }))));
  assert.ok((await texto(c.page)).includes("Números parciais: o Clarity devolveu 1.000 linhas"));
  assert.ok(!(await texto(c.page)).includes(NAO_RECONHECIDO));
  await c.page.close();
  c = await abre((r) => r.fulfill(json(env({ dados: { ...DADOS, reconhecido: "true" } }))));  // só o booleano true vale
  assert.ok((await texto(c.page)).includes(NAO_RECONHECIDO));
  await c.page.close();
});

test("nao_configurado, erro, stale e ocupada (com e sem dados)", async () => {
  let c = await abre((r) => r.fulfill(json(env({ estado: "nao_configurado", falta: ["CLARITY_API_TOKEN"], dados: null, buscado_em: null }))));
  assert.equal(await c.page.getAttribute("#f-clarity", "data-estado"), "nao_configurado");
  assert.ok((await texto(c.page)).includes("Defina CLARITY_API_TOKEN no Railway"));
  await c.page.close();

  const COTA = "Limite diário de consultas à fonte atingido; volta amanhã.";
  c = await abre((r) => r.fulfill(json(env({ estado: "erro", mensagem: COTA, dados: null }))));
  assert.equal(await c.page.getAttribute("#f-clarity", "data-estado"), "erro");
  assert.ok((await texto(c.page)).includes(COTA));
  assert.equal(await c.page.locator("#f-clarity .banner.err").count(), 1);
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "stale", mensagem: COTA }))));
  assert.equal(await c.page.getAttribute("#f-clarity", "data-estado"), "stale");
  let t = await texto(c.page);
  assert.ok(t.includes(`Dado de 12:30, a fonte falhou: ${COTA}`) && t.includes("1.234"));
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "stale", mensagem: OCUP }))));
  t = await texto(c.page);
  assert.ok(t.includes("Atualizando; dado de 12:30.") && !t.includes("a fonte falhou") && t.includes("1.234"));
  assert.equal(await c.page.locator("#f-clarity .banner.err").count(), 0);
  await c.page.close();

  c = await abre((r) => r.fulfill(json(env({ estado: "erro", mensagem: OCUP, dados: null }))));
  assert.ok((await texto(c.page)).includes(OCUP));
  assert.equal(await c.page.locator("#f-clarity .banner").count(), 1);
  assert.equal(await c.page.locator("#f-clarity .banner.err").count(), 0);
  await c.page.close();
});

test("números, estado e textos hostis viram texto, em todos os estados; nada injetado", async () => {
  const dados = { ...DADOS, trafego: { sessoes: XSS, usuarios: XSS }, rolagem_media_pct: XSS,
                  cliques_mortos: { pct_sessoes: XSS }, cliques_raiva: { pct_sessoes: "</td><script>window.__xss=1</script>" } };
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
    assert.equal(await page.getAttribute("#f-clarity", "data-estado"), esperado);
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(await page.evaluate(() => [
      document.querySelectorAll("#main img").length, document.querySelectorAll("#main script").length,
      document.querySelectorAll("#main [onerror], #main [onmouseover], #main [onclick]").length]), [0, 0, 0]);
    if (esperado === "ok") assert.ok((await texto(page)).includes("NaN"));  // número hostil não vira HTML nem some
    if (esperado !== "ok") assert.ok(!(await texto(page)).includes("<img") || (await page.locator("#f-clarity img").count()) === 0);
    await page.close();
  }
});

test("dados malformados viram cartão de erro sem apagar o painel nem os outros cartões", async () => {
  for (const dados of [null, {}, { trafego: null }, { ...DADOS, trafego: null },                        { ...DADOS, cliques_raiva: null }, "x", []]) {
    const { page, erros } = await abre((r) => r.fulfill(json(env({ dados }))));
    assert.equal(await page.getAttribute("#f-clarity", "data-estado"), "erro", JSON.stringify(dados));
    const painel = await page.evaluate(() => document.getElementById("main").textContent);
    assert.ok(painel.includes("1. Cadastro até o plano") && painel.includes("8a. Stripe") && painel.includes("8b. GA4") && painel.includes("9. Onde olhar o resto"));
    assert.ok((await texto(page)).includes("Não foi possível consultar esta fonte"));
    assert.deepEqual(erros, []);
    await page.close();
  }
});

for (const [rotulo, viewport] of [["1280", { width: 1280, height: 900 }], ["390", { width: 390, height: 800 }], ["320", { width: 320, height: 800 }]]) {
  test(`${rotulo}px: sem overflow horizontal, alvos >= 40px, texto longo quebra`, async () => {
    const grande = { ...DADOS, trafego: { sessoes: 1234567, usuarios: 9876543 }, truncado: true };
    for (const corpo of [env({ dados: grande }), env({ dados: NULOS }), env({ estado: "stale", mensagem: "A fonte demorou demais para responder.", dados: grande }),
                         env({ estado: "erro", mensagem: "Limite diário de consultas à fonte atingido; volta amanhã.", dados: null }),
                         env({ estado: "nao_configurado", falta: ["CLARITY_API_TOKEN"], dados: null })]) {
      const { page, erros } = await abre((r) => r.fulfill(json(corpo)), viewport);
      for (const w of ["#b30", "#b7"]) {
        await page.click(w);
        const m = await page.evaluate(() => ({
          pagina: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          cartao: (() => { const c = document.getElementById("f-clarity"); return c.scrollWidth - c.clientWidth; })(),
          alvos: [...document.querySelectorAll("button, a.btn, .links a")].filter((e) => e.getBoundingClientRect().height < 40).length,
        }));
        assert.deepEqual([m.pagina, m.cartao, m.alvos], [0, 0, 0], `${corpo.estado} ${w}`);
      }
      assert.deepEqual(erros, []);
      await page.close();
    }
  });
}

test("contraste AA >= 4,5 em todo texto do cartão Clarity (ok, stale, erro, nao_configurado)", async () => {
  const corpos = [env(), env({ dados: { ...NULOS, truncado: true } }), env({ estado: "stale", mensagem: "A fonte demorou demais para responder." }),
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
      for (const el of document.querySelectorAll("#f-clarity *")) {
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


test("`pagina` e `janela_dias` do payload não são lidos: o cartão só usa texto fixo", async () => {
  const { page } = await abre((r) => r.fulfill(json(env({ dados: { ...DADOS, pagina: XSS, janela_dias: XSS } }))));
  const t = await texto(page);
  assert.ok(!t.includes("<img") && t.includes("/precos") && t.includes("últimos 3 dias (UTC)"));  // `pagina`/`janela_dias` não são lidos
  await page.close();
});
