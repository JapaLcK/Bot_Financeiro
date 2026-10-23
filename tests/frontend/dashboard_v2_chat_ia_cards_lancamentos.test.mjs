// A ação de LEITURA dos cards da IA no chat do /painel: tocar numa conta do card `contas` abre os lançamentos dela DENTRO do card
// (webapp: parts/cardIa/LancamentosDoCard.tsx, conta/api.ts `antesDaConta`/`daConta`). Isolamento entre usuários: a conta da sessão é conferida ANTES de
// cada GET (nenhum sai em nome de outra conta) e DEPOIS (a resposta de uma troca durante a busca não é desenhada). L1 a L7 do plano, mais L8 (foco depois
// da troca) e L9 (id de conta que não é inteiro positivo). L10 a L14: o aviso do SSE não relê a lista, o prazo cobre as leituras do perfil, o foco, o item malformado e os selos. A rota `/api/v2/lancamentos` é DO TESTE (_chat_ia_lanc.mjs). Categoria e dia: dashboard_v2_chat_ia_cards_lancamentos_mes.test.mjs.
// Cada grupo foi provado vermelho com uma mutação do código que ele cobre (a tabela está no relato do PR, não aqui).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS, encerrar, iniciar, trocarConta } from "./_chat_ia.mjs";
import { FALHA, SESSAO, ate, prepararCard } from "./_chat_ia_lanc.mjs";

before(iniciar);
after(encerrar);
const F = RESPOSTAS;
// A partir do card `contas`: o mesmo helper dos testes de categoria e dia (_chat_ia_lanc.mjs).
const preparar = (opts = {}, contas = F.contas.todos_os_estados) => prepararCard(opts, { recursos: { contas } });

test("L1 positivo: abrir a conta lê UMA página (conta, mês, 10, sem guardado), conferindo a conta antes e depois; as linhas ficam DENTRO do card", async () => {
  const { page, t, linha, linhas, erros } = await preparar();
  t.ordem.length = 0;
  await linha.click();
  await linhas().first().waitFor();
  assert.equal(t.gets.length, 1);
  const p = t.gets[0].searchParams;
  assert.deepEqual([p.get("conta"), p.get("mes"), p.get("limite"), p.has("guardado"), p.has("cursor")], ["1", "2026-10", "10", false, false]);
  // A conferência pode ler o perfil mais de uma vez (lerContaFresca espera a leitura em voo e relê); o que vale: perfil antes do GET e perfil depois.
  const iGet = t.ordem.indexOf("GET");
  assert.equal(t.ordem.filter((x) => x === "GET").length, 1, "uma leitura");
  assert.ok(iGet > 0 && t.ordem.slice(0, iGet).every((x) => x === "perfil"), "conta lida antes do GET");
  assert.deepEqual(t.ordem.slice(iGet), ["GET", "perfil"], "conta relida logo depois do GET");
  assert.equal(await linhas().count(), 5);
  assert.ok((await page.locator(".card-ia .ci-lanc").innerText()).includes("DE-A-l901"));
  assert.equal(await linha.getAttribute("aria-expanded"), "true");
  assert.equal(await page.locator(".card-ia .ci-lanc a, .card-ia dialog").count(), 0, "sem link e sem folha");
  assert.deepEqual(erros, []);
  await page.context().close();
});

test("L2 troca de conta ANTES do clique (a aba ainda não sabe): nenhum GET de lançamentos sai e o card de A some", async () => {
  const { page, t, linha, conta, cardSumiu } = await preparar();
  conta.uid = 2; // a sessão virou B noutra aba; esta não recebeu o foco
  await linha.click();
  await cardSumiu();
  assert.deepEqual(t.gets, [], "nenhuma requisição de lançamentos saiu em nome de B");
  await page.context().close();
});

