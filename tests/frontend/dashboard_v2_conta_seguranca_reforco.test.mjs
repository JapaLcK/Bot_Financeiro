/**
 * Segurança da Conta, o que a primeira bateria deixava passar (cada caso tem a mutação que o reprova, medida fora do repositório):
 *   · campos: senha com type=password em TODO modo, TOTP com autocomplete=one-time-code; o segredo do setup é TEXTO, nunca HTML;
 *   · uid: só inteiro positivo habilita o link da senha (0, -3, 1.5, "7", null e ausente não postam nada);
 *   · "Copiar todos" sem navigator.clipboard (Safari, contexto inseguro): aviso, sem exceção; o reset da senha também tem prazo de 30 s;
 *   · status: uma falha da releitura não deixa o valor velho na tela; o primeiro 5xx é repetido (retry); "restantes" só com número;
 *   · o Perfil mantém o 404 FORA dos textos do servidor (só a Segurança o usa verbatim).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { JSON_ERRO, SENHA, SESSAO, abrirSeguranca, ateOCofre, portao } from "./_conta_seguranca.mjs";
import { abrirPerfil } from "./_conta_perfil.mjs";

const browser = navegador();
void browser;
const ON = { enabled: true, has_pending_secret: false, backup_codes_remaining: 7 };

test("campos de cada modo: a senha é type=password (current-password) e o TOTP é one-time-code, em ativar, desativar e regerar", async () => {
  const lidos = {};
  const campos = (p, nome) => p.page.evaluate((n) => { const i = document.querySelector(`dialog[open] input[name=${n}]`); return i ? [i.type, i.autocomplete] : null; }, nome);
  const ativar = await abrirSeguranca();
  await ativar.bt("Ativar").click();
  lidos.ativarSenha = await campos(ativar, "senha");
  await ativar.senha.fill(SENHA); await ativar.enviar.click();
  await ativar.dialogo.locator(".mfa-qr img").waitFor();
  lidos.ativarCodigo = await campos(ativar, "codigo");
  await ativar.ctx.close();
  for (const [modo, rotulo] of [["desativar", "Desativar"], ["regerar", "Gerar novos códigos de backup"]]) {
    const p = await abrirSeguranca({ mfa: ON });
    await p.bt(rotulo).click();
    lidos[modo + "Senha"] = await campos(p, "senha");
    lidos[modo + "Codigo"] = await campos(p, "codigo");
    await p.ctx.close();
  }
  assert.deepEqual(lidos, {
    ativarSenha: ["password", "current-password"], ativarCodigo: ["text", "one-time-code"],
    desativarSenha: ["password", "current-password"], desativarCodigo: ["text", "one-time-code"],
    regerarSenha: ["password", "current-password"], regerarCodigo: ["text", "one-time-code"],
  });
});

test("o segredo do setup é texto: um segredo com <img onerror> aparece escrito e não vira elemento nem executa", async () => {
  const XSS = '<img src=x onerror="window.__xss=1">';
  const p = await abrirSeguranca();
  p.s.setup = (r) => r.fulfill({ json: { secret: XSS, uri: "x", qr_code: "data:image/svg+xml;base64,PHN2Zy8+" } });
  await p.bt("Ativar").click();
  await p.senha.fill(SENHA); await p.enviar.click();
  await p.dialogo.locator(".mfa-segredo").waitFor();
  await p.page.waitForTimeout(150);
  const r = await p.page.evaluate(() => { const c = document.querySelector(".mfa-segredo"); return [c.textContent, c.children.length, window.__xss ?? null]; });
  await p.ctx.close();
  assert.deepEqual(r, [XSS, 0, null]);
});

const UIDS = [["0", 0], ["-3", -3], ["1.5", 1.5], ['"7" (texto)', "7"], ["null", null], ["ausente", undefined, true], ["7 (válido)", 7]];
for (const [nome, uid, semUid] of UIDS) {
  test(`user_id ${nome}: ${uid === 7 ? "habilita o botão e posta /settings/7/password-reset" : "o botão da senha fica desativado e nenhum POST sai, nem forçando o clique"}`, async () => {
    const p = await abrirSeguranca({ uid, semUid: !!semUid });
    const botao = p.bt("Resetar senha por e-mail");
    await botao.waitFor();
    await p.page.waitForTimeout(150);
    const desativado = await botao.isDisabled();
    // O React ignora o clique em botão `disabled`, até com o atributo tirado: chama o onClick direto, para sobrar só a guarda do handler.
    if (desativado) await botao.evaluate((b) => b[Object.keys(b).find((k) => k.startsWith("__reactProps"))].onClick());
    else await botao.click();
    await p.page.waitForTimeout(300);
    const posts = p.s.reqs.filter((q) => q.metodo === "POST").map((q) => new URL(q.url).pathname);
    await p.ctx.close();
    assert.equal(desativado, uid !== 7);
    assert.deepEqual(posts, uid === 7 ? ["/settings/7/password-reset"] : []);
  });
}

test("'Copiar todos' com navigator.clipboard INDEFINIDO desde o início: 'Não foi possível copiar' e nenhuma exceção da página", async () => {
  const p = await abrirSeguranca();
  await p.page.evaluate(() => { Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true }); });
  await ateOCofre(p);
  await p.cofre.getByRole("button", { name: "Copiar todos" }).click();
  await p.cofre.locator("[role=status]").getByText("Não foi possível copiar").waitFor();
  await p.page.waitForTimeout(150);
  await p.ctx.close();
  assert.deepEqual(p.erros, []);
});

test("reset da senha sem resposta: estoura em 30 s (prazo encurtado), 'Erro ao enviar reset' e o botão volta", async () => {
  const p = await abrirSeguranca();
  const [segura, solta] = portao();
  p.s.reset = async () => { await segura; };
  await p.encurtaPrazo(400);
  await p.bt("Resetar senha por e-mail").click();
  await p.painel.getByRole("alert").filter({ hasText: "Erro ao enviar reset" }).waitFor();
  const r = [await p.bt("Resetar senha por e-mail").isEnabled(), await p.page.evaluate(() => [...window.__ms])];
  solta();
  await p.ctx.close();
  assert.deepEqual(r, [true, [30000]]);
});

test("o primeiro 5xx do status é repetido sozinho: sem alerta, o 'Desativar' aparece (2 leituras)", async () => {
  const p = await abrirSeguranca({ mfa: ON });
  await p.bt("Desativar").waitFor();
  p.s.statusErro = (r) => { p.s.statusErro = null; return r.fulfill({ status: 500, body: "x" }); };
  const antes = p.s.statusHits;
  await p.page.reload();
  await p.bt("Desativar").waitFor();
  const r = [p.s.statusHits - antes, await p.painel.getByRole("alert").count()];
  await p.ctx.close();
  assert.deepEqual(r, [2, 0]);
});

for (const [nome, falha] of [["500", (r) => r.fulfill({ status: 500, body: "x" })], ["401 de sessão", (r) => r.fulfill({ status: 401, json: { detail: "Not authenticated" }, headers: SESSAO })]]) {
  test(`status em cache 'Ativada' e a releitura falha (${nome}): o valor velho (palavra, ícone de ativado) não fica na tela`, async () => {
    const p = await abrirSeguranca({ mfa: ON });
    await p.bt("Desativar").waitFor();
    p.s.statusErro = falha;
    await p.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
    await p.painel.getByRole("alert").waitFor();
    const r = await p.page.evaluate(() => { const l = document.querySelectorAll(".conta-seguranca dl > div")[1]; return { sr: [...l.querySelectorAll("dd .sr-only")].map((e) => e.textContent), ok: l.querySelectorAll(".perfil-ok").length, icone: l.querySelector("dt i").className }; });
    await p.ctx.close();
    assert.deepEqual(r, { sr: [], ok: 0, icone: "ph ph-shield" });
  });
}

for (const [nome, restantes] of [["null", null], ["ausente", undefined], ["texto", "7"]]) {
  test(`backup_codes_remaining ${nome}: a linha 'Códigos de backup restantes' não aparece; com número (0) aparece`, async () => {
    const p = await abrirSeguranca({ mfa: { enabled: true, has_pending_secret: false, backup_codes_remaining: restantes } });
    await p.bt("Desativar").waitFor();
    const sem = await p.painel.innerText();
    p.s.mfa = { ...ON, backup_codes_remaining: 0 };
    await p.page.reload();
    await p.painel.getByText("Códigos de backup restantes: 0 de 10").waitFor();
    await p.ctx.close();
    assert.ok(!sem.includes("restantes") && !sem.includes("null") && !sem.includes("undefined"), sem);
  });
}

test("Perfil: um 404 no salvar NÃO mostra o texto do servidor (a lista de status do Perfil é só 400/401/409/429)", async () => {
  const p = await abrirPerfil();
  p.s.patch = JSON_ERRO(404, "Usuário não encontrado.");
  await p.lapis("nome").click();
  await p.campo.fill("Nome Novo");
  await p.salvar.click();
  await p.alerta.waitFor();
  const r = await p.alerta.innerText();
  await p.ctx.close();
  assert.equal(r, "Não foi possível salvar. Seu rascunho foi mantido.");
});
