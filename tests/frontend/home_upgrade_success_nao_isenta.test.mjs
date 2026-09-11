/**
 * tests/frontend/home_upgrade_success_nao_isenta.test.mjs
 *
 * `?upgrade=success` é escolhido pelo CLIENTE, e virou bypass do corte.
 *
 * O servidor (`frontend/routes/shared.gate_plan_selection`) dispensa as DUAS
 * pernas do gate quando o parâmetro está presente, e tem de dispensar mesmo:
 * quem acabou de pagar tem `needs_plan_selection` true E `app_access` false até
 * o webhook chegar — as duas coisas que o `checkout.session.completed` escreve.
 * Medido 2026-09-11: dispensar só a perna da ESCOLHA devolve `302 /precos` para
 * `needs=True, acesso=False`, que é o estado de quem pagou há 3 segundos. A
 * opção mais apertada barra justamente quem o bypass existe para proteger.
 *
 * Quem fecha o buraco é ESTE lado. A home esperava o webhook com
 * `awaitCheckoutConfirmation()` (~20 s) e depois pulava os dois vereditos do
 * `/auth/me` com `!_justUpgraded` — a dispensa virava PERMANENTE, e qualquer
 * cortado abria `/home?upgrade=success` e ficava na Início com o snapshot
 * restaurado do `restoreHomeCache`.
 *
 * O `!_justUpgraded` saiu. Quando a execução chega no veredito, a espera já
 * aconteceu e o `me` é o último que o `/auth/me` deu.
 *
 * CONTROLE DECLARADO (`docs/controles_declarados.md`) — em `home.html`, reponha
 * o `&& !_justUpgraded` nas DUAS condições (troca de predicado; nada apagado).
 * VERMELHOS (medido 2026-09-11):
 *   "cortado com ?upgrade=success vai pra /precos depois do polling"
 *   "cadastro sem plano com ?upgrade=success vai pra /precos depois do polling"
 * Direção: bypass do corte por parâmetro de URL escolhido pelo cliente.
 *
 * Positivo do par, VERDE sob a injeção (é o que o torna positivo):
 *   "quem PAGOU e teve o webhook confirmado continua na Início"
 * Sem ele, um conserto que redirecionasse todo mundo passaria nos dois
 * negativos — e barrar quem pagou é pior que o furo.
 *
 * O QUE ESTE ARQUIVO NÃO ALCANÇA: o caminho em que o webhook demora MAIS que os
 * ~20 s do `_checkoutDeadline`. Exercitá-lo custa 20 s de relógio de parede por
 * caso, e a decisão que ele toma é a MESMA linha que os casos abaixo medem —
 * o que muda é só quanto tempo se espera antes. O resíduo está declarado no
 * comentário de `home.html`: esse usuário passa a cair na /precos.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const json = (body) => ({ status: 200, contentType: "application/json",
                          body: JSON.stringify(body) });

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Abre a /home com `?upgrade=success` e o `/auth/me` que você mandar.
 *
 * O corpo precisa ser "settled" para o `_checkoutSettled` (plano pago, sem
 * expiração vencida): é o que faz o polling sair na PRIMEIRA volta em vez de
 * rodar os 20 s. O que está sob teste é a decisão DEPOIS da espera, não a
 * duração dela.
 */
async function abrirHome(me, antes = null) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  if (antes) await antes(page);
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  if (me) await page.route("**/auth/me", (route) => route.fulfill(json(me)));
  await page.goto(`${ORIGIN}/home.html?upgrade=success`);
  return page;
}

/**
 * Espera o `location.replace` acontecer; devolve o pathname+search final.
 *
 * `timeout` curto no caso POSITIVO de propósito: lá o esperado é que NADA
 * aconteça, e esperar 12 s por um redirect que não vem custava 30 s de relógio
 * num arquivo de 3 casos. Os negativos redirecionam em menos de 1 s (medido),
 * então 4 s é margem de 4× — e se algum dia o redirect legítimo passar disso, o
 * caso positivo fica vermelho, que é o lado certo de errar.
 */
async function destino(page, timeout = 12000) {
  try {
    await page.waitForFunction(
      () => location.pathname.replace(/\.html$/, "") !== "/home", { timeout });
  } catch { /* não redirecionou — o caller decide se isso é o esperado */ }
  return page.evaluate(() => location.pathname + location.search);
}

// Plano pago e vigente: `_checkoutSettled` devolve true e o polling sai na 1ª
// volta. Só para o caso em que o webhook JÁ caiu.
const PAGO = { user_id: 1, plan: "pro", plan_expires_at: null };

