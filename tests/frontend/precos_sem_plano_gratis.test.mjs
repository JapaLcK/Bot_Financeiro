/**
 * /precos não oferece mais o plano Grátis.
 *
 * Assinar virou obrigatório pra entrar pela web, então o card do Grátis e os
 * dois CTAs `[data-free-cta]` saíram da página, junto com o `selectFree` que
 * batia em POST /billing/select-free. A COLUNA Grátis da tabela comparativa
 * também saiu — no #239 ela tinha ficado como informação, e o dono reverteu:
 * o Grátis não é mais nem uma escolha nem um degrau de comparação.
 *
 * O grupo tem os dois controles do CLAUDE.md §3:
 *   · asserção do conserto — nenhum CTA de Grátis, nenhum card "Grátis", e
 *     nenhuma requisição pro /billing/select-free em nenhum dos dois estados
 *     (deslogado e needs_plan_selection);
 *   · asserção do conserto na tabela — nenhum <th> "Grátis" e a contagem de
 *     colunas IGUAL (5) no colgroup, no thead, em CADA <tr> do tbody (contando
 *     colspan) e no tfoot;
 *   · asserção de ALINHAMENTO — o `left` de cada <th> do thead contra o de
 *     cada <td> do tfoot, listas idênticas. É a que pega o bug de verdade:
 *     esquecer um `colspan` ou uma célula desloca a tabela sem mudar contagem
 *     nenhuma que um teste de células veria;
 *   · copy do gate — as 6 células de {deslogado, logado sem gate, logado com
 *     gate} × {com ?escolha=1, sem marcador}: quem decide é o
 *     needs_plan_selection do /auth/me, não a URL (o marcador se perde num
 *     clique no "Planos" do próprio nav);
 *   · app iOS — mais 2 células com a UA PigBankApp: o gate vale lá também, e
 *     o mandato tem de aparecer. A isenção que routes/shared.py tinha era por
 *     substring de User-Agent, escolhida pelo cliente, e saiu;
 *   · controle POSITIVO — "Assinar Plus" ainda dispara EXATAMENTE 1
 *     POST /billing/create-checkout. Sem ele o grupo passaria numa página com
 *     todos os botões quebrados, que é pior que o bug.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

// UA do WebView do app (o `/PigBankApp/.test(navigator.userAgent)` de
// frontend/static/auth-refresh.js casa a SUBSTRING PigBankApp) — é o
// mecanismo real que liga window.PB_IN_APP, e não injeção da variável.
const APP_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
  + "AppleWebKit/605.1.15 Safari/604.1 PigBankApp/1.0";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Abre a /precos com o backend mockado. `me` vira o corpo do /auth/me
 * (`null` = 401, o visitante deslogado). Devolve a página e os contadores de
 * requisição por rota — a contagem é por INTERCEPTAÇÃO, não por efeito visível.
 */
