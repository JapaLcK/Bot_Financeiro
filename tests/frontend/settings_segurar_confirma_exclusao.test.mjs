/**
 * Exclusão imediata de conta no settings.html (S1): o que o corpo do DELETE leva, o botão
 * "Enviar código" e o resultado `deleting`. Usa o harness do segurar-para-confirmar.
 *
 * Controles: H1 vermelho sem o `totp` no corpo; H2 vermelho se o `totp` for sempre enviado
 * ou o campo aparecer sem MFA; H3 vermelho sem o POST do reauth-code; H4 vermelho se
 * `deleting` mostrar "Conta excluída". O positivo é o `deleted` do
 * settings_segurar_confirma.test.mjs.
 *
 * Rodar:  node --test tests/frontend/settings_segurar_confirma_exclusao.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { sobe, derruba, ACOES, abre, aperta, waitFor, json } from "./_hold_harness.mjs";

before(sobe);
after(derruba);

const a = ACOES.find((x) => x.nome === "delete");
const holdSegurado = async (page) => { await aperta(page, a); await page.clock.runFor(1200); };
const textoDoModal = (page) => page.evaluate(() => document.querySelector(".pig-modal-overlay")?.textContent || "");

test("H1: MFA ligado — o campo do autenticador aparece e o corpo leva confirmacao, code e totp", async () => {
  const { page, ctx, reqs } = await abre(a, { mfa: true });
  try {
    assert.equal(await page.isVisible("#delete-totp"), true);
    await holdSegurado(page);
    await waitFor(() => reqs.length === 1, "a requisição do hold");
    assert.deepEqual(reqs[0].corpo, { ...a.corpo, totp: "654321" });
  } finally { await ctx.close(); }
});

test("H2: MFA desligado — o campo some e o corpo não leva totp", async () => {
  const { page, ctx, reqs } = await abre(a);
  try {
    assert.equal(await page.isVisible("#delete-totp"), false);
    assert.equal(await page.isVisible("#delete-confirmacao"), true);
    assert.equal(await page.isVisible("#delete-codigo"), true);
    await holdSegurado(page);
    await waitFor(() => reqs.length === 1, "a requisição do hold");
    assert.equal("totp" in reqs[0].corpo, false);
  } finally { await ctx.close(); }
});

test("H3: Enviar código — POST no reauth-code e a linha de status com o e-mail mascarado", async () => {
  const { page, ctx } = await abre(a);
  const posts = [];
  try {
    await page.route("**/auth/account/reauth-code", (route) => {
      posts.push(route.request().method());
      return route.fulfill(json({ status: "sent", expires_in_minutes: 15, email_mascarado: "l•••@dominio.com" }));
    });
    await page.click("#delete-codigo-btn");
    await waitFor(() => posts.length === 1, "o POST do reauth-code");
    await waitFor(() => page.evaluate(() => document.getElementById("delete-codigo-status").textContent
      === "Código enviado para l•••@dominio.com."), "a linha de status");
    assert.deepEqual(posts, ["POST"]);
    await page.evaluate(() => hideDeleteConfirm());   // Cancelar limpa os campos e o status
    assert.equal(await page.evaluate(() => document.getElementById("delete-codigo-status").textContent), "");
  } finally { await ctx.close(); }
});

test("H3b: erro ao enviar o código vira toast e o botão volta a habilitar", async () => {
  const { page, ctx } = await abre(a);
  try {
    await page.route("**/auth/account/reauth-code", (route) =>
      route.fulfill(json({ detail: "Sua conta ainda não tem senha. Defina uma senha para continuar." }, 409)));
    await page.click("#delete-codigo-btn");
    await waitFor(() => page.evaluate(() => /ainda não tem senha/.test(document.getElementById("toast").textContent)), "o toast");
    assert.equal(await page.isEnabled("#delete-codigo-btn"), true);
  } finally { await ctx.close(); }
});

test("H4: status deleting — alerta 'Excluindo' com o texto de 'estamos concluindo'", async () => {
  const { page, ctx, reqs } = await abre(a, { resposta: () => json({ ok: true, status: "deleting", deleted: false }) });
  try {
    await holdSegurado(page);
    await waitFor(() => reqs.length === 1, "a requisição do hold");
    await waitFor(async () => /Estamos concluindo\. Você recebe um e-mail ao terminar\./.test(await textoDoModal(page)),
                  "o alerta do deleting");
    assert.match(await textoDoModal(page), /Excluindo/);
    assert.doesNotMatch(await textoDoModal(page), /Conta excluída/);
  } finally { await ctx.close(); }
});

