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
 * O QUE ESTE ARQUIVO NÃO ALCANÇA: o timeout de rede real dentro do orçamento
 * de `_boundedAuthMe`/`_raceBudget`. A bateria de `comWebhookEm` roda com
 * relógio FALSO e cobre webhook depois do deadline de `_checkoutDeadline`
 * (22 s, 25 s) — mas com o relógio congelado durante toda request real, essa
 * corrida contra timeout nunca vence lá (limite declarado na doc da função).
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
  // `antes` DEPOIS das rotas, e isso não é estilo: no Playwright a rota mais
  // recente ganha, então registrando antes o `**/*` deste helper sombreava o
  // `/auth/me` do caller e o `me` chegava `{}` — sem `app_access`, sem
  // `needs_plan_selection`, nenhum redirect, TODOS os casos terminando em
  // /home. Foi assim que a bateria do relógio nasceu tautológica.
  if (antes) await antes(page);
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
    // Semeia UMA vez: `addInitScript` roda em toda navegação, inclusive no
    // redirect para /precos — sem a trava, o teste replantava as chaves que o
    // conserto tinha acabado de apagar e media a si mesmo.
    p.addInitScript(() => {
      if (sessionStorage.getItem("__semeado")) return;
      sessionStorage.setItem("__semeado", "1");
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

/**
 * Roda o retorno de checkout com relógio FALSO, avançando em passos de 250 ms,
 * e vira o `/auth/me` para "pago" quando o relógio chega em `webhookMs`.
 *
 * `page.clock.install()` deixa o relógio falso andar em tempo real fora de um
 * `runFor` — só existe `pauseAt`, não existe "pausado" por padrão. Sem
 * congelar, a fase da cadeia de polls varia com a latência real do arranque
 * (goto, `/auth/validate`, primeiro `/auth/me`). Medido 2026-09-17 com `node
 * --test --test-name-pattern="webhook em" tests/frontend/home_upgrade_success_nao_isenta.test.mjs`
 * sob 5 processos Playwright em paralelo mais 14 laços de CPU saturando as
 * CPUs (14 laços sozinhos não reproduzem: 2/2 verde): com a lista ESTENDIDA
 * de 8 instantes, os casos 19000/19750/20000/20250 ficaram vermelhos em 5 de
 * 5 rodadas; com a lista REAL do arquivo (4 instantes), só `webhook em 20000`
 * ficou vermelho, também em 5 de 5 — não porque o conserto falhasse, mas
 * porque o relógio de fundo empurrava a fase. `pauseAt(Date.now() + 60000)`
 * congela de vez: dali em diante o relógio só anda quando o teste manda com
 * `runFor`.
 *
 * Sem carga, o relógio congelado não emperra a página nem trava um
 * `/auth/me` em voo: medido, com `pauseAt` puro (sem `ociosa()`), a lista
 * estendida de 8 instantes deu 4/4 verde em 3 de 3 rodadas (vermelho só em
 * 20250 e 21000, que já não discriminam o conserto — ver a tabela abaixo). O
 * que a `ociosa()` cobre é CARGA. Medido 2026-09-17 com o mesmo comando, sob
 * a mesma carga de 5 processos mais 14 laços de CPU: com a lista ESTENDIDA
 * de 8 instantes, sem `ociosa`, 6 de 40 casos ficaram vermelhos (20000,
 * 20250 três vezes, 21000, 22000); com a lista REAL do arquivo (4
 * instantes), sem `ociosa`, 1 vermelho em 72 slots contra 0 em 60 com ela. O
 * efeito real de um `/auth/me` em voo quando o `runFor` avança não é travar
 * a página: é deslocar a fase da cadeia de polls em um passo. O gancho em
 * `window.fetch` (via `addInitScript`) conta requests de
 * `/auth/(me|validate)` em voo; `ociosa()` espera esse contador zerar — em
 * tempo REAL, fora do relógio falso — antes de cada `runFor`. Sem o gancho a
 * espera não tem o que observar e falha alto (contador ausente).
 *
 * **O passo pequeno é o que faz o caso medir.** A primeira versão deste teste
 * dava `runFor(19000)` de uma vez: nesse salto ainda cabia um poll DEPOIS do
 * flip, então o 1,2 s morto no fim da janela nunca era exercido e o caso
 * passava COM E SEM o conserto — tautológico, a 1ª das três regras do §3.
 *
 * LIMITE: com o relógio congelado durante toda request real, os orçamentos de
 * `_boundedAuthMe`/`_raceBudget` (a corrida contra timeout) nunca vencem a
 * corrida — este harness não mede timeout de rede real, só a decisão que
 * `awaitCheckoutConfirmation` toma depois da espera. Também não cobre
 * resposta `!ok`/nula do `/auth/me`: toda rota aqui responde 200 com corpo
 * válido, e uma mutação como `if (ultimo) me = ultimo;` → `me = ultimo;` (o
 * guard cai e `me` fica nulo quando a releitura falha/estoura) passa verde
 * neste arquivo — em produção isso deixaria ninguém redirecionado e reabriria
 * o bypass do `?upgrade=success` para o cortado, sem `_limparSnapshots()`.
 */
// Só a navegação (o redirect pra /precos, no meio do laço) é erro tolerado
// aqui; qualquer outro — página fechada, crash, erro de protocolo, bug no
// callback — sobe com o motivo, em vez de virar sucesso por acidente.
//
// As duas leituras de localização do laço abaixo usam `page.url()` direto —
// não executa JS, não lança, e não depende de TEXTO de erro do Playwright
// (travado em 1.62.1, `package.json` declara `^1.56.0`; observado
// 2026-09-17). Só dentro de `ociosa()` o `evaluate` é necessário (lê
// `window.__authEmVoo`); o porquê do `waitForURL` no catch dela está no
// comentário logo abaixo.
function localizacao(page) {
  const u = new URL(page.url());
  return { home: u.pathname.replace(/\.html$/, "") === "/home", url: u.pathname + u.search };
}
async function evaluateOuNavegou(page, fn) {
  try {
    return { navegou: false, valor: await page.evaluate(fn) };
  } catch (e) {
    // `page.url()` só reflete a URL nova quando o frame navega de fato, e sob
    // carga isso pode ficar defasado por um tempo real depois do contexto já
    // ter sido destruído (medido: `localizacao(page).home` ainda via "/home"
    // no instante do throw e um erro DE NAVEGAÇÃO virava falso-negativo).
    // `waitForLoadState` não serve aqui: se o novo ciclo de navegação ainda
    // não começou no rastreio do Playwright, ele acha que já está em "load"
    // (o estado do documento ANTIGO) e resolve na hora sem esperar nada.
    // `waitForURL` é diferente — espera o EVENTO de navegação de verdade, com
    // teto curto: se não navegar dentro dele, não era isso, sobe o erro.
    const saiu = await page
      .waitForURL((url) => url.pathname.replace(/\.html$/, "") !== "/home", { timeout: 5000 })
      .then(() => true, () => false);
    if (saiu) return { navegou: true };
    throw new Error(`evaluate falhou fora de navegação: ${e?.message ?? e}`);
  }
}

async function comWebhookEm(webhookMs) {
  let confirmado = false;
  const page = await abrirHome(null, async (p) => {
    await p.addInitScript(() => {
      window.__authEmVoo = 0;
      const nativo = window.fetch;
      window.fetch = function (input, init) {
        if (!/\/auth\/(me|validate)\b/.test(String(input?.url ?? input))) return nativo.call(this, input, init);
        window.__authEmVoo++;
        return nativo.call(this, input, init).then((r) => {
          // `loadAuthMe` (home.html) só lê `.json()` no caminho 200 — o
          // `if (!res.ok) return null;` devolve sem tocar no corpo. Sem este
          // ramo o contador vazava em toda resposta não-2xx (medido: rota500,
          // `/auth/me`→500, 30 s por caso antes deste fix).
          if (!r.ok) { window.__authEmVoo--; return r; }
          const ler = r.json.bind(r);
          r.json = () => ler().finally(() => { window.__authEmVoo--; });
          return r;
        }, (e) => { window.__authEmVoo--; throw e; });
      };
    });
    await p.clock.install();
    // Margem de orçamento de LATÊNCIA DE ENTREGA do comando (CDP), não de
    // simulação: `pauseAt(t)` lança "Cannot fast-forward to the past" se `t`
    // já passou no relógio real da página quando o comando chega. Medido
    // 2026-09-17 com `node --test --test-name-pattern="webhook em"
    // tests/frontend/home_upgrade_success_nao_isenta.test.mjs` sob a mesma
    // carga da doc da função acima: chamadas CDP de até 2337 ms (`runFor`) e
    // 1462 ms (`evaluate`) — 60 s cobre isso com folga e não custa nada
    // porque nenhum timer existe antes do `goto`.
    await p.clock.pauseAt(Date.now() + 60000);
    await p.route("**/auth/me", (route) => route.fulfill(json(
      confirmado ? { ...PAGO, app_access: true }
                 : { user_id: 1, plan: "free", plan_expires_at: null,
                     app_access: false })));
  });
  const ociosa = async () => {
    // Teto de tempo REAL, não de simulação — guarda de deadlock (contador
    // que nunca zera), não desempenho. 10 s dá ~7× de margem sobre os 1462 ms
    // de `evaluate` sob carga medidos acima; os 30 s antigos só existiam por
    // causa do vazamento do gancho, já corrigido acima. Pior caso real: um
    // erro que NÃO é navegação perto do fim do orçamento ainda paga o teto de
    // 5 s do `waitForURL` acima antes de subir, então este caminho de erro
    // pode levar até ~15 s — o veredito não muda, só o relógio de parede.
    const limite = Date.now() + 10000;
    for (;;) {
      const r = await evaluateOuNavegou(page, () => window.__authEmVoo);
      if (r.navegou) return; // trocando de documento — deixa o laço decidir
      if (typeof r.valor !== "number") throw new Error("contador de /auth/* ausente");
      if (r.valor === 0) return;
      if (Date.now() > limite) throw new Error(`contador de /auth/* não zerou (${r.valor})`);
      await new Promise((res) => setTimeout(res, 2));
    }
  };
  await ociosa();
  for (let t = 0; t < 40000; t += 250) {
    if (!confirmado && t >= webhookMs) confirmado = true;
    await page.clock.runFor(250);
    await ociosa();
    if (!localizacao(page).home) break;  // saiu de /home == navegou
  }
  const url = localizacao(page).url;
  await page.close();
  return url;
}

// O caso de DINHEIRO, e a tabela abaixo é MEDIDA em duas colunas, não esperada.
//
// Com o relógio congelado e a espera ociosa, o cronograma da página deixa de
// depender de latência real e vira CONTA sobre as constantes de `home.html`
// (o `wait(1500)` do polling e o deadline de 20000 ms em `_checkoutDeadline`):
// `/auth/me` em 0, 1.5, 3, …, 19.5 s; deadline cruzado em 20 s; a releitura
// final de `awaitCheckoutConfirmation` sai em 21 s (o `wait(1500)` que estava
// em curso quando o deadline foi cruzado termina de 19.5 + 1.5 = 21 s). Se
// essas duas constantes mudarem, os instantes abaixo têm de ser remedidos —
// e o mesmo vale para qualquer mudança na CADÊNCIA do polling, não só nas
// constantes: medido 2026-09-17 com `node --test --test-name-pattern="webhook
// em" tests/frontend/home_upgrade_success_nao_isenta.test.mjs` depois de trocar
// `wait(1500)` por `wait(Math.min(1500, deadline - Date.now()))` em
// `frontend/home.html` (refatoração legítima que não altera nenhuma das duas
// constantes, revertida com `git checkout -- frontend/home.html` em seguida):
// também desloca o caso de 20000 ms para vermelho.
//
// | webhook | sem o conserto | com o conserto |
// |---|---|---|
// | 1 s  | /home   | /home   |
// | 20 s | /precos | **/home**  ← é ESTE caso que mede o conserto |
// | 22 s | /precos | /precos |
// | 25 s | /precos | /precos |
// Medido 2026-09-17 com `node --test --test-name-pattern="webhook em"
// tests/frontend/home_upgrade_success_nao_isenta.test.mjs` (coluna "sem o
// conserto" com o bloco de releitura apagado de `awaitCheckoutConfirmation`,
// revertido com `git checkout -- frontend/home.html` em seguida).
//
// 1 s é o positivo (quem pagou e confirmou rápido não pode ser expulso); 22 s
// e 25 s prendem o RESÍDUO — o conserto estende a janela, não a elimina, e um
// "conserto" que mandasse todo mundo para /home passaria sem eles. 20 s é o
// único que muda de coluna porque cai exatamente entre o último poll regular
// (19.5 s) e a releitura (21 s): sem a releitura, o veredito fica preso no
// poll de 19.5 s, que ainda não viu o webhook.
//
// 19 s e 21 s saíram da lista anterior: 19 s é pego pelo poll de 19.5 s COM
// OU SEM o conserto (o webhook já está confirmado quando aquele poll roda, a
// releitura nem entra em jogo) e não discrimina; 21 s coincide com o instante
// da própria releitura, então o resultado depende só da ordem de operações
// dentro do laço de teste (flip de `confirmado` → `runFor` → `ociosa`), não
// do conserto em si.
//
// CONTROLE: apague o bloco `if (!_checkoutSettled(me)) { ... }` do fim de
// `awaitCheckoutConfirmation`. VERMELHO: `webhook em 20000 ms`. Os outros três
// ficam verdes — confirmado por medição.
for (const [ms, destinoEsperado] of [
  [1000, /^\/home/], [20000, /^\/home/], [22000, /^\/precos/],
  [25000, /^\/precos/],
]) {
  test(`webhook em ${ms} ms`, async () => {
    assert.match(await comWebhookEm(ms), destinoEsperado);
  });
}
