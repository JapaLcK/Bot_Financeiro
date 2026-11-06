/**
 * /painel#/casa (PR 6c): a casa do grupo, metas e orçamentos (webapp/src/dashboard/familia/Casa*.tsx), sobre a api/v2/familia_casa.py de mentira de _familia.mjs.
 * Aqui: leitura, criar/editar/apagar, movimentos e o dinheiro digitado. Os erros e o conflito de edição: dashboard_v2_familia_casa_erros.test.mjs.
 * Em cada teste, o comentário de cima diz qual mutação o deixa vermelho (feita no webapp/src, com o artefato refeito, e desfeita em seguida).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { F, comCasa } from "./_familia.mjs";

const browser = navegador();
void browser;

const com = comCasa;
const linha = (t, nome) => t.page.locator(".casa-lista li", { hasText: nome });
const preencher = async (t, campos) => { for (const [rotulo, valor] of Object.entries(campos)) await t.dlg().getByLabel(rotulo).fill(valor); };

// mutação: `quem` (Casa.tsx) trocado por `a ?? "Membro"`: o "Ex-membro" some; ou `semZero(m.falta)` por `false`: o "Meta batida" some
test("a casa: valores como texto do servidor, selos de autor, prazo, estados vazios; 1 GET só", async () => {
  await com({}, async (t) => {
    const x = await t.texto();
    for (const trecho of ["Metas", "Orçamentos", "Outubro 2026", "Viagem", "R$ 1.234,56 de R$ 5.000,00 · 24%", "Faltam R$ 3.765,44", "até 31/12/2026", "Reserva", "Meta batida", "Mercado", "R$ 350,00 de R$ 800,00", "Restam R$ 450,00", "Luz", "Passou R$ 50,00"]) {
      assert.ok(x.includes(trecho), `falta "${trecho}" em: ${x}`);
    }
    assert.equal(await linha(t, "Viagem").locator(".selo").innerText(), "Ana");
    assert.equal(await linha(t, "Reserva").locator(".selo").innerText(), "Ex-membro"); // autor null
    assert.equal(await linha(t, "Mercado").locator(".selo").innerText(), "Membro"); // autor ""
    assert.equal(t.leituras().length, 1);
    assert.deepEqual(t.escritas(), []);
  });
  await com({ casa: { ...F.casa, metas: [], orcamentos: [] } }, async (t) => {
    const x = await t.texto();
    assert.ok(x.includes("Nenhuma meta da casa.") && x.includes("Nenhum orçamento da casa."), x);
    await t.bt("Nova meta").waitFor();
    await t.bt("Novo orçamento").waitFor();
  });
});

// mutação: o 404 not_found virar erro em `casaQuery` (casa.ts, o `catch`): a tela mostra "Não foi possível carregar" em vez do caminho para criar a família
test("sem família: o texto e o caminho para criar; nada é escrito nem há botão de meta", async () => {
  await com({ casa: null }, async (t) => {
    const x = await t.texto();
    assert.ok(x.includes("A casa é da família"), x);
    const criar = t.secao.getByRole("link", { name: "Criar família" });
    assert.equal(await criar.getAttribute("href"), "#/conta/familia");
    assert.equal(await t.bt("Nova meta").count(), 0);
    await criar.click();
    await t.page.getByRole("heading", { name: "Família" }).waitFor();
    assert.deepEqual(t.escritas(), []);
  });
});

// mutação: tirar `qc.setQueryData(chave, casa)` de CasaDialogo.tsx: a meta nova não aparece (nem sem 2º GET)
test("criar meta: corpo exato, JSON + CSRF, e a Casa da resposta vai para a tela SEM 2º GET; '1234,56' envia '1234.56'", async () => {
  await com({}, async (t) => {
    await t.bt("Nova meta").click();
    await preencher(t, { Nome: "  Carro  ", "Quanto juntar (R$)": "1234,56", "Prazo (opcional)": "2027-03-10" });
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await linha(t, "Carro").waitFor();
    const [q] = t.escritas();
    assert.equal(q.metodo + q.caminho, "POST/casa/metas");
    assert.deepEqual(JSON.parse(q.corpo), { nome: "Carro", alvo: "1234.56", prazo: "2027-03-10" });
    assert.match(q.tipo, /application\/json/);
    assert.ok(q.csrf, "sem CSRF");
    assert.equal(t.leituras().length, 1, "a Casa da resposta bastou");
    assert.equal(await t.dlg().count(), 0, "o diálogo fecha no sucesso");
    assert.ok((await t.texto()).includes("R$ 1.234,56") && (await t.texto()).includes("até 10/03/2027"));
  });
});

// mutação: `valorInvalido(norm)` trocado por `false` em CasaMetaForm.tsx (a checagem de cliente cai): vai um POST com "1.234.56"/"1.234,56"
// positivo: o teste de cima ("1234,56" envia "1234.56")
test("'1.234,56' (separador de milhar) é recusado no cliente: zero POST, o texto do dano e aria-invalid; o rascunho fica", async () => {
  await com({}, async (t) => {
    await t.bt("Nova meta").click();
    await preencher(t, { Nome: "Carro", "Quanto juntar (R$)": "1.234,56" });
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await t.dlg().getByText("Use um valor maior que zero, até 9 dígitos e 2 casas decimais, sem separador de milhares.").waitFor();
    assert.deepEqual(t.escritas(), []);
    assert.equal(await t.dlg().getByLabel("Quanto juntar (R$)").getAttribute("aria-invalid"), "true");
    assert.equal(await t.dlg().getByLabel("Quanto juntar (R$)").inputValue(), "1.234,56");
    await t.dlg().getByLabel("Nome").fill("   "); // sem nome: o texto do campo, também sem POST
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await t.dlg().getByText("Dê um nome.").waitFor();
    assert.deepEqual(t.escritas(), []);
  });
});

// mutação: o PUT sem `versao` (CasaMetaForm.tsx) ou com `versao` da lista viva em vez da foto do diálogo (ver o teste do conflito em _erros)
test("editar meta: PUT com a `versao` lida, valor pré-preenchido com vírgula, e a Casa devolvida entra na tela", async () => {
  await com({}, async (t) => {
    await linha(t, "Viagem").getByRole("button", { name: "Editar" }).click();
    assert.equal(await t.dlg().getByLabel("Quanto juntar (R$)").inputValue(), "5000,00");
    assert.equal(await t.dlg().getByLabel("Prazo (opcional)").inputValue(), "2026-12-31");
    await preencher(t, { Nome: "Viagem longa", "Quanto juntar (R$)": "6000,50", "Prazo (opcional)": "" });
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await linha(t, "Viagem longa").waitFor();
    const [q] = t.escritas();
    assert.equal(q.metodo + q.caminho, "PUT/casa/metas/m1");
    assert.deepEqual(JSON.parse(q.corpo), { nome: "Viagem longa", alvo: "6000.50", prazo: null, versao: 1 });
    assert.ok((await t.texto()).includes("de R$ 6.000,50"));
    assert.equal(t.leituras().length, 1);
  });
});

// mutação: `categoria: categoria.trim()` sem trim, ou o corpo com `valor` no lugar de `limite` (CasaOrcamentoForm.tsx)
test("criar e editar orçamento: corpo exato; o PUT leva a `versao`", async () => {
  await com({}, async (t) => {
    await t.bt("Novo orçamento").click();
    await preencher(t, { Categoria: " Lazer ", "Teto do mês (R$)": "300" });
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await linha(t, "Lazer").waitFor();
    await linha(t, "Mercado").getByRole("button", { name: "Editar" }).click();
    assert.equal(await t.dlg().getByLabel("Teto do mês (R$)").inputValue(), "800,00");
    await preencher(t, { "Teto do mês (R$)": "900,10" });
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await t.page.getByText("de R$ 900,10").waitFor();
    const [a, b] = t.escritas();
    assert.equal(a.metodo + a.caminho, "POST/casa/orcamentos");
    assert.deepEqual(JSON.parse(a.corpo), { categoria: "Lazer", limite: "300" });
    assert.equal(b.metodo + b.caminho, "PUT/casa/orcamentos/o1");
    assert.deepEqual(JSON.parse(b.corpo), { categoria: "Mercado", limite: "900.10", versao: 2 });
  });
});

// mutação: `sinal: "subtrair"` trocado por "somar" no botão "Tirar" (Casa.tsx), ou o caminho sem `/movimentos` (casa.ts)
test("movimentos: Guardar/Tirar numa meta e Lançar/Desfazer gasto num orçamento: corpo exato, tela com a Casa devolvida", async () => {
  await com({}, async (t) => {
    await linha(t, "Viagem").getByRole("button", { name: "Guardar" }).click();
    await preencher(t, { "Valor (R$)": "100,50" });
    await t.dlg().getByRole("button", { name: "Confirmar" }).click();
    await t.page.getByText("R$ 1.335,06 de R$ 5.000,00").waitFor();
    await linha(t, "Viagem").getByRole("button", { name: "Tirar" }).click();
    await preencher(t, { "Valor (R$)": "35,06" });
    await t.dlg().getByRole("button", { name: "Confirmar" }).click();
    await t.page.getByText("R$ 1.300,00 de R$ 5.000,00").waitFor();
    await linha(t, "Mercado").getByRole("button", { name: "Lançar gasto" }).click();
    await preencher(t, { "Valor (R$)": "50" });
    await t.dlg().getByRole("button", { name: "Confirmar" }).click();
    await t.page.getByText("R$ 400,00 de R$ 800,00").waitFor();
    await linha(t, "Mercado").getByRole("button", { name: "Desfazer gasto" }).click();
    await preencher(t, { "Valor (R$)": "10" });
    await t.dlg().getByRole("button", { name: "Confirmar" }).click();
    await t.page.getByText("R$ 390,00 de R$ 800,00").waitFor();
    const e = t.escritas().map((q) => [q.metodo + q.caminho, JSON.parse(q.corpo)]);
    assert.deepEqual(e, [
      ["POST/casa/metas/m1/movimentos", { tipo: "somar", valor: "100.50" }],
      ["POST/casa/metas/m1/movimentos", { tipo: "subtrair", valor: "35.06" }],
      ["POST/casa/orcamentos/o1/movimentos", { tipo: "somar", valor: "50" }],
      ["POST/casa/orcamentos/o1/movimentos", { tipo: "subtrair", valor: "10" }],
    ]);
    assert.equal(t.leituras().length, 1);
  });
});

// mutação: tirar o `void reler()` do `agir` do Apagar (Casa.tsx): o item continua na tela
test("apagar: confirma antes, DELETE no ref, e a casa é relida (204 não traz corpo)", async () => {
  await com({}, async (t) => {
    await linha(t, "Reserva").getByRole("button", { name: "Editar" }).click();
    await t.dlg().getByRole("button", { name: "Apagar" }).click();
    await t.dlg().getByRole("heading", { name: "Apagar Reserva?" }).waitFor();
    assert.deepEqual(t.escritas(), [], "só confirmar apaga");
    await t.dlg().getByRole("button", { name: "Apagar" }).click();
    await linha(t, "Reserva").waitFor({ state: "detached" });
    const [q] = t.escritas();
    assert.equal(q.metodo + q.caminho, "DELETE/casa/metas/m2");
    assert.equal(t.leituras().length, 2, "relida depois do 204");
    assert.equal(await t.dlg().count(), 0);
  });
});

// mutação: tirar a trava `voando` de CasaDialogo.tsx (`if (voando.current) return;`): dois envios no mesmo tick mandam 2 POST (o botão só desabilita depois do render)
test("uma escrita por vez: dois envios no mesmo tick, com a 1ª em voo, mandam UM POST", async () => {
  await com({ antes: (_c, s) => { s.casaCriar = () => new Promise((ok) => setTimeout(ok, 700)); } }, async (t) => {
    await t.bt("Novo orçamento").click();
    await preencher(t, { Categoria: "Lazer", "Teto do mês (R$)": "300" });
    await t.page.evaluate(() => { const f = document.querySelector("dialog[open] form"); f.requestSubmit(); f.requestSubmit(); });
    await linha(t, "Lazer").waitFor();
    assert.equal(t.escritas().length, 1);
    assert.equal((await t.texto()).split("Lazer").length - 1, 1, "um item só");
  });
});