async function abrirPrecos({ me = null, query = "", app = false,
                             viewport = { width: 1280, height: 900 },
                             plansConfig = { essencial_available: true, plus_available: true, pro_available: true },
                           } = {}) {
  const page = await browser.newPage({ viewport,
                                       ...(app ? { userAgent: APP_UA } : {}) });
  const chamadas = { selectFree: 0, checkout: 0 };
  const corposCheckout = [];

  await page.route("**/auth/me", (route) => (me
    ? route.fulfill({ contentType: "application/json", body: JSON.stringify(me) })
    : route.fulfill({ status: 401, contentType: "application/json", body: "{}" })));

  await page.route("**/billing/plans-config", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(plansConfig),
  }));

  await page.route("**/billing/subscription", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ active: false }),
  }));

  await page.route("**/billing/select-free", (route) => {
    chamadas.selectFree += 1;
    // 410 é o que o backend passou a devolver; o corpo segue o formato que o
    // front lê nas outras rotas de billing (detail.message).
    return route.fulfill({
      status: 410, contentType: "application/json",
      body: JSON.stringify({ detail: { error: "free_plan_discontinued", message: "indisponível" } }),
    });
  });

  await page.route("**/billing/create-checkout", (route) => {
    chamadas.checkout += 1;
    corposCheckout.push(JSON.parse(route.request().postData() || "{}"));
    // checkout_url pra uma página do próprio servidor: o startCheckout navega
    // no sucesso, e mandá-lo pra lugar nenhum deixaria o teste cego pro ramo
    // "Resposta inesperada do servidor".
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ checkout_url: `${ORIGIN}/precos.html?stripe=1` }),
    });
  });

  await page.goto(`${ORIGIN}/precos.html${query}`);
  await page.waitForSelector("#plans-v2 .plan");
  // O IIFE loadPlansState é assíncrono (plans-config + subscription) e o
  // applyOnboardingChoice que ele chamava era quem reescrevia os CTAs do
  // Grátis. Sem esperar, "não existe CTA" passaria antes de o JS rodar — e o
  // teste ficaria verde por corrida, não pelo conserto.
  await page.waitForTimeout(600);
  return { page, chamadas, corposCheckout };
}

// ── asserção do conserto ────────────────────────────────────────────────────

for (const [rotulo, ctx] of [
  ["deslogado", {}],
  ["needs_plan_selection", { me: { user_id: 42, needs_plan_selection: true }, query: "?escolha=1" }],
]) {
  test(`${rotulo}: nenhum CTA de Grátis e nenhum card Grátis na escada`, async () => {
    const { page, chamadas } = await abrirPrecos(ctx);

    assert.equal(await page.$$eval("[data-free-cta]", (e) => e.length), 0,
      "sobrou [data-free-cta] na página");

    const nomes = await page.$$eval("#plans-v2 .plan h3", (e) => e.map((h) => h.textContent.trim()));
    assert.deepEqual(nomes, ["Essencial", "Plus", "Pro", "Premium"]);

    // Qualquer clicável com a copy do Grátis, em QUALQUER lugar da página (a
    // classe, não o caso: o card era um, o rodapé da tabela era outro).
    const clicaveis = await page.$$eval("a, button", (els) =>
      els.map((e) => e.textContent.trim()).filter(Boolean));
    for (const proibido of ["Criar conta grátis", "Continuar com o plano Grátis", "Seu cadastro está pronto"]) {
      assert.ok(!clicaveis.includes(proibido),
        `clicável com a copy "${proibido}" ainda existe: ${JSON.stringify(clicaveis)}`);
    }

    assert.equal(chamadas.selectFree, 0, "alguém ainda bate no /billing/select-free");
    await page.close();
  });
}

// ── a coluna Grátis saiu da tabela comparativa ──────────────────────────────
// A tabela é o par obrigatório do CLAUDE.md §2: colgroup, thead, os 23 <tr> de
// dado, os 6 colspan das linhas de grupo e o tfoot só ficam alinhados se TODOS
// mudarem juntos. Uma célula sobrando desloca a tabela inteira.
const COLUNAS = 5;   // 1 de recursos + Essencial, Plus, Pro, Premium

/** Contagem de colunas de cada parte da tabela, contando `colspan`. */
function lerColunas(page) {
  return page.evaluate(() => {
    const somaLinha = (tr) => [...tr.children].reduce((a, c) => a + (c.colSpan || 1), 0);
    return {
      colgroup: document.querySelectorAll(".cmp-table colgroup col").length,
      thead: [...document.querySelectorAll(".cmp-table thead tr")].map(somaLinha),
      tbody: [...document.querySelectorAll(".cmp-table tbody tr")].map(somaLinha),
      tfoot: [...document.querySelectorAll(".cmp-table tfoot tr")].map(somaLinha),
      nomes: [...document.querySelectorAll(".cmp-table thead tr th")].map((t) => t.textContent.trim()),
      celulas: [...document.querySelectorAll(".cmp-table tfoot tr td")].map((t) => t.textContent.trim()),
    };
  });
}