test("H4b: storage que lança (webview restrito) — a exclusão já feita ainda navega para a home, sem toast de erro", async () => {
  const { page, ctx, reqs } = await abre(a, { resposta: () => json({ ok: true, status: "deleted", deleted: true }) });
  try {
    await page.evaluate(() => { Storage.prototype.setItem = () => { throw new DOMException("bloqueado", "SecurityError"); }; });
    await holdSegurado(page);
    await waitFor(() => reqs.length === 1, "a requisição do hold");
    await waitFor(async () => /Pronto, sua conta foi excluída\./.test(await textoDoModal(page)), "o alerta de conta excluída");
    // O relógio do harness está pausado (a animação do modal não termina): clica pelo DOM.
    await page.evaluate(() => document.querySelector(".pig-modal-btn-primary").click());
    await waitFor(() => new URL(page.url()).pathname === "/", "a navegação para a home");
    assert.equal(await page.evaluate(() => document.querySelectorAll(".toast.error, .toast-error").length), 0);
  } finally { await ctx.close(); }
});

test("H5: erro do servidor (frase) aparece no toast", async () => {
  const { page, ctx, reqs } = await abre(a, {
    resposta: () => json({ detail: { code: "confirmacao_invalida", message: "Digite EXCLUIR para confirmar." } }, 400),
  });
  try {
    await holdSegurado(page);
    await waitFor(() => reqs.length === 1, "a requisição do hold");
    await waitFor(() => page.evaluate(() => /Digite EXCLUIR para confirmar\./.test(document.getElementById("toast").textContent)), "o toast");
  } finally { await ctx.close(); }
});

const SO_GOOGLE = { has_password: false, precisa_criar_senha: false };

test("H6: conta só-Google — o bloco abre, a senha some e o corpo vai sem password, com code", async () => {
  const { page, ctx, reqs } = await abre(a, { me: SO_GOOGLE });
  try {
    assert.equal(await page.evaluate(() => document.getElementById("delete-confirm").classList.contains("show")), true);
    assert.equal(await page.isVisible("#delete-password"), false);
    assert.equal(await page.isVisible("#delete-codigo"), true);
    await page.evaluate(() => { document.getElementById("delete-password").value = ""; });   // a senha nunca é digitada
    await holdSegurado(page);
    await waitFor(() => reqs.length === 1, "a requisição do hold");
    assert.deepEqual(reqs[0].corpo, { code: "123456", confirmacao: "EXCLUIR" });
    assert.equal(await page.evaluate(() => /Confirme sua senha/.test(document.getElementById("toast").textContent)), false);
  } finally { await ctx.close(); }
});

test("H6b: conta só-Google sem o código — o servidor recusa (sem password no corpo) e o toast mostra o erro dele", async () => {
  const { page, ctx, reqs } = await abre(a, {
    me: SO_GOOGLE,
    resposta: () => json({ detail: { code: "codigo_obrigatorio", message: "Peça o código por e-mail." } }, 400),
  });
  try {
    await page.evaluate(() => { document.getElementById("delete-codigo").value = ""; });
    await holdSegurado(page);
    await waitFor(() => reqs.length === 1, "a requisição do hold");
    assert.deepEqual(reqs[0].corpo, { code: "", confirmacao: "EXCLUIR" });
    await waitFor(() => page.evaluate(() => /Peça o código por e-mail\./.test(document.getElementById("toast").textContent)), "o toast");
  } finally { await ctx.close(); }
});

test("H7: conta sem credencial (quiz/funil) continua barrada — o bloco não abre e pede definir a senha", async () => {
  const { page, ctx, reqs } = await abre(a, { me: { has_password: false, precisa_criar_senha: true } });
  try {
    await page.evaluate(() => window.showDeleteConfirm());
    assert.equal(await page.evaluate(() => document.getElementById("delete-confirm").classList.contains("show")), false);
    await waitFor(() => page.evaluate(() => /Defina uma senha primeiro/.test(document.querySelector(".pig-modal-overlay")?.textContent || "")),
                  "o diálogo de definir senha");
    assert.equal(reqs.length, 0);
  } finally { await ctx.close(); }
});

