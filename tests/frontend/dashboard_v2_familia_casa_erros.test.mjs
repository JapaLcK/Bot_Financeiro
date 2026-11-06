/**
 * /painel#/casa (PR 6c), os erros: conflito de edição (409 edit_conflict), category_taken, insufficient, 422 por campo, 402, 404, resposta perdida,
 * carga com erro e contrato quebrado. Em todo erro de escrita o rascunho fica. Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ERRO, F, ate, comCasa } from "./_familia.mjs";

const com = comCasa;

const browser = navegador();
void browser;

const E422 = (campo) => (r) => r.fulfill({ status: 422, json: { error: { code: "validation_error", message: "x", details: [{ loc: ["body", campo], msg: "x", type: "value_error" }] } } });
const ERRO_VALOR = "Use um valor maior que zero, até 9 dígitos e 2 casas decimais, sem separador de milhares.";
const linha = (t, nome) => t.page.locator(".casa-lista li", { hasText: nome });
const preencher = async (t, campos) => { for (const [rotulo, valor] of Object.entries(campos)) await t.dlg().getByLabel(rotulo).fill(valor); };
const salvar = (t) => t.dlg().getByRole("button", { name: /^(Salvar|Confirmar)$/ }).click();

// mutação: CasaMetaForm.tsx passar `versao: item vivo` (a Meta da lista, que a releitura troca) em vez da foto `meta` do diálogo: o PUT leva a versao 2, o servidor aceita e SOBRESCREVE às cegas
// (positivo: o teste de "editar meta" em dashboard_v2_familia_casa.test.mjs, PUT com a versao lida, e a 2ª abertura abaixo, que lê o estado novo)
test("conflito: outro membro muda a meta com o diálogo aberto; a releitura NÃO troca a versao do rascunho; o 409 mostra o texto e 'Recarregar' descarta o rascunho e relê", async () => {
  await com({}, async (t) => {
    await linha(t, "Viagem").getByRole("button", { name: "Editar" }).click();
    await preencher(t, { Nome: "Meu rascunho" });
    Object.assign(t.s.casa.metas[0], { nome: "Do outro membro", versao: 2 }); // outro membro salvou
    await t.page.evaluate(() => window.dispatchEvent(new Event("visibilitychange"))); // o foco da aba relê a casa (não grava log)
    await linha(t, "Do outro membro").waitFor();
    assert.equal(await t.dlg().getByLabel("Nome").inputValue(), "Meu rascunho", "a releitura não mexe no rascunho");
    await salvar(t);
    await t.dlg().getByText("Alguém mudou isso. Recarregue.").waitFor();
    const [q] = t.escritas();
    assert.equal(JSON.parse(q.corpo).versao, 1, "o PUT leva a versao LIDA na abertura");
    assert.equal(await t.dlg().getByLabel("Nome").inputValue(), "Meu rascunho", "até o Recarregar o rascunho fica");
    const antes = t.leituras().length;
    await t.dlg().getByRole("button", { name: "Recarregar" }).click();
    await t.dlg().waitFor({ state: "detached" });
    await ate(() => t.leituras().length > antes); // "Recarregar" relê a casa
    assert.equal(t.escritas().length, 1, "Recarregar não reenvia nada");
    await linha(t, "Do outro membro").getByRole("button", { name: "Editar" }).click();
    assert.equal(await t.dlg().getByLabel("Nome").inputValue(), "Do outro membro", "o rascunho foi descartado: abre o estado do servidor");
    await salvar(t);
    await t.page.getByText("Do outro membro").first().waitFor();
    assert.equal(JSON.parse(t.escritas()[1].corpo).versao, 2, "agora com a versao nova");
  });
});

// mutação: tirar `campo: "categoria"` do category_taken (casa.ts) ou o `aria-invalid` do CampoTexto: o campo não é marcado; o rascunho fica
test("category_taken e insufficient: texto próprio, rascunho mantido, campo marcado", async () => {
  await com({ antes: (_c, s) => { s.casaCriar = ERRO(409, "category_taken"); } }, async (t) => {
    await t.bt("Novo orçamento").click();
    await preencher(t, { Categoria: "mercado", "Teto do mês (R$)": "10" });
    await salvar(t);
    await t.dlg().getByText("Essa categoria já tem orçamento.").waitFor();
    assert.equal(await t.dlg().getByLabel("Categoria").getAttribute("aria-invalid"), "true");
    assert.equal(await t.dlg().getByLabel("Categoria").inputValue(), "mercado");
    assert.equal(await t.dlg().getByLabel("Teto do mês (R$)").inputValue(), "10");
    await t.dlg().getByRole("button", { name: "Cancelar" }).click();
    await linha(t, "Viagem").getByRole("button", { name: "Tirar" }).click();
    await preencher(t, { "Valor (R$)": "99999" });
    await salvar(t);
    await t.dlg().getByText("Não tem tanto guardado.").waitFor();
    assert.equal(await t.dlg().getByLabel("Valor (R$)").inputValue(), "99999");
    await t.dlg().getByRole("button", { name: "Cancelar" }).click();
    await linha(t, "Mercado").getByRole("button", { name: "Desfazer gasto" }).click();
    await preencher(t, { "Valor (R$)": "99999" });
    await salvar(t);
    await t.dlg().getByText("Não tem tanto gasto lançado.").waitFor();
  });
});

// mutação: o mapa do 422 olhar só `valor` (casa.ts, `falhaCasa`): alvo/limite, data_alvo e prazo caem no texto genérico; ou perder o `data_alvo`
const CASOS_META = [
  ["valor", ERRO_VALOR, "Quanto juntar (R$)"], ["alvo", ERRO_VALOR, "Quanto juntar (R$)"],
  ["data_alvo", "Escolha uma data de hoje em diante.", "Prazo (opcional)"], ["prazo", "Escolha uma data de hoje em diante.", "Prazo (opcional)"],
  ["nome", "Dê um nome.", "Nome"], ["versao", "Não foi possível salvar. Seu rascunho foi mantido.", null],
];
test("422 por campo (valor/alvo, data_alvo, prazo, nome, versao): texto certo, aria-invalid no campo, rascunho mantido; vale no POST e no PUT", async () => {
  for (const [campo, texto, rotulo] of CASOS_META) {
    await com({ antes: (_c, s) => { s.casaCriar = E422(campo); } }, async (t) => {
      await t.bt("Nova meta").click();
      await preencher(t, { Nome: "Carro", "Quanto juntar (R$)": "10,5", "Prazo (opcional)": "2020-01-01" });
      await salvar(t);
      await t.dlg().getByText(texto, { exact: true }).waitFor();
      if (rotulo) assert.equal(await t.dlg().getByLabel(rotulo).getAttribute("aria-invalid"), "true", campo);
      assert.deepEqual([await t.dlg().getByLabel("Nome").inputValue(), await t.dlg().getByLabel("Quanto juntar (R$)").inputValue(), await t.dlg().getByLabel("Prazo (opcional)").inputValue()], ["Carro", "10,5", "2020-01-01"], campo);
      assert.equal(t.escritas().length, 1);
    });
  }
  await com({ antes: (_c, s) => { s.casaEditar = E422("limite"); s.casaMover = E422("valor"); } }, async (t) => {
    await linha(t, "Mercado").getByRole("button", { name: "Editar" }).click();
    await salvar(t);
    await t.dlg().getByText(ERRO_VALOR).waitFor();
    await t.dlg().getByRole("button", { name: "Cancelar" }).click();
    await linha(t, "Mercado").getByRole("button", { name: "Lançar gasto" }).click();
    await preencher(t, { "Valor (R$)": "5" });
    await salvar(t);
    await t.dlg().getByText(ERRO_VALOR).waitFor();
  });
  await com({ antes: (_c, s) => { s.casaCriar = E422("categoria"); } }, async (t) => {
    await t.bt("Novo orçamento").click();
    await preencher(t, { Categoria: "x", "Teto do mês (R$)": "5" });
    await salvar(t);
    await t.dlg().getByText("Escreva uma categoria.").waitFor();
  });
});

// mutação: tirar o ramo `e.status === 402` de falhaFamilia (api.ts) ou o `falha.plano` do CasaDialogo: sem o link da cobrança
test("402 numa escrita: 'Seu plano não inclui família.' + 'Ver cobrança'; o rascunho fica; os botões de editar seguem à mostra", async () => {
  await com({ antes: (_c, s) => { s.casaCriar = ERRO(402, "family_coverage_required"); } }, async (t) => {
    assert.equal(await t.bt("Nova meta").count(), 1);
    await t.bt("Nova meta").click();
    await preencher(t, { Nome: "Carro", "Quanto juntar (R$)": "10" });
    await salvar(t);
    await t.dlg().getByText("Seu plano não inclui família.").waitFor();
    assert.equal(await t.dlg().getByRole("link", { name: "Ver cobrança" }).getAttribute("href"), "#/conta/cobranca");
    assert.equal(await t.dlg().getByLabel("Nome").inputValue(), "Carro");
  });
});

// mutação: `deveReler` sem o 404 (casa.ts): a tela não relê e segue mostrando o item que não existe mais
test("404 numa escrita: 'Isso não existe mais.' e a casa é relida; no Apagar o texto fica na página", async () => {
  await com({}, async (t) => {
    await linha(t, "Viagem").getByRole("button", { name: "Editar" }).click();
    t.s.casa.metas.shift(); // outro membro apagou
    await salvar(t);
    await t.secao.getByText("Isso não existe mais.").waitFor(); // o diálogo fecha e o aviso fica na página (o mesmo lugar do Apagar)
    assert.equal(await t.dlg().count(), 0);
    await linha(t, "Viagem").waitFor({ state: "detached" }); // a releitura tirou
    assert.equal(t.leituras().length, 2);
  });
  await com({ antes: (_c, s) => { s.casaApagar = ERRO(404, "not_found"); } }, async (t) => {
    await linha(t, "Reserva").getByRole("button", { name: "Editar" }).click();
    await t.dlg().getByRole("button", { name: "Apagar" }).click();
    await t.dlg().getByRole("button", { name: "Apagar" }).click();
    await t.secao.getByText("Isso não existe mais.").waitFor();
    assert.equal(await t.dlg().count(), 0);
  });
});

// mutação: tirar o ramo `status >= 500` de falhaCasa (casa.ts): cai no texto genérico, sem o aviso de conferir
test("resposta perdida (5xx): 'Confira antes de tentar de novo.', o rascunho fica e a casa é relida", async () => {
  await com({ antes: (_c, s) => { s.casaCriar = (r) => r.fulfill({ status: 500, body: "x" }); } }, async (t) => {
    await t.bt("Nova meta").click();
    await preencher(t, { Nome: "Carro", "Quanto juntar (R$)": "10" });
    await salvar(t);
    await t.dlg().getByText("A resposta se perdeu. Confira antes de tentar de novo.").waitFor();
    assert.equal(await t.dlg().getByLabel("Nome").inputValue(), "Carro");
    await ate(() => t.leituras().length >= 2);
  });
});

// mutação: `q.isError ? undefined : q.data` trocado por `q.data` (CasaPagina.tsx): o erro de uma releitura mostra a casa velha
test("erro de carga nunca mostra o dado velho; 'Tentar de novo' recupera; 401 pede para recarregar", async () => {
  await com({}, async (t) => {
    t.s.casaLer = ERRO(403, "forbidden");
    await t.page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
    await t.secao.getByText("Não foi possível carregar a casa.").waitFor();
    assert.ok(!(await t.texto()).includes("R$ 1.234,56"), "dado velho com o erro na tela");
    t.s.casaLer = null;
    await t.secao.getByRole("button", { name: "Tentar de novo" }).click();
    await t.page.getByText("R$ 1.234,56").first().waitFor();
    t.s.casaLer = ERRO(401, "unauthorized");
    await t.page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
    await t.secao.getByText("Sua sessão terminou.").waitFor();
    await t.secao.getByRole("button", { name: "Recarregar" }).waitFor();
  });
});

// mutação: `dinheiro(m.guardado)` removido do leitor (casa.ts): o número cru entra na tela
test("contrato: dinheiro em número, lista ausente ou autor ausente viram o estado de erro (sem pageerror); campo extra é ignorado", async () => {
  const ruim = (f) => { const c = structuredClone(F.casa); f(c); return c; };
  for (const c of [ruim((c) => { c.metas[0].guardado = 1234.56; }), ruim((c) => { delete c.orcamentos; }), ruim((c) => { delete c.metas[0].autor; }), ruim((c) => { c.metas[0].versao = "1"; })]) {
    await com({ casa: c, esperar: false }, async (t) => {
      await t.secao.getByText("Não foi possível carregar a casa.").waitFor({ timeout: 15000 });
    });
  }
  await com({ casa: { ...structuredClone(F.casa), extra: 1 } }, async (t) => {
    assert.ok((await t.texto()).includes("Viagem"));
  });
});
