/**
 * /assinar com os 7 dias grátis (entrada pelos CTAs da /): o texto do S1 e o S4 de quem
 * não tem período grátis (trial_days=0). A máquina de estados fica em assinar_estados.
 *
 * Controles (CLAUDE.md §3), medidos em 2026-11-02: tirar o `s4-titulo.hidden` do assinar.js
 * deixa os dois "trial_days=0" vermelhos; tirar a frase do e-mail de dentro da .field deixa
 * "S1" vermelho; tirar o max-width/font-size das etapas no assinar.css deixa "S1 em 1366×768"
 * vermelho; voltar a aplicá-los só ao #s1 deixa "S1 → S2" vermelho.
 * Positivo: "trial_days=7/15" (o título de "sem período grátis" fica escondido).
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, continuar, EMBUTIDO, ME, FRAG } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const CONTA = "POST /auth/quiz/conta";
const CHECKOUT = "/billing/create-checkout";
const texto = (page, id) => page.locator(`#${id}`).textContent();

test("S1: subtítulo, observação do telefone, frase do e-mail dentro do campo e 'Trocar de plano' → /?site=1#planos", async () => {
  const { ctx, page } = await abrir(browser);
  await tela(page, "s1");
  const s1 = await page.$eval("#s1", (sec) => ({
    sub: sec.querySelector(".assinar-sub").textContent,
    obs: sec.querySelector("form > p.hint").textContent,
    email: sec.querySelector("#email").closest(".field").querySelector(".hint")?.textContent,
    trocar: [...sec.querySelectorAll("a")].filter((a) => a.textContent === "Trocar de plano").map((a) => {
      const cs = getComputedStyle(a), dono = getComputedStyle(a.parentElement);
      // visível como link: cor própria (não a do texto da observação) e sublinhado
      // ?site=1: na PWA instalada a / manda ao /login sem ele (guarda no topo da index.html)
      return [a.getAttribute("href"), a.parentElement.matches("form > p.hint"), cs.color !== dono.color, cs.textDecorationLine,
        a.getBoundingClientRect().height >= 24];  // alvo de toque
    }),
    texto: sec.innerText,
  }));
  assert.equal(s1.sub, "Hoje você não paga nada. O cartão entra no próximo passo e a primeira cobrança só vem depois dos 7 dias. Cancele antes pelo painel e não paga nada.");
  assert.equal(s1.obs, "Teste grátis disponível uma vez por número de telefone · Trocar de plano");
  assert.equal(s1.email, "É com este e-mail que você vai entrar no PigBank.");
  assert.deepEqual(s1.trocar, [["/?site=1#planos", true, true, "underline", true]]);
  assert.ok(!s1.texto.includes("Confira seus dados"), "a frase antiga ficou");
  await ctx.close();
});

test("entrada pela URL da / (Plus mensal com UTM): o cadastro mantém Plus e mensal, e a URL guarda utm e fbclid", async () => {
  const query = "?plano=plus&ciclo=monthly&utm_source=fb&fbclid=abc";
  const { ctx, page, posts } = await abrir(browser, { query, hash: FRAG, api: { [CONTA]: [200, { estado: "criada", user_id: 42 }] } });
  await tela(page, "s1");
  assert.equal(await texto(page, "s1-titulo"), "Seus 7 dias grátis do Plus começam aqui");
  await continuar(page);
  await tela(page, "s4");
  const [checkout] = posts(CHECKOUT);
  assert.deepEqual([checkout.body.plan, checkout.body.interval], ["plus", "monthly"]);
  const url = new URL(page.url());
  assert.deepEqual([url.searchParams.get("utm_source"), url.searchParams.get("fbclid")], ["fb", "abc"]);
  await ctx.close();
});

// 15 e não só 7: o número do S4 vem do `trial_days` do backend, não da copy fixa do S1.
for (const td of [7, 15]) {
  test(`trial_days=${td}: sem o título de 'sem período grátis', e o texto com os ${td} dias do backend`, async () => {
    const { ctx, page } = await abrir(browser, { api: { "GET /auth/me": ME("ana@x.com"),
      [`POST ${CHECKOUT}`]: [200, { ...EMBUTIDO[1], trial_days: td }] } });
    await tela(page, "s4");
    assert.equal(await texto(page, "s4-trial"), `Você tem ${td} dias grátis. A cobrança do plano só começa depois, e dá para cancelar antes.`);
    assert.equal(await page.locator("#s4-titulo").isVisible(), false);
    await ctx.close();
  });
}

// O "Continuar" do S1 na 1ª dobra, com 12px de folga, nos notebooks comuns e no celular. O
// assinar_plano_b mede 1280×800 e 390×844 sem folga; aqui entram 1366×768 e 1440×789, e os 3 planos
// (o "Essencial" é o título mais longo).
for (const [w, h] of [[1366, 768], [1440, 789], [1280, 800], [390, 844]]) {
  test(`S1 em ${w}×${h}: o Continuar fica na 1ª dobra com 12px de folga nos 3 planos`, async () => {
    for (const plano of ["essencial", "plus", "pro"]) {
      const { ctx, page } = await abrir(browser, { hash: FRAG, query: `?plano=${plano}&ciclo=monthly`, viewport: { width: w, height: h } });
      await tela(page, "s1");
      await page.evaluate(() => document.fonts.ready);
      const m = await page.evaluate(() => ({ fundo: document.getElementById("s1-continuar").getBoundingClientRect().bottom,
        vaza: document.documentElement.scrollWidth > document.documentElement.clientWidth }));
      await ctx.close();
      assert.ok(m.fundo <= h - 12, `${plano}: Continuar termina em ${Math.round(m.fundo)} (dobra ${h})`);
      assert.equal(m.vaza, false, `${plano}: rolagem lateral`);
    }
  });
}

for (const caminho of ["logado (ME)", "cadastro novo (S1)"]) {
  test(`trial_days=0 pelo ${caminho}: título e texto de sem período grátis, e nada promete dias grátis`, async () => {
    const checkout = { [`POST ${CHECKOUT}`]: [200, { ...EMBUTIDO[1], trial_days: 0 }] };
    const logado = caminho.startsWith("logado");
    const { ctx, page } = await abrir(browser, logado
      ? { api: { "GET /auth/me": ME("ana@x.com"), ...checkout } }
      : { hash: FRAG, api: { [CONTA]: [200, { estado: "criada", user_id: 42 }], ...checkout } });
    if (!logado) { await tela(page, "s1"); await continuar(page); }
    await tela(page, "s4");
    assert.equal(await texto(page, "s4-titulo"), "Esta assinatura não tem período grátis");
    assert.ok(await page.locator("#s4-titulo").isVisible());
    assert.equal(await texto(page, "s4-trial"), "O teste grátis é um por número de telefone. A cobrança começa assim que você confirmar a assinatura.");
    const corpo = await page.evaluate(() => document.body.innerText);
    assert.doesNotMatch(corpo, /\d+ dias grátis/);
    assert.ok(!corpo.includes("Hoje você não paga nada"), "o subtítulo do S1 vazou para o S4");
    await ctx.close();
  });
}

// Uma página só: título e cartão não podem saltar do S1 (30px, 640px no desktop) para o S2.
for (const [w, h] of [[1366, 768], [390, 844]]) {
  test(`S1 → S2 em ${w}×${h}: o título e a largura do cartão são os mesmos nas duas etapas`, async () => {
    const { ctx, page } = await abrir(browser, { hash: FRAG, viewport: { width: w, height: h },
      api: { [CONTA]: [200, { estado: "tem_conta" }] } });
    const medir = (id) => page.evaluate((id) => ({ h1: getComputedStyle(document.querySelector(`#${id} h1`)).fontSize,
      cartao: Math.round(document.querySelector(".auth-card").getBoundingClientRect().width) }), id);
    await tela(page, "s1");
    await page.evaluate(() => document.fonts.ready);
    const s1 = await medir("s1");
    await continuar(page);
    await tela(page, "s2");
    const s2 = await medir("s2");
    await ctx.close();
    assert.deepEqual(s2, s1);
  });
}
