/**
 * Véu branco dentro do modal CLARO — o enumerador que faltava.
 *
 * `body.light .modal { background:#fff }` trocou a única superfície grande que
 * continuava escura no tema claro. Tudo que foi desenhado ali dentro contando
 * com fundo escuro parou de funcionar, e a descoberta foi terceirizada para o
 * revisor: uma categoria por rodada — cor de texto (2×), SVG embutido com traço
 * branco, `background` branco translúcido, `border` branca translúcida.
 * Todos os enumeradores anteriores casavam PREFIXO DE SELETOR, e o critério não
 * é o seletor, é a DECLARAÇÃO: `.bill-opt` usa `var(--glass-border)` no repouso
 * e sobreviveu, mas o `:hover` dela escrevia o literal e sumia — o MESMO
 * seletor dos dois lados. Só estilo computado responde isso.
 *
 * COMO MEDE, e por que assim:
 *
 * 1. ELEMENTO REAL, não seletor. Percorre `querySelectorAll("*")` e guarda quem
 *    tem `closest(".modal")`. É o que imuniza contra o falso positivo do
 *    checkbox — `.field input[type="checkbox"]` casa o seletor `.field input`
 *    (branco na mão) mas o computado dele vem de um seletor MAIS LARGO que
 *    casamento de texto nunca enxergaria — e é o que pega véu por `style`
 *    inline, que parser de CSS não vê (foi assim que `#ofx-result-body` saiu).
 *
 * 2. ΔE76 em CIELAB, não razão de contraste e não ΔL*. Um véu de 3% sobre
 *    branco dá ~1,04:1 — régua de 3:1 reprovaria consertos já aceitos. O que o
 *    véu tem de fazer é UMA coisa: diferir da superfície o bastante para
 *    desenhar a caixa.
 *    Por que não luminância relativa Y: perto do preto Y é comprimido (o modal
 *    escuro tem Y≈0,005), e uma régua em Y reprovava 55 véus LEGÍTIMOS no tema
 *    escuro, incluindo os que este PR conserta.
 *    Por que não L* sozinho: L* ignora CROMA, e isso não é teórico aqui.
 *    `.help-note` (`rgba(198,241,26,.09)`, lima sobre branco) tem |ΔL*| de só
 *    1,18 e ΔE de 10,00 — L* subestima esse véu em 8×, porque ele desenha a
 *    caixa pela COR, não pelo brilho. Com a régua em L* ela tinha de ficar
 *    abaixo de 1,18 para não reprovar a `.help-note`, e aí
 *    `rgba(15,23,42,.0125)` — que compõe rgb(252,252,252), invisível — passava
 *    com 1,003, podendo SUBSTITUIR os consertos com o teste verde. ΔE conta
 *    croma e separa os dois: veja `MARGEM` abaixo, cujo valor é medido.
 *    O SINAL é ignorado de propósito: `#pkt-move-amount` usa `rgba(0,0,0,.25)`,
 *    véu mais ESCURO sobre modal escuro, e é desenho válido.
 *
 * 3. FILTRO "TINTA IGNORANTE DE TEMA". Nem todo branco dentro do modal claro é
 *    defeito: `body.light .hbtn { background:rgba(255,255,255,.7) }` é
 *    superfície clara DELIBERADA, com borda e texto escuros. O defeito é a
 *    tinta que ninguém revisou por tema. Discriminador: medir os DOIS temas e
 *    só acusar quando o valor computado do elemento é IDÊNTICO nos dois.
 *
 * 4. HOVER/FOCUS com o pseudo VALENDO, por promoção a classe. A 1ª versão
 *    servia a folha com `:hover` REMOVIDO do seletor, e isso é cego exatamente
 *    onde existe regra-base clara: `body.light .btn-cancel:hover` virava
 *    `body.light .btn-cancel`, empatava com a base e a base mascarava a
 *    mutação — 3 dos 14 grupos podiam ser apagados com o teste VERDE.
 *    `:hover` → `.pb-force-hover` mantém a especificidade (pseudo-classe e
 *    classe valem o mesmo) E a ordem de origem, então a regra de hover continua
 *    ganhando da base, como no navegador.
 *    Descartado antes, por medir errado: `CSSSession.forcePseudoState`. Com
 *    overlay em `display:none` o `getComputedStyle` devolve valor OBSOLETO para
 *    propriedade derivada de `var()` — e como as duas passadas herdavam a mesma
 *    sujeira, o filtro do item 3 descartava o achado e a medição errada saía
 *    VERDE. Por isso os overlays aqui abrem (`.open`) e cada estado é uma carga
 *    NOVA de página, sem alternar tema em documento já montado.
 *
 * 6. TRANSIÇÃO DESLIGADA na medição, pela mesma razão e com o mesmo veneno.
 *    `body` tem `transition: background .3s, color .3s` e vários componentes
 *    têm `transition: all .2s`; ler no instante do flip devolvia o valor
 *    INTERPOLADO, ainda o do tema escuro — 167 de 466 elementos com `color`
 *    obsoleto e 145 com `border-top-color`. `medir()` injeta
 *    `transition:none !important` e o caso "harness: a leitura não pega valor
 *    no meio da transição" compara a leitura imediata com a de 1,2 s depois,
 *    para isso não voltar em silêncio.
 *
 * 5. RETORNO DE HOVER, que o item 3 não alcança por construção. Se a regressão
 *    foi escrita JÁ dentro do ramo `body.light`, o valor DIFERE do escuro e o
 *    filtro do item 3 descarta — cegueira que este arquivo chegou a declarar
 *    como teórica e que era real: `body.light .mock-cta.outline:hover
 *    { background:#fff }` saltava ΔE 32,34 enquanto o modal era escuro e ZERO
 *    sobre o branco, em 4 botões (6,78 é o salto do tema ESCURO, que é o que o
 *    caso usa como `noEscuro` — não confundir os dois).
 *    O caso "hover que muda no escuro tem de mudar no
 *    claro" compara o SALTO (ΔE entre repouso e hover, composto sobre a
 *    superfície) e pegou os 4 — mais os dois `.hbtn` da navegação de fatura.
 *    Ele compara o maior salto ENTRE as propriedades, não propriedade a
 *    propriedade: o claro pode dar o retorno pelo fundo onde o escuro dá pela
 *    borda, e exigir paridade por propriedade seria super-especificar.
 *
 * CEGUEIRAS DECLARADAS (`CLAUDE.md` §3, 2ª pergunta):
 *  a. PARIDADE DE ALPHA — NADA A VERIFICA. O bloco de véus do `dashboard.css`
 *     tem como intenção manter, em cada elemento, a mesma alpha nos dois temas.
 *     Este arquivo NÃO checa isso: a comparação claro × escuro em `achados()`
 *     serve só para DESCARTAR tinta que foi pensada por tema. A consequência
 *     medida: apagar `body.light .modal .field input:focus` derruba o salto do
 *     FUNDO no foco de ΔE 3,28 para 0,00 e o teste segue verde — porque a borda
 *     continua saltando 28,56 e o anel rosa de 2px continua lá, então o foco não
 *     deixa de ser indicado. É a única das 24 mutações de seletor que não fica
 *     vermelha. Assertar paridade de alpha esbarraria em exceções legítimas
 *     (`--glass-border` é .13 no escuro e .09 no claro, de propósito), então
 *     fica sem verificação e conferido à mão — dito aqui para ninguém
 *     escrever, em outro comentário, que o teste compara alpha. Já escrevi.
 *  b. GEOMETRIA. Borda de 1px e preenchimento inteiro passam pelo mesmo
 *     limiar. Um `#fcfcfc` de 1px sobre branco não existe na prática, e o teste
 *     o aprovaria se ΔE desse acima da margem.
 *  c. Véu por `background-image` (gradiente) não entra em `backgroundColor`. O
 *     caso 4 cobre o oposto — imagem MORTA pelo tema claro — mas não mede a cor
 *     de um gradiente.
 *  d. Modal montado por concatenação cuja CLASSE venha de `${}` interpolado.
 *     Hoje não existe: `classList.add('modal')`/`className='modal'` no
 *     `dashboard.js` dão zero.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { MARGEM, medir as medirPagina, rgba, sobre, lab, dE } from "./_modal_veu.mjs";

let ORIGIN, server, browser;
before(async () => {
  ({ proc: server, origin: ORIGIN } = await startServer());
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); server?.kill(); });

const medir = (opts) => medirPagina(browser, ORIGIN, opts);

/**
 * Elementos cuja tinta (a) é a MESMA nos dois temas — logo ninguém a revisou
 * por tema — e (b) composita a menos de `MARGEM` de ΔE da superfície do
 * `.modal` ancestral, isto é, sumiu.
 */
