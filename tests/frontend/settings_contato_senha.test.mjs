/**
 * B0a em frontend/settings.html: trocar o e-mail pede a senha atual (campo "Senha atual"
 * no editor do e-mail) — só para quem tem senha (has_password do /auth/me).
 *
 * Casos: (1) com senha o campo aparece e o PATCH leva `current_password`; (2) sem senha
 * (só-Google / sem credencial) o campo some e o corpo é `{email}` puro; (3) 401/400 do
 * servidor aparecem no toast; (4) o campo é limpo depois do envio, certo ou errado, e
 * a senha não vai para URL nem storage; (5) campo vazio nem chama o servidor (o 400 dele
 * gasta o teto de 5/hora); (6) nome e telefone não mandam senha e NÃO apagam a senha
 * digitada no editor do e-mail (S6); (7) duplo clique em Salvar = 1 PATCH nos três
 * editores, e o botão volta (S7); (8) HAS_PASSWORD que muda com o editor aberto vale
 * na hora do envio (S8).
 * Mutação: sem o `hidden = !HAS_PASSWORD` derruba (2); sem o `senha.value = ""` do finally
 * derruba (4); sem o `body.current_password` derruba (1); `senha.value = ""` sem o
 * `kind === "email"` derruba S6; `salvar.disabled = true` fora derruba S7; o
 * `senha.hidden = !HAS_PASSWORD` de saveSecurityContact fora derruba S8.
 *
 * Rodar:  node --test tests/frontend/settings_contato_senha.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const COM_SENHA = { precisa_criar_senha: false, has_password: true, of_ui_enabled: true };
const SO_GOOGLE = { precisa_criar_senha: false, has_password: false, of_ui_enabled: true };

async function waitFor(cond, what, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) { if (await cond()) return; await sleep(50); }
  throw new Error(`timeout esperando: ${what}`);
}

/** `resposta`: o que o PATCH do contato devolve; `patches`: [{url, body}] do que saiu. */
async function abre(me, resposta, viewport = { width: 390, height: 844 }, atraso = 0) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const patches = [];
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) => route.fulfill({ status: 200,
    contentType: "application/javascript", body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) => route.fulfill(json(me)));
  await page.route("**/settings/1/security", (route) => route.fulfill(json({ email: "a@b.com", plan: "pro", display_name: "Japa" })));
  await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: false })));
  await page.route("**/settings/1/sessions", (route) => route.fulfill(json({ sessions: [{ id: 1, current: true }] })));
  await page.route("**/settings/1/security/contact", async (route) => {
    patches.push({ url: route.request().url(), body: JSON.parse(route.request().postData() || "{}") });
    if (atraso) await sleep(atraso);   // PATCH em voo: dá tempo do segundo clique
    return route.fulfill(resposta());
  });
  await page.goto(`${ORIGIN}/settings.html?view=security`);
  await waitFor(() => page.evaluate(() => !document.getElementById("section-security").hidden), "o boot");
  await sleep(300);   // o /auth/me do boot assenta o HAS_PASSWORD
  return { page, patches, fechar: () => ctx.close() };
}
const toast = (page) => page.evaluate(() => document.getElementById("toast").textContent);
const senhaVisivel = (page) => page.evaluate(() => {
  const b = document.getElementById("security-email-senha").getBoundingClientRect();
  return b.width > 0 && b.height > 0;
});

for (const [nome, viewport] of [["mobile", { width: 390, height: 844 }], ["desktop", { width: 1280, height: 800 }]]) {
  test(`S1 com senha (${nome}): o campo aparece dentro da tela e o PATCH leva current_password; o campo é limpo`, async () => {
    const { page, patches, fechar } = await abre(COM_SENHA, () => json({ email: "novo@b.com", plan: "pro" }), viewport);
    try {
      await page.click("#security-email-edit-btn");
      assert.ok(await senhaVisivel(page), "campo Senha atual não apareceu");
      assert.equal(await page.getAttribute("#security-email-senha", "autocomplete"), "current-password");
      assert.equal(await page.getAttribute("#security-email-senha", "aria-label"), "Senha atual");
      const caixas = await page.evaluate(() => ["security-email-senha", "security-email-input"].map((id) => {
        const b = document.getElementById(id).getBoundingClientRect();
        return { l: b.left, r: b.right, t: b.top, b: b.bottom };
      }));
      for (const c of caixas) assert.ok(c.l >= 0 && c.r <= viewport.width, `campo fora da tela: ${JSON.stringify(c)}`);
      assert.ok(caixas[0].b <= caixas[1].t + 1, "a senha tem de ficar acima do e-mail, sem sobrepor");
      await page.fill("#security-email-senha", "Segredo-123");
      await page.fill("#security-email-input", "novo@b.com");
      await page.evaluate(() => saveSecurityContact("email"));
      await waitFor(async () => (await toast(page)).includes("E-mail atualizado"), "toast de sucesso");
      assert.deepEqual(patches.map((p) => p.body), [{ email: "novo@b.com", current_password: "Segredo-123" }]);
      assert.ok(!patches[0].url.includes("Segredo"), "senha na URL");
      assert.equal(await page.inputValue("#security-email-senha"), "", "senha ficou no campo");
      const guardado = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]));
      assert.ok(!guardado.includes("Segredo"), "senha foi parar no storage");
    } finally { await fechar(); }
  });
}