test("H8: o campo do autenticador não tem inputmode numérico (o código de backup é alfanumérico)", async () => {
  const { page, ctx } = await abre(a, { mfa: true });
  try {
    assert.equal(await page.getAttribute("#delete-totp", "inputmode"), null);
    assert.equal(await page.getAttribute("#delete-totp", "autocomplete"), "one-time-code");
    assert.equal(await page.getAttribute("#delete-totp", "aria-label"), "Código do autenticador ou backup");
  } finally { await ctx.close(); }
});

test("H9: duas cargas do status do MFA em corrida — o campo do autenticador não some para quem tem MFA", async () => {
  const { page, ctx } = await abre(a, { mfa: true });
  try {
    // "nunca carregado" + a carga do abrir-Excluir e a da seção de segurança juntas: a 1ª é
    // superada e não atribui `_mfaStatus`, que fica no valor velho (enabled:false).
    await page.evaluate(() => {
      _mfaStatus = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 };
      hideDeleteConfirm();
      showDeleteConfirm();
      loadMfaStatus();
    });
    await waitFor(() => page.evaluate(() => _mfaStatus.enabled === true), "a carga mais nova assentar");
    await new Promise((r) => setTimeout(r, 300));
    assert.equal(await page.isVisible("#delete-totp"), true);
  } finally { await ctx.close(); }
});

// ── Exportar: o card de excluir manda "baixe sua cópia antes", então o modo código também exporta ──
const exportar = { nome: "export", abrir: "showExportConfirm", fechar: "hideExportConfirm", bloco: "export-confirm",
  senha: "export-password", botao: "export-confirm-btn", metodo: "POST", rota: "**/auth/account/export",
  sucesso: { status: "email_sent", expires_in_minutes: 15, email_mascarado: "l•••@dominio.com" },
  campos: {}, corpo: { password: "senha123" } };
const bloco = (page) => page.evaluate(() => document.getElementById("export-confirm").classList.contains("show"));

test("X1: exportar com senha — o campo aparece e o corpo leva password (caminho legítimo)", async () => {
  const { page, ctx, reqs } = await abre(exportar);
  try {
    assert.equal(await bloco(page), true);
    assert.equal(await page.isVisible("#export-password"), true);
    await page.click("#export-confirm-btn");
    await waitFor(() => reqs.length === 1, "o POST do export");
    assert.deepEqual(reqs[0].corpo, { password: "senha123" });
  } finally { await ctx.close(); }
});

test("X1b: exportar com senha e campo vazio — 0 requisições e toast 'Confirme sua senha'", async () => {
  const { page, ctx, reqs } = await abre(exportar, { senha: "" });
  try {
    await page.click("#export-confirm-btn");
    await waitFor(() => page.evaluate(() => /Confirme sua senha/.test(document.getElementById("toast").textContent)), "o toast");
    await new Promise((r) => setTimeout(r, 200));
    assert.equal(reqs.length, 0);
  } finally { await ctx.close(); }
});

test("X2: exportar conta só-Google — o bloco abre, a senha some e o POST vai sem password", async () => {
  const { page, ctx, reqs } = await abre(exportar, { me: SO_GOOGLE, senha: "" });
  try {
    assert.equal(await bloco(page), true);
    assert.equal(await page.isVisible("#export-password"), false);
    await page.click("#export-confirm-btn");
    await waitFor(() => reqs.length === 1, "o POST do export");
    assert.deepEqual(reqs[0].corpo, {});
    assert.equal(await page.evaluate(() => /Confirme sua senha/.test(document.getElementById("toast").textContent)), false);
  } finally { await ctx.close(); }
});

test("X3: exportar conta sem credencial continua barrado — o bloco não abre e pede definir a senha", async () => {
  const { page, ctx, reqs } = await abre(exportar, { me: { has_password: false, precisa_criar_senha: true } });
  try {
    await page.evaluate(() => window.showExportConfirm());
    assert.equal(await bloco(page), false);
    await waitFor(() => page.evaluate(() => /Defina uma senha primeiro/.test(document.querySelector(".pig-modal-overlay")?.textContent || "")),
                  "o diálogo de definir senha");
    assert.equal(reqs.length, 0);
  } finally { await ctx.close(); }
});
