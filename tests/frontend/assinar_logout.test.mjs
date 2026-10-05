/**
 * /assinar, S2 com a sessão de outra conta (D7): se o POST /auth/logout falha, os
 * botões NÃO navegam. O /login veria o cookie velho e devolveria a pessoa logada
 * como a conta antiga, e o S3 a cobraria. A pessoa fica no S2 com o erro, e a trava
 * sai: o 2º clique (logout agora 200) segue. Os positivos (logout 200, sem sessão)
 * estão em assinar_estados.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrir, tela, continuar, ME, ORIGIN } from "./_assinar.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const ERRO = "Não deu para sair da conta atual. Tente de novo.";
const FALHAS = [["500", [500, { detail: "x" }]], ["403 (CSRF)", [403, { detail: "Token CSRF inválido ou ausente." }]],
                ["falha de rede", "aborta"]];

for (const [nome, falha] of FALHAS) {
  for (const [botao, destino] of [["#s2-senha", /\/login\?next=/], ["#s2-google", /\/auth\/google\/start\?next=/]]) {
    test(`${botao} com logout ${nome}: fica no S2 com o erro; o 2º clique (logout 200) navega`, async () => {
      let n = 0;
      const { ctx, page, reqs, posts } = await abrir(browser, { hash: "#e=b%40x.com&w=11987654321", api: {
        "GET /auth/me": ME("a@x.com"),
        "POST /auth/quiz/conta": [200, { estado: "tem_conta" }],
        "POST /auth/logout": () => (n++ === 0 ? falha : [200, { ok: true }]),
      } });
      await tela(page, "s1");
      await continuar(page);
      await tela(page, "s2");
      await page.click(botao);
      // O erro aparece, ou a página sai (o defeito): o waitForFunction sobrevive à navegação.
      await page.waitForFunction(() => !location.pathname.endsWith("/assinar.html")
        || document.getElementById("s2-erro").textContent !== "");
      assert.equal(reqs.filter((r) => r.navegacao && !r.path.endsWith("/assinar.html")).length, 0,
        "navegou com a sessão da conta velha viva");
      assert.equal(await page.locator("#s2-erro").textContent(), ERRO);
      assert.equal(posts("/auth/logout").length, 1);
      assert.ok(new URL(page.url()).pathname.endsWith("/assinar.html"));
      assert.equal(await page.evaluate(() => sessionStorage.getItem("pb_purchase_intent_v1")), null,
        "gravou a intenção sem o logout ter dado certo");
      assert.equal(await page.locator("#s2").isVisible(), true);

      await page.click(botao);  // a trava foi solta
      await page.waitForURL(destino);
      assert.equal(posts("/auth/logout").length, 2);
      if (botao === "#s2-google") assert.equal(page.url(), `${ORIGIN}/auth/google/start?next=%2Fcontinuar-compra`);
      await ctx.close();
    });
  }
}

// O "Sair" do S3/S4 (#sair), mesma classe: com o logout falho a conta velha segue logada, e o reload a
// cobraria em silêncio. A pessoa fica no S3 DELA com o erro e o Sair, e o checkout não reabre sozinho.
for (const [nome, falha] of FALHAS) {
  test(`#sair com logout ${nome}: S3 da conta velha com o erro, sem reabrir o checkout; o 2º clique (200) vai ao S1`, async () => {
    let n = 0;
    const { ctx, page, posts } = await abrir(browser, { api: {
      "GET /auth/me": ME("a@x.com"),
      "POST /auth/logout": () => (n++ === 0 ? falha : [200, { ok: true }]),
    } });
    await page.locator("#stripe-checkout iframe").waitFor();
    await page.click("#sair");
    await page.waitForFunction((t) => !document.getElementById("s1").hidden
      || document.getElementById("s3-titulo").textContent === t, ERRO);
    assert.equal(await page.locator("#s1").isVisible(), false, "foi ao S1 com a sessão da conta velha viva");
    assert.equal(await page.locator("#s3").isVisible(), true);
    assert.equal(await page.locator("#s3-titulo").textContent(), ERRO);  // o título é o problema, não "abrir o pagamento"
    assert.equal(await page.locator("#conta-email").textContent(), "a•••@x.com");
    assert.equal(await page.locator("#sair").isVisible(), true);
    assert.equal(await page.locator("#s3-retry").isVisible(), false);
    assert.equal(posts("/billing/create-checkout").length, 1, "reabriu o checkout da conta velha");
    assert.equal(posts("/auth/logout").length, 1);

    await page.click("#sair");  // a trava foi solta
    await tela(page, "s1");
    assert.equal(posts("/auth/logout").length, 2);
    await ctx.close();
  });
}

test("#sair com logout falho e reload: volta ao S4 da conta velha (a sessão segue viva), sem o aviso", async () => {
  const { ctx, page } = await abrir(browser, { api: { "GET /auth/me": ME("a@x.com"), "POST /auth/logout": [500, {}] } });
  await page.locator("#stripe-checkout iframe").waitFor();
  await page.click("#sair");
  await page.waitForFunction((t) => document.getElementById("s3-titulo").textContent === t, ERRO);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#stripe-checkout iframe").waitFor();
  assert.equal(await page.locator("#conta-email").textContent(), "a•••@x.com");
  assert.notEqual(await page.locator("#s3-titulo").textContent(), ERRO);  // o aviso não sobrevive ao reload
  await ctx.close();
});

// Os gatilhos tardios do plano B (o onerror do Stripe.js, o relógio de 10 s) depois do Sair falho: a página está no
// S3 da conta velha com o aviso, e nenhum deles pode abrir o hospedado dela sem clique (a guarda de geração).
test("#sair falho com o Stripe.js pendente: o onerror tardio não abre o hospedado da conta velha", async () => {
  const { ctx, page, posts, rotaStripe } = await abrir(browser, { stripe: "segura",
    api: { "GET /auth/me": ME("a@x.com"), "POST /auth/logout": [500, {}] } });
  const rota = await rotaStripe;
  await tela(page, "s4");
  await page.click("#sair");
  await page.waitForFunction((t) => document.getElementById("s3-titulo").textContent === t, ERRO);
  await page.evaluate(() => {
    const s = document.querySelector('script[src*="js.stripe.com"]');
    window.__erro = new Promise((ok) => s.addEventListener("error", () => setTimeout(ok, 50)));
  });
  await rota.abort();
  await page.evaluate(() => window.__erro);  // o onerror rodou, e o .catch atrás dele também
  assert.equal(await page.locator("#s3").isVisible(), true, "saiu do S3 da conta velha");
  assert.equal(posts("/billing/create-checkout").length, 1, "abriu o hospedado da conta velha");
  assert.ok(new URL(page.url()).pathname.endsWith("/assinar.html"));
  await ctx.close();
});

test("#sair falho com o Stripe.js pendurado: o relógio de 10 s não abre o hospedado da conta velha", async () => {
  const { ctx, page, posts } = await abrir(browser, { stripe: "pendura", relogio: true,
    api: { "GET /auth/me": ME("a@x.com"), "POST /auth/logout": [500, {}] } });
  await tela(page, "s4");
  await page.click("#sair");
  await page.waitForFunction((t) => document.getElementById("s3-titulo").textContent === t, ERRO);
  await page.clock.runFor(11000);  // o irParaHospedado mostra o H síncrono: o #s3 visível já discrimina
  assert.equal(await page.locator("#s3").isVisible(), true, "saiu do S3 da conta velha");
  assert.equal(posts("/billing/create-checkout").length, 1, "abriu o hospedado da conta velha");
  await ctx.close();
});
