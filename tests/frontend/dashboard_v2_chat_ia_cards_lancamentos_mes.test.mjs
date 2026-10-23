// A ação de LEITURA dos cards de gasto da IA no chat do /painel (PR 4 da Etapa 7): tocar numa categoria ("Para onde vai"), numa das categorias que "Outras" esconde ou
// num dia ("Dia a dia") abre os lançamentos DELA/DELE dentro do card, pelo mesmo `LancamentosDoCard` da conta (webapp: parts/cardIa/gastos.tsx). O servidor só protege
// `conta` contra a troca de conta: com a sessão de B, `categoria` e `dia` trazem os lançamentos de B. Por isso a conferência do cliente (`antesDaConta` ANTES de cada
// GET, `daConta` DEPOIS) vale aqui tanto quanto no card `contas`, e este arquivo roda os mesmos casos (L1 a L8, L13) para as TRÊS consultas, em tabela.
// Controles negativos (desligar o conserto e ver vermelho num caso que estava verde) estão na tabela do relato do PR: sem `antesDaConta` -> L2 de todas; sem `daConta` -> L3.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS, encerrar, fixture, iniciar, trocarConta } from "./_chat_ia.mjs";
import { FALHA, SESSAO, ate, prepararCard } from "./_chat_ia_lanc.mjs";

before(iniciar);
after(encerrar);
const F = RESPOSTAS;
const D = fixture("ai_chat_recurso_detalhes.json");
const SEM = { conta: null, categoria: null, tipo: null, dia: null, guardado: null, cursor: null };
// `esperado`: a query inteira (o que não está aqui NÃO pode ir). `linha`: a linha que abre a busca; `antes`: o que abre a linha que não busca nada ("Outras").
const CONSULTAS = [
  { nome: "categoria", card: "categorias", recursos: { detalhes: D }, linha: ".card-ia .ci-cat[aria-expanded]", esperado: { ...SEM, mes: "2026-10", categoria: "alimentacao", tipo: "saida" } },
  { nome: "Outras", card: "categorias", recursos: { detalhes: D }, antes: (p) => p.locator(".card-ia .ci-cat").nth(4).click(), linha: ".card-ia .ci-aberta .ci-cat[aria-expanded]", esperado: { ...SEM, mes: "2026-10", categoria: "lazer", tipo: "saida" } },
  { nome: "dia", card: "calendario", recursos: { detalhes: D }, linha: ".card-ia .ci-dia[aria-expanded]", esperado: { ...SEM, mes: "2026-10", dia: "2026-10-25" } },
];
const prep = (c, opts) => prepararCard(opts, c);
const query = (u) => Object.fromEntries(Object.keys({ ...SEM, mes: 0 }).map((k) => [k, u.searchParams.get(k)]));

