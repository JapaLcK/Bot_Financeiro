/**
 * "Recomeçar do zero" × múltiplas abas (Codex PR #217, 7º achado).
 *
 * sessionStorage é POR ABA: a limpeza de pb_snap_ e pb_home_ no sucesso do
 * reset só alcança a aba que disparou. A aba B guarda o snapshot pré-reset e,
 * num reload pós-onboarding, o restaurava (flash de dados apagados). O
 * conserto segue o padrão multi-aba que o repo já usa (finbot_logout_at em
 * localStorage): o settings grava `finbot_reset_at` e os restores da home e
 * do dashboard descartam snapshot que não for comprovadamente POSTERIOR.
 *
 * Os testes chamam as funções REAIS das páginas (globais de script clássico)
 * por page.evaluate — sem corrida com o boot, sem reimplementar o predicado.
 *
 * CONTROLE NEGATIVO do grupo (§3): com o predicado removido (código anterior),
 * os testes de descarte ficam vermelhos — verificado via git stash na sessão.
 * POSITIVO: os casos "sem marker" e "snapshot pós-reset" provam que o restore
 * legítimo continua funcionando.
 *
 * Rodar:  npm run test:frontend
 *         (um arquivo só: node --test tests/frontend/reset_cache_multiaba.test.mjs)
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FRONTEND = join(REPO, "frontend");
// porta própria: o node --test roda os arquivos em paralelo (8899/8901/8903/
// 8905/8907/8909 já têm dono).
const PORT = Number(process.env.PB_RESET_TEST_PORT || 8911);
const ORIGIN = `http://127.0.0.1:${PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function startServer() {
  const proc = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1",
                                 "--directory", FRONTEND], { stdio: "ignore" });
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    if (proc.exitCode !== null) throw new Error(`http.server morreu (porta ${PORT} ocupada?)`);
    try { if ((await fetch(`${ORIGIN}/home.html`)).ok) return proc; } catch { /* subindo */ }
  }
  proc.kill();
  throw new Error(`http.server não subiu em ${ORIGIN}`);
}

async function waitFor(cond, what, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await cond()) return;
    await sleep(50);
  }
  throw new Error(`timeout esperando: ${what}`);
}

/** page.evaluate que devolve false quando a NAVEGAÇÃO destrói o contexto no
 *  meio — reload/redirect em curso é o próprio sucesso que se está esperando,
 *  e o waitFor não engole rejeição (a exceção subia e matava o teste no exato
 *  instante do sucesso: flake do PR #226, run 33644738135). Só o erro de
 *  contexto morto vira false: qualquer outro SOBE, senão bug real viraria
 *  timeout mudo (§3) — é o que o teste "waitFor:" no fim do arquivo prova. */
const avaliaTolerandoNavegacao = (page, fn) => page.evaluate(fn).catch((e) => {
  if (/Execution context (was destroyed|is not available)|Cannot find context/.test(String(e)))
    return false;
  throw e;
});

let server, browser;
before(async () => { server = await startServer(); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

// Liberadores de rotas presas (o teste do t0 registra o dele aqui): o
// `fechar()` os solta ANTES do unroute/close, então um assert que estoure no
// meio nunca deixa handler estacionado com request pendente.
const liberadores = [];

/** Ação de rota que NUNCA rejeita: reload/close no meio do handler cancela a
 *  request e o fulfill/continue/abort estoura — no runner (mais lento) isso
 *  apanhava fetches em voo e virava "atividade assíncrona depois do teste"
 *  (o assert(!this.paused) do CI). Engolir aqui é seguro: request cancelada
 *  não tem mais consumidor. */
const acaoSegura = async (fn) => { try { await fn(); } catch { /* navegou/fechou */ } };

/** Página com as rotas de API neutralizadas (asset vai pro http.server). */
async function newPage() {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return acaoSegura(() => route.abort());     // CDN fora
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return acaoSegura(() => route.continue());
    return acaoSegura(() => route.fulfill(json({})));
  });
  await page.route("**/auth/validate",
                   (route) => acaoSegura(() => route.fulfill(json({ user_id: 1 }))));
  page.__ctx = ctx;
  return page;
}

/** Teardown blindado, na ordem que não deixa request pausada: solta os
 *  liberadores pendentes → desregistra as rotas sem esperar handlers
 *  (`ignoreErrors`) → fecha o contexto. Cada passo tolera o anterior já ter
 *  derrubado a página. */
async function fechar(page) {
  while (liberadores.length) { try { liberadores.pop()(); } catch { /* já liberado */ } }
  try { await page.unrouteAll({ behavior: "ignoreErrors" }); } catch { /* página já fechada */ }
  try { await page.__ctx.close(); } catch { /* contexto já fechado */ }
}

