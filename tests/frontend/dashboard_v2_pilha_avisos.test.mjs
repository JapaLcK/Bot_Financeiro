/**
 * A pilha de avisos no topo do /painel (webapp/src/dashboard/parts/PilhaAvisos.tsx + AvisosDoTopo.tsx), par da pilha do app (#1076, PR 1):
 *   · um aviso por vez, pontos "Aviso k de n", âmbar para o banco e rosa para a receita; o BANCO vem antes das receitas;
 *   · receitas pendentes só em `/` e em `/previsao` (e só aí o GET /previsao do anfitrião); o banco, em toda tela, menos /piggy e a Conta;
 *   · a seleção é o ID: quando o aviso visível some, a pilha cai no primeiro, nunca num card vazio;
 *   · cada aviso tem os seus botões (o "Sim" de um não vira o "Sim" do próximo);
 *   · falha do POST: o alerta fica FORA da pilha; o X do banco vale até recarregar; teclado ← →;
 *   · medidas: cabe na coluna em 1440 e 375, alvo de 44 px, contraste, sem animação com movimento reduzido;
 *   · sem aviso (banco em dia, sem receita pendente): a pilha não existe.
 * Mock com estado das receitas em _receitas.mjs; o banco desatualizado vem da fixture `bancos.pilha_desatualizado` (a `desatualizado` é a da tela Bancos).
 * Rodar:  npm run test:frontend   (artefato: `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { exigeArtefatoEmDia, abrirPainel, RESPOSTAS } from "./_painel.mjs";
import { con, LIMITE } from "./_bancos.mjs";
import { ALFA, MARIA, abrir as abrirReceitas, CAIXA, dito, alertas, pontos, verAviso } from "./_receitas.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const PILHA = `${CAIXA} section.pilha`;
const TITULO = `${PILHA} .pilha-titulo`;
const BETA = "extrato#99aabbccddeeff00";
const DUAS = [
  { chave: ALFA, nome: "Empresa Alfa Ltda", valor: "4000.00", dia: 4, status: "pendente" },
  { chave: BETA, nome: "Beta Serviços", valor: "900.00", dia: 8, status: "pendente" },
];
const UMA = [DUAS[0], { chave: MARIA, nome: "Maria Souza", valor: "1500.00", dia: 10, status: "confirmada" }];
const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }).reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
const razao = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const titulo = (page) => page.locator(TITULO).innerText().then((t) => t.replace(/\s+/g, " ").trim());

/** O Resumo com banco desatualizado e uma receita pendente: a pilha de 2 avisos. */
async function abrirResumo(opts = {}) {
  const r = await abrirReceitas(browser, { bancos: "pilha_desatualizado", receitas: UMA, ...opts });
  await r.ir("/");
  await r.page.locator(PILHA).waitFor();
  return r;
}

test("P1: banco desatualizado + 1 receita pendente em `/`: UM card, o banco primeiro (âmbar), pontos 1 de 2; o ponto 2 mostra a receita (rosa)", async () => {
  for (const width of [1440, 375]) {
    const { ctx, page, erros } = await abrirResumo({ width });
    const cards = await page.locator(`${PILHA} .pilha-card`).count();
    const t1 = await titulo(page);
    const tom1 = await page.locator(PILHA).getAttribute("data-tom");
    const texto1 = await page.locator(`${PILHA} .pilha-texto`).innerText();
    const link = await page.getByRole("link", { name: "Ver bancos conectados" }).getAttribute("href");
    const rotulos = await pontos(page).evaluateAll((bs) => bs.map((b) => [b.getAttribute("aria-label"), b.getAttribute("aria-current")]));
    await verAviso(page, 2);
    const t2 = await titulo(page);
    const tom2 = await page.locator(PILHA).getAttribute("data-tom");
    const corpo2 = await page.locator(`${PILHA} .pilha-card`).innerText();
    const atual = await pontos(page).evaluateAll((bs) => bs.map((b) => b.getAttribute("aria-current")));
    await ctx.close();
    assert.equal(cards, 1, `${width}px: mais de um card`);
    assert.equal(t1, "Aviso 1 de 2: Dados do banco desatualizados");
    assert.equal(tom1, "alerta");
    assert.equal(texto1, "Os dados de Itaú, Bradesco precisam ser atualizados. Alguns valores podem estar incompletos.");
    assert.equal(link, "#/conta/bancos");
    assert.deepEqual(rotulos, [["Aviso 1 de 2: Dados do banco desatualizados", "true"], ["Aviso 2 de 2: Receitas do extrato, Empresa Alfa Ltda", null]]);
    assert.equal(t2, "Aviso 2 de 2: Receitas do extrato");
    assert.equal(tom2, "decisao");
    assert.match(corpo2, /Empresa Alfa Ltda/);
    assert.deepEqual(atual, [null, "true"]);
    assert.deepEqual(erros, []);
  }
});

test("P2: Sim na receita da pilha: um POST, o anúncio uma vez, a pilha cai no banco e o foco vai para o título dele", async () => {
  const { ctx, page, posts } = await abrirResumo();
  await verAviso(page, 2);
  await page.getByRole("button", { name: "Sim, é receita fixa: Empresa Alfa Ltda" }).click();
  await pontos(page).first().waitFor({ state: "detached" }); // sobrou o banco: sem pontos
  await page.waitForFunction(() => document.activeElement?.classList.contains("pilha-titulo"));
  const foco = await page.evaluate(() => document.activeElement.textContent);
  const falado = await dito(page);
  const regioes = await page.locator(`${CAIXA} [role=status] span`).count();
  await ctx.close();
  assert.deepEqual(posts.map((p) => p.corpo), [{ chave: ALFA, resposta: "sim" }]);
  assert.equal(foco, "Dados do banco desatualizados");
  assert.equal(falado, "Empresa Alfa Ltda: confirmada.");
  assert.equal(regioes, 1);
});

test("P2b: duas receitas, Sim na 1ª: o card da 2ª é de OUTRO aviso (o botão Sim é um nó novo) e o foco vai para o título dele", async () => {
  const { ctx, page } = await abrirReceitas(browser, { receitas: DUAS });
  await page.locator(PILHA).waitFor();
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).evaluate((b) => { b.__daAlfa = true; });
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).focus();
  await page.keyboard.press("Enter");
  await page.locator(`${PILHA} :text("Beta Serviços")`).waitFor();
  const herdou = await page.getByRole("button", { name: /^Sim, é receita fixa/ }).evaluate((b) => b.__daAlfa === true);
  await page.waitForFunction(() => document.activeElement?.classList.contains("pilha-titulo"));
  await ctx.close();
  assert.equal(herdou, false, "o Sim da Beta é o mesmo nó do Sim da Alfa");
});

