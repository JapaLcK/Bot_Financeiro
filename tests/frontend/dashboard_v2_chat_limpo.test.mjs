// Chat do Piggy no /painel (#/piggy), visual limpo (o do app, #1060):
//   · o título "Converse com o Piggy" só existe para leitor de tela e para o foco da rota;
//   · estado vazio: logo, "Olá, {nome}" e "Como posso te ajudar?" no centro da coluna;
//   · as perguntas (sugestões e próximas) ficam numa fileira só, fixa, logo acima da barra;
//   · a resposta leva o autor (ícone e "Piggy", parts/PiggyChat.tsx), fora do .msg-by; só a de exemplo leva a linha do selo;
//   · o guia destaca o chip da fileira sem o balão nem o Piggy cobrirem a barra.
// Só a aparência: o conteúdo das respostas é de dashboard_v2_chat*.test.mjs.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PAINEL, exigeArtefatoEmDia, passouATrava, servir } from "./_painel.mjs";
import { PASSOS, FAZER, abrir as abrirGuia, esperaTitulo, bora, irAoAlvo, navegador, piggyEm } from "./_guia.mjs";

let browser;
navegador(); // o navegador do `abrirGuia` (_guia.mjs); o deste arquivo abre o próprio
before(async () => {
  exigeArtefatoEmDia();
  browser = await chromium.launch();
});
after(() => browser?.close());

const TELAS = [[1280, 800], [375, 812]];

// `conta`: o corpo do /auth/dashboard-profile; uma promise = a resposta só sai quando ela resolve (carregando).
async function abrir({ width, height, conta = { display_name: "Marina", email: "marina@exemplo.com" }, hash = "#/piggy", hasTouch = false }) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce", hasTouch });
  await servir(ctx, undefined, { plano: "pro", perfil: "padrao" });
  await ctx.route("**/auth/dashboard-profile", async (r) => {
    const c = await conta;
    return r.fulfill({ json: { user_id: 1, whatsapp_linked: true, ...c } });
  });
  const page = await ctx.newPage();
  const erros = [], pedidos = [];
  page.on("pageerror", (e) => erros.push(e.message));
  page.on("request", (r) => { if (r.url().includes("/auth/dashboard-profile")) pedidos.push(r.url()); });
  await page.goto(`${PAINEL}${hash}`);
  await page.locator("#page-title").waitFor({ state: "attached" });
  return { ctx, page, erros, pedidos };
}
const saudacao = (page) => page.locator(".chat-empty h2").textContent();
const perguntar = async (page, texto) => {
  await page.locator("#askbar-input").fill(texto);
  await page.locator("#askbar-input").press("Enter");
  await page.locator(".chat > .msg-piggy").last().waitFor();
};

