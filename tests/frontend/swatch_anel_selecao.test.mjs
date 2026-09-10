/**
 * O indicador de seleção dos três pickers de swatch (`.sw-opt`) — cor, VÃO, e a
 * escolha de propriedade que os sustenta.
 *
 * MEDE MARKUP DE PRODUÇÃO, e isto é o ponto do arquivo. A 1ª versão remontava os
 * botões à mão com `background-image:` e por isso NUNCA via a escolha de
 * propriedade real: trocar `background-image:` por `background:` (uma palavra, o
 * shorthand que qualquer um escreveria) matava o vão com a suíte VERDE, e trocar
 * por `background-color:` apagava os 6 swatches do cartão, também verde. Teste que
 * reescreve o markup do alvo afirma, não mede.
 * O QUE VEM DA PRODUÇÃO, exatamente (`_swatch_prod.mjs`, tudo por `fn`/`arr`/
 * `decl`/`contDiv`, que dão `assert` se não acharem): as 3 funções de render, os 5
 * `_set*`/`_pick*`, `_rerenderPicker`, `phIcon`, `EMOJI_TO_PH`, as 5 paletas, os 3
 * `let _*EditState = {…}` e os 4 `<div id="…-picker">` com o `gap` deles.
 * O QUE NÃO VEM: a página `dashboard.html` (carregada de verdade, com a folha real)
 * e o `#card-edit-colors`, que já é estático nela. Nada é recopiado à mão — a
 * versão anterior copiava contêineres e estados, e a cópia JÁ tinha divergido
 * (`{ emoji, color }` contra `{ id, emoji, color, is_system }` da produção),
 * enquanto o `gap` copiado é justamente a folga de que o anel externo depende.
 *
 * Por que não é um caso do `modal_claro_veu.test.mjs`: aquele mede véu por ΔE em
 * `background`/`border`, e o anel dos dois pickers de COR mora em `box-shadow`,
 * que ele não lê. A régua também é outra — aqui é razão de contraste com piso de
 * 3:1 (WCAG 2.2 SC 1.4.11, Non-text Contrast), lá é ΔE de véu.
 *
 * O VÃO vale SÓ nos pickers de cor, porque só ali o fundo é DADO DO USUÁRIO: no
 * tema escuro `--purple-ink` é `#FF2D8E` e o 1º swatch da paleta — default de
 * toda categoria e meta nova — também é `#FF2D8E`, então anel encostado dá
 * 1,00:1. Ele vem de `border:3px solid transparent` +
 * `background-clip:padding-box` com o anel jogado para fora por `box-shadow`, e
 * estilo computado NÃO o prova: `padding-box` computado não diz que a faixa tem
 * largura visível. Por isso o caso do vão amostra PIXEL.
 * No picker de EMOJI o anel encosta de propósito (fundo do selecionado é
 * conhecido, 4,07:1 / 3,94:1) e a maquinaria do vão está AUSENTE de propósito:
 * com ela, o ladrilho dos 50 botões encolhia de 36×36 para 30×30, ~30% de área,
 * e o selecionado ficava com tamanho aparente diferente do repouso. O caso "o
 * ladrilho do emoji continua 36px" trava isso.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { MARGEM, rgba, sobre, lab, dE } from "./_modal_veu.mjs";
import { PISO, CORES, CORES_SEL, EMOJIS, montar as montarPagina, tema,
         faixa, perto, soCor, contrasteSobre } from "./_swatch_prod.mjs";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });
const montar = () => montarPagina(browser, ORIGIN);


test("o anel de seleção tem 3:1 contra a superfície, nos dois temas", async () => {
  const page = await montar();
  const fora = [], soltos = [];
  // `montar()` renderiza UMA vez, no escuro, e o laço abaixo só ALTERNA O TEMA —
  // sem re-render. Isso não é economia, é o teste do flip: o indicador tem de
  // acompanhar o tema com o modal já aberto, e era exatamente o que a versão
  // inline não fazia. Medido em aeb5264: anel e borda ficavam IDÊNTICOS depois do
  // flip (borda branca sobre o modal que acabara de ficar branco). Não insira
  // re-render aqui — ele apagaria a única coisa que este laço prova além do 3:1.
  for (const claro of [false, true]) {
    await tema(page, claro);
    const lidos = await page.evaluate((sels) => sels.map((sel) => {
      const el = document.querySelector(`${sel}.selected`);
      if (!el) return { sel, ausente: true };
      const s = getComputedStyle(el), m = el.closest(".modal");
      const rep = getComputedStyle(document.querySelector(`${sel}:not(.selected)`));
      return { sel, anel: s.boxShadow, borda: s.borderTopColor, fill: s.backgroundColor,
               superficie: getComputedStyle(m).backgroundColor,
               pagina: getComputedStyle(document.body).backgroundColor,
               repAnel: rep.boxShadow, repBorda: rep.borderTopColor };
    }), [...CORES_SEL, ...EMOJIS]);
    for (const l of lidos) {
      if (l.ausente) { fora.push(`${l.sel}: nenhum selecionado no DOM de produção`); continue; }
      const rot = `${l.sel} [${claro ? "claro" : "escuro"}]`;
      const sup = sobre(rgba(l.superficie), rgba(l.pagina));
      // Emoji: o anel é a BORDA e encosta no fill, então a referência é o fill.
      // Cor/cartão: o anel é o `box-shadow` e a referência é a SUPERFÍCIE, porque
      // o vão (caso seguinte) o separa do swatch.
      const emoji = l.sel.includes("emoji");
      const tinta = rgba(emoji ? l.borda : soCor(l.anel));
      if (!tinta || tinta.a === 0) { fora.push(`${rot} sem anel: ${l.anel} / ${l.borda}`); continue; }
      // COMPOSTA sobre o fundo antes da razão: `box-shadow` e `border-color`
      // aceitam alpha, e ler a tinta crua dava 5,57:1 para um anel de alpha .06
      // que não se vê. `contrasteSobre` faz o `sobre()` e a régua se recusa a
      // receber alpha, para isto não voltar por outro caminho.
      const r = contrasteSobre(tinta, emoji && rgba(l.fill).a > 0 ? sobre(rgba(l.fill), sup) : sup);
      if (r < PISO) fora.push(`${rot} anel ${r.toFixed(2)}:1 < ${PISO}:1`);
      // CONTROLE POSITIVO, obrigatório porque o conserto RESTRINGE (`CLAUDE.md`
      // §3): o ramo NÃO selecionado não pode ganhar indicador. Sem ele o caso
      // passaria num CSS que pinta de rosa os 81 botões que este DOM monta
      // (30+15 categoria, 20+10 meta, 6 cartão) — pior que o bug original.
      if (l.repAnel !== "none" || !/, 0\)$/.test(l.repBorda))
        soltos.push(`${rot} repouso ganhou indicador: borda=${l.repBorda} anel=${l.repAnel}`);
    }
  }
  await page.close();
  assert.deepEqual(fora, [], `${fora.length} anel(éis) abaixo do piso\n  ` + fora.join("\n  "));
  assert.deepEqual(soltos, [], `${soltos.length} botão(ões) em repouso com indicador\n  `
    + soltos.join("\n  "));
});

test("o anel não ENCOSTA no swatch: vão de 2px, nos dois temas e nas duas piores cores", async () => {
  const page = await montar();
  const magros = [];
  for (const p of CORES) {
    for (const v of p.valores) {
      assert.ok(await p.tem(page, v),
        `${v} saiu da paleta de ${p.sel} — escolha outro pior caso, não afrouxe o caso`);
      await p.aplica(page, v);
      for (const claro of [false, true]) {
        await tema(page, claro);
        const sel = p.sel, cor = v;
        const { px, borda, anel } = await faixa(page, `${sel}.selected`, 9, 14);
        // ÂNCORA GEOMÉTRICA, não de cor: no escuro anel e swatch são o MESMO
        // pixel e nenhuma busca por cor os separa. O recorte começa em `x-9`,
        // logo o índice 9 é o 1º pixel do botão e a faixa da borda vai de 9 a
        // 9+borda-1. Com `background-clip:padding-box` essa faixa revela a
        // SUPERFÍCIE; sem ele, revela o swatch. Conta de 7 para absorver o
        // arredondamento subpixel de `x`. A superfície vem do índice 0 (9px à
        // esquerda do botão; só UM botão por picker fica selecionado, então o
        // vizinho não tem anel e aquele pixel é superfície limpa).
        //
        // Varre de DENTRO do swatch para a esquerda, em vez de contar pixels
        // iguais à superfície numa fatia fixa. A versão anterior fazia isso e
        // tinha um furo grave: anel AUSENTE aumenta a contagem de superfície e
        // PASSAVA — `overflow:hidden` no contêiner clipa o anel e os 7 casos
        // ficavam verdes, porque o `box-shadow` computado existe mesmo clipado e
        // nenhum caso olhava o pixel do anel. Percorrer swatch -> vão -> anel
        // resolve a ambiguidade de anel e swatch terem a MESMA cor no escuro:
        // eles não são comparados entre si, são atravessados em ordem.
        const rot = `${sel} ${cor} ${claro ? "claro" : "escuro"}`;
        let k = 10 + borda;                          // 1px dentro do padding box
        while (k > 0 && !perto(px[k], px[0])) k--;   // atravessa o swatch
        let vao = 0;
        while (k > 0 && perto(px[k], px[0])) { vao++; k--; }   // conta o vão
        if (vao < 2)
          magros.push(`${rot}: vão ${vao}px (borda ${borda}px, superfície ${px[0]})` +
                      ` — faixa ${px.join(" | ")}`);
        else {
          // …e o que vem antes é o ANEL, com a LARGURA que o `box-shadow` promete.
          // Exigir só "aparece" não bastava: com `overflow:hidden` no contêiner
          // sobra 1px de anel sangrando sob a borda transparente (o
          // `background-clip:padding-box` revela o pai ali), o anel de 2px vira
          // 1px quase invisível e a checagem de presença passava.
          let largura = 0;
          while (k >= 0 && perto(px[k], anel)) { largura++; k--; }
          if (largura < 2)
            magros.push(`${rot}: anel PINTADO com ${largura}px (esperado 2, do ` +
                        `box-shadow ${anel}) — faixa ${px.join(" | ")}`);
        }
      }
    }
  }
  await page.close();
  assert.deepEqual(magros, [], `${magros.length} anel(éis) sem vão de 2px\n  ` + magros.join("\n  "));
});

test("os swatches PINTAM a cor da paleta — nenhum fica em branco", async () => {
  const page = await montar();
  const apagados = await page.evaluate(() => {
    const out = [];
    // Cartão: a paleta são GRADIENTES (`CARD_COLOR_OPTIONS[].sample`), então o
    // dado tem de sair em `background-image`. `background-color:` rejeitaria o
    // valor e o botão ficaria sem pintura nenhuma — 6 swatches invisíveis.
    for (const el of document.querySelectorAll("#card-edit-colors .sw-card")) {
      const s = getComputedStyle(el);
      if (!/gradient/.test(s.backgroundImage))
        out.push(`${el.dataset.color}: backgroundImage=${s.backgroundImage}`);
    }
    for (const [sel, arr] of [["#cat-color-picker .sw-color", window.pb.CATEGORY_COLOR_OPTIONS],
                              ["#goal-color-picker .sw-color", window.pb.GOAL_COLOR_OPTIONS]]) {
      const bts = document.querySelectorAll(sel);
      if (bts.length !== arr.length) out.push(`${sel}: ${bts.length} botões para ${arr.length} cores`);
      bts.forEach((el, i) => {
        const c = getComputedStyle(el).backgroundColor.match(/\d+/g)?.slice(0, 3).map(Number);
        const q = arr[i].replace("#", "").match(/../g).map((h) => parseInt(h, 16));
        if (!c || c.some((v, j) => v !== q[j])) out.push(`${sel}[${i}] ${arr[i]} -> ${c}`);
      });
    }
    return out;
  });
  // …e a pintura chega ao PIXEL, não só ao estilo computado.
  const { px } = await faixa(page, "#card-edit-colors .sw-card:not(.selected)", 4, 20);
  const pintou = px.slice(8).some((p) => !perto(p, px[0]));
  await page.close();
  assert.deepEqual(apagados, [], `${apagados.length} swatch(es) sem a cor da paleta\n  `
    + apagados.join("\n  "));
  assert.ok(pintou, `o swatch do cartão não pintou pixel nenhum — faixa ${px.join(" | ")}`);
});

test("o emoji selecionado não se confunde com o de repouso, nos dois temas", async () => {
  const page = await montar();
  const iguais = [];
  for (const claro of [false, true]) {
    await tema(page, claro);
    const l = await page.evaluate((sels) => sels.map((sel) => {
      const s = getComputedStyle(document.querySelector(`${sel}.selected`));
      const r = getComputedStyle(document.querySelector(`${sel}:not(.selected)`));
      const m = document.querySelector(`${sel}.selected`).closest(".modal");
      return { sel, sel_: s.backgroundColor, rep: r.backgroundColor,
               superficie: getComputedStyle(m).backgroundColor,
               pagina: getComputedStyle(document.body).backgroundColor };
    }), EMOJIS);
    for (const e of l) {
      const sup = sobre(rgba(e.superficie), rgba(e.pagina));
      const d = dE(lab(sobre(rgba(e.sel_), sup)), lab(sobre(rgba(e.rep), sup)));
      // Sem `:not(.selected)` na regra clara, `body.light .modal .sw-emoji`
      // (0,3,1) passa por cima de `.sw-emoji.selected` (0,2,0) e o selecionado
      // volta ao cinza do repouso: ΔE 0,00, seleção invisível.
      if (d < MARGEM) iguais.push(`${e.sel} [${claro ? "claro" : "escuro"}] ` +
        `selecionado ${e.sel_} × repouso ${e.rep} — ΔE=${d.toFixed(2)}`);
    }
  }
  await page.close();
  assert.deepEqual(iguais, [], `${iguais.length} picker(s) com selecionado igual ao repouso\n  `
    + iguais.join("\n  "));
});

test("o ícone do emoji tem 3:1 contra o próprio ladrilho, nos dois temas", async () => {
  const page = await montar();
  const fracos = [];
  for (const claro of [false, true]) {
    await tema(page, claro);
    const l = await page.evaluate((sels) => sels.flatMap((sel) =>
      [`${sel}.selected`, `${sel}:not(.selected)`].map((q) => {
        const el = document.querySelector(q), m = el.closest(".modal");
        return { q, cor: getComputedStyle(el.querySelector("i") || el).color,
                 fill: getComputedStyle(el).backgroundColor,
                 superficie: getComputedStyle(m).backgroundColor,
                 pagina: getComputedStyle(document.body).backgroundColor };
      })), EMOJIS);
    for (const e of l) {
      // `<button>` NÃO herda cor: sem `color:inherit` ele nasce com o
      // `buttontext` do agente de usuário, que dá rgb(0,0,0) — ícone preto sobre
      // o ladrilho do modal ESCURO, ~1,31:1.
      const sup = sobre(rgba(e.superficie), rgba(e.pagina));
      const r = contrasteSobre(rgba(e.cor), sobre(rgba(e.fill), sup));
      if (r < PISO)
        fracos.push(`${e.q} [${claro ? "claro" : "escuro"}] ícone ${e.cor} sobre ${e.fill}` +
                    ` = ${r.toFixed(2)}:1 < ${PISO}:1`);
    }
  }
  await page.close();
  assert.deepEqual(fracos, [], `${fracos.length} ícone(s) abaixo do piso\n  ` + fracos.join("\n  "));
});

test("o ladrilho do emoji continua com 36px pintados, não 30", async () => {
  // Regressão que a 1ª versão deste bloco causou e ninguém relatou: a maquinaria
  // do vão (`border:3px` + `background-clip:padding-box`) estava em `.sw-opt`,
  // comum aos três pickers, e recortava o fill do emoji para 30×30 — ~30% de área
  // a menos, com o selecionado de tamanho aparente diferente do repouso. O emoji
  // não precisa de vão: ali o anel É a borda e não há fundo de usuário.
  const page = await montar();
  const erros = [];
  for (const claro of [false, true]) {
    await tema(page, claro);
    for (const sel of EMOJIS)
      for (const q of [`${sel}.selected`, `${sel}:not(.selected)`]) {
        const { px } = await faixa(page, q, 6, 48);
        const ini = px.findIndex((p, k) => k > 0 && !perto(p, px[0]));
        let fim = ini;
        while (fim < px.length && !perto(px[fim], px[0])) fim++;
        const largura = fim - ini;
        if (Math.abs(largura - 36) > 1)
          erros.push(`${q} [${claro ? "claro" : "escuro"}] pintou ${largura}px, esperado 36` +
                     ` — faixa ${px.join(" | ")}`);
      }
  }
  await page.close();
  assert.deepEqual(erros, [], `${erros.length} ladrilho(s) com largura errada\n  ` + erros.join("\n  "));
});

/**
 * O estado acessível da seleção: `aria-pressed` presente, VIRANDO, num botão que
 * tem NOME, e sobrevivendo ao re-render sem perder o foco.
 *
 * Os cinco `_set*`/`_pick*` refazem o `innerHTML` inteiro, então o atributo vira
 * num nó NOVO: sem devolver o foco, o leitor de tela não anuncia a mudança e o
 * usuário de teclado é jogado para o `<body>` no meio de 30 botões. Medido antes
 * do conserto: `BUTTON.sw-opt.sw-color` -> `BODY.has-sidenav`. E `aria-pressed`
 * num controle sem nome acessível é decoração: os 75 swatches de cor não tinham
 * texto, `title` nem `aria-label`, e o `<i>` do emoji é `aria-hidden`.
 */
