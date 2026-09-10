/**
 * O 409 de quem JÁ pagou o período (issue #353).
 *
 * O `POST /billing/pix/checkout` devolve `409 {error: "pix_future_purchase_conflict",
 * covered_until}` para quem tenta comprar cobertura que já tem. Antes disso, o
 * cliente pagante lia "Não consegui gerar o código Pix agora." — a MESMA frase da
 * indisponibilidade real de 2026-09-10 (ASAAS_BASE_URL errada), com a data que
 * veio na resposta jogada fora.
 *
 * E a causa RAIZ, que o primeiro conserto deixou aberta: `det.message` num
 * `detail` que é STRING dá `undefined`, então toda frase que o servidor escreve
 * em `HTTPException(detail="…")` era descartada — os dois 400, o 429, o 503 e o
 * 403 do CSRF liam o genérico. A tabela `FRASES_DO_SERVIDOR` é esses cinco.
 *
 * Os dois controles do CLAUDE.md §3:
 *   · negativo — (a) apague as 3 linhas do `pago` no `pixEnviar` e o caso
 *     "conflito" fica VERMELHO; (b) devolva o `det` para `(d && d.detail) || {}`
 *     e os 5 casos de `FRASES_DO_SERVIDOR` ficam vermelhos. Os dois eram verdes;
 *   · positivo — o caso "500" prova que o genérico continua existindo. Sem ele,
 *     um código que desse mensagem específica para TODO erro passaria.
 *
 * Os dois últimos casos são a fronteira: sem `covered_until`, ou com ele em
 * formato que não é ISO, a tela NÃO pode escrever "undefined" nem jogar o valor
 * cru na tela — volta para o genérico, que é o que o erro é. A guarda é de
 * FORMA e só: "2028-13-45" passa por ela e vira "45/13/2028" (medido), o que não
 * é alcançável — quem manda o campo é um timestamptz do banco.
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
async function tentarComprar({ httpStatus, corpo, corpoBruto, viewport } = {}) {
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
    body: corpoBruto || JSON.stringify({ detail: corpo }),
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
  // PII medida de VERDADE. `page.content()` serializa ATRIBUTOS e o `page.fill`
  // escreve a PROPRIEDADE `value`: procurar o CPF no HTML devolve "não achei"
  // com o campo cheio dele na tela (medido nas duas colunas). Quem mede é ler o
  // `.value` vivo. NÃO copie `page.content()` para os outros arquivos de Pix.
  valores: [...document.querySelectorAll("input")].map((i) => i.value).join(" "),
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
  assert.ok(!t.valores.includes(CPF), "o CPF sobreviveu no value de um <input>");
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
//
// O corpo é o do 500 REAL desta app, byte a byte: o handler de exceção não
// tratada responde `{"error": "Erro interno do servidor."}`
// (finance_bot_websocket_custom.py:2415) — sem `detail` nenhum. Vai como
// `corpoBruto` de propósito: `JSON.stringify({detail: undefined})` dá `{}`, que
// é mais POBRE que o real — no dia em que alguém somar `|| d.error` como fonte
// de mensagem (a app tem DOIS formatos de erro em circulação), o `{}` continua
// verde com a tela mostrando "Erro interno do servidor." ao cliente. Hoje os
// dois dão o mesmo porque ninguém lê `d.error` no topo: o corpo real mede o
// requisito, o `{}` media o código de hoje.
test("500 continua no genérico", async () => {
  const page = await tentarComprar({
    httpStatus: 500,
    corpoBruto: JSON.stringify({ error: "Erro interno do servidor." }),
  });
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

// ── A causa raiz: `detail` STRING ───────────────────────────────────────────
//
// Cada frase é COPIADA do servidor, com o arquivo:linha que a escreve — se
// alguém reescrever a frase lá, este arquivo não fica vermelho (o §0.7 não
// alcança copy de erro). O que ele prende é a CATEGORIA: `detail` string chega
// à tela em vez de virar o genérico.
const FRASES_DO_SERVIDOR = [
  [400, "plan inválido (use 'essencial', 'plus' ou 'pro').", "billing_pix.py:88"],
  [400, "Informe um CPF ou CNPJ válido.", "billing_pix.py:93"],
  [429, "Muitas tentativas. Aguarde alguns minutos e tente novamente.",
    "finance_bot_websocket_custom.py:2285 — o limitador é por IP (shared.py:98)"],
  [503, "Não consegui emitir o Pix agora. Tenta de novo em instantes.",
    "billing_pix.py:137"],
  [403, "Token CSRF inválido ou ausente.",
    "finance_bot_websocket_custom.py:2247 — o checkout não tem isenção de CSRF"],
];

for (const [status, frase, onde] of FRASES_DO_SERVIDOR) {
  test(`${status}: a frase do servidor chega à tela (${onde})`, async () => {
    const page = await tentarComprar({ httpStatus: status, corpo: frase });
    const t = await tela(page);
    assert.equal(t.toast, frase, "a frase do servidor foi descartada");
    assert.equal(t.toastVisivel, true);
    assert.ok(!t.corpo.includes(GENERICO), "mostrou o genérico junto da frase");
    await page.close();
  });
}
