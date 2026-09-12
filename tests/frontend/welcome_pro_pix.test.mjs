/**
 * O modal de boas-vindas da /home depois do Pix ANUAL, e o `Purchase` do pixel.
 *
 * Duas coisas moram aqui, e viraram uma só quando a FONTE mudou:
 *
 *   · a CÓPIA — quem pagava o Pix anual lia "Sua assinatura já está ativa. Dá
 *     pra cancelar quando quiser" e não tem assinatura nenhuma (compra à vista,
 *     sem cartão, sem renovação);
 *   · a FONTE — o valor e a data vinham da QUERY STRING (`vl=`, `inicio=`).
 *     Qualquer visitante abria `/home?upgrade=success&sid=<forjado>&vl=999999`
 *     e mandava um Purchase de R$ 999.999 para a NOSSA conta de anúncios, sem
 *     deduplicar com a CAPI (o `eventID` leva o `sid` que ele escolheu). E a
 *     data era um retrato do checkout, que envelhece: na migração Stripe→Pix o
 *     `_stripe_cancel` adia o começo do acesso depois de o QR estar na tela.
 *     Hoje os dois saem de `GET /billing/pix/<sid>`, que é autenticado e filtra
 *     por dono (404 para token de outro usuário).
 *
 * Os controles do CLAUDE.md §3, MEDIDOS (rodados, não deduzidos) — as três
 * contagens abaixo são ANTERIORES ao WP18, remeça antes de reusar:
 *   · negativo da CÓPIA — troque no `openWelcomePro` o ramo do Pix pelo `else`
 *     da Stripe (`} else if (false && modo === "pix") {`) e ficam vermelhos 20
 *     de 33 (remedido depois do WP15/WP16/WP17; era 13 de 24), por nome: WP1,
 *     WP2, os SETE casos do WP4, WP4b, WP8, WP10, WP13, WP14, os QUATRO do WP15
 *     e os dois do WP16. **WP5 e WP9 continuam VERDES** — eles só medem
 *     `searchParams.delete`, que a mutação não toca;
 *   · negativo do STATUS — `const pago = !!cobranca;` (sem o
 *     `&& cobranca.status === "paid"`) e ficam vermelhos os QUATRO casos do
 *     WP15; `WP15 paid` e os outros 28 seguem verdes;
 *   · negativo da FONTE — volte a ler `vl` e `inicio` da URL no `home.html`
 *     (`cents` do `params.get("vl")`, `inicioPix` do `wpInicioValido(inicio)`)
 *     e ficam vermelhos 5: WP1, WP4b, WP6, WP8 e WP13 — número MEDIDO antes de
 *     WP15/WP16/WP17 existirem, remeça antes de reusar;
 *   · positivo — WP3 e WP7 são o caminho da Stripe SEM `gw`, VERDES nas duas
 *     mutações acima: cópia igual à de hoje, objeto do pixel sem `value`, e ZERO
 *     requisição a `/billing/pix/`.
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
 *
 * `cobranca` é o corpo de `GET /billing/pix/<sid>`; `null` (o padrão) responde
 * **404**, que é o caminho de token forjado — e o que prova que nada é
 * inventado quando a busca falha.
 *
 * Devolve `{ page, erros, pix }` — `erros` é a lista de `pageerror` (entrada
 * suja não pode quebrar JS) e `pix.n` conta as requisições a `/billing/pix/`,
 * que no caminho da Stripe têm de ser ZERO.
 */
async function abrirHome(query, cobranca = null,
                         { pendurar = false, semTimeoutNativo = false,
                           initScript = null,
                           esperar = "#welcome-pro-overlay.open" } = {}) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const erros = [];
  const pix = { n: 0 };
  page.on("pageerror", (e) => erros.push(String(e)));
  // Antes de qualquer script da página: é assim que um stub de `window.fetch`
  // fica ANTES do wrapper do auth-refresh.js e enxerga as opções cruas do
  // fetch (o `signal`, que é o que os casos de teto medem). Aceita `fn` ou
  // `[fn, arg]` — o `arg` é a única forma de passar dado para dentro da página.
  if (initScript) {
    await page.addInitScript(...(Array.isArray(initScript) ? initScript : [initScript]));
  }
  // Safari/WKWebView < 16.4 (o alvo do app é iOS 14.0): `AbortSignal.timeout`
  // não existe. Apagar a propriedade ANTES do load é o que reproduz esse
  // navegador aqui — o Chromium do Playwright sempre a tem.
  if (semTimeoutNativo) {
    await page.addInitScript(() => { delete AbortSignal.timeout; });
  }
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
  // Depois do `**/*`, de propósito: no Playwright a rota mais recente ganha.
  await page.route("**/billing/pix/**", (route) => {
    pix.n += 1;
    if (pendurar) return;                 // nunca responde: exercita o teto
    return cobranca
      ? route.fulfill(json(cobranca))
      : route.fulfill({ status: 404, contentType: "application/json",
                        body: JSON.stringify({ detail: "Cobrança não encontrada." }) });
  });
  await page.goto(`${ORIGIN}/home.html${query}`);
  // O modal sobe 450 ms depois do load, e só se a validação de sessão passou.
  // `esperar` só muda nos casos em que a validação NÃO passa (WP21/WP22): lá o
  // `showAccessError` troca o #page-root e TIRA a classe `open` do overlay, então
  // esperar por ela penduraria até os 15 s.
  await page.waitForSelector(esperar, { timeout: 15000 });
  return { page, erros, pix };
}

