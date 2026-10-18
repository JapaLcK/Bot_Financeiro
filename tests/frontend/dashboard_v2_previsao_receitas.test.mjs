/**
 * A caixa "Receitas do extrato" no topo de /painel#/previsao e a seção "Receitas na previsão" no fim
 * (webapp/src/dashboard/widgets/ReceitasDoBanco.tsx, `modo` pendentes / respondidas): a `receitas_do_banco`
 * da Previsão, com Sim / Não / Desfazer = POST /api/v2/previsao/receita.
 *   · só a `pendente` pergunta no topo; confirmada e negada saem do topo e moram na seção do fim, com Desfazer;
 *   · a última pendente respondida some com o painel do topo (fica só o anúncio e o alvo do foco, sr-only);
 *   · lista vazia: nada existe; Plus também vê;
 *   · falha do POST (500, 403 de CSRF, 404): mensagem fixa e a lista intacta, com o GET refeito;
 *   · POST no ar trava os botões até a releitura voltar (também depois de sair da página e voltar);
 *     dois cliques no mesmo tique mandam um POST só; sem rede, a mutação falha com aviso, sem POST pendurado;
 *   · resposta perdida com a marca gravada (conexão cai depois do POST): a releitura mostra que pegou;
 *   · o foco só vai para o resultado se o usuário não foi para outro campo; o resultado vai ao leitor de tela;
 *   · servidor sem `receitas_do_banco`: sem caixa e sem erro;
 *   · a resposta do POST é a do MENOR horizonte: com 60 dias na tela, a tela segue em 60;
 *   · nome é texto livre do pagador: nunca vira HTML, e o nome longo não estoura a largura.
 * Mock com estado em _receitas.mjs; bordas de foco, rolagem e anúncio em dashboard_v2_previsao_receitas_borda.test.mjs.
 * Rodar:  npm run test:frontend   (artefato: `npm --prefix webapp run build`)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { RESPOSTAS, exigeArtefatoEmDia } from "./_painel.mjs";
import { ALFA, MARIA, JOAO, CAIXA, SECAO, PEND, RESP, abrir as abrirComBrowser, texto, dito, alerta, painelVisivel, botoesDasCaixas, semPendente } from "./_receitas.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abrir = (opts) => abrirComBrowser(browser, opts);

test("pendente: nome, valor do texto da API, dia e a pergunta; Sim e Não rotulados, 44 px; as respondidas ficam na seção do fim, fora do topo", async () => {
  const { ctx, page, erros } = await abrir();
  const pend = await texto(page, PEND);
  const botoes = await page.locator(`${PEND} button`).evaluateAll((bs) => bs.map((b) => [b.textContent, b.getAttribute("aria-label"), b.getBoundingClientRect().height]));
  const resp = await texto(page, RESP);
  const titulo = (await texto(page, `${SECAO} h2`))[0];
  const noTopo = await page.locator(`${CAIXA} ul[aria-label="Receitas respondidas"], ${CAIXA} button[aria-label^="Desfazer"], ${CAIXA} .selo`).count();
  await ctx.close();
  assert.deepEqual(pend, ["Empresa Alfa Ltda R$ 4.000,00 todo dia 4. É sua receita fixa? Sim Não"]);
  assert.deepEqual(botoes.map(([t, a]) => [t, a]), [["Sim", "Sim, é receita fixa: Empresa Alfa Ltda"], ["Não", "Não é receita fixa: Empresa Alfa Ltda"]]);
  assert.ok(botoes.every(([, , h]) => h >= 44), JSON.stringify(botoes));
  assert.equal(titulo, "Receitas na previsão");
  assert.deepEqual(resp, ["Maria Souza R$ 1.500,00 Confirmada todo dia 10 Desfazer", "Pix do João R$ 250,50 Recusada todo dia 20 Desfazer"]);
  assert.equal(noTopo, 0);
  assert.deepEqual(erros, []);
});

test("Sim: POST com corpo e CSRF do cookie, a linha sai da pendente e vai para as respondidas como Confirmada; o foco não se perde", async () => {
  const { ctx, page, posts } = await abrir();
  await page.getByRole("button", { name: "Sim, é receita fixa: Empresa Alfa Ltda" }).click();
  await semPendente(page);
  await page.waitForFunction(() => document.activeElement?.classList.contains("rec-foco"));
  const foco = await page.evaluate(() => document.activeElement?.closest("article")?.id + "|" + document.activeElement?.tagName);
  const resp = await texto(page, RESP);
  const lede = (await texto(page, `${CAIXA} .rec-foco`))[0];
  const falado = await dito(page);
  const topo = await painelVisivel(page, CAIXA);
  await ctx.close();
  assert.equal(topo, false, "sem pendente, o painel do topo some");
  assert.deepEqual(posts, [{ corpo: { chave: ALFA, resposta: "sim" }, csrf: "tok-123" }]);
  assert.deepEqual(resp, ["Empresa Alfa Ltda R$ 4.000,00 Confirmada todo dia 4 Desfazer", "Maria Souza R$ 1.500,00 Confirmada todo dia 10 Desfazer", "Pix do João R$ 250,50 Recusada todo dia 20 Desfazer"]);
  assert.equal(foco, "w-previsao-receitas|P");
  assert.equal(lede, "Nenhuma receita a confirmar.");
  assert.equal(falado, "Empresa Alfa Ltda: confirmada.");
});

test("Não: vai para as respondidas como Recusada", async () => {
  const { ctx, page, posts } = await abrir();
  await page.getByRole("button", { name: "Não é receita fixa: Empresa Alfa Ltda" }).click();
  await semPendente(page);
  const alfa = await texto(page, `${RESP}:has-text("Empresa Alfa")`);
  const falado = await dito(page);
  await ctx.close();
  assert.deepEqual(posts.map((p) => p.corpo), [{ chave: ALFA, resposta: "nao" }]);
  assert.deepEqual(alfa, ["Empresa Alfa Ltda R$ 4.000,00 Recusada todo dia 4 Desfazer"]);
  assert.equal(falado, "Empresa Alfa Ltda: recusada.");
});

test("última pendente respondida: o painel do topo some (sem a frase na tela, anúncio sr-only), a receita aparece na seção com Desfazer; Desfazer a traz de volta ao topo", async () => {
  const receitas = [
    { chave: ALFA, nome: "Empresa Alfa Ltda", valor: "4000.00", dia: 4, status: "pendente" },
    { chave: MARIA, nome: "Maria Souza", valor: "1500.00", dia: 10, status: "confirmada" },
  ];
  const { ctx, page, posts } = await abrir({ receitas });
  const antes = [await painelVisivel(page, CAIXA), await painelVisivel(page, SECAO)];
  await page.getByRole("button", { name: "Sim, é receita fixa: Empresa Alfa Ltda" }).click();
  await semPendente(page);
  await page.locator(RESP, { hasText: "Empresa Alfa" }).waitFor();
  const apos = [await painelVisivel(page, CAIXA), await painelVisivel(page, SECAO)];
  const frase = await page.getByText("Nenhuma receita a confirmar.").evaluate((p) => { const r = p.closest(".sr-only").getBoundingClientRect(); return [r.width, r.height]; });
  const secao = await texto(page, RESP);
  const falado = await dito(page);
  await page.getByRole("button", { name: "Desfazer resposta: Empresa Alfa Ltda" }).click();
  await page.locator(PEND, { hasText: "Empresa Alfa" }).waitFor();
  const volta = [await painelVisivel(page, CAIXA), await painelVisivel(page, SECAO)];
  const pend = await texto(page, PEND);
  const resp = await texto(page, RESP);
  await ctx.close();
  assert.deepEqual(antes, [true, true]);
  assert.deepEqual(apos, [false, true]);
  assert.ok(frase[0] <= 2 && frase[1] <= 2, `a frase sobrou na tela: ${frase}`);
  assert.deepEqual(secao, ["Empresa Alfa Ltda R$ 4.000,00 Confirmada todo dia 4 Desfazer", "Maria Souza R$ 1.500,00 Confirmada todo dia 10 Desfazer"]);
  assert.equal(falado, "Empresa Alfa Ltda: confirmada.");
  assert.deepEqual(volta, [true, true]);
  assert.deepEqual(pend, ["Empresa Alfa Ltda R$ 4.000,00 todo dia 4. É sua receita fixa? Sim Não"]);
  assert.deepEqual(resp, ["Maria Souza R$ 1.500,00 Confirmada todo dia 10 Desfazer"]);
  assert.deepEqual(posts.map((p) => p.corpo.resposta), ["sim", "desfazer"]);
});

test("duas pendentes: Sim e Não na SEGUNDA mandam a chave dela, não a da primeira", async () => {
  const BETA = "extrato#99aabbccddeeff00";
  const receitas = [
    { chave: ALFA, nome: "Empresa Alfa Ltda", valor: "4000.00", dia: 4, status: "pendente" },
    { chave: BETA, nome: "Beta Serviços", valor: "900.00", dia: 8, status: "pendente" },
    { chave: MARIA, nome: "Maria Souza", valor: "1500.00", dia: 10, status: "confirmada" },
  ];
  const { ctx, page, posts } = await abrir({ receitas });
  await page.getByRole("button", { name: "Não é receita fixa: Beta Serviços" }).click();
  await page.locator(PEND, { hasText: "Beta" }).waitFor({ state: "detached" });
  await page.getByRole("button", { name: "Desfazer resposta: Beta Serviços" }).click();
  await page.locator(PEND, { hasText: "Beta" }).waitFor();
  await page.getByRole("button", { name: "Sim, é receita fixa: Beta Serviços" }).click();
  await page.locator(PEND, { hasText: "Beta" }).waitFor({ state: "detached" });
  const falado = await dito(page);
  await ctx.close();
  assert.deepEqual(posts.map((p) => p.corpo), [{ chave: BETA, resposta: "nao" }, { chave: BETA, resposta: "desfazer" }, { chave: BETA, resposta: "sim" }]);
  assert.equal(falado, "Beta Serviços: confirmada.");
});

test("Desfazer: a confirmada e a recusada voltam a pendente, cada uma com a sua pergunta", async () => {
  const { ctx, page, posts } = await abrir();
  await page.getByRole("button", { name: "Desfazer resposta: Maria Souza" }).click();
  await page.locator(PEND, { hasText: "Maria Souza" }).waitFor();
  await page.getByRole("button", { name: "Desfazer resposta: Pix do João" }).click();
  await page.locator(PEND, { hasText: "Pix do João" }).waitFor();
  const pend = await texto(page, PEND);
  await page.locator(RESP).first().waitFor({ state: "detached" });
  const secao = await painelVisivel(page, SECAO);
  const falado = await dito(page);
  await ctx.close();
  assert.deepEqual(posts.map((p) => p.corpo), [{ chave: MARIA, resposta: "desfazer" }, { chave: JOAO, resposta: "desfazer" }]);
  assert.equal(pend.length, 3);
  assert.equal(secao, false, "sem respondidas, a seção some");
  assert.equal(falado, "Pix do João: resposta desfeita.");
});

test("lista vazia: nada existe; só respondidas: nada no topo, só a seção, sem Sim/Não; Plus também vê a caixa", async () => {
  const vazia = await abrir({ receitas: [] });
  const v = await vazia.page.locator(`${CAIXA}, ${SECAO}`).count();
  await vazia.ctx.close();
  const r = RESPOSTAS.previsao.pro30_receitas.receitas_do_banco;
  const so = await abrir({ receitas: r.slice(1) });
  const s = [await so.page.locator(PEND).count(), await so.page.getByRole("button", { name: /^Sim/ }).count(), await so.page.locator(CAIXA).count(), (await texto(so.page, RESP)).length];
  await so.ctx.close();
  const plus = await abrir({ plano: "plus" });
  const p = await texto(plus.page, PEND);
  await plus.page.getByRole("button", { name: /^Sim/ }).click();
  await semPendente(plus.page);
  const postou = plus.posts.length;
  await plus.ctx.close();
  assert.equal(v, 0);
  assert.deepEqual(s, [0, 0, 0, 2]);
  assert.equal(p.length, 1);
  assert.equal(postou, 1);
});

for (const [nome, falha] of [
  ["500", { status: 500, json: { error: { code: "internal", message: "Internal Server Error" } } }],
  ["403 do CSRF", { status: 403, json: { detail: "CSRF token missing" } }],
  ["404", { status: 404, json: { error: { code: "not_found", message: "Not Found" } } }],
]) {
  test(`POST com ${nome}: mensagem fixa, a lista intacta, o GET refeito; a segunda tentativa funciona`, async () => {
    const { ctx, page, posts, gets } = await abrir({ post: (_, n) => (n === 1 ? falha : undefined) });
    const antes = gets.length;
    await page.getByRole("button", { name: "Sim, é receita fixa: Empresa Alfa Ltda" }).click();
    await page.locator(`${CAIXA} [role=alert]`, { hasText: "Não deu para salvar. Tente de novo." }).waitFor();
    while (gets.length === antes) await page.waitForTimeout(20);
    const msg = (await texto(page, `${CAIXA} [role=alert]`))[0];
    const mudo = await dito(page);
    const pend = await texto(page, PEND);
    const habilitado = await page.getByRole("button", { name: /^Sim/ }).isEnabled();
    const foco = await page.evaluate(() => document.activeElement?.getAttribute("role"));
    await page.getByRole("button", { name: /^Sim/ }).click();
    await semPendente(page);
    const msg2 = (await texto(page, `${CAIXA} [role=alert]`))[0];
    await ctx.close();
    assert.equal(msg, "Não deu para salvar. Tente de novo.");
    assert.equal(mudo, "", "a falha real não pode anunciar sucesso");
    assert.equal(pend.length, 1);
    assert.ok(habilitado);
    assert.equal(foco, "alert");
    assert.equal(posts.length, 2);
    assert.equal(msg2, "");
  });
}

test("POST no ar: Sim, Não e Desfazer travados; só destravam DEPOIS da releitura voltar", async () => {
  let soltarPost, soltarGet;
  const trava = new Promise((ok) => { soltarPost = ok; });
  const lenta = new Promise((ok) => { soltarGet = ok; });
  const { ctx, page, posts, gets } = await abrir({ post: () => trava, get: (n) => (n ? lenta : undefined) });
  await page.getByRole("button", { name: /^Sim/ }).click();
  while (!posts.length) await page.waitForTimeout(20);
  await page.locator(`${CAIXA} button:disabled, ${SECAO} button:disabled`).first().waitFor();
  const travados = () => botoesDasCaixas(page);
  const durante = await travados();
  const antes = gets.length;
  soltarPost();
  while (gets.length === antes) await page.waitForTimeout(20);
  await page.waitForTimeout(300); // a releitura está no ar: o POST já respondeu e os botões seguem travados
  const releitura = await travados();
  soltarGet();
  await semPendente(page);
  const depois = await travados();
  await ctx.close();
  assert.ok(durante.length >= 2 && durante.every(Boolean), JSON.stringify(durante));
  assert.ok(releitura.every(Boolean), `destravou com a releitura no ar: ${JSON.stringify(releitura)}`);
  assert.ok(depois.length >= 3 && depois.every((d) => !d), JSON.stringify(depois));
});

test("sair da página com o POST no ar e voltar: os botões nascem travados e destravam ao responder", async () => {
  let soltar;
  const trava = new Promise((ok) => { soltar = ok; });
  const { ctx, page, posts, ir } = await abrir({ post: () => trava });
  await page.getByRole("button", { name: /^Sim/ }).click();
  while (!posts.length) await page.waitForTimeout(20);
  await ir("/");
  await page.locator("#board-profile").waitFor();
  await ir("/previsao");
  await page.locator(CAIXA).waitFor();
  const durante = await botoesDasCaixas(page);
  soltar();
  await semPendente(page);
  const depois = await botoesDasCaixas(page);
  await ctx.close();
  assert.ok(durante.length >= 2 && durante.every(Boolean), JSON.stringify(durante));
  assert.ok(depois.length >= 3 && depois.every((d) => !d), JSON.stringify(depois));
  assert.equal(posts.length, 1);
});

test("resposta perdida (o POST grava e a conexão cai): a releitura mostra que pegou, sem aviso de erro", async () => {
  const { ctx, page, posts } = await abrir({ post: () => ({ perdida: true }) });
  await page.getByRole("button", { name: /^Sim/ }).click();
  await semPendente(page);
  await page.waitForFunction(() => document.activeElement?.classList.contains("rec-foco"));
  const msg = await alerta(page);
  const resp = await texto(page, RESP);
  const falado = await dito(page);
  await ctx.close();
  assert.equal(posts.length, 1);
  assert.equal(msg, "");
  assert.equal(resp.length, 3);
  assert.equal(falado, "Empresa Alfa Ltda: confirmada.");
});

test("offline: o Sim falha com o aviso, os botões destravam e nenhum POST sai depois", async () => {
  const { ctx, page, posts } = await abrir();
  const saidos = [];
  page.on("request", (q) => { if (q.method() === "POST") saidos.push(q.url()); });
  // o Playwright atende rota mesmo offline: a rota que derruba tudo faz o papel da rede que sumiu
  const semRede = (rota) => rota.abort("internetdisconnected");
  await ctx.route("**/api/v2/previsao**", semRede);
  await ctx.setOffline(true);
  await page.getByRole("button", { name: /^Sim/ }).click();
  await page.locator(`${CAIXA} [role=alert]`, { hasText: "Não deu para salvar. Tente de novo." }).waitFor({ timeout: 5000 });
  const botoes = await page.locator(`${CAIXA} button`).evaluateAll((bs) => bs.map((b) => b.disabled));
  const tentativas = saidos.length;
  await ctx.unroute("**/api/v2/previsao**", semRede);
  await ctx.setOffline(false);
  await page.waitForTimeout(600);
  const pend = await texto(page, PEND);
  await ctx.close();
  assert.ok(botoes.length >= 2 && botoes.every((d) => !d), JSON.stringify(botoes));
  assert.equal(tentativas, 1);
  assert.equal(saidos.length, 1, "um POST pendurado saiu quando a rede voltou");
  assert.equal(posts.length, 0);
  assert.equal(pend.length, 1);
});