test("L3 troca DURANTE a busca: a resposta (marcada DE-B) nunca é desenhada e o card de A some", async () => {
  const { page, t, linha, conta, vistos, cardSumiu } = await preparar();
  t.modo = "preso";
  await linha.click();
  await ate(() => t.soltar);
  conta.uid = 2;
  t.soltar("DE-B");
  await cardSumiu();
  await page.waitForTimeout(300);
  assert.ok(!(await vistos()).includes("DE-B"), "o DE-B nunca entrou no DOM, nem por um quadro");
  assert.ok(!(await vistos()).includes("DE-A"), "e o do A, que já nem chegou a existir");
  await page.context().close();
});

test("L4 erro: 500 em todas as tentativas vira o texto de erro com \"Tentar de novo\" (que recupera); 403 é a sessão, sem \"Tentar de novo\"", async () => {
  const a = await preparar();
  a.t.modo = "500";
  await a.linha.click();
  const erro = a.page.locator(".card-ia .ci-erro");
  await erro.waitFor({ timeout: 12000 });
  assert.equal(await erro.getAttribute("role"), "alert");
  assert.equal(await erro.locator("p").innerText(), FALHA);
  assert.equal(a.t.gets.length, 3, "5xx: três tentativas no total (o retry do projeto), nenhuma a mais");
  a.t.modo = "ok";
  await erro.getByRole("button", { name: "Tentar de novo" }).click();
  await a.linhas().first().waitFor();
  assert.equal(await a.page.locator(".card-ia .ci-erro").count(), 0);
  assert.equal(await a.linhas().count(), 5);
  await a.page.context().close();
  const b = await preparar();
  b.t.modo = "403";
  await b.linha.click();
  await b.page.locator(".card-ia .ci-erro").waitFor();
  assert.equal(await b.page.locator(".card-ia .ci-erro p").innerText(), SESSAO);
  assert.equal(await b.page.getByRole("button", { name: "Tentar de novo" }).count(), 0);
  assert.equal(b.t.gets.length, 1, "4xx não repete");
  await b.page.context().close();
});

test("L5 lentidão: \"Carregando lançamentos…\" com aria-busy; recolher ABORTA o pedido; passados 15 s é o erro com \"Tentar de novo\"", async () => {
  const a = await preparar();
  a.t.modo = "pendurado";
  await a.linha.click();
  await a.page.locator(".card-ia .ci-lanc[aria-busy=true]").waitFor();
  assert.equal(await a.page.getByRole("status").filter({ hasText: "Carregando lançamentos…" }).count(), 1);
  assert.equal(await a.page.locator(".card-ia .ci-esq").count(), 3, "o esqueleto das linhas");
  await a.linha.click(); // recolhe
  await ate(() => a.t.falhas.length > 0);
  assert.deepEqual(a.t.falhas, ["net::ERR_ABORTED"], "o pedido em voo foi cancelado, não deixado pendurado");
  assert.equal(await a.page.locator(".card-ia .ci-aberta").count(), 0);
  await a.page.context().close();
  const b = await preparar({ relogio: "instalado" });
  b.t.modo = "pendurado";
  await b.linha.click();
  await b.page.locator(".card-ia .ci-lanc[aria-busy=true]").waitFor();
  await b.page.clock.runFor(14_000);
  assert.equal(await b.page.locator(".card-ia .ci-erro").count(), 0, "aos 14 s ainda carrega");
  await b.page.clock.runFor(2_000);
  await b.page.locator(".card-ia .ci-erro").waitFor();
  assert.equal(await b.page.locator(".card-ia .ci-erro p").innerText(), FALHA);
  assert.equal(await b.page.getByRole("button", { name: "Tentar de novo" }).count(), 1);
  assert.equal(b.t.gets.length, 1, "o corte do prazo não repete");
  await b.page.context().close();
});

test("L6 duplo clique: abre e fecha (nada desenhado, nenhum erro); reabrir dá UMA lista, sem linha repetida", async () => {
  const { page, linha, linhas, erros } = await preparar();
  await linha.dblclick();
  await page.waitForTimeout(400);
  assert.equal(await linha.getAttribute("aria-expanded"), "false");
  assert.equal(await page.locator(".card-ia .ci-aberta").count(), 0);
  assert.equal(await linhas().count(), 0);
  await linha.click();
  await linhas().first().waitFor();
  const ids = await linhas().evaluateAll((ls) => ls.map((l) => l.dataset.id));
  assert.equal(ids.length, 5);
  assert.equal(new Set(ids).size, 5, "nenhuma linha repetida");
  assert.equal(await page.locator(".card-ia .ci-lanc").count(), 1, "uma lista só");
  assert.deepEqual(erros, []);
  await page.context().close();
});