const textos = (page) => page.evaluate(() => ({
  olho: document.getElementById("wp-eyebrow-txt").textContent.trim(),
  titulo: document.getElementById("wp-title").textContent,
  sub: document.getElementById("wp-sub").textContent,
  subHtml: document.getElementById("wp-sub").innerHTML,
  chipEscondido: document.getElementById("wp-chip").hidden,
}));

const BASE = "?upgrade=success&sid=tok_x&ev=purchase&td=0";
// Cobrança AGENDADA e cobrança IMEDIATA, como o servidor as devolve. Na
// imediata `starts_at` também vem preenchido (com `agora`) — é por isso que
// quem separa os dois casos é `agendada`, e não a presença da data.
const AGENDADA = { status: "paid", plan: "pro_max", amount_cents: 49900,
                   credit_cents: 0, agendada: true,
                   starts_at: "2027-08-26T03:00:00+00:00" };
const IMEDIATA = { ...AGENDADA, agendada: false,
                   starts_at: new Date().toISOString() };

// ── WP1: Pix AGENDADO ───────────────────────────────────────────────────────
test("WP1: Pix agendado diz o plano, a data do servidor e que não renova", async () => {
  const { page } = await abrirHome(`${BASE}&pl=essencial&gw=pix`, AGENDADA);
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
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix`, IMEDIATA);
  const t = await textos(page);
  assert.match(t.titulo, /PigBank Pro/, `título: ${t.titulo}`);
  assert.match(t.sub, /já começou/, `sub: ${t.sub}`);
  assert.doesNotMatch(t.sub, /cancelar/i, `prometeu cancelamento no Pix: ${t.sub}`);
  assert.doesNotMatch(t.sub, /\d{2}\/\d{2}\/\d{4}/, `inventou data no imediato: ${t.sub}`);
  await page.close();
});

// ── WP3: CONTROLE POSITIVO — a Stripe não mudou e não busca nada ────────────
test("WP3: compra no cartão (sem gw) mantém a cópia de hoje, sem buscar cobrança", async () => {
  const { page, pix } = await abrirHome(`${BASE}&pl=plus`, AGENDADA);
  const t = await textos(page);
  assert.equal(t.titulo, "Tá dentro do PigBank+");
  assert.equal(t.sub,
    "Sua assinatura já está ativa. Dá pra cancelar quando quiser, direto nas configurações.");
  assert.equal(t.olho, "Assinatura confirmada", `o olho da Stripe mudou: ${t.olho}`);
  assert.equal(pix.n, 0, `a Stripe bateu ${pix.n}× em /billing/pix/`);
  await page.close();
});

// ── WP4: `starts_at` do servidor também passa pelo validador ────────────────
// Ele deixou de vir da URL, mas continua virando a promessa "seu ano começa em
// <data>" na tela — coluna estranha ou corpo malformado não podem escrever
// compromisso nosso. "2027-02-30" e "2027-11-31" TÊM o formato e passam no
// `Date.parse` (que normaliza em vez de recusar): eram os que chegavam à tela
// como "30/02/2027". "0000-01-01" morre na ida e volta (o `Date` do JS mapeia
// ano 0 para 1900). O ÚNICO que depende da faixa fixa é "9999-12-31".
for (const sujo of ["<img src=x onerror=alert(1)>", "2027-13-99", "amanhã",
                    "2027-02-30", "2027-11-31", "0000-01-01", "9999-12-31"]) {
  test(`WP4: starts_at=${sujo} cai no texto imediato, sem markup e sem erro`, async () => {
    const { page, erros } = await abrirHome(`${BASE}&pl=plus&gw=pix`,
      { ...AGENDADA, starts_at: sujo });
    const t = await textos(page);
    assert.match(t.sub, /já começou/, `data inválida virou texto de agendado: ${t.sub}`);
    assert.ok(!t.subHtml.includes("<"), `entrou markup no sub: ${t.subHtml}`);
    assert.ok(!t.sub.includes(sujo), `a entrada crua foi para a tela: ${t.sub}`);
    assert.deepEqual(erros, [], `pageerror com starts_at sujo: ${erros.join(" | ")}`);
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
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix`,
    { ...AGENDADA, starts_at: "2029-09-09T03:00:00+00:00" });
  const t = await textos(page);
  assert.match(t.sub, /09\/09\/2029/, `renovação empilhada virou imediato: ${t.sub}`);
  assert.doesNotMatch(t.sub, /já começou/, `sub: ${t.sub}`);
  await page.close();
});