test("S2 sem senha (só-Google ou sem credencial): o campo some e o corpo é {email} puro", async () => {
  const { page, patches, fechar } = await abre(SO_GOOGLE, () => json({ email: "novo@b.com", plan: "pro" }));
  try {
    await page.click("#security-email-edit-btn");
    assert.equal(await senhaVisivel(page), false, "campo apareceu para quem não tem senha");
    await page.fill("#security-email-input", "novo@b.com");
    await page.evaluate(() => saveSecurityContact("email"));
    await waitFor(async () => patches.length === 1, "o PATCH");
    assert.deepEqual(patches[0].body, { email: "novo@b.com" });
  } finally { await fechar(); }
});

for (const [status, detail] of [[401, "Senha incorreta."], [400, "Confirme sua senha atual para trocar o e-mail."]]) {
  test(`S3 ${status} do servidor: o texto vai ao toast e o campo é limpo`, async () => {
    const { page, patches, fechar } = await abre(COM_SENHA, () => json({ detail }, status));
    try {
      await page.click("#security-email-edit-btn");
      await page.fill("#security-email-senha", "errada");
      await page.fill("#security-email-input", "novo@b.com");
      await page.evaluate(() => saveSecurityContact("email"));
      await waitFor(async () => (await toast(page)).includes(detail), "toast do erro");
      assert.equal(patches.length, 1);
      assert.equal(await page.inputValue("#security-email-senha"), "", "senha ficou no campo");
    } finally { await fechar(); }
  });
}

test("S4 campo vazio: nem chama o servidor", async () => {
  const { page, patches, fechar } = await abre(COM_SENHA, () => json({ email: "x@b.com" }));
  try {
    await page.click("#security-email-edit-btn");
    await page.fill("#security-email-input", "novo@b.com");
    await page.evaluate(() => saveSecurityContact("email"));
    await waitFor(async () => (await toast(page)).includes("Confirme sua senha atual para trocar o e-mail."), "toast");
    assert.equal(patches.length, 0);
  } finally { await fechar(); }
});

// O servidor trata o e-mail igual ao atual (strip+lower) como no-op sem senha e sem contar no teto.
// O rótulo carrega "a@b.com" (mock de /settings/1/security).
for (const digitado of ["a@b.com", "A@B.com", "  a@B.com  "]) {
  test(`S9 e-mail igual ao atual (${JSON.stringify(digitado)}): Salvar manda 1 PATCH sem current_password e sem o toast da senha`, async () => {
    const { page, patches, fechar } = await abre(COM_SENHA, () => json({ email: "a@b.com", plan: "pro" }));
    try {
      await page.click("#security-email-edit-btn");
      await page.fill("#security-email-input", digitado);
      await page.click("#security-email-edit button");
      await waitFor(async () => (await toast(page)).includes("E-mail atualizado"), "toast de sucesso");
      assert.equal(patches.length, 1);
      assert.deepEqual(patches[0].body, { email: digitado.trim() });
      assert.ok(!(await toast(page)).includes("Confirme sua senha"));
    } finally { await fechar(); }
  });
}

test("S9b e-mail diferente com senha digitada: leva current_password (positivo do S9)", async () => {
  const { page, patches, fechar } = await abre(COM_SENHA, () => json({ email: "novo@b.com", plan: "pro" }));
  try {
    await page.click("#security-email-edit-btn");
    await page.fill("#security-email-senha", "Segredo-123");
    await page.fill("#security-email-input", "novo@b.com");
    await page.click("#security-email-edit button");
    await waitFor(async () => patches.length === 1, "o PATCH");
    assert.deepEqual(patches[0].body, { email: "novo@b.com", current_password: "Segredo-123" });
  } finally { await fechar(); }
});

test("S5 nome e telefone não mandam senha (positivo: o corpo segue puro)", async () => {
  const { page, patches, fechar } = await abre(COM_SENHA, () => json({ email: "a@b.com", plan: "pro" }));
  try {
    await page.click("#security-name-edit-btn");
    await page.fill("#security-name-input", "Fulana");
    await page.evaluate(() => saveSecurityContact("name"));
    await waitFor(async () => patches.length === 1, "PATCH do nome");
    await page.click("#security-phone-edit-btn");
    await page.fill("#security-phone-input", "(11) 99999-9999");
    await page.evaluate(() => saveSecurityContact("phone"));
    await waitFor(async () => patches.length === 2, "PATCH do telefone");
    assert.deepEqual(patches.map((p) => p.body), [{ display_name: "Fulana" }, { phone: "(11) 99999-9999" }]);
  } finally { await fechar(); }
});

