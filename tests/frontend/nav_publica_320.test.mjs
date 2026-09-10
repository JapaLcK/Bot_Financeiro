/**
 * A barra pública (`.nav`) cabendo em UMA linha a 320px, com os alvos de toque
 * em 44px — deslogado e logado.
 *
 * Não existia teste da nav pública: `pb_nav_gate` e `pb_nav_ios14` são sobre o
 * motor de navegação SPA (`pb-nav.js`), não sobre esta barra.
 *
 * COMO SE CONTA UMA LINHA (o detector ingênuo mente): agrupar os filhos
 * visíveis de `.nav` pelo CENTRO vertical, com tolerância de 12px, E cruzar com
 * a altura da nav — as duas medidas têm de concordar. Agrupar por `top` dá
 * "3 linhas" numa barra que cabe numa só, porque itens de alturas diferentes
 * (logo 30px, CTA 40px, burger 44px) começam em `top` diferente na MESMA linha.
 *
 * O QUE `document.fonts.ready` GARANTE, E O QUE NÃO: só que o navegador terminou
 * de RESOLVER as fontes — não que a Inter chegou. Com a fonte abortada ele
 * resolve igual (status `loaded`, faces em `error`) e a medição sai no fallback
 * sem aviso; com a fonte atrasada, o `page.goto` já espera. A espera é barata e
 * correta, mas quem protege o número é o ambiente ter a Inter — e a diferença de
 * métrica inverte o resultado. Medido a 320px, com o conserto aplicado:
 *     Inter (real)     1 linha, sobra   5,8
 *     Liberation Sans  1 linha, sobra  15,6
 *     DejaVu Sans      2 linhas, sobra -5,6   <- fallback de Linux
 * No cenário DejaVu o caso 7 (341px) também cai, com -2,6. Se estes dois casos
 * ficarem vermelhos num runner novo, suspeite da fonte ANTES de suspeitar da CSS.
 *
 * Os dois controles do CLAUDE.md §3, para o GRUPO:
 *  · negativo — DEZ mutações de código, cada uma injetada num caso que estava
 *    VERDE. Não são dez casos distintos: três caem todas no 8 e duas no 2 —
 *    o que importa é que nenhuma delas passa despercebida, não a bijeção.
 *      texto do CTA de volta para "Começar agora" -> 1, 6, 7 e 11
 *      remover o bloco @media 340                 -> 1, 2, 6 e 8
 *      reverter os dois valores do bloco 480      -> 7
 *      remover os três `min-height:44px`          -> 5
 *      `.nav.nav` reescrito como `.nav`           -> 8
 *      apagar a declaração `column-gap:4px`       -> 8
 *      remover o clamp de largura do .pb-acct-dd  -> 10
 *      clamp com `57px` no lugar de `71px`        -> 10
 *      apagar o padding do `.nav .pb-acct-btn`    -> 2
 *      logo de 30px para 12px (proporcional)      -> 9 e 11
 *    E o caso 2 tem guarda própria (11ª mutação, no teste e não no CSS):
 *    trocando o rótulo do mock pelo placeholder "Minha conta" ele fica VERMELHO,
 *    o que prova que ele mede o rótulo real.
 *    Três casos nasceram de mutação que não vermelhava: o 7 (o bloco de 480 é
 *    invisível a 320, porque o de 340 sobrescreve os dois valores), o 8 (a sobra
 *    não discrimina a especificidade) e o 11 (a proporção não discrimina
 *    encolhimento proporcional). E o 10 só passou a cobrir o respiro de 8px
 *    depois de amostrar 341-358, a faixa de padding 12px.
 *  · positivo — os casos 3 (375/390) e 4 (1280) provam que o que já funcionava
 *    continua funcionando: o conserto aperta espaçamento, e um aperto sem
 *    controle positivo passaria igual num CSS que espremesse o desktop também.
 *
 * O que este arquivo NÃO alcança (CLAUDE.md §3.2): o render ANTES de a Inter
 * carregar; aparelho real (WKWebView do app, Chrome do Android — SF Pro e
 * Roboto não existem neste Chromium); `env(safe-area-inset-*)`, que vale 0 no
 * headless (§6) — e como a PAISAGEM no iPhone está habilitada
 * (`docs/armadilhas.md`), os insets LATERAIS também contam de verdade e também
 * valem 0 aqui; e o app carrega o site AO VIVO, então nada disto aparece no
 * aparelho antes do deploy (§5).
 *
 * COBERTURA POR PÁGINA, explícita: os casos abrem só `/index.html` e
 * `/precos.html`. As 16 páginas que carregam esta `.nav` foram medidas À MÃO a
 * 320/341/360/375 e a 320 logado — todas em 1 faixa, 66px, nada fora da tela
 * (`login.html` logado não entra na conta: ele redireciona para `/home` antes de
 * renderizar a barra). Essa medição é de bancada e NÃO está travada por teste:
 * as outras 14 compartilham a marcação da nav mas diferem no conteúdo de
 * `.nav-links`, então uma mudança de conteúdo lá não acende luz nenhuma aqui.
 *
 * O caso 11 roda só DESLOGADO, que é onde o esmagamento existia.
 *
 * E o conserto a 320px vale com font-size raiz de 16px (o default): medido, a
 * 17px a barra deslogada já volta a 2 faixas (-0,2) e a 18px o 341 cai junto.
 * Não é regressão — a base era pior nas mesmas condições — mas "cabe a 320px"
 * não é verdade para quem aumentou a fonte do navegador.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/**
 * Abre `rota` na largura `w`. Com `logado`, o `/auth/validate` responde ok e o
 * `nav-auth.js` troca "Entrar / Criar conta" pelo menu da conta.
 */