/** Boota a home REAL (contrato pb-nav: restoreHomeCache é closure do init —
 *  inalcançável por evaluate; o boot o chama logo após o /auth/validate
 *  stubado). Observável: o destino da chave pb_home_1, que só o ramo de
 *  descarte remove. Devolve {raw, seed} pós-boot. */
async function bootHomeComSnapshot(page, { savedAt, resetAt }) {
  await page.addInitScript(([savedAt, resetAt]) => {
    // Espião: só o ramo de descarte do restore remove pb_home_1 (o flow
    // fresco pós-boot SOBRESCREVE a chave via setItem, nunca a remove) —
    // a remoção é o observável que discrimina os dois destinos.
    window.__removidas = [];
    const orig = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function (k) { window.__removidas.push(k); return orig.call(this, k); };

    if (resetAt) localStorage.setItem("finbot_reset_at", String(resetAt));
    else localStorage.removeItem("finbot_reset_at");
    const entrada = { userId: 1, snapshot: { total: 1 }, history: [], email: "", displayName: "" };
    if (savedAt) entrada.savedAt = savedAt;
    sessionStorage.setItem("pb_home_1", JSON.stringify(entrada));
  }, [savedAt, resetAt]);
  await page.goto(`${ORIGIN}/home.html`);
  return page;
}

const chaveFoiRemovida = (page) =>
  page.evaluate(() => (window.__removidas || []).includes("pb_home_1"));

test("home: snapshot anterior ao finbot_reset_at é descartado no restore", async () => {
  const page = await newPage();
  try {
    await bootHomeComSnapshot(page, { savedAt: 1000, resetAt: 2000 });
    await waitFor(() => chaveFoiRemovida(page), "descarte do pb_home_1 pré-reset");
  } finally { await fechar(page); }
});

test("home: snapshot gravado DEPOIS do reset restaura normal", async () => {
  const page = await newPage();
  try {
    await bootHomeComSnapshot(page, { savedAt: 3000, resetAt: 2000 });
    await sleep(1500);   // janela em que o teste acima prova que o restore roda
    assert.equal(await chaveFoiRemovida(page), false,
                 "snapshot pós-reset é legítimo — não pode ser descartado");
  } finally { await fechar(page); }
});

test("home: sem marker (nunca resetou), o restore segue como antes", async () => {
  const page = await newPage();
  try {
    await bootHomeComSnapshot(page, { savedAt: 1000, resetAt: 0 });
    await sleep(1500);
    assert.equal(await chaveFoiRemovida(page), false,
                 "sem finbot_reset_at nada muda no restore");
  } finally { await fechar(page); }
});

test("home: resposta em voo durante o reset é carimbada com o t0 do request", async () => {
  // Codex PR #217 (P2): o carimbo era do RECEBIMENTO — uma resposta gerada
  // antes do reset mas tratada depois ganhava savedAt > marker e o predicado
  // a tratava como pós-reset. Com o t0 (início do request), ela fica < marker
  // e o próximo restore a descarta. CONTROLE NEGATIVO: com o carimbo no
  // persist (código anterior), savedAt > marker → vermelho.
  const page = await newPage();
  // fora do try: se um assert/timeout estourar antes do liberar(), o handler
  // da rota ficaria estacionado no `await preso` com request pendente e o
  // close do contexto viraria "atividade assíncrona depois do teste" no CI.
  let liberar = () => {};
  try {
    const preso = new Promise((r) => { liberar = r; });
    liberadores.push(() => liberar());   // o fechar() solta mesmo se um assert estourar antes
    await page.route("**/data/**", async (route) => {
      await preso;
      try { await route.fulfill(json({})); } catch { /* contexto já fechado */ }
    });

    const pedido = page.waitForRequest("**/data/**");
    await page.goto(`${ORIGIN}/home.html`);
    await pedido;                                    // request em voo
    const marker = await page.evaluate(() => {       // o reset completa agora
      const t = Date.now();
      localStorage.setItem("finbot_reset_at", String(t));
      return t;
    });
    await sleep(50);                                 // recebimento vem DEPOIS do marker
    liberar();

    await waitFor(() => page.evaluate(() => sessionStorage.getItem("pb_home_1") !== null),
                  "persist do snapshot da home");
    const savedAt = await page.evaluate(
      () => JSON.parse(sessionStorage.getItem("pb_home_1")).savedAt);
    assert.ok(Number(savedAt) < marker,
              `savedAt (${savedAt}) tinha que ser o t0 do request, anterior ao marker (${marker})`);
  } finally { await fechar(page); }
});