for (const [width, height] of TELAS) {
  test(`${width}: o título é só do leitor de tela e recebe o foco ao entrar na rota`, async () => {
    const { ctx, page, erros } = await abrir({ width, height, hash: "#/" });
    await page.locator("#board-profile").waitFor();
    await page.evaluate(() => { location.hash = "#/piggy"; });
    await page.locator(".chat-empty").waitFor();
    const r = await page.evaluate(() => {
      const h = document.querySelector("#page-title"), b = h.getBoundingClientRect();
      return [h.tagName, h.textContent, b.width <= 1 && b.height <= 1, document.activeElement === h, document.querySelectorAll(".page-lede, .page-head").length];
    });
    await ctx.close();
    assert.deepEqual(r, ["H1", "Converse com o Piggy", true, true, 0]);
    assert.deepEqual(erros, []);
  });

  test(`${width}: "Olá, nome": o nome da conta, senão a parte do e-mail, senão "Você"; carregando, só "Olá"`, async () => {
    const vistos = [];
    for (const conta of [{ display_name: "  Marina  ", email: "m@x.com" }, { display_name: null, email: "joao.silva@exemplo.com" }, { display_name: " ", email: null }]) {
      const { ctx, page } = await abrir({ width, height, conta });
      await page.waitForFunction(() => /^Olá, /.test(document.querySelector(".chat-empty h2")?.textContent ?? ""));
      vistos.push(await saudacao(page));
      await ctx.close();
    }
    let solta;
    const lenta = new Promise((ok) => { solta = ok; });
    const { ctx, page } = await abrir({ width, height, conta: lenta });
    await page.locator(".chat-empty h2").waitFor();
    vistos.push(await saudacao(page));
    solta({ display_name: "Marina", email: null });
    await page.waitForFunction(() => document.querySelector(".chat-empty h2").textContent === "Olá, Marina");
    await ctx.close();
    assert.deepEqual(vistos, ["Olá, Marina", "Olá, joao.silva", "Olá, Você", "Olá"]);
  });

  test(`${width}: a saudação fica no centro da coluna e as pílulas numa fileira só, logo acima da barra`, async () => {
    const { ctx, page } = await abrir({ width, height });
    await page.locator(".chat-follow button").first().waitFor();
    const r = await page.evaluate(() => {
      const box = (s) => document.querySelector(s).getBoundingClientRect();
      const pag = box(".page"), h2 = box(".chat-empty h2"), img = box(".chat-empty img"), ul = box(".chat-follow"), bar = box(".askbar");
      const bs = [...document.querySelectorAll(".chat-follow button")].map((b) => b.getBoundingClientRect());
      return {
        centroH2: Math.abs(h2.left + h2.width / 2 - (pag.left + pag.width / 2)),
        centroLogo: Math.abs(img.left + img.width / 2 - (pag.left + pag.width / 2)),
        centroBarra: Math.abs(ul.left + ul.width / 2 - (bar.left + bar.width / 2)),
        larguraIgual: Math.abs(ul.width - bar.width),
        tops: [...new Set(bs.map((b) => b.top))].length,
        n: bs.length,
        vao: bar.top - ul.bottom,
        toque: Math.min(...bs.map((b) => b.height)),
        rolaDeLado: document.querySelector(".chat-follow").scrollWidth > document.querySelector(".chat-follow").clientWidth,
        overflowX: getComputedStyle(document.querySelector(".chat-follow")).overflowX,
        rotulo: document.querySelector(".chat-follow").getAttribute("aria-label"),
        subtitulo: document.querySelector(".chat-empty p").textContent,
        saudacaoAcima: h2.bottom < ul.top,
        rolaPagina: document.scrollingElement.scrollHeight - innerHeight,
        docLargo: document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth,
      };
    });
    await ctx.close();
    assert.ok(r.centroH2 <= 8, `saudação a ${r.centroH2}px do centro`);
    assert.ok(r.centroLogo <= 8, `logo a ${r.centroLogo}px do centro`);
    assert.ok(r.centroBarra <= 1 && r.larguraIgual <= 1, JSON.stringify(r)); // mesma coluna da barra
    assert.equal(r.tops, 1, "todas as pílulas no mesmo top");
    assert.equal(r.n, 5);
    assert.ok(r.vao >= 4 && r.vao <= 16, `o fundo da fileira fica ${r.vao}px acima da barra`);
    assert.ok(r.toque >= 44, `área de toque de ${r.toque}px`);
    assert.ok(r.rolaDeLado, "as 5 perguntas passam da largura: a fileira rola de lado");
    assert.equal(r.overflowX, "auto", "a fileira rola de lado de fato (hidden também passaria no scrollWidth)");
    assert.equal(r.rotulo, "Sugestões");
    assert.equal(r.subtitulo, "Como posso te ajudar?");
    assert.ok(r.saudacaoAcima);
    assert.ok(r.rolaPagina <= 0, `o estado vazio não rola a página (${r.rolaPagina}px)`);
    assert.equal(r.docLargo, 0);
  });

  test(`${width}: depois de perguntar, o .msg-by é só o selo (o autor é outra linha); só a de exemplo leva o selo; as perguntas saem da conversa`, async () => {
    const { ctx, page, erros } = await abrir({ width, height });
    await perguntar(page, "como estão minhas metas?"); // real
    await page.locator(".chat > .msg-piggy").last().locator(".pilula").first().waitFor();
    const real = await page.locator(".chat > .msg-piggy").last().evaluate((li) => [li.dataset.dado, li.querySelectorAll(".msg-by").length]);
    await passouATrava(page);
    await page.locator(".chat-follow button").first().click(); // exemplo, com blocos (a mais alta)
    await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-piggy").length === 2 && document.querySelector(".chat > .msg-piggy:last-child .msg-block"));
    const r = await page.evaluate(() => {
      const lis = [...document.querySelectorAll(".chat > .msg-piggy")];
      window.scrollTo(0, document.scrollingElement.scrollHeight);
      return {
        dados: lis.map((li) => li.dataset.dado),
        selos: lis.map((li) => li.querySelectorAll(".msg-by > .selo").length),
        by: lis.map((li) => li.querySelector(".msg-by")?.textContent.trim() ?? null),
        piggyNoTexto: lis.map((li) => !!li.querySelector(".msg-by img") || [...li.querySelectorAll(".msg-by")].some((e) => /Piggy/.test(e.textContent))),
        iconeDireto: lis.map((li) => li.querySelectorAll(":scope > img, .msg-by img").length),
        dentroDoChat: document.querySelectorAll(".chat .chat-follow").length,
        fileiras: document.querySelectorAll(".chat-follow").length,
        rotulo: document.querySelector(".chat-follow").getAttribute("aria-label"),
        // o autor da resposta (ícone, "Piggy"): primeiro filho, visível, separado do .msg-by
        autor: lis.map((li) => { const e = li.firstElementChild, b = e.getBoundingClientRect(); return [e.className, e.textContent, b.width <= 1 && b.height <= 1]; }),
      };
    });
    // O fundo da última mensagem fica acima do topo das pílulas, com a página rolada até o fim.
    await page.waitForTimeout(100);
    const folga = await page.evaluate(() => {
      const ul = document.querySelector(".chat-follow").getBoundingClientRect(), ult = document.querySelector(".chat > li:last-child").getBoundingClientRect();
      return [Math.round(ult.bottom), Math.round(ul.top), document.querySelector(".chat-follow li") !== null];
    });
    await ctx.close();
    assert.deepEqual(real, ["real", 0]);
    assert.deepEqual(r.dados, ["real", "exemplo"]);
    assert.deepEqual(r.selos, [0, 1]);
    assert.deepEqual(r.by, [null, "demonstração"]);
    assert.deepEqual(r.piggyNoTexto, [false, false]);
    assert.deepEqual(r.iconeDireto, [0, 0]);
    assert.equal(r.dentroDoChat, 0);
    assert.equal(r.fileiras, 1);
    assert.equal(r.rotulo, "Próximas perguntas");
    assert.deepEqual(r.autor, [["msg-autor", "Piggy", false], ["msg-autor", "Piggy", false]]);
    assert.ok(folga[2] && folga[0] <= folga[1], `a última mensagem termina em ${folga[0]}px e a fileira começa em ${folga[1]}px`);
    assert.deepEqual(erros, []);
  });
}