test("a tabela comparativa não tem mais coluna Grátis, e as 5 colunas fecham", async () => {
  const { page } = await abrirPrecos();
  const t = await lerColunas(page);

  // O conserto: nenhum cabeçalho de plano fala em Grátis.
  assert.ok(!t.nomes.some((n) => n.includes("Grátis")),
    `thead ainda tem Grátis: ${JSON.stringify(t.nomes)}`);
  assert.deepEqual(t.nomes.slice(1).map((n) => n.split(/R\$|Em breve/)[0].replace("Mais popular", "").trim()),
    ["Essencial", "Plus", "Pro", "Premium"], `cabeçalhos: ${JSON.stringify(t.nomes)}`);

  // Consistência: a MESMA contagem em todas as partes, e uma menos que as 6 de
  // antes do conserto (colgroup 6, thead 6, cada tbody 6, tfoot 6 — medido em
  // 7a87ae7). Cada linha entra na asserção, não só uma amostra.
  assert.equal(t.colgroup, COLUNAS, `colgroup com ${t.colgroup} <col>`);
  assert.deepEqual(t.thead, [COLUNAS], `thead: ${JSON.stringify(t.thead)}`);
  assert.equal(t.tbody.length, 29, `tbody com ${t.tbody.length} linhas (23 de dado + 6 de grupo)`);
  assert.deepEqual([...new Set(t.tbody)], [COLUNAS],
    `linhas do tbody fora das ${COLUNAS} colunas: ${JSON.stringify(t.tbody)}`);
  assert.deepEqual(t.tfoot, [COLUNAS], `tfoot: ${JSON.stringify(t.tfoot)}`);

  // Rodapé: rótulo vazio + os 4 CTAs, sem célula órfã do Grátis no meio.
  assert.deepEqual(t.celulas, ["", "Assinar Essencial", "Assinar Plus", "Assinar Pro", "Em breve"]);
  await page.close();
});

// A asserção que um teste de contagem de células NUNCA pegaria: se thead e
// tfoot discordarem de uma célula, os `left` deslizam e o CTA do Plus fica
// embaixo da coluna do Pro. Nos dois viewports porque a tabela troca de regime
// aos 900px (scroller horizontal + min-width no celular, fixed no desktop).
for (const viewport of [{ width: 390, height: 844 }, { width: 1560, height: 900 }]) {
  test(`thead e tfoot alinhados coluna a coluna em ${viewport.width}x${viewport.height}`, async () => {
    const { page } = await abrirPrecos({ viewport });
    const { thead, tfoot } = await page.evaluate(() => {
      const lefts = (sel) => [...document.querySelectorAll(sel)]
        .map((e) => +e.getBoundingClientRect().left.toFixed(1));
      return { thead: lefts(".cmp-table thead tr th"), tfoot: lefts(".cmp-table tfoot tr td") };
    });
    assert.equal(thead.length, COLUNAS, `thead com ${thead.length} células`);
    assert.deepEqual(tfoot, thead,
      `rodapé desalinhado do cabeçalho: thead=${JSON.stringify(thead)} tfoot=${JSON.stringify(tfoot)}`);
    await page.close();
  });
}

