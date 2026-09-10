/**
 * O 409 de quem JÁ pagou o período (issue #353).
 *
 * O `POST /billing/pix/checkout` devolve `409 {error: "pix_future_purchase_conflict",
 * covered_until}` para quem tenta comprar cobertura que já tem. Antes disso, o
 * cliente pagante lia "Não consegui gerar o código Pix agora." — a MESMA frase da
 * indisponibilidade real de 2026-09-10 (ASAAS_BASE_URL errada), com a data que
 * veio na resposta jogada fora.
 *
 * Os dois controles do CLAUDE.md §3:
 *   · negativo — apague as 3 linhas do `pago` em `pix-checkout.js:296` e o caso
 *     "conflito" fica VERMELHO (ele estava verde com o conserto);
 *   · positivo — o caso "500" prova que o genérico continua existindo. Sem ele,
 *     um código que desse mensagem específica para TODO erro passaria.
 *
 * Os dois últimos casos são a fronteira: sem `covered_until`, ou com ele em
 * formato que não é ISO, a tela NÃO pode escrever "undefined" nem uma data
 * inventada — volta para o genérico, que é o que o erro é.
 *
 * O que este arquivo NÃO alcança: o Asaas de verdade, o 409 do servidor real
 * (aqui ele é `page.route`) e o CPF inválido — ver o relato.
 *
 * Rodar:  node --test tests/frontend/precos_pix_ja_pago.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

// String improvável de aparecer por acaso: os casos procuram por ela no DOM
// depois da recusa (o CPF não pode sobreviver à troca de estado do modal).
const CPF = "11122233344";
const GENERICO = "Não consegui gerar o código Pix agora.";

/** Abre a /precos com o checkout mockado, digita o CPF e envia. */
async function tentarComprar({ httpStatus, corpo, viewport } = {}) {
  const page = await browser.newPage({
    viewport: viewport || { width: 1440, height: 900 },
  });
  await page.route("**/auth/me", (r) => r.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ user_id: 42, needs_plan_selection: true }),
  }));
  await page.route("**/billing/plans-config", (r) => r.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ essencial_available: true, plus_available: true,
                           pro_available: true, pix_annual_available: true }),
  }));
  await page.route("**/billing/subscription", (r) => r.fulfill({
    contentType: "application/json", body: JSON.stringify({ active: false }),
  }));
  await page.route("**/billing/pix/checkout", (r) => r.fulfill({
    status: httpStatus, contentType: "application/json",
    body: JSON.stringify({ detail: corpo }),
  }));

  await page.goto(`${ORIGIN}/precos.html`);
  await page.waitForSelector("#plans-v2 .plan");
  await page.waitForTimeout(600);        // loadPlansState = 2 awaits de rede
  await page.click("#cycle-annual");
  await page.click('[data-pix-cta="plus"]');
  await page.waitForSelector(".pix-doc");
  await page.fill(".pix-doc", CPF);
  await page.click('.pix-form button[type="submit"]');
  await page.waitForTimeout(300);
  return page;
}

const tela = (page) => page.evaluate(() => ({
  caixa: (document.querySelector(".pix-box") || {}).textContent || "",
  toast: document.getElementById("toast").textContent,
  toastVisivel: document.getElementById("toast").classList.contains("show"),
  campos: document.querySelectorAll(".pix-doc").length,
  corpo: document.body.innerText,
}));

const CONFLITO = { error: "pix_future_purchase_conflict", plan: "pro_max",
                   covered_until: "2028-07-06T00:00:00+00:00" };

test("conflito: a caixa nomeia a data já paga, e não o genérico", async () => {
  const page = await tentarComprar({ httpStatus: 409, corpo: CONFLITO });
  const t = await tela(page);
  assert.match(t.caixa, /06\/07\/2028/, "a data de covered_until não apareceu na tela");
  assert.match(t.caixa, /Esse ano já é seu/);
  assert.equal(t.toastVisivel, false, "o aviso genérico apareceu junto da caixa");
  assert.ok(!t.corpo.includes(GENERICO), "a tela ainda mostra a frase genérica");
  // PII: o formulário sai da tela junto com o documento digitado.
  assert.equal(t.campos, 0, "o campo do CPF continuou no DOM depois da recusa");
  assert.ok(!(await page.content()).includes(CPF), "o CPF sobreviveu no DOM");
  await page.close();
});

test("conflito em 390x844: a caixa cabe na tela do celular", async () => {
  const page = await tentarComprar({ httpStatus: 409, corpo: CONFLITO,
                                     viewport: { width: 390, height: 844 } });
  const cx = await page.$eval(".pix-box", (e) => {
    const r = e.getBoundingClientRect();
    return { left: r.left, right: r.right, largura: r.width };
  });
  assert.ok(cx.left >= 0 && cx.right <= 390, `a caixa vazou: ${JSON.stringify(cx)}`);
  assert.match(await page.$eval(".pix-box", (e) => e.textContent), /06\/07\/2028/);
  await page.close();
});

// Controle POSITIVO: sem este caso, um código que desse mensagem específica para
// qualquer erro passaria no caso de cima.
test("500 continua no genérico", async () => {
  const page = await tentarComprar({ httpStatus: 500, corpo: "erro interno" });
  const t = await tela(page);
  assert.equal(t.toast, GENERICO, "o 500 devia cair no aviso genérico");
  assert.equal(t.toastVisivel, true);
  assert.ok(!t.caixa.includes("Esse ano já é seu"), "o 500 abriu a caixa do conflito");
  await page.close();
});

test("conflito sem covered_until cai no genérico, sem 'undefined'", async () => {
  const page = await tentarComprar({
    httpStatus: 409, corpo: { error: "pix_future_purchase_conflict", plan: "pro_max" },
  });
  const t = await tela(page);
  assert.equal(t.toast, GENERICO);
  assert.ok(!t.corpo.includes("undefined"), "escreveu 'undefined' na tela");
  assert.ok(!t.caixa.includes("Esse ano já é seu"));
  await page.close();
});

test("conflito com covered_until malformado cai no genérico, sem 'undefined'", async () => {
  const page = await tentarComprar({
    httpStatus: 409,
    corpo: { error: "pix_future_purchase_conflict", covered_until: "em breve" },
  });
  const t = await tela(page);
  assert.equal(t.toast, GENERICO);
  assert.ok(!t.corpo.includes("undefined"), "escreveu 'undefined' na tela");
  assert.ok(!t.corpo.includes("em breve"), "jogou o valor cru do servidor na tela");
  await page.close();
});