test("L7 \"Carregar mais\": duplo clique manda UMA página (com o cursor), sem linha repetida; a troca de conta durante a página 2 não desenha nada dela", async () => {
  const a = await preparar();
  await a.linha.click();
  await a.linhas().first().waitFor();
  const mais = a.page.getByRole("button", { name: "Carregar mais lançamentos" });
  await mais.dblclick();
  await a.linhas().nth(5).waitFor();
  await a.page.waitForTimeout(300);
  const comCursor = a.t.gets.filter((u) => u.searchParams.has("cursor"));
  assert.equal(comCursor.length, 1, "1 GET da página 2");
  assert.equal(comCursor[0].searchParams.get("cursor"), F.lancamentos.estados.proximo);
  const ids = await a.linhas().evaluateAll((ls) => ls.map((l) => l.dataset.id));
  assert.deepEqual([ids.length, new Set(ids).size], [6, 6]);
  assert.equal(await mais.count(), 0, "sem `proximo`, sem botão");
  await a.page.context().close();
  const b = await preparar();
  await b.linha.click();
  await b.linhas().first().waitFor();
  b.t.modo = "preso"; b.t.emPagina2 = true;
  await b.page.getByRole("button", { name: "Carregar mais lançamentos" }).click();
  await ate(() => b.t.soltar);
  b.conta.uid = 2;
  b.t.soltar("DE-B");
  await b.cardSumiu();
  await b.page.waitForTimeout(300);
  assert.ok(!(await b.vistos()).includes("DE-B"), "nada da página 2 foi desenhado");
  await b.page.context().close();
});

test("L7b a página 2 repete uma linha da 1: ela entra uma vez só (dedupe por id)", async () => {
  const a = await preparar();
  a.t.repete = true;
  await a.linha.click();
  await a.linhas().first().waitFor();
  await a.page.getByRole("button", { name: "Carregar mais lançamentos" }).click();
  await a.linhas().nth(5).waitFor();
  await a.page.waitForTimeout(300);
  const ids = await a.linhas().evaluateAll((ls) => ls.map((l) => l.dataset.id));
  assert.deepEqual([ids.length, new Set(ids).size], [6, 6]);
  await a.page.context().close();
});

test("L8 troca de conta percebida pelo foco com a lista aberta: o card some e NENHUM GET novo sai com a sessão de B", async () => {
  const { page, t, linha, linhas, conta, cardSumiu } = await preparar();
  await linha.click();
  await linhas().first().waitFor();
  const antes = t.gets.length;
  await trocarConta(page, conta, 2);
  await cardSumiu();
  await page.waitForTimeout(500);
  assert.equal(t.gets.length, antes, "nenhuma releitura da lista de A em nome de B");
  await page.context().close();
});

test("L9 id de conta que não é inteiro positivo: a linha não vira botão e nenhum GET sai com id inventado; a conta válida abre", async () => {
  const c = (id, nome) => ({ ...F.contas.todos_os_estados.contas[0], id, instituicao: nome, motivos: [] });
  const contas = { ...F.contas.so_carteira, total: "160.00", fora_do_total: 0, motivos: ["carteira_nao_confirmada"], contas: [c(0, "Zero"), c(-3, "Negativa"), c(1.5, "Quebrada"), c(7, "Valida")].map((x) => ({ ...x, saldo: "10.00" })) };
  contas.carteira = { saldo: "120.00", motivos: ["carteira_nao_confirmada"] };
  const { page, t, linhas } = await preparar({}, contas);
  assert.deepEqual(await page.locator(".card-ia .ci-linha[aria-expanded]").evaluateAll((bs) => bs.map((b) => b.textContent.includes("Valida"))), [true], "só a conta válida é botão");
  assert.equal(await page.locator(".card-ia .ci-linha:not([aria-expanded])").count(), 4, "carteira + as 3 contas com id ruim: linhas sem botão");
  await page.locator(".card-ia .ci-linha[aria-expanded]").click();
  await linhas().first().waitFor();
  assert.deepEqual(t.gets.map((u) => u.searchParams.get("conta")), ["7"]);
  await page.context().close();
});