// ── WP5: a URL não guarda os parâmetros novos ───────────────────────────────
// `inicio` não é mais escrito pelo pix-poll.js, mas continua sendo APAGADO:
// link antigo, de antes desta mudança, ainda existe em aba e histórico.
test("WP5: gw e inicio somem da URL depois do load", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix&inicio=2027-08-26`, IMEDIATA);
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
 * A receita sai do `amount_cents` da COBRANÇA DO DONO, lida do servidor: no
 * upgrade Pix→Pix o cobrado é menor que o de tabela (crédito proporcional), e
 * a URL não é fonte de dinheiro.
 *
 * Controles:
 *   · negativo — leia o `vl` da URL de novo no home.html e WP6 fica vermelho
 *     (WP7, o positivo, continua verde: é o caminho que NÃO muda);
 *   · positivo — WP7 é a Stripe, cujo objeto tem de continuar sendo
 *     `{ currency: "BRL" }` e nada mais;
 *   · WP8 e WP8b são a falha da busca: nada é inventado.
 */
const purchase = (page) => page.evaluate(() =>
  (window.__fbq || []).filter((a) => a[0] === "track" && a[1] === "Purchase")[0]);

// ── WP6: o valor é o do SERVIDOR, mesmo com a URL gritando outro ────────────
test("WP6: vl=999999 na URL é ignorado; value sai dos 49900 centavos do servidor", async () => {
  const { page } = await abrirHome(`${BASE}&pl=essencial&gw=pix&vl=999999`, AGENDADA);
  const [, , dados, ids] = await purchase(page);
  assert.equal(dados.value, 499, `value: ${JSON.stringify(dados)}`);
  assert.equal(dados.currency, "BRL");
  assert.equal(ids.eventID, "purchase_tok_x");
  await page.close();
});

// ── WP7: CONTROLE POSITIVO — Stripe segue byte a byte ───────────────────────
test("WP7: compra no cartão manda só currency, sem a chave value", async () => {
  const { page, pix } = await abrirHome(`${BASE}&pl=plus`, AGENDADA);
  const [, , dados] = await purchase(page);
  assert.deepEqual(Object.keys(dados), ["currency"], `objeto: ${JSON.stringify(dados)}`);
  assert.equal(dados.currency, "BRL");
  assert.equal(pix.n, 0, `a Stripe bateu ${pix.n}× em /billing/pix/`);
  await page.close();
});

// ── WP8: token forjado (404) não vira receita ───────────────────────────────
// É o caso do apontamento: `?sid=<qualquer coisa>&vl=999999` de um visitante
// que nunca comprou. O 404 do endpoint (token inexistente OU de outro dono) tem
// de deixar o objeto igual ao da Stripe.
test("WP8: 404 na busca manda só currency, mesmo com vl na URL", async () => {
  const { page, erros, pix } = await abrirHome(
    `${BASE}&pl=plus&gw=pix&vl=999999&inicio=2027-08-26`);
  const [, , dados] = await purchase(page);
  const t = await textos(page);
  assert.deepEqual(Object.keys(dados), ["currency"], `objeto: ${JSON.stringify(dados)}`);
  assert.match(t.sub, /já começou/, `a data da URL virou promessa: ${t.sub}`);
  assert.equal(pix.n, 1, `buscas: ${pix.n}`);
  assert.deepEqual(erros, [], `pageerror no 404: ${erros.join(" | ")}`);
  await page.close();
});

// ── WP8b: `amount_cents` estranho no corpo não vira valor ───────────────────
for (const sujo of ["499", null, 0, -5, 49900.5]) {
  test(`WP8b: amount_cents=${JSON.stringify(sujo)} cai no caso sem valor`, async () => {
    const { page, erros } = await abrirHome(`${BASE}&pl=plus&gw=pix`,
      { ...IMEDIATA, amount_cents: sujo });
    const [, , dados] = await purchase(page);
    assert.deepEqual(Object.keys(dados), ["currency"], `objeto: ${JSON.stringify(dados)}`);
    assert.deepEqual(erros, [], `pageerror com amount_cents sujo: ${erros.join(" | ")}`);
    await page.close();
  });
}

// ── WP9: `vl` também some da URL ────────────────────────────────────────────
test("WP9: vl some da URL depois do load", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix&vl=99.00`, IMEDIATA);
  const busca = await page.evaluate(() => location.search);
  assert.ok(!busca.includes("vl="), `sobrou vl na URL: ${busca}`);
  await page.close();
});

