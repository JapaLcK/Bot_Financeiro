/**
 * O modal de boas-vindas da /home depois do Pix ANUAL.
 *
 * O que estava errado: quem pagava o Pix anual lia "Sua assinatura já está
 * ativa. Dá pra cancelar quando quiser, direto nas configurações" — e não tem
 * assinatura nenhuma (compra à vista, sem cartão, sem renovação). Quem comprava
 * Essencial ou Pro ainda lia o título do plano certo, mas o corpo era o da
 * Stripe.
 *
 * Os dois controles do CLAUDE.md §3, MEDIDOS (rodados, não deduzidos):
 *   · negativo — troque no `openWelcomePro` o ramo do Pix pelo `else` da
 *     Stripe e ficam vermelhos, por nome: WP1, WP2, WP10 e os SETE casos do
 *     WP4 (10 de 22). **WP5 e WP9 continuam VERDES** — eles só medem
 *     `searchParams.delete`, que a mutação não toca, e uma versão anterior
 *     deste cabeçalho os listava como cobertura que eles nunca deram;
 *   · positivo — WP3 é o caminho da Stripe SEM `gw`, provando que a cópia de
 *     quem assina no cartão continua exatamente a de hoje.
 *
 * WP4 e WP8 são a fronteira de confiança: `inicio` e `vl` vêm da URL, que
 * qualquer um digita. Os controles negativos de cada conserto estão no
 * comentário do próprio caso.
 *
 * O que este arquivo NÃO alcança: a compra de verdade e o e-mail. O e-mail é
 * `tests/test_pix_paid_email_copy.py`; a compra, só no aparelho.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Abre a /home com o backend inteiro em `{}` e o modal já aberto.
 * Devolve `{ page, erros }` — `erros` é a lista de `pageerror`, porque o WP4
 * mede JS quebrado por entrada suja, não só o texto que sobrou na tela.
 */
async function abrirHome(query) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));
  // O pixel da Meta vem de CDN, que este arquivo bloqueia — sem stub o
  // `window.fbq` é undefined e o disparo nem acontece (`window.fbq && sid`).
  // Empilha os argumentos crus: o teste mede o objeto que a página MANDOU.
  await page.addInitScript(() => {
    window.__fbq = [];
    window.fbq = (...args) => window.__fbq.push(args);
  });
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();          // CDN bloqueado
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  // `plan` NÃO pode ser `{}` aqui: com `?upgrade=success` a home levanta o
  // overlay de "confirmando pagamento" e só o baixa quando o `/auth/me` traz
  // plano pago (_checkoutSettled) — ou quando o fail-open de 20 s vence. Sem
  // este corpo o modal só abriria depois dos 20 s, e todo caso deste arquivo
  // (inclusive o controle positivo da Stripe) morria no timeout.
  await page.route("**/auth/me", (route) => route.fulfill(
    json({ user_id: 1, plan: "pro", plan_expires_at: null })));
  await page.goto(`${ORIGIN}/home.html${query}`);
  // O modal sobe 450 ms depois do load, e só se a validação de sessão passou.
  await page.waitForSelector("#welcome-pro-overlay.open", { timeout: 15000 });
  return { page, erros };
}

const textos = (page) => page.evaluate(() => ({
  olho: document.getElementById("wp-eyebrow-txt").textContent.trim(),
  titulo: document.getElementById("wp-title").textContent,
  sub: document.getElementById("wp-sub").textContent,
  subHtml: document.getElementById("wp-sub").innerHTML,
  chipEscondido: document.getElementById("wp-chip").hidden,
}));

const BASE = "?upgrade=success&sid=tok_x&ev=purchase&td=0";

