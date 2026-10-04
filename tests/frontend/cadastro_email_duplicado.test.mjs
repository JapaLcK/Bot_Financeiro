/**
 * Cadastro com e-mail que já tem conta: a tela mostra a mensagem do servidor
 * E um link direto pro /login — antes o /auth/register respondia 200 até com
 * e-mail duplicado (anti-enumeração) e o usuário caía na tela de código de 6
 * dígitos esperando um código que nunca chegava.
 *
 * O "Reenviar código" chama o mesmo /auth/register: com 409 (ou qualquer
 * erro) ele mostra o detail em vez de "Novo código enviado!".
 *
 * Controle negativo: desfazer o ramo `res.status===409` do doRegister (em
 * frontend/cadastro.html) derruba o assert do link; voltar o resendCode a
 * ignorar a resposta derruba o caso do reenvio. Positivo do reenvio:
 * auth_pages_redesign.test.mjs (reenvio 200 segue "ok").
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

// O texto de db_support.EMAIL_JA_TEM_CONTA (o detail real do 409).
const DETALHE = 'Este e-mail já tem conta. Entre com sua senha ou use "Esqueci a senha".';

async function preencheECria(page) {
  await page.goto(ORIGIN + "/cadastro.html");
  await page.fill("#reg-name", "Ana");
  await page.fill("#reg-email", "a@x.com");
  await page.fill("#reg-phone", "11999999999");
  await page.fill("#reg-password", "12345678");
  await page.fill("#reg-confirm", "12345678");
  await page.check("#terms");
  await page.click("#btn-register");
}

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
    await preencheECria(page);

    const caixa = page.locator("#reg-error");
    await caixa.locator("a").waitFor();
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

test("reenviar código com 409 mostra o detail, não \"Novo código enviado!\"", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));
  const respostas = [
    { status: 200, body: { status: "verification_sent", email: "a@x.com" } },
    { status: 409, body: { detail: DETALHE } },
  ];
  await page.route("**/auth/register", (r) => {
    const { status, body } = respostas.shift();
    return r.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
  await preencheECria(page);
  await page.locator("#form-verify").waitFor({ state: "visible" });
  await page.getByText("Reenviar código", { exact: true }).click();

  const caixa = page.locator("#verify-error.show");
  await caixa.waitFor();
  assert.equal(await caixa.innerText(), DETALHE);
  assert.equal(await caixa.evaluate((e) => e.classList.contains("ok")), false);
  assert.deepEqual(respostas, [], "o reenvio não chamou o /auth/register");
  assert.deepEqual(erros, [], "a página estourou JS");
  await page.close();
});
