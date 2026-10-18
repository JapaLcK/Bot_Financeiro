/**
 * Regerar os códigos de backup e a oferta do T2 (conta/MfaForm.tsx, conta/Cofre.tsx):
 *   · regerar com a resposta PERDIDA (o servidor comitou e apagou os antigos): "os códigos antigos podem ter sido invalidados", o modal
 *     segue utilizável; 4xx com `detalhe` continua verbatim; resposta fora do contrato (lista vazia, item que não é texto) cai no mesmo caminho;
 *   · depois do T2 (enable perdido, MFA ligado) o foco vai ao botão "Gerar novos códigos de backup", não ao <body>;
 *   · enable perdido + releitura do status também perdida: a nova tentativa volta 409 "já ativado" e vira T2 + oferta (só no passo do código).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { JSON_ERRO, NOVOS, SENHA, TOTP, abrirSeguranca, ateOQr } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const ON = { enabled: true, has_pending_secret: false, backup_codes_remaining: 4 };
const PERDIDO = "Os códigos antigos podem ter sido invalidados. Gere novos códigos.";
const tenta = async (p) => { await p.senha.fill(SENHA); await p.codigo.fill(TOTP); await p.enviar.click(); };

test("regerar com a resposta perdida (servidor comitou): texto de 'códigos antigos podem ter sido invalidados', status relido, modal utilizável e a nova tentativa chega ao Cofre; 4xx com detalhe segue verbatim", async () => {
  const p = await abrirSeguranca({ mfa: ON });
  await p.bt("Gerar novos códigos de backup").click();
  p.s.regerar = JSON_ERRO(400, "Código TOTP inválido.");
  await tenta(p);
  await p.alerta.filter({ hasText: "Código TOTP inválido." }).waitFor();
  const detalhe = await p.alerta.innerText();
  p.s.regerar = (r) => { p.s.mfa = { ...ON, backup_codes_remaining: 10 }; return r.abort("internetdisconnected"); }; // o servidor comitou; a resposta não voltou
  await tenta(p);
  await p.alerta.filter({ hasText: PERDIDO }).waitFor();
  await p.painel.getByText("Códigos de backup restantes: 10 de 10").waitFor(); // o status foi relido por trás do modal
  const r = [await p.alerta.innerText(), await p.dialogo.count(), await p.senha.inputValue(), await p.codigo.inputValue(), await p.enviar.isEnabled(), await p.cofre.count()];
  p.s.regerar = null;
  await tenta(p);
  await p.cofre.waitFor();
  const cofre = await p.codigos();
  await p.ctx.close();
  assert.equal(detalhe, "Código TOTP inválido.");
  assert.deepEqual(r, [PERDIDO, 1, "", "", true, 0]);
  assert.deepEqual(cofre, NOVOS);
});

for (const [nome, corpo] of [["lista vazia", []], ["8 textos e 1 número", [...NOVOS.slice(0, 8), 5]], ["texto vazio no meio", [...NOVOS.slice(0, 4), "", ...NOVOS.slice(5)]], ["texto solto", "ABC"], ["null", null]]) {
  test(`regerar, 200 fora do contrato (${nome}): o mesmo texto de códigos perdidos, nenhum código na tela, o modal segue aberto`, async () => {
    const p = await abrirSeguranca({ mfa: ON });
    p.s.regerar = (r) => r.fulfill({ json: { backup_codes: corpo } });
    await p.bt("Gerar novos códigos de backup").click();
    await tenta(p);
    await p.alerta.filter({ hasText: PERDIDO }).waitFor();
    const r = [await p.dialogo.count(), await p.cofre.count(), await p.page.locator(".mfa-codigos").count()];
    await p.ctx.close();
    assert.deepEqual(r, [1, 0, 0]);
  });
}

test("depois do T2 (enable perdido, MFA ligado) o foco está no botão 'Gerar novos códigos de backup' dentro do modal, não no <body>", async () => {
  const p = await abrirSeguranca();
  await ateOQr(p);
  p.s.enable = (r) => { p.s.mfa = { ...ON, backup_codes_remaining: 10 }; return r.abort("internetdisconnected"); };
  await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.alerta.filter({ hasText: "O MFA foi ativado, mas os códigos não chegaram." }).waitFor();
  await p.page.waitForTimeout(150);
  const foco = await p.page.evaluate(() => ({ dentro: !!document.activeElement.closest("dialog[open]"), texto: document.activeElement.textContent.trim() }));
  await p.ctx.close();
  assert.deepEqual(foco, { dentro: true, texto: "Gerar novos códigos de backup" });
});

const T2 = "O MFA foi ativado, mas os códigos não chegaram. Gere novos códigos.";
const T5 = "Não foi possível ativar agora. Tente de novo.";

test("enable perdido E status fora do ar (T5); a nova tentativa volta 409 'já ativado': T2 + oferta de gerar novos códigos, foco no botão, QR some", async () => {
  const p = await abrirSeguranca();
  await ateOQr(p);
  p.s.statusErro = (r) => r.abort("internetdisconnected");
  p.s.enable = (r) => { p.s.mfa = { ...ON, backup_codes_remaining: 10 }; return r.abort("internetdisconnected"); };
  await p.codigo.fill(TOTP); await p.enviar.click();
  await p.alerta.filter({ hasText: T5 }).waitFor();
  const t5 = [await p.dialogo.getByRole("button", { name: "Gerar novos códigos de backup" }).count(), await p.dialogo.locator(".mfa-qr img").count()];
  p.s.statusErro = null;
  p.s.enable = JSON_ERRO(409, "MFA já está ativado.");
  await p.codigo.fill(TOTP); await p.enviar.click();
  await p.alerta.filter({ hasText: T2 }).waitFor();
  await p.page.waitForTimeout(150);
  const r = [await p.alerta.innerText(), await p.dialogo.getByRole("button", { name: "Gerar novos códigos de backup" }).count(), await p.dialogo.locator(".mfa-qr img").count(), await p.foco()];
  await p.dialogo.getByRole("button", { name: "Gerar novos códigos de backup" }).click();
  await p.senha.fill(SENHA); await p.codigo.fill(TOTP); await p.enviar.click();
  await p.cofre.waitFor();
  const cofre = await p.codigos();
  await p.ctx.close();
  assert.deepEqual(t5, [0, 1]);
  assert.deepEqual(r, [T2, 1, 0, "Gerar novos códigos de backup"]);
  assert.deepEqual(cofre, NOVOS);
});

test("o 409 NÃO vira T2 fora do enable: no passo da senha (setup) sai verbatim, sem oferta; e o 400 de código errado no enable mantém o QR e o detalhe", async () => {
  const p = await abrirSeguranca();
  p.s.setup = JSON_ERRO(409, "MFA já está ativado.");
  await p.bt("Ativar").click();
  await p.senha.fill(SENHA); await p.enviar.click();
  await p.alerta.filter({ hasText: "MFA já está ativado." }).waitFor();
  await p.page.waitForTimeout(150);
  const setup = [await p.alerta.innerText(), await p.dialogo.getByRole("button", { name: "Gerar novos códigos de backup" }).count()];
  p.s.setup = null;
  await p.senha.fill(SENHA); await p.enviar.click();
  await p.dialogo.locator(".mfa-qr img").waitFor();
  p.s.enable = JSON_ERRO(400, "Código inválido. Tente novamente.");
  await p.codigo.fill(TOTP); await p.enviar.click();
  await p.alerta.filter({ hasText: "Código inválido. Tente novamente." }).waitFor();
  const enable = [await p.alerta.innerText(), await p.dialogo.getByRole("button", { name: "Gerar novos códigos de backup" }).count(), await p.dialogo.locator(".mfa-qr img").count()];
  await p.ctx.close();
  assert.deepEqual(setup, ["MFA já está ativado.", 0]);
  assert.deepEqual(enable, ["Código inválido. Tente novamente.", 0, 1]);
});