test("P3: seleção morta: no ponto 2 (receita), responder faz a pilha cair no primeiro aviso, sem card vazio", async () => {
  const { ctx, page } = await abrirResumo();
  await verAviso(page, 2);
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await pontos(page).first().waitFor({ state: "detached" });
  const cards = await page.locator(`${PILHA} .pilha-card`).count();
  const t = await titulo(page);
  await ctx.close();
  assert.equal(cards, 1);
  assert.equal(t, "Dados do banco desatualizados");
});

test("P4: `/gastos` com banco desatualizado: só o banco, ZERO receitas e nenhum GET /previsao; `/piggy` e a Conta: sem pilha", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", bancos: "pilha_desatualizado", previsao: "pro30_receitas" });
  const gets = [];
  page.on("request", (q) => { if (new URL(q.url()).pathname === "/api/v2/previsao") gets.push(q.url()); });
  await ir("/gastos");
  await page.locator(PILHA).waitFor();
  await page.waitForLoadState("networkidle");
  const t = await titulo(page);
  const dots = await pontos(page).count();
  const receitas = await page.locator(`${CAIXA} ul[aria-label="Receitas a confirmar"]`).count();
  const sem = [];
  for (const rota of ["/piggy", "/conta/perfil"]) { await ir(rota); sem.push(await page.locator(PILHA).count()); }
  await ctx.close();
  assert.equal(t, "Dados do banco desatualizados");
  assert.equal(dots, 0);
  assert.equal(receitas, 0);
  assert.deepEqual(gets, [], "o anfitrião leu a previsão numa tela que não mostra receita");
  assert.deepEqual(sem, [0, 0]);
});

test("P4b: `/previsao` também tem as receitas na pilha, depois do banco", async () => {
  const { ctx, page } = await abrirReceitas(browser, { bancos: "pilha_desatualizado", receitas: UMA });
  await page.locator(PILHA).waitFor();
  const rotulos = await pontos(page).evaluateAll((bs) => bs.map((b) => b.getAttribute("aria-label")));
  await ctx.close();
  assert.deepEqual(rotulos, ["Aviso 1 de 2: Dados do banco desatualizados", "Aviso 2 de 2: Receitas do extrato, Empresa Alfa Ltda"]);
});

test("P4c: `/previsao` em 60 dias: a pilha segue com a pendente, e nenhum GET do horizonte padrão sai por causa dela", async () => {
  const { ctx, page, gets } = await abrirReceitas(browser, { receitas: UMA });
  await page.getByRole("radio", { name: "60 dias" }).click();
  await page.waitForFunction(() => document.querySelector("#w-previsao .hero-sub")?.textContent.includes("5 de dezembro"));
  await page.waitForLoadState("networkidle");
  const card = await page.locator(`${PILHA}`).innerText();
  const antes = gets.length;
  // Aba volta ao primeiro plano: toda consulta ativa se relê (o `visibilitychange` é o evento que o TanStack escuta).
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true }); document.dispatchEvent(new Event("visibilitychange", { bubbles: true })); });
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true }); document.dispatchEvent(new Event("visibilitychange", { bubbles: true })); });
  await page.waitForTimeout(400);
  const depois = gets.slice(antes);
  await ctx.close();
  assert.match(card, /Empresa Alfa Ltda/);
  assert.ok(!gets.slice(1).includes(""), `GET sem horizonte: ${JSON.stringify(gets)}`);
  assert.ok(!depois.includes(""), `GET sem horizonte no foco: ${JSON.stringify(depois)}`);
});

test("P5: falha do POST da receita: o alerta fica FORA da pilha (antes dela), sem 'Aviso k de n'", async () => {
  const erro = { status: 500, json: { error: { code: "internal", message: "Internal Server Error" } } };
  const { ctx, page } = await abrirResumo({ post: () => erro });
  await verAviso(page, 2);
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await page.waitForFunction(() => document.activeElement?.getAttribute("role") === "alert");
  const m = await page.evaluate(() => {
    const a = document.querySelector("#avisos-topo [role=alert]");
    const p = document.querySelector("#avisos-topo section.pilha");
    return { texto: a.textContent, dentro: p.contains(a), antes: !!(a.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_FOLLOWING) };
  });
  const t = await titulo(page);
  const [topo] = await alertas(page);
  await ctx.close();
  assert.equal(m.texto, "Não deu para salvar. Tente de novo.");
  assert.equal(m.dentro, false);
  assert.equal(m.antes, true);
  assert.equal(t, "Aviso 2 de 2: Receitas do extrato", "a receita segue na pilha para tentar de novo");
  assert.equal(topo, "Não deu para salvar. Tente de novo.");
});

test("P6: o X do banco fecha o aviso, e ele continua fechado ao trocar de tela; volta ao recarregar a página", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", bancos: "pilha_desatualizado" });
  await ir("/");
  await page.locator(PILHA).waitFor();
  await page.getByRole("button", { name: "Fechar aviso" }).click();
  await page.locator(PILHA).waitFor({ state: "detached" });
  await ir("/gastos");
  await page.waitForLoadState("networkidle");
  const aoTrocar = await page.locator(PILHA).count();
  await page.reload();
  await page.locator(PILHA).waitFor();
  const aoRecarregar = await page.locator(PILHA).count();
  await ctx.close();
  assert.equal(aoTrocar, 0);
  assert.equal(aoRecarregar, 1);
});

