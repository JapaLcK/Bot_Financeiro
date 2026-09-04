/**
 * Drill-down do painel de admin: a linha da trava de trial e o botão "Liberar
 * novo trial".
 *
 * Os dois defeitos que isto pega, ambos no caso REAL do dono (`plan_trials` é
 * keyed por telefone, PK = phone_hash, e a FK pro usuário é ON DELETE SET NULL
 * — db/schema.py):
 *
 *   1. TRAVA HERDADA: conta antiga apagada, número recadastrado. A linha da
 *      trava sobrevive com `user_id` NULO. A versão anterior exigia
 *      `trial_lock_user_id != null` para explicar a trava, então esse caso —
 *      o mais comum — renderizava uma data pelada, sem uma palavra de porquê.
 *      É exatamente a confusão que a linha foi criada para resolver.
 *
 *   2. ÂNCORA SEM TRAVA: a troca de telefone (frontend/routes/settings.py)
 *      reescreve `phone_hash` e deixa `trial_started_at` para trás. A conta
 *      fica sem trava e com âncora velha — o estado que faz o próximo trial
 *      nascer VENCIDO. A versão anterior desabilitava o botão sempre que não
 *      havia trava, recusando o único remédio.
 *
 * Os dois controles do CLAUDE.md §3, para o GRUPO:
 *   · negativo — desligar qualquer um dos dois consertos deixa pelo menos um
 *     caso vermelho: sem o ramo `trial_lock_user_id == null` a herdada perde o
 *     texto; sem o `&& !p.trial_started_at` a âncora-sem-trava volta a ficar
 *     desabilitada. As duas rodadas estão no relato do PR.
 *   · positivo — `sem_nada` e `sem_telefone` continuam DESABILITADOS. Sem eles
 *     o grupo passaria numa versão que habilita o botão sempre, que é pior que
 *     o bug: apaga âncora e downsell de conta que não tinha nada a liberar.
 *
 * A medição é de DOM renderizado pelo caminho real (`openUserDetail` → fetch
 * → `renderUserDetail`), não de texto do arquivo: ler o .html com regex mede o
 * arquivo, não o comportamento.
 *
 * Rodar:  npm run test:frontend
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const UID = 4242;

/** Perfil mínimo que o `renderUserDetail` consome, com os campos do caso. */
function perfil(extra) {
  return {
    user_id: UID, email: "dono@test.local", display_name: "Dono",
    phone_e164: "+5511999990000", plan: "free", plan_expires_at: null,
    signup_source: "web", last_payment_status: null, account_status: "free",
    trial_started_at: null, trial_lock_started_at: null, trial_lock_user_id: null,
    plan_selected_at: null, created_at: "2026-01-01T00:00:00+00:00",
    last_activity_at: null, phone_status: "confirmed", has_whatsapp_identity: true,
    ai_messages_this_month: 0, stripe_customer_id: null, deletion_status: null,
    engagement_opt_out: false, tip_email_opt_out: false,
    insight_email_opt_out: false, whatsapp_updates_opt_out: false,
    ...extra,
  };
}

/**
 * Abre a página, força o drill-down do UID com o perfil dado e devolve o que a
 * tela mostra: o texto da linha da trava, se o botão está desabilitado, e a
 * dica ao lado dele.
 */
async function renderizar(extra) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  // Catch-all ANTES do específico: o Playwright casa as rotas da mais recente
  // para a mais antiga. Tudo 200 de propósito — um único 401 dispara
  // `window.location.href = '/admin/login'` e a página some debaixo do teste.
  await page.route("**/admin/api/**", (route) => route.fulfill({
    contentType: "application/json", body: "{}",
  }));
  await page.route(`**/admin/api/users/${UID}`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      profile: perfil(extra),
      usage: { tx_total: 0, tx_30d: 0, last_tx_at: null, pockets_count: 0,
               investments_count: 0, cards_count: 0 },
      recent_events: [], recent_logins: [],
    }),
  }));

  await page.goto(`${ORIGIN}/admin-dashboard.html`, { waitUntil: "domcontentloaded" });
  await page.evaluate((uid) => window.openUserDetail(uid), UID);
  // `attached`, não `visible`: o drill-down vive dentro do modal de usuários,
  // que só abre por clique. O que se mede aqui é o markup que o
  // `renderUserDetail` produz, e ele já está no DOM.
  await page.waitForSelector("#trial-reset", { state: "attached" });

  const visto = await page.evaluate(() => {
    const linha = [...document.querySelectorAll("#user-detail .detail-item")]
      .find((el) => el.querySelector(".k")?.textContent.includes("trava do telefone"));
    const btn = document.getElementById("trial-reset");
    return {
      trava: linha?.querySelector(".v")?.textContent.trim() ?? null,
      desabilitado: btn.disabled,
      dica: btn.closest(".plan-editor").querySelector(".plan-hint").textContent.trim(),
    };
  });
  await page.close();
  return visto;
}

