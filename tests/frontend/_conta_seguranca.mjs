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
// As sessões do servidor de mentira (dashboard_v2_conta_sessoes*.test.mjs). O relógio da página é fixo (_guia.mjs: 2026-10-02T15:00Z).
export const AGORA = Date.parse("2026-10-02T15:00:00Z");
export const ha = (ms) => new Date(AGORA - ms).toISOString().replace("Z", "123+00:00"); // 6 casas, como o servidor (NaN no Safari 14 com `new Date`)
export const ATUAL = "atual-1111";
export const DOIS = "0123456789abcdef0123456789abcdef"; // o jti real é uuid4().hex
export const listaPadrao = () => [
  { jti: ATUAL, device_label: "Chrome • macOS", ip: "203.0.113.5", user_agent: "Mozilla/5.0 (Macintosh) Chrome/120", created_at: ha(3600e3), last_seen_at: ha(30e3), is_current: true },
  { jti: DOIS, device_label: "Safari • iOS", ip: "198.51.100.7", user_agent: "Mozilla/5.0 (iPhone) Safari/604", created_at: ha(5 * 86400e3), last_seen_at: ha(330e3), is_current: false },
  { jti: "win-3333", device_label: "Edge • Windows", ip: null, user_agent: "Mozilla/5.0 (Windows) Edg/120", created_at: ha(40 * 86400e3), last_seen_at: ha(3.5 * 3600e3), is_current: false },
];
// As preferências de notificação (GET/PATCH /settings/7/notifications, dashboard_v2_conta_notificacoes.test.mjs).
export const notifPadrao = () => ({ ok: true, email: "ana.teste@exemplo.com", whatsapp_destination: "+5511987654321", email_notifications_available: true, whatsapp_updates_available: true,
  engagement_email_enabled: true, tip_email_enabled: true, insight_email_enabled: false, whatsapp_updates_enabled: true, daily_report_enabled: true, daily_report_hour: 9, daily_report_minute: 0,
  weekly_report_enabled: true, weekly_report_stored_enabled: true, weekly_report_available: true, monthly_report_enabled: false });
// O PATCH do servidor de mentira IMITA o backend (frontend/routes/settings.py): `daily_report_hour` LIGA o diário (set_daily_report_hour) ANTES de o
// `daily_report_enabled` do mesmo corpo ser aplicado; `weekly_report_enabled: true` sem o plano é 403 pro_required; e-mail/WhatsApp ausentes são 400.
function aplicaNotif(n, c) {
  if (c.weekly_report_enabled === true && !n.weekly_report_available) return { status: 403, json: { detail: { error: "pro_required" } } };
  if (("tip_email_enabled" in c || "insight_email_enabled" in c) && !n.email) return { status: 400, json: { detail: "Vincule um e-mail para configurar notificações por e-mail." } };
  if ("whatsapp_updates_enabled" in c && !n.whatsapp_destination) return { status: 400, json: { detail: "Vincule um WhatsApp para receber atualizações." } };
  for (const k of ["tip_email_enabled", "insight_email_enabled", "whatsapp_updates_enabled", "monthly_report_enabled"]) if (k in c) n[k] = c[k];
  if ("daily_report_hour" in c || "daily_report_minute" in c) { n.daily_report_hour = c.daily_report_hour ?? n.daily_report_hour; n.daily_report_minute = c.daily_report_minute ?? n.daily_report_minute; n.daily_report_enabled = true; }
  if ("daily_report_enabled" in c) n.daily_report_enabled = c.daily_report_enabled;
  if ("weekly_report_enabled" in c) n.weekly_report_stored_enabled = c.weekly_report_enabled;
  n.weekly_report_enabled = n.weekly_report_available && n.weekly_report_stored_enabled;
  return { json: structuredClone(n) };
}
const mfaOff = () => ({ enabled: false, has_pending_secret: false, backup_codes_remaining: 0 });

