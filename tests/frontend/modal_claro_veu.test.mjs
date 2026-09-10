/**
 * Véu branco dentro do modal CLARO — o enumerador que faltava.
 *
 * `body.light .modal { background:#fff }` trocou a única superfície grande que
 * continuava escura no tema claro, e tudo que foi desenhado ali contando com fundo
 * escuro parou de funcionar. A descoberta foi terceirizada para o revisor, uma
 * categoria por rodada: cor de texto (2×), SVG com traço branco, `background`
 * branco translúcido, `border` branca translúcida, `style` inline interpolado.
 * Todos os enumeradores anteriores casavam PREFIXO DE SELETOR, e o critério não é
 * o seletor, é a DECLARAÇÃO: `.bill-opt` usa `var(--glass-border)` no repouso e
 * sobreviveu, mas o `:hover` dela escrevia o literal e sumia — o MESMO seletor dos
 * dois lados. Só estilo computado responde isso.
 *
 * COMO MEDE, e por que assim:
 *
 * 1. ELEMENTO REAL, não seletor: `querySelectorAll("*")` filtrado por
 *    `closest(".modal")`. Imuniza contra o falso positivo do checkbox
 *    (`.field input[type=checkbox]` casa o seletor `.field input`, branco na mão,
 *    mas o computado vem de um seletor MAIS LARGO) e pega véu por `style` inline,
 *    que parser de CSS não vê (foi assim que `#ofx-result-body` saiu).
 *
 * 2. ΔE76 em CIELAB, não razão de contraste e não ΔL*. O véu tem de fazer UMA
 *    coisa: diferir da superfície o bastante para desenhar a caixa. Um véu de 3%
 *    sobre branco dá ~1,04:1, então régua de 3:1 reprovaria consertos aceitos.
 *    Não Y: perto do preto Y é comprimido (modal escuro tem Y≈0,005) e uma régua
 *    em Y reprovava 55 véus LEGÍTIMOS no escuro. Não L* sozinho: L* ignora CROMA
 *    — `.help-note` (`rgba(198,241,26,.09)`) tem |ΔL*| 1,18 e ΔE 10,00, subestimado
 *    8×, e com a régua em L* o `rgba(15,23,42,.0125)` (que compõe rgb(252,252,252),
 *    invisível) passava com 1,003 e podia SUBSTITUIR os consertos com o teste
 *    verde. Ver `MARGEM`, cujo valor é medido. O SINAL é ignorado de propósito:
 *    `#pkt-move-amount` usa `rgba(0,0,0,.25)`, véu mais ESCURO sobre modal escuro.
 *
 * 3. FILTRO "TINTA IGNORANTE DE TEMA". Nem todo branco no modal claro é defeito:
 *    `body.light .hbtn { background:rgba(255,255,255,.7) }` é superfície clara
 *    DELIBERADA. O defeito é a tinta que ninguém revisou por tema, então só acusa
 *    quando o valor computado é IDÊNTICO nos dois temas.
 *
 * 4. HOVER/FOCUS com o pseudo VALENDO, por promoção a classe. Servir a folha com
 *    `:hover` REMOVIDO é cego onde existe regra-base clara:
 *    `body.light .btn-cancel:hover` virava `body.light .btn-cancel`, empatava com
 *    a base e a base mascarava a mutação — 3 dos 14 grupos podiam ser apagados com
 *    o teste VERDE. `:hover` → `.pb-force-hover` mantém especificidade E ordem de
 *    origem. Descartado por medir errado: `CSSSession.forcePseudoState` — com
 *    overlay em `display:none` o computado de `var()` vem OBSOLETO, as duas passadas
 *    herdavam a mesma sujeira e o item 3 descartava o achado. Por isso os overlays
 *    abrem (`.open`) e cada estado é carga NOVA de página.
 *
 * 6. TRANSIÇÃO DESLIGADA na medição, mesmo veneno. `body` tem `transition:
 *    background .3s, color .3s` e vários componentes têm `transition: all .2s`;
 *    ler no instante do flip devolvia o valor INTERPOLADO, ainda o do escuro — 167 de
 *    466 com `color` obsoleto e 145 com `border-top-color`. `medir()` injeta
 *    `transition:none !important` e o caso-guarda compara a leitura imediata com a
 *    de 1,2 s depois.
 *
 * 5. RETORNO DE HOVER, que o item 3 não alcança por construção: regressão escrita
 *    JÁ dentro do ramo `body.light` difere do escuro e o item 3 descarta. Era
 *    cegueira dita teórica e era real — `body.light .mock-cta.outline:hover
 *    { background:#fff }` saltava ΔE 32,34 com o modal escuro e ZERO sobre o branco,
 *    em 4 botões (6,78 é o salto do tema ESCURO, que o caso usa como `noEscuro`).
 *    O caso do hover compara o MAIOR salto entre as propriedades, não propriedade a
 *    propriedade — o claro pode dar o retorno pelo fundo onde o escuro dá pela
 *    borda — e pegou os 4 mais os dois `.hbtn` da navegação de fatura.
 *
 * 7. RAMO DE TERNÁRIO, os DOIS. `ramificar()` resolve `${c ? "a" : "b"}` um ramo por
 *    passe e `medir()` monta os dois no mesmo documento. Sem isso o harness era cego
 *    ao VALOR (`border:2px solid ${sel ? "#fff" : "transparent"}` chegava como
 *    declaração inválida e os 6 botões eram medidos sem véu) e à CLASSE (`selected`
 *    de `class="bill-opt${c ? ' selected' : ''}"` morria no `limparClasses`).
 *    Medido em 2026-09-10 nas duas árvores: 899 -> 1908 elementos, 10 -> 18
 *    contêineres ÚNICOS, zero achado novo. Remeça, não copie (§2).
 *
 * CEGUEIRAS DECLARADAS (`CLAUDE.md` §3, 2ª pergunta). Três das cinco que este
 * cabeçalho já declarou viraram achado real depois — `.mock-cta.outline`, o
 * `style` inline interpolado e a consequência de (a). "Declarada" NÃO é estado
 * seguro: o critério é que cegueira só continua declarada se for impossível de
 * medir POR CONSTRUÇÃO. Se for só trabalhosa, fecha.
 *  a. PARIDADE DE ALPHA em si — segue sem verificação, e o motivo procede: assertar
 *     alpha igual nos dois temas esbarra em exceção legítima (`--glass-border` é .13
 *     no escuro e .09 no claro, de propósito). Errado era parar aí — a CONSEQUÊNCIA
 *     é medível sem assertar alpha, e é o caso "nenhum elemento perde a fronteira
 *     INTEIRA", que achou o defeito que o revisor não pegou: `var(--glass-bg)` vale
 *     .07 no escuro e .85 no claro (DIFERENTES, logo o item 3 descarta) e sobre o
 *     modal branco o .85 é o próprio #fff — 50 botões invisíveis. Outro efeito
 *     conhecido: apagar `body.light .modal .field input:focus` derruba o salto do
 *     FUNDO no foco de ΔE 3,28 para 0,00 e o teste segue verde porque a borda
 *     continua saltando 28,56 — a única das 24 mutações que não fica vermelha.
 *  b. GEOMETRIA — FECHADA onde este PR desenha geometria, declarada no resto. ΔE
 *     mede tinta, não área; onde a área ERA o conserto, o
 *     `swatch_anel_selecao.test.mjs` amostra PIXEL (vão de 2px, largura de 2px do
 *     anel, 36px do ladrilho). Sem a largura, `overflow:hidden` no contêiner deixava
 *     1px de anel sangrando sob a borda transparente e os 7 casos ficavam verdes.
 *     Segue declarada nos outros ~1900 elementos: seria 1908 × 2 temas com um ponto
 *     de amostra correto por elemento — ver (c), onde o caro não é rasterizar.
 *  c. Véu/tinta sobre GRADIENTE. O impedimento antigo ("exige rasterizar") caiu:
 *     este PR rasteriza no caso "os swatches PINTAM a cor da paleta". FECHADA para
 *     o gradiente que o PR desenha (os 6 swatches do cartão), onde o ponto de
 *     amostra é conhecido. Segue declarada para TEXTO sobre gradiente, e agora com a
 *     razão medida em vez de suposta: tentei em 2026-09-10 e o enumerador achou 21
 *     elementos com texto sobre gradiente dentro de `.modal`, mas 8 deram 1,00:1
 *     FALSO — o ponto de amostra caiu fora da caixa pintada. O caro não é
 *     rasterizar, é derivar um ponto de amostra correto POR ELEMENTO; um caso
 *     construído sobre aquele probe teria 8 falsos positivos em 21, o que é pior que
 *     não ter caso. O QUE FECHA: ponto de amostra derivado da caixa pintada de cada
 *     elemento (interseção do `getBoundingClientRect` com a caixa de padding do
 *     ancestral que pinta, amostrando dentro dela e não no centro do filho), e aí os
 *     21 viram medição honesta.
 *     CASO À PARTE, e este é cego POR CONSTRUÇÃO, não por ponto de amostra:
 *     `h3#upg-title` usa `background-clip:text` com `color:rgba(0,0,0,0)` — a tinta
 *     do texto É o gradiente, e não existe par tinta/fundo para a régua comparar.
 *     Ponto de amostra melhor não resolve; exigiria modelar o gradiente recortado
 *     pelas glifas. Fica declarado separado para não ser confundido com o resto.
 *     O caso 4 cobre o oposto (imagem MORTA pelo tema claro).
 *  f. INLINE SVG (`fill`/`stroke`) — enumerado nesta rodada e VAZIO POR AUSÊNCIA,
 *     que é diferente de limpo: `medir()` passou a lê-los e o DOM medido tem ZERO
 *     elementos SVG dentro de `.modal` (1908 elementos, 0 propriedades). Conferido
 *     no fonte: os `<svg>` inline estão em `_renderGoalCard`, no grid da visão geral
 *     e no sprite oculto do `dashboard.html` — nenhum em modal. O defeito da 3ª
 *     rodada do Codex NÃO era isto, era um SVG em `background-image` data-URI (a
 *     seta do `.cat-select`), coberto pelo caso 4. Se um `<svg>` entrar em modal o
 *     enumerador já lê; hoje um caso sobre 0 elementos seria cerimônia.
 *  d. Classe de ESTADO vinda de `${}` — FECHADA por (7). A redação anterior olhava a
 *     coisa errada ("modal cuja CLASSE venha de `${}`; não existe"): o risco era
 *     qualquer classe de ESTADO, e há 35 no `dashboard.js` — entre elas
 *     `.bill-opt.selected`, da pendência abaixo.
 *  e. COR DE TEXTO e traço de SVG — REMEDIDA em 2026-09-10 com o harness novo, não
 *     herdada: 406 elementos com texto dentro de `.modal`, `color` idêntico nos dois
 *     temas e sumido sobre o fundo EFETIVO → 1 candidato, que não é defeito (um
 *     `<option>`, desenhado pelo popup do agente de usuário e não pelo `background`
 *     que o `getComputedStyle` reporta); outros 17 caem em (c). `medir()` já lê
 *     `s.color` e nenhum caso assere, porque a régua honesta precisa resolver o fundo
 *     subindo a árvore e aí (c) engole o conjunto. Remeça.
 *
 * PENDÊNCIA NOMINAL, e CEGUEIRA ESCOLHIDA, não acidental. `.tipo-opt.active`,
 * `.vmode-opt.active` e `.bill-opt.selected` indicam seleção com uma borda de alpha
 * .45 — mas NÃO com uma tinta só. São QUATRO tintas em 8 regras, e a versão anterior
 * desta tabela listou três linhas e concluiu "reprova nos seis", subenumerando a
 * própria categoria que ela documenta (§2 dentro do comentário). Medido em
 * 2026-09-10 com a régua já COMPOSTA sobre a superfície do modal:
 *   tinta                  variantes                      escuro      claro
 *   rgba(255,45,45,.45)    despesa, resgate               1,83        1,94
 *   rgba(0,240,120,.45)    receita, aporte                3,27 PASSA  1,28
 *   rgba(255,45,142,.45)   credito, .vmode, .bill-opt     1,91        1,88
 *   rgba(255,128,184,.45)  novo-inv                       2,52        1,46
 * São DOZE números, não seis, e um deles PASSA: a variante verde de receita/aporte
 * cumpre o piso de 3:1 no tema escuro e só reprova no claro. A `main` tem os mesmos
 * números, então não é regressão daqui — documentado, não corrigido, e foi por isso
 * que `.sw-opt` não reusou a declaração (reusar seria adotar o defeito).
 * Graças ao `ramificar()`, `button.bill-opt.selected` AGORA ESTÁ no DOM medido (5×)
 * e passa mesmo assim, porque a régua de 3:1 vive no `swatch_anel_selecao.test.mjs`
 * e foi apontada só para os 5 seletores de swatch — não por não alcançá-los, mas
 * porque a linha de escopo é "pré-existente não entra" e apontá-la deixaria o PR
 * vermelho por defeito que ele não causou.
 * E o plano de fechamento só passou a ser REAL nesta rodada: a 1ª versão da régua
 * descartava o alpha, e apontá-la para os três teria dado 5,41:1 / 3,49:1 —
 * APROVANDO os três. A frase "o que fecharia isto é apontar o mesmo caso para os
 * três" era falsa enquanto a régua lia `rgba(255,45,142,.45)` como `#FF2D8E` opaco.
 * Com `contrasteSobre()` ela reprova de verdade, e aí sim o que fecha é apontar o
 * caso para as QUATRO tintas num PR próprio, junto do conserto. E o conserto não é
 * um só: `var(--purple-ink)` resolve a família rosa (5,41:1 / 5,57:1) e não diz nada
 * sobre a verde de receita/aporte nem sobre a vermelha — cada tinta precisa da sua,
 * e a verde ainda tem o caso especial de já passar num dos temas. Enquanto não for
 * feito, é cegueira declarada; três delas já viraram achado neste PR.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";
import { MARGEM, medir as medirPagina, rgba, sup, dSup } from "./_modal_veu.mjs";

let ORIGIN, server, browser;
before(async () => {
  ({ proc: server, origin: ORIGIN } = await startServer());
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); server?.kill(); });

const medir = (opts) => medirPagina(browser, ORIGIN, opts);


/** Tinta (a) IGUAL nos dois temas — logo ninguém a revisou por tema — e (b) a
 *  menos de `MARGEM` de ΔE da superfície do `.modal` ancestral: sumiu. */