async function abrirNav(w, { logado = false, rota = "/index.html", rotulo = "Ana Beatriz" } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: 720 } });
  if (logado) {
    await ctx.route("**/auth/validate", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' }));
    // SEM este mock o rótulo fica no placeholder "Minha conta" e o teste afirma
    // o que o produto não entrega: quem está logado vê `display_name` (ou o
    // e-mail encurtado por shortAccount, até 18 caracteres). Ver o cabeçalho.
    await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ email: "ana.beatriz@gmail.com", display_name: rotulo, plan: "free" }) }));
  }
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}${rota}`);
  // o burger é injetado pelo nav-auth.js; o menu da conta, só depois do fetch.
  // `attached`, não visível: no desktop o burger existe no DOM com display:none,
  // e esperar por visibilidade faria o caso de 1280 morrer de timeout.
  await page.waitForSelector(logado ? ".pb-acct-btn" : ".pb-burger", { state: "attached" });
  // o rótulo chega num 2º fetch: medir antes dele é medir o placeholder
  if (logado) await page.waitForFunction(
    (n) => document.querySelector(".pb-acct-lbl").textContent === n, rotulo);
  await page.evaluate(() => document.fonts.ready);   // ver cabeçalho
  return { ctx, page };
}

/** Faixas por centro vertical + altura da nav + sobra da linha. */
const medir = (page) => page.evaluate(() => {
  const nav = document.querySelector(".nav"), cs = getComputedStyle(nav);
  const itens = [...nav.children].filter((x) => x.getBoundingClientRect().width > 0);
  const caixa = nav.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const gap = parseFloat(cs.columnGap) || 0;
  const pede = itens.reduce((s, x) => s + x.getBoundingClientRect().width, 0) + gap * (itens.length - 1);
  const faixas = [];
  for (const x of itens) {
    const b = x.getBoundingClientRect(), meio = (b.top + b.bottom) / 2;
    if (!faixas.some((f) => Math.abs(f - meio) < 12)) faixas.push(meio);
  }
  const alvo = (s) => { const e = document.querySelector(s); if (!e) return null;
    const b = e.getBoundingClientRect(); return { w: +b.width.toFixed(1), h: +b.height.toFixed(1) }; };
  return {
    faixas: faixas.length,
    navH: +nav.getBoundingClientRect().height.toFixed(1),
    sobra: +(caixa - pede).toFixed(1),
    columnGap: cs.columnGap,   // quem venceu a cascata: a site.css ou o #pb-nav-css
    rotulo: (document.querySelector(".pb-acct-lbl") || {}).textContent || null,
    // nenhum item pode estar fora da viewport: cabe em 1 linha != cabe na tela
    fora: itens.filter((x) => x.getBoundingClientRect().right > innerWidth + 0.5).length,
    alvos: { cta: alvo(".nav-right .btn-primary"), entrar: alvo(".nav-right .link-entrar"),
             burger: alvo(".pb-burger"), conta: alvo(".pb-acct-btn") },
  };
});

/** Uma faixa, altura de barra de uma linha (66px), sobra > `sobraMin`, nada fora.
    `sobraMin` existe porque "cabe" por 0,8px não é caber: ver o caso 2. */
function umaFaixa(m, ctx, sobraMin = 0) {
  assert.equal(m.faixas, 1, `${ctx}: ${m.faixas} faixas (nav ${m.navH}px, sobra ${m.sobra})`);
  assert.equal(m.navH, 66, `${ctx}: nav com ${m.navH}px — 66 é a barra de uma linha`);
  assert.ok(m.sobra > sobraMin, `${ctx}: sobra ${m.sobra}px na linha (mínimo ${sobraMin})`);
  assert.equal(m.fora, 0, `${ctx}: ${m.fora} item(ns) além da viewport`);
}

test("1) 320 deslogado: a barra cabe em uma faixa de 66px", async () => {
  const { ctx, page } = await abrirNav(320);
  const m = await medir(page);
  // no head: 2 faixas, nav 96px, sobra -54,2 (medido antes do conserto)
  umaFaixa(m, "320 deslogado");
  assert.equal(await page.$eval(".nav-right .btn-primary", (e) => e.textContent.trim()), "Criar conta");
  await ctx.close();
});

test("2) 320 logado, com rótulo de perfil real, cabe em uma faixa", async () => {
  const { ctx, page } = await abrirNav(320, { logado: true });
  const m = await medir(page);
  // guarda: sem o mock do /auth/dashboard-profile isto seria "Minha conta" e o
  // caso mediria o placeholder, não o que o usuário vê
  assert.equal(m.rotulo, "Ana Beatriz", "o rótulo do perfil não chegou ao botão");
  // no head: 2 faixas, nav 103px, sobra -17,2 (com este mesmo rótulo, -11,2)
  // A sobra mínima de 4px cobre `.nav .pb-acct-btn{padding-left/right:10px}` do
  // bloco de 340, que de outro modo não tem UMA asserção: apagando a regra a
  // sobra aqui cai de 10,8 para 0,8 e a barra "cabe" pela beira, sem que nada
  // reclame. Com o placeholder (rótulo mais largo) ela cai para -5,2, 2 faixas.
  umaFaixa(m, "320 logado", 4);
  await ctx.close();
});

test("3) 375 e 390 deslogado seguem em uma faixa (controle positivo)", async () => {
  for (const w of [375, 390]) {
    const { ctx, page } = await abrirNav(w);
    umaFaixa(await medir(page), `${w} deslogado`);
    await ctx.close();
  }
});

test("4) 1280 deslogado: o desktop continua intocado (controle positivo)", async () => {
  const { ctx, page } = await abrirNav(1280);
  const m = await medir(page);
  assert.equal(m.faixas, 1, `desktop em ${m.faixas} faixas`);
  assert.equal(m.navH, 69.6, `a nav de desktop mudou de altura: ${m.navH}px`);
  assert.equal(m.alvos.burger.h, 0, "o burger apareceu no desktop");
  await ctx.close();
});

test("5) alvos de toque de 44px a 320 e a 375", async () => {
  for (const w of [320, 375]) {
    const { ctx, page } = await abrirNav(w);
    const { alvos } = await medir(page);
    // no head: CTA 137x40 e Entrar 44x21 — dois dos três abaixo de 44
    for (const nome of ["cta", "entrar", "burger"])
      assert.ok(alvos[nome].h >= 44,
        `${w} deslogado: ${nome} com ${alvos[nome].h}px de altura (mínimo 44)`);
    await ctx.close();

    // o menu da conta é o terceiro alvo, e só existe logado. Na base, com o
    // MESMO rótulo real que este arquivo usa: 148×37 (o 154×37 que já esteve
    // escrito aqui era a medição com o placeholder "Minha conta").
    // Depois: 138×44 a 320 e 148×44 a 375.
    const log = await abrirNav(w, { logado: true });
    const { alvos: a2 } = await medir(log.page);
    assert.ok(a2.conta.h >= 44, `${w} logado: menu da conta com ${a2.conta.h}px (mínimo 44)`);
    await log.ctx.close();
  }
});

test("6) /precos.html a 320 também cabe (é categoria, não a index)", async () => {
  const { ctx, page } = await abrirNav(320, { rota: "/precos.html" });
  umaFaixa(await medir(page), "320 deslogado /precos.html");
  await ctx.close();
});

/* 341px é o ponto mais apertado ACIMA do @media 340 — e o único onde o bloco de
   480 (`.nav-right{gap:6px}` + `.nav .btn{padding:10px 10px}`) é o que segura a
   linha. A 320px ele é invisível: o bloco de 340, mais específico na cascata por
   vir depois, sobrescreve os dois valores (`gap:4px`, `padding-left/right:8px`).
   Sem este caso o grupo NÃO mede metade do conserto — medido:
     bloco de 480 revertido -> 341px vira 2 faixas, nav 110px, sobra -3,2
                               (320, 360, 375 e 390 seguem VERDES)
     com o conserto         -> 1 faixa, nav 66px, sobra +8,8            */
test("7) 341 deslogado: o ponto mais apertado acima do @media 340", async () => {
  const { ctx, page } = await abrirNav(341);
  umaFaixa(await medir(page), "341 deslogado");
  await ctx.close();
});

/* A alavanca mais frágil do conserto é a que a sobra NÃO mede: escrita `.nav`
   em vez de `.nav.nav`, a regra perde para o `#pb-nav-css` e o column-gap volta
   a 6px — a sobra a 320 cai de 5,8 para 1,8 e o grupo inteiro segue VERDE,
   porque 1,8 > 0. Aqui o que importa não é margem, é se a regra existe na
   cascata: asserta-se o VALOR COMPUTADO, que é a repro de uma linha do
   comentário do site.css virada em teste. */