// Controle POSITIVO da remoção: os dados dos planos PAGOS continuam na tabela,
// nas colunas certas. Sem ele, uma tabela que perdeu a coluna errada (ou duas)
// passaria nas asserções de contagem acima.
test("controle positivo: os dados dos planos pagos seguem nas colunas certas", async () => {
  const { page } = await abrirPrecos();
  // O ` ` dos `&nbsp;` do HTML ("Open&nbsp;Finance") não é o espaço que se
  // digita aqui: sem normalizar, o `find` não acha a linha e o teste passaria a
  // estourar em vez de comparar.
  const linha = (nome) => page.evaluate((n) => {
    const txt = (e) => e.textContent.replace(/\u00a0/g, " ").trim();
    const th = [...document.querySelectorAll(".cmp-table tbody th.cmp-feat")].find((t) => txt(t) === n);
    if (!th) throw new Error(`linha "${n}" não existe na tabela`);
    return [...th.parentElement.querySelectorAll("td")].map(txt);
  }, nome);

  // Valores de core/services/plan_limits.py, na ordem Essencial/Plus/Pro/Premium.
  assert.deepEqual(await linha("Lançamentos por mês"),
    ["Ilimitados", "Ilimitados", "Ilimitados", "Ilimitados"]);
  assert.deepEqual(await linha("Histórico que você enxerga"),
    ["90 dias", "12 meses", "24 meses", "Completo"]);
  assert.deepEqual(await linha("Mensagens com a Piggy"),
    ["200por mês", "1.000por mês", "1.000por mês", "1.000por mês"]);
  assert.deepEqual(await linha("Bancos conectados (Open Finance)"),
    ["1", "2", "5", "Ilimitados"]);
  await page.close();
});

// ── copy do subtítulo: as 6 células de estado × marcador ────────────────────
// A copy do gate é decidida pelo needs_plan_selection do /auth/me, não pelo
// ?escolha=1: o marcador se perde num clique no "Planos" do próprio nav, no
// gate_pro_page (routes/shared.py) e nos redirects de /conta. Então as 6
// células = 3 estados × {com marcador, sem marcador}, e a coluna do marcador
// não muda NENHUMA linha — é isso que o teste fixa.
//
// A célula que discrimina é "logado com gate SEM marcador": com a decisão pela
// URL ela mostrava a copy de visitante a quem está travado no gate. As duas
// "sem gate + COM marcador" são o outro lado: marcador velho (bookmark, link
// compartilhado) não pode inventar gate pra quem já pagou nem pra visitante.
const COPY_PADRAO = /^15 dias grátis/;
const COPY_GATE = /Escolha um plano pra continuar/;

for (const [estado, me] of [
  ["deslogado", null],
  ["logado sem gate", { user_id: 42, needs_plan_selection: false }],
  ["logado com gate", { user_id: 42, needs_plan_selection: true }],
]) {
  for (const query of ["?escolha=1", ""]) {
    const esperado = me && me.needs_plan_selection ? COPY_GATE : COPY_PADRAO;
    const rotulo = query ? "com marcador" : "sem marcador";
    test(`copy do subtítulo: ${estado}, ${rotulo}`, async () => {
      const { page } = await abrirPrecos({ me, query });
      const sub = await page.textContent("#precos-sub");
      assert.match(sub, esperado,
        `${estado} ${rotulo} devia bater ${esperado} e leu: "${sub}"`);
      if (esperado === COPY_GATE) {
        // A copy do gate é NOSSA (941ffc0) e não pode garantir quando a
        // cobrança vem: quem recria a conta com um telefone já em plan_trials
        // é inelegível e o checkout sai com trial_days=0 (Codex, #239). A
        // oferta fica (o COPY_GATE acima + os "15 dias grátis" daqui são o
        // controle positivo: sem eles o caso passaria num subtítulo esvaziado).
        for (const garantia of ["não paga nada", "Nada é cobrado agora",
                                "primeira cobrança só vem depois"]) {
          assert.ok(!sub.includes(garantia),
            `a copy do gate garante a cobrança (${garantia}): "${sub}"`);
        }
        assert.ok(sub.includes("15 dias grátis") && sub.includes("checkout"),
          `a copy do gate perdeu a oferta ou a deferência ao checkout: "${sub}"`);
      }
      await page.close();
    });
  }
}