// ── WP1: Pix AGENDADO ───────────────────────────────────────────────────────
test("WP1: Pix agendado diz o plano, a data e que não renova", async () => {
  const { page } = await abrirHome(`${BASE}&pl=essencial&gw=pix&inicio=2027-08-26`);
  const t = await textos(page);
  assert.match(t.titulo, /PigBank Essencial/, `título: ${t.titulo}`);
  assert.match(t.sub, /26\/08\/2027/, `sub sem a data: ${t.sub}`);
  assert.match(t.sub, /sem renovação/, `sub: ${t.sub}`);
  assert.doesNotMatch(t.sub, /cancelar/i, `prometeu cancelamento no Pix: ${t.sub}`);
  assert.doesNotMatch(t.sub, /assinatura/i, `chamou compra à vista de assinatura: ${t.sub}`);
  assert.equal(t.chipEscondido, true, "o chip de dias grátis apareceu numa compra");
  // O olho do card é estático no HTML e dizia "Assinatura confirmada" — a mesma
  // mentira do sub, num elemento que ninguém tinha olhado.
  assert.equal(t.olho, "Pagamento confirmado", `olho: ${t.olho}`);
  await page.close();
});

// ── WP2: Pix IMEDIATO ───────────────────────────────────────────────────────
test("WP2: Pix imediato diz que já começou, sem data e sem cancelamento", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix`);
  const t = await textos(page);
  assert.match(t.titulo, /PigBank Pro/, `título: ${t.titulo}`);
  assert.match(t.sub, /já começou/, `sub: ${t.sub}`);
  assert.doesNotMatch(t.sub, /cancelar/i, `prometeu cancelamento no Pix: ${t.sub}`);
  assert.doesNotMatch(t.sub, /\d{2}\/\d{2}\/\d{4}/, `inventou data no imediato: ${t.sub}`);
  await page.close();
});

// ── WP3: CONTROLE POSITIVO — a Stripe não mudou ─────────────────────────────
test("WP3: compra no cartão (sem gw) mantém a cópia de hoje", async () => {
  const { page } = await abrirHome(`${BASE}&pl=plus`);
  const t = await textos(page);
  assert.equal(t.titulo, "Tá dentro do PigBank+");
  assert.equal(t.sub,
    "Sua assinatura já está ativa. Dá pra cancelar quando quiser, direto nas configurações.");
  assert.equal(t.olho, "Assinatura confirmada", `o olho da Stripe mudou: ${t.olho}`);
  await page.close();
});

// ── WP4: `inicio` é entrada de fronteira ────────────────────────────────────
// "2027-02-30" e "2027-11-31" TÊM o formato e passam no `Date.parse` (que
// normaliza em vez de recusar) — eram os que chegavam à tela como "30/02/2027".
// "0000-01-01" também morre na ida e volta, e não na faixa de ano: o `Date` do
// JS mapeia ano 0 para 1900, então o `getFullYear()` volta diferente. O ÚNICO
// que depende da faixa é "9999-12-31" — ele passa na ida e volta (medido), e
// sem a janela fixa viraria "começa em 31/12/9999" na tela.
for (const sujo of ["<img src=x onerror=alert(1)>", "2027-13-99", "amanhã",
                    "2027-02-30", "2027-11-31", "0000-01-01", "9999-12-31"]) {
  test(`WP4: inicio=${sujo} cai no texto imediato, sem markup e sem erro`, async () => {
    const { page, erros } = await abrirHome(
      `${BASE}&pl=plus&gw=pix&inicio=${encodeURIComponent(sujo)}`);
    const t = await textos(page);
    assert.match(t.sub, /já começou/, `data inválida virou texto de agendado: ${t.sub}`);
    assert.ok(!t.subHtml.includes("<"), `entrou markup no sub: ${t.subHtml}`);
    assert.ok(!t.sub.includes(sujo), `a entrada crua foi para a tela: ${t.sub}`);
    assert.deepEqual(erros, [], `pageerror com inicio sujo: ${erros.join(" | ")}`);
    await page.close();
  });
}

/* ── WP4b: renovação empilhada (3+ anos à frente) é data VÁLIDA ──────────────
 * `plano_da_cobranca` empilha renovação do mesmo plano sem teto: a 4ª compra
 * Pix seguida começa no ano corrente + 3. A faixa antiga (`anoAtual`..`+2`)
 * devolvia null nela, e o card dizia "já começou" para quem só começa em 2029.
 *
 * Controle negativo (MEDIDO): reponha a faixa relativa no `wpInicioValido`
 * (`const anoAtual = new Date().getFullYear();` + `a >= anoAtual && a <=
 * anoAtual + 2`) e SÓ este caso fica vermelho — os sete do WP4, o WP1 e o WP2
 * continuam verdes, que é o que o separa de teatro.
 */
test("WP4b: inicio 3+ anos à frente sai como agendado, com a data", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix&inicio=2029-09-09`);
  const t = await textos(page);
  assert.match(t.sub, /09\/09\/2029/, `renovação empilhada virou imediato: ${t.sub}`);
  assert.doesNotMatch(t.sub, /já começou/, `sub: ${t.sub}`);
  await page.close();
});