test("8) a regra de column-gap da site.css vence o #pb-nav-css injetado", async () => {
  const a = await abrirNav(320);
  assert.equal((await medir(a.page)).columnGap, "4px",
    "a 320 o column-gap devia vir do @media 340 da site.css (`.nav.nav`)");
  await a.ctx.close();
  // acima do bloco de 340 quem manda é o valor injetado: guarda o breakpoint
  const b2 = await abrirNav(360);
  assert.equal((await medir(b2.page)).columnGap, "6px",
    "a 360 o column-gap devia ser o 6px injetado pelo nav-auth.js");
  await b2.ctx.close();
});

/* PENDÊNCIA NOMINAL, não regressão: com rótulo longo a barra logada continua em
   2 faixas, exatamente como na base. Medido (sobra, rótulo -> largura):
       "Ana Beatriz"      (11)  320 +10,8   341 +9,8    360 +28,8   375 +43,8
       "Lucas Kuramoti"   (14)  320 -19,2   341 -20,2   360  -1,2   375 +13,8
       "lucaskuramoti06"  (15)  320 -29,2   341 -30,2   360 -11,2   375  +3,8
   O conserto não alcança este caso: a 341 e 360 a coluna é idêntica à da base.
   Este caso trava o status quo para que ele não se perca de vista.

   SE ELE FICAR VERMELHO, NÃO ATUALIZE A TABELA DIRETO. "Passou a caber" tem
   causa legítima e causa ilegítima, e as duas produzem exatamente este vermelho:
     · legítimo  — alguém apertou o botão da conta de propósito (truncar o
       rótulo, encolher padding) e mediu; aí sim, atualize a tabela e a pendência;
     · ilegítimo — alguma COISA ENCOLHEU SEM QUERER e liberou espaço. Já
       aconteceu na bancada: pôr o logo em `height:12px` faz a barra caber e
       deixa este caso vermelho sozinho, com tudo o mais verde.
   Descubra POR QUE passou a caber antes de mexer aqui: compare o logo (caso 11),
   o botão da conta e o burger com os tamanhos documentados. */
