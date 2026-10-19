/**
 * MFA da Segurança (conta/MfaForm.tsx, conta/api.ts): ativar, desativar e regerar os códigos, em modal.
 *   · ativar = senha, depois QR + segredo + código de 6 dígitos; os códigos de backup vão ao Cofre (..._cofre.test.mjs);
 *   · vazio e código fora de 6 dígitos: texto de validação, SEM POST; código errado no enable (400) mantém o QR; 409 no setup reconcilia o status;
 *   · as bordas (401, erros, duplo envio, travas em voo, prazo de 30 s, enable perdido, vazamento) estão em ..._mfa_bordas.test.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { CODIGOS, JSON_ERRO, NOVOS, SEGREDO, SENHA, TOTP, abrirSeguranca, ateOQr } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const ON = { enabled: true, has_pending_secret: false, backup_codes_remaining: 10 };

test("ativar: senha, QR + segredo + código, Cofre com os 10 códigos, status vira Ativada (10 de 10); corpos e CSRF certos", async () => {
  const p = await abrirSeguranca();
  await p.bt("Ativar").click();
  const passo1 = [await p.dialogo.locator("h2").innerText(), await p.dialogo.locator("p").first().innerText(), await p.dialogo.getByLabel("Senha atual").getAttribute("autocomplete"), await p.foco()];
  await p.senha.fill(SENHA);
  await p.senha.press("Enter");
  await p.dialogo.locator(".mfa-qr img").waitFor();
  const passo2 = await p.page.evaluate(() => ({
    h2: document.querySelector("dialog[open] h2").textContent, sub: document.querySelector("dialog[open] p").textContent,
    alt: document.querySelector(".mfa-qr img").alt, src: document.querySelector(".mfa-qr img").src.slice(0, 26),
    segredo: document.querySelector(".mfa-segredo").textContent, senha: document.querySelectorAll("dialog[open] input[name=senha]").length,
    rotulo: document.querySelector("dialog[open] input[name=codigo]").closest("label").firstChild.textContent, foco: document.activeElement.name,
    fundoDoQr: getComputedStyle(document.querySelector(".mfa-qr")).backgroundColor,
    texto: [...document.querySelectorAll("dialog[open] p")].map((e) => e.textContent),
  }));
  await p.codigo.fill(TOTP);
  await p.codigo.press("Enter");
  await p.cofre.waitFor();
  const cofre = [await p.cofre.locator("h2").innerText(), await p.codigos(), await p.cofre.locator("p").allInnerTexts()];
  await p.cofre.getByRole("button", { name: "Concluído" }).click();
  await p.fechado();
  await p.bt("Desativar").waitFor();
  const depois = [await p.painel.getByText("Códigos de backup restantes: 10 de 10").count(), await p.painel.locator(".perfil-ok").count()];
  await p.ctx.close();
  assert.deepEqual(passo1, ["Ativar autenticação em 2 etapas", "Confirme sua senha para começar.", "current-password", "senha"]);
  assert.deepEqual({ ...passo2, texto: undefined }, { h2: "Escaneie o QR code", sub: "Use Google Authenticator, Authy, 1Password ou similar.", alt: "QR code MFA", src: "data:image/svg+xml;base64,", segredo: SEGREDO, senha: 0,
    rotulo: "Código gerado pelo app (6 dígitos)", foco: "codigo", fundoDoQr: "rgb(255, 255, 255)", texto: undefined });
  assert.ok(passo2.texto.includes("Ou digite manualmente este segredo"));
  assert.deepEqual(cofre, ["Códigos de backup", CODIGOS, ["Guarde esses códigos em local seguro. Cada um pode ser usado uma vez se você perder o app autenticador.", " Você não verá esses códigos novamente. Salve agora.", ""]]);
  assert.deepEqual(depois, [1, 1]);
  assert.deepEqual(p.s.chamadas.setup.map((c) => [c.password, c.csrf]), [[SENHA, "tok-123"]]);
  assert.deepEqual(p.s.chamadas.enable.map((c) => [c.code, c.csrf, c.metodo]), [[TOTP, "tok-123", "POST"]]);
  assert.deepEqual(p.s.reqs.filter((q) => q.metodo === "POST").map((q) => new URL(q.url).pathname), ["/auth/mfa/setup", "/auth/mfa/enable"]);
});

for (const [nome, codigo] of [["TOTP", TOTP], ["código de backup", CODIGOS[0]]]) {
  test(`desativar com ${nome}: corpo {password, code}, 'MFA desativado' (aviso), status vira Desativada e o 'Ativar' volta`, async () => {
    const p = await abrirSeguranca({ mfa: ON });
    await p.bt("Desativar").click();
    const form = [await p.dialogo.locator("h2").innerText(), await p.dialogo.locator("p").first().innerText(), await p.dialogo.getByLabel("Senha").count(), await p.dialogo.getByLabel("Código TOTP ou de backup").count()];
    await p.senha.fill(SENHA);
    await p.codigo.fill(codigo);
    await p.enviar.click(); await p.fechado();
    await p.bt("Ativar").waitFor();
    const r = [await p.aviso.innerText(), await p.painel.locator(".sr-only", { hasText: "Desativada" }).count(), await p.bt("Gerar novos códigos de backup").count()];
    await p.ctx.close();
    assert.deepEqual(form, ["Desativar autenticação em 2 etapas", "Confirme com sua senha e código atual do app (ou um código de backup).", 1, 1]);
    assert.deepEqual(r, ["MFA desativado", 1, 0]);
    assert.deepEqual(p.s.chamadas.disable.map((c) => [c.password, c.code, c.csrf]), [[SENHA, codigo, "tok-123"]]);
  });
}

test("regerar: corpo {password, code}, Cofre 'Novos códigos de backup' com os códigos NOVOS e o aviso dos anteriores", async () => {
  const p = await abrirSeguranca({ mfa: { ...ON, backup_codes_remaining: 2 } });
  await p.painel.getByText("Códigos de backup restantes: 2 de 10").waitFor();
  await p.bt("Gerar novos códigos de backup").click();
  const form = [await p.dialogo.locator("h2").innerText(), await p.dialogo.locator("p").first().innerText(), await p.dialogo.getByLabel("Código TOTP atual").getAttribute("inputmode")];
  await p.senha.fill(SENHA);
  await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.cofre.waitFor();
  const r = [await p.cofre.locator("h2").innerText(), await p.codigos(), await p.cofre.locator("p").allInnerTexts()];
  await p.cofre.getByRole("button", { name: "Concluído" }).click();
  await p.painel.getByText("Códigos de backup restantes: 10 de 10").waitFor();
  await p.ctx.close();
  assert.deepEqual(form, ["Gerar novos códigos de backup", "Os códigos antigos serão invalidados. Confirme com senha e TOTP.", "numeric"]);
  assert.deepEqual(r, ["Novos códigos de backup", NOVOS, [" Os anteriores foram invalidados. Salve estes agora.", ""]]);
  assert.deepEqual(p.s.chamadas.regerar.map((c) => [c.password, c.code, c.csrf]), [[SENHA, TOTP, "tok-123"]]);
});

test("validação no cliente, SEM POST: senha vazia, código fora de 6 dígitos, senha ou código vazios (desativar/regerar)", async () => {
  const p = await abrirSeguranca();
  const vistos = [];
  const tenta = async (campos) => {
    for (const [campo, valor] of Object.entries(campos)) await p.dialogo.locator(`input[name=${campo}]`).fill(valor);
    await p.enviar.click();
    await p.alerta.waitFor();
    vistos.push([await p.alerta.innerText(), await p.foco()]);
  };
  await p.bt("Ativar").click();
  await tenta({});
  await p.senha.fill(SENHA); await p.enviar.click(); await p.dialogo.locator(".mfa-qr img").waitFor();
  await tenta({ codigo: "12345" });
  await tenta({ codigo: "12345a" });
  await tenta({ codigo: "  " });
  await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado();
  p.s.mfa = ON;
  await p.page.reload();
  for (const rotulo of ["Desativar", "Gerar novos códigos de backup"]) {
    await p.bt(rotulo).click();
    await tenta({});
    await tenta({ codigo: TOTP });
    await tenta({ senha: SENHA, codigo: "" });
    await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado();
  }
  await p.ctx.close();
  assert.deepEqual(vistos, [
    ["Digite sua senha", "senha"], ["Digite os 6 dígitos do app", "codigo"], ["Digite os 6 dígitos do app", "codigo"], ["Digite os 6 dígitos do app", "codigo"],
    ...[0, 1].flatMap(() => [["Preencha senha e código", "senha"], ["Preencha senha e código", "senha"], ["Preencha senha e código", "codigo"]]),
  ]);
  assert.equal(p.s.chamadas.setup.length, 1); // só o do passo da senha
  assert.deepEqual([p.s.chamadas.enable, p.s.chamadas.disable, p.s.chamadas.regerar].map((c) => c.length), [0, 0, 0]);
});

test("ativar, código errado (400): o texto do servidor, o QR e o segredo ficam, o código é limpo e o foco volta a ele; o certo, depois, ativa", async () => {
  const p = await abrirSeguranca();
  await ateOQr(p);
  await p.codigo.fill("000000");
  await p.enviar.click();
  await p.alerta.waitFor();
  await p.page.waitForTimeout(100);
  const r = [await p.alerta.innerText(), await p.dialogo.locator(".mfa-qr img").count(), await p.dialogo.locator(".mfa-segredo").innerText(), await p.codigo.inputValue(), await p.foco()];
  await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.cofre.waitFor();
  await p.ctx.close();
  assert.deepEqual(r, ["Código inválido. Tente novamente.", 1, SEGREDO, "", "codigo"]);
  assert.deepEqual(p.s.chamadas.enable.map((c) => c.code), ["000000", TOTP]);
  assert.equal(p.s.chamadas.setup.length, 1); // o QR não foi pedido de novo
});

test("ativar com o MFA já ativado em outra aba (409 no setup): o texto do servidor e a Segurança passa a mostrar 'Ativada'", async () => {
  const p = await abrirSeguranca();
  p.s.setup = (r) => { p.s.mfa = { enabled: true, has_pending_secret: false, backup_codes_remaining: 10 }; return JSON_ERRO(409, "MFA já está ativado.")(r); };
  await p.bt("Ativar").click();
  await p.senha.fill(SENHA);
  await p.enviar.click();
  await p.alerta.waitFor();
  await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado();
  await p.bt("Desativar").waitFor();
  const r = await p.painel.locator(".sr-only", { hasText: "Ativada" }).count();
  await p.ctx.close();
  assert.equal(r, 1);
});