// ── WP10: `gw` é comparado normalizado, como o `pl` ─────────────────────────
/**
 * `pl` já passava por `toLowerCase` e `gw` era comparado cru: `?gw=PIX` caía no
 * ramo da Stripe DEPOIS de uma compra Pix — a pessoa que pagou à vista lia
 * "sua assinatura já está ativa, dá pra cancelar quando quiser". Hoje o mesmo
 * `ehPix` também decide a BUSCA, então o normalizado vale por dois.
 *
 * Controle negativo: volte o `String(gw).toLowerCase() === "pix"` para
 * `gw === "pix"` e este caso fica vermelho (WP1 e WP2, minúsculos, seguem
 * verdes — é o que separa medir do teatro).
 */
test("WP10: gw=PIX (maiúsculo) cai no ramo do Pix, não no da Stripe", async () => {
  const { page, pix } = await abrirHome(`${BASE}&pl=pro&gw=PIX`, IMEDIATA);
  const t = await textos(page);
  assert.match(t.sub, /já começou/, `gw maiúsculo virou cópia da Stripe: ${t.sub}`);
  assert.equal(t.olho, "Pagamento confirmado", `olho: ${t.olho}`);
  assert.equal(pix.n, 1, `gw maiúsculo não buscou a cobrança: ${pix.n}`);
  await page.close();
});

// ── WP11: título e corpo decidem pelo MESMO ramo ────────────────────────────
/**
 * O título olhava `pix && inicio` e o corpo a cadeia `trial → pix → else`, onde
 * o trial ganha. Com `ev=trial&gw=pix` saía "Seu PigBank+ tá garantido"
 * (ramo Pix) em cima de "seus 30 dias grátis começam agora": duas promessas
 * incompatíveis no mesmo card.
 *
 * Qual dos dois ramos vence não é o que se testa aqui — é que os DOIS textos
 * saiam do mesmo.
 *
 * Controle negativo: volte o título para `pix && inicio` e este caso fica
 * vermelho.
 */