function achados(pass, oposta) {
  const outra = new Map(oposta.els.map((e) => [e.i, e]));
  const out = [];
  for (const e of pass.els) {
    const o = outra.get(e.i);
    if (!o) continue;
    const sup = sobre(rgba(e.superficie), rgba(e.pagina));
    const alvo = lab(sup);
    for (const prop of ["background", "border"]) {
      if (!e[prop] || e[prop] !== o[prop]) continue;   // tinta pensada por tema: fora
      const tinta = rgba(e[prop]);
      if (!tinta || tinta.a === 0) continue;           // transparente não é véu
      const d = dE(lab(sobre(tinta, sup)), alvo);
      if (d >= MARGEM) continue;
      out.push(`${e.nome} { ${prop}: ${e[prop]} }  ΔE=${d.toFixed(2)} sobre ${e.superficie}`);
    }
  }
  return [...new Set(out)];
}

/**
 * Elementos em que o ESCURO muda de aparência no hover e o CLARO não muda.
 * Não é sobre sumir — é sobre o feedback existir. Apagar
 * `body.light .btn-cancel:hover` não deixa nada branco (a base clara tem
 * especificidade maior e ganha), então o caso de "véu sumido" fica cego; o que
 * se perde é o hover ficar IGUAL ao repouso. Foi uma das 3 mutações que
 * passavam verdes.
 */