// O guia: o chip do passo do Piggy vai para o pé da tela, junto da barra. O balão e o Piggy não
// podem cobri-la nem sair da tela, e o chip (que pode estar fora da fileira, que rola) tem de vir à vista.
// 320 de largura: o chip do guia ("Meu gasto deste mês...") é mais largo que a fileira se não for cortado.
for (const [width, height] of [...TELAS, [320, 568], [2560, 1440], [1920, 1080]]) {
  test(`${width}: o guia destaca o chip da fileira; balão e Piggy não cobrem a barra nem saem da tela`, async () => {
    const { ctx, page, erros } = await abrirGuia({ width, height });
    await bora(page);
    await irAoAlvo(page, '[data-guia="mes.trocar"]');
    await FAZER["mes.trocado"](page);
    await esperaTitulo(page, PASSOS[1].fala.titulo);
    await FAZER["categoria.aberta"](page);
    await esperaTitulo(page, PASSOS[2].fala.titulo);
    await page.locator('[data-guia="piggy.chip"]').waitFor();
    await irAoAlvo(page, '[data-guia="piggy.chip"]');
    const r = await page.evaluate(() => {
      const box = (e) => { const b = e.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom }; };
      const cruza = (a, b) => a.l < b.r && a.r > b.l && a.t < b.b && a.b > b.t;
      const dentro = (a) => a.l >= 0 && a.t >= 0 && a.r <= innerWidth && a.b <= innerHeight;
      const barra = box(document.querySelector(".askbar")), balao = box(document.querySelector(".guia-balao"));
      const chip = document.querySelector('[data-guia="piggy.chip"]'), fileira = box(document.querySelector(".chat-follow"));
      const p = window.caixaDoPiggy(), piggy = { l: p.left, t: p.top, r: p.right, b: p.bottom };
      const c = box(chip);
      return {
        balaoCobreBarra: cruza(balao, barra), piggyCobreBarra: cruza(piggy, barra),
        balaoDentro: dentro(balao), piggyDentro: dentro(piggy),
        chipVisivel: Math.round(Math.min(c.r, fileira.r) - Math.max(c.l, fileira.l)) >= Math.round(Math.min(c.r - c.l, fileira.r - fileira.l)) - 1,
        balaoCobreChip: cruza(balao, c),
        // o Piggy encosta na quina de cima do chip (guia-posicao.ts): no máximo os 8 px dele
        piggyNoChip: Math.round(Math.max(0, Math.min(piggy.b, c.b) - Math.max(piggy.t, c.t))) <= 8,
        chipNaFileira: chip.closest(".chat-follow") !== null,
      };
    });
    const encosta = await piggyEm(page, '[data-guia="piggy.chip"]');
    // o toque no chip responde a pergunta (a ação do passo)
    await FAZER["piggy.perguntou"](page);
    await esperaTitulo(page, "Fechou!");
    await ctx.close();
    assert.deepEqual(r, { balaoCobreBarra: false, piggyCobreBarra: false, balaoDentro: true, piggyDentro: true, chipVisivel: true, balaoCobreChip: false, piggyNoChip: true, chipNaFileira: true }, JSON.stringify(encosta));
    assert.deepEqual(erros, []);
  });
}