const ONTEM = "2026-08-01T12:00:00+00:00";

// ── (4) os TRÊS casos da trava, não dois ───────────────────────────────────

test("trava herdada (user_id nulo) é EXPLICADA, não uma data pelada", async () => {
  const v = await renderizar({ trial_lock_started_at: ONTEM, trial_lock_user_id: null });
  assert.match(v.trava, /trava herdada/i, `linha da trava: ${v.trava}`);
  assert.match(v.trava, /apagada/i, `linha da trava: ${v.trava}`);
  // E a data continua lá — a explicação não pode ter comido o dado.
  assert.match(v.trava, /\d/, `linha da trava: ${v.trava}`);
  // O remédio tem de estar disponível: é o caso canônico do recurso.
  assert.equal(v.desabilitado, false);
});

test("trava de OUTRA conta identificável cita o user dela", async () => {
  const v = await renderizar({ trial_lock_started_at: ONTEM, trial_lock_user_id: 777 });
  assert.match(v.trava, /OUTRA conta/, `linha da trava: ${v.trava}`);
  assert.match(v.trava, /777/, `linha da trava: ${v.trava}`);
  assert.doesNotMatch(v.trava, /herdada/i, `linha da trava: ${v.trava}`);
  assert.equal(v.desabilitado, false);
});

test("trava da PRÓPRIA conta é só a data, sem ressalva nenhuma", async () => {
  const v = await renderizar({ trial_lock_started_at: ONTEM, trial_lock_user_id: UID,
                               trial_started_at: ONTEM });
  assert.doesNotMatch(v.trava, /herdada|OUTRA conta/i, `linha da trava: ${v.trava}`);
  assert.equal(v.desabilitado, false);
});

// ── (1) âncora sem trava: o botão existe para consertar ISTO ───────────────

test("âncora sem trava (pós-troca de telefone) NÃO bloqueia o botão", async () => {
  const v = await renderizar({ trial_started_at: ONTEM, trial_lock_started_at: null });
  assert.equal(v.desabilitado, false,
    "o estado que faz o trial nascer vencido é o que o botão conserta");
  assert.match(v.trava, /sem trava/i, `linha da trava: ${v.trava}`);
  // A dica tem de dizer QUAL dos dois casos está tratando: aqui não há trava a
  // apagar, e prometer que apaga uma seria mentir na confirmação.
  assert.match(v.dica, /âncora/i, `dica: ${v.dica}`);
  assert.doesNotMatch(v.dica, /Apaga a trava do TELEFONE/, `dica: ${v.dica}`);
});

test("com trava, a dica é a da trava — não a da âncora", async () => {
  const v = await renderizar({ trial_lock_started_at: ONTEM, trial_lock_user_id: UID });
  assert.match(v.dica, /Apaga a trava do TELEFONE/, `dica: ${v.dica}`);
});

// ── controles POSITIVOS: o botão continua recusando o que não tem remédio ──

test("sem trava e sem âncora: botão DESABILITADO", async () => {
  const v = await renderizar({});
  assert.equal(v.desabilitado, true);
  assert.match(v.dica, /nada a liberar/i, `dica: ${v.dica}`);
});

test("conta sem telefone: botão DESABILITADO mesmo com âncora", async () => {
  const v = await renderizar({ phone_e164: null, trial_started_at: ONTEM });
  assert.equal(v.desabilitado, true);
  assert.match(v.dica, /sem telefone vinculado/i, `dica: ${v.dica}`);
});