test("POST lento: se o usuário foi digitar no chat do Piggy, o foco e o texto ficam no campo", async () => {
  let soltar;
  const trava = new Promise((ok) => { soltar = ok; });
  const { ctx, page, posts } = await abrir({ post: () => trava });
  await page.getByRole("button", { name: /^Sim/ }).click();
  while (!posts.length) await page.waitForTimeout(20);
  const campo = page.locator("[placeholder^=Converse]");
  await campo.click();
  await page.keyboard.type("quanto posso gast");
  soltar();
  await semPendente(page);
  await page.waitForTimeout(300);
  await page.keyboard.type("ar hoje");
  const valor = await campo.inputValue();
  const noCampo = await campo.evaluate((c) => c === document.activeElement);
  await ctx.close();
  assert.equal(valor, "quanto posso gastar hoje");
  assert.ok(noCampo);
});

test("servidor sem `receitas_do_banco` (deploy fora de ordem): sem caixa, sem erro, a Previsão normal", async () => {
  const { ctx, page, erros } = await abrir({ receitas: null });
  const caixa = await page.locator(CAIXA).count();
  const hora = await page.locator("#w-previsao .prev-hora").count();
  await ctx.close();
  assert.equal(caixa, 0);
  assert.equal(hora, 1);
  assert.deepEqual(erros, []);
});

