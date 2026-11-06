/**
 * /painel#/casa (PR 6c), a releitura que falha (ou 404) com o diálogo aberto e a escrita em voo. O diálogo e a confirmação saem do último dado bom (q.data): o erro de carga esconde só a lista;
 * a conclusão da escrita fecha o diálogo mesmo assim; `aberto` não sobrevive ao diálogo (404/sem família). Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ERRO, ate, comCasa } from "./_familia.mjs";

const browser = navegador();
void browser;

const linha = (t, nome) => t.page.locator(".casa-lista li", { hasText: nome });
const preencher = async (t, campos) => { for (const [rotulo, valor] of Object.entries(campos)) await t.dlg().getByLabel(rotulo).fill(valor); };
const foco = (t) => t.page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
const porta = () => { let abre; const p = new Promise((r) => (abre = r)); return [p, abre]; };
const FALHA = "Não foi possível carregar a casa.";
/** a releitura do foco falha com 500 (depois dos 3 retries o corpo da tela vira o erro de carga) */
const releituraFalha = async (t) => { t.s.casaLer = ERRO(500, "x"); await foco(t); await t.secao.getByText(FALHA).waitFor(); };

// mutação: `diag` voltar a depender de `casa` (CasaPagina.tsx, `!q.data` -> `!casa`): o erro de carga desmonta o diálogo e o rascunho se perde
test("releitura 5xx com o diálogo aberto: o rascunho fica; recuperada a carga, o mesmo diálogo segue", async () => {
  await comCasa({}, async (t) => {
    await t.bt("Nova meta").click();
    await preencher(t, { Nome: "Carro", "Quanto juntar (R$)": "10" });
    await releituraFalha(t);
    assert.equal(await t.dlg().count(), 1, "o diálogo saiu com o erro de carga");
    assert.equal(await t.dlg().getByLabel("Nome").inputValue(), "Carro");
    t.s.casaLer = null;
    await foco(t);
    await t.page.getByText("R$ 1.234,56").first().waitFor();
    assert.equal(await t.dlg().getByLabel("Nome").inputValue(), "Carro", "o rascunho sobreviveu à recuperação");
  });
});

// mutação: as duas defesas de CasaPagina.tsx juntas (`diag` por `casa` E sem o useEffect que zera `aberto`): o diálogo volta em branco depois de criada (a duplicata). Cada uma sozinha segura este caso; o 1 e o 3 pegam cada uma
test("criar meta em voo + releitura 5xx: a escrita conclui, o diálogo FECHA e não reaparece em branco; 1 POST", async () => {
  const [seguro, solta] = porta();
  await comCasa({ antes: (_c, s) => { s.casaCriar = () => seguro; } }, async (t) => {
    await t.bt("Nova meta").click();
    await preencher(t, { Nome: "Carro", "Quanto juntar (R$)": "10" });
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await ate(() => t.escritas().length === 1);
    await releituraFalha(t);
    solta();
    await t.dlg().waitFor({ state: "detached" });
    await linha(t, "Carro").waitFor();
    t.s.casaLer = null;
    await foco(t);
    await t.page.waitForTimeout(500);
    assert.equal(await t.dlg().count(), 0, "o diálogo reapareceu");
    assert.equal(t.escritas().length, 1);
  });
});

// mutação: tirar o `useEffect` que zera `aberto` sem q.data (CasaPagina.tsx): a família volta e o diálogo reabre com a foto velha
test("404 (família some) com o diálogo aberto: o diálogo sai; a família voltando NÃO o reabre", async () => {
  await comCasa({}, async (t) => {
    await linha(t, "Viagem").getByRole("button", { name: "Editar" }).click();
    await t.dlg().getByLabel("Nome").fill("Rascunho");
    const casa = t.s.casa;
    t.s.casa = null;
    await foco(t);
    await t.secao.getByRole("link", { name: "Criar família" }).waitFor();
    assert.equal(await t.dlg().count(), 0);
    t.s.casa = casa;
    await foco(t);
    await t.page.getByText("R$ 1.234,56").first().waitFor();
    await t.page.waitForTimeout(300);
    assert.equal(await t.dlg().count(), 0, "o diálogo reabriu com a foto velha");
  });
});