// --- Consertos do ataque do Tester ---

test("375: duplo toque numa pílula manda uma pergunta; o toque depois da janela manda a segunda", async () => {
  const vistos = {};
  for (const intervalo of [120, 250, 400, 700]) {
    const { ctx, page, erros } = await abrir({ width: 375, height: 812, hasTouch: true });
    await page.locator(".chat-follow button").first().waitFor();
    const b = await page.locator(".chat-follow button").first().boundingBox();
    const x = b.x + b.width / 2, y = b.y + b.height / 2;
    await page.touchscreen.tap(x, y);
    await page.locator(".chat > .msg-user").first().waitFor();
    await page.waitForTimeout(intervalo);
    await page.touchscreen.tap(x, y); // o mesmo ponto: agora cai numa pílula da fileira nova
    await page.waitForTimeout(300);
    // A pergunta entra depois de conferir a conta (`ask`): sob carga a leitura passa dos 300 ms, então a 2ª pode demorar a aparecer.
    if (intervalo === 700) await page.locator(".chat > .msg-user").nth(1).waitFor({ timeout: 3000 }).catch(() => {});
    vistos[intervalo] = await page.locator(".chat > .msg-user").count();
    await ctx.close();
    assert.deepEqual(erros, []);
  }
  assert.deepEqual(vistos, { 120: 1, 250: 1, 400: 1, 700: 2 });
});

test("375: a fileira de cada resposta começa do início, sem herdar a rolagem lateral da anterior", async () => {
  const { ctx, page, erros } = await abrir({ width: 375, height: 812 });
  const fileira = page.locator(".chat-follow");
  await page.locator(".chat-follow button").first().click(); // "Onde eu consigo economizar este mês?"
  await page.locator(".chat > .msg-user").first().waitFor();
  await passouATrava(page);
  const rolou = await fileira.evaluate((ul) => { ul.scrollLeft = ul.scrollWidth; return ul.scrollLeft; }); // quem rolou até o fim para ler as outras
  await page.locator(".chat-follow button").nth(1).click(); // a do meio: a fileira seguinte também rola de lado
  await page.waitForFunction(() => document.querySelectorAll(".chat > .msg-user").length === 2);
  await page.waitForTimeout(50);
  const r = await fileira.evaluate((ul) => ({ left: ul.scrollLeft, rola: ul.scrollWidth > ul.clientWidth }));
  await ctx.close();
  assert.ok(rolou > 0 && r.rola, `o caso só mede com as duas fileiras roláveis (rolou ${rolou}, rola ${r.rola})`);
  assert.equal(r.left, 0, `a fileira nova herdou scrollLeft ${r.left} e esconde as primeiras perguntas`);
  assert.deepEqual(erros, []);
});

for (const [width, height] of [[1280, 800], [375, 812]]) {
  test(`${width}: o anel de foco da pílula cabe na fileira (o overflow não o corta)`, async () => {
    const { ctx, page } = await abrir({ width, height });
    await page.locator(".chat-follow button").first().waitFor();
    await page.keyboard.press("Tab"); // a modalidade vira teclado: o foco por script também é :focus-visible
    const medidas = [];
    for (const i of [0, 4]) {
      await page.locator(".chat-follow button").nth(i).focus();
      await page.waitForTimeout(50);
      medidas.push(await page.evaluate((i) => {
        const ul = document.querySelector(".chat-follow"), u = ul.getBoundingClientRect();
        const bt = ul.querySelectorAll("button")[i], c = bt.getBoundingClientRect(), cs = getComputedStyle(bt);
        const fora = parseFloat(cs.outlineOffset) + parseFloat(cs.outlineWidth); // quanto o anel passa da caixa da pílula
        return { i, estilo: cs.outlineStyle, cima: c.top - fora - u.top, baixo: u.bottom - (c.bottom + fora), esq: c.left - fora - u.left, dir: u.right - (c.right + fora) };
      }, i));
    }
    await ctx.close();
    for (const m of medidas) {
      assert.equal(m.estilo, "solid", JSON.stringify(m));
      // a ponta que a fileira rola até o limite do foco (esq da 1ª, dir da última) também tem de caber
      assert.ok(m.cima >= 0 && m.baixo >= 0, `anel cortado em cima/embaixo: ${JSON.stringify(m)}`);
      assert.ok(m.i === 0 ? m.esq >= 0 : m.dir >= -0.5, /* -0.5: fração de pixel da rolagem */ `anel cortado na ponta: ${JSON.stringify(m)}`);
    }
  });
}

