/**
 * /assinar: nenhuma URL, Referer ou log de console leva a PII do fragmento.
 *
 * O servidor de teste não injeta rastreio, então o harness põe um Pixel e um GA4
 * FALSOS antes do primeiro </head>, na mesma regra do `inject_tracking`
 * (frontend/routes/shared.py). Eles fazem o que os de verdade fazem: leem
 * `location.href` e `document.referrer` na hora em que rodam e mandam num GET. A
 * ordem real (assinar.js antes da injeção) é provada pelo tests/test_assinar_pagina.py.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, continuar } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const RASTREIO_FALSO = `<script>
(function () {
  function envia(base, ev) {
    new Image().src = base + "?ev=" + ev + "&dl=" + encodeURIComponent(location.href)
      + "&rl=" + encodeURIComponent(document.referrer);
  }
  envia("https://www.facebook.com/tr", "PageView");
  envia("https://www.google-analytics.com/g/collect", "page_view");
  var registra = window.fbq;
  window.fbq = function (_, ev) { envia("https://www.facebook.com/tr", ev); return registra.apply(this, arguments); };
})();
</script>
`;
const noFim = (t) => t.replace("</head>", RASTREIO_FALSO + "</head>");
const noComeco = (t) => t.replace("<head>", "<head>" + RASTREIO_FALSO);

const QUERY = "?plano=plus&ciclo=monthly&utm_source=ig";
const HASH = "#n=Ana%20%26%20Cia&e=a%2Bb%40x.com&w=11987654321&p=dividas&r=acdbd";
const PII = ["a+b@x.com", "a%2Bb", "987654321", "Ana", "dividas", "acdbd"];

/** O que vazou: cada PII achada em URL (crua e decodificada), Referer ou console. */
function vazamentos(reqs, console_, pii) {
  const achados = [];
  const olha = (onde, s) => {
    // Decodifica até 3 vezes: a URL dentro do `dl=` chega codificada duas.
    const formas = [s];
    for (let i = 0; i < 3; i++) {
      try { formas.push(decodeURIComponent(formas.at(-1).replace(/\+/g, " "))); } catch { break; }
    }
    const dec = formas.at(-1);
    for (const p of pii) if (formas.some((f) => f.includes(p))) achados.push(`${onde}: ${p} em ${s}`);
    if (/[?&#]p=|[?&#]r=/.test(dec)) achados.push(`${onde}: p/r em ${s}`);
  };
  // A 1ª é a própria navegação: a URL que a pessoa abriu (o fragmento nunca vai no request).
  for (const r of reqs.slice(1)) { olha("url", r.url); olha("referer", r.referer); }
  for (const m of console_) olha("console", m);
  return achados;
}

/** S1 → criada → S3 → S4 com o rastreio falso; devolve tudo o que saiu. */
async function fluxo(html, query = QUERY, hash = HASH, campos = {}) {
  const r = await abrir(browser, { query, hash, html, api: {
    "POST /auth/quiz/conta": [200, { estado: "criada", user_id: 9 }] } });
  const console_ = [];
  r.page.on("console", (m) => console_.push(m.text()));
  await tela(r.page, "s1");
  await continuar(r.page, campos);
  await r.page.locator("#stripe-checkout iframe").waitFor();
  await r.page.waitForTimeout(100);
  r.final = r.page.url();
  r.console_ = console_;
  return r;
}

test("T-P1: com o Pixel e o GA4 antes do </head>, nenhum request, Referer ou log leva a PII", async () => {
  const r = await fluxo(noFim);
  const rastreio = r.reqs.filter((x) => /facebook\.com\/tr|google-analytics\.com/.test(x.url));
  // Guarda: o rastreio falso rodou (PageView + page_view + CompleteRegistration + InitiateCheckout).
  assert.ok(rastreio.length >= 4, `só ${rastreio.length} requests de rastreio`);
  assert.deepEqual(vazamentos(r.reqs, r.console_, PII), []);
  assert.deepEqual(vazamentos([{}, { url: r.final, referer: "" }], [], PII), []);
  assert.equal(new URL(r.final).search, QUERY);
  // Positivo: a página LEU o fragmento (senão o verde acima seria de graça).
  assert.deepEqual(r.posts("/auth/quiz/conta")[0].body,
    { nome: "Ana & Cia", email: "a+b@x.com", whatsapp: "11987654321", aceitou_termos: true });
  await r.ctx.close();
});

test("controle do instrumento: com o rastreio ANTES do assinar.js, o mesmo teste vê a PII", async () => {
  const r = await fluxo(noComeco);
  const achados = vazamentos(r.reqs, r.console_, PII);
  assert.ok(achados.some((a) => a.includes("a+b@x.com") && a.includes("facebook.com/tr")), achados.join("\n"));
  await r.ctx.close();
});

test("T-P3: link direto com e-mail na query e p/r no fragmento: sai da URL e de todo request", async () => {
  const r = await fluxo(noFim, "?plano=plus&ciclo=monthly&e=x@y.com", "#p=dividas&r=acdbd",
    { email: "x@y.com", whatsapp: "11987654321" });
  assert.equal(new URL(r.final).search, "?plano=plus&ciclo=monthly");
  assert.deepEqual(vazamentos(r.reqs, r.console_, ["x@y.com", "x%40y", "dividas", "acdbd"]), []);
  await r.ctx.close();
});