// ── WP5: a URL não guarda os parâmetros novos ───────────────────────────────
test("WP5: gw e inicio somem da URL depois do load", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix&inicio=2027-08-26`);
  const busca = await page.evaluate(() => location.search);
  assert.ok(!busca.includes("gw="), `sobrou gw na URL: ${busca}`);
  assert.ok(!busca.includes("inicio="), `sobrou inicio na URL: ${busca}`);
  await page.close();
});

/* ── Purchase do pixel: o valor ──────────────────────────────────────────────
 * O `Purchase` do navegador ia sem `value` — só a CAPI (pix_drain_effects.py)
 * levava receita. Enquanto os dois chegam, deduplicam e ninguém perde nada; no
 * dia em que o do servidor falhar, a venda entra com R$ 0.
 *
 * Controles:
 *   · negativo — tire o `value` do objeto do fbq no home.html e WP6 fica
 *     vermelho (WP7, o positivo, continua verde: é o caminho que NÃO muda);
 *   · positivo — WP7 é a Stripe, que não manda `vl` e cujo objeto tem de
 *     continuar sendo `{ currency: "BRL" }` e nada mais.
 *   · WP8 é a fronteira: `vl` vem da URL.
 */
const purchase = (page) => page.evaluate(() =>
  (window.__fbq || []).filter((a) => a[0] === "track" && a[1] === "Purchase")[0]);

// ── WP6: Pix com valor ──────────────────────────────────────────────────────
test("WP6: vl=99.00 vira value 99 no Purchase, com o eventID de sempre", async () => {
  const { page } = await abrirHome(`${BASE}&pl=essencial&gw=pix&vl=99.00`);
  const [, , dados, ids] = await purchase(page);
  assert.equal(dados.value, 99, `value: ${JSON.stringify(dados)}`);
  assert.equal(dados.currency, "BRL");
  assert.equal(ids.eventID, "purchase_tok_x");
  await page.close();
});

// ── WP7: CONTROLE POSITIVO — Stripe (sem vl) segue byte a byte ──────────────
test("WP7: compra sem vl manda só currency, sem a chave value", async () => {
  const { page } = await abrirHome(`${BASE}&pl=plus`);
  const [, , dados] = await purchase(page);
  assert.deepEqual(Object.keys(dados), ["currency"], `objeto: ${JSON.stringify(dados)}`);
  assert.equal(dados.currency, "BRL");
  await page.close();
});

// ── WP8: `vl` é entrada de fronteira ────────────────────────────────────────
// `1e308` e `9e99` são o achado que dói: um link forjado mandava um Purchase
// de 9e+99 BRL para a conta de anúncios. `99abc` e `99,00` são a leniência do
// `parseFloat`, que lia o começo e descartava o resto.
for (const sujo of ["abc", "-5", "1e308", "9e99", "99abc", "99,00"]) {
  test(`WP8: vl=${sujo} cai no caso sem valor, sem erro de JS`, async () => {
    const { page, erros } = await abrirHome(
      `${BASE}&pl=plus&gw=pix&vl=${encodeURIComponent(sujo)}`);
    const [, , dados] = await purchase(page);
    assert.deepEqual(Object.keys(dados), ["currency"], `objeto: ${JSON.stringify(dados)}`);
    assert.deepEqual(erros, [], `pageerror com vl sujo: ${erros.join(" | ")}`);
    await page.close();
  });
}

// ── WP9: `vl` também some da URL ────────────────────────────────────────────
test("WP9: vl some da URL depois do load", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix&vl=99.00`);
  const busca = await page.evaluate(() => location.search);
  assert.ok(!busca.includes("vl="), `sobrou vl na URL: ${busca}`);
  await page.close();
});

