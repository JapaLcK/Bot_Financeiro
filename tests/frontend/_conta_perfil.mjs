/**
 * O que os testes do Perfil da Conta (dashboard_v2_conta_perfil*.test.mjs) compartilham: o servidor de mentira e o
 * `abrirPerfil`. Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não o roda.
 */
import { abrir } from "./_guia.mjs";

export const SEC = () => ({ ok: true, user_id: 7, email: "ana.teste@exemplo.com", display_name: "Ana Teste", phone: "+5511987654321", created_at: "2025-10-01T12:00:00.123456+00:00" });
export const portao = () => { let solta; const p = new Promise((ok) => { solta = ok; }); return [p, solta]; };
export const JSON_ERRO = (status, detail, headers) => (r) => r.fulfill({ status, json: { detail }, headers });
// O link do vínculo, como o servidor o manda (core/dashboard_links.py): https.
export const WA_OK = "https://wa.me/5511999999999";
export const SENHA_VAZIA = "Confirme sua senha atual para trocar o e-mail.";
export const SESSAO = { "WWW-Authenticate": 'Bearer realm="pigbank", error="invalid_token"' };

// O servidor de mentira. `s.patch(r, corpo)` responde o PATCH (padrão: aplica e devolve o estado, como o servidor); `s.hold`
// segura os GET de segurança enquanto estiver posto (cada um responde com a foto de quando chegou); `s.sse()` solta um aviso do SSE.
// `s.reqs`: TODA requisição da página, com a URL completa (query inclusa) e o corpo; `uid` é o `user_id` do dashboard-profile.
export function servidor({ sec = SEC(), linked = false, me = { has_password: true }, link, uid = 7, semUid = false } = {}) {
  const s = { sec, reqs: [], patches: [], gets: [], meHits: 0, refreshes: 0, posts: [], hold: null, patch: null, me, link, linked, getErro: null };
  const [segura, solta] = portao();
  s.sse = solta;
  s.antes = async (ctx) => {
    ctx.on("request", (q) => s.reqs.push({ url: q.url(), metodo: q.method(), corpo: q.postData() ?? "" }));
    await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: s.sec.display_name, email: s.sec.email, ...(semUid ? {} : { user_id: uid }), whatsapp_linked: s.linked } }));
    await ctx.route((u) => u.pathname === "/settings/7/security", async (r) => {
      s.gets.push(new URL(r.request().url()).pathname);
      if (s.getErro) return s.getErro(r);
      const foto = structuredClone(s.sec); // a foto de QUANDO o pedido chegou
      if (s.hold) await s.hold;
      return r.fulfill({ json: foto });
    });
    await ctx.route((u) => u.pathname === "/settings/7/security/contact", async (r) => {
      const req = r.request(), corpo = req.postDataJSON();
      s.patches.push({ metodo: req.method(), url: new URL(req.url()).pathname, csrf: req.headers()["x-csrf-token"], corpo });
      if (s.patch) return s.patch(r, corpo);
      if ("display_name" in corpo) s.sec.display_name = corpo.display_name.trim() || null;
      if ("email" in corpo) s.sec.email = corpo.email.trim().toLowerCase();
      if ("phone" in corpo) s.sec.phone = "+55" + corpo.phone.replace(/\D/g, "");
      return r.fulfill({ json: s.sec });
    });
    await ctx.route((u) => u.pathname === "/auth/me", (r) => { s.meHits++; return s.me === 500 ? r.fulfill({ status: 500, json: { detail: "x" } }) : r.fulfill({ json: s.me }); });
    await ctx.route((u) => u.pathname === "/auth/refresh", (r) => { s.refreshes++; return r.fulfill({ status: 401, json: { detail: "expirou" } }); });
    await ctx.route((u) => u.pathname === "/auth/link-code", async (r) => {
      s.posts.push({ metodo: r.request().method(), csrf: r.request().headers()["x-csrf-token"] });
      await new Promise((ok) => setTimeout(ok, 300));
      return s.link ? s.link(r) : r.fulfill({ json: { whatsapp_link: WA_OK } });
    });
    await ctx.route(`${WA_OK}**`, (r) => r.fulfill({ body: "ok", contentType: "text/html" })); // https: o painel só abre link https/whatsapp
    await ctx.route("**/api/v2/eventos", async (r) => {
      await segura;
      r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: 'retry: 600000\ndata: {"recurso":"tudo"}\n\n' }).catch(() => {});
    });
  };
  return s;
}

export async function abrirPerfil({ width = 1440, height = 900, celular = false, semDialog = false, ...opts } = {}) {
  const s = servidor(opts);
  const antes = async (ctx) => {
    if (celular) await ctx.addInitScript(() => Object.defineProperty(navigator, "userAgent", { get: () => "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148" }));
    await s.antes(ctx);
  };
  const p = await abrir({ guia: "concluido", width, height, rota: "/conta/perfil", antes, semDialog });
  await p.page.locator(".conta-perfil dl, .conta-perfil [role=alert]").first().waitFor({ timeout: 12000 });
  const { page } = p;
  page.setDefaultTimeout(15000); // regressão reprova em segundos, não nos 30 s do padrão
  return {
    ...p, s,
    dialogo: page.locator("dialog[open]"),
    lapis: (nome) => page.getByRole("button", { name: `Alterar ${nome}` }),
    campo: page.locator("dialog[open] input[name=valor]"),
    senha: page.locator("dialog[open] input[name=senha]"),
    salvar: page.locator("dialog[open]").getByRole("button", { name: /^(Salvar|Salvando…)$/ }),
    alerta: page.locator("dialog[open] [role=alert]"),
    aviso: page.locator(".conta-perfil [role=status]"),
    valores: () => page.locator(".conta-perfil dl > div").evaluateAll((ls) => ls.map((l) => l.querySelector("dd").innerText.replace(/\s+/g, " ").trim())),
    fechado: () => page.locator("dialog[open]").waitFor({ state: "detached" }),
    foco: () => page.evaluate(() => document.activeElement?.name || document.activeElement?.getAttribute("aria-label")),
  };
}

// Espera `cond()` até ~5 s e REPROVA com a causa, em vez de travar o teste inteiro num laço sem teto.
export async function ate(page, cond, quando) {
  for (let t = 0; t < 100; t++) { if (cond()) return; await page.waitForTimeout(50); }
  throw new Error(`nunca aconteceu: ${quando}`);
}

// As requisições onde o `segredo` aparece, fora do corpo do PATCH de contato (o único lugar onde ele PODE ir): na URL,
// inclusive na query, ou no corpo de qualquer outro pedido. Vazio = não vazou.
export const vazou = (s, segredo) => s.reqs
  .filter((q) => q.url.includes(segredo) || q.url.includes(encodeURIComponent(segredo))
    || (q.corpo.includes(segredo) && !(q.metodo === "PATCH" && new URL(q.url).pathname === "/settings/7/security/contact")))
  .map((q) => `${q.metodo} ${q.url}`);