test("P7: teclado: Tab chega aos pontos, ← → trocam de aviso levando o foco, e o aria-current acompanha", async () => {
  const { ctx, page } = await abrirResumo();
  await page.locator(TITULO).focus();
  let n = 0;
  while (n++ < 8 && !(await page.evaluate(() => document.activeElement?.classList.contains("pilha-ponto")))) await page.keyboard.press("Tab");
  const noPonto = await page.evaluate(() => [...document.querySelectorAll(".pilha-ponto")].indexOf(document.activeElement));
  const estado = () => page.evaluate(() => ({
    foco: [...document.querySelectorAll(".pilha-ponto")].indexOf(document.activeElement),
    atual: [...document.querySelectorAll(".pilha-ponto")].findIndex((b) => b.getAttribute("aria-current") === "true"),
    titulo: document.querySelector(".pilha-titulo").textContent,
  }));
  await page.keyboard.press("ArrowRight");
  const dir = await estado();
  await page.keyboard.press("ArrowRight"); // no último: fica
  const fim = await estado();
  await page.keyboard.press("ArrowLeft");
  const esq = await estado();
  await ctx.close();
  assert.equal(noPonto, 0, "Tab não chegou ao 1º ponto");
  assert.deepEqual([dir.foco, dir.atual], [1, 1]);
  assert.equal(dir.titulo, "Aviso 2 de 2: Receitas do extrato");
  assert.deepEqual([fim.foco, fim.atual], [1, 1]);
  assert.deepEqual([esq.foco, esq.atual], [0, 0]);
  assert.equal(esq.titulo, "Aviso 1 de 2: Dados do banco desatualizados");
});

for (const [nome, width] of [["desktop", 1440], ["mobile", 375]]) {
  test(`V1 ${nome} (${width} px): a pilha cabe na coluna do <main>, pontos de 44 px, sem rolagem lateral e acima da barra de baixo`, async () => {
    const { ctx, page } = await abrirResumo({ width });
    const m = await page.evaluate(() => {
      const r = (q) => document.querySelector(q).getBoundingClientRect();
      const main = r("main"), p = r("section.pilha"), tab = r(".tabbar");
      const tabVisivel = getComputedStyle(document.querySelector(".tabbar")).display !== "none";
      return {
        dentro: p.left >= main.left - 0.5 && p.right <= main.right + 0.5,
        pontos: [...document.querySelectorAll(".pilha-ponto")].map((b) => [b.getBoundingClientRect().width, b.getBoundingClientRect().height]),
        larg: [document.documentElement.scrollWidth, innerWidth],
        acimaDaBarra: !tabVisivel || p.bottom <= tab.top,
      };
    });
    await ctx.close();
    assert.ok(m.dentro);
    assert.ok(m.pontos.length === 2 && m.pontos.every(([w, h]) => w >= 44 && h >= 44), JSON.stringify(m.pontos));
    assert.ok(m.larg[0] <= m.larg[1], `rolagem horizontal: ${m.larg}`);
    assert.ok(m.acimaDaBarra);
  });
}

test("V2: movimento reduzido: sem animação de subida; sem a preferência, a animação existe (controle)", async () => {
  const { ctx, page } = await abrirResumo();
  const anim = () => page.locator(`${PILHA} .pilha-card`).evaluate((c) => getComputedStyle(c).animationName);
  const reduzido = await anim();
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const normal = await anim();
  await ctx.close();
  assert.equal(reduzido, "none");
  assert.equal(normal, "pilha-sobe");
});

test("V3: contraste medido: texto ≥ 4,5 sobre o fundo tingido; borda e ícone ≥ 3 sobre o fundo da página, nos dois tons", async () => {
  const { ctx, page } = await abrirResumo();
  const medir = async () => page.evaluate(() => {
    const cor = (s) => (s.match(/[\d.]+/g) ?? []).map(Number);
    const sobre = (f, b) => { const [r, g, bl, a = 1] = f; return [0, 1, 2].map((i) => Math.round([r, g, bl][i] * a + b[i] * (1 - a))); };
    const fundoPagina = cor(getComputedStyle(document.body).backgroundColor);
    const card = document.querySelector(".pilha-card");
    const cs = getComputedStyle(card);
    const fundo = sobre(cor(cs.backgroundColor), fundoPagina);
    const q = (s) => cor(getComputedStyle(document.querySelector(s)).color);
    return {
      fundoPagina, fundo,
      titulo: sobre(q(".pilha-titulo"), fundo),
      texto: document.querySelector(".pilha-texto") ? sobre(q(".pilha-texto"), fundo) : null,
      borda: sobre(cor(cs.borderTopColor), fundoPagina),
      icone: sobre(q(".pilha-icone"), fundo),
    };
  });
  const lido = {};
  lido.alerta = await medir();
  await verAviso(page, 2);
  lido.decisao = await medir();
  await ctx.close();
  const falhas = [];
  for (const [tom, m] of Object.entries(lido)) {
    for (const k of ["titulo", "texto"]) if (m[k] && razao(m[k], m.fundo) < 4.5) falhas.push(`${tom}.${k} ${razao(m[k], m.fundo).toFixed(2)}`);
    for (const k of ["borda", "icone"]) if (razao(m[k], k === "borda" ? m.fundoPagina : m.fundo) < 3) falhas.push(`${tom}.${k} ${razao(m[k], k === "borda" ? m.fundoPagina : m.fundo).toFixed(2)}`);
  }
  assert.deepEqual(falhas, [], JSON.stringify(lido));
});

test("controle positivo: banco em dia e nenhuma receita pendente: a pilha não existe (em `/` e em `/previsao`)", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", previsao: "pro30" });
  const contagens = [];
  for (const rota of ["/", "/previsao", "/gastos"]) { await ir(rota); await page.waitForLoadState("networkidle"); contagens.push(await page.locator("section[aria-label=Avisos]").count()); }
  const so = await abrirReceitas(browser, { receitas: [UMA[1]] }); // só respondida: a pendente é que vira aviso
  await so.ir("/");
  await so.page.waitForLoadState("networkidle");
  contagens.push(await so.page.locator("section[aria-label=Avisos]").count());
  await so.ctx.close();
  await ctx.close();
  assert.deepEqual(contagens, [0, 0, 0, 0]);
});

// ---- Correções da revisão (Tester, revisor e Codex) ----

const TOPO = "#avisos-topo";
const ERRO_500 = { status: 500, json: { error: { code: "internal", message: "Internal Server Error" } } };
/** A aba some e volta: o TanStack relê toda consulta ativa (é o evento `visibilitychange` que ele escuta). */
const refocar = async (page) => {
  for (const v of ["hidden", "visible"]) await page.evaluate((x) => { Object.defineProperty(document, "visibilityState", { value: x, configurable: true }); document.dispatchEvent(new Event("visibilitychange", { bubbles: true })); }, v);
};
/** `n` receitas pendentes de pagadores diferentes (mesmo título de aviso). */
const pendentes = (n) => Array.from({ length: n }, (_, i) => ({ chave: `extrato#${(i + 1).toString(16).padStart(16, "0")}`, nome: `Pagador ${i + 1}`, valor: "100.00", dia: 1 + (i % 28), status: "pendente" }));