test("9) 360 logado com rótulo longo AINDA não cabe (pendência conhecida)", async () => {
  const { ctx, page } = await abrirNav(360, { logado: true, rotulo: "lucaskuramoti06" });
  const m = await medir(page);
  assert.equal(m.faixas, 2,
    `360 com rótulo de 15 chars agora dá ${m.faixas} faixa(s), sobra ${m.sobra}. ` +
    "ANTES de atualizar a tabela: descubra o que encolheu (logo? botão da conta?) — " +
    "ver o comentário acima deste caso.");
  await ctx.close();
});

/* O painel da conta (288px fixos, `right:0`) transbordava PELA ESQUERDA depois
   que a barra virou uma linha só — e transbordo à esquerda NÃO cria rolagem
   (`scrollWidth` continua igual a `innerWidth`), então o pedaço cortado era
   inalcançável. Sem este caso o conserto passa 10/10: nenhum outro ABRE o menu.
   As larguras cobrem as duas faixas de padding, porque o `71` embutido no clamp
   é calculado para a PIOR delas: 316-340 tem padding 8 e sobra 14px de folga;
   341-358 tem padding 12 e sobra 8px. Amostrar só a primeira deixava a segunda
   — onde morava o corte da base — sem uma linha vermelha. */
test("10) o painel da conta abre inteiro dentro da tela (316 a 375)", async () => {
  for (const w of [316, 318, 320, 335, 340, 341, 345, 350, 358, 360, 375]) {
    const { ctx, page } = await abrirNav(w, { logado: true });
    // `scrollWidth` ANTES de abrir: de 316 a 317 a index já transborda para 318px
    // por causa do hero (.hero-copy/.pill/.hero-title), igualzinho na base e com
    // o menu fechado. Comparar com o valor de antes mede o PAINEL, não a página.
    const swFechado = await page.evaluate(() => document.documentElement.scrollWidth);
    await page.click(".pb-acct-btn");
    const d = await page.evaluate(() => {
      const b = document.querySelector(".pb-acct-dd").getBoundingClientRect();
      return { l: +b.left.toFixed(1), r: +b.right.toFixed(1), w: +b.width.toFixed(1),
               vw: innerWidth, sw: document.documentElement.scrollWidth };
    });
    // >= 6, não >= 0: os 8px de respiro embutidos no `71` são o que separa o
    // conserto de um encosto na borda, e `>= 0` deixava passar margem ZERO.
    // Com `71px` trocado por `57px` o painel volta a -6 de 341 a 358 e a 0 abaixo
    // de 340 — e 341-358 é justamente a faixa onde o padding é 12px, não 8px.
    assert.ok(d.l >= 6, `${w}: o painel começa em ${d.l}px — menos de 6px da borda`);
    assert.ok(d.r <= d.vw, `${w}: o painel termina em ${d.r}px, além dos ${d.vw}px da tela`);
    assert.equal(d.sw, swFechado, `${w}: abrir o painel empurrou a rolagem de ${swFechado} para ${d.sw}`);
    assert.ok(d.w > 150, `${w}: o painel encolheu para ${d.w}px — ilegível`);
    await ctx.close();
  }
});