test("L10 o aviso do SSE com a lista aberta (2 páginas) NÃO relê o card (0 GET de lançamentos, a foto fica); o controle positivo é o /me, que o mesmo aviso relê", async () => {
  const a = await preparar({ sse: true });
  await a.linha.click();
  await a.linhas().first().waitFor();
  await a.page.getByRole("button", { name: "Carregar mais lançamentos" }).click();
  await a.linhas().nth(5).waitFor();
  await a.page.waitForTimeout(300);
  const gets = a.t.gets.length, me = a.t.me;
  a.t.marca = "DE-NOVO";
  await ate(() => a.t.soltaSse);
  a.t.soltaSse();
  await ate(() => a.t.me > me); // positivo: o aviso invalidou o resto
  await a.page.waitForTimeout(1000);
  assert.equal(a.t.gets.length, gets, "nenhum GET novo de lançamentos");
  assert.ok(!(await a.page.locator(".card-ia .ci-lanc").innerText()).includes("DE-NOVO"));
  assert.equal(await a.linhas().count(), 6);
  await a.page.context().close();
});

test("L11 o prazo de 15 s cobre a página INTEIRA: com o perfil pendurado (antes do GET) é o erro com \"Tentar de novo\", e nenhum GET sai", async () => {
  const a = await preparar({ relogio: "instalado" });
  a.t.perfil = "pendurado";
  await a.linha.click();
  await a.page.locator(".card-ia .ci-lanc[aria-busy=true]").waitFor();
  await a.page.clock.runFor(14_000);
  assert.equal(await a.page.locator(".card-ia .ci-erro").count(), 0, "aos 14 s ainda carrega");
  await a.page.clock.runFor(2_000);
  await a.page.locator(".card-ia .ci-erro").waitFor();
  assert.equal(await a.page.locator(".card-ia .ci-erro p").innerText(), FALHA);
  assert.equal(await a.page.getByRole("button", { name: "Tentar de novo" }).count(), 1);
  assert.equal(a.t.gets.length, 0);
  await a.page.context().close();
});

test("L12 foco: depois de \"Carregar mais\" que traz a ÚLTIMA página (o botão some) e depois de \"Tentar de novo\" com sucesso, o foco vai para o 1º item novo, não para o <body>", async () => {
  const dono = (page) => page.evaluate(() => ({ corpo: document.activeElement === document.body, id: document.activeElement?.closest?.(".ci-lanc .lanc-linha")?.dataset.id ?? null }));
  const a = await preparar();
  await a.linha.click();
  await a.linhas().first().waitFor();
  const mais = a.page.getByRole("button", { name: "Carregar mais lançamentos" });
  await mais.focus();
  await mais.press("Enter");
  await a.linhas().nth(5).waitFor();
  await mais.waitFor({ state: "detached" });
  assert.deepEqual(await dono(a.page), { corpo: false, id: F.lancamentos.segunda.itens[0].id });
  await a.page.context().close();
  const b = await preparar();
  b.t.modo = "500";
  await b.linha.click();
  const erro = b.page.locator(".card-ia .ci-erro");
  await erro.waitFor({ timeout: 12000 });
  b.t.modo = "ok";
  await erro.getByRole("button", { name: "Tentar de novo" }).click();
  await b.linhas().first().waitFor();
  await erro.waitFor({ state: "detached" });
  assert.deepEqual(await dono(b.page), { corpo: false, id: F.lancamentos.estados.itens[0].id });
  await b.page.context().close();
});

