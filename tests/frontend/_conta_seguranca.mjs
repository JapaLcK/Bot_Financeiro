/**
 * O que os testes da Segurança da Conta (dashboard_v2_conta_seguranca*.test.mjs) compartilham: o servidor de mentira do MFA e
 * da senha, e o `abrirSeguranca`. Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import { abrir } from "./_guia.mjs";
import { SESSAO, JSON_ERRO, portao, ate } from "./_conta_perfil.mjs";

export { SESSAO, JSON_ERRO, portao, ate };
export const SENHA = "Senha-Do-Teste-9!";
export const TOTP = "123456";
export const SEGREDO = "JBSWY3DPEHPK3PXPSEGREDO42";
export const CODIGOS = ["AAAA1111", "BBBB2222", "CCCC3333", "DDDD4444", "EEEE5555", "FFFF6666", "GGGG7777", "HHHH8888", "IIII9999", "JJJJ0000"];
export const NOVOS = ["NNNN1111", "NNNN2222", "NNNN3333", "NNNN4444", "NNNN5555", "NNNN6666", "NNNN7777", "NNNN8888", "NNNN9999", "NNNN0000"];
export const MSG_RESET = "Enviamos um link de redefinição de senha para o seu e-mail.";
export const SEM_SENHA = "Sua conta ainda não tem senha. Defina uma senha por e-mail.";
export const QR = "data:image/svg+xml;base64," + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="29" height="29"><rect width="29" height="29" fill="#fff"/><rect x="2" y="2" width="9" height="9"/></svg>').toString("base64");
const mfaOff = () => ({ enabled: false, has_pending_secret: false, backup_codes_remaining: 0 });

// O servidor de mentira (as rotas antigas /auth/mfa/*, /auth/me, /settings/7/password-reset). `s.reqs`: TODA requisição, com a URL
// completa (query inclusa) e o corpo. `s.chamadas.<rota>`: os corpos que chegaram. Cada rota tem um gancho `s.<rota>(r, corpo)` que,
// se posto, responde no lugar do padrão; `s.status` / `s.statusErro` fazem o mesmo com o GET do status. `s.sse()` solta um aviso do SSE.
export function servidor({ mfa = mfaOff(), temSenha = true, uid = 7, semUid = false, meRuim = false } = {}) {
  const s = { mfa, temSenha, meRuim, reqs: [], refreshes: 0, statusHits: 0, apiMe: null, statusErro: null,
    chamadas: { setup: [], enable: [], disable: [], regerar: [], reset: [] }, setup: null, enable: null, disable: null, regerar: null, reset: null };
  const [segura, solta] = portao();
  s.sse = solta;
  const ok = (r, json) => r.fulfill({ json });
  const rota = (ctx, caminho, nome, padrao) => ctx.route((u) => u.pathname === caminho, async (r) => {
    const corpo = r.request().postDataJSON() ?? {};
    s.chamadas[nome].push({ ...corpo, csrf: r.request().headers()["x-csrf-token"], metodo: r.request().method() });
    return s[nome] ? s[nome](r, corpo) : padrao(r, corpo);
  });
  s.antes = async (ctx) => {
    ctx.on("request", (q) => s.reqs.push({ url: q.url(), metodo: q.method(), corpo: q.postData() ?? "" }));
    await ctx.route((u) => u.pathname === "/api/v2/me", (r) => (s.apiMe ? s.apiMe(r) : r.fallback()));
    await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => ok(r, { display_name: "Ana Teste", email: "ana.teste@exemplo.com", ...(semUid ? {} : { user_id: uid }), whatsapp_linked: false }));
    await ctx.route((u) => u.pathname === "/auth/me", (r) => (s.meRuim ? r.fulfill({ status: 500, json: { detail: "x" } }) : ok(r, { has_password: s.temSenha })));
    await ctx.route((u) => u.pathname === "/auth/refresh", (r) => { s.refreshes++; return r.fulfill({ status: 401, json: { detail: "expirou" } }); });
    await ctx.route((u) => u.pathname === "/auth/mfa/status", async (r) => {
      s.statusHits++;
      if (s.statusErro) return s.statusErro(r);
      const foto = structuredClone(s.mfa);
      if (s.statusSegura) await s.statusSegura;
      return ok(r, foto);
    });
    await rota(ctx, "/auth/mfa/setup", "setup", (r, c) => c.password === SENHA ? ok(r, { secret: SEGREDO, uri: `otpauth://totp/PigBank:ana?secret=${SEGREDO}`, qr_code: QR }) : JSON_ERRO(401, "Senha incorreta.")(r));
    await rota(ctx, "/auth/mfa/enable", "enable", (r, c) => {
      if (c.code !== TOTP) return JSON_ERRO(400, "Código inválido. Tente novamente.")(r);
      s.mfa = { enabled: true, has_pending_secret: false, backup_codes_remaining: 10 };
      return ok(r, { ok: true, backup_codes: CODIGOS });
    });
    await rota(ctx, "/auth/mfa/disable", "disable", (r, c) => {
      if (c.password !== SENHA) return JSON_ERRO(401, "Senha incorreta.")(r);
      if (c.code !== TOTP && !CODIGOS.includes(c.code)) return JSON_ERRO(400, "Código inválido.")(r);
      s.mfa = mfaOff();
      return ok(r, { ok: true });
    });
    await rota(ctx, "/auth/mfa/regenerate-backup-codes", "regerar", (r, c) => {
      if (c.password !== SENHA) return JSON_ERRO(401, "Senha incorreta.")(r);
      if (c.code !== TOTP) return JSON_ERRO(400, "Código TOTP inválido.")(r);
      s.mfa = { ...s.mfa, backup_codes_remaining: 10 };
      return ok(r, { backup_codes: NOVOS });
    });
    await rota(ctx, "/settings/7/password-reset", "reset", (r) => ok(r, { ok: true, message: MSG_RESET }));
    await ctx.route("**/api/v2/eventos", async (r) => {
      await segura;
      r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 600000\ndata: {"recurso":"tudo"}\n\n' }).catch(() => {});
    });
  };
  return s;
}