function achados(pass, oposta) {
  const outra = new Map(oposta.els.map((e) => [e.i, e]));
  const out = [];
  for (const e of pass.els) {
    const o = outra.get(e.i);
    if (!o) continue;
    const s = sup(e);
    // `border` é o TOPO; `lados` traz direita/baixo/esquerda. Sem eles um elemento
    // com borda só embaixo era invisível a este enumerador POR CONSTRUÇÃO — e a
    // folha tem `border-bottom` sozinho. Varridos: 1151 declarações R/B/L, 20
    // elementos sem borda-topo, 0 achados nos dois temas e com hover on/off.
    const tintas = { background: e.background, border: e.border,
                     ...Object.fromEntries(Object.entries(e.lados || {})
                       .map(([L, c]) => [`border-${L.toLowerCase()}`, c])) };
    const outras = { background: o.background, border: o.border,
                     ...Object.fromEntries(Object.entries(o.lados || {})
                       .map(([L, c]) => [`border-${L.toLowerCase()}`, c])) };
    for (const [prop, cor] of Object.entries(tintas)) {
      if (!cor || cor !== outras[prop]) continue;      // tinta pensada por tema: fora
      const tinta = rgba(cor);
      if (!tinta || tinta.a === 0) continue;           // transparente não é véu
      const d = dSup(tinta, s);
      if (d >= MARGEM) continue;
      out.push(`${e.nome} { ${prop}: ${cor} }  ΔE=${d.toFixed(2)} sobre ${e.superficie}`);
    }
  }
  return [...new Set(out)];
}