for (const [n, width] of [[12, 375], [12, 320], [41, 1440]]) {
  test(`D1 (${n} avisos, ${width} px): a linha de pontos quebra em linhas, sem rolagem lateral e com alvos de 44 px`, async () => {
    const { ctx, page } = await abrirReceitas(browser, { width, bancos: "pilha_desatualizado", receitas: pendentes(n - 1) });
    await page.locator(PILHA).waitFor();
    const m = await page.evaluate(() => ({
      larg: [document.documentElement.scrollWidth, innerWidth],
      pontos: [...document.querySelectorAll(".pilha-ponto")].map((b) => { const r = b.getBoundingClientRect(); return [r.width, r.height, r.right <= innerWidth + 0.5]; }),
    }));
    await ctx.close();
    assert.equal(m.pontos.length, n);
    assert.ok(m.pontos.every(([w, h, dentro]) => w >= 44 && h >= 44 && dentro), JSON.stringify(m.pontos));
    assert.equal(m.larg[0], m.larg[1], `rolagem lateral: ${m.larg}`);
  });
}

test("D2: o ponto inativo mede >= 3:1 contra a página nos dois tons (WCAG 1.4.11), e o ativo se distingue pela forma (cheio e maior)", async () => {
  const { ctx, page } = await abrirResumo();
  const medir = () => page.evaluate(() => {
    const cor = (s) => (s.match(/[\d.]+/g) ?? []).map(Number);
    const fundo = cor(getComputedStyle(document.body).backgroundColor);
    return [...document.querySelectorAll(".pilha-ponto")].map((b) => {
      const sp = b.firstElementChild, cs = getComputedStyle(sp);
      const anel = parseFloat(cs.borderTopWidth) > 0 ? cor(cs.borderTopColor) : cor(cs.backgroundColor); // o que se vê: o anel ou o miolo
      const [r, g, bl, a = 1] = anel, o = parseFloat(cs.opacity) * a;
      return { tom: b.dataset.tom, atual: b.getAttribute("aria-current") === "true", lado: sp.getBoundingClientRect().width, cheio: cor(cs.backgroundColor)[3] !== 0, visto: [r, g, bl].map((v, i) => Math.round(v * o + fundo[i] * (1 - o))), fundo };
    });
  });
  const lido = await medir();
  await ctx.close();
  assert.equal(lido.length, 2);
  for (const p of lido) assert.ok(razao(p.visto, p.fundo) >= 3, `${p.tom}${p.atual ? " (atual)" : ""}: ${razao(p.visto, p.fundo).toFixed(2)}:1`);
  const [ativo, inativo] = [lido.find((p) => p.atual), lido.find((p) => !p.atual)];
  assert.ok(ativo.cheio && !inativo.cheio && ativo.lado > inativo.lado, JSON.stringify(lido));
});

test("D3: plano Essencial em `/`: nenhum GET /previsao (nem no foco da aba) e nenhuma pilha; o Pro lê a previsão uma vez e vê a receita", async () => {
  const gets = (page) => { const l = []; page.on("request", (q) => { if (new URL(q.url()).pathname === "/api/v2/previsao" && q.method() === "GET") l.push(q.url()); }); return l; };
  const ess = await abrirPainel(browser, { espera: "#page-title", plano: "essencial" });
  const lEss = gets(ess.page);
  await ess.ir("/");
  await ess.page.waitForLoadState("networkidle");
  await refocar(ess.page);
  await ess.page.waitForTimeout(300);
  const pilhaEss = await ess.page.locator("section[aria-label=Avisos]").count();
  await ess.ctx.close();
  const pro = await abrirReceitas(browser, { receitas: UMA });
  const lPro = gets(pro.page);
  await pro.ir("/");
  await pro.page.locator(PILHA).waitFor();
  await pro.page.waitForLoadState("networkidle");
  const receita = await pro.page.locator(PILHA).innerText();
  await pro.ctx.close();
  assert.deepEqual(lEss, []);
  assert.equal(pilhaEss, 0);
  assert.match(receita, /Empresa Alfa Ltda/);
  assert.equal(lPro.length, 1, `o Pro deveria ler UMA vez: ${lPro}`);
  assert.ok(!lPro[0].includes("dias"), `sem horizonte (a chave do card): ${lPro[0]}`);
});

test("D4: `/bancos` sem a lista `motivos`: o painel NÃO fica em branco (sem pageerror) e não aparece aviso", async () => {
  const sem = await abrirPainel(browser, { espera: "#page-title" });
  await sem.ctx.route("**/api/v2/bancos", (r) => r.fulfill({ json: { total: null, bancos: [] } }));
  await sem.ir("/");
  await sem.page.waitForLoadState("networkidle");
  const corpo = await sem.page.locator("#page-title").innerText();
  const avisos = await sem.page.locator("section[aria-label=Avisos]").count();
  await sem.ctx.close();
  assert.deepEqual(sem.erros, []);
  assert.ok(corpo.length > 0);
  assert.equal(avisos, 0);
});

test("D5: o alvo 'Nenhum aviso.' NÃO existe em tela sem aviso; nasce só quando o último aviso sai pela resposta e some quando o foco sai dele", async () => {
  const sem = await abrirPainel(browser, { espera: "#page-title" });
  const contagens = [];
  for (const rota of ["/gastos", "/", "/previsao"]) { await sem.ir(rota); await sem.page.waitForLoadState("networkidle"); contagens.push(await sem.page.getByText("Nenhum aviso.").count()); }
  await sem.ctx.close();
  assert.deepEqual(contagens, [0, 0, 0]);
  const { ctx, page } = await abrirReceitas(browser, { receitas: UMA });
  await page.locator(PILHA).waitFor();
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.activeElement?.textContent === "Nenhum aviso.");
  const existe = await page.getByText("Nenhum aviso.").count();
  await page.evaluate(() => document.activeElement.blur());
  await page.getByText("Nenhum aviso.").waitFor({ state: "detached" });
  await ctx.close();
  assert.equal(existe, 1);
});