test("aria-pressed vira, o botão tem nome, e o foco sobrevive ao re-render", async () => {
  const page = await montar();
  const r = await page.evaluate(() => {
    const nome = (el) => el.getAttribute("aria-label") || el.getAttribute("title") ||
                         el.textContent.trim();
    const out = { semNome: [], semPressed: [], naoVirou: [], perdeuFoco: [] };
    const casos = [
      ["#cat-emoji-picker", ".sw-emoji", (el) => window._setCatEmoji(el.getAttribute("aria-label"))],
      ["#cat-color-picker", ".sw-color", (el) => window._setCatColor(
        el.getAttribute("style").match(/#[0-9a-f]{6}/i)[0])],
      ["#goal-emoji-picker", ".sw-emoji", (el) => window._setGoalEmoji(el.getAttribute("aria-label"))],
      ["#goal-color-picker", ".sw-color", (el) => window._setGoalColor(
        el.getAttribute("style").match(/#[0-9a-f]{6}/i)[0])],
      ["#card-edit-colors", ".sw-card", (el) => window._pickCardColor(el.dataset.color)],
    ];
    for (const [cont, cls, aciona] of casos) {
      for (const el of document.querySelectorAll(`${cont} ${cls}`)) {
        if (!nome(el)) out.semNome.push(`${cont} ${cls}[${el.dataset.color || ""}]`);
        if (el.getAttribute("aria-pressed") === null) out.semPressed.push(`${cont} ${cls}`);
      }
      // 3º botão: não selecionado no estado inicial dos cinco pickers.
      const alvo = document.querySelectorAll(`${cont} ${cls}`)[2];
      if (alvo.getAttribute("aria-pressed") !== "false")
        out.naoVirou.push(`${cont}: alvo já vinha pressionado`);
      alvo.focus();                                   // simula teclado, não mouse
      aciona(alvo);
      const novo = document.querySelector(`${cont} ${cls}.selected`);
      if (!novo || novo.getAttribute("aria-pressed") !== "true")
        out.naoVirou.push(`${cont}: aria-pressed não virou (${novo?.getAttribute("aria-pressed")})`);
      if (document.activeElement !== novo)
        out.perdeuFoco.push(`${cont}: foco foi para ` +
          `${document.activeElement.tagName}.${document.activeElement.className}`);
      // só um pressionado por picker
      const n = document.querySelectorAll(`${cont} [aria-pressed="true"]`).length;
      if (n !== 1) out.naoVirou.push(`${cont}: ${n} botões pressionados ao mesmo tempo`);
    }
    return out;
  });
  await page.close();
  assert.deepEqual(r.semNome, [], `${r.semNome.length} swatch(es) sem nome acessível`);
  assert.deepEqual(r.semPressed, [], `${r.semPressed.length} swatch(es) sem aria-pressed`);
  assert.deepEqual(r.naoVirou, [], r.naoVirou.join("\n  "));
  assert.deepEqual(r.perdeuFoco, [],
    `${r.perdeuFoco.length} picker(s) perderam o foco no re-render\n  ` + r.perdeuFoco.join("\n  "));
});