test("WP11: ev=trial com gw=pix não mistura título de Pix com sub de trial", async () => {
  const { page } = await abrirHome(
    "?upgrade=success&sid=tok_x&ev=trial&td=30&pl=plus&gw=pix", AGENDADA);
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

/* ── WP13: a data do servidor GANHA da data que a URL trazia ─────────────────
 * O caso do apontamento P2, e o motivo de a busca existir: quem cria o QR pouco
 * antes de a assinatura Stripe renovar tem o `access_starts_at` ADIADO pelo
 * `_stripe_cancel` no momento do pagamento. O `inicio=` da URL era a estimativa
 * do checkout, então a /home prometia a data VELHA enquanto o acesso e o e-mail
 * usavam a nova.
 *
 * Controle negativo: volte a passar `wpInicioValido(inicio)` (o da URL) para o
 * `openWelcomePro` e este caso fica vermelho — WP1 continua verde, porque lá as
 * duas datas seriam a mesma.
 */
test("WP13: com inicio antigo na URL, o modal mostra a data que o servidor deu", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix&inicio=2027-08-26`,
    { ...AGENDADA, starts_at: "2028-01-15T03:00:00+00:00" });
  const t = await textos(page);
  assert.match(t.sub, /15\/01\/2028/, `mostrou a data da URL: ${t.sub}`);
  assert.doesNotMatch(t.sub, /26\/08\/2027/, `a data velha sobreviveu: ${t.sub}`);
  await page.close();
});

/* ── WP14: a busca não pode segurar a celebração ─────────────────────────────
 * O `fbq` e o modal passaram a esperar a resposta do servidor. Sem teto, uma
 * conexão pendurada deixava quem ACABOU DE PAGAR olhando para a /home de
 * sempre, sem confirmação nenhuma. O teto de 4 s (`AbortController` +
 * `setTimeout`) fecha isso, e o caminho vencido é o mesmo do 404: cópia do
 * imediato, Purchase sem `value`.
 *
 * Controle negativo: tire o `signal: ctrl.signal` do fetch e este caso estoura
 * no `waitForSelector` (15 s) — os outros seguem verdes.
 */
test("WP14: busca pendurada — o modal sobe assim mesmo, sem valor inventado", async () => {
  const t0 = Date.now();
  const { page, erros } = await abrirHome(`${BASE}&pl=plus&gw=pix`, AGENDADA, { pendurar: true });
  const decorrido = Date.now() - t0;
  const [, , dados] = await purchase(page);
  const t = await textos(page);
  assert.deepEqual(Object.keys(dados), ["currency"], `objeto: ${JSON.stringify(dados)}`);
  assert.match(t.sub, /já começou/, `sub: ${t.sub}`);
  assert.ok(decorrido < 12000, `o modal levou ${decorrido}ms para subir`);
  assert.deepEqual(erros, [], `pageerror no timeout: ${erros.join(" | ")}`);
  await page.close();
});

/* ── WP15: cobrança NÃO PAGA não vira receita ────────────────────────────────
 * O buraco que a busca abriu, e que só a validação de DONO não fechava: o poll
 * responde 200 com `amount_cents` para `pending`, `expired`, `canceled` e
 * `refunded`. O próprio dono, com o QR na tela e sem ter pago, abrindo
 * `/home?upgrade=success&sid=<token>&ev=purchase&gw=pix`, mandava um Purchase
 * de R$ 499 — crível, sem venda e SEM par na CAPI (o evento do servidor só sai
 * no pagamento), logo sem deduplicar com nada. Repetível: cada ciclo expirado
 * gera um `public_token` novo, logo um `eventID` novo.
 *
 * Controle negativo MEDIDO: tire o `&& cobranca.status === "paid"` do `pago`
 * (`const pago = !!cobranca;`) e os quatro casos não-pagos ficam VERMELHOS.
 * Controle positivo: `WP15 paid`, no mesmo grupo — sem ele, um `pago = false`
 * fixo passaria nos quatro (e mandaria toda venda de verdade sem receita).
 */
for (const status of ["pending", "expired", "canceled", "refunded"]) {
  test(`WP15: status=${status} manda só currency, sem value`, async () => {
    const { page, erros } = await abrirHome(`${BASE}&pl=pro&gw=pix`,
      { ...AGENDADA, status });
    const [, , dados] = await purchase(page);
    const t = await textos(page);
    assert.deepEqual(Object.keys(dados), ["currency"],
      `Purchase com valor numa cobrança ${status}: ${JSON.stringify(dados)}`);
    // A data também é promessa, e promessa é de quem pagou: o texto cai no
    // imediato, exatamente como no 404 (WP8).
    assert.match(t.sub, /já começou/, `cobrança ${status} prometeu data: ${t.sub}`);
    assert.deepEqual(erros, [], `pageerror com status=${status}: ${erros.join(" | ")}`);
    await page.close();
  });
}

test("WP15 paid: a mesma cobrança, paga, manda os 49900 centavos", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix`,
    { ...AGENDADA, status: "paid" });
  const [, , dados] = await purchase(page);
  assert.equal(dados.value, 499, `value: ${JSON.stringify(dados)}`);
  await page.close();
});

/* ── WP16: `agendada` fora do booleano não vira promessa de data ─────────────
 * Par do WP8b, que já blinda `amount_cents` contra lixo: `agendada: 1` e
 * `agendada: "false"` são verdadeiros em JS e renderizavam "começa em <data>".
 * Hoje a fonte é o nosso servidor — o caso é TEÓRICO —, mas é a mesma
 * justificativa que manteve o `wpInicioValido` vivo (corpo malformado não pode
 * virar compromisso escrito), e ela não pode valer pela metade.
 *
 * Controle negativo: troque `cobranca.agendada === true` por `cobranca.agendada`
 * e o caso da STRING fica vermelho (`1` também). Positivo: WP1, que é
 * `agendada: true` de verdade e mostra a data.
 */