test("dois cliques no mesmo tique (antes do re-render): um POST só, no Sim e no Desfazer", async () => {
  const { ctx, page, posts } = await abrir();
  const duplo = (sel) => page.locator(sel).first().evaluate((b) => { b.click(); b.click(); });
  await duplo(`${PEND} button`);
  await semPendente(page);
  await page.waitForLoadState("networkidle");
  await duplo(`${RESP} button`);
  await page.locator(PEND).waitFor();
  await page.waitForLoadState("networkidle");
  await ctx.close();
  assert.deepEqual(posts.map((p) => p.corpo.resposta), ["sim", "desfazer"]);
});

test("horizonte: com 60 dias na tela, responder relê o 60 e a tela segue em 60 (a resposta do POST é a de 30)", async () => {
  const { ctx, page, posts, gets } = await abrir();
  await page.getByRole("radio", { name: "60 dias" }).click();
  await page.waitForFunction(() => document.querySelector("#w-previsao .hero-sub")?.textContent.includes("5 de dezembro"));
  await page.getByRole("button", { name: /^Sim/ }).click();
  await semPendente(page);
  const sub = await page.locator("#w-previsao .hero-sub").textContent();
  const ultimo = gets.at(-1);
  await ctx.close();
  assert.equal(posts.length, 1);
  assert.match(sub, /5 de dezembro/);
  assert.equal(ultimo, "?dias=60");
});

