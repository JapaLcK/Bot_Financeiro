// A ação de LEITURA dos cards da IA no chat do /painel: tocar numa conta do card `contas` abre os lançamentos dela DENTRO do card
// (webapp: parts/cardIa/LancamentosDoCard.tsx, conta/api.ts `antesDaConta`/`daConta`). Isolamento entre usuários: a conta da sessão é conferida ANTES de
// cada GET (nenhum sai em nome de outra conta) e DEPOIS (a resposta de uma troca durante a busca não é desenhada). L1 a L7 do plano, mais L8 (foco depois
// da troca) e L9 (id de conta que não é inteiro positivo). L10 a L14: o aviso do SSE não relê a lista, o prazo cobre as leituras do perfil, o foco, o item malformado e os selos. A rota `/api/v2/lancamentos` é DO TESTE: guarda a URL, segura a resposta quando ele quer e
// marca as descrições (`DE-A-…`, `DE-B-…`); um MutationObserver grava TODO texto que entra no DOM, para pegar o `DE-B` mesmo que viva um quadro só.
// Cada grupo foi provado vermelho com uma mutação do código que ele cobre (a tabela está no relato do PR, não aqui).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS, abrir, blocoDe, encerrar, enviar, evento, fim, iniciar, trocarConta, ultima } from "./_chat_ia.mjs";

before(iniciar);
after(encerrar);
const F = RESPOSTAS;
const FALHA = "Não deu para carregar os lançamentos.";
const SESSAO = "Sua sessão terminou. Recarregue a página.";
const pronto = (page) => page.waitForFunction(() => document.querySelector(".askbar-send")?.getAttribute("aria-label") === "Enviar", null, { timeout: 5000 });
const ate = async (cond, ms = 6000) => { for (let i = 0; i < ms / 25 && !cond(); i++) await new Promise((r) => setTimeout(r, 25)); assert.ok(cond(), "a condição não chegou a tempo"); };
const marcada = (u, marca, repete = false) => {
  const base = u.searchParams.has("cursor") ? F.lancamentos.segunda : F.lancamentos.estados;
  const itens = repete && u.searchParams.has("cursor") ? [F.lancamentos.estados.itens[0], ...base.itens] : base.itens; // a lista é lida ao vivo: a página 2 repete a última linha da 1
  return { ...base, mes: u.searchParams.get("mes"), itens: itens.map((i) => ({ ...i, descricao: `${marca}-${i.id}` })) };
};
const ERRO500 = { status: 500, json: { error: { code: "internal_error", message: "x" } } };

/** O /painel com o card `contas` na conversa e a rota de lançamentos do teste. `t.modo`: ok | 500 | 403 | 401 | preso (espera `t.soltar(marca)`) | pendurado (nunca responde).
 * `t.perfil = "pendurado"`: o /auth/dashboard-profile nunca responde. `t.itens`: os `itens` malformados que a rota devolve. `opts.sse`: o stream de avisos fica preso até `t.soltaSse()` (e `t.me` conta as leituras do /me). */
async function preparar(opts = {}, contas = F.contas.todos_os_estados) {
  const { sse, ...resto } = opts;
  const c = await abrir(resto);
  const t = { gets: [], ordem: [], modo: "ok", repete: false, soltar: null, marca: "DE-A", falhas: [], emPagina2: false, perfil: "ok", itens: null, soltaSse: null, me: 0 };
  if (sse) {
    await c.ctx.route("**/api/v2/eventos", async (r) => { await new Promise((ok) => { t.soltaSse = ok; }); return r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 600000\ndata: {"recurso":"open_finance"}\n\n' }); });
    await c.page.reload();
    await c.page.locator("#askbar-input").waitFor();
  }
  c.page.on("request", (r) => { if (new URL(r.url()).pathname === "/api/v2/me") t.me++; });
  await c.ctx.route("**/auth/dashboard-profile", (r) => { t.ordem.push("perfil"); if (t.perfil === "pendurado") return new Promise(() => {}); return r.fulfill({ json: { user_id: c.conta.uid, display_name: null, email: null, whatsapp_linked: true } }); });
  await c.ctx.route("**/api/v2/lancamentos*", async (r) => {
    const u = new URL(r.request().url());
    t.gets.push(u); t.ordem.push("GET");
    const pagina2 = u.searchParams.has("cursor");
    if (t.modo === "pendurado") return new Promise(() => {});
    if (t.itens) return r.fulfill({ json: { ...F.lancamentos.estados, itens: t.itens } });
    if (t.modo === "preso" && (!t.emPagina2 || pagina2)) { const marca = await new Promise((ok) => { t.soltar = ok; }); return r.fulfill({ json: marcada(u, marca) }); }
    if (t.modo === "500") return r.fulfill(ERRO500);
    if (t.modo === "403") return r.fulfill({ status: 403, json: { error: { code: "forbidden", message: "x" } } });
    if (t.modo === "401") return r.fulfill({ status: 401, json: { error: { code: "unauthorized", message: "x" } } });
    return r.fulfill({ json: marcada(u, t.marca, t.repete) });
  });
  c.page.on("requestfailed", (r) => { if (r.url().includes("/api/v2/lancamentos")) t.falhas.push(r.failure()?.errorText); });
  await c.page.evaluate(() => {
    window.__vistos = [];
    new MutationObserver((ms) => { for (const m of ms) { for (const n of m.addedNodes) window.__vistos.push(n.textContent); if (m.type === "characterData") window.__vistos.push(m.target.textContent); } })
      .observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  });
  await enviar(c.page, "minhas contas");
  await c.flux.espera(1);
  await c.flux.push(0, evento(blocoDe("contas", { contas }), fim("Aqui estão.")));
  await ultima(c.page).locator(".card-ia").waitFor();
  await pronto(c.page);
  const linha = c.page.locator(".card-ia .ci-linha[aria-expanded]").first(); // a 1ª conta do total (id 1, Nubank)
  const vistos = () => c.page.evaluate(() => window.__vistos.join("\n"));
  const linhas = () => c.page.locator(".card-ia .ci-lanc .lanc-linha");
  const cardSumiu = () => c.page.waitForFunction(() => document.querySelectorAll(".card-ia").length === 0 && document.querySelectorAll(".chat > .msg-piggy").length === 0, null, { timeout: 5000 });
  return { ...c, t, linha, vistos, linhas, cardSumiu };
}

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

test("L8 troca de conta percebida pelo foco com a lista aberta: o card some e NENHUM GET novo sai com a sessão de B (a chave é [conta, assunto, uid])", async () => {
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

for (const [nome, itens] of [["item null", [null]], ["item sem campos", [{ id: "l1" }]], ["data que não é data", [{ ...F.lancamentos.estados.itens[0], data: "lixo" }]], ["motivo que não é texto", [{ ...F.lancamentos.estados.itens[0], motivos: [{ a: 1 }] }]], ["descrição que não é texto", [{ ...F.lancamentos.estados.itens[0], descricao: { a: 1 } }]], ["categoria que não é texto", [{ ...F.lancamentos.estados.itens[0], categoria: { a: 1 } }]]]) {
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
