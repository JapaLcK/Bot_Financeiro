/**
 * O Cofre dos códigos de backup do MFA (conta/Cofre.tsx, montado em main.tsx como IRMÃO do portão): o servidor os devolve UMA vez.
 *   · só "Concluído" fecha: Esc (6×, inclusive o 3º do Chromium), clique no fundo e Cancelar não fecham;
 *   · aviso do SSE, foco da aba, troca de página e o portão cair (/me 500×3) NÃO apagam os códigos;
 *   · a resposta do enable chega mesmo com o modal do MFA já desmontado (troca de página em voo);
 *   · "Copiar todos" (avisos de leitor de tela) e `beforeunload` ligado enquanto houver códigos;
 *   · no máximo UM pedido gerador de códigos (enable, regenerate-backup-codes) em voo: enquanto um pendente (de um modal já desmontado)
 *     não responde, o botão de enviar dos dois modos fica desabilitado e nem Enter/requestSubmit mandam um 2º POST; a resposta libera;
 *   · Safari 14 (sem <dialog>): o Cofre abre DEPOIS do modal do MFA fechar (o fallback só aceita uma trava por vez).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { CODIGOS, JSON_ERRO, NOVOS, SENHA, TOTP, abrirSeguranca, ate, ateOCofre, ateOQr, portao, rastros } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const ON = { enabled: true, has_pending_secret: false, backup_codes_remaining: 4 };
// Abre o Cofre por um dos dois caminhos: ativar (CODIGOS) ou gerar novos (NOVOS).
const COMO = {
  ativar: { opcoes: {}, abre: ateOCofre, codigos: CODIGOS },
  regerar: { opcoes: { mfa: ON }, abre: async (p) => { await p.bt("Gerar novos códigos de backup").click(); await p.senha.fill(SENHA); await p.codigo.fill(TOTP); await p.enviar.click(); await p.cofre.waitFor(); }, codigos: NOVOS },
};

for (const [nome, { opcoes, abre, codigos }] of Object.entries(COMO)) {
  test(`${nome}: aviso do SSE, foco da aba e troca de página (e volta) NÃO apagam os códigos nem fecham o Cofre`, async () => {
    const p = await abrirSeguranca(opcoes);
    await abre(p);
    const lidos = [];
    const le = async (quando) => lidos.push([quando, (await p.codigos()).join(), await p.cofre.count()]);
    await le("aberto");
    p.s.sse(); // o SSE invalida TODAS as consultas: o status é relido por trás do modal
    const hits = p.s.statusHits;
    await ate(p.page, () => p.s.statusHits > hits, "o SSE releu o status");
    await le("sse");
    await p.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
    await p.page.waitForTimeout(300);
    await le("foco");
    await p.page.evaluate(() => { location.hash = "#/gastos"; });
    await p.page.locator("#page-title", { hasText: "Para onde vai" }).waitFor();
    await le("outra página");
    await p.page.evaluate(() => { location.hash = "#/conta/configuracoes"; });
    await p.page.locator(".conta-seguranca").waitFor();
    await le("de volta");
    await p.ctx.close();
    assert.deepEqual(lidos, ["aberto", "sse", "foco", "outra página", "de volta"].map((q) => [q, codigos.join(), 1]));
  });
}

test("o portão cair (SSE + /me 500 nas 3 tentativas): o painel vira a tela de erro e os códigos continuam na tela, até o 'Concluído'", async () => {
  const p = await abrirSeguranca();
  await ateOCofre(p);
  p.s.apiMe = (r) => r.fulfill({ status: 500, json: { error: { code: "interno" } } });
  p.s.sse();
  await p.page.getByText("Não deu para carregar o painel").waitFor({ state: "attached", timeout: 20000 });
  const r = [await p.codigos(), await p.cofre.count(), await p.page.locator(".conta-seguranca").count()];
  await p.cofre.getByRole("button", { name: "Concluído" }).click(); await p.fechado();
  const depois = await p.page.locator(".mfa-codigos").count();
  await p.ctx.close();
  assert.deepEqual(r, [CODIGOS, 1, 0]); // o painel SAIU (0 painel de segurança); o Cofre ficou
  assert.equal(depois, 0);
});

test("Esc (6×, com o 3º do Chromium), clique no fundo e Tab não fecham o Cofre; só o 'Concluído' fecha e os códigos somem do DOM e do status", async () => {
  const p = await abrirSeguranca();
  await ateOCofre(p);
  await p.page.waitForTimeout(500); // passada a janela em que o clique no fundo ainda não vale
  const aberto = [];
  for (let i = 0; i < 6; i++) { await p.page.keyboard.press("Escape"); await p.page.waitForTimeout(40); aberto.push(await p.cofre.count()); }
  await p.page.mouse.click(5, 5);
  await p.page.waitForTimeout(200);
  for (let i = 0; i < 4; i++) await p.page.keyboard.press("Tab");
  const foco = await p.page.evaluate(() => !!document.activeElement.closest("dialog[open]"));
  await p.page.waitForTimeout(200);
  const antes = [await p.cofre.count(), await p.codigos()];
  await p.cofre.getByRole("button", { name: "Concluído" }).click(); await p.fechado();
  p.s.sse();
  await p.page.waitForTimeout(400);
  const depois = [await p.page.locator(".mfa-codigos").count(), await p.page.locator("dialog[open]").count()];
  await p.ctx.close();
  assert.ok(aberto.every((n) => n === 1), aberto.join()); // `count` pode piscar 0 entre o `close` e o reabrir: só vale o fim
  assert.deepEqual(antes, [1, CODIGOS]);
  assert.ok(foco, "o Tab ficou dentro do Cofre");
  assert.deepEqual(depois, [0, 0]); // nem o SSE nem o refetch trazem os códigos de volta
});

test("a resposta do enable chega com o modal do MFA já desmontado (troca de página em voo): o Cofre abre na outra página", async () => {
  const p = await abrirSeguranca();
  await p.bt("Ativar").click();
  await p.senha.fill(SENHA); await p.enviar.click();
  await p.dialogo.locator(".mfa-qr img").waitFor();
  const [segura, solta] = portao();
  p.s.enable = async (r) => { await segura; p.s.mfa = { enabled: true, has_pending_secret: false, backup_codes_remaining: 10 }; return r.fulfill({ json: { ok: true, backup_codes: CODIGOS } }); };
  await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.dialogo.getByRole("button", { name: "Enviando…" }).waitFor();
  await p.page.evaluate(() => { location.hash = "#/gastos"; }); // desmonta a Segurança e o modal, com o POST em voo
  await p.page.locator("#page-title", { hasText: "Para onde vai" }).waitFor();
  solta();
  await p.cofre.waitFor();
  const r = [await p.codigos(), await p.page.locator("#page-title").innerText()];
  await p.ctx.close();
  assert.deepEqual(r, [CODIGOS, "Para onde vai o dinheiro"]);
});

test("beforeunload: desligado sem códigos, ligado com o Cofre aberto, desligado depois do 'Concluído'", async () => {
  const p = await abrirSeguranca();
  const bloqueia = () => p.page.evaluate(() => { const e = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; });
  const antes = await bloqueia();
  await ateOCofre(p);
  const aberto = await bloqueia();
  await p.cofre.getByRole("button", { name: "Concluído" }).click(); await p.fechado();
  const depois = await bloqueia();
  await p.ctx.close();
  assert.deepEqual([antes, aberto, depois], [false, true, false]);
});

test("'Copiar todos': copia os códigos (um por linha) e avisa 'Códigos copiados'; sem permissão ou sem clipboard, 'Não foi possível copiar'", async () => {
  const p = await abrirSeguranca();
  await p.ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: "http://127.0.0.1:1" });
  await ateOCofre(p);
  const status = p.cofre.locator("[role=status]");
  await p.cofre.getByRole("button", { name: "Copiar todos" }).click();
  await status.getByText("Códigos copiados").waitFor();
  const copiado = await p.page.evaluate(() => navigator.clipboard.readText());
  await p.page.evaluate(() => { navigator.clipboard.writeText = () => Promise.reject(new Error("negado")); });
  await p.cofre.getByRole("button", { name: "Copiar todos" }).click();
  await status.getByText("Não foi possível copiar").waitFor();
  await p.page.evaluate(() => { Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true }); });
  await p.cofre.getByRole("button", { name: "Concluído" }).focus();
  await p.cofre.getByRole("button", { name: "Copiar todos" }).click();
  await status.getByText("Não foi possível copiar").waitFor();
  await p.ctx.close();
  assert.equal(copiado, CODIGOS.join("\n"));
});

test("o Cofre tem nome, e o foco entra nele (Copiar todos); depois do 'Concluído' o foco não fica preso num elemento morto", async () => {
  const p = await abrirSeguranca();
  await ateOCofre(p);
  const r = [await p.page.getByRole("dialog", { name: "Códigos de backup" }).count(), await p.page.evaluate(() => document.activeElement.textContent)];
  await p.cofre.getByRole("button", { name: "Concluído" }).click(); await p.fechado();
  const depois = await p.page.evaluate(() => document.activeElement === document.body || document.body.contains(document.activeElement));
  await p.ctx.close();
  assert.deepEqual(r, [1, "Copiar todos"]);
  assert.ok(depois);
});

test("Safari 14 (sem <dialog>): ativar até o Cofre — o modal do MFA fecha ANTES, o Cofre trava Esc e fundo, 'Concluído' fecha; nada sobra no DOM", async () => {
  const p = await abrirSeguranca({ semDialog: true });
  await ateOCofre(p);
  const unico = await p.page.locator("dialog[open]").count(); // só o Cofre: o do MFA já fechou
  await p.page.waitForTimeout(500);
  for (let i = 0; i < 4; i++) await p.page.keyboard.press("Escape");
  await p.page.mouse.click(5, 5);
  await p.page.waitForTimeout(200);
  const preso = await p.page.evaluate(() => document.querySelectorAll("dialog[open]").length);
  await p.page.keyboard.press("Tab"); await p.page.keyboard.press("Tab"); await p.page.keyboard.press("Tab");
  const foco = await p.page.evaluate(() => !!document.activeElement.closest("dialog[open]"));
  await p.cofre.getByRole("button", { name: "Concluído" }).click(); await p.fechado();
  const resto = await rastros(p.page);
  await p.ctx.close();
  assert.equal(unico, 1);
  assert.equal(preso, 1);
  assert.ok(foco, "o foco ficou dentro do Cofre");
  for (const c of CODIGOS) assert.ok(!resto.includes(c), c);
});

test("o que o Cofre mostrou não volta: depois do 'Concluído', recarregar a página não traz os códigos", async () => {
  const p = await abrirSeguranca();
  await ateOCofre(p);
  await p.cofre.getByRole("button", { name: "Concluído" }).click(); await p.fechado();
  await p.page.reload();
  await p.painel.waitFor();
  await p.page.waitForTimeout(300);
  const r = [await p.page.locator(".mfa-codigos").count(), (await rastros(p.page)).includes(CODIGOS[0])];
  await p.ctx.close();
  assert.deepEqual(r, [0, false]);
});

// Um pedido gerador (A) sem prazo fica retido no portão; o usuário troca de rota (o form desmonta, o POST segue), volta e abre o modal B.
const OFF = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 };
const MODO = {
  regerar: { mfa: ON, hook: "regerar", codigos: NOVOS, corpo: { backup_codes: NOVOS }, abre: (p) => p.bt("Gerar novos códigos de backup").click(), preenche: async (p) => { await p.senha.fill(SENHA); await p.codigo.fill(TOTP); } },
  ativar: { mfa: OFF, hook: "enable", codigos: CODIGOS, corpo: { ok: true, backup_codes: CODIGOS }, abre: ateOQr, preenche: (p) => p.codigo.fill(TOTP) },
};
const posts = (p) => p.s.chamadas.enable.length + p.s.chamadas.regerar.length;
const rota = async (p, hash, espera) => { await p.page.evaluate((h) => { location.hash = h; }, hash); await espera.waitFor(); };
const submit = (p) => p.page.locator("dialog[open] form button[type=submit]");
// Abre A, deixa-o em voo, desmonta e volta; devolve o que solta A. O status do servidor passa a ser o que B precisa para abrir.
async function emVooAte(a, b) {
  const p = await abrirSeguranca({ mfa: MODO[a].mfa });
  const [segura, solta] = portao();
  p.s[MODO[a].hook] = async (r) => { await segura; return r.fulfill({ json: MODO[a].corpo }); };
  await MODO[a].abre(p); await MODO[a].preenche(p); await p.enviar.click();
  await p.dialogo.getByRole("button", { name: "Enviando…" }).waitFor();
  await rota(p, "#/gastos", p.page.locator("#page-title", { hasText: "Para onde vai" }));
  p.s.mfa = MODO[b].mfa;
  await rota(p, "#/conta/configuracoes", p.painel);
  return { p, solta };
}

for (const [a, b] of [["regerar", "regerar"], ["ativar", "ativar"], ["ativar", "regerar"], ["regerar", "ativar"]]) {
  test(`${a} pendente (modal desmontado) + modal ${b}: o envio fica desabilitado ('Enviando…'), Enter e requestSubmit não mandam 2º POST; a resposta mostra os códigos de A e reabilita`, async () => {
    const { p, solta } = await emVooAte(a, b);
    await MODO[b].abre(p); await MODO[b].preenche(p);
    const bloqueado = [await submit(p).isDisabled(), await submit(p).innerText()];
    await p.codigo.press("Enter");
    await p.page.evaluate(() => document.querySelector("dialog[open] form").requestSubmit());
    await p.page.waitForTimeout(400);
    const durante = posts(p);
    assert.deepEqual(bloqueado, [true, "Enviando…"]);
    assert.equal(durante, 1, "só o POST de A saiu");
    solta();
    await p.cofre.waitFor();
    const r = { codigos: await p.codigos(), depois: posts(p) };
    await p.page.waitForFunction(() => !document.querySelector("dialog[open] form button[type=submit]").disabled);
    const texto = await submit(p).innerText();
    await p.ctx.close();
    assert.deepEqual(r, { codigos: MODO[a].codigos, depois: 1 });
    assert.notEqual(texto, "Enviando…");
  });
}

for (const [nome, falha] of [["regerar", JSON_ERRO(500, "interno")], ["ativar", (r) => r.abort()]]) {
  test(`${nome}: o pedido FALHA (${nome === "regerar" ? "500" : "abort"}): o estado 'em voo' desliga e um novo pedido sai e abre o Cofre`, async () => {
    const m = MODO[nome];
    const p = await abrirSeguranca({ mfa: m.mfa });
    p.s[m.hook] = (r) => { p.s[m.hook] = null; return falha(r); }; // só o 1º falha; o 2º cai no padrão do servidor
    await m.abre(p); await m.preenche(p); await p.enviar.click();
    await p.alerta.waitFor();
    await m.preenche(p); await p.enviar.click();
    await p.cofre.waitFor();
    const r = [posts(p), await p.codigos()];
    await p.ctx.close();
    assert.deepEqual(r, [2, m.codigos]);
  });
}

test("modal bloqueado por pedido pendente: Cancelar, Fechar e Esc continuam livres (não é um pedido deste modal)", async () => {
  const { p, solta } = await emVooAte("regerar", "regerar");
  const fecha = [];
  for (const como of [() => p.bt("Cancelar").click(), () => p.bt("Fechar").click(), () => p.page.keyboard.press("Escape")]) {
    await MODO.regerar.abre(p);
    await submit(p).waitFor();
    fecha.push(await submit(p).isDisabled());
    await como();
    await p.fechado();
  }
  solta();
  await p.cofre.waitFor();
  const r = await p.codigos();
  await p.ctx.close();
  assert.deepEqual(fecha, [true, true, true]);
  assert.deepEqual(r, NOVOS);
});