// ── app iOS: o gate passou a valer lá também, então a copy de mandato é certa ─
// A isenção de routes/shared.py era decidida por substring de User-Agent, que o
// cliente escolhe: qualquer conta web entrava sem plano mandando "PigBankApp".
// Ela saiu, e com ela a guarda `|| window.PB_IN_APP` desta página. Quem abre a
// /precos de dentro do app (pelo "Ver planos" .pb-keep-in-app do paywall,
// `#upg-cta` em frontend/dashboard.html) está travado como qualquer um, e
// esconder o mandato deixaria a tela sem explicar por que o dashboard não abre.
// 2 células, não 6: as 4 de {deslogado, logado sem gate} × marcador saem da
// função pelo mesmo `!me.needs_plan_selection`, com e sem app. A coluna do
// marcador fica porque é o mesmo invariante das células web: a URL não decide.
for (const query of ["?escolha=1", ""]) {
  const rotulo = query ? "com marcador" : "sem marcador";
  test(`copy do subtítulo: app iOS, logado com gate, ${rotulo}`, async () => {
    const { page } = await abrirPrecos({
      me: { user_id: 42, needs_plan_selection: true }, query, app: true,
    });
    assert.equal(await page.evaluate(() => window.PB_IN_APP === true), true,
      "a UA do app não ligou window.PB_IN_APP — a célula não mediria o app");
    const sub = await page.textContent("#precos-sub");
    assert.match(sub, COPY_GATE,
      `app iOS ${rotulo} devia ler o mandato e leu: "${sub}"`);
    await page.close();
  });
}

// ── o CORTE DO GRÁTIS: um terceiro estado de gate, e a copy dele ────────────
// Quem foi cortado tem `needs_plan_selection` FALSE (escolheu um plano um dia)
// e `app_access` FALSE. Antes deste PR ele caía na COPY_PADRAO — "15 dias
// grátis pra testar" —, falsa duas vezes: ele não está escolhendo pela primeira
// vez, e o trial é UM por telefone na vida, então o ex-assinante que já o
// queimou não ganha outro.
//
// Decidido pelo /auth/me e NUNCA pela URL, igual às células acima: as duas
// colunas de marcador têm de dar a mesma linha.
//
// Os dois VIEWPORTS existem porque a mudança é de texto num subtítulo, e
// subtítulo mais longo é exatamente o que estoura a caixa no celular: além da
// copy, cada célula mede a largura do #precos-sub contra a do container.
const COPY_CORTE = /Sua conta está sem plano ativo/;
const TELAS = [["desktop", { width: 1280, height: 900 }],
               ["mobile", { width: 390, height: 844 }]];

for (const [tela, viewport] of TELAS) {
  for (const query of ["?escolha=1", ""]) {
    const rotulo = query ? "com marcador" : "sem marcador";
    test(`copy do subtítulo: cortado no fim do Grátis, ${tela}, ${rotulo}`, async () => {
      const { page } = await abrirPrecos({
        me: { user_id: 42, needs_plan_selection: false, app_access: false },
        query, viewport,
      });
      const sub = await page.textContent("#precos-sub");
      assert.match(sub, COPY_CORTE,
        `cortado ${tela} ${rotulo} devia ler a copy do corte e leu: "${sub}"`);
      // Não promete NEM nega o período grátis: quem diz é o checkout, que lê a
      // mesma is_trial_eligible_for_user (§0.7). Prometer aqui é a mentira que
      // esta célula existe para consertar.
      assert.ok(!sub.includes("15 dias grátis"),
        `a copy do corte prometeu o trial a um ex-assinante: "${sub}"`);
      assert.ok(sub.includes("checkout"),
        `a copy do corte perdeu a deferência ao checkout: "${sub}"`);

      // Overflow: o subtítulo tem de caber na largura do pai, nos dois tamanhos.
      // É a classe de bug que uma troca de TEXTO causa — a copy do corte é a
      // mais longa das três da página.
      //
      // Aqui havia também `documentElement.scrollWidth <= clientWidth`, e ela
      // SAIU por medição, não por conveniência: em 390px ela dá 544 nos TRÊS
      // estados do subtítulo (visitante, gate de escolha, corte), porque quem
      // rola é a tabela comparativa — de propósito, e o caso "dica de arrastar
      // — 390px" logo abaixo existe para exigir que ela role. Um número igual
      // com e sem a mudança não mede nada (§3).
      const medida = await page.evaluate(() => {
        const el = document.getElementById("precos-sub");
        return { sub: el.scrollWidth, caixa: el.parentElement.clientWidth,
                 altura: el.getBoundingClientRect().height };
      });
      assert.ok(medida.sub <= medida.caixa + 1,
        `${tela}: o subtítulo tem ${medida.sub}px numa caixa de ${medida.caixa}px`);
      await page.close();
    });
  }
}