/** O Resumo com as duas respostas seguradas até `soltar()`: a troca para 60 dias fica "carregando" por quanto tempo o teste quiser. */
async function seguraHorizonte(ctx) {
  let soltar;
  const segura = new Promise((r) => { soltar = r; });
  await ctx.route("**/api/v2/previsao**", async (rota) => {
    if (rota.request().method() === "GET" && new URL(rota.request().url()).searchParams.get("dias") === "60") await segura;
    return rota.fallback();
  });
  return soltar;
}

test("D6: troca de horizonte na `/previsao` com o 60 dias ainda carregando: a receita pendente fica na pilha, na MESMA posição (nem a seleção zera)", async () => {
  const { ctx, page } = await abrirReceitas(browser, { bancos: "pilha_desatualizado", receitas: UMA });
  await page.locator(PILHA).waitFor();
  await verAviso(page, 2);
  const soltar = await seguraHorizonte(ctx);
  await page.getByRole("radio", { name: "60 dias" }).click();
  await page.locator("#w-previsao").getByText("Carregando a previsão…").waitFor();
  const durante = { titulo: await titulo(page), sim: await page.getByRole("button", { name: /^Sim, é receita fixa/ }).count() };
  soltar();
  await page.waitForFunction(() => document.querySelector("#w-previsao .hero-sub")?.textContent.includes("5 de dezembro"));
  const depois = await titulo(page);
  await ctx.close();
  assert.deepEqual(durante, { titulo: "Aviso 2 de 2: Receitas do extrato", sim: 1 });
  assert.equal(depois, "Aviso 2 de 2: Receitas do extrato");
});

test("D6b: o Sim em voo e a troca de horizonte juntos: o botão da pilha não desmonta com o POST no ar, e a resposta fecha o aviso", async () => {
  let largaPost;
  const { ctx, page, posts } = await abrirReceitas(browser, { receitas: UMA, post: () => new Promise((r) => { largaPost = r; }) });
  await page.locator(PILHA).waitFor();
  const soltar = await seguraHorizonte(ctx);
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await page.getByRole("radio", { name: "60 dias" }).click();
  await page.locator("#w-previsao").getByText("Carregando a previsão…").waitFor();
  const durante = await page.getByRole("button", { name: /^Sim, é receita fixa/ }).count();
  largaPost({});
  soltar();
  await page.locator(PILHA).waitFor({ state: "detached" });
  await ctx.close();
  assert.equal(durante, 1, "o botão sumiu com o POST no ar");
  assert.equal(posts.length, 1);
});

test("D7: três receitas: cada ponto leva o nome do pagador (rótulos todos diferentes), escrito como texto", async () => {
  const lista = pendentes(3);
  lista[1].nome = "<img src=x onerror=alert(1)> Beta";
  const { ctx, page, erros } = await abrirReceitas(browser, { receitas: lista });
  await page.locator(PILHA).waitFor();
  const rotulos = await pontos(page).evaluateAll((bs) => bs.map((b) => b.getAttribute("aria-label")));
  const imgs = await page.locator(`${CAIXA} img`).count();
  await ctx.close();
  assert.deepEqual(rotulos, ["Aviso 1 de 3: Receitas do extrato, Pagador 1", "Aviso 2 de 3: Receitas do extrato, <img src=x onerror=alert(1)> Beta", "Aviso 3 de 3: Receitas do extrato, Pagador 3"]);
  assert.equal(new Set(rotulos).size, 3);
  assert.equal(imgs, 0);
  assert.deepEqual(erros, []);
});

test("D8: o anúncio 'Nome: confirmada.' e o alerta de falha NÃO atravessam telas; e telas sem aviso não têm região de alerta nem de status", async () => {
  const ok = await abrirReceitas(browser, { receitas: UMA });
  await ok.ir("/");
  await ok.page.locator(PILHA).waitFor();
  await ok.page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await ok.page.waitForFunction(() => document.querySelector("#avisos-topo [role=status]")?.textContent.includes("confirmada"));
  await ok.ir("/gastos");
  await ok.page.waitForLoadState("networkidle");
  const regioesOk = await ok.page.locator(`${TOPO} [role=status], ${TOPO} [role=alert]`).count();
  await ok.ctx.close();
  assert.equal(regioesOk, 0, "o status velho ficou pendurado em /gastos");

  const falha = await abrirReceitas(browser, { receitas: UMA, post: () => ERRO_500 });
  await falha.ir("/");
  await falha.page.locator(PILHA).waitFor();
  await falha.page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await falha.page.waitForFunction(() => document.querySelector("#avisos-topo [role=alert]")?.textContent.includes("Não deu para salvar"));
  await falha.ir("/gastos");
  await falha.page.waitForLoadState("networkidle");
  const regioesFalha = await falha.page.locator(`${TOPO} [role=status], ${TOPO} [role=alert]`).count();
  await falha.ctx.close();
  assert.equal(regioesFalha, 0, "o alerta de falha atravessou a tela");

  const vazia = await abrirPainel(browser, { espera: "#page-title" });
  await vazia.ir("/gastos");
  await vazia.page.waitForLoadState("networkidle");
  const regioesVazia = await vazia.page.locator(`${TOPO} [role=status], ${TOPO} [role=alert]`).count();
  await vazia.ctx.close();
  assert.equal(regioesVazia, 0);
});