for (const agendada of [1, "false"]) {
  test(`WP16: agendada=${JSON.stringify(agendada)} cai no texto do imediato`, async () => {
    const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix`,
      { ...AGENDADA, agendada });
    const t = await textos(page);
    assert.match(t.sub, /já começou/, `agendada não-booleano virou data: ${t.sub}`);
    await page.close();
  });
}

/* ── WP17: `amount_cents` absurdo não vira receita ───────────────────────────
 * O teto de R$ 1.000.000 existia quando o valor vinha da URL e sobreviveu à
 * troca de fonte: `999999999999` centavos viravam `value: 9999999999.99` num
 * Purchase da NOSSA conta. A coluna é nossa, mas o plano mais caro custa
 * R$ 499 — acima do teto é dado corrompido, não venda.
 *
 * Controle negativo: tire o `&& cents < 1e8` e este caso fica vermelho (WP6 e
 * `WP15 paid`, com 49900, seguem verdes).
 */
test("WP17: amount_cents=999999999999 cai no caso sem valor", async () => {
  const { page } = await abrirHome(`${BASE}&pl=pro&gw=pix`,
    { ...AGENDADA, amount_cents: 999999999999 });
  const [, , dados] = await purchase(page);
  assert.deepEqual(Object.keys(dados), ["currency"], `objeto: ${JSON.stringify(dados)}`);
  await page.close();
});

/* ── WP18: Safari/WKWebView sem `AbortSignal.timeout` ────────────────────────
 * O app roda com IPHONEOS_DEPLOYMENT_TARGET 14.0, e `AbortSignal.timeout` só
 * existe no WKWebView 16.4+. Como ele era avaliado ao MONTAR as opções do
 * fetch, num aparelho velho o `TypeError` estourava ANTES de a requisição sair:
 * o `catch` engolia, `cobranca` ficava nula e — com o backend PERFEITAMENTE
 * saudável — toda compra Pix perdia o `value` do Pixel e a compra AGENDADA era
 * anunciada como "já começou". `AbortController` + `setTimeout` roda desde o
 * Safari 12.1 e mantém o mesmo teto.
 *
 * Controle negativo MEDIDO: volte o fetch para `signal: AbortSignal.timeout(4000)`
 * e SÓ este caso fica vermelho (`sub` vira "já começou" e o objeto perde o
 * `value`). Positivo do grupo: WP1/WP6, os mesmos dados com a propriedade no
 * lugar — verdes nas duas versões, que é o que prova que a correção não trocou
 * o comportamento do navegador moderno.
 */
test("WP18: sem AbortSignal.timeout (iOS 14), a agendada mantém data e value", async () => {
  const { page, erros } = await abrirHome(`${BASE}&pl=pro&gw=pix`, AGENDADA, { semTimeoutNativo: true });
  const semTimeout = await page.evaluate(() => typeof AbortSignal.timeout);
  assert.equal(semTimeout, "undefined", "o ambiente do teste não foi degradado");
  const [, , dados] = await purchase(page);
  const t = await textos(page);
  assert.equal(dados.value, 499, `Purchase sem value no Safari velho: ${JSON.stringify(dados)}`);
  assert.match(t.sub, /26\/08\/2027/, `agendada anunciada sem a data: ${t.sub}`);
  assert.doesNotMatch(t.sub, /já começou/, `agendada virou "já começou": ${t.sub}`);
  assert.deepEqual(erros, [], `pageerror sem AbortSignal.timeout: ${erros.join(" | ")}`);
  await page.close();
});

/* ── WP19/WP20: o teto que o `finally` cancelava sozinho ─────────────────────
 *
 * Irmãos do conserto de `pb-nav.js` (PR #367): quando a função volta por um
 * `return` que fica ENTRE os headers e a leitura do corpo, o
 * `finally { clearTimeout(timer) }` cancela o único abort agendado e ninguém
 * aborta — o corpo não lido segue baixando até EOF/GC e o teto prometido deixa
 * de valer NAQUELE ramo. O conserto é uma guarda cobrindo todas as saídas:
 * `finally { clearTimeout(timer); ctrl.abort(); }`.
 *
 * A medida é os MILISSEGUNDOS DE DENTRO DA PÁGINA, do início do fetch até o
 * abort — nunca o relógio do Playwright.
 *
 * As mutações dos QUATRO sites deste arquivo, RODADAS (não deduzidas) e
 * rotuladas pelo TEXTO mutado — medidas em 2026-09-10, remeça antes de reusar:
 *
 *   | texto mutado (home.html)                            | resultado          |
 *   |-----------------------------------------------------|--------------------|
 *   | `} finally { clearTimeout(timer); ctrl.abort(); }`   | WP19 VERMELHO,     |
 *   |   → sem o `ctrl.abort()` (busca da cobrança)         |   os outros 37 ✔   |
 *   | o mesmo finally → `} finally { }`                    | WP19 VERMELHO,     |
 *   |                                                      |   os outros 37 ✔   |
 *   | `finally { if (timer) clearTimeout(timer); if (ctrl)` | WP20 VERMELHO,    |
 *   |   `ctrl.abort(); }` → sem o abort (`loadAuthMe`)      |   os outros 37 ✔  |
 *   | `finally { if (vctrl) vctrl.abort(); }` → `{ }`      | WP21 e WP22        |
 *   |   (o bloco do validate, em initHome)                 |   VERMELHOS (36 ✔) |
 *   | `.finally(() => clearTimeout(timer))` do             | 36 de 38 VERMELHOS |
 *   |   `_boundedValidate` → `{ clearTimeout(timer);`      |   — só WP21 e WP22 |
 *   |   `ctrl.abort(); }` (o abort no lugar ERRADO)        |   sobrevivem       |
 *
 * A última é a alternativa REJEITADA, e o número é o motivo: abortar no
 * `.finally` da cadeia mata o `r.json()` de todo validate bem-sucedido.
 *
 * A do `} finally { }` é a do timer VAZADO, e o que a mata é o `notEqual null`: quando o
 * caso lê `__abortado` (~1,2 s depois do load, assim que o modal sobe) o
 * `setTimeout` de 4 s ainda NÃO disparou. O `< 1000` fica assim como guarda de
 * regressão — ele prende o abort ao `return` do ramo, não ao teto —, mas quem
 * discrimina hoje é o `null`: registrado aqui porque a versão anterior deste
 * comentário atribuía a morte dessa mutação à janela, o que a medição desmentiu.
 *
 * Controle positivo dos dois: o resto deste arquivo. `abrirHome` só devolve
 * depois de `#welcome-pro-overlay.open`, então todo caso aqui já prova que o
 * caminho legítimo (cobrança respondida, `/auth/me` com plano pago) continua
 * fechando o overlay e abrindo o modal.
 */