// ── WP10: `gw` é comparado normalizado, como o `pl` ─────────────────────────
/**
 * `pl` já passava por `toLowerCase` e `gw` era comparado cru: `?gw=PIX` caía no
 * ramo da Stripe DEPOIS de uma compra Pix — a pessoa que pagou à vista lia
 * "sua assinatura já está ativa, dá pra cancelar quando quiser".
 *
 * Controle negativo: volte o `String(gw).toLowerCase() === "pix"` para
 * `gw === "pix"` e este caso fica vermelho (WP1 e WP2, minúsculos, seguem
 * verdes — é o que separa medir do teatro).
 */
test("WP10: gw=PIX (maiúsculo) cai no ramo do Pix, não no da Stripe", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=PIX`);
  const t = await textos(page);
  assert.match(t.sub, /já começou/, `gw maiúsculo virou cópia da Stripe: ${t.sub}`);
  assert.equal(t.olho, "Pagamento confirmado", `olho: ${t.olho}`);
  await page.close();
});

// ── WP11: título e corpo decidem pelo MESMO ramo ────────────────────────────
/**
 * O título olhava `pix && inicio` e o corpo a cadeia `trial → pix → else`, onde
 * o trial ganha. Com `ev=trial&gw=pix&inicio=…` saía "Seu PigBank+ tá garantido"
 * (ramo Pix) em cima de "seus 30 dias grátis começam agora" (ramo trial): duas
 * promessas incompatíveis no mesmo card.
 *
 * Qual dos dois ramos vence não é o que se testa aqui — é que os DOIS textos
 * saiam do mesmo.
 *
 * Controle negativo: volte o título para `pix && inicio` e este caso fica
 * vermelho.
 */
test("WP11: ev=trial com gw=pix não mistura título de Pix com sub de trial", async () => {
  const { page } = await abrirHome(
    "?upgrade=success&sid=tok_x&ev=trial&td=30&pl=plus&gw=pix&inicio=2027-08-26");
  const t = await textos(page);
  assert.match(t.sub, /dias grátis/, `sub: ${t.sub}`);
  assert.equal(t.titulo, "Tá dentro do PigBank+",
    `título do ramo Pix com corpo de trial: ${t.titulo}`);
  assert.equal(t.olho, "Assinatura confirmada", `olho: ${t.olho}`);
  await page.close();
});

/* ── WP12 não existe, e o motivo importa ─────────────────────────────────────
 * O olho (`wp-eyebrow-txt`) passou a ser escrito nos TRÊS ramos, e não só no do
 * Pix: título, sub e chip já eram, e a troca de mão única deixava o modal preso
 * em "Pagamento confirmado" depois de um ramo Pix.
 *
 * Não há caso aqui porque o defeito é INALCANÇÁVEL por URL: o `openWelcomePro`
 * roda uma vez por carga, e a carga começa do HTML estático, que já traz
 * "Assinatura confirmada" — que é o que o WP3 mede. Só uma segunda chamada na
 * mesma página o expõe, e `openWelcomePro` não é global (mora dentro do
 * `PBPages.home`); exportá-lo para o `window` só para o teste seria abrir
 * superfície de produção por causa do teste. O conserto fica pela simetria; a
 * cobertura, honesta sobre o que não tem.
 */