test("D9: em `/`, a releitura da previsão com 403 (downgrade) apaga a receita da pilha: 4xx nunca mostra dado guardado", async () => {
  const { ctx, page } = await abrirReceitas(browser, { receitas: UMA });
  await page.getByRole("radio", { name: "30 dias" }).waitFor(); // a /previsao carregou
  await page.goto(page.url().replace(/#.*/, "#/"));
  await page.locator(PILHA).waitFor();
  const e = RESPOSTAS.erros["403_pro_required"];
  await ctx.route("**/api/v2/previsao**", (r) => r.request().method() === "GET" ? r.fulfill({ status: e.status, json: e.body }) : r.fallback());
  await refocar(page);
  await page.locator(PILHA).waitFor({ state: "detached" });
  await ctx.close();
});

test("D10: o Desfazer não ressuscita o aviso que o usuário tinha escolhido: responder o 2º (Beta) e desfazer volta ao PRIMEIRO (Alfa)", async () => {
  const { ctx, page } = await abrirReceitas(browser, { receitas: DUAS });
  await page.locator(PILHA).waitFor();
  await verAviso(page, 2); // sel = Beta (escolha explícita)
  await page.getByRole("button", { name: "Sim, é receita fixa: Beta Serviços" }).click();
  await page.getByRole("button", { name: "Desfazer resposta: Beta Serviços" }).click();
  await pontos(page).nth(1).waitFor();
  const t = await titulo(page);
  const corpo = await page.locator(`${PILHA} .pilha-card`).innerText();
  await ctx.close();
  assert.equal(t, "Aviso 1 de 2: Receitas do extrato");
  assert.match(corpo, /Empresa Alfa Ltda/);
});

for (const [fixture, rotulo] of [["pausada", "conexão pausada"], ["pilha_desatualizado", "banco desatualizado"]]) {
  test(`D11: o gatilho do banco vale para cada motivo sozinho (${rotulo}): o aviso aparece em /gastos`, async () => {
    const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", bancos: fixture });
    await ir("/gastos");
    await page.locator(PILHA).waitFor();
    const t = await titulo(page);
    await ctx.close();
    assert.equal(t, "Dados do banco desatualizados");
  });
}

test("D12: o grupo de pontos tem o nome 'Trocar de aviso' (leitor de tela) e a pilha tem o nome 'Avisos'", async () => {
  const { ctx, page } = await abrirResumo();
  const grupo = await page.getByRole("group", { name: "Trocar de aviso" }).count();
  const regiao = await page.getByRole("region", { name: "Avisos" }).count();
  await ctx.close();
  assert.equal(grupo, 1);
  assert.equal(regiao, 1);
});

test("D13: na `/previsao`, a releitura com 403 (downgrade) apaga a receita da pilha; sem a resposta boa de um horizonte novo a lista antiga não sobrevive ao 4xx", async () => {
  const { ctx, page } = await abrirReceitas(browser, { receitas: UMA });
  await page.locator(PILHA).waitFor();
  const e = RESPOSTAS.erros["403_pro_required"];
  await ctx.route("**/api/v2/previsao**", (r) => r.request().method() === "GET" ? r.fulfill({ status: e.status, json: e.body }) : r.fallback());
  await refocar(page);
  await page.locator(PILHA).waitFor({ state: "detached" });
  await ctx.close();
});

// ---- Passada 2 do Tester ----

const foco = (page) => page.evaluate(() => { const a = document.activeElement; return a === document.body ? "body" : `${a.tagName.toLowerCase()}#${a.id}`; });
const naRota = (page, rota) => page.waitForFunction((r) => document.querySelector("main")?.dataset.page === r, rota);
const nenhumAviso = (page) => page.getByText("Nenhum aviso.").count();

for (const [rota, nome] of [["/", "Resumo"], ["/previsao", "Previsão"]]) {
  test(`A1 (${nome}): sair da tela com receita pendente para uma sem aviso, com o foco no body (hash, voltar, tabbar do iOS): o foco fica no título da tela e NÃO nasce 'Nenhum aviso.'`, async () => {
    const { ctx, page, ir } = await abrirReceitas(browser, { receitas: UMA });
    await ir(rota);
    await page.locator(PILHA).waitFor();
    await page.evaluate(() => { document.activeElement?.blur?.(); location.hash = "#/gastos"; });
    await naRota(page, "/gastos");
    await page.waitForTimeout(500);
    const f = await foco(page), n = await nenhumAviso(page);
    await ctx.close();
    assert.equal(f, "h1#page-title");
    assert.equal(n, 0);
  });
}

test("A2: a releitura que esvazia a pilha (o banco volta a ficar em dia) com o foco no body NÃO move o foco nem cria 'Nenhum aviso.'", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", bancos: "pilha_desatualizado" });
  await ir("/gastos");
  await page.locator(PILHA).waitFor();
  await page.evaluate(() => document.activeElement?.blur?.());
  await ctx.route("**/api/v2/bancos", (r) => r.fulfill({ json: RESPOSTAS.bancos.em_dia }));
  await refocar(page);
  await page.locator(PILHA).waitFor({ state: "detached" });
  await page.waitForTimeout(400);
  const f = await foco(page), n = await nenhumAviso(page);
  await ctx.close();
  assert.equal(f, "body");
  assert.equal(n, 0);
});

test("A3: o alvo 'Nenhum aviso.' que nasceu do Sim não fica para trás: com a pilha de volta e depois vazia por releitura, ele não reaparece", async () => {
  const { ctx, page, ir } = await abrirReceitas(browser, { receitas: UMA });
  await ir("/");
  await page.locator(PILHA).waitFor();
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.activeElement?.textContent === "Nenhum aviso.");
  await ctx.route("**/api/v2/bancos", (r) => r.fulfill({ json: RESPOSTAS.bancos.pilha_desatualizado }));
  await refocar(page);
  await page.locator(PILHA).waitFor();
  await ctx.route("**/api/v2/bancos", (r) => r.fulfill({ json: RESPOSTAS.bancos.em_dia }));
  await refocar(page);
  await page.locator(PILHA).waitFor({ state: "detached" });
  await page.waitForTimeout(400);
  const n = await nenhumAviso(page);
  await ctx.close();
  assert.equal(n, 0);
});

test("A4: o X do último aviso (banco) leva o foco ao alvo 'Nenhum aviso.' (a ação do usuário na pilha, não só o Sim)", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", bancos: "pilha_desatualizado" });
  await ir("/gastos");
  await page.getByRole("button", { name: "Fechar aviso" }).click();
  await page.waitForFunction(() => document.activeElement?.textContent === "Nenhum aviso.");
  await ctx.close();
});

test("O2: clicar no TEXTO do aviso (não em botão) e depois a releitura esvaziar a pilha, com o foco no body, NÃO move o foco; o Sim (botão) continua movendo (P2/A4)", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", bancos: "pilha_desatualizado" });
  await ir("/gastos");
  await page.locator(PILHA).waitFor();
  await page.locator(`${PILHA} .pilha-texto`).click();
  await page.evaluate(() => document.activeElement?.blur?.());
  await ctx.route("**/api/v2/bancos", (r) => r.fulfill({ json: RESPOSTAS.bancos.em_dia }));
  await refocar(page);
  await page.locator(PILHA).waitFor({ state: "detached" });
  await page.waitForTimeout(400);
  const f = await foco(page), n = await nenhumAviso(page);
  await ctx.close();
  assert.equal(f, "body");
  assert.equal(n, 0);
});