test("cortado que TAMBÉM não escolheu plano lê a copy da escolha, não a do corte", async () => {
  // Não é um canto: é o caso COMUM da página. Todo cadastro novo que ainda não
  // passou pelo checkout tem needs_plan_selection true E app_access false, e o
  // que o manda para a copy da escolha é a ORDEM do if (o `return` da primeira
  // perna). Este caso fixa a ordem; sem ele, inverter o if diria "sua conta
  // está sem plano ativo" a quem nunca teve um.
  const { page } = await abrirPrecos({
    me: { user_id: 42, needs_plan_selection: true, app_access: false },
  });
  assert.match(await page.textContent("#precos-sub"), COPY_GATE);
  await page.close();
});

test("pagante continua lendo a copy padrão (app_access true)", async () => {
  // POSITIVO da perna nova: sem ele, um `me.app_access === false` escrito como
  // `!me.app_access` passaria verde e trocaria a copy de todo mundo que o
  // /auth/me responde sem o campo.
  const { page } = await abrirPrecos({
    me: { user_id: 42, needs_plan_selection: false, app_access: true },
  });
  assert.match(await page.textContent("#precos-sub"), COPY_PADRAO);
  await page.close();
});


// ── controle POSITIVO: o caminho legítimo continua funcionando ───────────────

test("controle positivo: 'Assinar Plus' dispara exatamente 1 POST /billing/create-checkout", async () => {
  const { page, chamadas, corposCheckout } = await abrirPrecos({
    me: { user_id: 42, needs_plan_selection: true }, query: "?escolha=1",
  });
  await Promise.all([
    page.waitForURL(/stripe=1/, { timeout: 5000 }),
    page.click('#plans-v2 [data-plan-btn="plus"]'),
  ]);
  assert.equal(chamadas.checkout, 1, `foram ${chamadas.checkout} POSTs de checkout`);
  assert.deepEqual(corposCheckout[0], { interval: "monthly", plan: "plus" });
  await page.close();
});

test("controle positivo: os 6 CTAs pagos continuam habilitados (card e tabela)", async () => {
  // Clicar nos seis não dá: o primeiro clique bem-sucedido NAVEGA pro Stripe.
  // Então a prova de "não quebrei os outros" é o estado do DOM — o clique de
  // verdade é o teste acima. Cada plano pago tem DOIS botões (card + rodapé da
  // tabela) e o do rodapé já tinha ficado clicável rumo ao erro uma vez.
  const { page } = await abrirPrecos({ me: { user_id: 42, needs_plan_selection: true } });
  const estado = await page.$$eval("[data-plan-btn]", (els) => els.map((e) => ({
    plano: e.dataset.planBtn,
    onde: e.closest("#plans-v2") ? "card" : "tabela",
    desabilitado: e.disabled === true,
    texto: e.textContent.trim(),
  })));
  assert.deepEqual(estado, [
    { plano: "essencial", onde: "card",   desabilitado: false, texto: "Assinar Essencial" },
    { plano: "plus",      onde: "card",   desabilitado: false, texto: "Assinar Plus" },
    { plano: "pro",       onde: "card",   desabilitado: false, texto: "Assinar Pro" },
    { plano: "essencial", onde: "tabela", desabilitado: false, texto: "Assinar Essencial" },
    { plano: "plus",      onde: "tabela", desabilitado: false, texto: "Assinar Plus" },
    { plano: "pro",       onde: "tabela", desabilitado: false, texto: "Assinar Pro" },
  ]);
  await page.close();
});