test("dashboard: snapshot anterior ao finbot_reset_at é descartado no restore", async () => {
  const page = await newPage();
  try {
    await page.goto(`${ORIGIN}/dashboard.html`);
    await waitFor(() => page.evaluate(() => typeof restoreSnapshotFromSession === "function"),
                  "funções do dashboard carregadas");
    const resultado = await page.evaluate(() => {
      window.USER_ID = 1;
      localStorage.setItem("finbot_reset_at", "2000");
      const key = `pb_snap_1_${viewYear}_${String(viewMonth).padStart(2, "0")}`;
      sessionStorage.setItem(key, JSON.stringify(
        { year: viewYear, month: viewMonth, pb_saved_at: 1000 }));
      const restaurou = restoreSnapshotFromSession();
      return { restaurou, ficou: sessionStorage.getItem(key) !== null };
    });
    assert.equal(resultado.restaurou, false, "restore de snapshot pré-reset tinha que devolver false");
    assert.equal(resultado.ficou, false, "a chave pré-reset tinha que ser removida");
  } finally { await fechar(page); }
});

async function abaRecarregaNoEvento(page, url, prontoQuando) {
  await page.goto(url);
  await waitFor(() => page.evaluate(prontoQuando), `listener pronto em ${url}`);
  // negativo primeiro: chave alheia NÃO pode recarregar (comparação estrita)
  await page.evaluate(() => {
    window.__viva = 1;
    window.dispatchEvent(new StorageEvent("storage", { key: "pbNewsTab", newValue: "1" }));
  });
  await sleep(400);
  assert.equal(await page.evaluate(() => window.__viva), 1,
               "storage event de OUTRA chave recarregou a aba");
  // o evento do reset recarrega (a aba renderizada tem saldos apagados)
  await page.evaluate(() => {
    window.dispatchEvent(new StorageEvent("storage", { key: "finbot_reset_at", newValue: "123" }));
  });
  await waitFor(() => avaliaTolerandoNavegacao(page, () => window.__viva === undefined),
                "reload da aba após o evento do reset");
}

test("dashboard: storage event do finbot_reset_at recarrega a aba aberta", async () => {
  const page = await newPage();
  try {
    await abaRecarregaNoEvento(page, `${ORIGIN}/dashboard.html`,
      () => typeof restoreSnapshotFromSession === "function");
  } finally { await fechar(page); }
});

test("home: storage event do finbot_reset_at recarrega a aba aberta", async () => {
  const page = await newPage();
  try {
    await abaRecarregaNoEvento(page, `${ORIGIN}/home.html`,
      () => window.__pbResetAtListener === true);
  } finally { await fechar(page); }
});

test("settings: sucesso do reset grava o marker e limpa os snapshots da aba", async () => {
  const page = await newPage();
  try {
    await page.goto(`${ORIGIN}/settings.html`);
    await waitFor(() => page.evaluate(() => typeof requestAccountReset === "function"),
                  "funções do settings carregadas");
    await page.evaluate(() => {
      localStorage.removeItem("finbot_reset_at");
      sessionStorage.setItem("pb_snap_1_2026_01", "{}");
      sessionStorage.setItem("pb_home_1", "{}");
      window.confirmModal = async () => true;   // pula o modal destrutivo
      document.getElementById("reset-password").value = "senha";
      requestAccountReset();                    // POST stubado (200) → marker → /onboarding
    });
    await waitFor(() => avaliaTolerandoNavegacao(page, () => location.pathname === "/onboarding"),
                  "redirect pro /onboarding");
    const estado = await page.evaluate(() => ({
      marker: localStorage.getItem("finbot_reset_at"),
      snap: sessionStorage.getItem("pb_snap_1_2026_01"),
      home: sessionStorage.getItem("pb_home_1"),
    }));
    assert.ok(Number(estado.marker) > 0, "o reset tinha que gravar finbot_reset_at");
    assert.equal(estado.snap, null, "pb_snap_* da própria aba tinha que sumir");
    assert.equal(estado.home, null, "pb_home_* da própria aba tinha que sumir");
  } finally { await fechar(page); }
});

test("waitFor: erro que não é navegação sobe; condição impossível estoura com a mensagem", async () => {
  // Controle do conserto acima (§3): tolerar a destruição do contexto não pode
  // virar "engole tudo". NEGATIVO — com `catch { return false }` no lugar do
  // filtro, a 1ª rejeição vira timeout genérico e o /bug real/ fica vermelho.
  // about:blank de propósito: o helper e o waitFor não dependem de página
  // nenhuma, e bootar a home deixaria fetch em voo quando o fechar() derruba o
  // contexto — a "atividade assíncrona depois do teste" que o arquivo já
  // combate (acaoSegura/fechar). Sem request, sem corrida.
  const page = await newPage();
  try {
    await assert.rejects(
      () => waitFor(() => avaliaTolerandoNavegacao(page, () => { throw new TypeError("bug real"); }),
                    "nunca acontece", 300),
      /bug real/, "erro real virou timeout mudo — o catch engoliu demais");
    await assert.rejects(
      () => waitFor(() => avaliaTolerandoNavegacao(page, () => false), "condição impossível", 300),
      /timeout esperando: condição impossível/,
      "a mensagem do timeout tem que dizer o que se esperava");
  } finally { await fechar(page); }
});