test("nome do pagador é texto: <script> e <img onerror> não viram elemento; nome longo corta e não estoura a largura (390 px)", async () => {
  const longo = "Fulano de Tal Comércio e Serviços de Informática e Telecomunicações do Brasil Sociedade Limitada ME";
  const receitas = [
    { chave: ALFA, nome: "<script>window.__xss=1</script>", valor: "10.00", dia: 1, status: "pendente" },
    { chave: MARIA, nome: "<img src=x onerror=window.__xss=2>", valor: "20.00", dia: 2, status: "pendente" },
    { chave: JOAO, nome: longo, valor: "1234567.89", dia: 3, status: "pendente" },
  ];
  const { ctx, page, erros } = await abrir({ receitas, width: 390 });
  const r = await page.evaluate(() => ({
    xss: window.__xss ?? null,
    scripts: document.querySelectorAll("#w-previsao-receitas script, #w-previsao-receitas img").length,
    nomes: [...document.querySelectorAll("#w-previsao-receitas .rec-nome")].map((n) => [n.textContent, n.scrollWidth > n.clientWidth]),
    larg: [document.documentElement.scrollWidth, innerWidth],
  }));
  await ctx.close();
  assert.equal(r.xss, null);
  assert.equal(r.scripts, 0);
  assert.deepEqual(r.nomes.map(([n]) => n), receitas.map((x) => x.nome));
  assert.equal(r.nomes[2][1], true, "o nome longo precisa estar cortado");
  assert.ok(r.larg[0] <= r.larg[1], `rolagem horizontal: ${r.larg}`);
  assert.deepEqual(erros, []);
});