// ── degradação parcial do Stripe: só o Plus sem price configurado ───────────
// Sem o Grátis na página, um botão do Plus clicável só produz um toast de erro
// e nenhuma saída. O par positivo deste caso é o teste dos 6 CTAs habilitados
// acima: lá o mesmo DOM, com plus_available:true, tem os 6 clicáveis.
test("plus_available:false marca EXATAMENTE os 2 botões do Plus como indisponíveis", async () => {
  const { page } = await abrirPrecos({
    me: { user_id: 42, needs_plan_selection: true },
    plansConfig: { essencial_available: true, plus_available: false, pro_available: true },
  });
  const estado = await page.$$eval("[data-plan-btn]", (els) => els.map((e) => ({
    plano: e.dataset.planBtn,
    onde: e.closest("#plans-v2") ? "card" : "tabela",
    desabilitado: e.disabled === true,
    texto: e.textContent.trim(),
  })));
  assert.deepEqual(estado, [
    { plano: "essencial", onde: "card",   desabilitado: false, texto: "Assinar Essencial" },
    { plano: "plus",      onde: "card",   desabilitado: true,  texto: "Indisponível" },
    { plano: "pro",       onde: "card",   desabilitado: false, texto: "Assinar Pro" },
    { plano: "essencial", onde: "tabela", desabilitado: false, texto: "Assinar Essencial" },
    { plano: "plus",      onde: "tabela", desabilitado: true,  texto: "Indisponível" },
    { plano: "pro",       onde: "tabela", desabilitado: false, texto: "Assinar Pro" },
  ]);
  await page.close();
});

// ── a dica de arrastar só aparece onde a tabela realmente rola ───────────────
// O media query de 900px não sabe medir overflow. Com 4 colunas a tabela cabe
// a partir de ~610px, então entre ~610 e 900 a página pedia pra arrastar o que
// não arrasta — faixa que ESTE PR alargou (antes o corte era ~712px). Quem sabe
// é o updateCmpFade, que já mede o mesmo `max > 2` dos véus das bordas.
//
// O par é obrigatório: sem o caso de 390px, a asserção passaria numa página que
// escondeu a dica pra sempre, que é pior que mostrá-la demais.
for (const [rotulo, largura, deveAparecer] of [
  ["390px: a tabela rola, a dica aparece", 390, true],
  ["800px: a tabela cabe, a dica some", 800, false],
]) {
  test(`dica de arrastar — ${rotulo}`, async () => {
    const { page } = await abrirPrecos({ viewport: { width: largura, height: 844 } });

    const { oculto, visivel } = await page.evaluate(() => {
      const sc = document.getElementById("cmp-scroll");
      const hint = document.querySelector(".cmp-hint");
      return {
        oculto: sc.scrollWidth - sc.clientWidth,
        // getComputedStyle, não a classe: é o que o usuário enxerga.
        visivel: getComputedStyle(hint).display !== "none",
      };
    });

    // Âncora do próprio caso: se o overflow não for o esperado, a asserção de
    // baixo mediria outra coisa e passaria por acidente.
    assert.equal(oculto > 2, deveAparecer,
      `${largura}px devia ${deveAparecer ? "" : "não "}ter overflow e tem ${oculto}px`);
    assert.equal(visivel, deveAparecer,
      `${largura}px: overflow=${oculto}px mas a dica está ${visivel ? "visível" : "escondida"}`);
    await page.close();
  });
}

