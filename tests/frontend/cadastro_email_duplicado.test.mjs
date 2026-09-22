/**
 * Cadastro com e-mail que já tem conta: a tela mostra a mensagem do servidor
 * E um link direto pro /login — antes o /auth/register respondia 200 até com
 * e-mail duplicado (anti-enumeração) e o usuário caía na tela de código de 6
 * dígitos esperando um código que nunca chegava.
 *
 * Controle negativo: desfazer o ramo `res.status===409` do doRegister (em
 * frontend/cadastro.html) derruba o assert do link; trocar a mensagem no
 * mock derruba o assert do texto.
 *
 * Rodar:  node --test tests/frontend/cadastro_email_duplicado.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const DETALHE = "Esse e-mail já tem conta. Entre nela ou recupere a senha.";

for (const [rotulo, viewport] of [["desktop", { width: 1440, height: 900 }],
                                  ["mobile", { width: 390, height: 844 }]]) {
  test(`cadastro (${rotulo}): 409 mostra a mensagem e o link Entrar`, async () => {
    const page = await browser.newPage({ viewport });
    const erros = [];
    page.on("pageerror", (e) => erros.push(String(e)));
    await page.route("**/auth/register", (r) => r.fulfill({
      status: 409, contentType: "application/json",
      body: JSON.stringify({ detail: DETALHE }),
    }));
    await page.goto(ORIGIN + "/cadastro.html");
    await page.fill("#reg-name", "Ana");
    await page.fill("#reg-email", "a@x.com");
    await page.fill("#reg-phone", "11999999999");
    await page.fill("#reg-password", "12345678");
    await page.fill("#reg-confirm", "12345678");
    await page.check("#terms");
    await page.click("#btn-register");
    await page.waitForTimeout(400);

    const caixa = page.locator("#reg-error");
    assert.equal((await caixa.innerText()).includes(DETALHE), true,
      "a mensagem do servidor não apareceu");
    const link = caixa.locator("a");
    assert.equal(await link.innerText(), "Entrar");
    assert.ok((await link.getAttribute("href")).endsWith("/login"));
    assert.ok(await caixa.isVisible(), "a caixa de erro ficou invisível");
    // Não avançou pra tela de verificação.
    assert.ok(await page.locator("#form-register").isVisible());
    assert.ok(!(await page.locator("#form-verify").isVisible()));
    assert.deepEqual(erros, [], "a página estourou JS");
    await page.close();
  });
}