const semVeuSumido = (pass, oposta, rotulo) => {
  const l = achados(pass, oposta);
  assert.deepEqual(l, [], `${rotulo}: ${l.length} véu(s) ignorante(s) de tema sumiram\n  ` + l.join("\n  "));
};

test("modal claro: nenhum véu ignorante de tema some na superfície do modal", async () => {
  const claro = await medir({ claro: true }), escuro = await medir({ claro: false });
  assert.ok(claro.els.length > 400, `elementos dentro de modal: ${claro.els.length} — CSS/markup não carregou`);
  assert.ok(claro.usados.length >= 10, `contêineres de innerHTML dentro de modal: ${claro.usados.length}`);
  // CONTROLE POSITIVO do `ramificar()`: os DOIS ramos dos pickers têm de estar no
  // DOM medido. Sem isto o grupo inteiro passa a ser vacuamente verde se o
  // extrator parar de resolver o ternário — que é exatamente o estado anterior,
  // em que o botão selecionado não era medido e por isso "passava".
  const nomes = new Set(claro.els.map((e) => e.nome));
  for (const n of ["button.sw-opt.sw-emoji", "button.sw-opt.sw-emoji.selected",
                   "button.sw-opt.sw-color", "button.sw-opt.sw-color.selected",
                   "button.sw-opt.sw-card", "button.sw-opt.sw-card.selected"])
    assert.ok(nomes.has(n), `ramo ausente do DOM medido: ${n} — o grupo estaria vacuamente verde`);
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

/** Consequência da cegueira (a), que o item 3 descarta POR CONSTRUÇÃO: fill que
 *  EXISTIA no modal escuro e some no claro mesmo tendo valor DIFERENTE por tema.
 *  `achados()` não vê e nunca verá. Discriminador: a fronteira INTEIRA, fill E
 *  borda — quem perde as duas não desenha caixa nenhuma. */
test("modal claro: nenhum elemento perde a fronteira INTEIRA no tema claro", async () => {
  const claro = await medir({ claro: true }), escuro = await medir({ claro: false });
  const esc = new Map(escuro.els.map((e) => [e.i, e]));
  const semFronteira = [], salvosPelaBorda = [];
  for (const e of claro.els) {
    const o = esc.get(e.i); if (!o) continue;
    const [bgC, bgE] = [rgba(e.background), rgba(o.background)];
    if (!bgC || !bgE || bgC.a === 0) continue;
    const dC = dSup(bgC, sup(e)), dEs = dSup(bgE, sup(o));
    if (!(dC < MARGEM && dEs >= MARGEM)) continue;        // o fill não sumiu, ou nem existia
    const dBd = e.border ? dSup(rgba(e.border), sup(e)) : null;
    if (dBd !== null && dBd >= MARGEM) { salvosPelaBorda.push(e.nome); continue; }
    semFronteira.push(`${e.nome} { background: ${e.background} }  claro ΔE=${dC.toFixed(2)}` +
      `  |  escuro ${o.background} ΔE=${dEs.toFixed(2)}  |  borda ` +
      (dBd === null ? "AUSENTE" : `${e.border} ΔE=${dBd.toFixed(2)}`));
  }
  const l = [...new Set(semFronteira)];
  assert.deepEqual(l, [], `${l.length} elemento(s) sem fill NEM borda sobre o modal claro\n  ` + l.join("\n  "));
  // Os salvos pela BORDA ficam NOMEADOS, não só contados — é o que impede este caso
  // de virar depósito. Todos os cinco pintam a borda com `var(--glass-border)`, que
  // no claro dá ΔE 7,33: a fronteira sobrevive por ela, e é por isso que NÃO cabe um
  // override transversal de `--glass-bg`. Nome novo aqui pede conferência à mão.
  assert.deepEqual([...new Set(salvosPelaBorda)].sort(),
    ["button#bill-detail-next.hbtn", "button#bill-detail-prev.hbtn",
     "div.cl-box", "div.rate-tile", "div.stat-tile"],
    "a lista de elementos que perdem o fill no claro e sobrevivem pela BORDA mudou");
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