test("S6 salvar o telefone com o editor do e-mail aberto: nem manda nem apaga a senha digitada", async () => {
  const { page, patches, fechar } = await abre(COM_SENHA, () => json({ email: "a@b.com", plan: "pro" }));
  try {
    await page.click("#security-email-edit-btn");
    await page.fill("#security-email-senha", "Segredo-123");
    await page.click("#security-phone-edit-btn");
    await page.fill("#security-phone-input", "(11) 99999-9999");
    await page.evaluate(() => saveSecurityContact("phone"));
    await waitFor(async () => (await toast(page)).includes("Telefone atualizado"), "toast do telefone");
    assert.deepEqual(patches.map((p) => p.body), [{ phone: "(11) 99999-9999" }]);
    assert.equal(await page.inputValue("#security-email-senha"), "Segredo-123", "a senha do e-mail foi apagada");
  } finally { await fechar(); }
});

for (const kind of ["email", "name", "phone"]) {
  test(`S7 duplo clique em Salvar (${kind}): um PATCH só, e o botão volta`, async () => {
    const { page, patches, fechar } = await abre(COM_SENHA, () => json({ email: "a@b.com", plan: "pro" }), undefined, 500);
    try {
      await page.click(`#security-${kind}-edit-btn`);
      if (kind === "email") await page.fill("#security-email-senha", "Segredo-123");
      await page.fill(`#security-${kind}-input`, { email: "novo@b.com", name: "Fulana", phone: "(11) 99999-9999" }[kind]);
      await page.dblclick(`#security-${kind}-edit button`);   // o 1º botão do editor é o Salvar
      await sleep(900);   // passa do atraso do PATCH: um 2º envio já teria chegado
      assert.equal(patches.length, 1, `saíram ${patches.length} PATCH`);
      assert.equal(await page.evaluate((k) => document.querySelector(`#security-${k}-edit button`).disabled, kind), false, "botão ficou travado");
    } finally { await fechar(); }
  });
}

test("S7b erro do servidor: o botão Salvar volta a ficar utilizável", async () => {
  const { page, patches, fechar } = await abre(COM_SENHA, () => json({ detail: "Senha incorreta." }, 401));
  try {
    await page.click("#security-email-edit-btn");
    await page.fill("#security-email-senha", "errada");
    await page.fill("#security-email-input", "novo@b.com");
    await page.click("#security-email-edit button");
    await waitFor(async () => (await toast(page)).includes("Senha incorreta."), "toast");
    assert.equal(await page.evaluate(() => document.querySelector("#security-email-edit button").disabled), false);
    await page.fill("#security-email-senha", "outra");
    await page.click("#security-email-edit button");
    await waitFor(async () => patches.length === 2, "o 2º envio depois do erro");
  } finally { await fechar(); }
});

test("S8 HAS_PASSWORD muda com o editor aberto: vale a visibilidade do campo na hora do envio", async () => {
  // era só-Google (campo oculto), virou com-senha: não envia sem a senha, e o campo aparece
  let { page, patches, fechar } = await abre(SO_GOOGLE, () => json({ email: "novo@b.com", plan: "pro" }));
  try {
    await page.click("#security-email-edit-btn");
    assert.equal(await senhaVisivel(page), false);
    await page.evaluate(() => { HAS_PASSWORD = true; });
    await page.fill("#security-email-input", "novo@b.com");
    await page.click("#security-email-edit button");
    await waitFor(async () => (await toast(page)).includes("Confirme sua senha atual para trocar o e-mail."), "toast");
    assert.equal(patches.length, 0);
    assert.ok(await senhaVisivel(page), "o campo não apareceu");
  } finally { await fechar(); }
  // era com-senha (campo visível, preenchido), virou sem-senha: corpo {email} puro
  ({ page, patches, fechar } = await abre(COM_SENHA, () => json({ email: "novo@b.com", plan: "pro" })));
  try {
    await page.click("#security-email-edit-btn");
    await page.fill("#security-email-senha", "Segredo-123");
    await page.evaluate(() => { HAS_PASSWORD = false; });
    await page.fill("#security-email-input", "novo@b.com");
    await page.click("#security-email-edit button");
    await waitFor(async () => patches.length === 1, "o PATCH");
    assert.deepEqual(patches[0].body, { email: "novo@b.com" });
  } finally { await fechar(); }
});