// O servidor de mentira (as rotas antigas /auth/mfa/*, /auth/me, /settings/7/password-reset). `s.reqs`: TODA requisição, com a URL
// completa (query inclusa) e o corpo. `s.chamadas.<rota>`: os corpos que chegaram. Cada rota tem um gancho `s.<rota>(r, corpo)` que,
// se posto, responde no lugar do padrão; `s.status` / `s.statusErro` fazem o mesmo com o GET do status. `s.sse()` solta um aviso do SSE.
// Sessões: `s.sessoes` (a lista), `s.sessGets` (os GET), `s.dels` (todo DELETE: método, URL completa, CSRF), `s.sessHold` (promise que segura o GET),
// Notificações: `s.notif` (o estado), `s.nGets`, `s.nPatches` (método, URL, CSRF e corpo de cada PATCH), `s.nHold` (segura o GET), ganchos `s.nGetErro(r)` / `s.nPatch(r, corpo)`.
// ganchos `s.sessGetErro(r)` / `s.sessDel(r, jti | null)` (jti null = "as outras") e `s.atual` (o jti da sessão que faz o pedido).
export function servidor({ mfa = mfaOff(), temSenha = true, uid = 7, semUid = false, meRuim = false, sessoes = listaPadrao(), notif = notifPadrao() } = {}) {
  const s = { notif, nGets: 0, nPatches: [], nHold: null, nGetErro: null, nPatch: null, mfa, temSenha, meRuim, sessoes, atual: ATUAL, sessGets: 0, dels: [], sessHold: null, sessGetErro: null, sessDel: null, reqs: [], refreshes: 0, statusHits: 0, apiMe: null, statusErro: null,
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
    await ctx.route((u) => u.pathname === "/settings/7/sessions", async (r) => {
      const q = r.request();
      if (q.method() === "GET") {
        s.sessGets++;
        if (s.sessGetErro) return s.sessGetErro(r);
        const foto = structuredClone({ ok: true, sessions: s.sessoes, current_jti: s.atual }); // a foto de QUANDO o pedido chegou
        if (s.sessHold) await s.sessHold;
        return ok(r, foto);
      }
      s.dels.push({ metodo: q.method(), url: q.url(), csrf: q.headers()["x-csrf-token"], corpo: q.postData() });
      if (s.sessDel) return s.sessDel(r, null);
      const fora = s.sessoes.filter((x) => x.jti !== s.atual).length;
      s.sessoes = s.sessoes.filter((x) => x.jti === s.atual);
      return ok(r, { ok: true, revoked: fora });
    });
    await ctx.route((u) => u.pathname.startsWith("/settings/7/sessions/"), async (r) => {
      const q = r.request(), jti = decodeURIComponent(new URL(q.url()).pathname.slice("/settings/7/sessions/".length));
      s.dels.push({ metodo: q.method(), url: q.url(), csrf: q.headers()["x-csrf-token"], corpo: q.postData(), jti });
      if (s.sessDel) return s.sessDel(r, jti);
      if (jti === s.atual) return JSON_ERRO(400, "Use o botão 'Sair' para encerrar a sessão atual.")(r);
      if (!s.sessoes.some((x) => x.jti === jti)) return JSON_ERRO(404, "Sessão não encontrada ou já encerrada.")(r);
      s.sessoes = s.sessoes.filter((x) => x.jti !== jti);
      return ok(r, { ok: true });
    });
    await ctx.route((u) => u.pathname === "/settings/7/notifications", async (r) => {
      const q = r.request();
      if (q.method() === "GET") {
        s.nGets++;
        if (s.nGetErro) return s.nGetErro(r);
        const foto = structuredClone(s.notif); // a foto de QUANDO o pedido chegou
        if (s.nHold) await s.nHold;
        return ok(r, foto);
      }
      const corpo = q.postDataJSON();
      s.nPatches.push({ metodo: q.method(), url: new URL(q.url()).pathname, csrf: q.headers()["x-csrf-token"], corpo });
      if (s.nPatch) return s.nPatch(r, corpo);
      return r.fulfill(aplicaNotif(s.notif, corpo));
    });
    await ctx.route("**/api/v2/eventos", async (r) => {
      await segura;
      r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 600000\ndata: {"recurso":"tudo"}\n\n' }).catch(() => {});
    });
  };
  return s;
}

// Encurta o setTimeout de 30000 ms (e só ele) para `ms` e registra o que o app pediu em `window.__ms`.
export const encurtaPrazo = (page, ms = 400) => page.evaluate((m) => { const st = window.setTimeout; window.__ms = []; window.setTimeout = (f, d, ...a) => { if (d === 30000) { window.__ms.push(d); d = m; } return st(f, d, ...a); }; }, ms);

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
    notif: page.locator(".conta-notif"),
    sw: (nome) => page.getByRole("switch", { name: nome }),
    sess: page.locator(".conta-sessoes"),
    avisoSess: page.locator(".conta-sessoes > p[role=status]"),
    alertaSess: page.locator(".conta-sessoes [role=alert]"),
    // uma entrada por linha da lista de dispositivos: o que se vê, o ícone e se há botão
    linhas: () => page.locator(".conta-sessoes li").evaluateAll((ls) => ls.map((l) => ({ nome: l.querySelector("b").textContent, selo: !!l.querySelector(".selo"), meta: l.querySelector(".sess-meta").textContent, icone: l.querySelector("i").className, botao: !!l.querySelector("button") }))),
    fechado: () => page.locator("dialog[open]").waitFor({ state: "detached" }),
    foco: () => page.evaluate(() => document.activeElement?.name || document.activeElement?.textContent?.trim()),
    codigos: () => page.locator(".mfa-codigos code").allTextContents(),
    encurtaPrazo: (ms) => encurtaPrazo(page, ms),
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
