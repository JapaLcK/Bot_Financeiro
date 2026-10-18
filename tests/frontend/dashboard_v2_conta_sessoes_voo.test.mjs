/**
 * Dispositivos conectados (conta/Sessoes.tsx), o DELETE em voo:
 *   · duplo clique, dois click() no mesmo tick e Enter mandam UM só DELETE, na sessão e nas outras (a trava síncrona `voando`);
 *   · Esc (1 a 6 vezes: o 3º do Chromium fecha o <dialog> à força), clique no fundo, Cancelar e o botão não fecham o diálogo em voo, no
 *     <dialog> nativo e no fallback (Safari < 15.4); terminado o voo, o Esc volta a fechar;
 *   · o prazo de 30 s (encurtado) aborta o DELETE que nunca responde: "Erro ao encerrar sessão", diálogo livre, lista relida;
 *   · o MESMO prazo vale para a releitura depois do DELETE que falhou: GET que nunca responde não prende o diálogo em "Encerrando…".
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { abrirSeguranca, portao } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const encerrar = (p, nome) => p.sess.locator("li").filter({ hasText: nome }).getByRole("button", { name: "Encerrar", exact: true });
const todas = (p) => p.sess.getByRole("button", { name: "Encerrar todas as outras", exact: true });
const confirmar = (p, nome = "Encerrar") => p.dialogo.getByRole("button", { name: nome, exact: true });
const abrirUma = async (p) => { await p.sess.locator("li").first().waitFor(); await encerrar(p, "Safari").click(); };
const abrirTodas = async (p) => { await p.sess.locator("li").first().waitFor(); await todas(p).click(); };

for (const [rotulo, abre, nome, esperado] of [["uma", abrirUma, "Encerrar", 1], ["as outras", abrirTodas, "Encerrar outras", 2]]) {
  test(`${rotulo}: duplo clique, dois click() no mesmo tick e Enter = UM DELETE`, async () => {
    const p = await abrirSeguranca();
    const [segura, solta] = portao();
    p.s.sessDel = async (r) => { await segura; return r.fulfill({ json: { ok: true, revoked: esperado } }); };
    await abre(p);
    await p.page.waitForTimeout(500); // passada a janela em que o clique no fundo ainda não vale
    await confirmar(p, nome).evaluate((b) => { b.click(); b.click(); }); // o 2º cai antes do React desabilitar o botão
    await confirmar(p, "Encerrando…").dblclick({ force: true }).catch(() => {});
    await p.page.keyboard.press("Enter");
    await p.page.waitForTimeout(300);
    const emVoo = [p.s.dels.length, await confirmar(p, "Encerrando…").isDisabled()];
    solta();
    await p.fechado();
    await p.page.waitForTimeout(200);
    await p.ctx.close();
    assert.deepEqual(emVoo, [1, true]);
    assert.equal(p.s.dels.length, 1);
  });
}

for (const semDialog of [false, true]) {
  test(`${semDialog ? "sem <dialog>" : "nativo"}: Esc (6×), clique no fundo, Cancelar e o botão NÃO fecham com o DELETE em voo; terminado o voo, o Esc fecha`, async () => {
    const p = await abrirSeguranca({ semDialog });
    const [segura, solta] = portao();
    p.s.sessDel = async (r) => { await segura; return r.fulfill({ status: 404, json: { detail: "Sessão não encontrada ou já encerrada." } }); };
    await abrirUma(p);
    await p.page.waitForTimeout(500);
    await confirmar(p).click();
    await confirmar(p, "Encerrando…").waitFor();
    for (let i = 0; i < 6; i++) { await p.page.keyboard.press("Escape"); await p.page.waitForTimeout(40); }
    await p.page.mouse.click(3, 3); // o fundo (::backdrop ou o scrim)
    const emVoo = [await p.dialogo.count(), await p.dialogo.getByRole("button", { name: "Cancelar" }).isDisabled(), await confirmar(p, "Encerrando…").isDisabled()];
    solta();
    await p.fechado(); // o desfecho (404) nunca fica engolido: o alerta está na seção
    await p.alertaSess.waitFor();
    const alerta = await p.alertaSess.innerText();
    await encerrar(p, "Edge").click(); // reabre: sem voo, o Esc fecha
    await p.page.waitForTimeout(500);
    await p.page.keyboard.press("Escape");
    await p.fechado();
    await p.ctx.close();
    assert.deepEqual(emVoo, [1, true, true]);
    assert.equal(alerta, "Sessão não encontrada ou já encerrada.");
    assert.equal(p.s.dels.length, 1);
  });
}

test("prazo de 30 s (encurtado): o DELETE que nunca responde é abortado, o diálogo se solta, 'Erro ao encerrar sessão' e a lista é relida", async () => {
  const p = await abrirSeguranca();
  const [segura] = portao();
  p.s.sessDel = async () => { await segura; };
  await p.sess.locator("li").first().waitFor();
  await p.encurtaPrazo(400);
  await encerrar(p, "Safari").click();
  await p.page.waitForTimeout(500);
  const antes = p.s.sessGets;
  await confirmar(p).click();
  await p.fechado();
  await p.alertaSess.waitFor();
  await p.page.waitForFunction(() => document.querySelectorAll(".conta-sessoes li").length === 3);
  const [alerta, ms] = [await p.alertaSess.innerText(), await p.page.evaluate(() => [...window.__ms])];
  const releu = p.s.sessGets > antes;
  const linhas = (await p.linhas()).map((l) => l.nome);
  await p.ctx.close();
  assert.equal(alerta, "Erro ao encerrar sessão");
  assert.deepEqual(ms, [30000, 30000]); // o do DELETE e o da releitura
  assert.equal(releu, true);
  assert.equal(linhas.length, 3);
});

test("prazo de 30 s (encurtado) na RELEITURA: DELETE 500 + GET que nunca responde não prende o diálogo; o foco vai ao título", async () => {
  const p = await abrirSeguranca();
  p.s.sessDel = (r) => r.fulfill({ status: 500, body: "x" });
  await p.sess.locator("li").first().waitFor();
  await p.encurtaPrazo(400);
  await encerrar(p, "Safari").click();
  await p.page.waitForTimeout(500);
  const [segura] = portao();
  p.s.sessHold = segura; // a releitura nunca responde
  await confirmar(p).click();
  await p.fechado();
  await p.alertaSess.waitFor();
  const [alerta, ms, foco] = [await p.alertaSess.innerText(), await p.page.evaluate(() => [...window.__ms]), await p.page.evaluate(() => document.activeElement?.tagName)];
  await p.ctx.close();
  assert.equal(alerta, "Erro ao encerrar sessão");
  assert.deepEqual(ms, [30000, 30000]); // o do DELETE e o da releitura
  assert.equal(foco, "H2");
});