/**
 * Stub in-page: responde a URL que casa `marca` com os headers prontos e o
 * CORPO ABERTO (`ReadableStream` que nunca empurra nada), e anota, DE DENTRO da
 * página, os ms do início do fetch até o abort.
 *
 * `so1a` serve o /auth/me: só a PRIMEIRA chamada com `signal` (a do poll
 * pós-checkout, a única com teto) cai no corpo aberto; as seguintes seguem para
 * o `page.route`, que devolve o plano pago e deixa o overlay fechar dentro dos
 * 15 s do `waitForSelector`.
 */
function stubCorpoAberto({ marca, status, so1a }) {
  window.__abortado = null;
  window.__pedidos = 0;
  // `pageerror` NÃO pega promise rejeitada sem catch, e é justamente o risco de
  // abortar aqui: o `auth-refresh.js` renova o 401 com `_origFetch` SEM signal
  // (ver o comentário do `_raceBudget`, home.html), e ele já perdeu a corrida
  // quando o abort
  // chega. Sem esta lista o caso ficaria calado sobre isso.
  window.__rejeicoes = [];
  window.addEventListener("unhandledrejection", (e) => {
    window.__rejeicoes.push(String((e.reason && e.reason.name) || e.reason));
  });
  const orig = window.fetch;
  window.fetch = function (u, o) {
    // `o.signal` some quando a mutação tira o teto — sem esta guarda o stub
    // lançaria e o caso ficaria vermelho pelo motivo errado.
    const comSinal = !!(o && o.signal);
    const casa = String(u).includes(marca) && (!so1a || (comSinal && window.__pedidos === 0));
    if (!casa) return orig.apply(this, arguments);
    window.__pedidos += 1;
    const t = Date.now();
    if (comSinal) {
      o.signal.addEventListener("abort", () => { window.__abortado = Date.now() - t; });
    }
    return Promise.resolve(new Response(new ReadableStream({ start() {} }), { status }));
  };
}

const msAteAbort = (page) => page.evaluate(() => window.__abortado);

test("WP19: 404 do /billing/pix com o corpo aberto é ABORTADO, não esquecido", async () => {
  const { page, erros } = await abrirHome(`${BASE}&pl=pro&gw=pix`, null, {
    initScript: [stubCorpoAberto, { marca: "/billing/pix/", status: 404 }],
  });
  const ms = await msAteAbort(page);
  assert.notEqual(ms, null,
    "o 404 com corpo aberto não foi abortado: sem o `ctrl.abort()` no finally o"
    + " clearTimeout tira o único relógio e o corpo baixa sem teto nenhum");
  assert.equal(ms < 1000, true,
    `o abort chegou ${ms}ms depois do início do fetch: é o teto de 4 s disparando,`
    + " não o abort() do finally");
  // E o ramo continua sendo o do 404: nada inventado, texto do imediato.
  assert.match((await textos(page)).sub, /já começou/);
  assert.deepEqual(await page.evaluate(() => window.__rejeicoes), [],
    "o abort virou promise rejeitada sem catch");
  assert.deepEqual(erros, []);
  await page.close();
});