// O CORTADO DE VERDADE. A versão anterior deste arquivo usava
// `{plan:"pro", plan_expires_at:null, app_access:false}` — estado que o
// servidor NUNCA produz: `plan_expires_at` nulo é vitalício, então
// `has_app_access` devolve True (medido). O efeito colateral era pior que a
// imprecisão: com aquele corpo o `_checkoutSettled` era verdadeiro, o polling
// saía na PRIMEIRA volta e os casos nunca exercitavam a espera — exatamente o
// trecho onde mora o bug do pagante de 19 s.
const CORTADO = { user_id: 1, plan: "free", plan_expires_at: null,
                  app_access: false };

test("cortado com ?upgrade=success vai pra /precos depois do polling", async () => {
  const page = await abrirHome(CORTADO);
  const url = await destino(page);
  assert.match(url, /^\/precos/,
    `o cortado ficou na Início com ?upgrade=success: ${url}`);
  assert.match(url, /ativar=1/, `marcador errado pro cortado: ${url}`);
  await page.close();
});

test("cortado com ?upgrade=success não deixa snapshot repintável", async () => {
  // O gêmeo do `clearSessionSnapshots()` de `dashboard.js:407`. A assimetria
  // era medida: o dashboard limpava antes de redirecionar e a Início não, então
  // o saldo do próprio usuário voltava à tela a cada recarga durante os ~21 s.
  const page = await abrirHome(CORTADO, (p) =>
    p.addInitScript(() => {
      sessionStorage.setItem("pb_home_1", JSON.stringify({ saldo: 4242.42 }));
      sessionStorage.setItem("pb_snap_1_2026_9", JSON.stringify({ x: 1 }));
    }));
  await destino(page);
  const sobrou = await page.evaluate(() => Object.keys(sessionStorage)
    .filter((k) => k.startsWith("pb_home_") || k.startsWith("pb_snap_")));
  assert.deepEqual(sobrou, [],
    `snapshot sobreviveu ao veredito negativo e repinta no reload: ${sobrou}`);
  await page.close();
});

test("cadastro sem plano com ?upgrade=success vai pra /precos depois do polling", async () => {
  // A outra perna. Sem este caso, um conserto que mexesse só no `app_access`
  // deixaria a da ESCOLHA aberta — "achei um caso" ≠ "resolvi a categoria" (§2).
  const page = await abrirHome({ user_id: 1, plan: "free", plan_expires_at: null,
                                 needs_plan_selection: true });
  const url = await destino(page);
  assert.match(url, /^\/precos/,
    `o cadastro sem plano ficou na Início com ?upgrade=success: ${url}`);
  assert.match(url, /escolha=1/, `marcador errado pro cadastro novo: ${url}`);
  await page.close();
});

test("quem PAGOU e teve o webhook confirmado continua na Início", async () => {
  // POSITIVO: é o que o bypass existe para proteger. Se este caso ficar
  // vermelho, o conserto passou a barrar cliente pagante — pior que o furo.
  const page = await abrirHome({ ...PAGO, app_access: true,
                                 needs_plan_selection: false });
  const url = await destino(page, 4000);
  assert.match(url, /^\/home/, `quem pagou foi expulso da Início: ${url}`);
  await page.close();
});

test("pagante cujo webhook cai NO FIM da janela continua na Início", async () => {
  // O caso de DINHEIRO, e o que a bateria anterior não tinha: o `/auth/me`
  // responde "sem plano" até o webhook cair, e o veredito final tem de ser o
  // ÚLTIMO que o servidor deu, não o de 1,5 s atrás.
  //
  // Medido antes do conserto: polls em 571…18795 ms, redirect em 21140 ms —
  // o último 1,2 s era tempo morto, e webhook em 19 s (DENTRO da janela que o
  // comentário promete) caía em `/precos?escolha=1`, segundos depois de pagar.
  //
  // O relógio é falso (`page.clock`) porque o caso real custa 20 s de parede.
  let confirmado = false;
  const page = await abrirHome(null, async (p) => {
    await p.clock.install();
    await p.route("**/auth/me", (route) => route.fulfill(json(
      confirmado ? { ...PAGO, app_access: true }
                 : { user_id: 1, plan: "free", plan_expires_at: null,
                     app_access: false })));
  });
  // Avança até depois do último poll da versão antiga (18,8 s) e confirma o
  // webhook ali — o ponto exato em que o pagante era expulso.
  await page.clock.runFor(19000);
  confirmado = true;
  await page.clock.runFor(4000);

  const url = await destino(page, 4000);
  assert.match(url, /^\/home/,
    `pagante com webhook no fim da janela foi mandado pra ${url}`);
  await page.close();
});