// PAINEL_SHOT=<pasta>: guarda as capturas da verificação visual.
for (const [nome, width] of [["desktop", 1280], ["mobile", 390]]) {
  test(`layout ${nome} (${width} px): sem rolagem horizontal, a caixa acima do saldo, a seção das respondidas depois de Compromissos e Como foi feita, botões dentro dos recipientes`, async () => {
    const { ctx, page } = await abrir({ width });
    const m = await page.evaluate(() => {
      const c = document.querySelector("#w-previsao-receitas").getBoundingClientRect();
      const h = document.querySelector("#w-previsao").getBoundingClientRect();
      const sec = document.querySelector("#w-previsao-receitas-respondidas").getBoundingClientRect();
      const fim = Math.max(...["#w-previsao-qualidade", "#w-previsao-compromissos"].map((q) => document.querySelector(q)?.getBoundingClientRect().bottom ?? 0));
      const fora = (raiz, caixa) => [...document.querySelectorAll(`${raiz} button`)].map((b) => b.getBoundingClientRect()).filter((b) => b.left < caixa.left || b.right > caixa.right).length;
      return { larg: [document.documentElement.scrollWidth, innerWidth], caixaAcima: c.bottom <= h.top, secaoDepois: sec.top >= fim, caixa: [c.left, c.right],
        fora: fora("#w-previsao-receitas", c) + fora("#w-previsao-receitas-respondidas", sec) };
    });
    if (process.env.PAINEL_SHOT) {
      mkdirSync(process.env.PAINEL_SHOT, { recursive: true });
      await page.screenshot({ path: `${process.env.PAINEL_SHOT}/previsao-${nome}.png`, fullPage: true });
    }
    await ctx.close();
    assert.ok(m.larg[0] <= m.larg[1], `rolagem horizontal: ${m.larg}`);
    assert.equal(m.caixaAcima, true);
    assert.equal(m.secaoDepois, true);
    assert.equal(m.fora, 0);
  });
}