function hoverMudo(repouso, hover, rotulo) {
  const rc = new Map(repouso.claro.els.map((e) => [e.i, e]));
  const re = new Map(repouso.escuro.els.map((e) => [e.i, e]));
  const he = new Map(hover.escuro.els.map((e) => [e.i, e]));
  const out = [];
  // Quanto o hover MOVE a cor, em ΔE sobre a superfície do modal — não se o
  // texto da cor mudou. `rgba(255,255,255,.7)` → `rgb(255,255,255)` sobre modal
  // branco são strings diferentes e o MESMO pixel: comparar string dava o hover
  // da `.hbtn` como "mudou" quando ele não muda nada.
  const salto = (antes, depois, prop) => {
    const sup = sobre(rgba(antes.superficie), rgba(antes.pagina));
    const [x, y] = [antes[prop], depois[prop]].map((v) => rgba(v));
    if (!x || !y) return 0;
    return dE(lab(sobre(x, sup)), lab(sobre(y, sup)));
  };
  for (const h of hover.claro.els) {
    const [a, b, c] = [rc.get(h.i), re.get(h.i), he.get(h.i)];
    if (!a || !b || !c) continue;
    // MAIOR salto entre as propriedades, não uma a uma: o claro pode dar o
    // retorno pelo fundo onde o escuro dá pela borda, e exigir paridade
    // propriedade a propriedade seria super-especificar o desenho. O defeito é
    // o hover não mover NADA de forma perceptível.
    const noEscuro = Math.max(salto(b, c, "background"), salto(b, c, "border"));
    if (noEscuro < MARGEM) continue;             // o escuro nem dá retorno aqui
    const noClaro = Math.max(salto(a, h, "background"), salto(a, h, "border"));
    if (noClaro >= MARGEM) continue;
    out.push(`${h.nome}  salto no hover: escuro ΔE=${noEscuro.toFixed(2)}, claro ΔE=${noClaro.toFixed(2)}`);
  }
  const l = [...new Set(out)];
  assert.deepEqual(l, [], `${rotulo}: ${l.length} elemento(s) com hover MUDO no tema claro\n  ` + l.join("\n  "));
}

const semVeuSumido = (pass, oposta, rotulo) => {
  const l = achados(pass, oposta);
  assert.deepEqual(l, [], `${rotulo}: ${l.length} véu(s) ignorante(s) de tema sumiram\n  ` + l.join("\n  "));
};

test("modal claro: nenhum véu ignorante de tema some na superfície do modal", async () => {
  const claro = await medir({ claro: true }), escuro = await medir({ claro: false });
  assert.ok(claro.els.length > 400, `elementos dentro de modal: ${claro.els.length} — CSS/markup não carregou`);
  assert.ok(claro.usados.length >= 10, `contêineres de innerHTML dentro de modal: ${claro.usados.length}`);
  semVeuSumido(claro, escuro, "tema claro");
  // Controle positivo: a MESMA enumeração no tema escuro. Prova que o teste não
  // é um "reprova tudo" e que só o ramo `body.light` mudou.
  semVeuSumido(escuro, claro, "tema escuro");
});

test("modal claro: idem com :hover/:focus/:active VALENDO", async () => {
  const claro = await medir({ claro: true, hover: true });
  const escuro = await medir({ claro: false, hover: true });
  assert.ok(claro.els.length > 400, `elementos dentro de modal: ${claro.els.length}`);
  semVeuSumido(claro, escuro, "tema claro + hover");
  semVeuSumido(escuro, claro, "tema escuro + hover");
});