test("M3: o POST em voo e a troca de tela: a região `role=status` existe VAZIA antes da resposta e o texto entra NELA depois (o mesmo nó, não uma região que nasce preenchida)", async () => {
  let larga;
  const { ctx, page } = await abrirReceitas(browser, { receitas: UMA, post: () => new Promise((r) => { larga = r; }) });
  await page.locator(PILHA).waitFor();
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await naRota(page, "/gastos");
  const regiao = page.locator(`${TOPO} [role=status]`);
  await regiao.waitFor();
  const antes = { n: await regiao.count(), texto: await regiao.innerText() };
  await regiao.evaluate((el) => { el.dataset.marca = "1"; });
  larga({});
  await page.waitForFunction(() => document.querySelector("#avisos-topo [role=status][data-marca]")?.textContent === "Empresa Alfa Ltda: confirmada.");
  await ctx.close();
  assert.deepEqual(antes, { n: 1, texto: "" });
});

test("X2: `/` Pro com o card em 90 dias; no downgrade o 90 volta 403 horizon e o card NÃO mostra o dado guardado enquanto a releitura não chega; e o foco da aba faz UM GET (o do card), não dois", async () => {
  const { ctx, page, ir } = await abrirReceitas(browser, { receitas: UMA });
  await ir("/");
  await page.locator("#w-hero").getByRole("radio", { name: "90 dias" }).click();
  await page.waitForFunction(() => document.querySelector("#w-hero [aria-checked=true]")?.textContent.includes("90"));
  await page.waitForTimeout(300);
  let solta; const segura = new Promise((r) => { solta = r; });
  const e = RESPOSTAS.erros["403_forecast_horizon_not_allowed"];
  const gets = [];
  await ctx.route("**/api/v2/previsao**", async (r) => {
    if (r.request().method() !== "GET") return r.fallback();
    const dias = new URL(r.request().url()).searchParams.get("dias");
    gets.push(dias);
    if (dias) return r.fulfill({ status: e.status, json: e.body });
    await segura;
    return r.fulfill({ json: { ...RESPOSTAS.previsao.plus30, receitas_do_banco: UMA } });
  });
  await refocar(page);
  await page.waitForTimeout(1500);
  const hero = await page.locator("#w-hero").innerText();
  const radios = await page.locator("#w-hero [role=radio]").count();
  solta();
  await page.waitForFunction(() => !document.querySelector("#w-hero")?.innerText.includes("Carregando a previsão"));
  await ctx.close();
  assert.match(hero, /Carregando a previsão/);
  assert.equal(radios, 0, "o card mostrou o dado guardado do Pro");
  assert.deepEqual(gets, ["90", null], `GETs: ${gets}`); // o 90 (403) e depois o padrão do card; nenhum extra da pilha
});

for (const atraso of [0, 150, 600]) {
  test(`B1 (/perfil atrasado ${atraso} ms): o Pro faz UM GET /previsao ao abrir o Resumo e a pilha mostra a receita`, async () => {
    const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", previsao: "pro30_receitas" });
    await ctx.route("**/api/v2/perfil", async (r) => { if (r.request().method() === "GET") await new Promise((s) => setTimeout(s, atraso)); return r.fallback(); });
    const gets = [];
    page.on("request", (q) => { if (new URL(q.url()).pathname === "/api/v2/previsao" && q.method() === "GET") gets.push(q.url()); });
    await ir("/");
    await page.locator(PILHA).waitFor();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(400);
    await ctx.close();
    assert.equal(gets.length, 1, `GETs: ${gets}`);
  });
}

test("B1b: o perfil 'Economizar' não tem o card 'Saldo previsto': a receita aparece na pilha em `/` do mesmo jeito, com UM GET /previsao (o da pilha)", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", previsao: "pro30_receitas", perfil: "economizar" });
  const gets = [];
  page.on("request", (q) => { if (new URL(q.url()).pathname === "/api/v2/previsao" && q.method() === "GET") gets.push(q.url()); });
  await ir("/");
  await page.locator(PILHA).waitFor();
  await page.waitForLoadState("networkidle");
  const hero = await page.locator("#w-hero").count();
  const texto = await page.locator(PILHA).innerText();
  await ctx.close();
  assert.equal(hero, 0, "o perfil deveria vir sem o card hero");
  assert.match(texto, /Empresa Alfa Ltda/);
  assert.equal(gets.length, 1, `GETs: ${gets}`);
});

test("C1: 5xx no horizonte novo, o Sim: o POST vale uma vez só por clique e o servidor aceita repetir (a lista antiga fica, é limite declarado)", async () => {
  const { ctx, page, posts } = await abrirReceitas(browser, { receitas: UMA });
  await page.locator(PILHA).waitFor();
  await ctx.route("**/api/v2/previsao**", (r) => r.request().method() === "GET" && new URL(r.request().url()).searchParams.get("dias") === "60" ? r.fulfill({ status: 500, json: RESPOSTAS.erros["500"].body }) : r.fallback());
  await page.getByRole("radio", { name: "60 dias" }).click();
  await page.waitForTimeout(2500);
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo [role=status]")?.textContent.includes("confirmada"));
  const aindaLa = await page.locator(PILHA).count();
  await ctx.close();
  assert.equal(posts.length, 1);
  assert.equal(aindaLa, 1, "limite declarado: sem a releitura do horizonte novo a lista antiga fica");
});

test("E2: o POST em voo, o usuário vai para /gastos e o POST volta 500: nenhum alerta em /gastos (sem receita para repetir)", async () => {
  let larga;
  const { ctx, page } = await abrirReceitas(browser, { receitas: UMA, post: () => new Promise((r) => { larga = r; }) });
  await page.locator(PILHA).waitFor();
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await naRota(page, "/gastos");
  larga(ERRO_500);
  await page.waitForTimeout(1200);
  const regioes = await page.locator(`${TOPO} [role=alert]`).count();
  await ctx.close();
  assert.equal(regioes, 0);
});