/* "Preservar o logo" é restrição do dono e não tinha asserção nenhuma.

   Duas asserções, porque uma só não cobre a restrição:
    · PROPORÇÃO — pega o esmagamento horizontal. Não é teórico: na base o logo
      era achatado de 901 a 919px pelo limiar de 99,5% usado aqui (a 919 dá
      99,48% e a 920, 99,63%; o número muda com o limiar, por isso ele vem
      junto), porque a barra estourava e o flex encolhia a <img> — 97,67×30 a
      901px, 96,5% do aspecto natural, wordmark deformado.
      Com "Criar conta" a linha deixa de estourar e fica em 100,0% de 300 a 1300.
      ATENÇÃO: o esmagamento era exclusivo do DESLOGADO. Medido de 890 a 935 nas
      duas árvores, o logado nunca foi esmagado, nem na base — o conserto não
      resgatou "os dois estados", resgatou um.
    · TAMANHO RENDERIZADO — sozinha, a proporção é cega a mudança PROPORCIONAL:
      com `height:12px` o logo fica 60% menor, a razão continua 100% e a asserção
      de aspecto passa VERDE (medido). Nada mais no repositório cobre isto:
      `tests/test_brand_asset_budget.py` assere a proporção dos ARQUIVOS e só
      cita os 30px numa docstring.
   Os 30px vêm de `.nav-logo .logo-full{height:30px}` (site.css): se o logo for
   redesenhado, este é o lugar de atualizar — de propósito. */
test("11) o logo nunca é esmagado nem encolhido (aspecto E tamanho)", async () => {
  for (const w of [320, 375, 901]) {
    const { ctx, page } = await abrirNav(w);
    const g = await page.evaluate(() => {
      const i = document.querySelector(".nav-logo .logo-full");
      const b = i.getBoundingClientRect();
      return { w: +b.width.toFixed(2), h: +b.height.toFixed(2),
               esperado: +(b.height * (i.naturalWidth / i.naturalHeight)).toFixed(2) };
    });
    const pct = (g.w / g.esperado) * 100;
    assert.ok(pct >= 99.5,
      `${w}px: logo ${g.w}×${g.h}, esperado ${g.esperado} de largura — ${pct.toFixed(1)}% do aspecto`);
    assert.equal(g.h, 30, `${w}px: o logo renderizou com ${g.h}px de altura, não 30`);
    assert.ok(Math.abs(g.w - 101.16) <= 0.5,
      `${w}px: o logo renderizou com ${g.w}px de largura, esperado ~101,16`);
    await ctx.close();
  }
});
