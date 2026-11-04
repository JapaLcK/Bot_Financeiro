/**
 * /painel#/conta/familia, a visão do MEMBRO: o que o titular vê (Permissoes.tsx) e "Quem viu" (Historico.tsx).
 * Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ERRO, F, abrirFamilia } from "./_familia.mjs";

const browser = navegador();
void browser;

const membro = (permissoes = []) => ({ papel: "membro", permissoes });
const com = async (opts, fn) => {
  const t = await abrirFamilia(opts);
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};
const sw = (page, nome) => page.getByRole("switch", { name: nome });

// mutação: POST sem corpo/content-type (CSRF do servidor), "Lançamentos" habilitado sem o resumo
test("liberar: POST com o escopo, CSRF e json; 'Lançamentos' só depois do resumo; o estado vem do servidor, não do clique", async () => {
  await com({ visao: membro() }, async ({ page, s, secao, escritas }) => {
    assert.ok((await secao.innerText()).includes("Saldo, contas, Pix e conversas nunca aparecem."));
    assert.ok(await sw(page, "Lançamentos").isDisabled());
    let soltar; const preso = new Promise((r) => (soltar = r));
    s.liberar = async () => { await preso; };
    await sw(page, "Resumo do mês").click();
    await page.waitForTimeout(300);
    assert.equal(await sw(page, "Resumo do mês").isChecked(), false, "nada otimista: a resposta ainda não chegou");
    soltar();
    await page.waitForFunction(() => document.querySelector('[role=switch]').checked);
    assert.ok(await sw(page, "Lançamentos").isEnabled());
    await sw(page, "Lançamentos").click();
    await page.waitForFunction(() => document.querySelectorAll('[role=switch]:checked').length === 2);
    const e = escritas();
    assert.deepEqual(e.map((x) => [x.metodo, x.caminho, JSON.parse(x.corpo).escopo, x.csrf, x.tipo]), [
      ["POST", "/permissoes", "agregados", "tok-123", "application/json"],
      ["POST", "/permissoes", "lancamentos", "tok-123", "application/json"],
    ]);
  });
});

// mutação: revogar só o próprio escopo no cliente em vez de usar a resposta do servidor (Permissoes.tsx)
test("revogar o resumo: DELETE /permissoes/agregados e a resposta [] desliga os dois", async () => {
  await com({ visao: membro(["agregados", "lancamentos"]) }, async ({ page, s, secao, escritas }) => {
    let soltar; s.hold = new Promise((r) => (soltar = r)); // a releitura da família fica presa: o que se vê é só a RESPOSTA do DELETE
    await sw(page, "Resumo do mês").click();
    await page.waitForFunction(() => document.querySelectorAll('[role=switch]:checked').length === 0);
    soltar();
    assert.ok(await sw(page, "Lançamentos").isDisabled());
    const e = escritas();
    assert.equal(e.length, 1);
    assert.deepEqual([e[0].metodo, e[0].caminho, e[0].csrf, e[0].tipo], ["DELETE", "/permissoes/agregados", "tok-123", "application/json"]);
    assert.equal(await secao.getByText("O que o titular vê").count(), 1);
  });
});

// mutação: tirar o 'aggregates_required' do falhaFamilia (api.ts)
test("409 aggregates_required vira texto; 404 relê a família; o switch volta ao que o servidor diz", async () => {
  await com({ visao: membro(["agregados"]) }, async ({ page, s, secao }) => {
    s.liberar = ERRO(409, "aggregates_required");
    await sw(page, "Lançamentos").click();
    await secao.getByText("Libere o resumo primeiro.").waitFor();
    assert.equal(await sw(page, "Lançamentos").isChecked(), false);
    s.liberar = ERRO(404, "not_found");
    const antes = s.reqs.filter((q) => q.metodo === "GET" && q.caminho === "").length;
    await sw(page, "Lançamentos").click();
    await secao.getByText("Isso não existe mais.").waitFor();
    await page.waitForTimeout(300);
    assert.equal(s.reqs.filter((q) => q.metodo === "GET" && q.caminho === "").length, antes + 1);
  });
});

// mutação: o "Ver mais" sem `antes` (dados.ts), o item sem o fuso de São Paulo (Historico.tsx)
test("histórico: vazio, itens com data no fuso, 'Ver mais' com antes=<proximo>", async () => {
  await com({ visao: membro() }, async ({ secao }) => { await secao.getByText("Ninguém viu nada ainda.").waitFor(); });
  await com({ visao: membro(), antes: (_c, s) => { s.historico = (r) => { const antes = new URL(s.reqs.at(-1).url).searchParams.get("antes"); return r.fulfill({ json: antes ? { itens: [{ quando: "2026-10-30T02:10:00+00:00", quem: "Dona Josefina", escopo: "agregados", mes: "2026-09" }], proximo: null } : F.historico }); }; } }, async ({ page, s, secao, bt }) => {
    await secao.getByText("Carlos viu o resumo de novembro de 2026").waitFor();
    const txt = await secao.innerText();
    assert.ok(txt.includes("Carlos viu os lançamentos de outubro de 2026"), txt);
    assert.ok(txt.includes("02/11 14:30") && txt.includes("01/11 09:05"), txt); // 17:30Z e 12:05Z no fuso de São Paulo
    await bt("Ver mais").click();
    await secao.getByText("Dona Josefina viu o resumo de setembro de 2026").waitFor();
    assert.ok((await secao.innerText()).includes("29/10 23:10"));
    const h = s.reqs.filter((q) => q.caminho === "/historico");
    assert.equal(h.length, 2);
    assert.equal(new URL(h[1].url).searchParams.get("antes"), "41");
    assert.equal(await bt("Ver mais").count(), 0);
  });
});

// mutação: tirar o ramo `q.isError` de Historico.tsx (a lista velha ou a tela vazia no lugar do erro)
test("histórico: erro mostra 'Tentar de novo' (sem a lista velha); 401 pede para recarregar; item fora do contrato é erro", async () => {
  await com({ visao: membro(), antes: (_c, s) => { s.historico = ERRO(429, "rate_limited"); } }, async ({ s, secao, bt }) => {
    await secao.getByText("Não foi possível carregar.").waitFor();
    s.historico = (r) => r.fulfill({ json: F.historico_vazio });
    await bt("Tentar de novo").click();
    await secao.getByText("Ninguém viu nada ainda.").waitFor();
  });
  await com({ visao: membro(), antes: (_c, s) => { s.historico = ERRO(401, "unauthorized"); } }, async ({ secao, bt }) => {
    await secao.getByText("Sua sessão terminou.").waitFor();
    await bt("Recarregar").waitFor();
  });
  await com({ visao: membro(), antes: (_c, s) => { s.historico = (r) => r.fulfill({ json: { proximo: null } }); } }, async ({ secao }) => {
    await secao.getByText("Não foi possível carregar.").waitFor(); // sem `itens`
  });
  await com({ visao: membro(), antes: (_c, s) => { s.historico = (r) => r.fulfill({ json: { itens: [{ quando: "2026-11-02T17:30:00+00:00", quem: "X", escopo: "contas", mes: "2026-11" }], proximo: null } }); } }, async ({ secao }) => {
    await secao.getByText("Não foi possível carregar.").waitFor(); // escopo que o cliente não conhece
  });
});