test("E2b: controle positivo, na tela do POST (`/`): Sim na única pendente, POST 404 e a lista relida vazia: o alerta fica sozinho, sem pilha (limite (c))", async () => {
  const { ctx, page, ir } = await abrirReceitas(browser, { receitas: [DUAS[0]], post: () => ({ status: 404, json: RESPOSTAS.erros["404"].body, sumir: true }) });
  await ir("/");
  await page.locator(PILHA).waitFor();
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await page.waitForFunction(() => document.querySelector("#avisos-topo [role=alert]")?.textContent.includes("Não deu para salvar"));
  const pilha = await page.locator(PILHA).count();
  await ctx.close();
  assert.equal(pilha, 0);
});

test("E3: o POST em voo, o usuário vai para /gastos e o POST volta ok: o anúncio entra na região de status de lá e sai sozinho em 3 s (não fica pendurado)", async () => {
  let larga;
  const { ctx, page } = await abrirReceitas(browser, { receitas: UMA, post: () => new Promise((r) => { larga = r; }) });
  await page.locator(PILHA).waitFor();
  await page.getByRole("button", { name: /^Sim, é receita fixa/ }).click();
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await naRota(page, "/gastos");
  larga({});
  await page.waitForFunction(() => document.querySelector("#avisos-topo [role=status]")?.textContent === "Empresa Alfa Ltda: confirmada.");
  await page.locator(`${TOPO} [role=status]`).waitFor({ state: "detached", timeout: 6000 });
  await ctx.close();
});

test("F1: sair da /previsao e voltar com o GET ainda no ar: a lista velha da página não volta à pilha (ela sai com a página); chegando a resposta, a receita volta", async () => {
  const { ctx, page } = await abrirReceitas(browser, { receitas: UMA });
  await page.locator(PILHA).waitFor();
  await page.evaluate(() => { location.hash = "#/gastos"; });
  await naRota(page, "/gastos");
  await page.waitForLoadState("networkidle"); // a previsão sai do cache (gcTime 0): a volta começa sem dado
  let solta; const segura = new Promise((r) => { solta = r; });
  await ctx.route("**/api/v2/previsao**", async (r) => { if (r.request().method() === "GET") await segura; return r.fallback(); });
  await page.evaluate(() => { location.hash = "#/previsao"; });
  await naRota(page, "/previsao");
  await page.waitForTimeout(500);
  const durante = await page.locator(PILHA).count();
  solta();
  await page.locator(PILHA).waitFor();
  await ctx.close();
  assert.equal(durante, 0, "a lista velha da página voltou à pilha");
});

test("F2: o plano Free em `/`: nenhum GET /previsao (nem no foco da aba) e nenhuma pilha", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", plano: "free" });
  const l = [];
  page.on("request", (q) => { if (new URL(q.url()).pathname === "/api/v2/previsao" && q.method() === "GET") l.push(q.url()); });
  await ir("/");
  await page.waitForLoadState("networkidle");
  await refocar(page);
  await page.waitForTimeout(300);
  const pilha = await page.locator("section[aria-label=Avisos]").count();
  await ctx.close();
  assert.deepEqual(l, []);
  assert.equal(pilha, 0);
});

test("F3: 300 caracteres sem espaço no nome, a 320 px: a pilha cabe na coluna (sem rolagem lateral, o card não passa da tela)", async () => {
  const lista = [{ chave: ALFA, nome: "X".repeat(300), valor: "123456789012.00", dia: 3, status: "pendente" }];
  const { ctx, page } = await abrirReceitas(browser, { width: 320, receitas: lista, bancos: "pilha_desatualizado" });
  await page.locator(PILHA).waitFor();
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth, pilha: document.querySelector(".pilha").getBoundingClientRect().right }));
  await ctx.close();
  assert.ok(m.sw <= m.iw && m.pilha <= m.iw + 0.5, JSON.stringify(m));
});

test("G1: sem GET duplicado de /api/v2/bancos: a pilha lê em `/` (1), a tela Bancos lê a dela (+1) e a pilha não pede nada em /conta/bancos", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", bancos: "pilha_desatualizado" });
  await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "a@b.c", user_id: 7, whatsapp_linked: false } }));
  await ctx.route((u) => u.pathname === "/open-finance/7", (r) => r.fulfill({ json: { ok: true, connections: [con(1, "Nubank")] } }));
  await ctx.route((u) => u.pathname === "/open-finance/7/limite", (r) => r.fulfill({ json: LIMITE() }));
  let gets = 0;
  page.on("request", (q) => { if (new URL(q.url()).pathname === "/api/v2/bancos") gets++; });
  await ir("/");
  await page.locator(PILHA).waitFor();
  await page.waitForTimeout(300);
  const emResumo = gets;
  await page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await page.locator(".banco-card").waitFor();
  await page.waitForFunction(() => !document.querySelector(".banco-total")?.textContent.includes("…"));
  await page.waitForTimeout(500);
  const pilha = await page.locator(PILHA).count();
  await ctx.close();
  assert.equal(emResumo, 1);
  assert.equal(gets, 2);
  assert.equal(pilha, 0);
});

// O snapshot é `paused`: sem conserto pelo app, então o aviso mostra o link (com um banco `updated`/`partial` o botão é o "Atualizar agora", #1076 PR 2).
test("G2: 'Ver bancos conectados' no aviso do banco abre a página Bancos conectados do painel (#/conta/bancos), sem sair do /painel", async () => {
  const { ctx, page, ir } = await abrirPainel(browser, { espera: "#page-title", bancos: "pilha_desatualizado" });
  await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "a@b.c", user_id: 7, whatsapp_linked: false } }));
  await ctx.route((u) => u.pathname === "/open-finance/7", (r) => r.fulfill({ json: { ok: true, connections: [con(1, "Nubank", "paused", "Pausado")] } }));
  await ctx.route((u) => u.pathname === "/open-finance/7/limite", (r) => r.fulfill({ json: LIMITE() }));
  await ir("/");
  await page.locator(PILHA).waitFor();
  await page.getByRole("link", { name: "Ver bancos conectados" }).click();
  await page.waitForFunction(() => document.querySelector("#page-title")?.textContent.trim() === "Bancos conectados");
  const hash = await page.evaluate(() => location.hash);
  const pilha = await page.locator(PILHA).count();
  await ctx.close();
  assert.equal(hash, "#/conta/bancos");
  assert.equal(pilha, 0);
});