// mutação: `diag` por `casa`: a confirmação some com o erro de carga, ressuscita quando a casa volta e o 2º clique manda o 2º DELETE (404)
test("Apagar em voo + releitura 5xx: a confirmação fecha ao concluir, não ressuscita, 1 DELETE", async () => {
  const [seguro, solta] = porta();
  await comCasa({ antes: (_c, s) => { s.casaApagar = () => seguro; } }, async (t) => {
    await linha(t, "Reserva").getByRole("button", { name: "Editar" }).click();
    await t.dlg().getByRole("button", { name: "Apagar" }).click();
    await t.dlg().getByRole("button", { name: "Apagar" }).click();
    await ate(() => t.escritas().length === 1);
    await releituraFalha(t);
    solta();
    await t.dlg().waitFor({ state: "detached" });
    t.s.casaLer = null;
    await foco(t);
    await linha(t, "Viagem").waitFor();
    await t.page.waitForTimeout(500);
    assert.equal(await t.dlg().count(), 0, "a confirmação ressuscitou");
    assert.equal(await linha(t, "Reserva").count(), 0);
    assert.equal(t.escritas().length, 1);
  });
});

// mutação: tirar `await qc.cancelQueries` de CasaDialogo.tsx: a leitura que saiu ANTES da escrita (foto sem o item) chega depois e o item recém-escrito some da tela
test("leitura que saiu antes da escrita e chega depois dela não apaga o item novo", async () => {
  const [seguro, solta] = porta();
  let chegou = false;
  await comCasa({}, async (t) => {
    t.s.casaLer = async (rr) => { t.s.casaLer = null; chegou = true; const foto = structuredClone(t.s.casa); await seguro; rr.fulfill({ json: foto }); };
    await t.bt("Nova meta").click();
    await preencher(t, { Nome: "Carro", "Quanto juntar (R$)": "10" });
    await foco(t); // o GET sai e FICA com a foto sem o Carro
    await ate(() => chegou);
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await t.dlg().waitFor({ state: "detached" });
    await linha(t, "Carro").waitFor();
    solta();
    await t.page.waitForTimeout(600);
    assert.equal(await linha(t, "Carro").count(), 1, "a leitura velha ganhou");
  });
});

// mutação: voltar o `await reler()` no `agir` do Apagar (CasaPagina.tsx): a confirmação fica presa em "Apagando…" ~3,4 s com a releitura em 5xx (e para sempre com ela pendurada)
for (const [nome, ler] of [["500", () => ERRO(500, "x")], ["pendurada", () => () => new Promise(() => {})]]) {
  test(`Apagar OK + releitura ${nome}: a confirmação fecha em menos de 1 s (o DELETE já deu certo), 1 DELETE`, async () => {
    await comCasa({}, async (t) => {
      await linha(t, "Reserva").getByRole("button", { name: "Editar" }).click();
      await t.dlg().getByRole("button", { name: "Apagar" }).click();
      t.s.casaLer = ler();
      const t0 = Date.now();
      await t.dlg().getByRole("button", { name: "Apagar" }).click();
      await t.dlg().waitFor({ state: "detached", timeout: 1000 });
      assert.ok(Date.now() - t0 < 1000, `fechou em ${Date.now() - t0} ms`);
      assert.equal(t.escritas().length, 1);
      await t.page.waitForTimeout(300);
      assert.equal(await t.dlg().count(), 0, "a confirmação ressuscitou");
    });
  });
}

// mutação: tirar o ramo `status === 404` do catch de CasaDialogo.tsx: o diálogo fica aberto, com Salvar e Apagar ativos, sobre um item que não existe mais
test("editar um item que outro membro apagou (404): o diálogo fecha, o aviso fica na página, a casa é relida; 1 PUT", async () => {
  await comCasa({}, async (t) => {
    await linha(t, "Viagem").getByRole("button", { name: "Editar" }).click();
    await t.dlg().getByLabel("Nome").fill("Rascunho");
    t.s.casa.metas = t.s.casa.metas.filter((m) => m.nome !== "Viagem"); // outro membro apagou
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await t.dlg().waitFor({ state: "detached", timeout: 2000 });
    await t.secao.getByText("Isso não existe mais.").waitFor();
    await linha(t, "Viagem").waitFor({ state: "detached" });
    assert.equal(t.escritas().length, 1);
    await linha(t, "Reserva").waitFor(); // o resto da casa segue
  });
});