test("375 e 320: nome longo sem espaço quebra e a página não rola de lado", async () => {
  for (const [width, conta] of [[375, { display_name: "x".repeat(50), email: "a@b.com" }], [320, { display_name: null, email: "joao.silva.santos.pereira@gmail.com" }]]) {
    const { ctx, page } = await abrir({ width, height: 700, conta });
    await page.waitForFunction(() => /^Olá, /.test(document.querySelector(".chat-empty h2")?.textContent ?? ""));
    const r = await page.evaluate(() => {
      const d = document.scrollingElement, h = document.querySelector(".chat-empty h2").getBoundingClientRect();
      return { largo: d.scrollWidth - d.clientWidth, h2: h.right - innerWidth };
    });
    await ctx.close();
    assert.ok(r.largo <= 0 && r.h2 <= 0, `${width}: ${JSON.stringify(r)}`);
  }
});

test("1280: display_name que não é texto não derruba a página (cai no e-mail)", async () => {
  const vistos = [];
  for (const display_name of [123, { a: 1 }, ["x"], true]) {
    const { ctx, page, erros } = await abrir({ width: 1280, height: 800, conta: { display_name, email: "marina@exemplo.com" } });
    await page.waitForFunction(() => /^Olá, /.test(document.querySelector(".chat-empty h2")?.textContent ?? ""));
    vistos.push([await saudacao(page), erros]);
    await ctx.close();
  }
  assert.deepEqual(vistos, Array(4).fill(["Olá, marina", []]));
});

// Com a conversa aberta o perfil também é lido: é como a troca de conta noutra aba chega ao `contaDaSessao` (Codex no #1157).
// Perguntar de outra página lê duas vezes: a pergunta confere a conta antes de entrar (`ask`) e o chat confere ao montar.
test("1280: o perfil é lido uma vez no estado vazio; perguntar de outra página o lê na pergunta e ao abrir o chat", async () => {
  const vazio = await abrir({ width: 1280, height: 800 });
  await vazio.page.waitForFunction(() => /^Olá, /.test(document.querySelector(".chat-empty h2")?.textContent ?? ""));
  const noVazio = vazio.pedidos.length;
  await vazio.ctx.close();
  const { ctx, page, erros, pedidos } = await abrir({ width: 1280, height: 800, hash: "#/" });
  await page.locator("#board-profile").waitFor();
  await perguntar(page, "como estão minhas metas?"); // sai de outra rota e monta a conversa já com mensagens
  await page.waitForTimeout(300);
  await ctx.close();
  assert.equal(noVazio, 1);
  assert.equal(pedidos.length, 2);
  assert.deepEqual(erros, []);
});

test("375 e 320: a pílula mais longa nunca passa da fileira (reticências); o nome acessível é o texto inteiro", async () => {
  for (const [width, height] of [[375, 812], [320, 568]]) {
    const { ctx, page, erros } = await abrir({ width, height });
    await page.locator(".chat-follow button").first().waitFor();
    const r = await page.evaluate(() => {
      const ul = document.querySelector(".chat-follow"), u = ul.getBoundingClientRect();
      const bs = [...ul.querySelectorAll("button")].map((b) => ({ w: b.getBoundingClientRect().width, cortada: b.scrollWidth > b.clientWidth, texto: b.textContent, ellipsis: getComputedStyle(b).textOverflow }));
      return { fileira: u.width, maior: bs.reduce((a, b) => (b.w > a.w ? b : a)), bs };
    });
    const nome = await page.locator(".chat-follow button", { hasText: "acima ou abaixo do normal" }).evaluate((b) => b.textContent);
    await ctx.close();
    assert.ok(r.bs.every((b) => b.w <= r.fileira + 0.5), `${width}: pílula de ${r.maior.w}px numa fileira de ${r.fileira}px`);
    assert.ok(r.bs.some((b) => b.cortada), `${width}: alguma pílula é cortada com reticências`);
    assert.equal(r.maior.ellipsis, "ellipsis");
    assert.equal(nome, "Meu gasto deste mês tá acima ou abaixo do normal?");
    assert.deepEqual(erros, []);
  }
});