test("L12b foco: \"Tentar de novo\" pelo teclado que falha DE NOVO devolve o foco ao botão do erro; clique que não focou o botão (o mouse do Safari) não move o foco", async () => {
  const a = await preparar(), erro = a.page.locator(".card-ia .ci-erro");
  a.t.modo = "500";
  await a.linha.click();
  await erro.waitFor({ timeout: 12000 });
  const n = a.t.gets.length;
  await erro.getByRole("button", { name: "Tentar de novo" }).press("Enter");
  await ate(() => a.t.gets.length > n);
  await a.page.waitForFunction(() => document.querySelector(".card-ia .ci-lanc")?.getAttribute("aria-busy") === "false" && document.querySelector(".card-ia .ci-erro button"), null, { timeout: 12000 });
  assert.equal(await a.page.evaluate(() => document.activeElement?.textContent), "Tentar de novo");
  a.t.modo = "ok";
  await a.page.evaluate(() => { document.activeElement?.blur(); document.querySelector(".card-ia .ci-erro button").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
  await a.linhas().first().waitFor();
  assert.equal(await a.page.evaluate(() => document.activeElement === document.body), true, "o foco não pulou para o item");
  await a.page.context().close();
});

for (const [nome, itens] of [["item null", [null]], ["item sem campos", [{ id: "l1" }]], ["data que não é data", [{ ...F.lancamentos.estados.itens[0], data: "lixo" }]], ["motivo que não é texto", [{ ...F.lancamentos.estados.itens[0], motivos: [{ a: 1 }] }]], ["descrição que não é texto", [{ ...F.lancamentos.estados.itens[0], descricao: { a: 1 } }]], ["categoria que não é texto", [{ ...F.lancamentos.estados.itens[0], categoria: { a: 1 } }]], ["valor que não é decimal", [{ ...F.lancamentos.estados.itens[0], valor: "invalid" }]], ["dia que não existe", [{ ...F.lancamentos.estados.itens[0], data: "2026-02-30" }]], ["data com horário", [{ ...F.lancamentos.estados.itens[0], data: "2026-10-10T12:00:00" }]]]) {
  test(`L13 lançamento malformado (${nome}): o card fica de pé com o erro e "Tentar de novo" (que recupera), em vez de virar o marcador`, async () => {
    const a = await preparar();
    a.t.itens = itens;
    await a.linha.click();
    const erro = a.page.locator(".card-ia .ci-erro");
    await erro.waitFor();
    assert.equal(await erro.locator("p").innerText(), FALHA);
    assert.equal(await a.page.locator(".card-ia").count(), 1);
    assert.equal(await a.page.getByText("Este card ainda não aparece aqui.").count(), 0);
    assert.equal(a.t.gets.length, 1, "contrato quebrado não repete");
    a.t.itens = null;
    await erro.getByRole("button", { name: "Tentar de novo" }).click();
    await a.linhas().first().waitFor();
    assert.equal(await a.linhas().count(), 5, "positivo: a lista boa desenha inteira");
    await a.page.context().close();
  });
}

for (const width of [1440, 390]) {
  test(`L14 selos: os da conta ficam recuados sob o nome (44px); os do lançamento aberto NÃO herdam o recuo (${width}px, sem rolagem lateral)`, async () => {
    const a = await preparar({ width });
    await a.linha.click();
    await a.linhas().first().waitFor();
    const m = await a.page.evaluate(() => {
      const ml = (sel) => [...document.querySelectorAll(sel)].map((s) => getComputedStyle(s).marginLeft);
      return { conta: ml(".card-ia .ci-conta > .selos"), lanc: ml(".card-ia .ci-lanc .selos"), larg: [document.documentElement.scrollWidth, innerWidth] };
    });
    assert.ok(m.conta.length > 0 && m.conta.every((x) => x === "44px"), JSON.stringify(m));
    assert.ok(m.lanc.length > 0 && m.lanc.every((x) => x === "0px"), JSON.stringify(m));
    assert.ok(m.larg[0] <= m.larg[1], "sem rolagem lateral: " + m.larg);
    await a.page.context().close();
  });
}