test("modal claro: hover que muda no escuro tem de mudar no claro também", async () => {
  const repouso = { claro: await medir({ claro: true }), escuro: await medir({ claro: false }) };
  const hover = { claro: await medir({ claro: true, hover: true }), escuro: await medir({ claro: false, hover: true }) };
  hoverMudo(repouso, hover, "hover");
});

test("nenhum background-image morre no tema claro", async () => {
  // A seta do `<select>` (`.modal-inp.cat-select`) é um SVG embutido em
  // `background-image`, e com `appearance:none` a nativa não volta. Uma regra
  // de véu escrita com o shorthand `background` zera essa imagem: foi o que
  // apagou a seta de 4 selects. `background-color` não faz isso.
  const claro = await medir({ claro: true }), escuro = await medir({ claro: false });
  const mapa = new Map(claro.els.map((e) => [e.i, e]));
  const mortas = escuro.els
    .filter((e) => e.imagem !== "none" && mapa.get(e.i)?.imagem === "none")
    .map((e) => e.nome);
  assert.deepEqual(mortas, [], `o tema claro matou ${mortas.length} background-image: ${mortas.join(", ")}`);
  // Repor a imagem não basta. `background:` também zera repeat/position/size, e
  // com a imagem reposta por uma regra mais específica a seta do <select>
  // voltava LADRILHADA — uma fileira de chevrons dentro do campo. A imagem
  // sobrevivia, então uma verificação só de `background-image` não via nada.
  const desalinhadas = escuro.els
    .filter((e) => e.imagem !== "none" && mapa.get(e.i)?.pintura !== e.pintura)
    .map((e) => `${e.nome}  escuro [${e.pintura}] -> claro [${mapa.get(e.i)?.pintura}]`);
  assert.deepEqual(desalinhadas, [],
    `o tema claro mexeu na PINTURA de ${desalinhadas.length} elemento(s):\n  ` + desalinhadas.join("\n  "));
  assert.ok(escuro.els.filter((e) => e.imagem !== "none").length >= 25,
    "quase nenhum background-image no escuro — a folha não carregou");
});

test("harness: a leitura não pega valor no meio da transição", async () => {
  // `body` tem `transition: background .3s, color .3s` e vários componentes têm
  // `transition: all .2s`. Ligar `body.light` e ler no mesmo instante devolvia o
  // valor INTERPOLADO — ainda o do tema escuro. Medido antes do conserto, dentro
  // de `.modal`: 167 de 466 com `color` obsoleto e 145 com `border-top-color`
  // obsoleto. Nenhum caso lia `color` ainda, mas cor de texto é a categoria das
  // rodadas 1 e 2: o próximo caso nasceria VERDE medindo o tema errado.
  // O conserto é desligar transição/animação em `medir()`; este caso é o que
  // impede de voltar — sem ele, a leitura imediata e a leitura tardia divergem.
  const agora = await medir({ claro: true });
  const tarde = await medir({ claro: true, esperar: true });
  const mapa = new Map(tarde.els.map((e) => [e.i, e]));
  const divergem = agora.els.flatMap((e) => ["background", "border", "cor"]
    .filter((k) => mapa.get(e.i) && mapa.get(e.i)[k] !== e[k])
    .map((k) => `${e.nome} { ${k} }  na hora ${e[k]}  |  1,2s depois ${mapa.get(e.i)[k]}`));
  assert.deepEqual(divergem, [],
    `${divergem.length} leitura(s) pegaram a transição no meio\n  ` + divergem.slice(0, 8).join("\n  "));
});

test("controle positivo: os .field input FORA de modal seguem com o véu de main", async () => {
  const page = await browser.newPage();
  await page.route("**/*.js", (r) => r.abort());
  await page.route("**/*.js?*", (r) => r.abort());
  await page.goto(`${ORIGIN}/dashboard.html`);
  const fora = await page.evaluate(() => {
    document.body.classList.add("light");
    return [...document.querySelectorAll(".field input:not([type=checkbox]), .field select")]
      .filter((el) => !el.closest(".modal"))
      .map((el) => `${el.id || el.tagName}=${getComputedStyle(el).backgroundColor}`);
  });
  await page.close();
  // Os 10 campos de boleto/simulador estão quebrados no claro desde antes deste
  // PR (véu branco sobre `--bg-page` claro). Não é regressão daqui, e o override
  // é escopado em `.modal` justamente para o diff não os arrastar.
  assert.ok(fora.length >= 10, `campos fora de modal encontrados: ${fora.length}`);
  const mudados = fora.filter((s) => !s.endsWith("rgba(255, 255, 255, 0.06)"));
  assert.deepEqual(mudados, [], `o override escapou do .modal: ${mudados.join(", ")}`);
});