for (const c of CONSULTAS) {
  const t = (nome, f) => test(`${c.nome} ${nome}`, f);
  t("L1 positivo: abrir lê UMA página com a query inteira (mês do bloco, filtro, limite 10, sem guardado), conferindo a conta antes e depois; as linhas ficam DENTRO do card", async () => {
    const { page, t, linha, linhas, erros } = await prep(c);
    t.ordem.length = 0;
    await linha.click();
    await linhas().first().waitFor();
    assert.equal(t.gets.length, 1);
    assert.deepEqual(query(t.gets[0]), c.esperado);
    assert.equal(t.gets[0].searchParams.get("limite"), "10");
    const iGet = t.ordem.indexOf("GET");
    assert.ok(iGet > 0 && t.ordem.slice(0, iGet).every((x) => x === "perfil"), "conta lida antes do GET");
    assert.deepEqual(t.ordem.slice(iGet), ["GET", "perfil"], "conta relida logo depois do GET");
    assert.equal(await linhas().count(), 5);
    assert.ok((await page.locator(".card-ia .ci-lanc").innerText()).includes("DE-A-l901"));
    assert.equal(await linha.getAttribute("aria-expanded"), "true");
    assert.equal(await page.locator(".card-ia a, .card-ia dialog").count(), 0, "sem link e sem folha");
    assert.deepEqual(erros, []);
    await page.context().close();
  });

  t("L2 troca de conta ANTES do clique (a aba ainda não sabe): nenhum GET de lançamentos sai e o card de A some", async () => {
    const { page, t, linha, conta, cardSumiu } = await prep(c);
    conta.uid = 2; // a sessão virou B noutra aba; esta não recebeu o foco
    await linha.click();
    await cardSumiu();
    assert.deepEqual(t.gets, [], "nenhuma requisição de lançamentos saiu em nome de B");
    await page.context().close();
  });

  t("L3 troca DURANTE a busca: a resposta (marcada DE-B) nunca é desenhada e o card de A some", async () => {
    const { page, t, linha, conta, vistos, cardSumiu } = await prep(c);
    t.modo = "preso";
    await linha.click();
    await ate(() => t.soltar);
    conta.uid = 2;
    t.soltar("DE-B");
    await cardSumiu();
    await page.waitForTimeout(300);
    assert.ok(!(await vistos()).includes("DE-B"), "o DE-B nunca entrou no DOM, nem por um quadro");
    await page.context().close();
  });

  t("L4 erro: 500 em todas as tentativas vira o erro com \"Tentar de novo\" (que recupera); 403 é a sessão, sem \"Tentar de novo\"", async () => {
    const a = await prep(c);
    a.t.modo = "500";
    await a.linha.click();
    const erro = a.page.locator(".card-ia .ci-erro");
    await erro.waitFor({ timeout: 12000 });
    assert.equal(await erro.locator("p").innerText(), FALHA);
    assert.equal(a.t.gets.length, 3, "5xx: três tentativas no total, nenhuma a mais");
    a.t.modo = "ok";
    await erro.getByRole("button", { name: "Tentar de novo" }).click();
    await a.linhas().first().waitFor();
    assert.equal(await a.page.locator(".card-ia .ci-erro").count(), 0);
    assert.equal(await a.linhas().count(), 5);
    await a.page.context().close();
    const b = await prep(c);
    b.t.modo = "403";
    await b.linha.click();
    await b.page.locator(".card-ia .ci-erro").waitFor();
    assert.equal(await b.page.locator(".card-ia .ci-erro p").innerText(), SESSAO);
    assert.equal(await b.page.getByRole("button", { name: "Tentar de novo" }).count(), 0);
    assert.equal(b.t.gets.length, 1, "4xx não repete");
    await b.page.context().close();
  });

  t("L5 lentidão: \"Carregando lançamentos…\" com aria-busy; recolher ABORTA o pedido; passados 15 s é o erro com \"Tentar de novo\"", async () => {
    const a = await prep(c);
    a.t.modo = "pendurado";
    await a.linha.click();
    await a.page.locator(".card-ia .ci-lanc[aria-busy=true]").waitFor();
    assert.equal(await a.page.getByRole("status").filter({ hasText: "Carregando lançamentos…" }).count(), 1);
    await a.linha.click(); // recolhe
    await ate(() => a.t.falhas.length > 0);
    assert.deepEqual(a.t.falhas, ["net::ERR_ABORTED"], "o pedido em voo foi cancelado, não deixado pendurado");
    assert.equal(await a.page.locator(".card-ia .ci-lanc").count(), 0);
    await a.page.context().close();
    const b = await prep(c, { relogio: "instalado" });
    b.t.modo = "pendurado";
    await b.linha.click();
    await b.page.locator(".card-ia .ci-lanc[aria-busy=true]").waitFor();
    await b.page.clock.runFor(14_000);
    assert.equal(await b.page.locator(".card-ia .ci-erro").count(), 0, "aos 14 s ainda carrega");
    await b.page.clock.runFor(2_000);
    await b.page.locator(".card-ia .ci-erro").waitFor();
    assert.equal(await b.page.locator(".card-ia .ci-erro p").innerText(), FALHA);
    assert.equal(b.t.gets.length, 1, "o corte do prazo não repete");
    await b.page.context().close();
  });

  t("L6 duplo clique: abre e fecha (nada desenhado, nenhum erro); reabrir dá UMA lista, sem linha repetida", async () => {
    const { page, linha, linhas, erros } = await prep(c);
    await linha.dblclick();
    await page.waitForTimeout(400);
    assert.equal(await linha.getAttribute("aria-expanded"), "false");
    assert.equal(await linhas().count(), 0);
    await linha.click();
    await linhas().first().waitFor();
    const ids = await linhas().evaluateAll((ls) => ls.map((l) => l.dataset.id));
    assert.deepEqual([ids.length, new Set(ids).size], [5, 5]);
    assert.equal(await page.locator(".card-ia .ci-lanc").count(), 1, "uma lista só");
    assert.deepEqual(erros, []);
    await page.context().close();
  });

  t("L7 \"Carregar mais\": duplo clique manda UMA página (com o cursor); a troca de conta durante a página 2 não desenha nada dela", async () => {
    const a = await prep(c);
    await a.linha.click();
    await a.linhas().first().waitFor();
    await a.page.getByRole("button", { name: "Carregar mais lançamentos" }).dblclick();
    await a.linhas().nth(5).waitFor();
    await a.page.waitForTimeout(300);
    const comCursor = a.t.gets.filter((u) => u.searchParams.has("cursor"));
    assert.equal(comCursor.length, 1, "1 GET da página 2");
    assert.deepEqual({ ...query(comCursor[0]), cursor: null }, c.esperado, "a página 2 leva o MESMO filtro");
    await a.page.context().close();
    const b = await prep(c);
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

  t("L8 troca de conta percebida pelo foco com a lista aberta: o card some e NENHUM GET novo sai com a sessão de B", async () => {
    const { page, t, linha, linhas, conta, cardSumiu } = await prep(c);
    await linha.click();
    await linhas().first().waitFor();
    const antes = t.gets.length;
    await trocarConta(page, conta, 2);
    await cardSumiu();
    await page.waitForTimeout(500);
    assert.equal(t.gets.length, antes, "nenhuma releitura da lista de A em nome de B");
    await page.context().close();
  });

  t("L13 lançamento malformado: o card fica de pé com o erro e \"Tentar de novo\" (que recupera), em vez de virar o marcador", async () => {
    const a = await prep(c);
    a.t.itens = [{ ...F.lancamentos.estados.itens[0], valor: "invalid" }];
    await a.linha.click();
    const erro = a.page.locator(".card-ia .ci-erro");
    await erro.waitFor();
    assert.equal(await erro.locator("p").innerText(), FALHA);
    assert.equal(await a.page.getByText("Este card ainda não aparece aqui.").count(), 0);
    a.t.itens = null;
    await erro.getByRole("button", { name: "Tentar de novo" }).click();
    await a.linhas().first().waitFor();
    assert.equal(await a.linhas().count(), 5, "positivo: a lista boa desenha inteira");
    await a.page.context().close();
  });
}

test("a página 2 repete uma linha da 1 (a lista é lida ao vivo): ela entra uma vez só, também numa categoria e num dia", async () => {
  for (const c of [CONSULTAS[0], CONSULTAS[2]]) {
    const a = await prep(c);
    a.t.repete = true;
    await a.linha.click();
    await a.linhas().first().waitFor();
    await a.page.getByRole("button", { name: "Carregar mais lançamentos" }).click();
    await a.linhas().nth(5).waitFor();
    const ids = await a.linhas().evaluateAll((ls) => ls.map((l) => l.dataset.id));
    assert.deepEqual([ids.length, new Set(ids).size], [6, 6]);
    await a.page.context().close();
  }
});

test("o mês da busca é o do bloco (setembro no bloco = mes=2026-09), não o de hoje", async () => {
  const set = { ...D, mes: "2026-09", ate: "2026-09-30", dias: D.dias.map((x) => ({ ...x, dia: x.dia.replace("2026-10", "2026-09") })) };
  for (const [card, filtro] of [["categorias", ".card-ia .ci-cat[aria-expanded]"], ["calendario", ".card-ia .ci-dia[aria-expanded]"]]) {
    const a = await prep({ card, recursos: { detalhes: set }, linha: filtro });
    await a.linha.click();
    await a.linhas().first().waitFor();
    assert.deepEqual(a.t.gets.map((u) => u.searchParams.get("mes")), ["2026-09"]);
    await a.page.context().close();
  }
});

test("\"Sem categoria\" (chave nula ou vazia) abre com categoria=\"\", como o app (o servidor junta vazio, nulo e 'sem categoria'); dia que não existe não vira botão e nenhum GET sai", async () => {
  const a = await prep({ card: "categorias", recursos: { detalhes: { ...D, categorias: [{ categoria: null, valor: "100.00", quantidade: 1 }, { categoria: "mercado", valor: "20.00", quantidade: 1 }] } }, linha: ".card-ia .ci-cat[aria-expanded]" });
  assert.equal(await a.page.locator(".card-ia .ci-cat[aria-expanded]").count(), 2, "as duas categorias são botão");
  await a.linha.click();
  await a.linhas().first().waitFor();
  assert.deepEqual(a.t.gets.map((u) => [u.searchParams.has("categoria"), u.searchParams.get("categoria"), u.searchParams.get("tipo")]), [[true, "", "saida"]]);
  await a.page.context().close();
  const b = await prep({ card: "calendario", recursos: { detalhes: { ...D, dias: [{ dia: "2026-02-30", entrou: "0.00", saiu: "5.00" }, { dia: "lixo", entrou: "0.00", saiu: "5.00" }, { dia: "2026-10-03", entrou: "0.00", saiu: "5.00" }] } }, linha: ".card-ia .ci-dia[aria-expanded]" });
  assert.equal(await b.page.locator(".card-ia .ci-dia[aria-expanded]").count(), 1, "só o dia que existe é botão");
  await b.linha.click();
  await b.linhas().first().waitFor();
  assert.deepEqual(b.t.gets.map((u) => u.searchParams.get("dia")), ["2026-10-03"]);
  await b.page.context().close();
});

test("resumo e periodo não buscam nada: nem ao desenhar, nem ao abrir a categoria do período (os maiores lançamentos vêm no bloco)", async () => {
  const a = await prep({ card: "periodo", recursos: { periodo: fixture("ai_chat_bloco_periodo.json") }, linha: ".card-ia .ci-cat[aria-expanded]" });
  await a.linha.click();
  await a.page.locator(".card-ia .lanc-linha").first().waitFor();
  assert.deepEqual(a.t.gets, []);
  await a.page.context().close();
  const b = await prep({ card: "resumo", recursos: { resumo: F.resumo_do_mes.exato, detalhes: D }, linha: ".card-ia h2" });
  assert.deepEqual(b.t.gets, []);
  await b.page.context().close();
});
