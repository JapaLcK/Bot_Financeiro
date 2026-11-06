/**
 * /painel#/conta/familia, a visão do TITULAR sobre um membro (webapp/src/dashboard/familia/VerMembro.tsx, dados.ts): o primeiro lugar em que
 * o navegador mostra dado financeiro de outra conta. Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ERRO, E422, F, MARIA, MES, abrirFamilia, abrirVer, titular } from "./_familia.mjs";

const browser = navegador();
void browser;

const com = async (abrir, fn) => {
  const t = await abrir();
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};
const ver = (fn, opts) => com(() => abrirVer(opts), fn);
const mesAnterior = (page) => page.getByRole("button", { name: "Mês anterior" });
const pausa = (page) => page.waitForTimeout(400);
const mesAntes = (mes) => { const [a, m] = mes.split("-").map(Number); const d = new Date(Date.UTC(a, m - 2, 1)); return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`; };

// mutação: mostrar o "Ver" para todo membro (Titular.tsx)
test("sem permissão não há 'Ver' nem GET de dados; com o resumo liberado há exatamente um 'Ver'", async () => {
  const outros = [{ ...MARIA, permissoes: [] }, { ref: "r2", nome: "João", status: "ativo", permissoes: ["lancamentos"] }, { ref: "r3", nome: "Ana", status: "suspenso", permissoes: ["agregados"] }];
  await com(() => abrirFamilia({ visao: titular({ membros: outros }) }), async ({ page, bt, s, secao }) => {
    await pausa(page);
    assert.equal(await bt("Ver").count(), 0);
    assert.equal((await secao.innerText()).match(/Não compartilha/g).length, 3);
    assert.equal(s.reqs.filter((q) => q.caminho.startsWith("/membros/")).length, 0);
  });
  await com(() => abrirFamilia({ visao: titular({ membros: [MARIA, { ...MARIA, ref: "r9", nome: "Zeca", permissoes: [] }] }) }), async ({ bt, s }) => {
    assert.equal(await bt("Ver").count(), 1);
    assert.equal(s.reqs.filter((q) => q.caminho.startsWith("/membros/")).length, 0);
  });
});

// mutação: uma leitura a mais no abrir (efeito duplo), ou uid/user_id no path
test("abrir o membro gera exatamente 1 GET, sem id de conta na URL, e o dinheiro sai como texto", async () => {
  await ver(async ({ page, secao, dados, bt }) => {
    await pausa(page);
    const g = dados();
    assert.equal(g.length, 1);
    const u = new URL(g[0].url);
    assert.match(u.pathname + u.search, new RegExp(`^/api/v2/familia/membros/${encodeURIComponent(MARIA.ref)}/agregados\\?mes=\\d{4}-\\d{2}$`));
    assert.doesNotMatch(g[0].url, /user_id|uid/);
    const txt = await secao.innerText();
    for (const t of ["R$ 1.234,56", "R$ 789,10", "Desde 20/10.", "Mercado", "Transporte", "Orçamentos", "Maria"]) assert.ok(txt.includes(t), t);
    assert.equal(await page.locator(".fam-valores dd.gain").innerText(), "R$ 1.234,56"); // entrou em verde; saiu em tinta neutra
    assert.equal(await page.locator(".fam-valores dd:not(.gain)").innerText(), "R$ 789,10");
    await bt("Voltar").click();
    await bt("Ver").click();
    await page.getByText("R$ 1.234,56").first().waitFor();
    assert.equal(dados().length, 2); // reabrir é um GET novo
  });
});

// mutação: tirar o 'consentimento' de motivoDe (dados.ts), ou o reler() do efeito `parou` (VerMembro.tsx)
test("revogado no meio: o 403 tira todo valor da tela, mostra o texto, relê a família e o 'Ver' some", async () => {
  await ver(async ({ page, s, secao, bt }) => {
    s.agregados = ERRO(403, "consent_required");
    const familias = () => s.reqs.filter((q) => q.metodo === "GET" && q.caminho === "").length;
    const antes = familias();
    s.visao = titular({ membros: [{ ...MARIA, permissoes: [] }] }); // o que a releitura vai trazer
    await mesAnterior(page).click();
    await secao.getByText("Maria parou de compartilhar.").waitFor();
    const txt = await secao.innerText();
    for (const t of ["1.234,56", "789,10", "Mercado", "Orçamentos"]) assert.ok(!txt.includes(t), `sobrou ${t}`);
    await pausa(page);
    assert.equal(familias(), antes + 1);
    await bt("Voltar").click();
    assert.equal(await bt("Ver").count(), 0);
    await secao.getByText("Não compartilha").waitFor();
  });
});

// mutação: gcTime padrão em `leitura` (dados.ts)
test("sem cache: fechar e reabrir com o 2º GET preso não mostra valor nenhum enquanto espera", async () => {
  await ver(async ({ page, s, bt, secao }) => {
    await bt("Voltar").click();
    let soltar; s.holdAgregados = new Promise((r) => (soltar = r));
    await bt("Ver").click();
    await secao.getByText("Carregando…").waitFor();
    await pausa(page);
    const txt = await secao.innerText();
    assert.ok(!txt.includes("1.234,56") && !txt.includes("Mercado"), txt);
    soltar();
    await page.getByText("R$ 1.234,56").first().waitFor();
  });
});

// mutação: tirar o resetQueries de ["conta","familia"] do main.tsx (a troca zera a família e o painel sai com a Tela; só o `key` de ContaFamilia, sozinho, não deixa vermelho)
test("trocar de conta com o painel aberto: nenhum texto nem valor da conta anterior, e nenhum GET novo de dados", async () => {
  let uid = 7;
  const perfil = (ctx) => ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "a@exemplo.com", user_id: uid, whatsapp_linked: false } }));
  await com(() => abrirVer({ antes: perfil }), async ({ page, s, dados }) => {
    let soltar; s.hold = new Promise((r) => (soltar = r)); // o /familia da conta 8 demora
    s.visao = null;
    uid = 8;
    await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
    await page.waitForFunction(() => !document.body.innerText.includes("1.234,56"));
    await pausa(page);
    const corpo = await page.locator(".conta-familia").innerText();
    for (const t of ["1.234,56", "789,10", "Maria", "Mercado", "Voltar"]) assert.ok(!corpo.includes(t), `sobrou ${t}`);
    soltar();
    await page.getByRole("button", { name: "Criar família" }).waitFor();
    assert.equal(dados().length, 1);
  });
});

// mutação: refetchOnWindowFocus: true em `leitura` (dados.ts): cada releitura grava uma linha no log do membro
test("o foco da aba não relê o resumo do membro", async () => {
  await ver(async ({ page, dados }) => {
    await page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus")); });
    await pausa(page);
    assert.equal(dados().length, 1);
  });
});

// mutação: tirar `limite` do disabled do "Mês anterior"; ler `orcamentos: null` como lista e desenhar a seção (dados.ts + o `length > 0` de VerMembro.tsx, juntos)
test("mês: 422 'mes' mostra o texto e trava o 'Mês anterior'; o 'Próximo' trava no mês corrente; orçamento null some", async () => {
  await ver(async ({ page, s, secao, dados }) => {
    s.agregados = (r) => {
      const mes = new URL(s.reqs.at(-1).url).searchParams.get("mes");
      if (mes >= MES) return;
      return mes < mesAntes(MES) ? E422("mes")(r) : r.fulfill({ json: { ...F.agregados_passado, mes } });
    };
    const proximo = page.getByRole("button", { name: "Próximo mês" });
    assert.ok(await proximo.isDisabled(), "no mês corrente");
    assert.ok((await secao.innerText()).includes("Orçamentos"));
    await mesAnterior(page).click();
    await secao.getByText("R$ 2.000,00").waitFor();
    assert.ok(!(await secao.innerText()).includes("Orçamentos"), "orçamentos null: a seção some");
    await mesAnterior(page).click();
    await secao.getByText("Sem dados antes de Maria entrar.").waitFor();
    assert.ok(await mesAnterior(page).isDisabled());
    assert.ok(await proximo.isEnabled());
    await proximo.click();
    await secao.getByText("R$ 2.000,00").waitFor();
    assert.ok(await mesAnterior(page).isEnabled(), "o mês depois do limite volta a permitir voltar");
    assert.equal(dados().length, 4);
  });
});