export async function abrirSeguranca({ width = 1440, height = 900, semDialog = false, ...opts } = {}) {
  const s = servidor(opts);
  const p = await abrir({ guia: "concluido", width, height, rota: "/conta/configuracoes", antes: (ctx) => s.antes(ctx), semDialog });
  const { page } = p;
  await page.locator(".conta-seguranca").waitFor({ timeout: 12000 });
  page.setDefaultTimeout(15000); // regressão reprova em segundos, não nos 30 s do padrão
  const bt = (nome) => page.getByRole("button", { name: nome, exact: true });
  return {
    ...p, s, bt,
    dialogo: page.locator("dialog[open]"),
    senha: page.locator("dialog[open] input[name=senha]"),
    codigo: page.locator("dialog[open] input[name=codigo]"),
    alerta: page.locator("dialog[open] [role=alert]"),
    enviar: page.locator("dialog[open] button[type=submit]"),
    cofre: page.locator("dialog[open]:has(.mfa-codigos)"),
    painel: page.locator(".conta-seguranca"),
    aviso: page.locator(".conta-seguranca > p[role=status]"),
    fechado: () => page.locator("dialog[open]").waitFor({ state: "detached" }),
    foco: () => page.evaluate(() => document.activeElement?.name || document.activeElement?.textContent?.trim()),
    codigos: () => page.locator(".mfa-codigos code").allTextContents(),
    // Encurta o setTimeout de 30000 ms (e só ele) para `ms` e registra o que o app pediu em `window.__ms`.
    encurtaPrazo: (ms = 400) => page.evaluate((m) => { const st = window.setTimeout; window.__ms = []; window.setTimeout = (f, d, ...a) => { if (d === 30000) { window.__ms.push(d); d = m; } return st(f, d, ...a); }; }, ms),
  };
}

// Do "Ativar" ao QR (passo 2) e do QR ao Cofre aberto.
export async function ateOQr(p) {
  await p.bt("Ativar").click();
  await p.senha.fill(SENHA);
  await p.enviar.click();
  await p.dialogo.locator(".mfa-qr img").waitFor();
}
export async function ateOCofre(p) {
  await ateOQr(p);
  await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.cofre.waitFor();
}

// As requisições onde o `segredo` aparece fora das rotas em que ele PODE ir (`onde`: pathnames): na URL, inclusive na query, ou no corpo.
export const vazou = (s, segredo, onde = []) => s.reqs
  .filter((q) => q.url.includes(segredo) || q.url.includes(encodeURIComponent(segredo)) || (q.corpo.includes(segredo) && !onde.includes(new URL(q.url).pathname)))
  .map((q) => `${q.metodo} ${q.url}`);

// O que sobra de segredo no aparelho: storage, endereço, e o HTML inteiro da página.
export const rastros = (page) => page.evaluate(() => [localStorage, sessionStorage].flatMap((st) => Object.keys(st).map((k) => k + "=" + st.getItem(k))).join("|") + "|" + location.href + "|" + document.documentElement.outerHTML);
