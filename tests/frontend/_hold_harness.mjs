/**
 * Harness dos testes de "segurar para confirmar" (settings_segurar_confirma*.test.mjs):
 * settings logado, seção de dados aberta, bloco inline aberto, senha digitada e
 * relógio falso PAUSADO. O prefixo `_` o deixa fora do glob `*.test.mjs`.
 */
import { startServer } from "./_server.mjs";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const env = {};   // { proc, origin, browser }: o `sobe` preenche

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

export async function waitFor(cond, what, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await cond()) return;
    await sleep(50);
  }
  throw new Error(`timeout esperando: ${what}`);
}

export async function sobe() {
  const { proc, origin } = await startServer();
  Object.assign(env, { proc, origin, browser: await chromium.launch() });
}
export async function derruba() { await env.browser?.close(); env.proc?.kill(); }

const SECURITY_OK = { ok: true, user_id: 1, email: "a@b.com", plan: "pro", display_name: "Japa", identities: [] };
const T0 = 1_800_000_000_000;

export const ACOES = [
  { nome: "reset", abrir: "showResetConfirm", fechar: "hideResetConfirm", bloco: "reset-confirm", senha: "reset-password",
    botao: "reset-confirm-btn", metodo: "POST", rota: "**/settings/reset", sucesso: { ok: true },
    campos: {}, corpo: { password: "senha123" },
    rotulo: "Segure para apagar e recomeçar", modal: "Apagar tudo e recomeçar" },
  { nome: "delete", abrir: "showDeleteConfirm", fechar: "hideDeleteConfirm", bloco: "delete-confirm", senha: "delete-password",
    botao: "delete-confirm-btn", metodo: "DELETE", rota: "**/auth/account", sucesso: { ok: true, status: "deleted", deleted: true },
    // a frase e o código do e-mail digitados; a senha vai pelo campo `senha`
    campos: { "delete-confirmacao": "EXCLUIR", "delete-codigo": "123456" },
    corpo: { password: "senha123", code: "123456", confirmacao: "EXCLUIR" },
    rotulo: "Segure para excluir a conta", modal: "Excluir conta" },
];

/**
 * `resposta(n)` devolve o que a rota da ação responde (pode ser uma Promise: é como o
 * teste segura o fetch). `reqs` junta as requisições da ação, `errs` os pageerrors.
 * `me`: o corpo do /auth/me (conta só-Google: `{has_password:false, precisa_criar_senha:false}`;
 * sem credencial: `precisa_criar_senha:true`, e aí a página abre na segurança e `abre` volta cedo,
 * sem relógio falso nem campos preenchidos).
 * `mfa`: o /auth/mfa/status diz que o MFA está ligado (a exclusão passa a levar `totp`).
 * `semHold`: aborta o /hold-confirm.js (script que falha ao carregar).
 * `holdStub`: serve esse texto no lugar do /hold-confirm.js (ex.: `window.PBHoldConfirm = {};`).
 */
export async function abre(a, { senha = "senha123", resposta, touch = false, viewport, semHold = false, holdStub, mfa = false, me = { has_password: true } } = {}) {
  const ctx = await env.browser.newContext({ hasTouch: touch, ...(viewport && { viewport }) });
  const page = await ctx.newPage();
  const reqs = [];
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== env.origin) return route.abort();
    if (semHold && url.pathname === "/hold-confirm.js") return route.abort();
    if (holdStub !== undefined && url.pathname === "/hold-confirm.js") {
      return route.fulfill({ status: 200, contentType: "application/javascript", body: holdStub });
    }
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (route) => route.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (route) => route.fulfill(json(me)));
  await page.route("**/settings/1/security", (route) => route.fulfill(json(SECURITY_OK)));
  await page.route("**/auth/mfa/status", (route) => route.fulfill(json({ enabled: mfa })));
  await page.route(a.rota, async (route) => {
    const req = route.request();
    if (req.method() !== a.metodo) return route.fallback();
    reqs.push({ metodo: req.method(), corpo: req.postDataJSON() });
    const r = resposta ? await resposta(reqs.length) : json(a.sucesso);
    try { await route.fulfill(r); } catch { /* navegou/fechou */ }
  });
  await page.goto(`${env.origin}/settings.html?view=data`);
  if (me.precisa_criar_senha) {
    await waitFor(() => page.evaluate(() => !document.getElementById("section-security").hidden), "seção de segurança");
    return { page, ctx, reqs, errs };
  }
  await waitFor(() => page.evaluate(() => !document.getElementById("section-data").hidden),
                "seção de dados (boot do initSettings)");
  await page.evaluate(([abrir, id, s, campos]) => {
    window[abrir]();
    document.getElementById(id).value = s;
    for (const [campo, valor] of Object.entries(campos)) document.getElementById(campo).value = valor;
  }, [a.abrir, a.senha, senha, a.campos]);
  if (mfa && a.nome === "delete") {   // o campo do autenticador aparece quando o status do MFA volta
    await waitFor(() => page.isVisible("#delete-totp"), "o campo do autenticador");
    await page.evaluate(() => { document.getElementById("delete-totp").value = "654321"; });
  }
  await page.locator(`#${a.botao}`).scrollIntoViewIfNeeded();
  await page.clock.install({ time: T0 });
  await page.clock.pauseAt(T0 + 1000);
  return { page, ctx, reqs, errs };
}

export const estado = (page, a) => page.getAttribute(`#${a.botao}`, "data-hold-state");
export const modalAberto = (page) => page.evaluate(() => !!document.querySelector(".pig-modal-overlay"));
/** O confirmModal tem "Cancelar"; o alertModal pós-sucesso da exclusão não. */
export const confirmAberto = (page) => page.evaluate(() => !!document.querySelector(".pig-modal-btn-cancel"));
export const rotulo = (page, a) => page.evaluate((id) => document.querySelector(`#${id} .hold-label`).textContent, a.botao);

export async function centro(page, a) {
  const b = await page.locator(`#${a.botao}`).boundingBox();
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}
export async function aperta(page, a) {
  const { x, y } = await centro(page, a);
  await page.mouse.move(x, y);
  await page.mouse.down();
}
/** Dá tempo de uma requisição indevida chegar à rede antes de afirmar que NÃO houve. */
export const assenta = () => sleep(200);