// ── A NOTA DOS PLANOS, o SEGUNDO texto que prometia o trial ─────────────────
//
// A perna do corte reescrevia só o #precos-sub. Três parágrafos abaixo dele o
// #plans-note-v2 continuava dizendo "Todo plano vem com 15 dias grátis pra
// testar (um teste por número)" — a mesma promessa falsa, na mesma tela, para
// o mesmo ex-assinante cujo telefone já queimou o trial.
//
// CONTROLE DECLARADO (`docs/controles_declarados.md`) — em `precos.html`, na
// perna `me.app_access === false`, troque o `if (nota)` por `if (false)` (o
// bloco continua lá e deixa de agir; nada é apagado). VERMELHOS:
//   `a nota dos planos não promete trial ao cortado — desktop`
//   `a nota dos planos não promete trial ao cortado — mobile`
// Direção: promessa falsa de trial a quem já o usou.
//
// Positivo do PAR, VERDE sob a injeção (é o que o torna positivo):
//   `a nota dos planos continua prometendo o trial a quem não foi cortado`
const NOTA = "#plans-note-v2";

for (const [tela, viewport] of TELAS) {
  test(`a nota dos planos não promete trial ao cortado — ${tela}`, async () => {
    const { page } = await abrirPrecos({
      me: { user_id: 42, needs_plan_selection: false, app_access: false },
      viewport,
    });
    const nota = await page.textContent(NOTA);

    assert.ok(!nota.includes("15 dias grátis"),
      `a nota prometeu o trial a um ex-assinante: "${nota}"`);
    assert.ok(!/\btrial\b/i.test(nota),
      `a nota ainda pressupõe um trial que pode não existir: "${nota}"`);
    // As duas frases que continuam verdadeiras para todo mundo têm de ficar —
    // sem elas o conserto tiraria informação de pagamento em vez de corrigir
    // uma promessa.
    assert.ok(nota.includes("Stripe"), `a nota perdeu a frase da Stripe: "${nota}"`);
    assert.ok(nota.includes("nunca vê os dados do seu cartão"),
      `a nota perdeu a garantia sobre o cartão: "${nota}"`);
    assert.ok(nota.includes("checkout"),
      `a nota perdeu a deferência ao checkout: "${nota}"`);

    // O ícone sobrevive à troca do texto: `textContent =` apaga os filhos, e
    // sem o `prepend` a nota ficaria sem o glifo — some calado (§5).
    const icone = await page.getAttribute(`${NOTA} i`, "class");
    assert.equal(icone, "ph ph-info", `a nota ficou sem ícone: ${icone}`);

    // Não vaza da caixa em nenhuma das duas larguras.
    const medida = await page.$eval(NOTA, (el) => ({
      largura: el.scrollWidth, caixa: el.parentElement.clientWidth,
    }));
    assert.ok(medida.largura <= medida.caixa + 1,
      `${tela}: a nota tem ${medida.largura}px numa caixa de ${medida.caixa}px`);
    await page.close();
  });
}

test("a nota dos planos continua prometendo o trial a quem não foi cortado", async () => {
  // POSITIVO: sem ele, apagar a promessa do HTML estático passaria verde e
  // todo visitante deixaria de ler a oferta que a página existe para fazer.
  const { page } = await abrirPrecos({
    me: { user_id: 42, needs_plan_selection: false, app_access: true },
  });
  const nota = await page.textContent(NOTA);
  assert.ok(nota.includes("15 dias grátis"),
    `a nota deixou de oferecer o trial a quem pode tê-lo: "${nota}"`);
  await page.close();
});

test("visitante deslogado (sem /auth/me) continua lendo a nota original", async () => {
  // A perna só roda com `me`; deslogado é a maioria da página e não pode mudar.
  const { page } = await abrirPrecos();
  assert.ok((await page.textContent(NOTA)).includes("15 dias grátis"));
  await page.close();
});