test("WP20: /auth/me !ok com o corpo aberto é ABORTADO dentro do budget", async () => {
  const { page, erros } = await abrirHome(`${BASE}&pl=pro&gw=pix`, AGENDADA, {
    initScript: [stubCorpoAberto, { marca: "/auth/me", status: 503, so1a: true }],
  });
  const ms = await msAteAbort(page);
  assert.notEqual(ms, null,
    "o /auth/me !ok com corpo aberto não foi abortado: o `return null` do !ok sai"
    + " entre os headers e o corpo, e o finally cancelava o único abort agendado");
  assert.equal(ms < 1000, true,
    `o abort chegou ${ms}ms depois do início do fetch: é o budget do poll`
    + " disparando, não o abort() do finally");
  // Controle positivo DENTRO do caso: o overlay fechou e o modal subiu, ou seja
  // a 2ª volta do poll (plano pago) seguiu funcionando com o abort no lugar.
  assert.match((await textos(page)).sub, /26\/08\/2027/);
  // O risco nomeado no plano: o abort do `loadAuthMe` não alcança a renovação
  // que o auth-refresh faz com `_origFetch` sem signal, e o `_raceBudget` já
  // devolveu null pelo timer. Medido, não deduzido.
  assert.deepEqual(await page.evaluate(() => window.__rejeicoes), [],
    "o abort do loadAuthMe virou unhandled rejection no _raceBudget");
  assert.deepEqual(erros, []);
  await page.close();
});

/* ── WP21/WP22: o 6º site — o /auth/validate do retorno de checkout ──────────
 *
 * O `_boundedValidate` tem a MESMA doença dos outros cinco com uma forma
 * diferente: a cadeia `.finally(() => clearTimeout(timer))` roda quando os
 * HEADERS chegam, e dali em diante não há relógio nenhum. Só que aqui o corpo
 * é consumido pelo CALLER (`r.json()`, duas linhas depois), então o abort não
 * pode morar no `finally` da cadeia — mataria todo validate bem-sucedido. Quem
 * abandona é quem aborta: o `vctrl` fica no bloco do caller, abortado num
 * `finally` que cobre os quatro desfechos.
 *
 * Dois ramos, ambos no caminho de quem acabou de pagar (`_justUpgraded`):
 *   · WP21 — `if (!r || !r.ok) { … return; }` sai entre headers e corpo
 *     (sessão vencida no retorno do checkout é desfecho normal);
 *   · WP22 — headers 200 e o corpo trava: o `_raceBudget` desiste do `r.json()`
 *     aos 3 s e o caller retorna, abandonando a leitura.
 *
 * As duas mutações destes casos estão na tabela do cabeçalho do WP19/WP20, com
 * o resultado medido — inclusive a do abort no lugar ERRADO, que é o motivo de
 * o controller não morar dentro do `_boundedValidate`.
 *
 * Controle positivo: o resto do arquivo — `abrirHome` só volta com
 * `#welcome-pro-overlay.open`, ou seja, validate 200 com corpo lido de verdade.
 */

test("WP21: /auth/validate !ok com o corpo aberto é ABORTADO, não esquecido", async () => {
  const { page, erros } = await abrirHome(`${BASE}&pl=pro&gw=pix`, AGENDADA, {
    initScript: [stubCorpoAberto, { marca: "/auth/validate", status: 503 }],
    esperar: ".access-error",
  });
  const ms = await msAteAbort(page);
  assert.notEqual(ms, null,
    "o /auth/validate !ok com corpo aberto não foi abortado: o `return` do !r.ok"
    + " sai entre os headers e o corpo, e o .finally() da cadeia já cancelou o timer");
  assert.equal(ms < 1000, true,
    `o abort chegou ${ms}ms depois do início do fetch: perto do teto de 8s é`
    + " relógio vazado, não o abort() do finally do caller");
  assert.deepEqual(await page.evaluate(() => window.__rejeicoes), []);
  assert.deepEqual(erros, []);
  await page.close();
});

test("WP22: /auth/validate 200 com o corpo travado é ABORTADO quando o caller desiste", async () => {
  const { page, erros } = await abrirHome(`${BASE}&pl=pro&gw=pix`, AGENDADA, {
    initScript: [stubCorpoAberto, { marca: "/auth/validate", status: 200 }],
    esperar: ".access-error",
  });
  const ms = await msAteAbort(page);
  assert.notEqual(ms, null,
    "o corpo travado do /auth/validate não foi abortado: o `_raceBudget` desiste"
    + " aos 3s e o caller retorna com o timer da cadeia já cancelado");
  // Aqui o `< 1000` do WP19/WP20 NÃO vale: o abort legítimo chega em ~3000ms
  // (o `_checkoutBudget(3000)` da leitura do corpo). 6000 ainda separa isso do
  // teto de 8s do `_boundedValidate`, que é o relógio que não deve sobrar.
  assert.equal(ms < 6000, true,
    `o abort chegou ${ms}ms depois do início do fetch: acima do _raceBudget de 3s`);
  assert.deepEqual(await page.evaluate(() => window.__rejeicoes), []);
  assert.deepEqual(erros, []);
  await page.close();
});
