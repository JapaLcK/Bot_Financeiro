/* dashboard.js — extraído de dashboard.html (refactor Fase 1: CSP script-src).
   Bloco principal do dashboard. Servido em /dashboard.js. */
/* ═══════════════════════════════════════════════════════════════════════
   Tradução de mensagens nativas de validação HTML5 (sem inline)
═══════════════════════════════════════════════════════════════════════ */
document.addEventListener("invalid", (e) => {
  const el = e.target;
  if (!(el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement)) return;
  const v = el.validity;
  let msg = "";
  if (v.valueMissing) msg = "Preencha este campo.";
  else if (v.typeMismatch && el.type === "email") msg = "Digite um email válido.";
  else if (v.typeMismatch && el.type === "url") msg = "Digite uma URL válida.";
  else if (v.typeMismatch) msg = "Formato inválido.";
  else if (v.rangeUnderflow) msg = `Valor mínimo: ${el.min}.`;
  else if (v.rangeOverflow) msg = `Valor máximo: ${el.max}.`;
  else if (v.tooShort) msg = `Mínimo de ${el.minLength} caracteres.`;
  else if (v.tooLong) msg = `Máximo de ${el.maxLength} caracteres.`;
  else if (v.patternMismatch) msg = el.title || "Formato inválido.";
  else if (v.stepMismatch) msg = "Use um valor com a precisão correta.";
  else if (v.badInput) msg = "Entrada inválida.";
  el.setCustomValidity(msg);
}, true);
document.addEventListener("input", (e) => {
  if (e.target && typeof e.target.setCustomValidity === "function") e.target.setCustomValidity("");
}, true);
document.addEventListener("change", (e) => {
  if (e.target && typeof e.target.setCustomValidity === "function") e.target.setCustomValidity("");
}, true);

/* ═══════════════════════════════════════════════════════════════════════
   CONFIG
═══════════════════════════════════════════════════════════════════════ */
const params    = new URLSearchParams(window.location.search);
const BASE_HTTP = window.location.origin;
const BASE_WS   = BASE_HTTP.replace("http://", "ws://").replace("https://", "wss://");
const API       = BASE_HTTP;

// USER_ID e WS_URL são resolvidos após validação da sessão (ver seção INIT)
let USER_ID = 0;
let WS_URL  = "";
let USER_EMAIL = "";
let USER_PLAN = "";
// Gates de feature resolvidos pelo backend (/auth/dashboard-profile). {} = tudo
// bloqueado até o perfil chegar (default conservador). Ver applyProGates.
let USER_GATES = {};

/* ─── Loader de scripts sob demanda ──────────────────────────────────────
   Carrega uma lib de terceiros só quando ela é realmente necessária, em vez
   de baixá-la em todo boot. Deduplica: várias chamadas para a mesma URL
   compartilham a mesma Promise. */
const _scriptLoaders = {};
function _loadScriptOnce(src) {
  if (_scriptLoaders[src]) return _scriptLoaders[src];
  _scriptLoaders[src] = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { delete _scriptLoaders[src]; reject(new Error(`Falha ao carregar ${src}`)); };
    document.head.appendChild(s);
  });
  return _scriptLoaders[src];
}

/* Sortable só é usado no drag-to-reorder dos cartões (ponteiro fino). Carrega
   sob demanda pra tirar ~50KB de todo boot de quem nunca reordena cartão. */
function ensureSortable() {
  if (typeof window.Sortable !== "undefined") return Promise.resolve();
  return _loadScriptOnce("https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js")
    .catch(() => {});
}

function getCookie(name) {
  return document.cookie.split("; ").find(row => row.startsWith(`${name}=`))?.split("=")[1] || "";
}

function csrfHeaders(extra = {}) {
  const csrf = decodeURIComponent(getCookie("csrf_token") || "");
  return csrf ? { ...extra, "X-CSRF-Token": csrf } : { ...extra };
}

async function readResponsePayload(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function shortAccountLabel(email) {
  const value = (email || "").trim();
  if (!value) return "Minha conta";
  const [name] = value.split("@");
  return name.length > 18 ? `${name.slice(0, 16)}...` : name;
}

function userMenuLabel(displayName, email) {
  const trimmed = (displayName || "").trim();
  if (trimmed) return trimmed.length > 22 ? `${trimmed.slice(0, 20)}...` : trimmed;
  return shortAccountLabel(email);
}

function formatPlanLabel(plan) {
  const value = (plan || "free").trim();
  return value.toLowerCase() === "free" ? "Plano Free" : `Plano ${value}`;
}

function applyUserMenuState(email, plan, displayName, gates) {
  USER_EMAIL = email || "";
  USER_PLAN = plan || "free";
  if (gates && typeof gates === "object") USER_GATES = gates;
  document.getElementById("user-label").textContent = userMenuLabel(displayName, USER_EMAIL);
  document.getElementById("user-email").textContent = USER_EMAIL || "Minha conta";
  document.getElementById("user-plan").textContent = formatPlanLabel(USER_PLAN);
  // Reaplicar gates Pro sempre que o plano for atualizado (login, refresh,
  // upgrade no meio da sessao). Idempotente.
  applyProGates();
}

/* ─── Cache do chrome do header (instant paint no cold start) ─────────────
   Guarda só dados NÃO-financeiros do menu (nome/email/plano) pra pintar o
   cabeçalho e aplicar os gates de UI na hora, sem esperar o round-trip do
   /auth/dashboard-profile. Saldos e transações NUNCA entram aqui — a política
   do app é não cachear dado financeiro no dispositivo (ver service-worker.js).
   O valor é sobrescrito pelo perfil fresco ~1 RTT depois.
   O cache é ESCOPADO ao USER_ID validado: o paint otimista só acontece se o
   registro pertencer ao usuário da sessão atual. Isso evita mostrar a
   identidade de um usuário anterior quando outra conta loga no mesmo
   navegador — inclusive se o logout foi feito por Settings/Home (cujos
   handlers não conhecem esta chave) e mesmo que o fetch fresco falhe. */
const _MENU_CACHE_KEY = "pigbank_menu_v1";
function _readMenuCache() {
  try {
    const raw = localStorage.getItem(_MENU_CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}
function _writeMenuCache(userId, email, plan, displayName) {
  // NÃO cacheamos feature_gates: entitlement é estado que expira (assinatura
  // vence, freio v2 liga) e cache no localStorage viraria "liberado" preso.
  // Só o chrome não-sensível (email/plano-label/nome) é paintado do cache.
  try {
    localStorage.setItem(_MENU_CACHE_KEY, JSON.stringify({ userId, email, plan, displayName }));
  } catch {}
}
function clearMenuCache() {
  try { localStorage.removeItem(_MENU_CACHE_KEY); } catch {}
}

async function loadUserMenuState() {
  // Paint otimista: aplica o último chrome conhecido ANTES do fetch resolver,
  // mas só se o cache for do usuário já validado nesta sessão (USER_ID). Os
  // gates NÃO vêm do cache (sem 4º arg) — ficam no default {} (tudo bloqueado)
  // até o /auth/dashboard-profile fresco chegar. Fail-closed de propósito:
  // melhor um flash de cadeado pra quem paga que liberar controle pra assinatura
  // já expirada (ou pós-freio v2) enquanto o perfil real não confirma.
  const cached = _readMenuCache();
  if (cached && USER_ID && String(cached.userId) === String(USER_ID)) {
    applyUserMenuState(cached.email || "", cached.plan || "free", cached.displayName || "");
  } else if (cached) {
    // Cache de outro usuário (ou formato antigo sem userId): descarta pra não
    // vazar identidade. Será reescrito com o perfil correto abaixo.
    clearMenuCache();
  }
  // Fail-closed universal: trava os controles pagos ANTES de qualquer fetch, em
  // TODA path — inclusive sem cache / cache de outro user / storage limpo, onde
  // nada acima chama applyProGates e o HTML inicial fica destravado. USER_GATES
  // começa {} → tudo vira .pro-locked até o /auth/dashboard-profile confirmar.
  // Idempotente com o applyUserMenuState do branch de cache acima.
  applyProGates();
  try {
    const res = await fetch(`${API}/auth/dashboard-profile`, { credentials: "same-origin" });
    if (!res.ok) return;
    const data = await readResponsePayload(res);
    applyUserMenuState(data.email || "", data.plan || "free", data.display_name || "", data.feature_gates || {});
    _writeMenuCache(USER_ID, data.email || "", data.plan || "free", data.display_name || "");
  } catch {}
}

/* Fecha só o que está aberto. Os dois listeners globais de Escape que fechavam
   os modais de fatura e os de investimento chamavam os `close*` sem checar
   nada: um Esc destinado a um diálogo aberto por cima derrubava os de baixo
   junto — foi o que colidiu com a confirmação de antecipar fatura no #73, que
   precisou de captura + stopPropagation para se defender. */
function _fechaSeAberto(overlayId, close) {
  const ov = document.getElementById(overlayId);
  if (ov && ov.classList.contains("open")) close();
}

function closeUserMenu() {
  const dropdown = document.getElementById("user-dropdown");
  const button = document.getElementById("user-menu-btn");
  dropdown.classList.remove("open");
  button.setAttribute("aria-expanded", "false");
}

function toggleUserMenu(event) {
  event.stopPropagation();
  const dropdown = document.getElementById("user-dropdown");
  const button = document.getElementById("user-menu-btn");
  const isOpen = dropdown.classList.toggle("open");
  button.setAttribute("aria-expanded", String(isOpen));
}

document.addEventListener("click", (event) => {
  const menu = document.getElementById("user-menu");
  if (menu && !menu.contains(event.target)) closeUserMenu();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeUserMenu();
});

async function connectWhatsAppFromDashboard(event) {
  if (event) event.preventDefault();
  closeUserMenu();

  try {
    const res = await fetch(`${API}/auth/link-code`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders()
    });
    const data = await readResponsePayload(res);
    if (!res.ok) {
      await alertModal(data.detail || "Não foi possível gerar seu link do WhatsApp agora.", { title: "WhatsApp" });
      return;
    }
    if (!data.whatsapp_link) {
      await alertModal("WhatsApp ainda não configurado neste ambiente.", { title: "WhatsApp" });
      return;
    }
    const isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "");
    if (isMobile) {
      window.location.href = data.whatsapp_link;
    } else {
      window.open(data.whatsapp_link, "_blank", "noopener");
    }
  } catch {
    await alertModal("Erro ao conectar o WhatsApp. Tente novamente.", { title: "WhatsApp" });
  }
}

/* ═══════════════════════════════════════════════════════════════════════
   STATE
═══════════════════════════════════════════════════════════════════════ */
let ws, lastData = null;
// Timer da reconexão do WS. Guardado para o caminho de erro do paywall poder
// CANCELAR explicitamente — hoje o retry na tela de erro só morria porque
// setStatus() estourava no DOM apagado; um `?.` inocente ali criaria loop.
let wsReconnectTimer = null;
// Reconexão em duas velocidades: tentativa que ABRIU cai e volta em 3 s
// (queda transitória — retry legítimo); tentativa que NÃO chegou a abrir
// (handshake rejeitado pelo gate de plano, outage de auth) usa backoff
// dobrado até 60 s. O critério é a ÚLTIMA tentativa, não "alguma vez abriu":
// assinatura revogada no meio da sessão rejeita as reconexões de um socket
// que já tinha aberto, e com "alguma vez" o retry ficava fixo em 3 s para
// sempre (Codex-8). Depois de WS_REVALIDATE_AFTER falhas seguidas o cliente
// re-executa o gate de acesso (/auth/me) — é assim que ele DESCOBRE que
// perdeu o plano, já que o close(4402) é pré-accept e chega como 1006.
const WS_REVALIDATE_AFTER = 2;
let wsOpenedLastAttempt = false;
let wsFailStreak = 0;
let wsRetryStopped = false;
let wsRetryDelay = 3000;
let filterType   = "all";
let bgtTarget    = null;
let chartCat     = null, chartDay = null, chartHistory = null;
const prevNums   = {};

let launchesPage     = 1;
const LAUNCHES_LIMIT = 25;
let launchesLoading  = false;
let alertsDismissed  = false;
let monthRequestSeq  = 0;
let monthAbortController = null;
const monthDataCache = new Map();

/* ── Canal de fetch compartilhado (dedup + abort + geração) ────────────────
   Mesmo padrão provado do fetchMonthHttp (seq + AbortController + guarda de
   geração), empacotado pra os loaders secundários do dashboard reusarem sem
   copiar. Cada loader instancia o seu (makeFetchChannel()).

   run(fetcher, { force }) devolve uma promise que resolve pra:
     • os dados            — quando este pedido é o mais novo (geração atual);
     • undefined           — quando foi SUPERADO por um pedido mais novo ou
                             ABORTADO (neutro: não renderiza, não pinta erro);
   e REJEITA só no erro real do pedido da geração atual.

   force=true cancela o pedido anterior DE VERDADE (não só zera a ref — foi o
   que o stopgap e7badca errou, revertido em f89bcbf: zerar sem abortar deixou
   o velho terminar e sobrescrever o novo). force=false deduplica (reaproveita
   o pedido em curso), pro revalidate silencioso do stale-while-revalidate. */
function makeFetchChannel() {
  let inFlight = null, controller = null, gen = 0;
  return {
    run(fetcher, { force = false } = {}) {
      if (inFlight && !force) return inFlight;   // dedup (revalidate SWR)
      if (controller) controller.abort();         // cancela o anterior de verdade
      const myGen = ++gen;
      controller = new AbortController();
      const signal = controller.signal;
      const p = (async () => {
        try {
          const data = await fetcher(signal);
          return (myGen === gen) ? data : undefined;   // superado → neutro
        } catch (err) {
          // Superado (por abort ou por corrida) nunca vira erro de tela: quem
          // manda agora é o pedido mais novo. Só o erro real da geração atual
          // sobe pro caller (pro indicador do puxão ficar âmbar).
          if (myGen !== gen) return undefined;
          if (err && err.name === "AbortError") return undefined;
          throw err;
        } finally {
          // Só o dono atual limpa as refs — o velho abortado não pode zerar o
          // controller/inFlight do novo que acabou de assumir.
          if (myGen === gen) { inFlight = null; controller = null; }
        }
      })();
      inFlight = p;
      return p;
    },
  };
}
let filterDebounceTimer = null;

const NOW = new Date();
let viewYear = NOW.getFullYear(), viewMonth = NOW.getMonth() + 1;
let historyEarliestDate = null;
// True depois que o usuário troca de mês pelo seletor. Enquanto false, o
// snapshot do WebSocket pode corrigir viewYear/viewMonth (virada UTC×local).
let userNavigatedMonth = false;
// Mês mais novo alcançável (ano*12+mês). Nasce do relógio local e AVANÇA se o
// servidor mandar snapshot de mês mais novo — sem isso, após adotar o mês da
// virada UTC o btn-next ficava habilitado para um mês seguinte vazio.
let latestKnownMonth = NOW.getFullYear() * 12 + (NOW.getMonth() + 1);

const PT_MONTHS = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
                   "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"];
// Paleta categórica PigBank — reharmonizada e validada (CVD-safe, dark+light).
// Ordem = mecanismo de segurança CVD (pior par ΔE ~26). Slots 9-10 = overflow.
// Canvas não lê CSS var, então hex direto; catColors() escolhe pelo tema.
const PALETTE_DARK  = ["#FF2D8E","#5FA83C","#2E7FE0","#E84545","#12A892","#BE8200","#7E5FE6","#E85F2A","#22C3D6","#94A3B8"];
const PALETTE_LIGHT = ["#C7186B","#3E8E23","#1E6FD0","#D42E2E","#0A8F7A","#A66E00","#6544D8","#D24A17","#0E8D9E","#64748B"];
function catColors() {
  return document.body.classList.contains("light") ? PALETTE_LIGHT : PALETTE_DARK;
}
const CAT_CLR = PALETTE_DARK;   // legado: refs diretas caem no dark; charts usam catColors()
const PALETTE = PALETTE_DARK;
const PKT_CLR   = ["var(--purple)","var(--blue)","var(--green)"];

function getFilterText() {
  return (document.getElementById("filter-text")?.value || "").trim();
}

function monthCacheKey(year, month, page = 1, type = filterType, text = getFilterText()) {
  return [
    year,
    String(month).padStart(2, "0"),
    page,
    type || "all",
    encodeURIComponent(text || "")
  ].join("-");
}

function cacheMonthData(data) {
  if (!data || !data.year || !data.month) return;
  const page = data.launches_pagination?.page || 1;
  const type = data.launches_pagination?.filter_type || "all";
  const text = data.launches_pagination?.query || "";
  monthDataCache.set(monthCacheKey(data.year, data.month, page, type, text), data);
}

function isCurrentViewData(data) {
  return Number(data?.year) === Number(viewYear) && Number(data?.month) === Number(viewMonth);
}

/* ─── Snapshot em sessionStorage: paint instantâneo entre páginas ──────────
   O app troca /home <-> /app (Dashboard) com reload de página inteira; sem
   isso a Visão Geral mostrava skeleton e esperava o WebSocket a cada troca.
   Guardamos só o snapshot no ESTADO PADRÃO da aba (pág 1, filtro "all", sem
   busca), escopado ao USER_ID. sessionStorage some quando o app é fechado de
   vez — nada de dado financeiro gravado no disco a longo prazo. */
function _snapSessionKey(year, month) {
  return `pb_snap_${USER_ID}_${year}_${String(month).padStart(2, "0")}`;
}
function persistSnapshotToSession(data) {
  if (!data || !data.year || !data.month || !USER_ID) return;
  const page = data.launches_pagination?.page || 1;
  const type = data.launches_pagination?.filter_type || "all";
  const text = data.launches_pagination?.query || "";
  if (page !== 1 || (type && type !== "all") || text) return; // só o estado padrão
  // pb_saved_at é o RECEBIMENTO do payload WS — push do servidor não tem "t0
  // de request" para capturar (a home, que usa fetch, carimba o t0). Teto
  // aceito: payload gerado ANTES de um reset e entregue DEPOIS do marker
  // passaria pelo predicado do restore; a janela é geração→entrega num socket
  // vivo (ms) e não há replay — a reconexão re-GERA o snapshot no connect.
  try { sessionStorage.setItem(_snapSessionKey(data.year, data.month), JSON.stringify({ ...data, pb_saved_at: Date.now() })); } catch {}
}
// Limpa os snapshots da aba. Chamada no logout e quando o paywall NEGA
// (meGate): o restore pinta o snapshot que a PRÓPRIA aba gravou antes do
// veredito do /me — aceitável (o navegador do usuário já possui o dado, e
// gatear o restore custaria ~1 RTT no caminho quente) — mas depois de um
// veredito negativo um reload da aba não deve repintar saldo.
// Os DOIS prefixos: pb_snap_ (esta tela) e pb_home_ (a /home, que a mesma aba
// pinta). Mesmo par do clearHomeCache (home.html) e do reset (settings.html).
// O paywall não chama /auth/logout, então a limpeza central do auth-refresh.js
// não roda neste caminho — aqui é a única que limpa.
function clearSessionSnapshots() {
  try { Object.keys(sessionStorage).forEach(k => { if (k.startsWith("pb_snap_") || k.startsWith("pb_home_")) sessionStorage.removeItem(k); }); } catch {}
}
function restoreSnapshotFromSession() {
  if (!USER_ID) return false;
  try {
    const raw = sessionStorage.getItem(_snapSessionKey(viewYear, viewMonth));
    if (!raw) return false;
    const data = JSON.parse(raw);
    // "Recomeçar do zero" em OUTRA aba (sessionStorage é por aba): snapshot
    // que não for comprovadamente POSTERIOR ao finbot_reset_at é dado apagado
    // — descarta em vez de pintar. Mesma regra do restoreHomeCache (home.html).
    const resetAt = Number(localStorage.getItem("finbot_reset_at") || 0);
    if (resetAt && !(Number(data.pb_saved_at) > resetAt)) {
      sessionStorage.removeItem(_snapSessionKey(viewYear, viewMonth));
      return false;
    }
    if (!isCurrentViewData(data)) return false;
    lastData = data;
    cacheMonthData(data);
    render(data);              // pinta na hora; o WebSocket revalida em seguida
    setLaunchesLoading(false);
    return true;
  } catch { return false; }
}

async function logoutDashboard() {
  try {
    await fetch(`${API}/auth/logout`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders()
    });
  } catch {}
  clearMenuCache();  // não deixa o chrome de um usuário vazar pro próximo login
  // Limpa os snapshots da sessão (defense-in-depth; já são escopados ao userId).
  clearSessionSnapshots();
  localStorage.setItem('finbot_logout_at', String(Date.now()));
  window.location.replace('/?logout=1');
}

window.addEventListener('storage', (event) => {
  if (event.key === 'finbot_logout_at') {
    window.location.replace('/?logout=1');
  }
  // "Recomeçar do zero" noutra aba: os saldos renderizados aqui são dado
  // apagado e nada mais repinta sozinho. Reação ao EVENTO (storage não
  // dispara na própria aba nem pela mera presença da chave — sem loop de
  // reload); newValue estrito ignora remoção. O reload cai no gate, que
  // decide (needs_onboarding → /onboarding).
  if (event.key === 'finbot_reset_at' && event.newValue) {
    window.location.reload();
  }
});

/* ═══════════════════════════════════════════════════════════════════════
   FORMATTERS
═══════════════════════════════════════════════════════════════════════ */
const fmt = n =>
  "R$ " + Number(n).toLocaleString("pt-BR", {minimumFractionDigits:2, maximumFractionDigits:2});

const fmtShort = n => {
  const a = Math.abs(n);
  return a >= 1000
    ? "R$" + (n/1000).toLocaleString("pt-BR",{minimumFractionDigits:1,maximumFractionDigits:1}) + "k"
    : "R$" + Number(n).toLocaleString("pt-BR",{minimumFractionDigits:0,maximumFractionDigits:0});
};

// Fuso do app (o backend agrupa tudo em America/Sao_Paulo). Exibimos as datas
// SEMPRE nesse fuso pra não depender do timezone do dispositivo — no WebView do
// iOS ele costuma vir em UTC, o que fazia a hora aparecer ~3h adiantada.
const APP_TZ = "America/Sao_Paulo";

// Normaliza uma string de data: se vier sem timezone (naive), a coluna é
// timestamptz em UTC, então trata como UTC. Devolve um Date (instante) ou null.
function _isoToDate(iso) {
  if (!iso) return null;
  let s = String(iso);
  const hasTz = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(s);
  if (!hasTz) s = s.replace(" ", "T") + "Z";
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

// Partes de parede (ano/mês/dia/hora/min) de um instante num dado fuso.
function _wallPartsInTZ(date, tz) {
  const dtf = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  const p = {};
  for (const part of dtf.formatToParts(date)) p[part.type] = part.value;
  if (p.hour === "24") p.hour = "00"; // alguns engines usam 24 pra meia-noite
  return p;
}

// Offset do fuso (em minutos) num instante: negativo p/ oeste de UTC (-180 = -03:00).
function _tzOffsetMinutes(date, tz) {
  const p = _wallPartsInTZ(date, tz);
  const asUTC = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  return Math.round((asUTC - date.getTime()) / 60000);
}

// "YYYY-MM-DDTHH:MM" interpretado como hora de PAREDE em APP_TZ -> instante ISO
// (UTC). Ex.: 12:00 em São Paulo -> 15:00Z. Brasil não tem DST (desde 2019),
// então o offset é estável.
function appTzWallClockToISO(localStr) {
  if (!localStr) return null;
  const [datePart, timePart = "00:00"] = String(localStr).split("T");
  const [y, mo, da] = datePart.split("-").map(Number);
  const [h, mi] = timePart.split(":").map(Number);
  if ([y, mo, da, h, mi].some(n => Number.isNaN(n))) return null;
  const asUTC = Date.UTC(y, mo - 1, da, h, mi);
  const offsetMin = _tzOffsetMinutes(new Date(asUTC), APP_TZ);
  return new Date(asUTC - offsetMin * 60000).toISOString();
}

const fmtDate = iso => {
  const d = _isoToDate(iso);
  if (!d) return "—";
  return d.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    timeZone: APP_TZ,
  });
};

// Data/hora de um lançamento na lista.
//  - has_time (manual, cartão, banco que envia hora) → "dd/mm, HH:MM" do instante real.
//  - só data (banco que manda apenas a data) → "dd/mm" a partir de `posted_at`,
//    lendo a string YYYY-MM-DD direto (sem new Date → sem conversão de fuso, que
//    era o que jogava a data pro dia anterior).
const fmtLaunchWhen = l => {
  if (l && l.has_time && l.criado_em) return fmtDate(l.criado_em);
  const src = l && (l.posted_at || l.criado_em);
  if (!src) return "—";
  const p = String(src).slice(0, 10).split("-");
  return p.length === 3 ? `${p[2]}/${p[1]}` : fmtDate(l && l.criado_em);
};

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"
}[c]));

const escapeJsString = s => esc(String(s ?? "")
  .replace(/\\/g, "\\\\")
  .replace(/\r/g, "\\r")
  .replace(/\n/g, "\\n")
  .replace(/'/g, "\\'"));

const displayInvestmentName = (i) => {
  const name = String(i?.name || "").trim();
  const legacyPrefix = [i?.asset_type, i?.issuer].filter(Boolean).join(" ").trim();
  if (legacyPrefix && name.toLowerCase().startsWith(legacyPrefix.toLowerCase() + " ")) {
    return name.slice(legacyPrefix.length).trim();
  }
  return name;
};

const fmtPct = n =>
  n == null || Number.isNaN(Number(n)) ? "—" : Number(n).toLocaleString("pt-BR", {minimumFractionDigits:2, maximumFractionDigits:2}) + "%";

const PERIOD_LABELS = {
  cdi: "CDI",
  cdi_spread: "CDI + spread",
  ipca_spread: "IPCA + spread",
  selic_spread: "SELIC + spread",
  yearly: "a.a.",
  monthly: "a.m.",
  daily: "a.d."
};

// Taxa efetiva do investimento: média ponderada por saldo dos lotes abertos.
// Se todos os lotes têm a mesma taxa (ou não há lotes), retorna a taxa do
// investimento-pai. Sinaliza isWeighted=true quando os lotes diferem entre si.
function effectiveInvestmentRate(inv) {
  const fallback = { rate: Number(inv?.rate || 0), period: inv?.period, isWeighted: false };
  const lots = (inv?.lots || []).filter(l => l.status === "open" && Number(l.balance || 0) > 0);
  if (!lots.length) return fallback;
  const totalBalance = lots.reduce((s, l) => s + Number(l.balance || 0), 0);
  if (totalBalance <= 0) return fallback;
  const firstRate = Number(lots[0].rate ?? inv?.rate ?? 0);
  const allSame = lots.every(l => Math.abs(Number(l.rate ?? inv?.rate ?? 0) - firstRate) < 1e-9);
  if (allSame) return { rate: firstRate, period: inv?.period, isWeighted: false };
  const weighted = lots.reduce((s, l) => s + Number(l.balance || 0) * Number(l.rate ?? inv?.rate ?? 0), 0) / totalBalance;
  return { rate: weighted, period: inv?.period, isWeighted: true };
}

function investmentRateLabel(i) {
  const eff = effectiveInvestmentRate(i);
  const value = Number(eff.rate || 0) * 100;
  const suffix = eff.isWeighted ? " (média)" : "";
  if (i?.period === "cdi") return `${fmtPct(value)} do CDI${suffix}`;
  if (["cdi_spread", "ipca_spread", "selic_spread", "yearly"].includes(i?.period)) return `${fmtPct(value)} a.a.${suffix}`;
  if (i?.period === "monthly") return `${fmtPct(value)} a.m.${suffix}`;
  if (i?.period === "daily") return `${fmtPct(value)} a.d.${suffix}`;
  return `${fmtPct(value)}${suffix}`;
}

const INDEXER_TO_PERIOD = {
  pct_cdi: "cdi",
  cdi_spread: "cdi_spread",
  ipca_spread: "ipca_spread",
  fixed: "yearly",
  selic_spread: "selic_spread"
};

const TAX_BY_ASSET = {
  LCI: "exempt_ir_iof",
  LCA: "exempt_ir_iof",
  CRI: "exempt_ir_iof",
  CRA: "exempt_ir_iof",
  "ETF Renda Fixa": "etf_rf_15"
};

const INVESTMENT_GUIDES = {
  CDB: {
    indexer: "pct_cdi", rate: "110", frequency: "maturity",
    issuerPlaceholder: "Nubank, Itaú, BTG",
    namePlaceholder: "CDB liquidez diária 110% CDI"
  },
  LCI: {
    indexer: "pct_cdi", rate: "95", frequency: "maturity",
    issuerPlaceholder: "Banco emissor",
    namePlaceholder: "LCI 95% CDI"
  },
  LCA: {
    indexer: "pct_cdi", rate: "95", frequency: "maturity",
    issuerPlaceholder: "Banco emissor",
    namePlaceholder: "LCA 95% CDI"
  },
  "Debênture": {
    indexer: "ipca_spread", rate: "7.5", frequency: "semiannual",
    issuerPlaceholder: "Empresa emissora",
    namePlaceholder: "Debênture IPCA+ 2031"
  },
  CRI: {
    indexer: "ipca_spread", rate: "7.5", frequency: "maturity",
    issuerPlaceholder: "Securitizadora",
    namePlaceholder: "CRI IPCA+ 2030"
  },
  CRA: {
    indexer: "ipca_spread", rate: "7.5", frequency: "maturity",
    issuerPlaceholder: "Securitizadora",
    namePlaceholder: "CRA IPCA+ 2030"
  },
  "ETF Renda Fixa": {
    indexer: "fixed", rate: "15", frequency: "maturity",
    issuerPlaceholder: "Gestora ou ticker",
    namePlaceholder: "ETF Renda Fixa"
  },
  "Tesouro Selic": {
    indexer: "selic_spread", rate: "0.07", frequency: "maturity",
    issuerPlaceholder: "Tesouro Direto",
    namePlaceholder: "Tesouro Selic 2029"
  },
  "Tesouro IPCA+": {
    indexer: "ipca_spread", rate: "7.43", frequency: "maturity",
    issuerPlaceholder: "Tesouro Direto",
    namePlaceholder: "Tesouro IPCA+ 2035"
  },
  "Tesouro Prefixado": {
    indexer: "fixed", rate: "13.59", frequency: "maturity",
    issuerPlaceholder: "Tesouro Direto",
    namePlaceholder: "Tesouro Prefixado 2032"
  }
};

function taxProfileForAsset(assetType) {
  return TAX_BY_ASSET[assetType] || "regressive_ir_iof";
}

// Mapa view-id → elemento. Inclui as novas seções acessíveis pelo sidebar.
const DASH_VIEWS = [
  "overview", "analytics", "history", "fixed", "budgets", "goals",
  "categories", "installments", "cards", "investments", "affiliate", "agentes"
];

function setMainView(view) {
  // Free: bloqueia navegacao pra tela inteira de investimentos. Botao fica
  // visivel mas desabilitado; click abre modal de upgrade (item 17/18).
  if (view === "investments" && !featureAllowed("investments")) {
    showUpgradeModal("investments");
    return;
  }
  document.querySelectorAll(".main-tab").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".sidenav-item[data-nav]").forEach(b => b.classList.toggle("active", b.dataset.nav === view));

  DASH_VIEWS.forEach(v => {
    const el = document.getElementById(`${v}-view`);
    if (el) el.classList.toggle("active", v === view);
  });

  // Alterna o botão de ação rápida conforme a aba: + Investir só na aba
  // de investimentos; + Lançar nas demais.
  const btnLaunch = document.getElementById("open-launch-btn");
  const btnInvest = document.getElementById("open-invest-btn");
  if (btnLaunch) btnLaunch.style.display = view === "investments" ? "none" : "";
  if (btnInvest) btnInvest.style.display = view === "investments" ? "" : "none";
  if (view === "investments" && lastData) {
    renderInvestmentsPanel(lastData);
    runInvestmentSimulator();
  }
}

// Atalho usado pelos itens do sidebar. Fecha o drawer no mobile.
function navigateTo(view) {
  setMainView(view);
  toggleSidenav(false);
  // Scrollar pro topo quando troca de view
  try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch(_) {}
  // Lazy load da view Análises (5 endpoints em paralelo, stale-while-revalidate)
  if (view === "analytics") loadAnalyticsView();
  if (view === "history") loadHistoryView();
  if (view === "cards") loadCardsView();
  if (view === "installments") loadInstallmentsView();
  if (view === "categories") loadCategoriesView();
  if (view === "budgets") loadBudgetsView();
  // View Recorrentes: abre sempre na Visão geral (resumo das 3 áreas).
  if (view === "fixed") setRecurringTab("overview");
  if (view === "goals") loadGoalsView();
  if (view === "affiliate") loadAffiliateView();
  if (view === "agentes") loadAgentesView();
}

// ── Cartões (view dinâmica conectada ao backend) ──────────────────────
const CARD_COLOR_OPTIONS = [
  { key: "purple", label: "Rosa",    sample: "linear-gradient(135deg,#FF2D8E 0%,#C7186B 100%)" },
  { key: "coral",  label: "Coral",   sample: "linear-gradient(135deg,#ec4899 0%,#db2777 100%)" },
  { key: "gold",   label: "Dourado", sample: "linear-gradient(135deg,#f59e0b 0%,#c2410c 100%)" },
  { key: "green",  label: "Verde",   sample: "linear-gradient(135deg,#10b981 0%,#047857 100%)" },
  { key: "blue",   label: "Azul",    sample: "linear-gradient(135deg,#3b82f6 0%,#1e40af 100%)" },
  { key: "gray",   label: "Cinza",   sample: "linear-gradient(135deg,#6b7280 0%,#374151 100%)" },
];

let _cardEditState = { id: null, color: "purple" };
let _currentCards = [];
let _cardsCache = null;       // último payload do GET /cards/summary
const _cardsChannel = makeFetchChannel(); // dedup + abort + geração
const CARDS_FREE_LIMIT = 1; // Free plan: 1 cartão. Pro: ilimitado.

function _fmtBRL(n) {
  return "R$ " + Number(n || 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _fmtDateBR(iso) {
  if (!iso) return "—";
  try {
    const [y, m, d] = iso.split("-").map(Number);
    return String(d).padStart(2, "0") + "/" + String(m).padStart(2, "0");
  } catch { return "—"; }
}

function _bestPurchaseDay(closing_day) {
  // Melhor dia = dia seguinte ao fechamento (maior prazo até vencer)
  if (!closing_day) return "—";
  const d = (Number(closing_day) % 31) + 1;
  return d + " do mês";
}

let _cardsRetryTimer = null;

async function loadCardsView(forceFresh = false, { background = false } = {}) {
  const grid = document.getElementById("cards-grid");
  const stats = document.getElementById("cards-stats");
  if (!grid || !stats) return;
  // Aguarda USER_ID resolver — protege contra clique muito cedo, antes do init.
  // Usa setInterval recorrente: se USER_ID demorar muito (Railway lento), continua
  // tentando até resolver, em vez de desistir após 500ms.
  if (!USER_ID) {
    // No puxão (background) não dá pra ficar em retry silencioso: a promise
    // precisa assentar pro indicador do gesto sair. Rejeita — vira âmbar.
    if (background) throw new Error("cartões: sessão ainda não pronta");
    stats.innerHTML = "";
    grid.innerHTML = '<div class="empty" style="grid-column:1/-1;padding:30px;text-align:center;color:var(--text-3)"><img class="loading-sticker" src="/brand/stickers/loading.webp" alt="" />Conectando à sua conta…</div>';
    if (!_cardsRetryTimer) {
      _cardsRetryTimer = setInterval(() => {
        if (USER_ID) {
          clearInterval(_cardsRetryTimer);
          _cardsRetryTimer = null;
          loadCardsView(forceFresh);
        }
      }, 250);
    }
    return;
  }

  // Puxar pra atualizar: sem skeleton, fetch ANTES de render e falha REAL
  // rejeita sem tocar no DOM (o render bom fica na tela, indicador âmbar).
  if (background) {
    const data = await _fetchCardsSummary({ force: true });
    if (data === undefined) return;   // superado/abortado — deixa a tela como está
    _cardsCache = data;
    renderCardsView(data);
    return;
  }

  // Stale-while-revalidate: se já tem cache, mostra IMEDIATAMENTE (sem
  // skeleton) e refaz o fetch em background pra atualizar. Igual à Visão
  // Geral que serve do `lastData` do WebSocket.
  if (_cardsCache && !forceFresh) {
    renderCardsView(_cardsCache);
    // Revalidate silencioso (não bloqueia UI). Se mudou algo, re-renderiza.
    // `fresh` undefined (superado) é falsy → o if pula sozinho.
    _fetchCardsSummary().then(fresh => {
      if (fresh && JSON.stringify(fresh) !== JSON.stringify(_cardsCache)) {
        _cardsCache = fresh;
        renderCardsView(fresh);
      }
    }).catch(() => {});
    return;
  }

  // Sem cache (primeira vez ou forceFresh): mostra skeleton e bloqueia até carregar
  stats.innerHTML = `
    <div class="stat-tile"><div class="stat-label">Limite total</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Usado</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Disponível</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Próxima fatura</div><div class="sk sk-h2"></div></div>
  `;
  grid.innerHTML = '<div class="empty" style="grid-column:1/-1;padding:30px;text-align:center;color:var(--text-3)">Carregando cartões…</div>';

  try {
    const data = await _fetchCardsSummary({ force: true });
    if (data === undefined) return;   // superado por um pedido mais novo
    _cardsCache = data;
    renderCardsView(data);
  } catch (err) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1;padding:30px;text-align:center;color:var(--red)">Erro ao carregar: ${escapeHtmlSafe(String(err.message || err))}</div>`;
    stats.innerHTML = "";
  }
}

// Fetch puro do summary via canal (dedup + abort + geração).
// Devolve os cartões (array), ou undefined se superado/abortado; rejeita no erro real.
async function _fetchCardsSummary({ force = false } = {}) {
  return _cardsChannel.run(async (signal) => {
    const resp = await fetch(`${API}/cards/${USER_ID}/summary`, {
      credentials: "same-origin",
      headers: csrfHeaders(),
      signal,
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(`(HTTP ${resp.status}) ${detail}`);
    }
    const data = await resp.json();
    return data.cards || [];
  }, { force });
}

function renderCardsView(cards) {
  _currentCards = cards || [];
  const grid = document.getElementById("cards-grid");
  const stats = document.getElementById("cards-stats");

  // Stats agregados
  const limitSum = cards.reduce((s, c) => s + (c.credit_limit || 0), 0);
  const usedSum  = cards.reduce((s, c) => s + (c.credit_used || 0), 0);
  const availSum = cards.reduce((s, c) => s + (c.credit_available != null ? c.credit_available : 0), 0);
  const openSum  = cards.reduce((s, c) => s + (c.open_bill?.due_amount || 0), 0);
  const pct = limitSum > 0 ? Math.round(usedSum / limitSum * 1000) / 10 : 0;
  const availPct = limitSum > 0 ? Math.round(availSum / limitSum * 1000) / 10 : 0;

  stats.innerHTML = `
    <div class="stat-tile" style="animation-delay:0ms">
      <div class="stat-label">Limite total</div>
      <div class="stat-value">${_fmtBRL(limitSum)}</div>
      <div class="stat-delta" style="color:var(--text-3)">${cards.length} cartão${cards.length === 1 ? "" : "(s)"} ativo${cards.length === 1 ? "" : "s"}</div>
    </div>
    <div class="stat-tile" style="animation-delay:60ms">
      <div class="stat-label">Usado este mês</div>
      <div class="stat-value" style="color:var(--red)">${_fmtBRL(usedSum)}</div>
      <div class="stat-delta down">${pct.toFixed(1).replace(".", ",")}% do limite</div>
    </div>
    <div class="stat-tile" style="animation-delay:120ms">
      <div class="stat-label">Disponível agora</div>
      <div class="stat-value" style="color:var(--green)">${_fmtBRL(availSum)}</div>
      <div class="stat-delta up">${availPct.toFixed(1).replace(".", ",")}% livre</div>
    </div>
    <div class="stat-tile" style="animation-delay:180ms">
      <div class="stat-label">Fatura aberta total</div>
      <div class="stat-value">${_fmtBRL(openSum)}</div>
      <div class="stat-delta" style="color:var(--text-3)">somando todos os cartões</div>
    </div>
  `;

  if (cards.length === 0) {
    grid.innerHTML = `
      <div class="empty" style="grid-column:1/-1;padding:50px;text-align:center;color:var(--text-3);background:var(--glass-bg);border:1px dashed var(--glass-border);border-radius:var(--radius)">
        <img class="empty-sticker" src="/brand/stickers/report.webp" alt="" />
        <div style="font-size:1.05rem;font-weight:700;color:var(--text);margin-bottom:6px">Nenhum cartão cadastrado</div>
        <div style="margin-bottom:14px">Cadastre seu primeiro cartão pra acompanhar fatura, limite e parcelamentos.</div>
        <button class="mock-cta" onclick="openCardEditModal()">+ Cadastrar cartão</button>
      </div>`;
    return;
  }

  grid.innerHTML = cards.map((c, i) => _renderCardItem(c, i)).join("");
  setupCardsGridSort();
}

function _renderCardItem(c, idx = 0) {
  const color = c.color || "purple";
  const hasFlag = !!c.flag;
  const hasNumber = !!c.last4;
  const isMinimal = !hasFlag && !hasNumber;
  const flag = hasFlag ? `<span class="cc-flag">${escapeHtmlSafe(c.flag)}</span>` : "";
  const number = hasNumber ? `<div class="cc-number">•••• •••• •••• ${escapeHtmlSafe(c.last4)}</div>` : "";
  const lim = c.credit_limit;
  const used = c.credit_used || 0;
  const usePct = lim ? Math.min(100, (used / lim) * 100) : 0;
  const fillClass = usePct > 80 ? "red" : usePct > 60 ? "yellow" : "green";
  const openDue = c.open_bill?.due_amount || 0;
  const openColor = openDue > 0 ? "red" : "";

  // Layout do cc-card muda quando NÃO tem flag nem last4 (centraliza nome)
  const ccInner = isMinimal
    ? `<div class="cc-bg-icon"><i class="ph ph-credit-card" aria-hidden="true"></i></div><div class="cc-nickname">${escapeHtmlSafe(c.name)}</div>`
    : `<div class="cc-bg-icon"><i class="ph ph-credit-card" aria-hidden="true"></i></div>
       <div class="cc-top">${flag}</div>
       ${number}
       <div class="cc-nickname">${escapeHtmlSafe(c.name)}</div>`;

  const animDelay = 240 + idx * 80; // começa depois dos 4 stat-tiles (180+60)

  return `
    <details class="mock-card cc-details" data-card-id="${c.id}" data-open-bill-id="${c.open_bill?.id || ''}" style="animation-delay:${animDelay}ms">
      <summary>
        <div class="cc-card ${color}${isMinimal ? " minimal" : ""}">${ccInner}</div>
      </summary>
      <div class="cc-detail-body">
        <div class="cc-meta" style="margin:0">
          <div class="row"><span class="label">Fatura aberta</span><span class="val cc-money ${openColor}">${_fmtBRL(openDue)}</span></div>
          <div class="row"><span class="label">Limite</span><span class="val cc-money">${lim != null ? _fmtBRL(lim) : "—"}</span></div>
          <div class="row"><span class="label">Melhor dia</span><span class="val">${_bestPurchaseDay(c.closing_day)}</span></div>
          <div class="row"><span class="label">Fecha em</span><span class="val">${c.closing_day ? "dia " + c.closing_day : "—"}</span></div>
          <div class="row"><span class="label">Vence em</span><span class="val">${c.due_day ? "dia " + c.due_day : "—"}</span></div>
          <div class="row"><span class="label">Próxima fatura</span><span class="val cc-money">${_fmtBRL(c.next_bill?.total || 0)}</span></div>
        </div>
        ${lim != null ? `
          <div class="bar-body" style="margin-top:12px">
            <div class="bar-head"><span class="name">Limite usado</span><span class="val cc-money">${_fmtBRL(used)} / ${_fmtBRL(lim)}</span></div>
            <div class="bar-track"><div class="bar-fill ${fillClass}" style="width:${usePct.toFixed(1)}%"></div></div>
          </div>` : ""}
        <div class="cc-detail-actions">
          ${c.open_bill?.id ? `<button class="mock-cta" onclick='event.stopPropagation(); openCardBillDetail(${c.id}, ${c.open_bill.id})'><i class="ph ph-file-text" aria-hidden="true"></i> Ver fatura</button>` : ""}
          <button class="mock-cta outline" onclick='event.stopPropagation(); openCardEditModal(${escapeHtmlSafe(JSON.stringify(c))})'><i class="ph ph-pencil-simple" aria-hidden="true"></i> Editar</button>
          <button class="inst-delete-btn" onclick="event.stopPropagation(); openCardDeleteModal(${c.id}, ${escapeHtmlSafe(JSON.stringify(c.name || ""))})"><i class="ph ph-trash" aria-hidden="true"></i> Excluir</button>
        </div>
      </div>
    </details>
  `;
}

// ── Modal cadastrar/editar cartão ─────────────────────────────────────
function _renderCardColorPicker(selected) {
  const wrap = document.getElementById("card-edit-colors");
  if (!wrap) return;
  wrap.innerHTML = CARD_COLOR_OPTIONS.map(opt => `
    <button type="button" data-color="${opt.key}"
      title="${opt.label}"
      onclick="_pickCardColor('${opt.key}')"
      style="width:44px;height:30px;border-radius:8px;border:2px solid ${opt.key === selected ? "#fff" : "transparent"};
             background:${opt.sample};cursor:pointer;
             box-shadow:${opt.key === selected ? "0 0 0 2px rgba(255,45,142,.5)" : "none"}"
    ></button>
  `).join("");
}

function _pickCardColor(key) {
  _cardEditState.color = key;
  _renderCardColorPicker(key);
}

function openCardEditModal(card) {
  const isEdit = !!(card && card.id);
  // Pro gate: bloqueia novo cadastro pra Free que já atingiu o limite.
  // Edição NUNCA bloqueia.
  if (!isEdit && !featureAllowed("cards_unlimited") && _currentCards.length >= CARDS_FREE_LIMIT) {
    showUpgradeModal("cards_unlimited");
    return;
  }
  _cardEditState = { id: isEdit ? card.id : null, color: (card && card.color) || "purple" };
  document.getElementById("card-edit-title").textContent = isEdit ? "Editar cartão" : "Novo cartão";
  document.getElementById("card-edit-id").value = isEdit ? card.id : "";
  document.getElementById("card-edit-name").value = isEdit ? (card.name || "") : "";
  document.getElementById("card-edit-flag").value = isEdit ? (card.flag || "") : "";
  document.getElementById("card-edit-last4").value = isEdit ? (card.last4 || "") : "";
  document.getElementById("card-edit-limit").value = isEdit && card.credit_limit != null ? card.credit_limit : "";
  document.getElementById("card-edit-closing").value = isEdit ? (card.closing_day || "") : "";
  document.getElementById("card-edit-due").value = isEdit ? (card.due_day || "") : "";
  document.getElementById("card-edit-save-btn").textContent = isEdit ? "Salvar alterações" : "Cadastrar";
  _renderCardColorPicker(_cardEditState.color);
  document.getElementById("card-edit-overlay").classList.add("open");
  setTimeout(() => document.getElementById("card-edit-name")?.focus(), 50);
}

function closeCardEditModal() {
  document.getElementById("card-edit-overlay").classList.remove("open");
}

async function saveCard() {
  const id = document.getElementById("card-edit-id").value;
  const isEdit = !!id;
  const name = document.getElementById("card-edit-name").value.trim();
  const flag = document.getElementById("card-edit-flag").value || null;
  const last4Raw = document.getElementById("card-edit-last4").value.trim();
  const last4 = last4Raw || null;
  const limitRaw = document.getElementById("card-edit-limit").value;
  const credit_limit = limitRaw === "" ? null : Number(limitRaw);
  const closing_day = Number(document.getElementById("card-edit-closing").value);
  const due_day = Number(document.getElementById("card-edit-due").value);
  const color = _cardEditState.color;

  if (!name) { await alertModal("Digite um apelido pro cartão.", { title: "Apelido obrigatório" }); return; }
  if (!closing_day || !due_day) { await alertModal("Informe os dias de fechamento e vencimento.", { title: "Dados incompletos" }); return; }
  if (closing_day < 1 || closing_day > 31 || due_day < 1 || due_day > 31) { await alertModal("Os dias de fechamento e vencimento devem estar entre 1 e 31.", { title: "Dia inválido" }); return; }
  if (last4 && !/^\d{4}$/.test(last4)) { await alertModal("Últimos 4 dígitos devem ser 4 números.", { title: "Inválido" }); return; }

  const btn = document.getElementById("card-edit-save-btn");
  btn.disabled = true; const original = btn.textContent; btn.textContent = "Salvando…";

  try {
    const url = isEdit
      ? `${API}/cards/${USER_ID}/${id}`
      : `${API}/cards/${USER_ID}`;
    const body = { name, closing_day, due_day, color, flag, last4, credit_limit };
    if (isEdit) {
      body.clear_last4 = (last4 === null);
      body.clear_limit = (credit_limit === null);
    }
    const resp = await fetch(url, {
      method: isEdit ? "PATCH" : "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}));
      const detail = errBody.detail;
      // Pro gate: detail vem como objeto {error: "pro_required", feature: "cards_unlimited"}
      if (detail && typeof detail === "object" && detail.error === "pro_required") {
        closeCardEditModal();
        showUpgradeModal(detail.feature || "cards_unlimited");
        return;
      }
      const msg = (typeof detail === "string" ? detail : null) || `HTTP ${resp.status}`;
      throw new Error(msg);
    }
    closeCardEditModal();
    showToast(isEdit ? "✓ Cartão atualizado" : "✓ Cartão cadastrado");
    await loadCardsView(true);
  } catch (err) {
    await alertModal(String(err.message || err), { title: "Erro ao salvar" });
  } finally {
    btn.disabled = false; btn.textContent = original;
  }
}

// ── Modal excluir cartão (mostra impacto antes) ───────────────────────
async function openCardDeleteModal(cardId, cardName) {
  const body = document.getElementById("card-delete-body");
  body.innerHTML = "Verificando impacto…";
  document.getElementById("card-delete-overlay").classList.add("open");

  try {
    const resp = await fetch(`${API}/cards/${USER_ID}/${cardId}/delete-impact`, {
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    const imp = data.impact || {};
    const openTotal = Number(imp.open_bill_total || 0);
    const futCount = Number(imp.future_installments_count || 0);
    const totalTx = Number(imp.total_transactions_count || 0);
    const totalBills = Number(imp.total_bills_count || 0);

    let warning = "";
    if (openTotal > 0 || futCount > 0) {
      warning = `
        <div style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);border-radius:10px;padding:12px 14px;margin:14px 0">
          <div style="font-weight:700;color:#fca5a5;margin-bottom:6px"><i class="ph ph-warning" aria-hidden="true"></i> Este cartão ainda tem movimentação</div>
          <ul style="margin:0;padding-left:20px;font-size:.86rem;line-height:1.6;color:var(--text-2)">
            ${openTotal > 0 ? `<li><strong>Fatura em aberto:</strong> ${_fmtBRL(openTotal)}</li>` : ""}
            ${futCount > 0 ? `<li><strong>${futCount} parcela${futCount === 1 ? "" : "s"} futura${futCount === 1 ? "" : "s"}</strong> agendada${futCount === 1 ? "" : "s"}</li>` : ""}
          </ul>
        </div>
      `;
    }

    body.innerHTML = `
      <p>Tem certeza que quer excluir <strong>${escapeHtmlSafe(cardName)}</strong>?</p>
      ${warning}
      <p style="font-size:.84rem;color:var(--text-3);margin-top:10px">
        Serão apagados: <strong>${totalBills}</strong> fatura${totalBills === 1 ? "" : "s"} e <strong>${totalTx}</strong> lançamento${totalTx === 1 ? "" : "s"} de cartão. Isso é irreversível.
      </p>
    `;

    const btn = document.getElementById("card-delete-confirm-btn");
    btn.onclick = () => confirmDeleteCard(cardId, cardName);
  } catch (err) {
    body.innerHTML = `<span style="color:var(--red)">Erro: ${escapeHtmlSafe(String(err.message || err))}</span>`;
  }
}

function closeCardDeleteModal() {
  document.getElementById("card-delete-overlay").classList.remove("open");
}

async function confirmDeleteCard(cardId, cardName) {
  const btn = document.getElementById("card-delete-confirm-btn");
  btn.disabled = true; const original = btn.textContent; btn.textContent = "Excluindo…";
  try {
    const resp = await fetch(`${API}/cards/${USER_ID}/${cardId}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}));
      throw new Error(errBody.detail || `HTTP ${resp.status}`);
    }
    closeCardDeleteModal();
    showToast(`✓ Cartão "${cardName}" excluído`);
    await loadCardsView(true);
  } catch (err) {
    await alertModal(String(err.message || err), { title: "Erro ao excluir" });
  } finally {
    btn.disabled = false; btn.textContent = original;
  }
}

// Click no backdrop fecha os modais
document.getElementById("card-edit-overlay")?.addEventListener("click", e => {
  if (e.target === e.currentTarget) closeCardEditModal();
});
document.getElementById("card-delete-overlay")?.addEventListener("click", e => {
  if (e.target === e.currentTarget) closeCardDeleteModal();
});

// Auto-scroll quando expande um cartão — se a parte aberta sai da tela visível,
// rola suavemente pra mostrar com uma margem extra de respiro embaixo.
// Usa capture porque o evento `toggle` não bubble.
document.addEventListener("toggle", (e) => {
  const t = e.target;
  if (!t || !t.classList || !t.classList.contains("cc-details")) return;
  if (!t.open) return;
  // Espera 2 frames pro browser pintar a animação detailSlide antes de medir
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const body = t.querySelector(".cc-detail-body");
    if (!body) return;
    const rect = body.getBoundingClientRect();
    const viewport = window.innerHeight || document.documentElement.clientHeight;
    const desiredBottomMargin = 48; // px de respiro embaixo
    const overflow = rect.bottom - (viewport - desiredBottomMargin);
    if (overflow > 0) {
      window.scrollBy({ top: overflow, behavior: "smooth" });
    }
  }));
}, true);

// ── Ver fatura a partir do card de cartão (Sprint 2 — reusa modal existente) ──
async function openCardBillDetail(cardId, fallbackBillId) {
  try {
    const res = await fetch(
      `${API}/bills/${USER_ID}?card_id=${cardId}&include_closed=true`,
      { credentials: "same-origin" },
    );
    if (!res.ok) throw new Error(await readApiError(res));
    const data = await res.json();
    const bills = data.bills || [];
    if (!bills.length) {
      if (typeof showLaunchSuccessToast === "function") {
        showLaunchSuccessToast("Sem faturas pra esse cartão ainda.");
      } else {
        showToast("Sem faturas pra esse cartão ainda.");
      }
      return;
    }
    // Abre PRIMEIRO a fatura que o card mostra (a clicada). Só se não vier,
    // cai pra fatura aberta com valor mais RECENTE (lista vem period_end ASC,
    // então varre de trás pra frente) — nunca a mais antiga em aberto, que era
    // o bug (parcelamento deixa várias faturas futuras "open" ao mesmo tempo).
    let currentIdx = fallbackBillId
      ? bills.findIndex(b => b.id === fallbackBillId)
      : -1;
    if (currentIdx < 0) {
      for (let i = bills.length - 1; i >= 0; i--) {
        if (bills[i].status === "open" && (bills[i].due_amount > 0 || bills[i].total > 0)) {
          currentIdx = i;
          break;
        }
      }
    }
    if (currentIdx < 0) currentIdx = bills.length - 1;
    _billNav = {
      cardId,
      billIds: bills.map(b => b.id),
      currentIdx,
    };
    openBillDetailModal(bills[currentIdx].id, { preserveNav: true });
  } catch (err) {
    showToast(err.message || "Erro ao carregar fatura.");
  }
}

// ── Parcelamentos (view dinâmica — Sprint 2) ──────────────────────────
const INST_CATEGORIA_EMOJI = {
  eletronicos: "💻", eletronico: "💻", celular: "📱", phone: "📱",
  alimentacao: "🍽️", alimentação: "🍽️", mercado: "🛒", restaurante: "🍴",
  transporte: "🚗", combustivel: "⛽", combustível: "⛽", uber: "🚕",
  saude: "❤️", saúde: "❤️", farmacia: "💊", farmácia: "💊",
  lazer: "🎮", entretenimento: "🎬", viagem: "✈️", viagens: "✈️",
  educacao: "📚", educação: "📚", curso: "📚", livro: "📖",
  roupas: "👕", vestuario: "👕", vestuário: "👕", calcado: "👟", calçado: "👟",
  casa: "🏠", moveis: "🛋️", móveis: "🛋️", decoracao: "🪴", decoração: "🪴",
  servicos: "🔧", serviços: "🔧", assinatura: "📺", assinaturas: "📺",
  outros: "🛍️",
};

function _instEmoji(categoria) {
  const k = (categoria || "").toLowerCase().trim();
  return INST_CATEGORIA_EMOJI[k] || "🛍️";
}

let _instCache = null;
const _instChannel = makeFetchChannel(); // dedup + abort + geração
let _instRetryTimer = null;

async function loadInstallmentsView(forceFresh = false, { background = false } = {}) {
  const stats = document.getElementById("installments-stats");
  const list = document.getElementById("installments-list");
  if (!stats || !list) return;

  if (!USER_ID) {
    if (background) throw new Error("parcelamentos: sessão ainda não pronta");
    stats.innerHTML = "";
    list.innerHTML = '<div class="empty" style="padding:30px;text-align:center;color:var(--text-3)">Conectando à sua conta…</div>';
    if (!_instRetryTimer) {
      _instRetryTimer = setInterval(() => {
        if (USER_ID) {
          clearInterval(_instRetryTimer);
          _instRetryTimer = null;
          loadInstallmentsView(forceFresh);
        }
      }, 250);
    }
    return;
  }

  // Puxão: sem skeleton, fetch antes de render, falha real rejeita sem tocar DOM.
  if (background) {
    const data = await _fetchInstallments({ force: true });
    if (data === undefined) return;
    _instCache = data;
    renderInstallmentsView(data);
    return;
  }

  if (_instCache && !forceFresh) {
    renderInstallmentsView(_instCache);
    _fetchInstallments().then(fresh => {
      if (fresh && JSON.stringify(fresh) !== JSON.stringify(_instCache)) {
        _instCache = fresh;
        renderInstallmentsView(fresh);
      }
    }).catch(() => {});
    return;
  }

  stats.innerHTML = `
    <div class="stat-tile"><div class="stat-label">Compras parceladas</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Total devido</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Próximo mês</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Já pago</div><div class="sk sk-h2"></div></div>
  `;
  list.innerHTML = '<div class="empty" style="padding:30px;text-align:center;color:var(--text-3)">Carregando parcelamentos…</div>';

  try {
    const data = await _fetchInstallments({ force: true });
    if (data === undefined) return;
    _instCache = data;
    renderInstallmentsView(data);
  } catch (err) {
    list.innerHTML = `<div class="empty" style="padding:30px;text-align:center;color:var(--red)">Erro ao carregar: ${escapeHtmlSafe(String(err.message || err))}</div>`;
    stats.innerHTML = "";
  }
}

async function _fetchInstallments({ force = false } = {}) {
  return _instChannel.run(async (signal) => {
    const resp = await fetch(`${API}/installments/${USER_ID}/list?sort=urgency`, {
      credentials: "same-origin",
      headers: csrfHeaders(),
      signal,
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(`(HTTP ${resp.status}) ${detail}`);
    }
    const data = await resp.json();
    return data.installments || [];
  }, { force });
}

function _isNextMonthIso(iso) {
  if (!iso) return false;
  const today = new Date();
  const [y, m] = iso.split("-").map(Number);
  const nextMonth = today.getMonth() + 2; // 1-indexed + 1
  const nextYear = today.getFullYear() + (nextMonth > 12 ? 1 : 0);
  const realMonth = nextMonth > 12 ? nextMonth - 12 : nextMonth;
  return y === nextYear && m === realMonth;
}

let _instTab = "active"; // 'active' (n_pending > 0) | 'history' (n_pending === 0)

function setInstallmentsTab(tab) {
  if (tab !== "active" && tab !== "history") return;
  if (tab === _instTab) return;
  _instTab = tab;
  document.querySelectorAll("#installments-tabs .inst-tab").forEach(b => {
    b.classList.toggle("active", b.dataset.tab === tab);
  });
  if (_instCache) renderInstallmentsView(_instCache);
}

function renderInstallmentsView(groups) {
  const stats = document.getElementById("installments-stats");
  const list = document.getElementById("installments-list");
  const hint = document.getElementById("installments-sort-hint");
  if (!stats || !list) return;

  const isHistory = _instTab === "history";
  const filtered = (groups || []).filter(g => isHistory
    ? (g.n_pending || 0) === 0
    : (g.n_pending || 0) > 0);

  const totalDevido = filtered.reduce((s, g) => s + (g.remaining_amount || 0), 0);
  const totalPago = filtered.reduce((s, g) => s + (g.paid_amount || 0), 0);
  const nParcelasFuturas = filtered.reduce((s, g) => s + (g.n_pending || 0), 0);
  const nParcelasPagas = filtered.reduce((s, g) => s + (g.n_paid || 0), 0);

  // Próximo mês: soma das próximas parcelas que caem no mês seguinte
  let proxMes = 0;
  let nProxMes = 0;
  filtered.forEach(g => {
    (g.parcelas || []).forEach(p => {
      if (!p.is_paid && _isNextMonthIso(p.due_date)) {
        proxMes += p.valor;
        nProxMes++;
      }
    });
  });

  if (isHistory) {
    // Maior parcelamento concluído (em valor total)
    const maior = filtered.reduce((max, g) => (g.total || 0) > (max?.total || 0) ? g : max, null);
    stats.innerHTML = `
      <div class="stat-tile" style="animation-delay:0ms">
        <div class="stat-label">Parcelamentos concluídos</div>
        <div class="stat-value">${filtered.length}</div>
        <div class="stat-delta up">${nParcelasPagas} parcela${nParcelasPagas === 1 ? "" : "s"} paga${nParcelasPagas === 1 ? "" : "s"}</div>
      </div>
      <div class="stat-tile" style="animation-delay:60ms">
        <div class="stat-label">Total quitado</div>
        <div class="stat-value" style="color:var(--green)">${_fmtBRL(totalPago)}</div>
        <div class="stat-delta" style="color:var(--text-3)">soma de tudo que já foi pago</div>
      </div>
      <div class="stat-tile" style="animation-delay:120ms">
        <div class="stat-label">Maior compra</div>
        <div class="stat-value">${maior ? _fmtBRL(maior.total) : "—"}</div>
        <div class="stat-delta" style="color:var(--text-3)">${maior ? (maior.name || "—").slice(0, 26) : "sem histórico"}</div>
      </div>
      <div class="stat-tile" style="animation-delay:180ms">
        <div class="stat-label">Categoria mais comum</div>
        <div class="stat-value" style="font-size:1.15rem">${_instMostCommonCategory(filtered) || "—"}</div>
        <div class="stat-delta" style="color:var(--text-3)">no histórico</div>
      </div>
    `;
  } else {
    stats.innerHTML = `
      <div class="stat-tile" style="animation-delay:0ms">
        <div class="stat-label">Compras parceladas</div>
        <div class="stat-value">${filtered.length}</div>
        <div class="stat-delta" style="color:var(--text-3)">em andamento</div>
      </div>
      <div class="stat-tile" style="animation-delay:60ms">
        <div class="stat-label">Total devido</div>
        <div class="stat-value" style="color:var(--red)">${_fmtBRL(totalDevido)}</div>
        <div class="stat-delta" style="color:var(--text-3)">${nParcelasFuturas} parcela${nParcelasFuturas === 1 ? "" : "s"} futura${nParcelasFuturas === 1 ? "" : "s"}</div>
      </div>
      <div class="stat-tile" style="animation-delay:120ms">
        <div class="stat-label">Próximo mês</div>
        <div class="stat-value">${_fmtBRL(proxMes)}</div>
        <div class="stat-delta" style="color:var(--text-3)">${nProxMes} parcela${nProxMes === 1 ? "" : "s"}</div>
      </div>
      <div class="stat-tile" style="animation-delay:180ms">
        <div class="stat-label">Já pago</div>
        <div class="stat-value" style="color:var(--green)">${_fmtBRL(totalPago)}</div>
        <div class="stat-delta up">${nParcelasPagas} parcela${nParcelasPagas === 1 ? "" : "s"} concluída${nParcelasPagas === 1 ? "" : "s"}</div>
      </div>
    `;
  }

  if (filtered.length === 0) {
    const msg = isHistory
      ? { sticker: "chill", title: "Nenhum parcelamento concluído ainda", body: "Quando você terminar de pagar um parcelamento, ele aparece aqui." }
      : { sticker: "report", title: "Nenhum parcelamento ativo", body: "Quando você comprar parcelado no cartão (pelo bot ou WhatsApp), aparecem aqui." };
    list.innerHTML = `
      <div class="empty" style="padding:50px;text-align:center;color:var(--text-3);background:var(--glass-bg);border:1px dashed var(--glass-border);border-radius:var(--radius)">
        <img class="empty-sticker" src="/brand/stickers/${msg.sticker}.webp" alt="" />
        <div style="font-size:1.05rem;font-weight:700;color:var(--text);margin-bottom:6px">${msg.title}</div>
        <div>${msg.body}</div>
      </div>`;
    if (hint) hint.style.display = "none";
    return;
  }

  if (hint) hint.style.display = isHistory ? "none" : "flex";
  list.innerHTML = filtered.map((g, i) => _renderInstallmentItem(g, i)).join("");
}

function _instMostCommonCategory(groups) {
  const counts = {};
  groups.forEach(g => {
    const c = (g.categoria || "outros").toLowerCase();
    counts[c] = (counts[c] || 0) + 1;
  });
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!sorted.length) return null;
  const [cat] = sorted[0];
  return cat.charAt(0).toUpperCase() + cat.slice(1);
}

function _renderInstallmentItem(g, idx = 0) {
  const emoji = _instEmoji(g.categoria);
  const catLabel = g.categoria ? (g.categoria.charAt(0).toUpperCase() + g.categoria.slice(1)) : "Sem categoria";
  const cardLabel = `<i class="ph ph-credit-card" aria-hidden="true"></i> ${escapeHtmlSafe(g.card_name || "Cartão")}`;
  const purchasedFmt = g.purchased_at ? (() => {
    const [y, m, d] = g.purchased_at.split("-").map(Number);
    return `${String(d).padStart(2, "0")}/${String(m).padStart(2, "0")}/${y}`;
  })() : "—";

  const parcelas = g.parcelas || [];
  const blocks = parcelas.map(p => {
    if (p.is_paid) return `<span class="progress-block paid" title="Parcela ${p.installment_no} · paga"></span>`;
    if (p.is_next) return `<span class="progress-block next" title="Parcela ${p.installment_no} · próxima (${_fmtDateBR(p.due_date)})"></span>`;
    return `<span class="progress-block" title="Parcela ${p.installment_no} · pendente"></span>`;
  }).join("");

  const progressText = g.n_pending === 0
    ? `${g.n_paid}/${parcelas.length} · quitado`
    : (g.n_pending === 1
        ? `${g.n_paid}/${parcelas.length} · última ${_fmtDateBR(g.next_due_date)}`
        : `${g.n_paid}/${parcelas.length} · próx. ${_fmtDateBR(g.next_due_date)}`);

  const parcelaRows = parcelas.map(p => {
    const dateBR = _fmtDateBR(p.due_date);
    if (p.is_paid) {
      return `<div class="parcel-row paid"><div class="parcel-status-icon paid"><i class="ph ph-check" aria-hidden="true"></i></div><div class="parcel-info"><span class="parcel-name">Parcela ${p.installment_no}</span><span class="parcel-date">${dateBR}</span><span class="parcel-tag paid">paga</span></div><span class="parcel-val">${_fmtBRL(p.valor)}</span></div>`;
    }
    if (p.is_next) {
      const tagText = g.n_pending === 1 ? "última!" : "próxima";
      return `<div class="parcel-row next"><div class="parcel-status-icon next">${p.installment_no}</div><div class="parcel-info"><span class="parcel-name">Parcela ${p.installment_no}</span><span class="parcel-date">${dateBR}</span><span class="parcel-tag next">${tagText}</span></div><span class="parcel-val">${_fmtBRL(p.valor)}</span></div>`;
    }
    return `<div class="parcel-row"><div class="parcel-status-icon future">${p.installment_no}</div><div class="parcel-info"><span class="parcel-name">Parcela ${p.installment_no}</span><span class="parcel-date">${dateBR}</span></div><span class="parcel-val">${_fmtBRL(p.valor)}</span></div>`;
  }).join("");

  const valorParcela = g.valor_parcela || 0;
  const anticipateBtn = g.n_pending > 0
    ? `<button class="mock-cta outline" onclick='event.stopPropagation(); openInstAnticipateModal(${escapeHtmlSafe(JSON.stringify(g.group_id))}, ${escapeHtmlSafe(JSON.stringify(g.name))}, ${valorParcela}, ${parcelas.find(p => p.is_next)?.installment_no || 0}, ${g.installments_total})'><i class="ph ph-lightning" aria-hidden="true"></i> Antecipar próxima</button>`
    : "";

  return `
    <details class="mock-card inst-card" style="animation-delay:${idx * 50}ms">
      <summary>
        <div class="inst-icon-box">${phIcon(emoji)}</div>
        <div class="inst-body">
          <div class="inst-row-1">
            <span class="inst-name">${escapeHtmlSafe(g.name)}</span>
            <span class="inst-tag-cat">${escapeHtmlSafe(catLabel)}</span>
            <span class="inst-tag-card">${cardLabel}</span>
            <span class="inst-tag-date">comprado ${purchasedFmt}</span>
          </div>
          <div class="inst-progress-mini">
            <div class="progress-blocks">${blocks}</div>
            <span class="inst-progress-text">${progressText}</span>
          </div>
        </div>
        <div class="inst-right">
          <div class="inst-amounts">
            <div class="tot">${_fmtBRL(g.total)}</div>
            <div class="sub">${parcelas.length}× ${_fmtBRL(valorParcela)}</div>
          </div>
          <div class="inst-chevron">⌄</div>
        </div>
      </summary>
      <div class="inst-detail-body">
        <div class="inst-detail-head">
          <h4>Parcelas</h4>
          <div class="inst-pay-summary">
            <span class="paid-amt">Pago ${_fmtBRL(g.paid_amount)}</span>
            <span class="left-amt">Restante ${_fmtBRL(g.remaining_amount)}</span>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${anticipateBtn}
            <button class="mock-cta outline" onclick='event.stopPropagation(); openInstEditModal(${escapeHtmlSafe(JSON.stringify(g.group_id))}, ${escapeHtmlSafe(JSON.stringify(g.name))}, ${escapeHtmlSafe(JSON.stringify(g.categoria || ""))})'><i class="ph ph-pencil-simple" aria-hidden="true"></i> Editar</button>
            <button class="inst-delete-btn" onclick='event.stopPropagation(); openInstDeleteModal(${escapeHtmlSafe(JSON.stringify(g.group_id))}, ${escapeHtmlSafe(JSON.stringify(g.name))})'>Excluir parcelamento</button>
          </div>
        </div>
        <div class="parcel-rows">${parcelaRows}</div>
      </div>
    </details>
  `;
}

// ── Modal: antecipar parcela ──────────────────────────────────────────
let _instAnticipateState = { group_id: null };

function openInstAnticipateModal(group_id, name, valor, installment_no, total) {
  _instAnticipateState = { group_id };
  const body = document.getElementById("inst-anticipate-body");
  body.innerHTML = `
    Antecipar a parcela <b>${installment_no}/${total}</b> de <b>${escapeHtmlSafe(name)}</b>?<br><br>
    Vai ser paga à vista da sua conta corrente:<br>
    <b style="color:var(--red)">${_fmtBRL(valor)}</b> agora<br><br>
    <span style="color:var(--text-3);font-size:.88rem">A parcela some do parcelamento e aparece no histórico de lançamentos como "Antecipou parcela ${installment_no}/${total}".</span>
  `;
  document.getElementById("inst-anticipate-overlay").classList.add("open");
}

function closeInstAnticipateModal() {
  document.getElementById("inst-anticipate-overlay").classList.remove("open");
  _instAnticipateState = { group_id: null };
}

async function confirmInstAnticipate() {
  const gid = _instAnticipateState.group_id;
  if (!gid) return;
  const btn = document.getElementById("inst-anticipate-confirm-btn");
  btn.disabled = true;
  btn.textContent = "Antecipando…";
  try {
    const resp = await fetch(`${API}/installments/${USER_ID}/${gid}/anticipate`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(detail);
    }
    const data = await resp.json();
    closeInstAnticipateModal();
    showToast(`✓ Parcela ${data.result.anticipated_installment_no}/${data.result.installments_total} antecipada`);
    _instCache = null;
    await loadInstallmentsView(true);
    sendRefresh && sendRefresh();
  } catch (err) {
    showToast(`Erro: ${err.message || err}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sim, antecipar";
  }
}

document.getElementById("inst-anticipate-confirm-btn")?.addEventListener("click", confirmInstAnticipate);
document.getElementById("inst-anticipate-overlay")?.addEventListener("click", e => {
  if (e.target === e.currentTarget) closeInstAnticipateModal();
});

// ── Modal: excluir parcelamento (com impact) ───────────────────────────
let _instDeleteState = { group_id: null };

async function openInstDeleteModal(group_id, name) {
  _instDeleteState = { group_id };
  const body = document.getElementById("inst-delete-body");
  body.innerHTML = "Carregando impacto…";
  document.getElementById("inst-delete-overlay").classList.add("open");

  try {
    const resp = await fetch(`${API}/installments/${USER_ID}/${group_id}/delete-impact`, {
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!resp.ok) {
      body.innerHTML = `<span style="color:var(--red)">Erro ao carregar impacto.</span>`;
      return;
    }
    const data = await resp.json();
    const imp = data.impact;
    const hasPaid = imp.paid_count > 0;
    const futOne = imp.future_count === 1;
    const paidOne = imp.paid_count === 1;
    if (hasPaid) {
      body.innerHTML = `
        Excluir <b>${escapeHtmlSafe(name)}</b>?<br><br>
        • <b>${imp.future_count}</b> parcela${futOne ? "" : "s"} futura${futOne ? "" : "s"} (${_fmtBRL(imp.future_total)}) ${futOne ? "será removida" : "serão removidas"} das faturas abertas. Saldo do mês volta.<br>
        • <b>${imp.paid_count}</b> parcela${paidOne ? "" : "s"} já paga${paidOne ? "" : "s"} (${_fmtBRL(imp.paid_total)}) ${paidOne ? "fica" : "ficam"} no histórico (faturas pagas intactas).<br><br>
        <span style="color:var(--red);font-weight:600">R$ ${imp.paid_total.toFixed(2).replace(".", ",")} já pago${paidOne ? "" : "s"} NÃO ${paidOne ? "volta" : "voltam"} pra conta</span>. Dinheiro já saiu via fatura. Se precisar corrigir, crie um lançamento manual.
      `;
    } else {
      body.innerHTML = `
        Excluir <b>${escapeHtmlSafe(name)}</b>?<br><br>
        • <b>${imp.future_count}</b> parcela${futOne ? "" : "s"} (${_fmtBRL(imp.future_total)}) ${futOne ? "será removida" : "serão removidas"} das faturas abertas.<br>
        • Saldo do mês volta automaticamente.<br>
        • Sem impacto na sua conta corrente (nada foi pago ainda).
      `;
    }
  } catch (err) {
    body.innerHTML = `<span style="color:var(--red)">Erro: ${err.message || err}</span>`;
  }
}

function closeInstDeleteModal() {
  document.getElementById("inst-delete-overlay").classList.remove("open");
  _instDeleteState = { group_id: null };
}

async function confirmInstDelete() {
  const gid = _instDeleteState.group_id;
  if (!gid) return;
  const btn = document.getElementById("inst-delete-confirm-btn");
  btn.disabled = true;
  btn.textContent = "Excluindo…";
  try {
    const resp = await fetch(`${API}/installments/${USER_ID}/${gid}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(detail);
    }
    const data = await resp.json();
    closeInstDeleteModal();
    const removed = data.result.removed_count || 0;
    const orphaned = data.result.orphaned_count || 0;
    if (orphaned > 0) {
      showToast(`✓ Parcelamento excluído (${removed} futura${removed === 1 ? "" : "s"} removida${removed === 1 ? "" : "s"}, ${orphaned} paga${orphaned === 1 ? "" : "s"} mantida${orphaned === 1 ? "" : "s"} no histórico)`);
    } else {
      showToast(`✓ Parcelamento excluído (${removed} parcela${removed === 1 ? "" : "s"})`);
    }
    _instCache = null;
    await loadInstallmentsView(true);
    sendRefresh && sendRefresh();
  } catch (err) {
    showToast(`Erro: ${err.message || err}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sim, excluir";
  }
}

document.getElementById("inst-delete-confirm-btn")?.addEventListener("click", confirmInstDelete);
document.getElementById("inst-delete-overlay")?.addEventListener("click", e => {
  if (e.target === e.currentTarget) closeInstDeleteModal();
});

// ── Modal: editar parcelamento ────────────────────────────────────────
let _instEditState = { group_id: null };

function openInstEditModal(group_id, name, categoria) {
  _instEditState = { group_id };
  document.getElementById("inst-edit-nome").value = name || "";
  document.getElementById("inst-edit-categoria").value = categoria || "";
  document.getElementById("inst-edit-overlay").classList.add("open");
  setTimeout(() => document.getElementById("inst-edit-nome")?.focus(), 50);
}

function closeInstEditModal() {
  document.getElementById("inst-edit-overlay").classList.remove("open");
  _instEditState = { group_id: null };
}

async function saveInstallmentEdit() {
  const gid = _instEditState.group_id;
  if (!gid) return;
  const nome = document.getElementById("inst-edit-nome").value.trim();
  const categoria = document.getElementById("inst-edit-categoria").value.trim();
  const btn = document.getElementById("inst-edit-save-btn");
  btn.disabled = true;
  btn.textContent = "Salvando…";
  try {
    const resp = await fetch(`${API}/installments/${USER_ID}/${gid}`, {
      method: "PATCH",
      credentials: "same-origin",
      headers: { ...csrfHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ nome: nome || null, categoria: categoria || null }),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(detail);
    }
    closeInstEditModal();
    showToast("✓ Parcelamento atualizado");
    _instCache = null;
    await loadInstallmentsView(true);
    sendRefresh && sendRefresh();
  } catch (err) {
    showToast(`Erro: ${err.message || err}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Salvar";
  }
}

document.getElementById("inst-edit-overlay")?.addEventListener("click", e => {
  if (e.target === e.currentTarget) closeInstEditModal();
});

// ══════════════════════════════════════════════════════════════════════
// Categorias + Orçamentos (Sprint 3 — views dinâmicas)
// ══════════════════════════════════════════════════════════════════════

const CATEGORY_EMOJI_OPTIONS = [
  "🏷️","🍔","🛒","🚗","💊","🏠","🎬","📚","📺","🐾",
  "💄","✈️","🎮","👕","🍺","☕","⛽","💼","💻","🎁",
  "📈","💰","₿","🎓","🐶","👶","🏋️","🎵","🧴","🎂",
];


// ── Icones Phosphor p/ categorias/metas/caixinhas (shim nao-destrutivo) ──
// Continua guardando o emoji no banco; converte pra <i> so no render.
// Fallback = icone neutro (tag): NUNCA mostra emoji.
const EMOJI_TO_PH = {"🏷":"tag","🍔":"hamburger","🍟":"hamburger","🛒":"shopping-cart","🚗":"car","💊":"pill","🏠":"house","🎬":"film-slate","📚":"books","📖":"book-open","📺":"television","🐾":"paw-print","🖥":"desktop","🖨":"printer","✈":"airplane-tilt","🎮":"game-controller","👕":"t-shirt","👟":"sneaker-move","👜":"handbag","🍺":"beer-stein","☕":"coffee","⛽":"gas-pump","💼":"briefcase","💻":"laptop","🎁":"gift","📈":"trend-up","💰":"coins","🤝":"handshake","₿":"currency-btc","🎓":"graduation-cap","🐶":"dog","👶":"baby","🏋":"barbell","🎵":"music-notes","💄":"heart-straight","🧴":"spray-bottle","🎂":"cake","🎯":"target","🛟":"lifebuoy","📱":"device-mobile","💎":"diamond","📸":"camera","🎸":"guitar","🛏":"bed","🏖":"umbrella","🐷":"piggy-bank","🍽":"fork-knife","🍴":"fork-knife","🥖":"bread","🥩":"fork-knife","🍕":"pizza","🌐":"globe","💧":"drop","💡":"lightbulb","🔥":"flame","🚇":"train","🚌":"bus","❤":"heart","🔧":"wrench","🍎":"apple-logo","🔎":"magnifying-glass","⛪":"church","🎟":"ticket","🚕":"taxi","🏦":"bank","🏢":"buildings","🏛":"bank","🦷":"tooth","🩺":"stethoscope","🛡":"shield","🧺":"basket","🧹":"broom","🎭":"mask-happy","🎤":"microphone","🎄":"tree-evergreen","💐":"flower","☁":"cloud","🍷":"wine","💇":"scissors","🖌":"paint-brush","🎨":"paint-brush","🪴":"tree","🩹":"first-aid","📄":"file-text","🧾":"receipt","📅":"calendar-dots","📊":"chart-bar","💳":"credit-card","💸":"trend-down","📡":"broadcast"};
function phIcon(val) {
  const raw = val == null ? "" : String(val);
  const v = raw.replace(/\uFE0F/g, "").trim();
  if (!v) return "";
  const name = EMOJI_TO_PH[v] || "tag";
  return `<i class="ph ph-${name}" aria-hidden="true"></i>`;
}

const CATEGORY_COLOR_OPTIONS = [
  "#FF2D8E","#5FA83C","#2E7FE0","#E84545","#12A892",
  "#BE8200","#7E5FE6","#E85F2A","#22C3D6","#94A3B8",
  "#f43f5e","#22c55e","#eab308","#14b8a6","#64748b",
];

let _categoriesCache = null;
let _budgetsStatusCache = null;
const _categoriesChannel = makeFetchChannel(); // dedup + abort + geração
const _budgetsChannel = makeFetchChannel();     // dedup + abort + geração

async function _fetchCategories(includeArchived = true, { force = false, direct = false } = {}) {
  const doFetch = async (signal) => {
    const resp = await fetch(`${API}/categories/${USER_ID}?include_archived=${includeArchived ? "true" : "false"}`, {
      credentials: "same-origin",
      headers: csrfHeaders(),
      signal,
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(`(HTTP ${resp.status}) ${detail}`);
    }
    const data = await resp.json();
    return data.categories || [];
  };
  // direct: chamada avulsa (ex.: dropdown do modal de orçamento) que NÃO pode
  // ser abortada por um load da view de Categorias. Fica fora do canal — sempre
  // devolve a lista (ou lança), nunca undefined. Sem o direct, um _fetchCategories
  // com force=true da view superaria essa e o modal receberia undefined → dropdown
  // vazio ("todas já têm orçamento").
  if (direct) return doFetch();
  return _categoriesChannel.run(doFetch, { force });
}

async function loadCategoriesView(forceFresh = false, { background = false } = {}) {
  const grid = document.getElementById("categories-grid");
  const stats = document.getElementById("categories-stats");
  if (!grid || !stats) return;
  if (!USER_ID) {
    if (background) throw new Error("categorias: sessão ainda não pronta");
    grid.innerHTML = '<div class="empty" style="grid-column:1/-1;padding:20px;text-align:center;color:var(--text-3)">Conectando…</div>';
    stats.innerHTML = "";
    setTimeout(() => loadCategoriesView(forceFresh), 300);
    return;
  }
  const showArchived = document.getElementById("cat-show-archived")?.checked ?? false;

  // Puxão: sem skeleton, fetch antes de render, falha real rejeita sem tocar DOM.
  if (background) {
    const data = await _fetchCategories(true, { force: true });
    if (data === undefined) return;
    _categoriesCache = data;
    renderCategoriesView(data, showArchived);
    return;
  }

  if (_categoriesCache && !forceFresh) {
    renderCategoriesView(_categoriesCache, showArchived);
    _fetchCategories(true).then(fresh => {
      if (fresh) { _categoriesCache = fresh; renderCategoriesView(fresh, showArchived); }
    }).catch(() => {});
    return;
  }

  grid.innerHTML = '<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Carregando categorias…</div>';
  stats.innerHTML = "";

  try {
    const data = await _fetchCategories(true, { force: true });
    if (data === undefined) return;
    _categoriesCache = data;
    renderCategoriesView(data, showArchived);
  } catch (err) {
    grid.innerHTML = `<div class="empty" style="padding:20px;color:var(--red)">Erro: ${escapeHtmlSafe(String(err.message || err))}</div>`;
  }
}

function renderCategoriesView(categories, showArchived) {
  const grid = document.getElementById("categories-grid");
  const stats = document.getElementById("categories-stats");
  if (!grid || !stats) return;

  const visible = (categories || []).filter(c => showArchived || !c.is_archived);
  const totalActive = (categories || []).filter(c => !c.is_archived).length;
  const totalArchived = (categories || []).filter(c => c.is_archived).length;
  const withUsage = (categories || []).filter(c => !c.is_archived && c.usage_count > 0).length;

  stats.innerHTML = `
    <div class="stat-tile" style="animation-delay:0ms">
      <div class="stat-label">Categorias ativas</div>
      <div class="stat-value">${totalActive}</div>
      <div class="stat-delta" style="color:var(--text-3)">${withUsage} com lançamentos</div>
    </div>
    <div class="stat-tile" style="animation-delay:60ms">
      <div class="stat-label">Arquivadas</div>
      <div class="stat-value" style="color:var(--text-3)">${totalArchived}</div>
      <div class="stat-delta" style="color:var(--text-3)">não aparecem em novos lançamentos</div>
    </div>
    <div class="stat-tile" style="animation-delay:120ms">
      <div class="stat-label">Customizadas</div>
      <div class="stat-value">${(categories || []).filter(c => !c.is_system).length}</div>
      <div class="stat-delta" style="color:var(--text-3)">${(categories || []).filter(c => c.is_system).length} padrão</div>
    </div>
  `;

  if (visible.length === 0) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1;padding:30px;text-align:center;color:var(--text-3)">Nenhuma categoria.</div>`;
  } else {
    grid.innerHTML = visible.map((c, i) => _renderCategoryPill(c, i)).join("");
  }

  _renderCategoriesDistribution(categories);
}

function _renderCategoriesDistribution(categories) {
  const dist = document.getElementById("categories-distribution");
  if (!dist) return;
  const monthly = (lastData && lastData.expense_categories) || [];
  if (!monthly.length) {
    dist.innerHTML = `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Sem despesas este mês.</div>`;
    return;
  }
  const total = monthly.reduce((s, c) => s + (c.total || 0), 0);
  if (total <= 0) {
    dist.innerHTML = `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Sem despesas este mês.</div>`;
    return;
  }
  const metaByName = {};
  (categories || []).forEach(c => { metaByName[(c.name || "").toLowerCase()] = c; });

  // Janela do mês VISÍVEL (a distribuição é do mês, não do histórico todo).
  // getDate() do dia 0 do mês seguinte = último dia; sem toISOString, que
  // converteria pro fuso e podia voltar um dia.
  const dy = (lastData && lastData.year) || viewYear;
  const dm = (lastData && lastData.month) || viewMonth;
  const mm = String(dm).padStart(2, "0");
  // `tipo`/`includeInternal` NÃO são enfeite: esta barra soma só despesa e
  // ignora movimento interno (query 6 de finance_bot_websocket_custom.py). Sem
  // os dois filtros a lista abria contradizendo o número que acabou de ser
  // clicado — R$ 50 na barra, R$ 750 no rodapé da lista, mesma categoria.
  const win = {
    from: `${dy}-${mm}-01`,
    to: `${dy}-${mm}-${new Date(dy, dm, 0).getDate()}`,
    tipo: "despesa",
    includeInternal: false,
  };

  dist.innerHTML = monthly.map((m, i) => {
    const meta = metaByName[(m.categoria || "").toLowerCase()] || {};
    const emoji = meta.emoji || "🏷️";
    const color = meta.color || "#FF2D8E";
    const pct = total > 0 ? (m.total / total * 100) : 0;
    const fillClass = pct > 30 ? "red" : pct > 15 ? "yellow" : "green";
    // `m.categoria` é texto de terceiro (a tool da IA escreve em
    // launches.categoria): JSON.stringify não escapa `'`/`&`, então vai
    // escapeHtmlSafe por fora — mesmo par de _renderCategoryPill.
    const argsSafe = escapeHtmlSafe(JSON.stringify(m.categoria) + "," + JSON.stringify(win));
    return `
      <div class="bar-row" style="cursor:pointer;animation-delay:${i * 70}ms"
           role="button" tabindex="0"
           aria-label="${escapeHtmlSafe(`Ver todos os lançamentos em ${m.categoria || ""} neste mês`)}"
           onclick='openCategoryLaunches(${argsSafe})'
           onkeydown='if(event.key==="Enter"||event.key===" "){event.preventDefault();openCategoryLaunches(${argsSafe});}'>
        <div class="bar-icon" style="color:${escapeHtmlSafe(color)}">${phIcon(emoji)}</div>
        <div class="bar-body">
          <div class="bar-head"><span class="name">${escapeHtmlSafe(m.categoria)}</span><span class="val">${_fmtBRL(m.total)}</span></div>
          <div class="bar-track"><div class="bar-fill ${fillClass}" style="width:${pct.toFixed(1)}%"></div></div>
          <div class="bar-sub">${pct.toFixed(1).replace(".", ",")}% do mês • ${m.count || 0} lançamento${m.count === 1 ? "" : "s"}</div>
        </div>
      </div>
    `;
  }).join("");
}

function _renderCategoryPill(cat, idx = 0) {
  const dim = cat.is_archived ? "opacity:.45;" : "";
  const tag = cat.is_archived
    ? '<span style="font-size:.65rem;color:var(--text-3);margin-left:6px">(arquivada)</span>'
    : (cat.is_system ? '<span style="font-size:.62rem;color:var(--text-3);margin-left:6px">padrão</span>' : '');
  const delay = 200 + idx * 30;
  return `
    <div class="cat-pill" style="cursor:pointer;${dim}animation-delay:${delay}ms" onclick='openCategoryEditModal(${escapeHtmlSafe(JSON.stringify(cat))})'>
      <span class="cat-dot" style="background:${escapeHtmlSafe(cat.color)}"></span>
      <div class="cat-body">
        <div class="cat-name">${phIcon(cat.emoji)} ${escapeHtmlSafe(cat.name)}${tag}</div>
        <div class="cat-val" style="color:var(--text-3)">${cat.usage_count || 0} lanç.</div>
      </div>
    </div>
  `;
}

// ── Lançamentos de uma categoria ──────────────────────────────────────
// Duas portas, e elas mostram conjuntos DIFERENTES de propósito:
//   • linha da Distribuição do mês → janela do mês + tipo=despesa + sem
//     movimento interno, que é exatamente o que aquela barra soma. A lista não
//     pode contradizer o número que o usuário acabou de clicar.
//   • "Ver lançamentos" do modal de edição → a categoria inteira, sem filtro
//     (não há número clicado); o subtítulo diz que inclui receita e interna.
//
// UM overlay por vez, sempre. Todo .overlay é z-index:800 (dashboard.css) e
// cada modal registra o SEU handler de ESC no document, em bolha: com dois
// abertos, um ESC fecharia os dois e quem pinta por cima vira sorteio da ordem
// de inserção no DOM. Por isso este modal FECHA o de edição ao abrir (e reabre
// ao fechar), e o detalhe de lançamento fecha ESTE ao abrir (e reabre).

const _CL_PAGE = 50;                // tamanho da página do "Carregar mais"
let _catLaunchesRows = [];          // linhas já adaptadas, na ordem da tela
let _catLaunchesTotal = 0;          // n_total do servidor (TODAS as que casam)
let _catLaunchesLoadingMore = false;
/* Cursor KEYSET da próxima página (`next_cursor` da rota), opaco: o front
   devolve o que recebeu. Era `offset = rows.length`, e OFFSET não sobrevive ao
   cenário NORMAL deste produto — o bot escreve no banco com o dashboard aberto,
   a linha nova entra ACIMA do corte (a ordem é por data desc) e a página 2
   repetia a última linha da 1 e comia outra. */
let _catLaunchesCursor = null;
let _catLaunchesCtx = null;         // opts da lista NO AR (null = não há lista pra voltar)
let _catLaunchesReturnFocus = null;
let _catLaunchesScroll = 0;         // scroll do #cl-list enquanto o detalhe está por cima
let _catLaunchesHiddenFocus = null; // linha que abriu o detalhe, pro voltar devolver o foco
// Mesmo canal de fetch do Histórico (makeFetchChannel): aborta o pedido anterior
// e devolve `undefined` quando este foi superado. Sem ele, clicar "mercado" e
// depois "saúde" deixava a resposta de mercado chegar por último e pintar as
// linhas de mercado sob o título "saúde" — e o clique numa linha abria o
// detalhe (com Excluir) de um lançamento de OUTRA categoria.
const _catLaunchesChannel = makeFetchChannel();

function _ensureCategoryLaunchesModal() {
  if (document.getElementById("cat-launches-overlay")) return;
  document.body.insertAdjacentHTML("beforeend", `
    <div class="overlay" id="cat-launches-overlay">
      <div class="modal wide cl-modal" role="dialog" aria-modal="true" aria-labelledby="cl-title">
        <h3 id="cl-title">Lançamentos</h3>
        <div class="msub" id="cl-sub"></div>
        <div class="cl-sum" id="cl-sum" hidden></div>
        <div id="cl-list"></div>
        <div id="cl-more" class="cl-more"></div>
        <div class="modal-acts cl-acts">
          <span class="cl-count" id="cl-foot"></span>
          <button type="button" class="btn-save" id="cl-close">Fechar</button>
        </div>
      </div>
    </div>`);
  const ov = document.getElementById("cat-launches-overlay");
  ov.addEventListener("click", e => { if (e.target === ov) closeCategoryLaunches(); });
  document.getElementById("cl-close").addEventListener("click", closeCategoryLaunches);
  /* CAPTURA + stopPropagation — o mesmo remédio do #generic-confirm-overlay
     (:2663, porquê em :2655) e a resposta pra classe que a issue #76 já pagou.

     O eixo que faltava não é "quais overlays estão abertos", é A ORDEM EM QUE OS
     LISTENERS FORAM REGISTRADOS: os `_ensure*Modal` são preguiçosos, então quem
     o usuário abriu primeiro na sessão roda primeiro, em bolha, no MESMO
     `document`. Com o detalhe de lançamento aberto POR CIMA desta lista, um Esc
     fazia (1) o handler do detalhe fechar e reabrir a lista SINCRONAMENTE e
     (2) o handler da lista, se registrado depois, ver `open` e fechá-la — um Esc
     levava os dois. Só acontecia se o usuário tivesse visto algum detalhe antes
     (Visão Geral/Histórico), que é a ordem de uso normal.

     Em captura este handler decide ANTES de qualquer bolha: com a lista
     escondida atrás do detalhe ele sai sem fazer nada, e a reabertura não é mais
     vista por ninguém. Não depende de ordem de registro — fecha a classe, não o
     caso. `stopPropagation` consome a tecla quando a lista É a de cima, pra não
     derrubar o que está atrás (o mesmo motivo do :2655). */
  document.addEventListener("keydown", e => {
    if (!ov.classList.contains("open")) return;
    if (e.key === "Escape") { e.stopPropagation(); closeCategoryLaunches(); return; }
    // Trap de foco: helper compartilhado do modals.js (`window.pigTrapTab`), o
    // mesmo que o detalhe de lançamento usa. O seletor dele já inclui
    // `[tabindex]:not([tabindex="-1"])`, então as linhas (role=button
    // tabindex=0) e o "Carregar mais" entram sozinhos — prender o Tab só no
    // Fechar tornaria a lista inalcançável por teclado. O `stopPropagation`
    // continua sendo daqui: é o que impede a tecla de chegar no diálogo de trás
    // (mesmo motivo do Esc acima).
    if (e.key !== "Tab") return;
    e.stopPropagation();
    if (window.pigTrapTab) window.pigTrapTab(e, ov.querySelector(".modal"));
  }, true);
}

function _hideCategoryLaunches() {
  const ov = document.getElementById("cat-launches-overlay");
  if (!ov) return;
  // O .overlay some com `display:none`, e display:none ZERA o scrollTop do
  // #cl-list. Guardar aqui (e não no show) é o único instante em que o valor
  // ainda existe.
  const list = document.getElementById("cl-list");
  _catLaunchesScroll = list ? list.scrollTop : 0;
  _catLaunchesHiddenFocus = ov.contains(document.activeElement) ? document.activeElement : null;
  ov.classList.remove("open");
}

/* Par do _hide: reexibe a lista QUE JÁ ESTÁ MONTADA, sem pedir nada ao
   servidor. É o caminho de volta do detalhe (o detalhe esconde a lista, não
   fecha). Reabrir por `openCategoryLaunches` jogava fora toda página anexada
   pelo "Carregar mais" (150 linhas voltavam a ser 50), o scroll ia pro topo e
   um refetch que falhasse trocava uma lista boa por uma mensagem de erro.
   Devolve false quando não sobrou o que reexibir (ctx zerado por
   Editar/Excluir, DOM sem linhas) — aí quem chama refaz o caminho antigo, que
   é o fallback, não o normal. */
function _showCategoryLaunches() {
  const ov = document.getElementById("cat-launches-overlay");
  if (!ov || !_catLaunchesCtx || !ov.querySelector(".cl-row")) return false;
  ov.classList.add("open");
  // Volta pra linha de onde o detalhe saiu; sem ela (linha some numa recarga),
  // o Fechar — o mesmo alvo que a abertura usa. `preventScroll` porque focar
  // rola o contêiner sozinho: medido, o scroll restaurado virava 518 em vez dos
  // 900 guardados (o foco puxava a linha pro topo).
  const alvo = _catLaunchesHiddenFocus && ov.contains(_catLaunchesHiddenFocus)
    ? _catLaunchesHiddenFocus : document.getElementById("cl-close");
  if (alvo) alvo.focus({ preventScroll: true });
  const list = document.getElementById("cl-list");
  if (list) list.scrollTop = _catLaunchesScroll;
  _catLaunchesHiddenFocus = null;
  return true;
}

/* `_catLaunchesCtx != null` significa UMA coisa só: existe uma lista pra onde
   voltar (visível, ou escondida atrás do detalhe). Todo caminho que abandona a
   lista de vez passa por aqui — fechar, e também Editar/Excluir, que trocam o
   detalhe pelo editor e nunca mais voltam. Sem isto o ctx e o foco guardado
   ficavam de pé indefinidamente depois de "Editar". Devolve o ctx antigo. */
function _forgetCategoryLaunches(restoreFocus = true) {
  const ctx = _catLaunchesCtx;
  _catLaunchesCtx = null;
  _catLaunchesRows = [];
  _catLaunchesTotal = 0;
  _catLaunchesCursor = null;
  _catLaunchesLoadingMore = false;
  _catLaunchesHiddenFocus = null;
  // `restoreFocus = false` quando outro modal assume a tela (Editar/Excluir):
  // devolver o foco pra linha do gráfico o deixaria ATRÁS do overlay novo.
  if (restoreFocus && _catLaunchesReturnFocus
      && typeof _catLaunchesReturnFocus.focus === "function") {
    _catLaunchesReturnFocus.focus();
  }
  _catLaunchesReturnFocus = null;
  return ctx;
}

function closeCategoryLaunches() {
  _hideCategoryLaunches();
  const ctx = _forgetCategoryLaunches();
  // Veio do modal de edição: devolve o usuário pra lá. O formulário é remontado
  // dos valores persistidos — nome digitado e não salvo se perde, que é o que
  // openCategoryEditModal já faz em toda abertura.
  if (ctx && ctx.backToEdit && _catEditCurrent) openCategoryEditModal(_catEditCurrent);
}

/* Caixa central da lista (carregando / vazio / erro). Mesmo padrão de estado
   vazio dos Cartões e dos Parcelamentos: sticker do Piggy, título e o que fazer
   pra sair do zero — aqui o caminho é mandar o gasto pro Piggy no WhatsApp,
   que é como o lançamento nasce. */
function _clBox(sticker, titulo, corpo, classe = "empty-sticker") {
  return `<div class="cl-box">
    <img class="${classe}" src="/brand/stickers/${sticker}.webp" alt="" />
    <div class="cl-box-t">${titulo}</div>
    ${corpo ? `<div>${corpo}</div>` : ""}
  </div>`;
}

/* Nome que a HASHTAG consegue carregar inteiro. É a MESMA classe de
   `_extract_explicit_category` (parsers.py:119, `#([a-zA-ZÀ-ÿ0-9_\-]+)`), e ela
   casa UM token: fora dela o `#` corta no primeiro caractere estranho e o resto
   do nome vira texto solto. Medido pelo `handle_incoming`
   (tests/test_categoria_frase_estado_vazio.py): "gastei 30 na loja
   #mcdonald's" grava a categoria "mcdonald" — a lista continua vazia E nasce
   uma categoria FANTASMA, que ainda vira barra na Distribuição. O mesmo com
   `uber/99` → "uber", `l'occitane` → "l", `cafe & cia` → "cafe".
   Testar só espaço (o que estava aqui) deixava passar ' / & % ( ) + e emoji. */
const _CAT_HASHTAG_OK = /^[a-zA-ZÀ-ÿ0-9_-]+$/;

/* A frase que o estado vazio ensina. Fonte única com
   tests/test_categoria_frase_estado_vazio.py, que a roda pelo `handle_incoming`.
   Fora da classe da hashtag sobra a MENÇÃO simples, que casa pela categoria do
   próprio usuário (`custom_category_match`) e — medido — nunca cria categoria
   fantasma: quando erra, cai em "outros", que já existe. */
function _catExemploFrase(nome) {
  return _CAT_HASHTAG_OK.test(nome)
    ? `gastei 30 na loja #${nome}`
    : `gastei 30 em ${nome}`;
}

/* Uma linha da lista. `base` é o índice GLOBAL da primeira: o onclick indexa
   `_catLaunchesRows`, então o "Carregar mais" tem que continuar a numeração em
   vez de recomeçar do zero. */
function _clRowsHtml(rows, base) {
  return rows.map((l, k) => {
    const i = base + k;
    const isCred = l.fonte === "credito";
    const isIn = l.tipo === "receita" || l.tipo === "entrada";
    const desc = describeLaunch(l).replace(/<[^>]+>/g, "").trim() || "—";
    // Mesma convenção de cor da Visão Geral: entrada verde, saída vermelha,
    // movimentação interna apagada (não é gasto, é dinheiro que mudou de lugar).
    const valClass = l.is_internal_movement ? "" : (isIn ? "g" : "r");
    // Índice, não o objeto: nenhum texto de usuário entra no atributo onclick.
    return `
      <div class="bar-row cl-row" role="button" tabindex="0"
           aria-label="${escapeHtmlSafe(`${desc}, ${_fmtBRL(l.valor)}, ${fmtLaunchWhen(l)}`)}"
           onclick="openCategoryLaunchDetail(${i})"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openCategoryLaunchDetail(${i});}">
        <div class="bar-icon">${isCred ? '<i class="ph ph-credit-card" aria-hidden="true"></i>' : phIcon(isIn ? "💰" : "💸")}</div>
        <div class="bar-body">
          <div class="bar-head">
            <span class="name">${escapeHtmlSafe(desc)}</span>
            <span class="val ${valClass}"${l.is_internal_movement ? ' style="color:var(--text-2)"' : ""}>${_fmtBRL(l.valor)}</span>
          </div>
          <div class="bar-sub">${escapeHtmlSafe(fmtLaunchWhen(l))}${isCred ? " • 💳 cartão" : (l.user_seq ? ` • #${l.user_seq}` : "")}${l.is_internal_movement ? " • movimentação interna" : ""}</div>
        </div>
      </div>`;
  }).join("");
}

/* Rodapé da lista: contagem à esquerda do Fechar, e o "Carregar mais" logo
   abaixo das linhas enquanto sobrar página. O pedido do dono era ver TODOS os
   lançamentos da categoria; "Mostrando 50 de 312" sem saída não era isso. */
function _clPintaRodape() {
  const n = _catLaunchesRows.length;
  const total = _catLaunchesTotal;
  document.getElementById("cl-foot").textContent = total > n
    ? `Mostrando ${n} de ${total} lançamentos`
    : `${total} lançamento${total === 1 ? "" : "s"}`;
  const more = document.getElementById("cl-more");
  // Sem cursor não há próxima página pra pedir (lista vazia, ou o servidor já
  // devolveu tudo): o botão pediria uma página que nunca vem.
  more.innerHTML = total > n && _catLaunchesCursor
    ? `<button type="button" class="mock-cta outline cl-more-btn" id="cl-more-btn"
               onclick="loadMoreCategoryLaunches()">Carregar mais ${Math.min(_CL_PAGE, total - n)}</button>`
    : "";
}

async function openCategoryLaunches(nome, opts) {
  _ensureCategoryLaunchesModal();
  const o = opts || {};
  // O ctx é o pedido: guarda os MESMOS filtros que foram pro servidor, pra
  // reabrir igual (voltar do detalhe) e pra servir de identidade da geração.
  const ctx = {
    nome,
    from: o.from || null,
    to: o.to || null,
    tipo: o.tipo || null,
    includeInternal: o.includeInternal !== false,
    backToEdit: !!o.backToEdit,
  };
  _catLaunchesCtx = ctx;
  _catLaunchesCursor = null;
  _catLaunchesLoadingMore = false;
  if (!_catLaunchesReturnFocus) _catLaunchesReturnFocus = document.activeElement;

  const list = document.getElementById("cl-list");
  const sum = document.getElementById("cl-sum");
  // Aberta pela Distribuição: mesmos filtros do gráfico (só despesa, sem
  // movimento interno). O subtítulo tem que dizer QUAL conjunto está na tela —
  // é o que impede a lista de parecer contradizer a barra que foi clicada.
  const doGrafico = ctx.tipo === "despesa" && !ctx.includeInternal;
  const mesLabel = ctx.from ? PT_MONTHS[Number(ctx.from.slice(5, 7)) - 1] : "";
  document.getElementById("cl-title").textContent = `Lançamentos em ${nome}`;
  // Subtítulo NEUTRO enquanto carrega: qual conjunto está na tela só se sabe
  // depois da resposta (o plano pode ter cortado a janela — ver `_clSubtitulo`).
  document.getElementById("cl-sub").textContent = doGrafico
    ? `Despesas de ${mesLabel}` : "Lançamentos desta categoria";
  document.getElementById("cl-foot").textContent = "";
  document.getElementById("cl-more").innerHTML = "";
  sum.hidden = true;
  list.innerHTML = _clBox("loading", "Carregando…", "", "loading-sticker");
  document.getElementById("cat-launches-overlay").classList.add("open");
  const closeBtn = document.getElementById("cl-close");
  if (closeBtn) closeBtn.focus();

  let data;
  try {
    data = await _catLaunchesChannel.run(
      (signal) => _catLaunchesFetch(ctx, null, signal), { force: true });
  } catch (err) {
    if (_catLaunchesCtx === ctx) {
      list.innerHTML = _clBox("thinking", "Não deu pra carregar",
                              escapeHtmlSafe(String(err.message || err)));
    }
    return;
  }
  // `undefined` = superado por uma abertura mais nova (o canal já abortou este).
  // ctx !== o atual = o usuário fechou (ESC) ou trocou de categoria enquanto
  // carregava: renderizar aqui povoaria um modal fechado, ou pintaria as linhas
  // de uma categoria sob o título de outra.
  if (data === undefined || _catLaunchesCtx !== ctx) return;

  // Sem adaptador: a rota manda as MESMAS chaves que a Visão Geral
  // (`posted_at` + `has_time` + `criado_em`), então `fmtLaunchWhen` imprime
  // "dd/mm, HH:MM" onde a hora é confiável e "dd/mm" onde só a data é. Mapear
  // `posted_at: r.data` era o que fazia esta lista nunca mostrar hora e imprimir
  // o dia do fuso da SESSÃO do Postgres, que na época era UTC — "09/03" aqui,
  // "10/03, 00:30" na Visão Geral, mesmo lançamento. A sessão hoje segue o fuso
  // do app (utils_date.align_process_tz), mas o mapeamento errado voltaria a
  // esconder a hora do mesmo jeito.
  // Isso vale para as linhas de `launches`. A compra no CRÉDITO CONTINUA
  // divergindo, e não é o front: a Visão Geral manda `has_time=true` +
  // `criado_em` = quando a LINHA foi gravada; esta lista manda `has_time=false`
  // + `posted_at` = quando a COMPRA aconteceu (docstring de
  // `list_launches_by_category`, db/accounts.py). Compra em 25/08 gravada em
  // 28/08 sai "25/08" aqui e "28/08, HH:MM" lá — medido. Alinhar as duas é
  // mudança de comportamento, não de comentário.
  // `nota`, `alvo` e `criado_em` vêm CRUS — o editor pré-preenche a partir deles,
  // e fabricar `nota` a partir de `descricao` (que é o ALVO quando existe)
  // gravava o alvo por cima da nota real ao salvar. `id` já vem nulo no crédito
  // (db/accounts.py), então o `editable` de _renderLaunchDetail esconde
  // Editar/Excluir sozinho.
  _catLaunchesRows = data.launches || [];
  _catLaunchesCursor = data.next_cursor || null;

  const resumo = data.resumo || { n_total: 0, despesa: 0, receita: 0 };
  _catLaunchesTotal = resumo.n_total || 0;
  const win = data.window || {};
  document.getElementById("cl-sub").textContent =
    _clSubtitulo(doGrafico, mesLabel, win);

  if (!_catLaunchesRows.length) {
    /* A frase tem que produzir o que promete — e a promessa é o que muda aqui.
       `_catExemploFrase` escolhe hashtag ou menção pelo que o parser aguenta, e
       a segunda linha diz o que fazer quando ele erra mesmo assim: existe nome
       (ex.: "b+c") em que nenhuma das duas casa, e aí o gasto cai em "outros".
       Prometer "e o lançamento aparece aqui" naquele caso era a tela mentindo
       pela terceira vez. */
    list.innerHTML = _clBox("point",
      doGrafico ? `Nada em ${escapeHtmlSafe(nome)} em ${mesLabel}` : "Categoria ainda vazia",
      `Mande <b>“${escapeHtmlSafe(_catExemploFrase(nome))}”</b> pro Piggy no WhatsApp. ` +
      `Se ele mandar pra outra categoria, dá pra trocar no próprio lançamento.`);
    return;
  }

  list.innerHTML = _clRowsHtml(_catLaunchesRows, 0);

  // O número que o usuário veio buscar fica ACIMA da lista, em corpo grande:
  // no dashboard clareza financeira ganha da decoração. A contagem é metadado
  // e desce pro rodapé, ao lado do Fechar.
  // Categoria só de receita (salário, rendimentos) não abre com "Saídas R$ 0,00"
  // em corpo grande — o número que importa ali é a entrada.
  const saidas = resumo.despesa > 0 || resumo.receita === 0
    ? `<span><span class="cl-k">${doGrafico ? `Gasto em ${mesLabel}` : "Saídas"}</span>` +
      `<b>${_fmtBRL(resumo.despesa)}</b></span>`
    : "";
  sum.innerHTML = saidas + (resumo.receita > 0
    ? `<span class="cl-in"><span class="cl-k">Entradas</span><b>${_fmtBRL(resumo.receita)}</b></span>`
    : "");
  sum.hidden = false;
  // `resumo` cobre TODAS as linhas que casam (window aggregate ANTES do LIMIT),
  // não só as que couberam na página — é o que faz "Mostrando 50 de 312" e o
  // total em cima continuarem verdadeiros com paginação.
  _clPintaRodape();
}

/* O pedido, em um lugar só: abertura e "carregar mais" mandam os MESMOS
   filtros, mudando só o offset. Duas cópias da URL era o jeito de a página 2
   vir com filtro diferente da 1. */
async function _catLaunchesFetch(ctx, cursor, signal) {
  const q = new URLSearchParams({ categoria: ctx.nome, limit: String(_CL_PAGE) });
  if (cursor) q.set("cursor", cursor);
  if (ctx.from) q.set("from", ctx.from);
  if (ctx.to) q.set("to", ctx.to);
  if (ctx.tipo) q.set("tipo", ctx.tipo);
  if (!ctx.includeInternal) q.set("include_internal", "false");
  const resp = await fetch(`${API}/categories/${USER_ID}/launches?${q}`,
                           { credentials: "same-origin", signal });
  // readApiError: o 402 do gate de plano vem como
  // {"detail":{"error":"subscription_required"}} e resp.text() jogava esse
  // JSON cru na cara do usuário.
  if (!resp.ok) throw new Error(await readApiError(resp));
  return await resp.json();
}

/* O subtítulo tem que ser VERDADE, e ele deixou de ser quando a rota passou a
   cortar a janela pelo teto de histórico do plano (`history_earliest_date`,
   core/services/plan_service.py). Numa conta Grátis o corte é
   `history_current_month_only` → dia 1 do mês, e a tela dizia "Tudo nesta
   categoria" mostrando UM MÊS. `capped_by_plan` vem da rota justamente porque
   `window.from` sozinho não distingue "o usuário pediu este mês" de "o plano
   cortou". Sem upsell: é o fato e a data, que é o que o dono pediu. */
function _clSubtitulo(doGrafico, mesLabel, win) {
  const desde = win.capped_by_plan && win.from
    ? win.from.split("-").reverse().join("/") : "";
  if (desde) {
    return doGrafico
      ? `Despesas de ${mesLabel} — seu plano guarda o histórico desde ${desde}.`
      : `Despesas, receitas e movimentações internas desde ${desde} — é até onde seu plano guarda o histórico.`;
  }
  return doGrafico
    ? `Despesas de ${mesLabel} — o mesmo conjunto que a barra do gráfico soma.`
    : "Tudo nesta categoria: despesas, receitas e movimentações internas.";
}

/* "Carregar mais": ANEXA a próxima página, nunca substitui.

   As corridas, enumeradas antes de escrever (a lista tem UM canal de fetch, que
   aborta o pedido anterior — `makeFetchChannel`):
   • clique repetido em "Carregar mais" → o `_catLaunchesLoadingMore` recusa o
     segundo antes de chegar no canal. Sem ele os dois pediriam o MESMO offset
     (a lista ainda não cresceu) e o segundo abortaria o primeiro — não duplica,
     mas o botão piscava sem motivo.
   • trocar de categoria com uma página em voo → a abertura nova roda no mesmo
     canal e aborta esta; se ainda assim a resposta chegar, `_catLaunchesCtx !==
     ctx` recusa o append. Sem essa guarda, linhas de "mercado" entrariam sob o
     título "saúde".
   • fechar (Esc) durante o voo → mesmo `ctx` diferente (o close zera), o append
     não acontece e o modal fechado não é repovoado.
   • lançamento novo pelo WhatsApp com a lista aberta → o cursor é KEYSET
     (`db/accounts.py`): a próxima página é o que vem DEPOIS da última linha já
     na tela, então a linha nova (que entra no topo) não desloca fronteira
     nenhuma. Com OFFSET a página 2 repetia a última da 1 e comia outra. */
async function loadMoreCategoryLaunches() {
  const ctx = _catLaunchesCtx;
  if (!ctx || _catLaunchesLoadingMore || !_catLaunchesCursor) return;
  const btn = document.getElementById("cl-more-btn");
  const cursor = _catLaunchesCursor;
  _catLaunchesLoadingMore = true;
  if (btn) { btn.disabled = true; btn.textContent = "Carregando…"; }

  let data;
  try {
    data = await _catLaunchesChannel.run(
      (signal) => _catLaunchesFetch(ctx, cursor, signal), { force: true });
  } catch (err) {
    _catLaunchesLoadingMore = false;
    if (_catLaunchesCtx !== ctx) return;   // lista trocou/fechou: não pinta nada
    // Repinta o botão (o "Carregando…" volta a ser clicável) e põe o motivo
    // embaixo: o usuário tem que poder tentar de novo sem reabrir a lista.
    _clPintaRodape();
    const m = document.getElementById("cl-more");
    if (m) m.insertAdjacentHTML("beforeend",
      `<div class="cl-more-err">${escapeHtmlSafe(String(err.message || err))}</div>`);
    return;
  }
  _catLaunchesLoadingMore = false;
  if (data === undefined || _catLaunchesCtx !== ctx) return;

  const novas = data.launches || [];
  const base = _catLaunchesRows.length;
  _catLaunchesRows = _catLaunchesRows.concat(novas);
  // Página vazia não move o cursor: `next_cursor` vem nulo e o botão some pelo
  // ajuste do total logo abaixo.
  _catLaunchesCursor = data.next_cursor || null;
  // O total vem do servidor a cada página: se alguém lançou/apagou no meio, o
  // rodapé segue o número REAL em vez de um contador congelado na 1ª página.
  _catLaunchesTotal = (data.resumo && data.resumo.n_total) || _catLaunchesTotal;
  if (novas.length) {
    document.getElementById("cl-list")
            .insertAdjacentHTML("beforeend", _clRowsHtml(novas, base));
  }
  // Página vazia com total dizendo que havia mais (linha apagada entre os dois
  // pedidos): sem isto o botão ficaria de pé pedindo uma página que não vem.
  if (!novas.length) _catLaunchesTotal = _catLaunchesRows.length;
  _clPintaRodape();
  // O botão sumiu (acabou) → o foco ficaria no nada, e o trap de Tab devolveria
  // pro topo do diálogo. Manda pra primeira linha nova, que é o que o usuário
  // acabou de pedir.
  const aindaTem = document.getElementById("cl-more-btn");
  if (aindaTem) aindaTem.focus();
  else {
    const linhas = document.querySelectorAll("#cl-list .cl-row");
    if (linhas[base]) linhas[base].focus();
  }
}

function openCategoryLaunchDetail(idx) {
  const l = _catLaunchesRows[idx];
  if (!l) return;
  _launchDetailCurrent = l;
  _launchDetailSource = "category";
  _hideCategoryLaunches();   // um overlay por vez; closeLaunchDetail reabre
  _renderLaunchDetail(l);
}

// ── Modal cadastrar/editar categoria ──────────────────────────────────

let _catEditState = { id: null, emoji: "🏷️", color: "#FF2D8E", is_system: false };
let _catEditCurrent = null;   // objeto recebido por openCategoryEditModal, pro retorno da lista

function _ensureCategoryModal() {
  if (document.getElementById("cat-edit-overlay")) return;
  const html = `
    <div class="overlay" id="cat-edit-overlay">
      <div class="modal wide">
        <h3 id="cat-edit-title">Nova categoria</h3>
        <!-- Entrada da lista de lançamentos: fica ANTES do formulário de
             aparência de propósito — "o que tem nesta categoria" vem antes de
             "que cor ela tem", e embaixo (junto de Salvar/Excluir) parecia uma
             ação de formulário, que não é. -->
        <div id="cat-edit-launches-row" class="cl-open-row" style="display:none">
          <button type="button" class="mock-cta outline" onclick="categoryLaunchesFromModal()"><i class="ph ph-receipt" aria-hidden="true"></i> Ver lançamentos</button>
        </div>
        <form id="cat-edit-form" onsubmit="event.preventDefault(); saveCategoryFromModal();">
          <div class="invest-form">
            <div class="field">
              <label for="cat-edit-name">Nome</label>
              <input type="text" id="cat-edit-name" maxlength="40" required placeholder="Ex: Streaming" />
            </div>
            <div class="field">
              <label>Emoji</label>
              <div id="cat-emoji-picker" style="display:flex;gap:6px;flex-wrap:wrap"></div>
            </div>
            <div class="field">
              <label>Cor</label>
              <div id="cat-color-picker" style="display:flex;gap:8px;flex-wrap:wrap"></div>
            </div>
          </div>
          <div class="modal-acts" style="margin-top:18px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button type="button" class="mock-cta outline" id="cat-edit-archive-btn" style="display:none" onclick="categoryArchiveFromModal()"><i class="ph ph-archive" aria-hidden="true"></i> Arquivar</button>
            <button type="button" class="mock-cta outline" id="cat-edit-unarchive-btn" style="display:none" onclick="categoryUnarchiveFromModal()">↩️ Desarquivar</button>
            <button type="button" class="inst-delete-btn" id="cat-edit-delete-btn" style="display:none" onclick="categoryDeleteFromModal()"><i class="ph ph-trash" aria-hidden="true"></i> Excluir</button>
            <span style="flex:1"></span>
            <button type="button" class="btn-cancel" onclick="closeCategoryEditModal()">Cancelar</button>
            <button type="submit" class="btn-save">Salvar</button>
          </div>
        </form>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML("beforeend", html);
  document.getElementById("cat-edit-overlay").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeCategoryEditModal();
  });
}

function _renderCategoryPickers() {
  const ePick = document.getElementById("cat-emoji-picker");
  const cPick = document.getElementById("cat-color-picker");
  ePick.innerHTML = CATEGORY_EMOJI_OPTIONS.map(e => {
    const sel = e === _catEditState.emoji;
    return `<button type="button" onclick="_setCatEmoji('${e}')"
      style="width:36px;height:36px;border-radius:8px;font-size:1.2rem;cursor:pointer;
             border:2px solid ${sel ? "#fff" : "transparent"};
             background:${sel ? "rgba(255,45,142,.25)" : "var(--glass-bg)"};
             display:flex;align-items:center;justify-content:center">${phIcon(e)}</button>`;
  }).join("");
  cPick.innerHTML = CATEGORY_COLOR_OPTIONS.map(c => {
    const sel = c === _catEditState.color;
    return `<button type="button" onclick="_setCatColor('${c}')"
      style="width:32px;height:32px;border-radius:8px;cursor:pointer;
             border:2px solid ${sel ? "#fff" : "transparent"};
             background:${c};
             box-shadow:${sel ? "0 0 0 2px rgba(255,45,142,.5)" : "none"}"></button>`;
  }).join("");
}
function _setCatEmoji(e) { _catEditState.emoji = e; _renderCategoryPickers(); }
function _setCatColor(c) { _catEditState.color = c; _renderCategoryPickers(); }

function openCategoryEditModal(category) {
  _ensureCategoryModal();
  const isEdit = category && category.id;
  _catEditCurrent = isEdit ? category : null;
  _catEditState = {
    id: isEdit ? category.id : null,
    emoji: isEdit ? category.emoji : "🏷️",
    color: isEdit ? category.color : "#FF2D8E",
    is_system: isEdit ? !!category.is_system : false,
    is_archived: isEdit ? !!category.is_archived : false,
    original_name: isEdit ? category.name : "",
    usage_count: isEdit ? category.usage_count : 0,
  };
  document.getElementById("cat-edit-title").textContent = isEdit ? "Editar categoria" : "Nova categoria";
  document.getElementById("cat-edit-name").value = isEdit ? category.name : "";
  document.getElementById("cat-edit-archive-btn").style.display = (isEdit && !category.is_archived) ? "" : "none";
  document.getElementById("cat-edit-unarchive-btn").style.display = (isEdit && category.is_archived) ? "" : "none";
  document.getElementById("cat-edit-delete-btn").style.display = (isEdit && !category.is_system) ? "" : "none";
  // "Nova categoria" não tem lançamento pra listar.
  document.getElementById("cat-edit-launches-row").style.display = isEdit ? "" : "none";
  _renderCategoryPickers();
  document.getElementById("cat-edit-overlay").classList.add("open");
  setTimeout(() => document.getElementById("cat-edit-name").focus(), 50);
}

function closeCategoryEditModal() {
  const el = document.getElementById("cat-edit-overlay");
  if (el) el.classList.remove("open");
}

// Sem janela de data: a categoria inteira, 50 linhas por página ("Carregar
// mais" traz o resto). O plano ainda pode cortar o INÍCIO da janela
// (`history_earliest_date`) — quando corta, o subtítulo diz desde quando.
function categoryLaunchesFromModal() {
  const cat = _catEditCurrent;
  if (!cat) return;
  closeCategoryEditModal();
  openCategoryLaunches(cat.name, { backToEdit: true });
}

async function saveCategoryFromModal() {
  const name = (document.getElementById("cat-edit-name").value || "").trim();
  if (!name) { await alertModal("Digite um nome.", { title: "Nome obrigatório" }); return; }
  const isEdit = !!_catEditState.id;
  try {
    const url = isEdit
      ? `${API}/categories/${USER_ID}/${_catEditState.id}`
      : `${API}/categories/${USER_ID}`;
    const method = isEdit ? "PATCH" : "POST";
    const resp = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, emoji: _catEditState.emoji, color: _catEditState.color }),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      if (resp.status === 403) {
        await alertModal("Categorias customizadas são exclusivas do Pro.", { title: "Recurso Pro" });
        return;
      }
      throw new Error(detail);
    }
    closeCategoryEditModal();
    showToast(isEdit ? "✓ Categoria atualizada" : "✓ Categoria criada");
    await loadCategoriesView(true);
    sendRefresh();
  } catch (err) {
    alert(String(err.message || err));
  }
}

async function categoryArchiveFromModal() {
  if (!_catEditState.id) return;
  try {
    const r = await fetch(`${API}/categories/${USER_ID}/${_catEditState.id}/archive`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!r.ok) throw new Error(await r.text());
    closeCategoryEditModal();
    showToast("✓ Categoria arquivada");
    await loadCategoriesView(true);
  } catch (err) { await alertModal(String(err.message || err), { title: "Erro" }); }
}

async function categoryUnarchiveFromModal() {
  if (!_catEditState.id) return;
  try {
    const r = await fetch(`${API}/categories/${USER_ID}/${_catEditState.id}/unarchive`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!r.ok) throw new Error(await r.text());
    closeCategoryEditModal();
    showToast("✓ Categoria desarquivada");
    await loadCategoriesView(true);
  } catch (err) { await alertModal(String(err.message || err), { title: "Erro" }); }
}

async function categoryDeleteFromModal() {
  if (!_catEditState.id || _catEditState.is_system) return;
  const ok = await confirmModal(
    `Excluir a categoria "${_catEditState.original_name}"? Isso só funciona se ela não tiver lançamentos.`,
    { title: "Excluir categoria", okText: "Excluir", danger: true },
  );
  if (!ok) return;
  try {
    const r = await fetch(`${API}/categories/${USER_ID}/${_catEditState.id}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!r.ok) {
      const txt = await r.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(detail);
    }
    closeCategoryEditModal();
    showToast("✓ Categoria excluída");
    await loadCategoriesView(true);
  } catch (err) { await alertModal(String(err.message || err), { title: "Erro" }); }
}

// ══════════════════════════════════════════════════════════════════════
// Orçamentos (view dinâmica — semáforo real)
// ══════════════════════════════════════════════════════════════════════

async function _fetchBudgetsStatus(month, { force = false } = {}) {
  return _budgetsChannel.run(async (signal) => {
    const q = month ? `?month=${encodeURIComponent(month)}` : "";
    const resp = await fetch(`${API}/budgets/${USER_ID}/status${q}`, {
      credentials: "same-origin",
      headers: csrfHeaders(),
      signal,
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(`(HTTP ${resp.status}) ${detail}`);
    }
    return await resp.json();
  }, { force });
}

async function loadBudgetsView(forceFresh = false, { background = false } = {}) {
  const list = document.getElementById("budgets-list");
  const stats = document.getElementById("budgets-stats");
  const title = document.getElementById("budgets-title");
  if (!list || !stats) return;
  if (!USER_ID) {
    if (background) throw new Error("orçamentos: sessão ainda não pronta");
    list.innerHTML = '<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Conectando…</div>';
    setTimeout(() => loadBudgetsView(forceFresh), 300);
    return;
  }

  // Puxão: sem skeleton, fetch antes de render, falha real rejeita sem tocar DOM.
  if (background) {
    const data = await _fetchBudgetsStatus(undefined, { force: true });
    if (data === undefined) return;
    _budgetsStatusCache = data;
    renderBudgetsView(data);
    return;
  }

  if (_budgetsStatusCache && !forceFresh) {
    renderBudgetsView(_budgetsStatusCache);
    _fetchBudgetsStatus().then(fresh => {
      if (fresh) { _budgetsStatusCache = fresh; renderBudgetsView(fresh); }
    }).catch(() => {});
    return;
  }

  list.innerHTML = '<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Carregando orçamentos…</div>';
  stats.innerHTML = `
    <div class="stat-tile"><div class="stat-label">Orçamento total</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Gasto este mês</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Disponível</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Em risco</div><div class="sk sk-h2"></div></div>
  `;

  try {
    const data = await _fetchBudgetsStatus(undefined, { force: true });
    if (data === undefined) return;
    _budgetsStatusCache = data;
    renderBudgetsView(data);
  } catch (err) {
    list.innerHTML = `<div class="empty" style="padding:20px;color:var(--red)">Erro: ${escapeHtmlSafe(String(err.message || err))}</div>`;
    stats.innerHTML = "";
  }
}

function renderBudgetsView(payload) {
  const list = document.getElementById("budgets-list");
  const stats = document.getElementById("budgets-stats");
  const title = document.getElementById("budgets-title");
  if (!list || !stats) return;

  const t = payload.totals || { budget: 0, spent: 0, pct: 0, remaining: 0, at_risk: 0 };
  const buds = payload.budgets || [];

  // Título com mês legível
  if (title && payload.month) {
    try {
      const [y, m] = payload.month.split("-").map(Number);
      const monthName = new Date(y, m - 1, 1).toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
      title.textContent = `Orçamentos · ${monthName}`;
    } catch (_) {}
  }

  const pctColor = t.pct >= 100 ? "var(--red)" : (t.pct >= 80 ? "#fbbf24" : "var(--green)");
  stats.innerHTML = `
    <div class="stat-tile" style="animation-delay:0ms">
      <div class="stat-label">Orçamento total</div>
      <div class="stat-value">${_fmtBRL(t.budget)}</div>
      <div class="stat-delta" style="color:var(--text-3)">${buds.length} categoria${buds.length === 1 ? "" : "s"}</div>
    </div>
    <div class="stat-tile" style="animation-delay:60ms">
      <div class="stat-label">Gasto este mês</div>
      <div class="stat-value" style="color:${pctColor}">${_fmtBRL(t.spent)}</div>
      <div class="stat-delta" style="color:var(--text-3)">${t.pct.toFixed(1).replace(".", ",")}% do orçamento</div>
    </div>
    <div class="stat-tile" style="animation-delay:120ms">
      <div class="stat-label">Disponível</div>
      <div class="stat-value" style="color:${t.remaining >= 0 ? "var(--green)" : "var(--red)"}">${_fmtBRL(t.remaining)}</div>
      <div class="stat-delta" style="color:var(--text-3)">${t.remaining >= 0 ? "no caminho" : "estourou"}</div>
    </div>
    <div class="stat-tile" style="animation-delay:180ms">
      <div class="stat-label">Em risco</div>
      <div class="stat-value" style="color:${t.at_risk > 0 ? "#fbbf24" : "var(--text-3)"}">${t.at_risk}</div>
      <div class="stat-delta" style="color:var(--text-3)">categoria(s) ≥ 80%</div>
    </div>
  `;

  if (buds.length === 0) {
    list.innerHTML = `
      <div class="empty" style="padding:30px;text-align:center;color:var(--text-3)">
        <img class="empty-sticker" src="/brand/stickers/report.webp" alt="" />
        <div style="font-size:1.05rem;font-weight:700;color:var(--text);margin-bottom:6px">Nenhum orçamento ainda</div>
        <div style="margin-bottom:14px">Defina um limite mensal por categoria e o Piggy avisa quando você se aproxima.</div>
        <button class="mock-cta" onclick="openBudgetEditModal()">+ Criar orçamento</button>
      </div>
    `;
    return;
  }

  list.innerHTML = buds.map((b, i) => _renderBudgetRow(b, i)).join("");
}

function _renderBudgetRow(b, idx = 0) {
  const pct = b.pct || 0;
  const fillClass = b.status === "vermelho" ? "red" : (b.status === "amarelo" ? "yellow" : "green");
  const widthPct = Math.min(100, pct);
  const subColor = b.status === "vermelho" ? "color:#FF2D2D" : "";
  const dotEmoji = `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:middle;margin-right:5px;background:${b.status === "vermelho" ? "#ef4444" : (b.status === "amarelo" ? "#fbbf24" : "#22c55e")}"></span>`;
  let subText = `${pct.toFixed(0)}%, ${_fmtBRL(b.remaining)} restantes`;
  if (b.status === "vermelho") {
    subText = `<i class="ph ph-warning" aria-hidden="true"></i> ${pct.toFixed(0)}%, estourou ${_fmtBRL(-b.remaining)}`;
  } else if (b.status === "amarelo") {
    subText = `${pct.toFixed(0)}%, ${_fmtBRL(b.remaining)} restantes. Piggy te avisa via WhatsApp.`;
  }
  const safeCatJson = escapeHtmlSafe(JSON.stringify(b));
  const delay = 240 + idx * 60;
  return `
    <div class="bar-row" style="cursor:pointer;animation-delay:${delay}ms" onclick='openBudgetEditModal(${safeCatJson})'>
      <div class="bar-icon" style="color:${escapeHtmlSafe(b.color)}">${phIcon(b.emoji)}</div>
      <div class="bar-body">
        <div class="bar-head"><span class="name">${dotEmoji} ${escapeHtmlSafe(b.categoria)}</span><span class="val">${_fmtBRL(b.spent)} / ${_fmtBRL(b.budget)}</span></div>
        <div class="bar-track"><div class="bar-fill ${fillClass}" style="width:${widthPct.toFixed(1)}%"></div></div>
        <div class="bar-sub" style="${subColor}">${subText}</div>
      </div>
    </div>
  `;
}

// ── Modal cadastrar/editar orçamento ──────────────────────────────────

let _budgetEditState = { categoria: null, budget: null };

function _ensureBudgetModal() {
  if (document.getElementById("budget-edit-overlay")) return;
  const html = `
    <div class="overlay" id="budget-edit-overlay">
      <div class="modal">
        <h3 id="budget-edit-title">Novo orçamento</h3>
        <form id="budget-edit-form" onsubmit="event.preventDefault(); saveBudgetFromModal();">
          <div class="invest-form">
            <div class="field">
              <label for="budget-edit-cat">Categoria</label>
              <select id="budget-edit-cat"></select>
            </div>
            <div class="field">
              <label for="budget-edit-amount">Limite mensal (R$)</label>
              <input type="number" id="budget-edit-amount" min="0" step="0.01" required placeholder="0,00" />
            </div>
          </div>
          <div class="modal-acts" style="margin-top:18px;display:flex;gap:8px;align-items:center">
            <button type="button" class="inst-delete-btn" id="budget-edit-delete-btn" style="display:none" onclick="budgetDeleteFromModal()"><i class="ph ph-trash" aria-hidden="true"></i> Excluir</button>
            <span style="flex:1"></span>
            <button type="button" class="btn-cancel" onclick="closeBudgetEditModal()">Cancelar</button>
            <button type="submit" class="btn-save">Salvar</button>
          </div>
        </form>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML("beforeend", html);
  document.getElementById("budget-edit-overlay").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeBudgetEditModal();
  });
}

async function openBudgetEditModal(budget) {
  _ensureBudgetModal();
  const isEdit = budget && budget.categoria;
  _budgetEditState = {
    categoria: isEdit ? budget.categoria : null,
    budget: isEdit ? budget.budget : null,
  };
  document.getElementById("budget-edit-title").textContent = isEdit ? "Editar orçamento" : "Novo orçamento";
  document.getElementById("budget-edit-amount").value = isEdit ? Number(budget.budget).toFixed(2) : "";
  document.getElementById("budget-edit-delete-btn").style.display = isEdit ? "" : "none";

  const sel = document.getElementById("budget-edit-cat");
  if (isEdit) {
    sel.innerHTML = `<option value="${escapeHtmlSafe(budget.categoria)}" selected>${escapeHtmlSafe(budget.categoria)}</option>`;
    sel.disabled = true;
  } else {
    sel.disabled = false;
    sel.innerHTML = '<option value="">— Carregando…</option>';
    try {
      const cats = await _fetchCategories(false, { direct: true });
      const usedCats = new Set((_budgetsStatusCache?.budgets || []).map(b => (b.categoria || "").toLowerCase()));
      const options = (cats || [])
        .filter(c => !c.is_archived)
        .filter(c => !usedCats.has((c.name || "").toLowerCase()))
        .map(c => `<option value="${escapeHtmlSafe(c.name)}">${escapeHtmlSafe(c.name)}</option>`)
        .join("");
      sel.innerHTML = options || '<option value="">— Todas categorias já têm orçamento —</option>';
    } catch (err) {
      sel.innerHTML = `<option value="">Erro: ${escapeHtmlSafe(String(err.message || err))}</option>`;
    }
  }

  document.getElementById("budget-edit-overlay").classList.add("open");
  setTimeout(() => document.getElementById("budget-edit-amount").focus(), 50);
}

function closeBudgetEditModal() {
  const el = document.getElementById("budget-edit-overlay");
  if (el) el.classList.remove("open");
}

async function saveBudgetFromModal() {
  const categoria = document.getElementById("budget-edit-cat").value;
  const amount = parseFloat(document.getElementById("budget-edit-amount").value);
  if (!categoria) { await alertModal("Escolha uma categoria.", { title: "Categoria obrigatória" }); return; }
  if (!amount || amount <= 0) { await alertModal("Digite um valor maior que zero.", { title: "Valor inválido" }); return; }
  try {
    const resp = await fetch(`${API}/budgets/${USER_ID}`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ categoria, budget: amount }),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try {
        const parsed = JSON.parse(txt);
        if (parsed.detail?.error === "pro_required") {
          await alertModal(
            `Free permite até ${parsed.detail.limit || 3} orçamentos. Faça upgrade pra criar mais.`,
            { title: "Recurso Pro" },
          );
          return;
        }
        detail = parsed.detail || txt;
      } catch(_) {}
      throw new Error(detail);
    }
    closeBudgetEditModal();
    showToast("✓ Orçamento salvo");
    await loadBudgetsView(true);
    sendRefresh();
  } catch (err) { await alertModal(String(err.message || err), { title: "Erro" }); }
}

async function budgetDeleteFromModal() {
  if (!_budgetEditState.categoria) return;
  const ok = await confirmModal(
    `Excluir o orçamento de "${_budgetEditState.categoria}"?`,
    { title: "Excluir orçamento", okText: "Excluir", danger: true },
  );
  if (!ok) return;
  try {
    const r = await fetch(`${API}/budgets/${USER_ID}/${encodeURIComponent(_budgetEditState.categoria)}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!r.ok) throw new Error(await r.text());
    closeBudgetEditModal();
    showToast("✓ Orçamento excluído");
    await loadBudgetsView(true);
  } catch (err) { await alertModal(String(err.message || err), { title: "Erro" }); }
}

// ══════════════════════════════════════════════════════════════════════
// Modal genérico de alerta/confirmação (substitui alert()/confirm() nativos)
// ══════════════════════════════════════════════════════════════════════

let _genericModalResolver = null;
let _genericModalLastFocus = null;

function _ensureGenericConfirmModal() {
  if (document.getElementById("generic-confirm-overlay")) return;
  const html = `
    <div class="overlay" id="generic-confirm-overlay">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="generic-confirm-title">
        <h3 id="generic-confirm-title">Confirmar</h3>
        <p class="msub" id="generic-confirm-body" style="white-space:pre-wrap"></p>
        <div class="modal-acts" style="margin-top:18px">
          <button type="button" class="btn-cancel" id="generic-confirm-cancel">Cancelar</button>
          <button type="button" class="btn-save" id="generic-confirm-ok">OK</button>
        </div>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML("beforeend", html);
  document.getElementById("generic-confirm-overlay").addEventListener("click", e => {
    if (e.target === e.currentTarget) _genericModalClose(false);
  });
  document.getElementById("generic-confirm-cancel").addEventListener("click", () => _genericModalClose(false));
  document.getElementById("generic-confirm-ok").addEventListener("click", () => _genericModalClose(true));

  /* Este arquivo REDECLARA confirmModal/alertModal, e a dashboard.html carrega
     dashboard.js depois de modals.js — então no dashboard quem roda é este
     modal aqui, e o trap que o modals.js ganhou nunca era alcançado. Era o
     furo que o Codex achou: o helper compartilhado cobria home e settings, e
     deixava de fora justamente a página com mais chamadas de confirm/alert.

     O trap vem do window.pigTrapTab, exposto pelo modals.js, pra não virar a
     quarta cópia do mesmo bloco. Se por algum motivo o modals.js não tiver
     carregado, o Esc continua funcionando e o Tab só não fica preso.

     CAPTURE + stopPropagation, e isso importa: este listener é registrado
     preguiçosamente, na primeira chamada de confirmModal(), então em fase de
     bolha ele rodaria DEPOIS dos listeners de Escape que a página já tinha —
     em especial o de :9178, que fecha os modais de fatura sem checar nada.
     O submitPayBill() abre esta confirmação COM o overlay de pagamento aberto
     atrás; um Esc pra dispensar o aviso fecharia junto o fluxo de pagamento e
     o valor digitado. Em captura, este handler vê a tecla primeiro e a
     consome, então o Esc fecha só a confirmação. */
  document.addEventListener("keydown", (e) => {
    const ov = document.getElementById("generic-confirm-overlay");
    if (!ov || !ov.classList.contains("open")) return;
    if (e.key === "Escape") {
      e.stopPropagation();
      _genericModalClose(false);
      return;
    }
    if (window.pigTrapTab) window.pigTrapTab(e, ov.querySelector(".modal"));
  }, true);
}

function _genericModalClose(value) {
  const overlay = document.getElementById("generic-confirm-overlay");
  if (overlay) overlay.classList.remove("open");
  // devolve o foco pra quem abriu, senão o teclado volta pro topo do dashboard
  if (_genericModalLastFocus && document.contains(_genericModalLastFocus)) {
    _genericModalLastFocus.focus();
  }
  _genericModalLastFocus = null;
  if (_genericModalResolver) {
    const r = _genericModalResolver;
    _genericModalResolver = null;
    r(value);
  }
}

function confirmModal(message, opts = {}) {
  _ensureGenericConfirmModal();
  const title = opts.title || "Confirmar";
  const okText = opts.okText || "OK";
  const cancelText = opts.cancelText || "Cancelar";
  const danger = !!opts.danger;
  document.getElementById("generic-confirm-title").textContent = title;
  document.getElementById("generic-confirm-body").textContent = message || "";
  const cancelBtn = document.getElementById("generic-confirm-cancel");
  const okBtn = document.getElementById("generic-confirm-ok");
  cancelBtn.textContent = cancelText;
  cancelBtn.style.display = "";
  okBtn.textContent = okText;
  okBtn.className = danger ? "inst-delete-btn" : "btn-save";
  _genericModalLastFocus = document.activeElement;
  document.getElementById("generic-confirm-overlay").classList.add("open");
  okBtn.focus();
  return new Promise(resolve => { _genericModalResolver = resolve; });
}

function alertModal(message, opts = {}) {
  _ensureGenericConfirmModal();
  const title = opts.title || "Aviso";
  const okText = opts.okText || "OK";
  document.getElementById("generic-confirm-title").textContent = title;
  document.getElementById("generic-confirm-body").textContent = message || "";
  const cancelBtn = document.getElementById("generic-confirm-cancel");
  const okBtn = document.getElementById("generic-confirm-ok");
  cancelBtn.style.display = "none";
  okBtn.textContent = okText;
  okBtn.className = "btn-save";
  _genericModalLastFocus = document.activeElement;
  document.getElementById("generic-confirm-overlay").classList.add("open");
  okBtn.focus();
  return new Promise(resolve => { _genericModalResolver = () => resolve(undefined); _genericModalResolver = resolve; });
}

// ══════════════════════════════════════════════════════════════════════
// Metas (Sprint 5 — cada caixinha pode ter target_amount/date opcional)
// ══════════════════════════════════════════════════════════════════════

const GOAL_EMOJI_OPTIONS = [
  "🎯","💰","🛟","✈️","🏠","💻","📱","🚗","💍","🎓",
  "🎂","👶","🐶","💼","💎","🎮","📸","🎸","🛏️","🏖️",
];
const GOAL_COLOR_OPTIONS = [
  "#FF2D8E","#5FA83C","#2E7FE0","#E84545","#12A892",
  "#BE8200","#7E5FE6","#E85F2A","#22C3D6","#94A3B8",
];

function _cdiPercentFromRate(rate) {
  const pct = Number(rate == null ? 1 : rate) * 100;
  return Number.isFinite(pct) && pct > 0 ? pct : 100;
}

function _formatCdiRate(rate) {
  const pct = _cdiPercentFromRate(rate);
  return `Rende ${pct.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}% do CDI · simulado`;
}

let _goalsCache = null;
const _goalsChannel = makeFetchChannel(); // dedup + abort + geração

async function _fetchGoalsStatus({ force = false } = {}) {
  return _goalsChannel.run(async (signal) => {
    const resp = await fetch(`${API}/goals/${USER_ID}/status`, {
      credentials: "same-origin",
      headers: csrfHeaders(),
      signal,
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(`(HTTP ${resp.status}) ${detail}`);
    }
    const data = await resp.json();
    return data.goals || [];
  }, { force });
}

async function loadGoalsView(forceFresh = false, { background = false } = {}) {
  const stats = document.getElementById("goals-stats");
  const grid = document.getElementById("goals-grid");
  if (!stats || !grid) return;
  if (!USER_ID) {
    if (background) throw new Error("metas: sessão ainda não pronta");
    setTimeout(() => loadGoalsView(forceFresh), 300); return;
  }

  // Puxão: sem skeleton, fetch antes de render, falha real rejeita sem tocar DOM.
  if (background) {
    const data = await _fetchGoalsStatus({ force: true });
    if (data === undefined) return;
    _goalsCache = data;
    _renderGoalsView(data);
    return;
  }

  if (_goalsCache && !forceFresh) {
    _renderGoalsView(_goalsCache);
    _fetchGoalsStatus().then(fresh => {
      if (fresh) { _goalsCache = fresh; _renderGoalsView(fresh); }
    }).catch(() => {});
    return;
  }

  stats.innerHTML = `
    <div class="stat-tile"><div class="stat-label">Metas ativas</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Total guardado</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Total alvo</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Progresso geral</div><div class="sk sk-h2"></div></div>
  `;
  grid.innerHTML = "";

  try {
    const data = await _fetchGoalsStatus({ force: true });
    if (data === undefined) return;
    _goalsCache = data;
    _renderGoalsView(data);
  } catch (err) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1;padding:30px;color:var(--red)">Erro: ${escapeHtmlSafe(String(err.message || err))}</div>`;
  }
}

function _renderGoalsView(goals) {
  const stats = document.getElementById("goals-stats");
  const grid = document.getElementById("goals-grid");
  if (!stats || !grid) return;

  const list = goals || [];
  const goalsWithTarget = list.filter(g => g.is_goal);
  const pocketsOnly = list.filter(g => !g.is_goal);
  const onTrack = goalsWithTarget.filter(g => g.indicator === "on_track" || g.indicator === "ahead").length;
  const behind = goalsWithTarget.filter(g => g.indicator === "behind").length;
  const totalSaved = list.reduce((s, g) => s + (g.balance || 0), 0);
  const totalTarget = goalsWithTarget.reduce((s, g) => s + (g.target_amount || 0), 0);
  const overallPct = totalTarget > 0
    ? (goalsWithTarget.reduce((s, g) => s + (g.balance || 0), 0) / totalTarget * 100)
    : 0;

  stats.innerHTML = `
    <div class="stat-tile" style="animation-delay:0ms">
      <div class="stat-label">Metas ativas</div>
      <div class="stat-value">${goalsWithTarget.length}</div>
      <div class="stat-delta" style="color:var(--text-3)">${onTrack} no prazo${behind ? " · " + behind + " atrasada" + (behind === 1 ? "" : "s") : ""}${pocketsOnly.length ? " · " + pocketsOnly.length + " sem meta" : ""}</div>
    </div>
    <div class="stat-tile" style="animation-delay:60ms">
      <div class="stat-label">Total guardado</div>
      <div class="stat-value" style="color:var(--green)">${_fmtBRL(totalSaved)}</div>
      <div class="stat-delta" style="color:var(--text-3)">em ${list.length} caixinha${list.length === 1 ? "" : "s"}</div>
    </div>
    <div class="stat-tile" style="animation-delay:120ms">
      <div class="stat-label">Total alvo</div>
      <div class="stat-value">${_fmtBRL(totalTarget)}</div>
      <div class="stat-delta" style="color:var(--text-3)">${overallPct.toFixed(0)}% completo</div>
    </div>
    <div class="stat-tile" style="animation-delay:180ms">
      <div class="stat-label">Próximo prazo</div>
      <div class="stat-value" style="font-size:1.15rem">${_nextGoalDeadline(goalsWithTarget) || "—"}</div>
      <div class="stat-delta" style="color:var(--text-3)">${goalsWithTarget.length ? "vencimento mais próximo" : "sem metas"}</div>
    </div>
  `;

  if (!list.length) {
    grid.innerHTML = `
      <div class="empty" style="grid-column:1/-1;padding:50px;text-align:center;color:var(--text-3);background:var(--glass-bg);border:1px dashed var(--glass-border);border-radius:var(--radius)">
        <img class="empty-sticker" src="/brand/stickers/goal.webp" alt="" />
        <div style="font-size:1.05rem;font-weight:700;color:var(--text);margin-bottom:6px">Sem caixinhas ainda</div>
        <div style="margin-bottom:14px">Crie uma caixinha pra guardar dinheiro (com ou sem meta).</div>
        <button class="mock-cta" onclick="openGoalEditModal()">+ Criar</button>
      </div>`;
    return;
  }
  // Render: metas com target primeiro, depois caixinhas sem meta
  const ordered = [...goalsWithTarget, ...pocketsOnly];
  grid.innerHTML = ordered.map((g, i) => g.is_goal
    ? _renderGoalCard(g, i)
    : _renderPocketOnlyCard(g, i)
  ).join("");
}

// Caixinha vinda do banco (Open Finance): saldo espelhado, sem rendimento interno.
function _isOfPocket(p) { return p && (p.source === "open_finance" || p.of_investment_id != null); }
// No Grátis (pós-trial) o OF não está ativo → a caixinha do banco fica congelada.
function _isOfStale(p) { return _isOfPocket(p) && p.of_plan_active === false; }
function _ofPocketBadge(p) {
  if (!_isOfPocket(p)) return "";
  return _isOfStale(p)
    ? `<span class="rv-badge rv-badge-stale">banco desconectado</span>`
    : `<span class="rv-badge">via banco</span>`;
}

function _renderPocketOnlyCard(p, idx = 0) {
  const emoji = p.emoji || "🐷";
  const color = p.color || "#FF2D8E";
  const ofPocket = _isOfPocket(p);
  const ofStale = _isOfStale(p);
  const line1 = ofStale ? "<i class='ph ph-lock' aria-hidden='true'></i> Banco desconectado. Reative pra atualizar"
              : ofPocket ? "Sincronizada com seu banco"
              : "Caixinha sem meta: depósitos livres";
  const line2 = ofStale ? "Reative seu banco (plano pago) pra o saldo voltar a atualizar"
              : ofPocket ? "Saldo atualizado pela corretora/banco"
              : (p.interest_enabled === false ? "Sem rendimento" : _formatCdiRate(p.interest_rate));
  return `
    <div class="goal-card${ofStale ? " of-stale" : ""}" style="animation-delay:${idx * 80}ms;cursor:pointer" onclick="openPocketHistory('${escapeJsString(p.name)}')">
      <div class="goal-ring">
        <div style="width:78px;height:78px;border-radius:50%;background:${color}22;display:flex;align-items:center;justify-content:center;font-size:1.6rem">${phIcon(emoji)}</div>
      </div>
      <div class="goal-info">
        <div class="goal-name">${phIcon(emoji)} ${escapeHtmlSafe(p.name)} ${_ofPocketBadge(p)}</div>
        <div class="goal-amt">${_fmtBRL(p.balance || 0)} guardado</div>
        <div class="goal-deadline" style="color:var(--text-3)">${line1}</div>
        <div class="goal-deadline" style="color:var(--text-3)">${line2}</div>
        ${p.description ? `<div class="goal-deadline" style="color:var(--text-3);font-style:italic">${escapeHtmlSafe(p.description)}</div>` : ""}
      </div>
    </div>
  `;
}

function _nextGoalDeadline(goals) {
  const withDate = goals.filter(g => g.target_date).sort((a, b) => a.target_date.localeCompare(b.target_date));
  if (!withDate.length) return null;
  const next = withDate[0];
  try {
    const [y, m] = next.target_date.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString("pt-BR", { month: "short", year: "numeric" });
  } catch (_) { return next.target_date; }
}

function _renderGoalCard(g, idx = 0) {
  const pct = Math.min(100, g.pct_complete || 0);
  const color = g.color || GOAL_COLOR_OPTIONS[idx % GOAL_COLOR_OPTIONS.length];
  const emoji = g.emoji || "🎯";
  const circumference = 207.3;
  const offset = circumference * (1 - pct / 100);

  let deadlineText = "Sem prazo definido";
  let deadlineColor = "var(--text-3)";
  if (g.target_date) {
    const [y, m] = g.target_date.split("-").map(Number);
    const monthYear = new Date(y, m - 1, 1).toLocaleDateString("pt-BR", { month: "short", year: "numeric" });
    deadlineText = `Prazo: ${monthYear}`;
  }

  let alertText = "";
  if (g.indicator === "behind" && g.projected_months) {
    const today = new Date();
    const proj = new Date(today.getFullYear(), today.getMonth() + Math.ceil(g.projected_months), 1);
    const projStr = proj.toLocaleDateString("pt-BR", { month: "short", year: "numeric" });
    alertText = `<div class="goal-deadline" style="color:#FF2D2D"><i class="ph ph-warning" aria-hidden="true"></i> Ritmo atual chega só em ${projStr}${g.target_date ? ", prazo era " + deadlineText.replace("Prazo: ", "") : ""}</div>`;
  } else if (g.indicator === "tight") {
    alertText = `<div class="goal-deadline" style="color:#fbbf24">Ritmo apertado, pode atrasar</div>`;
  } else if (g.indicator === "ahead") {
    alertText = `<div class="goal-deadline" style="color:#00F078"><i class="ph ph-rocket-launch" aria-hidden="true"></i> Adiantado, no melhor caminho</div>`;
  } else if (g.indicator === "on_track") {
    alertText = `<div class="goal-deadline" style="color:#00F078">No prazo</div>`;
  } else if (g.indicator === "achieved") {
    alertText = `<div class="goal-deadline" style="color:#00F078"><i class="ph ph-check" aria-hidden="true"></i> Meta atingida</div>`;
  }

  return `
    <div class="goal-card" style="animation-delay:${idx * 80}ms;cursor:pointer" onclick="openPocketHistory('${escapeJsString(g.name)}')">
      <div class="goal-ring">
        <svg width="78" height="78" viewBox="0 0 78 78">
          <circle class="ring-bg" cx="39" cy="39" r="33" fill="none" stroke-width="7"/>
          <circle class="ring-fg" cx="39" cy="39" r="33" fill="none" stroke="${color}" stroke-width="7" stroke-dasharray="${circumference}" stroke-dashoffset="${offset.toFixed(1)}"/>
        </svg>
        <div class="ring-pct">${pct.toFixed(0)}%</div>
      </div>
      <div class="goal-info">
        <div class="goal-name">${phIcon(emoji)} ${escapeHtmlSafe(g.name)}</div>
        <div class="goal-amt">${_fmtBRL(g.balance || 0)} / ${_fmtBRL(g.target_amount || 0)}</div>
        <div class="bar-track" style="margin-top:6px"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
        <div class="goal-deadline" style="color:${deadlineColor}">${deadlineText}${g.days_left !== null ? " · " + (g.days_left >= 0 ? "em " + g.days_left + " dias" : "vencido há " + (-g.days_left) + " dias") : ""}</div>
        <div class="goal-deadline" style="color:var(--text-3)">${g.interest_enabled === false ? "Sem rendimento" : _formatCdiRate(g.interest_rate)}</div>
        ${alertText}
      </div>
    </div>
  `;
}

// ── Modal Meta ────────────────────────────────────────────────────────

let _goalEditState = { pocket_id: null, emoji: "🎯", color: "#FF2D8E" };
let _goalSaving = false;

function _ensureGoalModal() {
  if (document.getElementById("goal-edit-overlay")) return;
  const html = `
    <div class="overlay" id="goal-edit-overlay">
      <div class="modal wide">
        <h3 id="goal-edit-title">Nova meta ou caixinha</h3>
        <p class="msub" style="font-size:.78rem;margin-bottom:14px">
          Toda meta vira uma caixinha. Se você deixar o <strong>valor alvo vazio</strong>, vira só uma caixinha pra guardar dinheiro sem objetivo definido.
        </p>
        <form id="goal-edit-form" onsubmit="event.preventDefault(); saveGoal();">
          <div class="invest-form">
            <div class="form-row">
              <div class="field" style="flex:2">
                <label for="goal-name">Nome *</label>
                <input type="text" id="goal-name" required maxlength="80" placeholder="Ex: Viagem Japão, Reserva..." />
              </div>
              <div class="field" style="flex:1">
                <label for="goal-target">Valor alvo (R$)</label>
                <input type="number" id="goal-target" min="0.01" step="0.01" placeholder="vazio = sem meta" />
              </div>
            </div>
            <div class="form-row">
              <div class="field">
                <label for="goal-target-date">Prazo (opcional)</label>
                <input type="date" id="goal-target-date" />
              </div>
              <div class="field">
                <label>Status</label>
                <select id="goal-status">
                  <option value="active">Ativa</option>
                  <option value="achieved">Concluída</option>
                  <option value="abandoned">Abandonada</option>
                </select>
              </div>
            </div>
            <div class="field">
              <label>Emoji</label>
              <div id="goal-emoji-picker" style="display:flex;gap:6px;flex-wrap:wrap"></div>
            </div>
            <div class="field">
              <label>Cor</label>
              <div id="goal-color-picker" style="display:flex;gap:8px;flex-wrap:wrap"></div>
            </div>
            <div class="field">
              <label for="goal-description">Descrição (opcional)</label>
              <input type="text" id="goal-description" maxlength="200" placeholder="Anotações..." />
            </div>
            <div class="field">
              <label for="goal-interest-enabled">Rendimento?</label>
              <label style="display:flex;align-items:center;gap:10px;color:var(--text-2);font-size:.86rem">
                <input type="checkbox" id="goal-interest-enabled" checked onchange="syncGoalInterestConfig()" />
                <span>Rendimento atrelado ao CDI</span>
              </label>
              <div id="goal-interest-config" style="margin-top:10px">
                <label for="goal-interest-rate">Percentual do CDI</label>
                <div style="display:flex;align-items:center;gap:8px">
                  <input type="number" id="goal-interest-rate" min="1" max="300" step="0.01" value="100" inputmode="decimal" style="width:112px;flex:0 0 112px" />
                  <span style="color:var(--text-2);font-size:.86rem;white-space:nowrap">% do CDI</span>
                </div>
                <div style="color:var(--text-3);font-size:.78rem;margin-top:8px;line-height:1.35"><i class="ph ph-lightbulb" aria-hidden="true"></i> Valor simulado: o PigBank não custodia seu dinheiro. Use a taxa do banco onde o saldo realmente está aplicado.</div>
              </div>
            </div>
          </div>
          <div class="modal-acts" style="margin-top:18px;display:flex;gap:8px;align-items:center">
            <button type="button" class="inst-delete-btn" id="goal-delete-btn" style="display:none" onclick="deleteGoalFromModal()"><i class="ph ph-trash" aria-hidden="true"></i> Excluir</button>
            <span style="flex:1"></span>
            <button type="button" class="btn-cancel" onclick="closeGoalEditModal()">Cancelar</button>
            <button type="submit" class="btn-save">Salvar</button>
          </div>
        </form>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML("beforeend", html);
  document.getElementById("goal-edit-overlay").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeGoalEditModal();
  });
}

function _renderGoalPickers() {
  const ePick = document.getElementById("goal-emoji-picker");
  const cPick = document.getElementById("goal-color-picker");
  ePick.innerHTML = GOAL_EMOJI_OPTIONS.map(e => {
    const sel = e === _goalEditState.emoji;
    return `<button type="button" onclick="_setGoalEmoji('${e}')"
      style="width:36px;height:36px;border-radius:8px;font-size:1.2rem;cursor:pointer;
             border:2px solid ${sel ? "#fff" : "transparent"};
             background:${sel ? "rgba(255,45,142,.25)" : "var(--glass-bg)"};
             display:flex;align-items:center;justify-content:center">${phIcon(e)}</button>`;
  }).join("");
  cPick.innerHTML = GOAL_COLOR_OPTIONS.map(c => {
    const sel = c === _goalEditState.color;
    return `<button type="button" onclick="_setGoalColor('${c}')"
      style="width:32px;height:32px;border-radius:8px;cursor:pointer;
             border:2px solid ${sel ? "#fff" : "transparent"};
             background:${c};
             box-shadow:${sel ? "0 0 0 2px rgba(255,45,142,.5)" : "none"}"></button>`;
  }).join("");
}
function _setGoalEmoji(e) { _goalEditState.emoji = e; _renderGoalPickers(); }
function _setGoalColor(c) { _goalEditState.color = c; _renderGoalPickers(); }

function syncGoalInterestConfig() {
  const enabled = document.getElementById("goal-interest-enabled")?.checked ?? true;
  const config = document.getElementById("goal-interest-config");
  if (config) config.style.display = enabled ? "" : "none";
}

function openGoalEditModal(goal) {
  _ensureGoalModal();
  const isEdit = !!(goal && goal.id);
  _goalEditState = {
    pocket_id: isEdit ? goal.id : null,
    emoji: isEdit ? (goal.emoji || "🎯") : "🎯",
    color: isEdit ? (goal.color || "#FF2D8E") : "#FF2D8E",
    original_name: isEdit ? goal.name : null,
  };
  document.getElementById("goal-edit-title").textContent = isEdit ? "Editar meta" : "Nova caixinha / meta";
  document.getElementById("goal-name").value = isEdit ? goal.name : "";
  document.getElementById("goal-target").value = isEdit && goal.target_amount != null ? Number(goal.target_amount).toFixed(2) : "";
  document.getElementById("goal-target-date").value = isEdit && goal.target_date ? goal.target_date : "";
  document.getElementById("goal-status").value = isEdit ? (goal.status || "active") : "active";
  document.getElementById("goal-description").value = isEdit ? (goal.description || "") : "";
  document.getElementById("goal-interest-enabled").checked = isEdit ? goal.interest_enabled !== false : true;
  document.getElementById("goal-interest-rate").value = _cdiPercentFromRate(isEdit ? goal.interest_rate : 1).toFixed(2).replace(/\.00$/, "");
  syncGoalInterestConfig();
  document.getElementById("goal-delete-btn").style.display = isEdit ? "" : "none";
  _renderGoalPickers();
  document.getElementById("goal-edit-overlay").classList.add("open");
  setTimeout(() => document.getElementById("goal-name").focus(), 50);
}

function closeGoalEditModal() {
  document.getElementById("goal-edit-overlay")?.classList.remove("open");
}

async function saveGoal() {
  if (_goalSaving) return;
  const targetRaw = document.getElementById("goal-target").value.trim();
  const hasTarget = targetRaw !== "";
  const targetVal = hasTarget ? parseFloat(targetRaw) : null;
  const interestEnabled = document.getElementById("goal-interest-enabled").checked;
  const cdiPct = parseFloat((document.getElementById("goal-interest-rate").value || "100").replace(",", "."));
  const payload = {
    name: document.getElementById("goal-name").value.trim(),
    description: document.getElementById("goal-description").value.trim() || null,
    target_amount: hasTarget ? targetVal : null,
    target_date: document.getElementById("goal-target-date").value || null,
    emoji: _goalEditState.emoji,
    color: _goalEditState.color,
    status: document.getElementById("goal-status").value,
    interest_enabled: interestEnabled,
    interest_rate: interestEnabled ? cdiPct / 100 : 1.0,
    clear_target: !hasTarget,
  };
  if (!payload.name) { await alertModal("Digite um nome.", { title: "Nome obrigatório" }); return; }
  if (hasTarget && (!targetVal || targetVal <= 0)) {
    await alertModal("Valor alvo deve ser maior que zero ou deixe vazio pra criar só uma caixinha.", { title: "Valor inválido" });
    return;
  }
  if (interestEnabled && (!Number.isFinite(cdiPct) || cdiPct <= 0)) {
    await alertModal("Informe um percentual do CDI maior que zero.", { title: "Rendimento inválido" });
    return;
  }
  _goalSaving = true;
  const isEdit = !!_goalEditState.pocket_id;
  closeGoalEditModal();
  try {
    let pocketId = _goalEditState.pocket_id;
    if (!isEdit) {
      // Criar caixinha primeiro
      const r = await fetch(`${API}/pockets/${USER_ID}`, {
        method: "POST",
        credentials: "same-origin",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          name: payload.name,
          description: payload.description || null,
          interest_enabled: payload.interest_enabled,
          interest_rate: payload.interest_rate,
        }),
      });
      if (!r.ok) {
        const txt = await r.text();
        let detail = txt;
        try {
          const parsed = JSON.parse(txt);
          if (parsed.detail?.error === "pro_required") {
            await alertModal("Você atingiu o limite de caixinhas do Free. Pro libera ilimitado.", { title: "Recurso Pro" });
            openGoalEditModal({ id: null, ...payload });
            return;
          }
          detail = parsed.detail || txt;
        } catch(_) {}
        throw new Error(detail);
      }
      const data = await r.json();
      pocketId = data.pocket?.id;
    }
    // Agora PATCH com metadata da meta
    const metaResp = await fetch(`${API}/pockets/${USER_ID}/${pocketId}/meta`, {
      method: "PATCH",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
    if (!metaResp.ok) throw new Error(await metaResp.text());
    showToast(isEdit ? "✓ Meta atualizada" : "✓ Meta criada");
    _goalsCache = null;
    loadGoalsView(true);
    sendRefresh();
  } catch (err) {
    openGoalEditModal({ id: _goalEditState.pocket_id, ...payload });
    await alertModal(String(err.message || err), { title: "Erro" });
  } finally {
    _goalSaving = false;
  }
}

async function deleteGoalFromModal() {
  if (!_goalEditState.pocket_id) return;
  const ok = await confirmModal(
    "Excluir essa meta? A caixinha também será excluída se estiver vazia. Se tiver saldo, esvazie primeiro.",
    { title: "Excluir meta", okText: "Excluir", danger: true },
  );
  if (!ok) return;
  try {
    // Reusa o endpoint de delete de pocket — exige saldo zero
    const r = await fetch(`${API}/pockets/${USER_ID}/${encodeURIComponent(_goalEditState.original_name || "")}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!r.ok) {
      const txt = await r.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(detail);
    }
    closeGoalEditModal();
    showToast("✓ Meta excluída");
    _goalsCache = null;
    await loadGoalsView(true);
    sendRefresh();
  } catch (err) {
    await alertModal(String(err.message || err), { title: "Erro" });
  }
}


// ══════════════════════════════════════════════════════════════════════
// Gastos Fixos / Recorrentes (Sprint 4 — Pro-only)
// ══════════════════════════════════════════════════════════════════════

const RECURRING_CATEGORY_EMOJI = {
  "alimentação": "🍔", "mercado": "🛒", "transporte": "🚗", "saúde": "💊", "moradia": "🏠",
  "lazer": "🎬", "educação": "📚", "assinaturas": "📺", "pets": "🐾",
  "compras online": "📦", "beleza": "💄", "outros": "🏷️",
  "internet": "🌐", "telefone": "📱", "água": "💧", "luz": "💡",
};

// 1ª ocorrência do dia `dayNum` (1-31) em/depois de hoje E de `startISO`
// (YYYY-MM-DD, opcional). Espelha o guard do charger: recorrência com início
// futuro só "vence" a partir do start_date.
function _nextRecurringOccurrence(dayNum, startISO, frequency, monthNum) {
  const floor = new Date();
  floor.setHours(0, 0, 0, 0);
  if (startISO) {
    const s = new Date(startISO + "T00:00:00");
    if (s > floor) { floor.setTime(s.getTime()); }
  }
  // Frequências ancoradas no start_date.
  const startDate = startISO ? new Date(startISO + "T00:00:00") : null;
  if (frequency === "once") return startDate || floor;
  if (frequency === "daily") return floor;
  if (frequency === "weekly") {
    const base = startDate || floor;
    if (base >= floor) return base;
    const days = Math.round((floor - base) / 86400000);
    const bumps = Math.ceil(days / 7);
    return new Date(base.getTime() + bumps * 7 * 86400000);
  }
  const dd = Math.min(dayNum || 1, 28);
  // Anual: próxima ocorrência no mês monthNum (1-12), este ano ou o que vem.
  if (frequency === "annual" && monthNum) {
    const m = monthNum - 1; // JS: 0-11
    let d = new Date(floor.getFullYear(), m, dd);
    if (d < floor) d = new Date(floor.getFullYear() + 1, m, dd);
    return d;
  }
  let d = new Date(floor.getFullYear(), floor.getMonth(), dd);
  if (d < floor) d = new Date(floor.getFullYear(), floor.getMonth() + 1, dd);
  return d;
}

// Custo/renda mensal-equivalente de um recorrente: anual conta valor/12 no total
// mensal; mensal conta o valor cheio. (Usado nos cards "Total mensal".)
function _recMonthlyEquiv(r) {
  const v = r.amount || 0;
  switch (r.frequency) {
    case "annual": return v / 12;
    case "weekly": return v * 52 / 12;   // ~4,33 ocorrências/mês
    case "daily":  return v * 365 / 12;  // ~30,4 ocorrências/mês
    case "once":   return 0;             // pagamento único não é custo mensal recorrente
    default:       return v;             // monthly
  }
}

let _recurringCache = null;
const _recurringChannel = makeFetchChannel(); // dedup + abort + geração

async function _fetchRecurring({ force = false } = {}) {
  return _recurringChannel.run(async (signal) => {
    const resp = await fetch(`${API}/recurring-expenses/${USER_ID}`, {
      credentials: "same-origin",
      headers: csrfHeaders(),
      signal,
    });
    if (resp.status === 403) {
      const data = await resp.json().catch(() => ({}));
      if (data?.detail?.error === "pro_required") return { pro_required: true };
    }
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(`(HTTP ${resp.status}) ${detail}`);
    }
    const data = await resp.json();
    return data.recurring || [];
  }, { force });
}

async function loadFixedView(forceFresh = false, { background = false } = {}) {
  const stats = document.getElementById("recurring-stats");
  if (!stats) return;
  if (!USER_ID) {
    if (background) throw new Error("gastos fixos: sessão ainda não pronta");
    setTimeout(() => loadFixedView(forceFresh), 300);
    return;
  }

  // Puxão: sem skeleton, fetch antes de render, falha real rejeita sem tocar DOM.
  // undefined (superado) sai neutro; pro_required renderiza o gate (não é erro).
  if (background) {
    const data = await _fetchRecurring({ force: true });
    if (data === undefined) return;
    if (data && data.pro_required) { _renderFixedProGate(); return; }
    _recurringCache = data;
    _renderFixedView(data);
    return;
  }

  if (_recurringCache && !forceFresh) {
    _renderFixedView(_recurringCache);
    _fetchRecurring().then(fresh => {
      if (fresh && !fresh.pro_required) { _recurringCache = fresh; _renderFixedView(fresh); }
    }).catch(() => {});
    return;
  }

  stats.innerHTML = `
    <div class="stat-tile"><div class="stat-label">Total mensal</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Recorrentes ativos</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Próximo vencimento</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Reajustes</div><div class="sk sk-h2"></div></div>
  `;
  try {
    const data = await _fetchRecurring({ force: true });
    if (data === undefined) return;
    if (data && data.pro_required) {
      _renderFixedProGate();
      return;
    }
    _recurringCache = data;
    _renderFixedView(data);
  } catch (err) {
    stats.innerHTML = `<div class="empty" style="grid-column:1/-1;color:var(--red)">Erro: ${escapeHtmlSafe(String(err.message || err))}</div>`;
  }
}

function _renderFixedProGate() {
  const stats = document.getElementById("recurring-stats");
  if (stats) stats.innerHTML = "";
  ["recurring-essentials-list", "recurring-leisure-list", "recurring-upcoming-list", "recurring-adjustments-list"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
  const ess = document.getElementById("recurring-essentials-list");
  if (ess) {
    ess.innerHTML = `
      <div class="empty" style="padding:30px;text-align:center;color:var(--text-3)">
        <div style="font-size:2.5rem;margin-bottom:10px"><i class="ph ph-lock" aria-hidden="true"></i></div>
        <div style="font-size:1.05rem;font-weight:700;color:var(--text);margin-bottom:6px">Gastos Fixos é Pro</div>
        <div style="margin-bottom:14px">Cadastre suas assinaturas e contas recorrentes pra o Piggy lançar automaticamente todo mês.</div>
        <button class="mock-cta" onclick="showUpgradeModal('recurring_expenses')"><i class="ph ph-star" aria-hidden="true"></i> Ver Pro</button>
      </div>`;
  }
}

function _renderFixedView(items) {
  const stats = document.getElementById("recurring-stats");
  const essEl = document.getElementById("recurring-essentials-list");
  const leiEl = document.getElementById("recurring-leisure-list");
  const upEl = document.getElementById("recurring-upcoming-list");
  const adjEl = document.getElementById("recurring-adjustments-list");

  const list = items || [];
  // 'manual' (conta a pagar) não entra em Gastos fixos — vive na aba própria.
  const active = list.filter(r => r.is_active && (r.payment_mode || "autopay") === "autopay");
  // Total MENSAL: anual entra prorrateado (valor/12) pra refletir o custo médio
  // por mês. Um domínio de R$55/ano pesa ~R$4,58/mês, não R$55.
  const total = active.reduce((s, r) => s + _recMonthlyEquiv(r), 0);
  const nEssentials = active.filter(r => r.is_essential).length;
  const nLeisure = active.filter(r => !r.is_essential).length;

  // Próximo vencimento (1ª data do due_day em/depois de hoje E do start_date)
  const today = new Date();
  const nextDue = active.map(r => ({
    rec: r, date: _nextRecurringOccurrence(r.due_day, r.start_date, r.frequency, r.due_month),
  })).sort((a, b) => a.date - b.date);
  const next = nextDue[0];

  const adjustments = active.filter(r => r.last_amount != null && r.last_amount_changed_at);
  const incomeMonth = (lastData && lastData.monthly_income) || 0;
  const renderPct = incomeMonth > 0 ? ((total / incomeMonth) * 100).toFixed(0) : null;

  stats.innerHTML = `
    <div class="stat-tile" style="animation-delay:0ms">
      <div class="stat-label">Total mensal</div>
      <div class="stat-value" style="color:var(--red)">${_fmtBRL(total)}</div>
      <div class="stat-delta" style="color:var(--text-3)">${renderPct != null ? renderPct + "% da renda do mês" : active.length + " ativos"}</div>
    </div>
    <div class="stat-tile" style="animation-delay:60ms">
      <div class="stat-label">Recorrentes ativos</div>
      <div class="stat-value">${active.length}</div>
      <div class="stat-delta" style="color:var(--text-3)">${nEssentials} essenciais · ${nLeisure} lazer</div>
    </div>
    <div class="stat-tile" style="animation-delay:120ms">
      <div class="stat-label">Próximo vencimento</div>
      <div class="stat-value" style="font-size:1.15rem">${next ? escapeHtmlSafe(next.rec.name) : "—"}</div>
      <div class="stat-delta" style="color:var(--text-3)">${next ? _formatDueIn(next.date) : "sem agendamentos"}</div>
    </div>
    <div class="stat-tile" style="animation-delay:180ms">
      <div class="stat-label">Reajustes recentes</div>
      <div class="stat-value" style="color:${adjustments.length ? '#fbbf24' : 'var(--text-3)'}">${adjustments.length}</div>
      <div class="stat-delta" style="color:var(--text-3)">${adjustments.length ? "este mês" : "sem reajustes"}</div>
    </div>
  `;

  const essentials = active.filter(r => r.is_essential);
  const leisure = active.filter(r => !r.is_essential);

  essEl.innerHTML = essentials.length
    ? essentials.map(r => _renderRecurringRow(r)).join("")
    : `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Sem gastos essenciais cadastrados.</div>`;
  leiEl.innerHTML = leisure.length
    ? leisure.map(r => _renderRecurringRow(r)).join("")
    : `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Sem assinaturas cadastradas.</div>`;

  // Próximos 7 dias
  const upcoming = nextDue.filter(x => {
    const diffDays = (x.date - today) / (1000 * 60 * 60 * 24);
    return diffDays <= 7;
  });
  upEl.innerHTML = upcoming.length
    ? upcoming.map(x => `
        <div class="tx-row">
          <div class="tx-icon" style="color:${(x.date - today) / (1000 * 60 * 60 * 24) <= 2 ? '#FF2D2D' : '#fbbf24'}">${phIcon(_recurringEmoji(x.rec))}</div>
          <div class="tx-main">
            <div class="tx-desc">${escapeHtmlSafe(x.rec.name)} · ${x.date.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" })}</div>
            <div class="tx-meta">${_formatDueIn(x.date)} · ${x.rec.payment_type === "credit_card" ? "Cartão " + escapeHtmlSafe(x.rec.card_name || "?") : "Débito automático"}</div>
          </div>
          <div class="tx-amt red">-${_fmtBRL(x.rec.amount)}</div>
        </div>
      `).join("")
    : `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Nada nos próximos 7 dias.</div>`;

  // Reajustes
  adjEl.innerHTML = adjustments.length
    ? adjustments.map(r => {
        const delta = r.amount - r.last_amount;
        const sign = delta > 0 ? "+" : "−";
        const pct = r.last_amount > 0 ? ((delta / r.last_amount) * 100).toFixed(1) : "—";
        const when = r.last_amount_changed_at ? new Date(r.last_amount_changed_at).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) : "";
        return `
          <div class="tx-row">
            <div class="tx-icon" style="color:#fbbf24"><i class="ph ph-warning" aria-hidden="true"></i></div>
            <div class="tx-main">
              <div class="tx-desc">${escapeHtmlSafe(r.name)} ${delta > 0 ? "aumentou" : "diminuiu"} ${sign}${_fmtBRL(Math.abs(delta))}</div>
              <div class="tx-meta">${pct}% vs valor anterior · detectado em ${when}</div>
            </div>
          </div>`;
      }).join("")
    : `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Nenhum reajuste detectado ainda.</div>`;
}

function _recurringEmoji(r) {
  const name = (r.name || "").toLowerCase();
  if (name.includes("netflix") || name.includes("hbo") || name.includes("prime")) return "📺";
  if (name.includes("spotify") || name.includes("apple music") || name.includes("deezer")) return "🎵";
  if (name.includes("icloud") || name.includes("drive") || name.includes("dropbox")) return "☁️";
  if (name.includes("audible") || name.includes("kindle")) return "📚";
  if (name.includes("internet") || name.includes("vivo") || name.includes("claro")) return "🌐";
  if (name.includes("celular") || name.includes("telefone")) return "📱";
  if (name.includes("luz") || name.includes("enel") || name.includes("cemig")) return "💡";
  if (name.includes("água") || name.includes("sabesp")) return "💧";
  if (name.includes("aluguel") || name.includes("condomínio")) return "🏠";
  return RECURRING_CATEGORY_EMOJI[(r.category || "").toLowerCase()] || "🏷️";
}

// Rótulo curto da frequência de um recorrente (gasto fixo / receita fixa).
function _recFreqLabel(r) {
  const dia = r.due_day || r.pay_day;
  switch (r.frequency) {
    case "once":   return r.start_date ? `único · ${_fmtDateBR(r.start_date)}` : "pagamento único";
    case "daily":  return "todo dia";
    case "weekly": return r.start_date ? `semanal · a partir de ${_fmtDateBR(r.start_date)}` : "semanal";
    case "annual":
      return (r.due_month || r.pay_month)
        ? `anual · ${dia}/${_MESES_ABREV[(r.due_month || r.pay_month) - 1]}`
        : "anual";
    default:       return `dia ${dia}`;
  }
}

function _fmtDateBR(iso) {
  try {
    const d = new Date(String(iso).slice(0, 10) + "T00:00:00");
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" });
  } catch (_) { return String(iso); }
}

function _renderRecurringRow(r) {
  const quando = _recFreqLabel(r);
  const payment = r.payment_type === "credit_card"
    ? `Cartão ${escapeHtmlSafe(r.card_name || "?")} · ${quando}`
    : `Débito · ${quando}`;
  let adjustText = "";
  if (r.last_amount != null && r.last_amount > 0) {
    const delta = r.amount - r.last_amount;
    if (Math.abs(delta) > 0.005) {
      const arrow = delta > 0 ? "↑" : "↓";
      const color = delta > 0 ? "#fbbf24" : "#22c55e";
      adjustText = ` · <span style="color:${color}">${_fmtBRL(r.last_amount)} → ${_fmtBRL(r.amount)} ${arrow}</span>`;
    }
  }
  const startText = _futureStartHint(r.start_date);
  const safeRecJson = escapeHtmlSafe(JSON.stringify(r));
  return `
    <div class="tx-row" style="cursor:pointer" onclick="openRecurringEditModal(${safeRecJson})">
      <div class="tx-icon">${phIcon(_recurringEmoji(r))}</div>
      <div class="tx-main">
        <div class="tx-desc">${escapeHtmlSafe(r.name)}</div>
        <div class="tx-meta">${payment}${adjustText}${startText}</div>
      </div>
      <div class="tx-amt red">-${_fmtBRL(r.amount)}</div>
    </div>
  `;
}

// Mostra "· começa DD/MM" só quando o início ainda está no futuro.
function _futureStartHint(startISO) {
  if (!startISO) return "";
  const s = new Date(startISO + "T00:00:00");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  if (s <= today) return "";
  return ` · <span style="color:var(--text-3)">começa ${s.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" })}</span>`;
}

function _formatDueIn(date) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(date);
  target.setHours(0, 0, 0, 0);
  const diff = Math.round((target - today) / (1000 * 60 * 60 * 24));
  if (diff === 0) return "hoje";
  if (diff === 1) return "amanhã";
  if (diff < 0) return `há ${-diff} dia${diff === -1 ? "" : "s"}`;
  return `em ${diff} dias`;
}

// ── Modal cadastrar/editar recorrente ─────────────────────────────────

let _recurringEditState = { id: null };

function _ensureRecurringModal() {
  if (document.getElementById("recurring-edit-overlay")) return;
  const html = `
    <div class="overlay" id="recurring-edit-overlay">
      <div class="modal wide">
        <h3 id="recurring-edit-title">Novo gasto fixo</h3>
        <p class="msub" id="recurring-mode-hint" style="background:rgba(255,45,142,.1);border:1px solid rgba(255,45,142,.3);border-radius:8px;padding:10px 12px;margin-bottom:14px;font-size:.78rem"></p>
        <form id="recurring-edit-form" onsubmit="event.preventDefault(); saveRecurring();">
          <div class="invest-form">
            <div class="form-row">
              <div class="field">
                <label for="recurring-mode">Tipo *</label>
                <select id="recurring-mode" onchange="_toggleRecurringModeHint()">
                  <option value="autopay">Gasto fixo (débito automático)</option>
                  <option value="manual">Conta a pagar (boleto/lembrete)</option>
                </select>
              </div>
            </div>
            <div class="form-row">
              <div class="field" style="flex:2">
                <label for="recurring-name">Nome *</label>
                <input type="text" id="recurring-name" required maxlength="80" placeholder="Ex: Netflix, Aluguel..." />
              </div>
              <div class="field" style="flex:1">
                <label for="recurring-amount" id="recurring-amount-label">Valor (R$) *</label>
                <input type="number" id="recurring-amount" min="0.01" step="0.01" required placeholder="0,00" />
              </div>
            </div>
            <div class="form-row">
              <div class="field" id="recurring-dueday-field">
                <label for="recurring-due-day">Dia do vencimento *</label>
                <input type="number" id="recurring-due-day" min="1" max="31" placeholder="1-31" />
              </div>
              <div class="field">
                <label for="recurring-category">Categoria *</label>
                <input type="text" id="recurring-category" required maxlength="40" placeholder="alimentação, lazer..." />
              </div>
            </div>
            <div class="form-row">
              <div class="field">
                <label for="recurring-frequency">Frequência *</label>
                <select id="recurring-frequency" onchange="_toggleRecurringFreqFields()">
                  <option value="once">Único (1x)</option>
                  <option value="daily">Diária (todo dia)</option>
                  <option value="weekly">Semanal (a cada 7 dias)</option>
                  <option value="monthly">Mensal (todo mês)</option>
                  <option value="annual">Anual (1x por ano)</option>
                </select>
              </div>
              <div class="field" id="recurring-month-field" style="display:none">
                <label for="recurring-month">Mês do vencimento *</label>
                <select id="recurring-month">${_monthOptionsHTML()}</select>
              </div>
            </div>
            <div class="form-row" id="recurring-paytype-row">
              <div class="field">
                <label for="recurring-payment-type">Forma de pagamento *</label>
                <select id="recurring-payment-type" onchange="_toggleRecurringCardField()">
                  <option value="account">Débito automático na conta</option>
                  <option value="credit_card">Cartão de crédito</option>
                </select>
              </div>
              <div class="field" id="recurring-card-field" style="display:none">
                <label for="recurring-card">Cartão</label>
                <select id="recurring-card"></select>
              </div>
            </div>
            <div class="form-row">
              <div class="field">
                <label for="recurring-start-date" id="recurring-start-label">Começa a partir de</label>
                <input type="date" id="recurring-start-date" />
                <span id="recurring-start-hint" style="font-size:.68rem;color:var(--text-3);margin-top:4px;display:block">A 1ª cobrança é no dia do vencimento em/após esta data. Deixe hoje pra começar já.</span>
              </div>
            </div>
            <div class="field">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="checkbox" id="recurring-is-essential" />
                <span>Marcar como gasto essencial (não-discricionário)</span>
              </label>
            </div>
            <div class="field">
              <label for="recurring-notes">Notas (opcional)</label>
              <input type="text" id="recurring-notes" maxlength="200" placeholder="Anotações..." />
            </div>
          </div>
          <div class="modal-acts" style="margin-top:18px;display:flex;gap:8px;align-items:center">
            <button type="button" class="inst-delete-btn" id="recurring-delete-btn" style="display:none" onclick="deleteRecurringFromModal()"><i class="ph ph-trash" aria-hidden="true"></i> Excluir</button>
            <span style="flex:1"></span>
            <button type="button" class="btn-cancel" onclick="closeRecurringEditModal()">Cancelar</button>
            <button type="submit" class="btn-save">Salvar</button>
          </div>
        </form>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML("beforeend", html);
  document.getElementById("recurring-edit-overlay").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeRecurringEditModal();
  });
}

function _toggleRecurringCardField() {
  const type = document.getElementById("recurring-payment-type").value;
  const field = document.getElementById("recurring-card-field");
  field.style.display = type === "credit_card" ? "" : "none";
  if (type === "credit_card") _populateRecurringCardOptions();
}

const _MESES = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"];
const _MESES_ABREV = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"];
function _monthOptionsHTML(selected) {
  return _MESES.map((m, i) => `<option value="${i + 1}"${selected == i + 1 ? " selected" : ""}>${m}</option>`).join("");
}
// Mostra o campo "Mês" só quando a frequência é Anual (vale pros dois modais:
// prefix "recurring" (gasto) ou "recurring-income" (receita)).
function _toggleRecurringMonthField(prefix = "recurring") {
  const freq = document.getElementById(`${prefix}-frequency`);
  const field = document.getElementById(`${prefix}-month-field`);
  if (!freq || !field) return;
  field.style.display = freq.value === "annual" ? "" : "none";
}

// Modal de gasto/conta a pagar: ajusta os campos conforme a frequência.
//  - anual  → mostra "Mês do vencimento"
//  - mensal/anual → "Dia do vencimento" (1-31) obrigatório
//  - única  → sem dia do mês; a data é o próprio vencimento ("Data do vencimento")
//  - semanal/diária → sem dia do mês; ancora no start_date ("A partir de")
function _toggleRecurringFreqFields() {
  const freq = (document.getElementById("recurring-frequency") || {}).value || "monthly";
  _toggleRecurringMonthField("recurring");
  const dueField = document.getElementById("recurring-dueday-field");
  const dueInput = document.getElementById("recurring-due-day");
  const usesDay = (freq === "monthly" || freq === "annual");
  if (dueField) dueField.style.display = usesDay ? "" : "none";
  if (dueInput) dueInput.required = usesDay;

  const startLabel = document.getElementById("recurring-start-label");
  const startHint = document.getElementById("recurring-start-hint");
  const startInput = document.getElementById("recurring-start-date");
  if (freq === "once") {
    if (startLabel) startLabel.textContent = "Data do vencimento *";
    if (startHint) startHint.textContent = "A conta vence nesta data (pagamento único).";
    if (startInput) startInput.required = true;
  } else if (freq === "weekly" || freq === "daily") {
    if (startLabel) startLabel.textContent = "A partir de *";
    if (startHint) startHint.textContent = freq === "weekly"
      ? "Repete a cada 7 dias a partir desta data."
      : "Repete todo dia a partir desta data.";
    if (startInput) startInput.required = true;
  } else {
    if (startLabel) startLabel.textContent = "Começa a partir de";
    if (startHint) startHint.textContent = "A 1ª cobrança é no dia do vencimento em/após esta data. Deixe hoje pra começar já.";
    if (startInput) startInput.required = false;
  }
}

// Ajusta ajuda + título + campos do modal conforme o modo (gasto fixo vs conta
// a pagar). Numa CONTA A PAGAR o valor é SEMPRE uma estimativa (o valor real é
// informado ao pagar), então o campo de valor vira opcional; num GASTO FIXO o
// valor é obrigatório (o charger debita esse valor sozinho).
function _toggleRecurringModeHint() {
  const mode = (document.getElementById("recurring-mode") || {}).value || "autopay";
  const hint = document.getElementById("recurring-mode-hint");
  const title = document.getElementById("recurring-edit-title");
  const isEdit = !!(_recurringEditState && _recurringEditState.id);
  const paytypeRow = document.getElementById("recurring-paytype-row");
  const label = document.getElementById("recurring-amount-label");
  const amount = document.getElementById("recurring-amount");
  const name = document.getElementById("recurring-name");
  if (mode === "manual") {
    if (hint) hint.innerHTML = "<i class='ph ph-receipt' aria-hidden='true'></i> <strong>Conta a pagar:</strong> a Piggy te <strong>lembra</strong> do vencimento e <strong>nada sai da conta</strong> até você confirmar. O valor é sempre uma <strong>estimativa</strong>. Você informa o valor real ao marcar como paga.";
    if (title && !isEdit) title.textContent = "Nova conta a pagar";
    // Conta a pagar nunca é débito automático — o user sempre confirma na mão.
    // A "forma de pagamento" (autopay/cartão) não se aplica: esconde e fixa account.
    if (paytypeRow) paytypeRow.style.display = "none";
    const pt = document.getElementById("recurring-payment-type");
    if (pt) pt.value = "account";
    _toggleRecurringCardField();
    // valor = estimativa opcional
    if (label) label.textContent = "Valor estimado (opcional)";
    if (amount) { amount.required = false; amount.placeholder = "estimativa, ex: 80,00"; }
    if (name) name.placeholder = "Ex: Água, Luz, Internet...";
  } else {
    if (hint) hint.innerHTML = "<i class='ph ph-warning' aria-hidden='true'></i> <strong>Gasto fixo:</strong> é <strong>lançado automaticamente</strong> no dia escolhido (débito na conta). Pra contas que você paga na mão (boleto), use \"Conta a pagar\".";
    if (title && !isEdit) title.textContent = "Novo gasto fixo";
    if (paytypeRow) paytypeRow.style.display = "";
    if (label) label.textContent = "Valor (R$) *";
    if (amount) { amount.required = true; amount.placeholder = "0,00"; }
    if (name) name.placeholder = "Ex: Netflix, Aluguel...";
  }
}

function _populateRecurringCardOptions(selectedId = null) {
  const sel = document.getElementById("recurring-card");
  const cards = (lastData && lastData.credit_cards) || [];
  if (!cards.length) {
    sel.innerHTML = `<option value="">— Nenhum cartão cadastrado —</option>`;
    return;
  }
  sel.innerHTML = cards.map(c => `<option value="${c.id}"${selectedId == c.id ? " selected" : ""}>${escapeHtmlSafe(c.name)}</option>`).join("");
}

function openRecurringEditModal(rec) {
  _ensureRecurringModal();
  const isEdit = !!(rec && rec.id);
  _recurringEditState = { id: isEdit ? rec.id : null };

  document.getElementById("recurring-edit-title").textContent = isEdit ? "Editar gasto fixo" : "Novo gasto fixo";
  document.getElementById("recurring-name").value = isEdit ? rec.name : "";
  document.getElementById("recurring-amount").value = isEdit ? Number(rec.amount).toFixed(2) : "";
  document.getElementById("recurring-due-day").value = isEdit ? rec.due_day : "";
  document.getElementById("recurring-start-date").value = isEdit ? (rec.start_date || "") : new Date().toLocaleDateString("en-CA");
  document.getElementById("recurring-category").value = isEdit ? rec.category : "";
  document.getElementById("recurring-mode").value = isEdit ? (rec.payment_mode || "autopay") : ((rec && rec._mode) || "autopay");
  document.getElementById("recurring-payment-type").value = isEdit ? rec.payment_type : "account";
  document.getElementById("recurring-frequency").value = isEdit ? (rec.frequency || "monthly") : "monthly";
  document.getElementById("recurring-month").value = (isEdit && rec.due_month) ? rec.due_month : (new Date().getMonth() + 1);
  document.getElementById("recurring-is-essential").checked = isEdit ? !!rec.is_essential : false;
  document.getElementById("recurring-notes").value = isEdit ? (rec.notes || "") : "";
  document.getElementById("recurring-delete-btn").style.display = isEdit ? "" : "none";
  _toggleRecurringCardField();
  _toggleRecurringMonthField("recurring");
  _toggleRecurringModeHint();
  _toggleRecurringFreqFields();
  if (isEdit && rec.payment_type === "credit_card") _populateRecurringCardOptions(rec.card_id);

  document.getElementById("recurring-edit-overlay").classList.add("open");
  setTimeout(() => document.getElementById("recurring-name").focus(), 50);
}

function closeRecurringEditModal() {
  document.getElementById("recurring-edit-overlay")?.classList.remove("open");
}

let _recurringSaving = false;
async function saveRecurring() {
  if (_recurringSaving) return;  // bloqueia double-submit

  // Coleta + valida ANTES de fechar (pra reabrir com dados se erro)
  const _amtRaw = parseFloat(document.getElementById("recurring-amount").value);
  const _freq = document.getElementById("recurring-frequency").value;
  const _usesDay = (_freq === "monthly" || _freq === "annual");
  const _dueRaw = parseInt(document.getElementById("recurring-due-day").value, 10);
  const payload = {
    name: document.getElementById("recurring-name").value.trim(),
    amount: Number.isFinite(_amtRaw) ? _amtRaw : null,  // conta variável pode ficar sem estimativa
    category: document.getElementById("recurring-category").value.trim(),
    // dia do mês só vale pra mensal/anual; nas outras o servidor deriva do start_date
    due_day: (_usesDay && Number.isFinite(_dueRaw)) ? _dueRaw : null,
    start_date: document.getElementById("recurring-start-date").value || null,
    frequency: _freq,
    due_month: _freq === "annual"
      ? parseInt(document.getElementById("recurring-month").value, 10) : null,
    payment_mode: document.getElementById("recurring-mode").value,
    // conta a pagar: valor é sempre estimativa (informado ao pagar)
    variable_amount: document.getElementById("recurring-mode").value === "manual",
    payment_type: document.getElementById("recurring-payment-type").value,
    card_id: null,
    is_essential: document.getElementById("recurring-is-essential").checked,
    notes: document.getElementById("recurring-notes").value.trim() || null,
  };
  if (payload.payment_type === "credit_card") {
    const cardVal = document.getElementById("recurring-card").value;
    if (!cardVal) {
      await alertModal("Escolha um cartão.", { title: "Cartão obrigatório" });
      return;
    }
    payload.card_id = parseInt(cardVal, 10);
  }

  _recurringSaving = true;
  const isEdit = !!_recurringEditState.id;
  const editingId = _recurringEditState.id;

  // Optimistic: fecha modal imediatamente. Se der erro, reabre.
  closeRecurringEditModal();

  try {
    const url = isEdit
      ? `${API}/recurring-expenses/${USER_ID}/${editingId}`
      : `${API}/recurring-expenses/${USER_ID}`;
    const method = isEdit ? "PATCH" : "POST";
    const resp = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      if (resp.status === 403) {
        await alertModal("Gastos fixos é uma feature Pro.", { title: "Recurso Pro" });
        return;
      }
      throw new Error(detail);
    }
    const isBill = payload.payment_mode === "manual";
    showToast(isEdit ? (isBill ? "✓ Conta a pagar atualizada" : "✓ Gasto fixo atualizado")
                     : (isBill ? "✓ Conta a pagar cadastrada" : "✓ Gasto fixo cadastrado"));
    _recurringCache = null;
    loadFixedView(true);           // re-render gastos fixos (sem await)
    if (isBill) loadBillsView(true);
    sendRefresh();
  } catch (err) {
    // Reabre modal com dados preservados pro user corrigir
    openRecurringEditModal(isEdit ? { ...payload, id: editingId } : payload);
    await alertModal(String(err.message || err), { title: "Erro ao salvar" });
  } finally {
    _recurringSaving = false;
  }
}

async function deleteRecurringFromModal() {
  if (!_recurringEditState.id) return;
  const ok = await confirmModal(
    "Excluir este gasto fixo? Lançamentos passados ficam preservados. Só não vai mais cobrar automaticamente.",
    { title: "Excluir gasto fixo", okText: "Excluir", danger: true },
  );
  if (!ok) return;
  try {
    const resp = await fetch(`${API}/recurring-expenses/${USER_ID}/${_recurringEditState.id}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!resp.ok) throw new Error(await resp.text());
    closeRecurringEditModal();
    showToast("✓ Gasto fixo excluído");
    _recurringCache = null;
    await loadFixedView(true);
  } catch (err) {
    await alertModal(String(err.message || err), { title: "Erro" });
  }
}

// ══════════════════════════════════════════════════════════════════════
// Receitas Recorrentes (Pro-only — mesma feature/gate do gasto fixo)
// ══════════════════════════════════════════════════════════════════════
// Espelho do bloco acima, do lado da entrada. Sem forma de pagamento:
// receita sempre cai na conta. `is_primary` separa renda principal de extras.

const RECURRING_INCOME_CATEGORY_EMOJI = {
  "salário": "💼", "salario": "💼", "freela": "🧑‍💻", "freelance": "🧑‍💻",
  "aluguel": "🏠", "dividendos": "📈", "investimentos": "📈", "rendimentos": "📈",
  "vendas": "🛍️", "pensão": "🤝", "aposentadoria": "🧓", "bolsa": "🎓",
  "comissão": "🤑", "bônus": "🎁", "outros": "🏷️",
};

// Aba ativa da view Recorrentes: "overview" | "expenses" | "incomes" | "bills"
let _recurringTab = "overview";
let _recurringIncomeCache = null;
const _recurringIncomeChannel = makeFetchChannel(); // dedup + abort + geração

function setRecurringTab(tab) {
  if (!["overview", "expenses", "incomes", "bills"].includes(tab)) return;
  _recurringTab = tab;
  document.querySelectorAll("#recurring-tabs .ftab").forEach(b => {
    b.classList.toggle("active", b.dataset.rectab === tab);
  });
  const panes = {
    overview: document.getElementById("recurring-overview-pane"),
    expenses: document.getElementById("recurring-expenses-pane"),
    incomes:  document.getElementById("recurring-incomes-pane"),
    bills:    document.getElementById("recurring-bills-pane"),
  };
  Object.entries(panes).forEach(([k, el]) => { if (el) el.style.display = k === tab ? "" : "none"; });

  // Botão "+ Novo" some na Visão geral (cada card tem seu próprio atalho).
  const btn = document.getElementById("recurring-new-btn");
  if (btn) {
    btn.style.display = tab === "overview" ? "none" : "";
    btn.textContent = tab === "incomes" ? "+ Nova receita fixa"
      : tab === "bills" ? "+ Novo boleto" : "+ Novo gasto fixo";
  }

  if (tab === "overview") loadRecurringOverview();
  else if (tab === "incomes") loadRecurringIncomeView();
  else if (tab === "bills") loadBillsView();
  else loadFixedView();
}

// ── Previsão mensal: o que entra (receitas fixas) × o que sai (gastos fixos +
// boletos) × resultado, + próximos vencimentos. Deixa explícito que é RECORRENTE
// (não colide com "Receitas/Gastos do mês" do dashboard principal). ─────────────
const _recurringOverviewChannel = makeFetchChannel(); // dedup + abort + geração

async function loadRecurringOverview({ background = false } = {}) {
  const wrap = document.getElementById("recurring-overview-cards");
  if (!wrap) return;
  // background (puxar pra atualizar): sem skeleton — o render bom fica na
  // tela até os dados novos chegarem.
  if (!background) {
    wrap.innerHTML = `<div class="mock-card"><div class="empty" style="padding:16px;color:var(--text-3)">Carregando…</div></div>`;
  }
  // Canal compartilhado (abort + geração): os 3 endpoints são independentes,
  // mas DUAS invocações do overview podem correr juntas (navego pra
  // Recorrentes e puxo antes do load de nav terminar). Sem guarda de geração,
  // o de nav (mais lento) renderizaria por último e sobrescreveria o fresco do
  // puxão — o mesmo stale-overwrite, só que na janela do load inicial. force:true
  // sempre (o overview nunca deduplicou; sempre busca fresco) + os 3 fetches no
  // MESMO signal, então o abort cancela os 3 de uma vez.
  const result = await _recurringOverviewChannel.run(async (signal) => {
    const j = async (url) => {
      try {
        const r = await fetch(url, { credentials: "same-origin", signal });
        if (!r.ok) return null;
        return await r.json();
      } catch (err) {
        // Abort tem que propagar (o canal converte em neutro/undefined); só a
        // falha de rede "normal" vira null tolerável na navegação.
        if (err && err.name === "AbortError") throw err;
        return null;
      }
    };
    const [exp, inc, bills] = await Promise.all([
      j(`${API}/recurring-expenses/${USER_ID}`),
      j(`${API}/recurring-incomes/${USER_ID}`),
      j(`${API}/recurring-bills/${USER_ID}?include_paid=false`),
    ]);
    // No puxão, endpoint que falhou NÃO pode virar total zerado: o j() acima
    // converte falha em null e o reduce embaixo somaria zero por cima de números
    // que estavam certos na tela. Rejeita sem tocar no DOM — o indicador do
    // gesto (app-mode.js) fica âmbar e o render antigo sobrevive. (Na navegação,
    // null é tolerado: renderiza o parcial.)
    if (background && (exp === null || inc === null || bills === null)) {
      throw new Error("recurring overview: fetch falhou no refresh");
    }
    return { exp, inc, bills };
  }, { force: true });
  if (result === undefined) return;   // superado por outra invocação — deixa a tela
  const { exp, inc, bills } = result;

  const gastos = ((exp && exp.recurring) || []).filter(r => r.is_active && (r.payment_mode || "autopay") === "autopay");
  const totalGastos = gastos.reduce((s, r) => s + _recMonthlyEquiv(r), 0);
  const receitas = ((inc && inc.incomes) || []).filter(r => r.is_active);
  const totalReceitas = receitas.reduce((s, r) => s + _recMonthlyEquiv(r), 0);
  const pend = ((bills && bills.bills) || []).filter(b => b.status === "pending");
  const totalPend = pend.reduce((s, b) => s + (b.amount || 0), 0);

  const entradas = totalReceitas;
  const saidas = totalGastos + totalPend;
  const resultado = entradas - saidas;
  const positivo = resultado >= 0;
  const resColor = positivo ? "#22c55e" : "#FF2D2D";
  const plural = (n) => n === 1 ? "" : "s";

  const today = new Date(); today.setHours(0, 0, 0, 0);
  const daysUntil = (d) => Math.round((d - today) / 86400000);
  const dOf = (b) => { const d = new Date(b.due_date + "T00:00:00"); d.setHours(0, 0, 0, 0); return d; };

  // Próximos vencimentos (30 dias): boletos + gastos fixos + receitas fixas.
  const horizon = new Date(today.getTime() + 30 * 86400000);
  const up = [];
  pend.forEach(b => up.push({ d: dOf(b), name: b.name || "Boleto", amt: -(b.amount || 0), tag: "<i class='ph ph-receipt' aria-hidden='true'></i>" }));
  gastos.forEach(r => { const d = _nextRecurringOccurrence(r.due_day, r.start_date, r.frequency, r.due_month); if (d) up.push({ d, name: r.name || "Gasto fixo", amt: -(r.amount || 0), tag: "<i class='ph ph-trend-down' aria-hidden='true'></i>" }); });
  receitas.forEach(r => { const d = _nextRecurringOccurrence(r.pay_day, r.start_date, r.frequency, r.pay_month); if (d) up.push({ d, name: r.name || "Receita", amt: +(r.amount || 0), tag: "<i class='ph ph-trend-up' aria-hidden='true'></i>" }); });
  const upAll = up.filter(x => x.d >= today && x.d <= horizon).sort((a, b) => a.d - b.d);
  const upcoming = upAll.slice(0, 6);

  const vencBadge = (d) => {
    const n = daysUntil(d);
    if (n < 0) return { txt: "Vencido", bg: "rgba(255,45,45,.15)", fg: "#FF6B6B" };
    if (n === 0) return { txt: "Hoje", bg: "rgba(255,45,142,.18)", fg: "#FF2D8E" };
    if (n === 1) return { txt: "Amanhã", bg: "rgba(251,191,36,.15)", fg: "#fbbf24" };
    if (n <= 7) return { txt: `Em ${n} dias`, bg: "rgba(251,191,36,.15)", fg: "#fbbf24" };
    return { txt: `Dia ${d.getDate()}`, bg: "rgba(168,85,247,.18)", fg: "#c084fc" };
  };

  // ── Título ──
  const head = `
    <div style="margin-bottom:14px">
      <h2 style="margin:0 0 2px;font-size:1.5rem">Previsão mensal</h2>
      <div style="font-size:.86rem;color:var(--text-3)">Veja rapidamente o que entra, o que sai e o que vence primeiro, só do que é recorrente.</div>
    </div>`;

  // ── Alerta (déficit ou no azul) ──
  const alerta = positivo
    ? `<div class="mock-card" style="border:1px solid rgba(34,197,94,.35);margin-bottom:14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <div style="font-size:1.5rem"><i class="ph ph-check-circle" aria-hidden="true"></i></div>
        <div style="flex:1;min-width:220px">
          <div style="font-weight:700">Suas entradas cobrem os compromissos. Sobra <span style="color:#22c55e">${_fmtBRL(resultado)}</span>.</div>
          <div style="font-size:.82rem;color:var(--text-3)">Mês recorrente equilibrado. Bom trabalho! <i class="ph ph-piggy-bank" aria-hidden="true"></i></div>
        </div>
      </div>`
    : `<div class="mock-card" style="border:1px solid rgba(255,45,142,.4);margin-bottom:14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <div style="font-size:1.5rem"><i class="ph ph-warning" aria-hidden="true"></i></div>
        <div style="flex:1;min-width:220px">
          <div style="font-weight:700">Saídas previstas maiores que entradas em <span style="color:#FF2D8E">${_fmtBRL(-resultado)}</span>.</div>
          <div style="font-size:.82rem;color:var(--text-3)">Revise contas a pagar ou planeje novas entradas para equilibrar o mês.</div>
        </div>
        <button class="mock-cta" onclick="setRecurringTab('bills')">Revisar →</button>
      </div>`;

  // ── 3 cards (Entradas / Saídas / Resultado) ──
  const statCard = (accent, iconBg, icon, label, value, valColor, sub) => `
    <div class="mock-card" style="flex:1;min-width:190px;border-left:3px solid ${accent}">
      <div style="display:flex;gap:12px;align-items:flex-start">
        <div style="width:40px;height:40px;border-radius:11px;background:${iconBg};display:flex;align-items:center;justify-content:center;font-size:1.15rem;flex-shrink:0">${icon}</div>
        <div style="min-width:0">
          <div style="font-size:.82rem;color:var(--text-2)">${label}</div>
          <div style="font-size:1.55rem;font-weight:700;color:${valColor};line-height:1.15">${value}</div>
          <div style="font-size:.76rem;color:var(--text-3)">${sub}</div>
        </div>
      </div>
    </div>`;
  const statsRow = `<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px">
    ${statCard("#22c55e", "rgba(34,197,94,.15)", '<i class="ph ph-chart-line-up" aria-hidden="true"></i>', "Entradas previstas", _fmtBRL(entradas), "#22c55e",
      `${receitas.length} receita${plural(receitas.length)} fixa${plural(receitas.length)}`)}
    ${statCard("#fb7185", "rgba(251,113,133,.15)", '<i class="ph ph-chart-line-down" aria-hidden="true"></i>', "Saídas previstas", _fmtBRL(saidas), "#fb7185",
      `${gastos.length} gasto${plural(gastos.length)} fixo${plural(gastos.length)} + ${pend.length} boleto${plural(pend.length)}`)}
    ${statCard(resColor, positivo ? "rgba(34,197,94,.15)" : "rgba(255,45,45,.15)", positivo ? '<i class="ph ph-plus" aria-hidden="true"></i>' : '<i class="ph ph-minus" aria-hidden="true"></i>', "Resultado previsto",
      (positivo ? "" : "- ") + _fmtBRL(Math.abs(resultado)), resColor, "Projeção até o fim do mês")}
  </div>`;

  // ── Próximos vencimentos (esquerda) ──
  const vencRows = upcoming.length ? upcoming.map(x => {
    const b = vencBadge(x.d);
    return `<div style="display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid rgba(128,128,128,.13)">
      <div style="width:30px;text-align:center;font-size:1rem">${x.tag}</div>
      <div style="flex:1;min-width:0;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtmlSafe(x.name)}</div>
      <div style="font-size:.72rem;font-weight:600;padding:3px 9px;border-radius:20px;background:${b.bg};color:${b.fg}">${b.txt}</div>
      <div style="min-width:92px;text-align:right;font-weight:600;color:${x.amt >= 0 ? '#22c55e' : 'var(--red)'}">${x.amt >= 0 ? '+ ' : '- '}${_fmtBRL(Math.abs(x.amt))}</div>
    </div>`;
  }).join("") : `<div class="empty" style="padding:16px;text-align:center;color:var(--text-3)">Nada nos próximos 30 dias.</div>`;
  const vencCard = `
    <div class="mock-card" style="flex:2;min-width:300px">
      <h3><i class="ph ph-calendar-dots" aria-hidden="true"></i> Próximos vencimentos</h3>
      ${vencRows}
      ${upAll.length > upcoming.length ? `<div style="text-align:center;margin-top:10px"><a onclick="setRecurringTab('bills')" style="color:var(--pink,#FF2D8E);cursor:pointer;font-size:.84rem;font-weight:600">Ver todos os vencimentos →</a></div>` : ""}
    </div>`;

  // ── Resumo rápido + Próxima ação (direita) ──
  const resumoRow = (label, val, color) => `
    <div style="display:flex;justify-content:space-between;padding:5px 0;font-size:.88rem">
      <span style="color:var(--text-2)">${label}</span><span style="font-weight:600;color:${color}">${val}</span></div>`;
  const resumoCard = `
    <div class="mock-card">
      <h3><i class="ph ph-chart-bar" aria-hidden="true"></i> Resumo rápido</h3>
      ${resumoRow("Receitas fixas", _fmtBRL(totalReceitas), "#22c55e")}
      ${resumoRow("Gastos fixos", "- " + _fmtBRL(totalGastos), "#fb7185")}
      ${resumoRow("Boletos / contas", "- " + _fmtBRL(totalPend), "#fb7185")}
      <div style="border-top:1px solid rgba(128,128,128,.2);margin:6px 0;padding-top:6px;display:flex;justify-content:space-between;font-weight:700">
        <span>Resultado do mês</span><span style="color:${resColor}">${(positivo ? "" : "- ") + _fmtBRL(Math.abs(resultado))}</span></div>
    </div>`;

  let acaoMsg, acaoTab, acaoCta;
  if (entradas === 0 && saidas === 0) { acaoMsg = "Cadastre suas receitas e gastos fixos pra ver a previsão do mês."; acaoTab = "expenses"; acaoCta = "Começar"; }
  else if (positivo) { acaoMsg = "Você está no azul este mês. Que tal adiantar um boleto?"; acaoTab = "bills"; acaoCta = "Ver boletos"; }
  else if (totalPend >= totalGastos) { acaoMsg = "O maior peso está em boletos e contas a pagar."; acaoTab = "bills"; acaoCta = "Ver contas"; }
  else { acaoMsg = "O maior peso está nos gastos fixos."; acaoTab = "expenses"; acaoCta = "Ver gastos fixos"; }
  const acaoCard = `
    <div class="mock-card">
      <h3><i class="ph ph-target" aria-hidden="true"></i> Próxima ação</h3>
      <div style="font-size:.88rem;color:var(--text-2);margin-bottom:10px">${acaoMsg}</div>
      <button class="mock-cta outline" style="width:100%" onclick="setRecurringTab('${acaoTab}')">${acaoCta} →</button>
    </div>`;

  const twoCol = `<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start">
    ${vencCard}
    <div style="flex:1;min-width:250px;display:flex;flex-direction:column;gap:14px">${resumoCard}${acaoCard}</div>
  </div>`;

  wrap.innerHTML = head + alerta + statsRow + twoCol;
}

// O botão do header muda de destino conforme a aba ativa.
function openRecurringNewFromTab() {
  if (_recurringTab === "incomes") { openRecurringIncomeEditModal(); return; }
  if (_recurringTab === "bills") {
    // Boleto agora é entrada rápida inline (avulso) — foca o primeiro campo.
    const el = document.getElementById("boleto-quick-name");
    if (el) { try { el.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (_) {} el.focus(); }
    return;
  }
  openRecurringEditModal();
}

// ── Agenda de boletos (a pagar) ───────────────────────────────────────
const _billsChannel = makeFetchChannel(); // dedup + abort + geração

async function _fetchBills({ force = false } = {}) {
  return _billsChannel.run(async (signal) => {
    const resp = await fetch(`${API}/recurring-bills/${USER_ID}?include_paid=true`, {
      credentials: "same-origin",
      signal,
    });
    if (resp.status === 403) return { pro_required: true };
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    return data.bills || [];
  }, { force });
}

async function loadBillsView(forceFresh = false, { background = false } = {}) {
  const agendaEl = document.getElementById("recurring-bills-agenda");
  if (!agendaEl) return;
  loadForecast();  // independente dos boletos; o próprio gate cuida do não-Pro
  const proMsg = `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Boletos é uma feature <b>PigBank+</b>.</div>`;

  // Puxão: sem estado neutro por cima do render bom — falha REAL rejeita sem
  // tocar no DOM (o indicador do gesto fica âmbar). 403 vira o aviso Pro
  // (não é erro); superado sai neutro.
  if (background) {
    const data = await _fetchBills({ force: true });
    if (data === undefined) return;
    if (data && data.pro_required) { agendaEl.innerHTML = proMsg; return; }
    _renderBillsView(data);
    return;
  }

  try {
    const data = await _fetchBills({ force: true });
    if (data === undefined) return;
    if (data && data.pro_required) { agendaEl.innerHTML = proMsg; return; }
    _renderBillsView(data);
  } catch (_) {
    agendaEl.innerHTML = `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Não consegui carregar. Toque em Atualizar.</div>`;
  }
}

const _billToday = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
const _billDate = (b) => { const d = new Date(b.due_date + "T00:00:00"); d.setHours(0, 0, 0, 0); return d; };
const _billDaysUntil = (b) => Math.round((_billDate(b) - _billToday()) / 86400000);
function _billOverdue(b) { return _billDaysUntil(b) < 0; }

function _renderBillsView(bills) {
  const agendaEl = document.getElementById("recurring-bills-agenda");
  const paidEl = document.getElementById("recurring-bills-paid-list");
  const statsEl = document.getElementById("recurring-bills-stats");
  const today = _billToday();
  const endMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0); endMonth.setHours(0, 0, 0, 0);
  const pending = bills.filter(b => b.status === "pending");
  const paid = bills.filter(b => b.status === "paid");

  const sum = (arr) => arr.reduce((s, b) => s + (b.amount || 0), 0);
  const totalPend = sum(pending);
  const overdue = pending.filter(b => _billDaysUntil(b) < 0);
  const wk = pending.filter(b => { const n = _billDaysUntil(b); return n >= 0 && n <= 7; });
  const mo = pending.filter(b => _billDate(b) >= today && _billDate(b) <= endMonth);
  const prox = pending.filter(b => _billDaysUntil(b) >= 0).sort((a, b) => _billDate(a) - _billDate(b))[0];
  const proxTxt = prox
    ? `${escapeHtmlSafe(prox.name || "boleto")} · ${_billDate(prox).toLocaleDateString("pt-BR", { day: "2-digit", month: "short" })}`
    : "—";

  if (statsEl) statsEl.innerHTML = `
    <div class="stat-tile"><div class="stat-label">Em aberto</div>
      <div class="stat-value" style="color:var(--red)">${_fmtBRL(totalPend)}</div>
      <div class="stat-delta" style="color:var(--text-3)">${pending.length} boleto(s)</div></div>
    <div class="stat-tile"><div class="stat-label">Próx. 7 dias</div>
      <div class="stat-value">${_fmtBRL(sum(wk))}</div>
      <div class="stat-delta" style="color:var(--text-3)">${wk.length} boleto(s)</div></div>
    <div class="stat-tile"><div class="stat-label">Ainda este mês</div>
      <div class="stat-value">${_fmtBRL(sum(mo))}</div>
      <div class="stat-delta" style="color:var(--text-3)">${mo.length} boleto(s)</div></div>
    <div class="stat-tile"><div class="stat-label">${overdue.length ? "<i class='ph ph-warning' aria-hidden='true'></i> Vencidos" : "Próximo"}</div>
      <div class="stat-value" style="color:${overdue.length ? 'var(--red)' : 'var(--text)'}">${overdue.length ? _fmtBRL(sum(overdue)) : proxTxt}</div>
      <div class="stat-delta" style="color:var(--text-3)">${overdue.length ? `${overdue.length} atrasado(s)` : "a vencer"}</div></div>`;

  const buckets = [
    { label: "<i class='ph ph-warning' aria-hidden='true'></i> Vencidos", color: "#FF2D2D", items: pending.filter(b => _billDaysUntil(b) < 0) },
    { label: "Hoje", color: "#fbbf24", items: pending.filter(b => _billDaysUntil(b) === 0) },
    { label: "Próximos 7 dias", color: "#fbbf24", items: pending.filter(b => { const n = _billDaysUntil(b); return n >= 1 && n <= 7; }) },
    { label: "Ainda este mês", color: "var(--text-2)", items: pending.filter(b => _billDaysUntil(b) > 7 && _billDate(b) <= endMonth) },
    { label: "Mais pra frente", color: "var(--text-3)", items: pending.filter(b => _billDate(b) > endMonth) },
  ];
  const agendaHtml = buckets.filter(bk => bk.items.length).map(bk => {
    const rows = bk.items.sort((a, b) => _billDate(a) - _billDate(b)).map(_renderBillRow).join("");
    return `<div style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:${bk.color};margin:4px 0 6px;font-weight:600">
        <span>${bk.label} · ${bk.items.length}</span><span>${_fmtBRL(sum(bk.items))}</span></div>
      ${rows}</div>`;
  }).join("");
  if (agendaEl) agendaEl.innerHTML = agendaHtml
    || `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Nenhum boleto pendente. Adicione um acima <i class="ph ph-hand-pointing" aria-hidden="true"></i></div>`;

  if (paidEl) paidEl.innerHTML = paid.length
    ? paid.slice(0, 12).map(_renderBillPaidRow).join("")
    : `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Nada pago ainda.</div>`;
}

function _renderBillRow(b) {
  const due = _billDate(b);
  const overdue = _billOverdue(b);
  const quando = overdue ? "vencido" : _formatDueIn(due);
  const color = overdue ? "#FF2D2D" : "#fbbf24";
  const variavel = !!b.variable_amount;
  const temEstimativa = (b.amount || 0) > 0;
  const amtLabel = variavel
    ? (temEstimativa ? `~-${_fmtBRL(b.amount)}` : "a confirmar")
    : `-${_fmtBRL(b.amount)}`;
  const nameSafe = escapeJsString(b.name || "");
  return `
    <div class="tx-row">
      <div class="tx-icon" style="color:${color}"><i class="ph ph-receipt" aria-hidden="true"></i></div>
      <div class="tx-main">
        <div class="tx-desc">${escapeHtmlSafe(b.name || "Boleto")}${variavel ? ' <span style="font-size:.68rem;color:var(--text-3)">· valor varia</span>' : ""}</div>
        <div class="tx-meta">vence ${due.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" })} · <span style="color:${color}">${quando}</span></div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
        <div class="tx-amt red">${amtLabel}</div>
        <div style="display:flex;gap:4px">
          <button class="mock-cta" style="padding:3px 9px;font-size:.72rem" onclick="payBill(${b.id}, ${b.amount || 0}, '${nameSafe}', ${variavel})"><i class="ph ph-check" aria-hidden="true"></i> Pago</button>
          <button class="mock-cta outline" title="Editar" style="padding:3px 8px;font-size:.72rem" onclick="editBoleto(${b.id}, '${nameSafe}', ${b.amount || 0}, '${b.due_date}')"><i class="ph ph-pencil-simple" aria-hidden="true"></i></button>
          <button class="mock-cta outline" title="Apagar" style="padding:3px 8px;font-size:.72rem" onclick="deleteBoleto(${b.id}, '${nameSafe}')"><i class="ph ph-trash" aria-hidden="true"></i></button>
        </div>
      </div>
    </div>`;
}

function _renderBillPaidRow(b) {
  const paidVal = b.paid_amount != null ? b.paid_amount : b.amount;
  const when = b.paid_at ? new Date(b.paid_at).toLocaleDateString("pt-BR", { day: "2-digit", month: "short" }) : "";
  return `
    <div class="tx-row">
      <div class="tx-icon" style="color:#22c55e"><i class="ph ph-check" aria-hidden="true"></i></div>
      <div class="tx-main">
        <div class="tx-desc">${escapeHtmlSafe(b.name || "Conta")}</div>
        <div class="tx-meta">paga ${when}</div>
      </div>
      <div class="tx-amt" style="color:var(--text-2)">-${_fmtBRL(paidVal)}</div>
    </div>`;
}

// Extrai uma mensagem de erro LEGÍVEL da resposta, nunca "[object Object]".
// Cobre: texto puro, {detail:"..."} (HTTPException), {detail:[{msg}]} (422 do
// FastAPI) e {detail:{...}} (ex: pro_required).
async function _errDetail(resp) {
  const txt = await resp.text().catch(() => "");
  let d;
  try { d = JSON.parse(txt).detail; } catch (_) { return txt || `Erro (HTTP ${resp.status})`; }
  if (d == null) return txt || `Erro (HTTP ${resp.status})`;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map(e => (e && e.msg) || String(e)).join("; ");
  if (typeof d === "object") return d.message || d.msg || d.error || JSON.stringify(d);
  return String(d);
}

async function payBill(billId, estimate, name, variavel) {
  // Valor da conta a pagar é sempre confirmado na hora (pode variar). Se tem
  // estimativa, pré-preenche pra ser só ajustar/confirmar; senão abre vazio.
  const pergunta = variavel
    ? `Quanto veio a conta de "${name}" este mês? (R$)`
    : `Quanto você pagou de "${name}"? (R$)`;
  const prefill = Number(estimate) > 0 ? Number(estimate).toFixed(2) : "";
  const raw = window.prompt(pergunta, prefill);
  if (raw === null) return;                       // cancelou
  const amount = parseFloat(String(raw).replace(",", "."));
  if (isNaN(amount) || amount <= 0) {
    await alertModal("Digite um valor válido (maior que zero).", { title: "Valor inválido" });
    return;
  }
  try {
    const resp = await fetch(`${API}/recurring-bills/${USER_ID}/${billId}/pay`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ amount }),
    });
    if (resp.status === 403) return;   // pro_required: interceptor abre upgrade
    if (!resp.ok) {
      throw new Error(await _errDetail(resp));
    }
    showToast(`✓ Boleto pago: ${_fmtBRL(amount)}`);
    loadBillsView(true);
    sendRefresh();
  } catch (err) {
    await alertModal(String(err.message || err), { title: "Erro ao pagar" });
  }
}

// Entrada rápida de boleto avulso (fornecedor + valor + vencimento).
let _boletoAdding = false;
async function quickAddBoleto() {
  if (_boletoAdding) return;
  const nameEl = document.getElementById("boleto-quick-name");
  const amtEl = document.getElementById("boleto-quick-amount");
  const dueEl = document.getElementById("boleto-quick-due");
  if (!nameEl) return;
  const name = nameEl.value.trim();
  const amount = parseFloat(amtEl.value);
  const due = dueEl.value;
  if (!name) { await alertModal("Informe o fornecedor ou a descrição do boleto.", { title: "Falta o nome" }); nameEl.focus(); return; }
  if (!Number.isFinite(amount) || amount <= 0) { await alertModal("Informe um valor válido.", { title: "Valor" }); amtEl.focus(); return; }
  if (!due) { await alertModal("Informe a data de vencimento.", { title: "Vencimento" }); dueEl.focus(); return; }
  const btn = document.getElementById("boleto-quick-btn");
  _boletoAdding = true;
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch(`${API}/recurring-bills/${USER_ID}`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, amount, due_date: due }),
    });
    if (resp.status === 403) {
      // Free furou o gate visual: o interceptor global já abre o modal de
      // upgrade. Só saímos sem mostrar erro (o detail é um objeto pro_required).
      return;
    }
    if (!resp.ok) {
      throw new Error(await _errDetail(resp));
    }
    showToast("✓ Boleto adicionado");
    nameEl.value = ""; amtEl.value = "";  // mantém a data (costuma cadastrar vários próximos)
    nameEl.focus();
    loadBillsView(true);
    sendRefresh();
  } catch (err) {
    await alertModal(String(err.message || err), { title: "Erro ao adicionar" });
  } finally {
    _boletoAdding = false;
    if (btn) btn.disabled = false;
  }
}

async function editBoleto(id, name, amount, dueISO) {
  const nv = window.prompt(`Novo valor de "${name}" (R$):`, Number(amount || 0).toFixed(2));
  if (nv === null) return;
  const amt = parseFloat(String(nv).replace(",", "."));
  if (isNaN(amt) || amt <= 0) { await alertModal("Valor inválido.", { title: "Valor" }); return; }
  const nd = window.prompt("Vencimento (AAAA-MM-DD):", dueISO);
  if (nd === null) return;
  try {
    const resp = await fetch(`${API}/recurring-bills/${USER_ID}/${id}`, {
      method: "PATCH",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ amount: amt, due_date: nd }),
    });
    if (resp.status === 403) return;   // pro_required: interceptor abre upgrade
    if (!resp.ok) {
      throw new Error(await _errDetail(resp));
    }
    showToast("✓ Boleto atualizado");
    loadBillsView(true);
  } catch (err) {
    await alertModal(String(err.message || err), { title: "Erro ao editar" });
  }
}

async function deleteBoleto(id, name) {
  const ok = await confirmModal(`Apagar o boleto "${name}"? (não afeta boletos já pagos)`, { title: "Apagar boleto" });
  if (!ok) return;
  try {
    const resp = await fetch(`${API}/recurring-bills/${USER_ID}/${id}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (resp.status === 403) return;   // pro_required: interceptor abre upgrade
    if (!resp.ok) {
      throw new Error(await _errDetail(resp));
    }
    showToast(" Boleto apagado");
    loadBillsView(true);
  } catch (err) {
    await alertModal(String(err.message || err), { title: "Erro ao apagar" });
  }
}

// Simulador "tô tranquilo nesse prazo?" — projeta o caixa até uma data.
async function simularPrazo() {
  const dateEl = document.getElementById("boleto-sim-date");
  const amtEl = document.getElementById("boleto-sim-amount");
  const resEl = document.getElementById("boleto-sim-result");
  if (!dateEl || !resEl) return;
  const d = dateEl.value;
  if (!d) { await alertModal("Escolha a data do prazo.", { title: "Prazo" }); return; }
  const amount = parseFloat(amtEl.value);
  const q = new URLSearchParams({ date: d });
  if (Number.isFinite(amount) && amount > 0) q.set("amount", String(amount));
  resEl.innerHTML = `<div class="empty" style="color:var(--text-3);padding:8px">Calculando…</div>`;
  try {
    const resp = await fetch(`${API}/recurring-bills/${USER_ID}/projection?${q.toString()}`, { credentials: "same-origin" });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    _renderProjection(data.projection);
  } catch (_) {
    resEl.innerHTML = `<div class="empty" style="color:var(--text-3);padding:8px">Não consegui calcular agora.</div>`;
  }
}

function _renderProjection(p) {
  const resEl = document.getElementById("boleto-sim-result");
  if (!resEl || !p) return;
  const ok = p.tranquilo;
  const accent = ok ? "#22c55e" : "#FF2D2D";
  const alvo = new Date(p.target + "T00:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "long" });
  const header = ok
    ? `<i class="ph ph-smiley" aria-hidden="true"></i> Tranquilo até ${alvo}, sobra ${_fmtBRL(p.projetado)}`
    : `<i class="ph ph-warning" aria-hidden="true"></i> Aperta até ${alvo}, falta ${_fmtBRL(Math.abs(p.projetado))}`;
  const line = (label, val, positive) => `
    <div style="display:flex;justify-content:space-between;font-size:.82rem;padding:2px 0">
      <span style="color:var(--text-2)">${label}</span>
      <span style="color:${positive ? 'var(--text)' : 'var(--red)'}">${positive ? '+' : '−'} ${_fmtBRL(Math.abs(val))}</span>
    </div>`;
  resEl.innerHTML = `
    <div style="border-radius:10px;padding:12px;background:${ok ? 'rgba(34,197,94,.10)' : 'rgba(255,45,45,.10)'};border:1px solid ${ok ? 'rgba(34,197,94,.35)' : 'rgba(255,45,45,.35)'}">
      <div style="font-weight:700;color:${accent};margin-bottom:8px">${header}</div>
      ${line("Saldo hoje", p.saldo_atual, p.saldo_atual >= 0)}
      ${p.receitas_previstas > 0 ? line("Receitas previstas", p.receitas_previstas, true) : ""}
      ${p.gastos_fixos_previstos > 0 ? line("Gastos fixos", p.gastos_fixos_previstos, false) : ""}
      ${line(`Boletos até lá (${p.n_boletos})`, p.boletos_ate, false)}
      ${p.faturas_cartao > 0 ? line("Faturas de cartão até lá", p.faturas_cartao, false) : ""}
      ${p.boleto_novo > 0 ? line("Boleto novo em análise", p.boleto_novo, false) : ""}
      <div style="border-top:1px solid rgba(128,128,128,.25);margin-top:6px;padding-top:6px;display:flex;justify-content:space-between;font-weight:700">
        <span>Projeção do caixa</span><span style="color:${accent}">${_fmtBRL(p.projetado)}</span>
      </div>
      ${_forecastBanksWarning(p)}
      <div style="font-size:.68rem;color:var(--text-3);margin-top:6px">Estimativa: saldo + receitas fixas − gastos fixos − boletos. Não inclui gastos avulsos futuros.</div>
    </div>`;
}

// Previsão de saldo 30/60/90 dias (feature Pro). Só busca se o gate liberar; pro
// não-Pro o card fica com o teaser travado (applyProGates + click→upgrade modal).
const _forecastLockedMsg = `<div class="empty" style="padding:8px;color:var(--text-3)">Assine o <b>Pro</b> pra ver a previsão do seu saldo a 30, 60 e 90 dias.</div>`;

async function loadForecast() {
  const resEl = document.getElementById("forecast-result");
  if (!resEl) return;
  if (!featureAllowed("forecast")) { resEl.innerHTML = _forecastLockedMsg; return; }
  resEl.innerHTML = `<div class="empty" style="color:var(--text-3);padding:8px">Calculando…</div>`;
  try {
    const resp = await fetch(`${API}/forecast/${USER_ID}`, { credentials: "same-origin" });
    if (resp.status === 403) { resEl.innerHTML = _forecastLockedMsg; return; }
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    _renderForecast(data.forecast);
  } catch (_) {
    resEl.innerHTML = `<div class="empty" style="color:var(--text-3);padding:8px">Não consegui calcular agora.</div>`;
  }
}

function _renderForecast(fc) {
  const resEl = document.getElementById("forecast-result");
  if (!resEl || !fc || !fc.horizons) return;
  const tile = (dias, p) => {
    if (!p) return "";
    const ok = p.tranquilo;
    const accent = ok ? "#22c55e" : "#FF2D2D";
    return `
      <div style="flex:1;min-width:120px;border-radius:10px;padding:12px;background:${ok ? 'rgba(34,197,94,.10)' : 'rgba(255,45,45,.10)'};border:1px solid ${ok ? 'rgba(34,197,94,.30)' : 'rgba(255,45,45,.30)'}">
        <div style="font-size:.72rem;color:var(--text-3);text-transform:uppercase;letter-spacing:.04em">Em ${dias} dias</div>
        <div style="font-weight:700;font-size:1.05rem;color:${accent};margin-top:2px">${_fmtBRL(p.projetado)}</div>
        <div style="font-size:.72rem;color:var(--text-2);margin-top:2px">${ok ? "no positivo" : "no vermelho"}</div>
      </div>`;
  };
  const h = fc.horizons || {};
  resEl.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      ${tile(30, h["30"])}${tile(60, h["60"])}${tile(90, h["90"])}
    </div>
    ${_forecastBanksWarning(fc)}
    <div style="font-size:.68rem;color:var(--text-3);margin-top:8px">Estimativa: saldo + receitas fixas − gastos fixos − boletos até a data. Não inclui gastos avulsos futuros.</div>`;
}

// Aviso quando a previsão não parte do saldo consolidado: ou o usuário tem bancos
// conectados fora do saldo (gate desligado → banks_excluded), ou a consulta ao
// consolidado falhou e não dá pra confirmar o saldo (balance_source "unavailable").
function _forecastBanksWarning(fc) {
  if (!fc) return "";
  const unavailable = fc.balance_source === "unavailable";
  if (!fc.banks_excluded && !unavailable) return "";
  const msg = unavailable
    ? "Não foi possível confirmar seu saldo consolidado agora — esta previsão pode não incluir o saldo dos seus bancos."
    : "Seus bancos conectados não estão somados nesta previsão — ela usa só o saldo da sua Carteira.";
  return `<div style="display:flex;gap:6px;align-items:flex-start;margin-top:8px;padding:8px 10px;border-radius:8px;background:rgba(245,158,11,.10);border:1px solid rgba(245,158,11,.35);font-size:.72rem;color:var(--text-2)">
    <i class="ph ph-warning" aria-hidden="true" style="color:#F59E0B;margin-top:1px"></i>
    <span>${msg}</span>
  </div>`;
}

async function _fetchRecurringIncomes({ force = false } = {}) {
  return _recurringIncomeChannel.run(async (signal) => {
    const resp = await fetch(`${API}/recurring-incomes/${USER_ID}`, {
      credentials: "same-origin",
      headers: csrfHeaders(),
      signal,
    });
    if (resp.status === 403) {
      const data = await resp.json().catch(() => ({}));
      if (data?.detail?.error === "pro_required") return { pro_required: true };
    }
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      throw new Error(`(HTTP ${resp.status}) ${detail}`);
    }
    const data = await resp.json();
    return data.incomes || [];
  }, { force });
}

// Puxa o total de gastos fixos pra "sobra prevista" quando o user entrou direto
// na aba de receitas. Fire-and-forget: nunca bloqueia nem falha a view. No
// puxão (background) o segundo render só acontece se o cache de receitas ainda
// é o mesmo — senão sobrescreveria um render mais novo.
function _hydrateRecurringIncomeSobra() {
  if (_recurringCache) return;
  const snapshot = _recurringIncomeCache;
  _fetchRecurring().then(exp => {
    if (exp && exp !== undefined && !exp.pro_required) {
      _recurringCache = exp;
      if (_recurringIncomeCache === snapshot) _renderRecurringIncomeView(_recurringIncomeCache);
    }
  }).catch(() => {});
}

async function loadRecurringIncomeView(forceFresh = false, { background = false } = {}) {
  const stats = document.getElementById("recurring-income-stats");
  if (!stats) return;
  if (!USER_ID) {
    if (background) throw new Error("receitas fixas: sessão ainda não pronta");
    setTimeout(() => loadRecurringIncomeView(forceFresh), 300);
    return;
  }

  // Puxão: sem skeleton, fetch antes de render, falha real rejeita sem tocar DOM.
  if (background) {
    const data = await _fetchRecurringIncomes({ force: true });
    if (data === undefined) return;
    if (data && data.pro_required) { _renderRecurringIncomeProGate(); return; }
    _recurringIncomeCache = data;
    _renderRecurringIncomeView(data);
    _hydrateRecurringIncomeSobra();
    return;
  }

  if (_recurringIncomeCache && !forceFresh) {
    _renderRecurringIncomeView(_recurringIncomeCache);
    _fetchRecurringIncomes().then(fresh => {
      if (fresh && !fresh.pro_required) {
        _recurringIncomeCache = fresh;
        _renderRecurringIncomeView(fresh);
      }
    }).catch(() => {});
    return;
  }

  stats.innerHTML = `
    <div class="stat-tile"><div class="stat-label">Total mensal</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Receitas ativas</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Próximo recebimento</div><div class="sk sk-h2"></div></div>
    <div class="stat-tile"><div class="stat-label">Sobra prevista</div><div class="sk sk-h2"></div></div>
  `;
  try {
    const data = await _fetchRecurringIncomes({ force: true });
    if (data === undefined) return;
    if (data && data.pro_required) {
      _renderRecurringIncomeProGate();
      return;
    }
    _recurringIncomeCache = data;
    _renderRecurringIncomeView(data);
    // Sobra prevista precisa do total de gastos fixos: puxa em background
    // se o user entrou direto na aba de receitas.
    _hydrateRecurringIncomeSobra();
  } catch (err) {
    stats.innerHTML = `<div class="empty" style="grid-column:1/-1;color:var(--red)">Erro: ${escapeHtmlSafe(String(err.message || err))}</div>`;
  }
}

function _renderRecurringIncomeProGate() {
  const stats = document.getElementById("recurring-income-stats");
  if (stats) stats.innerHTML = "";
  ["recurring-income-primary-list", "recurring-income-extra-list",
   "recurring-income-upcoming-list", "recurring-income-adjustments-list"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
  const primary = document.getElementById("recurring-income-primary-list");
  if (primary) {
    primary.innerHTML = `
      <div class="empty" style="padding:30px;text-align:center;color:var(--text-3)">
        <div style="font-size:2.5rem;margin-bottom:10px"><i class="ph ph-lock" aria-hidden="true"></i></div>
        <div style="font-size:1.05rem;font-weight:700;color:var(--text);margin-bottom:6px">Receitas fixas é Pro</div>
        <div style="margin-bottom:14px">Cadastre salário, aluguel e freelas recorrentes pra o Piggy lançar automaticamente todo mês.</div>
        <button class="mock-cta" onclick="showUpgradeModal('recurring_expenses')"><i class="ph ph-star" aria-hidden="true"></i> Ver Pro</button>
      </div>`;
  }
}

function _renderRecurringIncomeView(items) {
  const stats = document.getElementById("recurring-income-stats");
  const priEl = document.getElementById("recurring-income-primary-list");
  const extEl = document.getElementById("recurring-income-extra-list");
  const upEl = document.getElementById("recurring-income-upcoming-list");
  const adjEl = document.getElementById("recurring-income-adjustments-list");
  if (!stats || !priEl) return;

  const list = items || [];
  const active = list.filter(r => r.is_active);
  const total = active.reduce((s, r) => s + _recMonthlyEquiv(r), 0);
  const nPrimary = active.filter(r => r.is_primary).length;
  const nExtra = active.filter(r => !r.is_primary).length;

  // Próximo recebimento (1ª data do pay_day em/depois de hoje E do start_date)
  const today = new Date();
  const nextPay = active.map(r => ({
    rec: r, date: _nextRecurringOccurrence(r.pay_day, r.start_date, r.frequency, r.pay_month),
  })).sort((a, b) => a.date - b.date);
  const next = nextPay[0];

  const adjustments = active.filter(r => r.last_amount != null && r.last_amount_changed_at);

  // Sobra prevista = receitas fixas − gastos fixos. Projeção do mês, NÃO é
  // o saldo da conta nem o "sobrou" real (que conta lançamentos avulsos).
  const hasExpenseData = Array.isArray(_recurringCache);
  const fixedExpenses = (hasExpenseData ? _recurringCache : []).filter(r => r.is_active)
    .reduce((s, r) => s + _recMonthlyEquiv(r), 0);
  const leftover = total - fixedExpenses;

  stats.innerHTML = `
    <div class="stat-tile" style="animation-delay:0ms">
      <div class="stat-label">Total mensal</div>
      <div class="stat-value" style="color:var(--green)">${_fmtBRL(total)}</div>
      <div class="stat-delta" style="color:var(--text-3)">${active.length} recorrente${active.length === 1 ? "" : "s"}</div>
    </div>
    <div class="stat-tile" style="animation-delay:60ms">
      <div class="stat-label">Receitas ativas</div>
      <div class="stat-value">${active.length}</div>
      <div class="stat-delta" style="color:var(--text-3)">${nPrimary} principal · ${nExtra} extra${nExtra === 1 ? "" : "s"}</div>
    </div>
    <div class="stat-tile" style="animation-delay:120ms">
      <div class="stat-label">Próximo recebimento</div>
      <div class="stat-value" style="font-size:1.15rem">${next ? escapeHtmlSafe(next.rec.name) : "—"}</div>
      <div class="stat-delta" style="color:var(--text-3)">${next ? _formatDueIn(next.date) : "sem agendamentos"}</div>
    </div>
    <div class="stat-tile" style="animation-delay:180ms">
      <div class="stat-label">Sobra prevista</div>
      <div class="stat-value" style="color:${!hasExpenseData ? "var(--text-3)" : (leftover >= 0 ? "var(--green)" : "var(--red)")}">${hasExpenseData ? _fmtBRL(leftover) : "—"}</div>
      <div class="stat-delta" style="color:var(--text-3)">${hasExpenseData ? "receitas fixas − gastos fixos" : "calculando…"}</div>
    </div>
  `;

  const primary = active.filter(r => r.is_primary);
  const extra = active.filter(r => !r.is_primary);

  priEl.innerHTML = primary.length
    ? primary.map(r => _renderRecurringIncomeRow(r)).join("")
    : `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Sem renda principal cadastrada.</div>`;
  extEl.innerHTML = extra.length
    ? extra.map(r => _renderRecurringIncomeRow(r)).join("")
    : `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Sem rendas extras cadastradas.</div>`;

  // Próximos 7 dias
  const upcoming = nextPay.filter(x => (x.date - today) / (1000 * 60 * 60 * 24) <= 7);
  upEl.innerHTML = upcoming.length
    ? upcoming.map(x => `
        <div class="tx-row">
          <div class="tx-icon" style="color:var(--green)">${phIcon(_recurringIncomeEmoji(x.rec))}</div>
          <div class="tx-main">
            <div class="tx-desc">${escapeHtmlSafe(x.rec.name)} · ${x.date.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" })}</div>
            <div class="tx-meta">${_formatDueIn(x.date)} · ${x.rec.is_primary ? "Renda principal" : "Renda extra"}</div>
          </div>
          <div class="tx-amt green">+${_fmtBRL(x.rec.amount)}</div>
        </div>
      `).join("")
    : `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Nada nos próximos 7 dias.</div>`;

  // Reajustes (aumento de salário aparece aqui)
  adjEl.innerHTML = adjustments.length
    ? adjustments.map(r => {
        const delta = r.amount - r.last_amount;
        const sign = delta > 0 ? "+" : "−";
        const pct = r.last_amount > 0 ? ((delta / r.last_amount) * 100).toFixed(1) : "—";
        const when = r.last_amount_changed_at ? new Date(r.last_amount_changed_at).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) : "";
        return `
          <div class="tx-row">
            <div class="tx-icon" style="color:${delta > 0 ? "#22c55e" : "#fbbf24"}">${delta > 0 ? '<i class="ph ph-confetti" aria-hidden="true"></i>' : '<i class="ph ph-warning" aria-hidden="true"></i>'}</div>
            <div class="tx-main">
              <div class="tx-desc">${escapeHtmlSafe(r.name)} ${delta > 0 ? "aumentou" : "diminuiu"} ${sign}${_fmtBRL(Math.abs(delta))}</div>
              <div class="tx-meta">${pct}% vs valor anterior · detectado em ${when}</div>
            </div>
          </div>`;
      }).join("")
    : `<div class="empty" style="padding:20px;text-align:center;color:var(--text-3)">Nenhum reajuste detectado ainda.</div>`;
}

function _recurringIncomeEmoji(r) {
  const name = (r.name || "").toLowerCase();
  if (name.includes("salário") || name.includes("salario") || name.includes("clt")) return "💼";
  if (name.includes("aluguel")) return "🏠";
  if (name.includes("freela") || name.includes("pj")) return "🧑‍💻";
  if (name.includes("dividendo") || name.includes("rendimento")) return "📈";
  if (name.includes("pensão") || name.includes("pensao")) return "🤝";
  if (name.includes("aposentadoria") || name.includes("inss")) return "🧓";
  if (name.includes("bolsa") || name.includes("estágio") || name.includes("estagio")) return "🎓";
  return RECURRING_INCOME_CATEGORY_EMOJI[(r.category || "").toLowerCase()] || "💰";
}

function _renderRecurringIncomeRow(r) {
  const quandoInc = r.frequency === "annual" && r.pay_month
    ? `anual · ${r.pay_day}/${_MESES_ABREV[r.pay_month - 1]}`
    : `dia ${r.pay_day}`;
  const when = `${r.is_primary ? "Renda principal" : "Renda extra"} · ${quandoInc}`;
  let adjustText = "";
  if (r.last_amount != null && r.last_amount > 0) {
    const delta = r.amount - r.last_amount;
    if (Math.abs(delta) > 0.005) {
      const arrow = delta > 0 ? "↑" : "↓";
      const color = delta > 0 ? "#22c55e" : "#fbbf24";
      adjustText = ` · <span style="color:${color}">${_fmtBRL(r.last_amount)} → ${_fmtBRL(r.amount)} ${arrow}</span>`;
    }
  }
  const safeRecJson = escapeHtmlSafe(JSON.stringify(r));
  return `
    <div class="tx-row" style="cursor:pointer" onclick="openRecurringIncomeEditModal(${safeRecJson})">
      <div class="tx-icon">${phIcon(_recurringIncomeEmoji(r))}</div>
      <div class="tx-main">
        <div class="tx-desc">${escapeHtmlSafe(r.name)}</div>
        <div class="tx-meta">${when}${adjustText}${_futureStartHint(r.start_date)}</div>
      </div>
      <div class="tx-amt green">+${_fmtBRL(r.amount)}</div>
    </div>
  `;
}

// ── Modal cadastrar/editar receita recorrente ─────────────────────────

let _recurringIncomeEditState = { id: null };

function _ensureRecurringIncomeModal() {
  if (document.getElementById("recurring-income-edit-overlay")) return;
  const html = `
    <div class="overlay" id="recurring-income-edit-overlay">
      <div class="modal wide">
        <h3 id="recurring-income-edit-title">Nova receita fixa</h3>
        <p class="msub" style="background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);border-radius:8px;padding:10px 12px;margin-bottom:14px;font-size:.78rem">
          <i class="ph ph-coins" aria-hidden="true"></i> <strong>Importante:</strong> essa receita será <strong>lançada automaticamente</strong> todo mês no dia escolhido. Se o valor variar, é só editar aqui: o Piggy registra o reajuste.
        </p>
        <form id="recurring-income-edit-form" onsubmit="event.preventDefault(); saveRecurringIncome();">
          <div class="invest-form">
            <div class="form-row">
              <div class="field" style="flex:2">
                <label for="recurring-income-name">Nome *</label>
                <input type="text" id="recurring-income-name" required maxlength="80" placeholder="Ex: Salário, Aluguel recebido..." />
              </div>
              <div class="field" style="flex:1">
                <label for="recurring-income-amount">Valor (R$) *</label>
                <input type="number" id="recurring-income-amount" min="0.01" step="0.01" required placeholder="0,00" />
              </div>
            </div>
            <div class="form-row">
              <div class="field">
                <label for="recurring-income-pay-day">Dia do recebimento *</label>
                <input type="number" id="recurring-income-pay-day" min="1" max="31" required placeholder="1-31" />
              </div>
              <div class="field">
                <label for="recurring-income-category">Categoria *</label>
                <input type="text" id="recurring-income-category" required maxlength="40" list="recurring-income-categories" placeholder="salário, freela, aluguel..." />
                <datalist id="recurring-income-categories">
                  <option value="salário"></option>
                  <option value="freela"></option>
                  <option value="aluguel"></option>
                  <option value="dividendos"></option>
                  <option value="vendas"></option>
                  <option value="pensão"></option>
                  <option value="aposentadoria"></option>
                  <option value="bolsa"></option>
                  <option value="outros"></option>
                </datalist>
              </div>
            </div>
            <div class="form-row">
              <div class="field">
                <label for="recurring-income-frequency">Frequência *</label>
                <select id="recurring-income-frequency" onchange="_toggleRecurringMonthField('recurring-income')">
                  <option value="monthly">Mensal (todo mês)</option>
                  <option value="annual">Anual (1x por ano)</option>
                </select>
              </div>
              <div class="field" id="recurring-income-month-field" style="display:none">
                <label for="recurring-income-month">Mês do recebimento *</label>
                <select id="recurring-income-month">${_monthOptionsHTML()}</select>
              </div>
            </div>
            <div class="form-row">
              <div class="field">
                <label for="recurring-income-start-date">Começa a partir de</label>
                <input type="date" id="recurring-income-start-date" />
                <span style="font-size:.68rem;color:var(--text-3);margin-top:4px;display:block">O 1º crédito é no dia do recebimento em/após esta data. Deixe hoje pra começar já.</span>
              </div>
            </div>
            <div class="field">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="checkbox" id="recurring-income-is-primary" />
                <span>Marcar como renda principal (salário / fonte fixa)</span>
              </label>
            </div>
            <div class="field">
              <label for="recurring-income-notes">Notas (opcional)</label>
              <input type="text" id="recurring-income-notes" maxlength="200" placeholder="Anotações..." />
            </div>
          </div>
          <div class="modal-acts" style="margin-top:18px;display:flex;gap:8px;align-items:center">
            <button type="button" class="inst-delete-btn" id="recurring-income-delete-btn" style="display:none" onclick="deleteRecurringIncomeFromModal()"><i class="ph ph-trash" aria-hidden="true"></i> Excluir</button>
            <span style="flex:1"></span>
            <button type="button" class="btn-cancel" onclick="closeRecurringIncomeEditModal()">Cancelar</button>
            <button type="submit" class="btn-save">Salvar</button>
          </div>
        </form>
      </div>
    </div>
  `;
  document.body.insertAdjacentHTML("beforeend", html);
  document.getElementById("recurring-income-edit-overlay").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeRecurringIncomeEditModal();
  });
}

function openRecurringIncomeEditModal(rec) {
  _ensureRecurringIncomeModal();
  const isEdit = !!(rec && rec.id);
  _recurringIncomeEditState = { id: isEdit ? rec.id : null };

  document.getElementById("recurring-income-edit-title").textContent = isEdit ? "Editar receita fixa" : "Nova receita fixa";
  document.getElementById("recurring-income-name").value = isEdit ? rec.name : "";
  document.getElementById("recurring-income-amount").value = isEdit ? Number(rec.amount).toFixed(2) : "";
  document.getElementById("recurring-income-pay-day").value = isEdit ? rec.pay_day : "";
  document.getElementById("recurring-income-start-date").value = isEdit ? (rec.start_date || "") : new Date().toLocaleDateString("en-CA");
  document.getElementById("recurring-income-category").value = isEdit ? rec.category : "";
  document.getElementById("recurring-income-frequency").value = isEdit ? (rec.frequency || "monthly") : "monthly";
  document.getElementById("recurring-income-month").value = (isEdit && rec.pay_month) ? rec.pay_month : (new Date().getMonth() + 1);
  document.getElementById("recurring-income-is-primary").checked = isEdit ? !!rec.is_primary : false;
  document.getElementById("recurring-income-notes").value = isEdit ? (rec.notes || "") : "";
  document.getElementById("recurring-income-delete-btn").style.display = isEdit ? "" : "none";
  _toggleRecurringMonthField("recurring-income");

  document.getElementById("recurring-income-edit-overlay").classList.add("open");
  setTimeout(() => document.getElementById("recurring-income-name").focus(), 50);
}

function closeRecurringIncomeEditModal() {
  document.getElementById("recurring-income-edit-overlay")?.classList.remove("open");
}

let _recurringIncomeSaving = false;
async function saveRecurringIncome() {
  if (_recurringIncomeSaving) return;  // bloqueia double-submit

  const payload = {
    name: document.getElementById("recurring-income-name").value.trim(),
    amount: parseFloat(document.getElementById("recurring-income-amount").value),
    category: document.getElementById("recurring-income-category").value.trim(),
    pay_day: parseInt(document.getElementById("recurring-income-pay-day").value, 10),
    start_date: document.getElementById("recurring-income-start-date").value || null,
    frequency: document.getElementById("recurring-income-frequency").value,
    pay_month: document.getElementById("recurring-income-frequency").value === "annual"
      ? parseInt(document.getElementById("recurring-income-month").value, 10) : null,
    is_primary: document.getElementById("recurring-income-is-primary").checked,
    notes: document.getElementById("recurring-income-notes").value.trim() || null,
  };

  _recurringIncomeSaving = true;
  const isEdit = !!_recurringIncomeEditState.id;
  const editingId = _recurringIncomeEditState.id;

  // Optimistic: fecha modal imediatamente. Se der erro, reabre com os dados.
  closeRecurringIncomeEditModal();

  try {
    const url = isEdit
      ? `${API}/recurring-incomes/${USER_ID}/${editingId}`
      : `${API}/recurring-incomes/${USER_ID}`;
    const resp = await fetch(url, {
      method: isEdit ? "PATCH" : "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      let detail = txt;
      try { detail = JSON.parse(txt).detail || txt; } catch(_) {}
      if (resp.status === 403) {
        await alertModal("Receitas fixas é uma feature Pro.", { title: "Recurso Pro" });
        return;
      }
      throw new Error(detail);
    }
    showToast(isEdit ? "✓ Receita fixa atualizada" : "✓ Receita fixa cadastrada");
    _recurringIncomeCache = null;
    loadRecurringIncomeView(true);  // sem await — re-render em paralelo
    sendRefresh();
  } catch (err) {
    openRecurringIncomeEditModal(isEdit ? { ...payload, id: editingId } : payload);
    await alertModal(String(err.message || err), { title: "Erro ao salvar" });
  } finally {
    _recurringIncomeSaving = false;
  }
}

async function deleteRecurringIncomeFromModal() {
  if (!_recurringIncomeEditState.id) return;
  const ok = await confirmModal(
    "Excluir esta receita fixa? Lançamentos passados ficam preservados. Só não vai mais lançar automaticamente.",
    { title: "Excluir receita fixa", okText: "Excluir", danger: true },
  );
  if (!ok) return;
  try {
    const resp = await fetch(`${API}/recurring-incomes/${USER_ID}/${_recurringIncomeEditState.id}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!resp.ok) throw new Error(await resp.text());
    closeRecurringIncomeEditModal();
    showToast("✓ Receita fixa excluída");
    _recurringIncomeCache = null;
    await loadRecurringIncomeView(true);
  } catch (err) {
    await alertModal(String(err.message || err), { title: "Erro" });
  }
}

// ── View Análises (Sprint 6) ─────────────────────────────────────────────
// Cada widget tem dado próprio vindo de um endpoint separado, pra preparar o
// painel personalizável que vem em breve (user vai poder ligar/desligar cards).
let _analyticsCache = null;        // { kpis, evolution, categories, weekday, merchants, months }
let _analyticsRetryTimer = null;
const _analyticsChannel = makeFetchChannel(); // dedup + abort + geração (7 fetches, 1 signal)
let _analyticsChartInstances = [];
let _analyticsCurrentMonths = 6;

function _analyticsTheme() {
  const isLight = document.body.classList.contains("light");
  return {
    isLight,
    tickColor: isLight ? "rgba(15,23,42,0.65)" : "rgba(255,255,255,0.55)",
    grid:      isLight ? "rgba(15,23,42,0.08)" : "rgba(255,255,255,0.05)",
  };
}

function _destroyAnalyticsCharts() {
  if (Array.isArray(_analyticsChartInstances) && _analyticsChartInstances.length) {
    _analyticsChartInstances.forEach(c => { try { c.destroy(); } catch(_) {} });
  }
  _analyticsChartInstances = [];
}

async function loadAnalyticsView(forceFresh = false, months = null, { background = false } = {}) {
  if (months != null) _analyticsCurrentMonths = Math.max(1, Math.min(36, parseInt(months, 10) || 6));

  const statsEl = document.getElementById("analytics-stats");
  if (!statsEl) return;

  if (!USER_ID) {
    if (background) throw new Error("análises: sessão ainda não pronta");
    if (!_analyticsRetryTimer) {
      _analyticsRetryTimer = setInterval(() => {
        if (USER_ID) {
          clearInterval(_analyticsRetryTimer);
          _analyticsRetryTimer = null;
          loadAnalyticsView(forceFresh);
        }
      }, 250);
    }
    return;
  }

  // Puxão: sem skeleton (Análises nunca teve), fetch antes de render, falha
  // real rejeita sem tocar DOM (indicador âmbar). Superado sai neutro.
  if (background) {
    const data = await _fetchAnalyticsAll(_analyticsCurrentMonths, { force: true });
    if (data === undefined) return;
    _analyticsCache = data;
    renderAnalyticsView(data);
    return;
  }

  // Stale-while-revalidate: já tem cache do mesmo período → renderiza e
  // revalida em background.
  if (_analyticsCache && _analyticsCache.months === _analyticsCurrentMonths && !forceFresh) {
    renderAnalyticsView(_analyticsCache);
    _fetchAnalyticsAll(_analyticsCurrentMonths).then(fresh => {
      // Só re-renderiza se algo mudou de verdade — senão reconstruía os
      // gráficos do Chart.js a cada visita, dando flicker de "recarregando".
      // fresh undefined (superado) é falsy → o if pula sozinho.
      if (fresh && JSON.stringify(fresh) !== JSON.stringify(_analyticsCache)) {
        _analyticsCache = fresh;
        renderAnalyticsView(fresh);
      }
    }).catch(() => {});
    return;
  }

  try {
    const data = await _fetchAnalyticsAll(_analyticsCurrentMonths, { force: true });
    if (data === undefined) return;
    _analyticsCache = data;
    renderAnalyticsView(data);
  } catch (err) {
    statsEl.innerHTML = `<div class="empty" style="grid-column:1/-1;padding:30px;text-align:center;color:var(--red)">Erro ao carregar análises: ${escapeHtmlSafe(String(err.message || err))}</div>`;
  }
}

async function _fetchAnalyticsAll(months, { force = false } = {}) {
  return _analyticsChannel.run(async (signal) => {
    const base = `/analytics/${USER_ID}`;
    const qs = `?months=${months}`;
    // Os 7 fetches recebem o MESMO signal — um abort cancela todos de uma vez.
    const getJson = async (url) => {
      const r = await fetch(url, { credentials: "same-origin", signal });
      // Sem checar r.ok, um 4xx/5xx voltaria como JSON de erro "com cara de dado"
      // e o render pintaria KPIs/gráficos vazios COMO SUCESSO — no puxão, apagando
      // o render bom e reportando sucesso. Lança nos obrigatórios; os opcionais
      // (optional() abaixo) engolem esse throw e viram {}.
      if (!r.ok) throw new Error(`analytics (HTTP ${r.status}) ${url}`);
      return r.json();
    };
    // patterns/insights são opcionais: falha de rede/HTTP vira {} (não derruba a
    // view). Mas o AbortError PRECISA propagar, senão um abort não cancelaria o
    // Promise.all (o canal ficaria esperando um pedido que já foi superado).
    const optional = async (url) => {
      try { return await getJson(url); }
      catch (err) { if (err && err.name === "AbortError") throw err; return {}; }
    };
    const [k, ev, cat, wk, tm, pat, ins] = await Promise.all([
      getJson(`${base}/kpis${qs}`),
      getJson(`${base}/evolution${qs}`),
      getJson(`${base}/categories${qs}`),
      getJson(`${base}/weekday-pattern${qs}`),
      getJson(`${base}/top-merchants${qs}&limit=8`),
      optional(`${base}/patterns${qs}`),
      optional(`/insights/${USER_ID}/current`),
    ]);
    return {
      kpis:       k.kpis       || null,
      evolution:  ev.evolution || [],
      categories: cat.categories || [],
      weekday:    wk.weekdays  || [],
      merchants:  tm.merchants || [],
      patterns:   pat.patterns || [],     // Sprint 7: narrativas LLM
      insights:   ins.insights || [],
      months,
    };
  }, { force });
}

function renderAnalyticsView(data) {
  _destroyAnalyticsCharts();
  renderAnalyticsKPIs(data.kpis, data.months);
  renderAnalyticsEvolution(data.evolution);
  renderAnalyticsIncomeExpense(data.evolution);
  renderAnalyticsCategoryDonut(data.categories);
  renderAnalyticsWeekday(data.weekday);
  renderAnalyticsComparative(data.evolution);
  renderAnalyticsMerchants(data.merchants, data.months);
  renderAnalyticsInsights(data.insights);
  renderAnalyticsPatterns(data.patterns);
}

function _fmtBRL(v) {
  return "R$ " + Number(v || 0).toLocaleString("pt-BR", { minimumFractionDigits:2, maximumFractionDigits:2 });
}
function _fmtBRLshort(v) {
  // formato compacto pros gráficos: "R$ 1,2k" / "R$ 487"
  const n = Number(v || 0);
  if (Math.abs(n) >= 1000) return "R$ " + (n / 1000).toFixed(1).replace(".", ",") + "k";
  return "R$ " + Math.round(n);
}
function _fmtMonthLabel(ym) {
  // "2026-05" → "Mai/26"
  const m = /^(\d{4})-(\d{2})$/.exec(String(ym || ""));
  if (!m) return ym || "";
  const months = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"];
  return months[parseInt(m[2], 10) - 1] + "/" + m[1].slice(2);
}
function _fmtDateBR(iso) {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}` : iso;
}
function _deltaLabel(pct) {
  if (pct == null) return { text: "—", cls: "" };
  const sign = pct > 0 ? "+" : "";
  const cls = pct > 0 ? "down" : (pct < 0 ? "up" : "");
  return { text: `${sign}${pct.toFixed(1).replace(".", ",")}% vs período anterior`, cls };
}

function renderAnalyticsKPIs(k, months) {
  const root = document.getElementById("analytics-stats");
  if (!root) return;
  if (!k) {
    root.innerHTML = `<div class="empty" style="grid-column:1/-1;padding:20px;text-align:center;color:var(--text-3)">Sem dados no período.</div>`;
    return;
  }
  const n = Math.max(1, months || 6);
  const avgExpense = k.total_expense / n;
  const avgIncome  = k.total_income  / n;
  const dExpense = _deltaLabel(k.delta_pct?.expense ?? null);
  const dIncome  = _deltaLabel(k.delta_pct?.income  ?? null);
  // Inverte semântica do delta de despesa: mais despesa = ruim, então
  // delta positivo deve ficar "down" (vermelho). Recebe assim do _deltaLabel.
  const savings = (k.savings_rate || 0) * 100;
  const savingsLabel = savings >= 15
    ? "acima da média BR (15%)"
    : (savings > 0 ? "abaixo da média BR (15%)" : "negativo no período");
  const savingsCls = savings >= 15 ? "up" : "down";

  const peak = k.peak_day;
  const peakHTML = peak
    ? `<div class="stat-value">${_fmtDateBR(peak.date)}</div>
       <div class="stat-delta down">${_fmtBRL(peak.total)} em gastos</div>`
    : `<div class="stat-value" style="color:var(--text-3)">—</div>
       <div class="stat-delta">sem gastos no período</div>`;

  root.innerHTML = `
    <div class="stat-tile">
      <div class="stat-label">Gasto médio mensal</div>
      <div class="stat-value">${_fmtBRL(avgExpense)}</div>
      <div class="stat-delta ${dExpense.cls}">${dExpense.text}</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">Receita média</div>
      <div class="stat-value" style="color:var(--green)">${_fmtBRL(avgIncome)}</div>
      <div class="stat-delta ${dIncome.cls}">${dIncome.text}</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">Taxa de poupança</div>
      <div class="stat-value" style="color:#FF2D8E">${savings.toFixed(1).replace(".", ",")}%</div>
      <div class="stat-delta ${savingsCls}">${savingsLabel}</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">Maior dia do período</div>
      ${peakHTML}
    </div>
  `;
}

function _trackAnalyticsChart(el, cfg) {
  if (!el || typeof Chart === "undefined") return;
  _analyticsChartInstances.push(new Chart(el, cfg));
}

function renderAnalyticsEvolution(evolution) {
  const el = document.getElementById("mock-evolution-chart");
  if (!el || typeof Chart === "undefined") return;
  const t = _analyticsTheme();
  Chart.defaults.color = t.tickColor;
  Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif";
  const labels  = evolution.map(b => _fmtMonthLabel(b.month));
  const expense = evolution.map(b => b.expense);
  _trackAnalyticsChart(el, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Gastos",
        data: expense,
        borderColor: "#FF2D8E",
        backgroundColor: "rgba(167,139,250,.15)",
        fill: true, tension: .35, borderWidth: 2.5,
        pointBackgroundColor: "#FF2D8E",
        pointBorderColor: t.isLight ? "#fff" : "#0f1422",
        pointBorderWidth: 2, pointRadius: 4,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: t.grid }, ticks: { color: t.tickColor } },
        y: { grid: { color: t.grid }, ticks: { color: t.tickColor, callback: v => _fmtBRLshort(v) } }
      }
    }
  });
}

function renderAnalyticsIncomeExpense(evolution) {
  const el = document.getElementById("mock-income-expense-chart");
  if (!el || typeof Chart === "undefined") return;
  const t = _analyticsTheme();
  const labels  = evolution.map(b => _fmtMonthLabel(b.month));
  _trackAnalyticsChart(el, {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Receita", data: evolution.map(b => b.income),  backgroundColor: "#00F078", borderRadius: 6 },
        { label: "Despesa", data: evolution.map(b => b.expense), backgroundColor: "#FF2D2D", borderRadius: 6 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { color: t.tickColor, boxWidth: 12 } } },
      scales: {
        x: { grid: { display: false }, ticks: { color: t.tickColor } },
        y: { grid: { color: t.grid }, ticks: { color: t.tickColor, callback: v => _fmtBRLshort(v) } }
      }
    }
  });
}

// Mesma paleta do donut da Visão Geral (PALETTE) — mantém identidade visual
// PigBank (roxo/azul/verde/amarelo/etc). Sequencial pra garantir distinção
// entre fatias adjacentes (não usa c.color, que herda default roxo na maioria).
const _CATEGORY_PALETTE = PALETTE;

function renderAnalyticsCategoryDonut(categories) {
  const el = document.getElementById("mock-category-donut");
  if (!el || typeof Chart === "undefined") return;
  const t = _analyticsTheme();
  if (!categories || !categories.length) {
    const ctx = el.getContext("2d");
    ctx.clearRect(0, 0, el.width, el.height);
    return;
  }
  const labels = categories.map(c => c.name);
  const data   = categories.map(c => c.total);
  // Ignora c.color de propósito: a maioria das categorias herda o default roxo
  // (#FF2D8E) do banco, então respeitar c.color resultava em várias fatias
  // visualmente idênticas no donut. Sequencial pela paleta de 12 garante distinção.
  // Cor customizada do user continua aparecendo na lista de categorias.
  const _pal = catColors();
  const colors = categories.map((_, i) => _pal[i % _pal.length]);
  _trackAnalyticsChart(el, {
    type: "doughnut",
    data: { labels, datasets: [{
      data, backgroundColor: colors,
      borderColor: t.isLight ? "rgba(15,23,42,0.08)" : "rgba(0,0,0,.2)",
      borderWidth: 2,
    }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: { position: "right", labels: { color: t.tickColor, boxWidth: 10, padding: 8, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => `${ctx.label}: ${_fmtBRL(ctx.parsed)}` } }
      }
    }
  });
}

function renderAnalyticsWeekday(weekday) {
  const el = document.getElementById("mock-weekday-chart");
  if (!el || typeof Chart === "undefined") return;
  const t = _analyticsTheme();
  const labels  = weekday.map(w => w.label[0].toUpperCase() + w.label.slice(1));
  const data    = weekday.map(w => w.avg);
  const max     = Math.max(...data, 1);
  const colors  = weekday.map(w => {
    if ([0, 6].includes(w.dow)) return "#FF2D2D"; // dom/sáb
    if (w.avg / max > 0.8)       return "#fbbf24";
    return "#FF2D8E";
  });
  _trackAnalyticsChart(el, {
    type: "bar",
    data: { labels, datasets: [{ label: "Média/dia", data, backgroundColor: colors, borderRadius: 8 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => `Média: ${_fmtBRL(ctx.parsed.y)}` } }
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: t.tickColor } },
        y: { grid: { color: t.grid }, ticks: { color: t.tickColor, callback: v => _fmtBRLshort(v) } }
      }
    }
  });
}

function renderAnalyticsComparative(evolution) {
  const root = document.getElementById("analytics-comparative-list");
  const title = document.getElementById("analytics-comparative-title");
  if (!root) return;
  if (title) title.textContent = `Comparativo mês a mês`;
  if (!evolution || !evolution.length) {
    root.innerHTML = `<div class="empty" style="padding:16px;text-align:center;color:var(--text-3)">Sem dados.</div>`;
    return;
  }
  const max = Math.max(...evolution.map(b => b.expense), 1);
  const todayY = new Date().getFullYear();
  const todayM = new Date().getMonth() + 1;
  const rows = evolution.map(b => {
    const [yStr, mStr] = b.month.split("-");
    const y = parseInt(yStr, 10);
    const m = parseInt(mStr, 10);
    const isCurrent = (y === todayY && m === todayM);
    const pct = (b.expense / max) * 100;
    const cls = b.expense / max > 0.75 ? "red"
              : b.expense / max > 0.5  ? "yellow"
              : "green";
    const monthNames = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"];
    const label = `${monthNames[m - 1]}/${y}${isCurrent ? " (atual)" : ""}`;
    return `
      <div class="bar-row">
        <div class="bar-icon" ${isCurrent ? 'style="color:#FF2D8E"' : ""}>📅</div>
        <div class="bar-body">
          <div class="bar-head">
            <span class="name" ${isCurrent ? 'style="color:#FF2D8E"' : ""}>${escapeHtmlSafe(label)}</span>
            <span class="val">${_fmtBRL(b.expense)}</span>
          </div>
          <div class="bar-track"><div class="bar-fill ${isCurrent ? "blue" : cls}" style="width:${pct.toFixed(1)}%"></div></div>
        </div>
      </div>`;
  }).join("");
  root.innerHTML = rows;
}

// Dicionário de palavras-chave → emoji pros estabelecimentos.
// Array de pares (não objeto) pra garantir a ordem de match: termos mais
// específicos PRIMEIRO, genéricos depois. "burger king" antes de "king",
// "investimento bitcoin" → bate "bitcoin" (mais específico) antes de
// "investimento".
const _MERCHANT_EMOJIS = [
  // Marcas / Apps específicos
  ["ifood",          "🍔"],
  ["mc donalds",     "🍔"], ["mcdonalds", "🍔"], ["mcdonald", "🍔"],
  ["burger king",    "🍔"], ["bk delivery", "🍔"],
  ["subway",         "🥪"],
  ["dominos",        "🍕"], ["pizza hut", "🍕"],
  ["starbucks",      "☕"],
  ["padaria",        "🥖"], ["padoca", "🥖"],
  ["acougue",        "🥩"], ["açougue", "🥩"],
  ["pao de acucar",  "🛒"], ["pão de açúcar", "🛒"],
  ["carrefour",      "🛒"], ["assai", "🛒"], ["assaí", "🛒"],
  ["atacadao",       "🛒"], ["atacadão", "🛒"], ["extra hiper", "🛒"],
  ["sams club",      "🛒"], ["sam's club", "🛒"],
  ["drogasil",       "💊"], ["droga raia", "💊"], ["pacheco", "💊"],
  ["raia",           "💊"], ["pague menos", "💊"],
  ["uber eats",      "🍔"], ["rappi", "🍔"], ["ze delivery", "🍺"],
  ["uber",           "🚗"], ["99 taxi", "🚗"], ["99pop", "🚗"],
  ["cabify",         "🚗"],
  ["shell",          "⛽"], ["petrobras", "⛽"], ["ipiranga", "⛽"],
  ["br mania",       "⛽"], ["ale combustivel", "⛽"],
  ["sabesp",         "💧"], ["comgas", "🔥"], ["comgás", "🔥"],
  ["enel",           "💡"], ["cpfl", "💡"], ["light energia", "💡"],
  ["eletropaulo",    "💡"],
  ["vivo",           "📡"], ["claro tim", "📡"], ["oi telefone", "📡"],
  ["nextel",         "📡"],
  ["netflix",        "📺"], ["disney plus", "📺"], ["hbo max", "📺"],
  ["prime video",    "📺"], ["globoplay", "📺"], ["paramount", "📺"],
  ["spotify",        "🎵"], ["deezer", "🎵"], ["apple music", "🎵"],
  ["amazon music",   "🎵"], ["tidal", "🎵"],
  ["youtube premium","▶️"], ["youtube", "▶️"],
  ["steam games",    "🎮"], ["steam", "🎮"], ["playstation", "🎮"],
  ["xbox",           "🎮"], ["nintendo", "🎮"], ["epic games", "🎮"],
  ["icloud",         "☁️"], ["onedrive", "☁️"], ["dropbox", "☁️"],
  ["apple store",    "🍎"], ["apple",   "🍎"],
  ["google play",    "🔎"], ["google",  "🔎"],
  ["microsoft",      "💻"], ["adobe",   "🖌️"],
  ["amazon",         "📦"], ["mercado livre", "📦"], ["mercadolivre", "📦"],
  ["aliexpress",     "📦"], ["shopee",  "📦"], ["magalu", "📦"],
  ["magazine luiza", "📦"], ["americanas", "📦"], ["casas bahia", "📦"],
  ["shein",          "👕"], ["zara",    "👕"], ["renner", "👕"],
  ["riachuelo",      "👕"], ["c&a",     "👕"], ["c e a", "👕"],
  ["smart fit",      "🏋️"], ["smartfit","🏋️"], ["bodytech", "🏋️"],
  ["alura",          "📚"], ["coursera","📚"], ["udemy",   "📚"],
  ["airbnb",         "🛏️"], ["booking", "🛏️"], ["decolar", "✈️"],
  ["latam",          "✈️"], ["gol",     "✈️"], ["azul aerolinea", "✈️"],
  ["bitcoin",        "₿"], ["ethereum","₿"], ["binance", "₿"],
  ["mercado bitcoin","₿"], ["foxbit",  "₿"],

  // Palavras-chave genéricas (mais específicas → mais genéricas)
  ["dia das maes",   "🎁"], ["dia das mães", "🎁"],
  ["dia dos pais",   "🎁"], ["aniversario", "🎂"], ["aniversário", "🎂"],
  ["natal",          "🎄"], ["pascoa", "🐣"], ["páscoa", "🐣"],
  ["dentista",       "🦷"], ["odonto", "🦷"],
  ["consulta",       "🏥"], ["medico", "🏥"], ["médico", "🏥"],
  ["hospital",       "🏥"], ["clinica", "🏥"], ["clínica", "🏥"],
  ["estacionamento", "🅿️"], ["pedagio", "🛣️"], ["pedágio", "🛣️"],
  ["onibus",         "🚇"], ["ônibus", "🚇"], ["metro", "🚇"],
  ["metrô",          "🚇"], ["uber",   "🚗"], ["taxi", "🚗"],
  ["gasolina",       "⛽"], ["combustivel", "⛽"], ["combustível", "⛽"],
  ["farmacia",       "💊"], ["farmácia", "💊"], ["remedio", "💊"],
  ["remédio",        "💊"], ["droga", "💊"],
  ["mercado",        "🛒"], ["supermercado", "🛒"], ["hortifruti", "🥦"],
  ["restaurante",    "🍽️"], ["lanchonete", "🍽️"], ["churrascaria", "🥩"],
  ["pizzaria",       "🍕"], ["cafeteria", "☕"], ["cafe", "☕"], ["café", "☕"],
  ["bar ",           "🍺"], ["cerveja",   "🍺"], ["choperia", "🍺"],
  ["balada",         "🪩"],
  ["aluguel",        "🏠"], ["condominio", "🏢"], ["condomínio", "🏢"],
  ["financiamento",  "🏦"], ["iptu",     "🏛️"], ["ipva", "🚗"],
  ["agua",           "💧"], ["água",     "💧"],
  ["luz",            "💡"], ["energia",  "💡"],
  ["internet",       "📡"], ["telefone", "📡"], ["celular", "📱"],
  ["gas ",           "🔥"], ["gás",      "🔥"],
  ["cinema",         "🎬"], ["teatro",   "🎭"], ["show",  "🎤"],
  ["shopping",       "🛍️"], ["loja",    "🛍️"],
  ["presente",       "🎁"], ["gift",     "🎁"], ["flores", "💐"],
  ["floricultura",   "💐"],
  ["roupa",          "👕"], ["camiseta", "👕"], ["calcado", "👟"],
  ["calçado",        "👟"], ["sapato",   "👟"], ["tenis",  "👟"],
  ["tênis",          "👟"], ["bolsa",    "👜"],
  ["perfume",        "💄"], ["cosmetic", "💄"], ["maquiagem", "💄"],
  ["salao",          "💇"], ["salão",    "💇"], ["cabeleireiro", "💇"],
  ["manicure",       "💅"], ["barbearia","💈"], ["spa", "💆"],
  ["academia",       "🏋️"], ["yoga",    "🧘"], ["pilates", "🧘"],
  ["pet",            "🐾"], ["racao",    "🐾"], ["ração",  "🐾"],
  ["veterinario",    "🐾"], ["veterinário", "🐾"],
  ["escola",         "📚"], ["faculdade","📚"], ["curso",  "📚"],
  ["livro",          "📖"], ["livraria", "📖"],
  ["passagem",       "✈️"], ["voo",      "✈️"], ["aviao",  "✈️"],
  ["avião",          "✈️"], ["hotel",    "🛏️"], ["pousada", "🛏️"],
  ["hospedagem",     "🛏️"],
  ["acao",           "📈"], ["ação",     "📈"], ["acoes",  "📈"],
  ["ações",          "📈"], ["dividendo","📈"], ["aporte", "💰"],
  ["investimento",   "📈"], ["renda fixa", "📈"], ["tesouro", "📈"],
  ["cripto",         "₿"], ["cryptocurrency", "₿"], ["crypto", "₿"],
  ["doacao",         "❤️"], ["doação",   "❤️"], ["caridade","❤️"],
  ["dizimo",         "⛪"], ["dízimo",   "⛪"], ["igreja", "⛪"],
  ["emprestimo",     "🤝"], ["empréstimo", "🤝"],
  ["rifa",           "🎟️"], ["sorteio", "🎟️"], ["loteria", "🎰"],
  ["mega sena",      "🎰"], ["megasena", "🎰"],
  ["pescaria",       "🎣"], ["camping",  "⛺"], ["viagem", "✈️"],
  ["seguro",         "🛡️"], ["plano de saude", "🩺"],
  ["plano de saúde", "🩺"],
  ["mecanica",       "🔧"], ["mecânica", "🔧"], ["oficina", "🔧"],
  ["lavanderia",     "🧺"], ["limpeza",  "🧹"],
];

function _normalizeMerchantKey(name) {
  // Lower + remove acentos pra "ações" bater com "acoes" e "Stanley
  // PRESENTE dia das mães" → "stanley presente dia das maes".
  // NFD decompõe acento + range ̀-ͯ remove a marca diacrítica.
  return String(name || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");
}

function _merchantEmoji(name) {
  const key = _normalizeMerchantKey(name);
  for (const [k, emoji] of _MERCHANT_EMOJIS) {
    if (key.includes(_normalizeMerchantKey(k))) return emoji;
  }
  return "💸";
}

function renderAnalyticsMerchants(merchants, months) {
  const root  = document.getElementById("analytics-merchants-list");
  const title = document.getElementById("analytics-merchants-title");
  if (!root) return;
  if (title) title.textContent = `Top estabelecimentos · últimos ${months || 6} meses`;
  if (!merchants || !merchants.length) {
    root.innerHTML = `<div class="empty" style="padding:16px;text-align:center;color:var(--text-3)">Sem estabelecimentos registrados no período.</div>`;
    return;
  }
  root.innerHTML = merchants.slice(0, 8).map(m => {
    const emoji = _merchantEmoji(m.name);
    // Trunca nome muito longo (>40 chars) — protege layout contra notas
    // bagunçadas tipo "compras [Parcelamento removido em DD/MM/YYYY]".
    const rawName = String(m.name || "Sem nome");
    const displayName = rawName.length > 40 ? rawName.slice(0, 37) + "…" : rawName;
    const debCred = (m.sources?.debito && m.sources?.credito)
      ? `${m.count}× • débito + crédito`
      : (m.sources?.credito)
        ? `${m.count}× • crédito`
        : `${m.count}× • débito`;
    return `
      <div class="tx-row">
        <div class="tx-icon">${phIcon(emoji)}</div>
        <div class="tx-main">
          <div class="tx-desc" title="${escapeHtmlSafe(rawName)}">${escapeHtmlSafe(displayName)}</div>
          <div class="tx-meta">${escapeHtmlSafe(debCred)}</div>
        </div>
        <div class="tx-amt red">-${_fmtBRL(m.total)}</div>
      </div>`;
  }).join("");
}

// ── Sprint 7: Insights proativos do Piggy ────────────────────────────────

// localStorage de dismissed insights (TTL 24h). Key = `_pigInsightsDismissed`.
function _getDismissedInsights() {
  try {
    const raw = localStorage.getItem("_pigInsightsDismissed");
    if (!raw) return {};
    const obj = JSON.parse(raw);
    const now = Date.now();
    // Limpa entradas expiradas (>24h)
    const cleaned = {};
    for (const k in obj) {
      if (obj[k] && (now - obj[k]) < 24 * 60 * 60 * 1000) cleaned[k] = obj[k];
    }
    return cleaned;
  } catch (e) { return {}; }
}
function _dismissInsight(key) {
  const obj = _getDismissedInsights();
  obj[key] = Date.now();
  try { localStorage.setItem("_pigInsightsDismissed", JSON.stringify(obj)); } catch (e) {}
  // Re-render
  if (_analyticsCache) renderAnalyticsInsights(_analyticsCache.insights);
}

// Mapeia action_view do backend pra nome real da view no app
const _INSIGHT_VIEW_MAP = {
  budgets: "budgets",
  recurring: "fixed",
  fixed: "fixed",
  goals: "goals",
  pockets: "goals",
  analytics: "analytics",
};

function _switchToInsightView(view) {
  const target = _INSIGHT_VIEW_MAP[view] || view;
  if (typeof navigateTo === "function") navigateTo(target);
}

function renderAnalyticsInsights(insights) {
  const root = document.getElementById("analytics-insights-list");
  if (!root) return;
  const dismissed = _getDismissedInsights();
  const visible = (insights || []).filter(i => !dismissed[i.key]);

  if (!visible.length) {
    root.innerHTML = `
      <div class="empty" style="padding:24px 16px;text-align:center;color:var(--text-3);font-size:.88rem">
        <div style="font-size:2rem;margin-bottom:6px"><i class="ph ph-piggy-bank" aria-hidden="true"></i></div>
        <div style="font-weight:600;color:var(--text-2);margin-bottom:4px">Tudo sob controle</div>
        <div>Sem alertas relevantes pra você agora. Piggy continua de olho.</div>
      </div>`;
    return;
  }

  const sevColor = {
    critical: "var(--red, #ef4444)",
    warning:  "#f59e0b",
    info:     "var(--text-2)",
  };

  root.innerHTML = visible.map(i => {
    const color = sevColor[i.severity] || "var(--text-2)";
    const action = i.action_label && i.action_view
      ? `<button class="mini-action" onclick="_switchToInsightView('${escapeJsString(i.action_view)}')" style="background:rgba(255,45,142,.12);border:none;color:var(--purple,#FF2D8E);font-size:.75rem;font-weight:600;padding:5px 10px;border-radius:6px;cursor:pointer;white-space:nowrap">${escapeHtmlSafe(i.action_label)} →</button>`
      : "";
    const closeBtn = `<button title="Dispensar" aria-label="Dispensar" onclick="_dismissInsight('${escapeJsString(i.key)}')" style="background:none;border:none;color:var(--text-3);cursor:pointer;font-size:.85rem;line-height:1;padding:2px 6px;border-radius:6px;opacity:.6" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=.6"><i class="ph ph-x" aria-hidden="true"></i></button>`;
    return `
      <div class="tx-row" style="border-left:3px solid ${color};padding-left:10px;align-items:flex-start">
        <div class="tx-icon">${phIcon(i.icon || "🐷")}</div>
        <div class="tx-main" style="min-width:0">
          <div class="tx-desc" style="font-weight:600;color:var(--text-1)">${escapeHtmlSafe(i.title)}</div>
          <div class="tx-meta" style="color:var(--text-2);font-size:.84rem;line-height:1.35;white-space:normal">${escapeHtmlSafe(i.message)}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;flex-shrink:0">
          ${action}
          ${closeBtn}
        </div>
      </div>`;
  }).join("");
}

function renderAnalyticsPatterns(patterns) {
  const root = document.getElementById("analytics-patterns-list");
  if (!root) return;

  const items = Array.isArray(patterns) ? patterns : [];
  if (!items.length) {
    root.innerHTML = `
      <div class="empty" style="padding:24px 16px;text-align:center;color:var(--text-3);font-size:.85rem">
        <div style="font-size:2rem;margin-bottom:6px"><i class="ph ph-piggy-bank" aria-hidden="true"></i></div>
        <div style="font-weight:600;color:var(--text-2);margin-bottom:4px">Sem padrões ainda</div>
        <div>A IA precisa de mais histórico pra detectar padrões. Continue lançando, vai aparecer aqui em breve.</div>
      </div>`;
    return;
  }

  // Borda lateral colorida por tom
  const toneColor = {
    neutral: "var(--purple, #FF2D8E)",
    warn:    "#f59e0b",
    tip:     "#10b981",
  };

  root.innerHTML = items.map(p => {
    const color = toneColor[p.tone] || toneColor.neutral;
    return `
      <div class="tx-row" style="border-left:3px solid ${color};padding-left:10px;align-items:flex-start">
        <div class="tx-icon">${phIcon(p.icon || "🐷")}</div>
        <div class="tx-main" style="min-width:0">
          <div class="tx-desc" style="font-weight:600;color:var(--text-1);white-space:normal">${escapeHtmlSafe(p.title)}</div>
          ${p.subtitle ? `<div class="tx-meta" style="color:var(--text-2);font-size:.82rem;line-height:1.4;white-space:normal;margin-top:2px">${escapeHtmlSafe(p.subtitle)}</div>` : ""}
        </div>
      </div>`;
  }).join("");
}

// Wire up: dropdown de período recarrega view.
document.addEventListener("change", (e) => {
  if (e.target && e.target.id === "analytics-period-select") {
    loadAnalyticsView(true, e.target.value);
  }
  if (e.target && e.target.id === "history-period-select") {
    _historyFilters.months = parseInt(e.target.value, 10) || 6;
    _historyResetAndReload();
  }
});

// ── View Histórico (Sprint 6) ────────────────────────────────────────────
// Estado dos filtros — guarda tudo num objeto pra facilitar diff e reload.
let _historyFilters = {
  months: 6,           // período principal (dropdown)
  tipo: "all",         // chip de tipo: all|despesa|receita|credito
  q: "",               // busca textual (debounce)
  page: 1,
};
let _historyStatsCache = null;
let _historyRetryTimer = null;
const _historyListChannel = makeFetchChannel(); // dedup + abort + geração
let _historySearchDebounce = null;
// Loads completos (nav/filtro/busca/período/puxão) em voo. O "carregar mais" e o
// load completo dividem o MESMO canal e têm semânticas opostas (append × substitui):
// se um append dispara enquanto um load completo está em voo, ele aborta o reload
// da página 1 e appenda numa base que está pra sumir (mistura filtros). Enquanto
// isto for > 0, o load-more não roda. Contador (não bool) pra aguentar reloads
// concorrentes: um reload superado não pode zerar o gate de outro ainda em voo.
let _historyReloadsInFlight = 0;
// Geração de stats: só a resposta da geração CORRENTE aplica. A guarda de período
// não basta — dois reloads do MESMO período podem ter o stats mais VELHO resolvendo
// por último e sobrescrevendo os contadores que o mais novo já renderizou. A geração
// só é bumpada quando um FETCH de stats novo é disparado (statsNeeded); um reload
// só-cache NÃO bumpa, pra não invalidar um refresh ainda em voo de um reload forçado.
let _historyStatsGen = 0;

function _historyResetAndReload() {
  _historyFilters.page = 1;
  _renderHistoryActiveFilter();
  loadHistoryView(true);
}

async function loadHistoryView(forceFresh = false, { background = false } = {}) {
  const timeline = document.getElementById("history-timeline");
  if (!timeline) return;
  if (!USER_ID) {
    if (background) throw new Error("histórico: sessão ainda não pronta");
    if (!_historyRetryTimer) {
      _historyRetryTimer = setInterval(() => {
        if (USER_ID) {
          clearInterval(_historyRetryTimer);
          _historyRetryTimer = null;
          loadHistoryView(forceFresh);
        }
      }, 250);
    }
    return;
  }

  // loadHistoryView é SEMPRE um load completo — renderiza append=false (substitui
  // a timeline, que é acumulada pelo "carregar mais"). Sem forçar página 1, puxar
  // pra atualizar (ou navegar de volta) depois de paginar buscaria a página
  // corrente e substituiria por só ela ("puxei e o histórico pulou pro meio").
  //
  // INVARIANTE: _historyFilters.page tem que bater com as páginas que estão no
  // DOM. Por isso NÃO mutamos o contador antes de renderizar — passamos page:1 só
  // pro fetch e só commitamos ao efetivamente renderizar. Se um refresh em
  // background falhar (DOM preservado), o contador não pode ter ido pra 1 sozinho,
  // senão dessincroniza com as páginas que ficaram na tela (e o "carregar mais"
  // seguinte pularia/duplicaria).
  const statsNeeded = !_historyStatsCache || _historyStatsCache.months !== _historyFilters.months || forceFresh;
  // Stats é secundário e NÃO-cancelável (o fetch não recebe signal). Fica FORA do
  // gate e do await da timeline: se entrasse no Promise.all gateado, um reload
  // superado cuja LISTA foi abortada mas cujo stats segue pendurado nunca chegaria
  // ao finally (o Promise.all esperaria o stats) e o gate ficaria PRESO acima de
  // zero — todo "carregar mais" ignorado pra sempre. O gate segue só o ciclo da
  // LISTA (cancelável, com guarda de geração); o stats atualiza os contadores
  // quando chegar. .catch pra nunca virar unhandledrejection num caminho superado.
  const statsMonths = _historyFilters.months;
  // Bumpa a geração SÓ quando dispara um fetch novo. Um reload só-cache (re-entrar
  // no Histórico com cache válido, statsNeeded=false) não pode invalidar um refresh
  // de stats ainda em voo de um reload forçado anterior — senão a resposta fresca
  // falharia a guarda de geração e os contadores ficariam velhos até o próximo forçado.
  const statsGen = statsNeeded ? ++_historyStatsGen : _historyStatsGen;
  const statsP = (statsNeeded
    ? _fetchHistoryStats(statsMonths)
    : Promise.resolve(_historyStatsCache)).catch(() => null);
  // Stats (secundário) é aplicado INDEPENDENTE do desfecho da lista deste reload:
  // se a lista for superada (o `return` adiante) ou falhar, o stats fresco que já
  // está em voo não pode ficar órfão — por isso o handler é anexado AQUI, antes do
  // await da lista, não dentro do caminho de sucesso dela. Guarda de GERAÇÃO: só a
  // resposta do fetch de stats mais novo aplica (subsume o período; sem ela, um
  // stats mais velho do mesmo período resolvendo por último sobrescreveria o novo).
  // Atualiza os contadores quando chegar, sem segurar o gate nem a timeline.
  statsP.then(stats => {
    if (statsGen !== _historyStatsGen) return;
    if (statsNeeded && stats) _historyStatsCache = { ...stats, months: statsMonths };
    renderHistoryStats(_historyStatsCache);
  });

  _historyReloadsInFlight++;
  try {
    const list = await _fetchHistoryList({ ..._historyFilters, page: 1 });
    // list undefined = este load foi superado por um mais novo (troca de filtro,
    // nova busca, puxão). Não renderiza a TIMELINE — o mais novo é quem manda. (O
    // stats já é tratado acima, independente disto.) Guarda de geração da lista.
    if (list === undefined) return;
    _historyFilters.page = 1;   // commit: o DOM vira página 1 agora
    renderHistoryTimeline(list, /*append=*/false);
  } catch (err) {
    // Falha REAL da lista (HTTP/rede). No puxão (background): rejeita sem tocar no
    // DOM NEM no contador — o render bom e a paginação ficam, indicador âmbar. Na
    // navegação/filtro: renderiza o estado de erro, então o contador passa a 1.
    if (background) throw err;
    _historyFilters.page = 1;
    renderHistoryTimeline(null, /*append=*/false);
  } finally {
    _historyReloadsInFlight--;   // solto quando a LISTA assenta — nunca preso no stats
  }
}

async function _fetchHistoryStats(months) {
  try {
    const r = await fetch(`/history/${USER_ID}/quick-stats?months=${months}`, { credentials:"same-origin" });
    // Stats é secundário e tolerante (o refresh não falha por causa dele — ver a
    // assimetria no corpo do PR). Mas sem checar r.ok, um 500 voltaria o payload
    // de erro como "stats" e renderHistoryStats pintaria lixo. !ok → null →
    // renderHistoryStats sai cedo e mantém os contadores anteriores.
    if (!r.ok) return null;
    return await r.json();
  } catch (_) { return null; }
}

async function _fetchHistoryList(filters, opts = {}) {
  const qs = _buildHistoryQuery(filters);
  const doFetch = async (signal) => {
    const r = await fetch(`/history/${USER_ID}/list?${qs}`, { credentials:"same-origin", signal });
    // Sem checar r.ok, um 401/500 voltaria como payload de erro e o
    // renderHistoryTimeline substituiria a timeline boa por "Erro ao carregar",
    // reportando o puxão como sucesso. Lança pra falha REAL subir pelo canal.
    if (!r.ok) throw new Error(`histórico (HTTP ${r.status})`);
    return await r.json();
  };
  // allowParallel = busca concorrente (ex.: digitação incremental futura): sem
  // canal/abort, cada pedido vive por conta própria e nenhum estrangula o
  // outro. Nenhum caller passa allowParallel hoje; preservado de propósito.
  if (opts.allowParallel) return doFetch();
  // Demais chamadas (load principal, "carregar mais", puxão) passam pelo canal:
  // abort + geração. Mata o hang-strand do antigo `await _historyListInFlight`
  // (um list pendurado travava toda chamada seguinte) e evita que um pedido
  // velho renderize por cima do novo. Devolve os dados, ou undefined se superado.
  return _historyListChannel.run(doFetch, { force: true });
}

function _buildHistoryQuery(filters) {
  const p = new URLSearchParams();
  // Janela de N meses cheios terminando no mês atual (resolve_window).
  // Calcula localmente pq o endpoint /list não aceita ?months.
  const today = new Date();
  const start = new Date(today.getFullYear(), today.getMonth() - (filters.months - 1), 1);
  const end = new Date(today.getFullYear(), today.getMonth() + 1, 1);
  p.set("from", _isoDate(start));
  p.set("to", _isoDate(end));
  if (filters.tipo && filters.tipo !== "all") p.set("tipo", filters.tipo);
  if (filters.q && filters.q.trim()) p.set("q", filters.q.trim());
  p.set("page", String(filters.page || 1));
  p.set("limit", "50");
  return p.toString();
}

function _isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function renderHistoryStats(s) {
  if (!s) return;
  const grid = document.getElementById("history-stats");
  if (!grid) return;
  const tiles = grid.querySelectorAll(".stat-tile");
  // Ordem fixa: Média/mês, Receitas, Despesas, Total no período
  const data = [
    {
      value: s.avg_per_month != null ? Number(s.avg_per_month).toLocaleString("pt-BR", { maximumFractionDigits:1 }) : "—",
      sub: "lançamentos / mês",
      color: "#FF2D8E",
    },
    {
      value: s.receitas_count != null ? s.receitas_count : "—",
      sub: "no período",
      color: "var(--green)",
    },
    {
      value: s.despesas_count != null ? s.despesas_count : "—",
      sub: "débito + cartão",
      color: "#FF2D2D",
    },
    {
      value: s.total_count != null ? s.total_count : "—",
      sub: "todos os lançamentos",
      color: "var(--text)",
    },
  ];
  tiles.forEach((tile, i) => {
    const d = data[i];
    if (!d) return;
    const valueOld = tile.querySelector(".stat-value, .sk");
    const delta = tile.querySelector(".stat-delta");
    if (valueOld) {
      const newVal = document.createElement("div");
      newVal.className = "stat-value";
      newVal.style.color = d.color;
      newVal.textContent = String(d.value);
      valueOld.replaceWith(newVal);
    }
    if (delta) {
      delta.textContent = d.sub;
      delta.style.color = "var(--text-3)";
    }
  });
}

function _renderHistoryActiveFilter() {
  const badge = document.getElementById("history-active-filter");
  if (!badge) return;
  const parts = [];
  if (_historyFilters.q && _historyFilters.q.trim()) parts.push(`busca: "${_historyFilters.q.trim()}"`);
  if (!parts.length) {
    badge.style.display = "none";
    badge.innerHTML = "";
    return;
  }
  badge.style.display = "inline-flex";
  badge.innerHTML = `Filtrando: ${escapeHtmlSafe(parts.join(" · "))}<span class="clear-x" onclick="_clearHistoryFilters()">×</span>`;
}

function _clearHistoryFilters() {
  _historyFilters.q = "";
  const input = document.getElementById("history-search-input");
  if (input) input.value = "";
  _historyResetAndReload();
}

// Itens do Histórico atualmente renderizados — indexados pra o clique na linha
// abrir o modal de detalhe (openHistoryDetail).
let _renderedHistoryItems = [];

function renderHistoryTimeline(payload, append = false) {
  const root = document.getElementById("history-timeline");
  const moreWrap = document.getElementById("history-load-more-wrap");
  if (!root) return;
  if (!payload || !payload.ok) {
    root.innerHTML = `<div class="empty" style="padding:30px;text-align:center;color:var(--red)">Erro ao carregar histórico.</div>`;
    if (moreWrap) moreWrap.style.display = "none";
    return;
  }

  const items = payload.items || [];
  // Indexa os itens no array global pra o clique na linha (openHistoryDetail).
  const _base = append ? _renderedHistoryItems.length : 0;
  if (append) _renderedHistoryItems.push(...items);
  else _renderedHistoryItems = items.slice();
  items.forEach((it, n) => { it._ldx = _base + n; });

  if (!items.length) {
    if (!append) _renderedHistoryItems = [];
    root.innerHTML = `<div class="empty" style="padding:30px;text-align:center;color:var(--text-3)">Nenhum lançamento encontrado com os filtros atuais.</div>`;
    if (moreWrap) moreWrap.style.display = "none";
    return;
  }

  // Agrupa por dia (date string YYYY-MM-DD).
  const groups = new Map();
  for (const i of items) {
    const key = (i.criado_em || "").slice(0, 10);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(i);
  }

  const html = Array.from(groups.entries()).map(([dayKey, list]) => {
    const header = _historyDayHeader(dayKey);
    const rows = list.map(_historyRowHTML).join("");
    return `
      <div style="font-size:.78rem;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;margin:18px 0 10px">${escapeHtmlSafe(header)}</div>
      <div class="tx-list">${rows}</div>
    `;
  }).join("");

  if (append) {
    // Append: tira a mensagem "empty" se houver e concatena novo HTML.
    const empty = root.querySelector(".empty");
    if (empty) empty.remove();
    root.insertAdjacentHTML("beforeend", html);
  } else {
    root.innerHTML = html;
  }

  // Paginação: mostra "Carregar mais" se há mais páginas.
  const totalPages = payload.total_pages || 0;
  const currentPage = payload.page || 1;
  if (totalPages > currentPage) {
    if (moreWrap) {
      moreWrap.style.display = "";
      const btn = document.getElementById("history-load-more-btn");
      if (btn) {
        btn.textContent = `Carregar mais (${payload.total - currentPage * 50} restantes)`;
        btn.disabled = false;
      }
    }
  } else {
    if (moreWrap) moreWrap.style.display = "none";
  }
}

function _historyDayHeader(dayKey) {
  if (!dayKey) return "—";
  const today = new Date();
  const todayKey = _isoDate(today);
  const yesterday = new Date(today.getTime() - 86400000);
  const yesterdayKey = _isoDate(yesterday);
  if (dayKey === todayKey) {
    return `Hoje · ${_fmtDayLabel(dayKey)}`;
  }
  if (dayKey === yesterdayKey) {
    return `Ontem · ${_fmtDayLabel(dayKey)}`;
  }
  return _fmtDayLabel(dayKey, /*includeWeekday=*/true);
}
function _fmtDayLabel(dayKey, includeWeekday = false) {
  const [y, m, d] = dayKey.split("-").map(s => parseInt(s, 10));
  if (!y || !m || !d) return dayKey;
  const months = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"];
  const weekdays = ["domingo","segunda","terça","quarta","quinta","sexta","sábado"];
  const base = `${d} ${months[m - 1]}`;
  if (!includeWeekday) return base;
  const dt = new Date(y, m - 1, d);
  return `${base} · ${weekdays[dt.getDay()]}`;
}

function _historyRowHTML(i) {
  const isReceita = i.tipo === "receita";
  const isCredito = i.tipo === "credito";
  const isDespesa = i.tipo === "despesa" || i.tipo === "saida";
  const valor = Number(i.valor || 0);
  const sign = isReceita ? "+" : (isCredito || isDespesa ? "-" : "");
  const amtClass = isReceita ? "green" : "red";
  const icon = isReceita ? "<i class='ph ph-trend-down' aria-hidden='true'></i>" : (isCredito ? "<i class='ph ph-credit-card' aria-hidden='true'></i>" : "<i class='ph ph-receipt' aria-hidden='true'></i>");
  const time = (i.criado_em || "").slice(11, 16);
  const desc = i.alvo || i.nota || "—";
  const meta = [];
  if (i.categoria) meta.push(i.categoria);
  if (isCredito && i.alvo)  meta.push(`Cartão ${i.alvo}`);
  if (!isCredito && i.nota && i.alvo && i.nota !== i.alvo) meta.push(i.nota);
  if (time) meta.push(time);
  const clickable = i._ldx != null ? ` style="cursor:pointer" onclick="openHistoryDetail(${i._ldx})"` : "";
  return `
    <div class="tx-row"${clickable}>
      <div class="tx-icon" style="color:${isReceita ? "#00F078" : (isCredito ? "#7E5FE6" : "#fbbf24")}">${icon}</div>
      <div class="tx-main">
        <div class="tx-desc">${escapeHtmlSafe(_truncate(desc, 60))}</div>
        <div class="tx-meta">${escapeHtmlSafe(meta.join(" • "))}</div>
      </div>
      <div class="tx-amt ${amtClass}">${sign}${_fmtBRL(valor)}</div>
    </div>
  `;
}

function _truncate(s, n) {
  s = String(s || "");
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// ── Wire-up de eventos da view Histórico ─────────────────────────────────
document.addEventListener("input", (e) => {
  if (e.target && e.target.id === "history-search-input") {
    if (_historySearchDebounce) clearTimeout(_historySearchDebounce);
    _historySearchDebounce = setTimeout(() => {
      _historyFilters.q = e.target.value || "";
      _historyResetAndReload();
    }, 300);
  }
});

document.addEventListener("click", async (e) => {
  // Filter chip (tipo)
  const chip = e.target.closest?.('#history-filter-chips .filter-chip');
  if (chip) {
    document.querySelectorAll('#history-filter-chips .filter-chip').forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    _historyFilters.tipo = chip.dataset.tipo || "all";
    _historyResetAndReload();
    return;
  }
  // Botão "Carregar mais"
  if (e.target && e.target.id === "history-load-more-btn") {
    const btn = e.target;
    // Um load completo (nav/filtro/busca/período/puxão) em voo vai SUBSTITUIR a
    // timeline. Paginar agora abortaria a página 1 desse reload (mesmo canal) e
    // appendaria numa base que está pra sumir — misturando filtros e sumindo com a
    // página 1. Ignora o clique: o reload re-renderiza o botão no fim. O botão fica
    // como está (não o desabilito aqui, pra não deixá-lo preso se o reload for um
    // puxão em background que falha e preserva o DOM).
    if (_historyReloadsInFlight > 0) return;
    // NÃO muta _historyFilters.page até o append dar certo (mesma invariante do
    // loadHistoryView: contador == páginas no DOM). Passa nextPage só pro fetch;
    // se for superado ou falhar, o contador fica intacto e batendo com o DOM.
    const nextPage = (_historyFilters.page || 1) + 1;
    btn.disabled = true;
    btn.textContent = "Carregando…";
    let more;
    try {
      more = await _fetchHistoryList({ ..._historyFilters, page: nextPage });
    } catch (err) {
      // Falha REAL (HTTP/rede): o throw do canal (guard de r.ok) chega aqui, fora
      // do try/catch do loadHistoryView. Botão volta acionável; contador intacto,
      // então o retry pega a MESMA próxima página (não pula).
      btn.disabled = false;
      btn.textContent = "Tentar de novo";
      return;
    }
    // Superado (um puxão/nova busca abortou este load-more): não faz append. Mas
    // reabilita o botão AQUI — se o superador for um render bem-sucedido ele
    // reconstrói o botão por cima (idempotente); se for um puxão em background que
    // FALHOU (DOM preservado, sem re-render), este é o ÚNICO restore e evita o
    // botão preso em "Carregando…". Contador nunca foi mexido → segue consistente.
    if (more === undefined) {
      btn.disabled = false;
      btn.textContent = "Carregar mais";
      return;
    }
    _historyFilters.page = nextPage;   // commit: só ao append com sucesso
    renderHistoryTimeline(more, /*append=*/true);
    return;
  }
});

function toggleSidenav(force) {
  const nav = document.getElementById("sidenav");
  const bd  = document.getElementById("sidenav-backdrop");
  if (!nav) return;
  const open = (typeof force === "boolean") ? force : !nav.classList.contains("open");
  nav.classList.toggle("open", open);
  if (bd) bd.classList.toggle("open", open);
}

// Fechar sidebar com ESC
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const nav = document.getElementById("sidenav");
    if (nav && nav.classList.contains("open")) toggleSidenav(false);
  }
});

// ── Theme toggle (dark/light) ────────────────────────────────────────
function applyTheme(theme) {
  const isLight = theme === "light";
  document.body.classList.toggle("light", isLight);
  // espelha no <html>: o fundo do CANVAS (o que o elástico revela no app)
  // vem do html, então ele precisa saber do tema — senão o claro fica com
  // canvas escuro e a faixa reaparece, invertida
  document.documentElement.classList.toggle("light", isLight);
  const icon  = document.getElementById("theme-toggle-icon");
  const label = document.getElementById("theme-toggle-label");
  if (icon)  icon.innerHTML = isLight
    ? '<i class="ph ph-sun" aria-hidden="true"></i>'
    : '<i class="ph ph-moon" aria-hidden="true"></i>';
  if (label) label.textContent = isLight ? "Modo claro" : "Modo escuro";

  // Re-renderiza os gráficos da view Análises pra pegar as cores novas do tema.
  // Se o user está na view Análises, força re-fetch (cores dependem de tema,
  // mas dados são os mesmos — usa cache).
  const analyticsVisible = document.getElementById("analytics-view")?.classList.contains("active");
  if (analyticsVisible && _analyticsCache) {
    renderAnalyticsView(_analyticsCache);
  }

  // Idem pros gráficos do overview: Chart.js não relê as cores no toggle,
  // então reconstrói com os últimos dados pra pegar o tema novo.
  const overviewVisible = document.getElementById("overview-view")?.classList.contains("active");
  if (overviewVisible && lastData) {
    const d = lastData;
    if ((d.expense_categories || []).length) buildCatChart(d.expense_categories);
    if (_expenseSeries) buildExpenseChart(_expenseSeries, _expensePeriod);
    if (_lastHistory && _lastHistory.length) buildHistoryChart(_lastHistory);
  }
}
function toggleTheme() {
  const next = document.body.classList.contains("light") ? "dark" : "light";
  try { localStorage.setItem("pigbank_theme", next); } catch(_) {}
  applyTheme(next);
}
(function initTheme(){
  let saved = "dark";
  try { saved = localStorage.getItem("pigbank_theme") || "dark"; } catch(_) {}
  applyTheme(saved);
})();

// ── Esconder saldo (olho) ────────────────────────────────────────────────
function applyHideBalance(hidden){
  document.body.classList.toggle("balance-hidden", hidden);
  const btn = document.getElementById("hide-balance-btn");
  if (btn){
    const ico = btn.querySelector("i");
    if (ico) ico.className = hidden ? "ph ph-eye-slash" : "ph ph-eye";
    btn.setAttribute("aria-pressed", hidden ? "true" : "false");
    btn.title = hidden ? "Mostrar saldo" : "Esconder saldo";
  }
}
function toggleHideBalance(){
  const hidden = !document.body.classList.contains("balance-hidden");
  try { localStorage.setItem("pigbank_hide_balance", hidden ? "1" : "0"); } catch(_){}
  applyHideBalance(hidden);
}
(function initHideBalance(){
  let v = "0";
  try { v = localStorage.getItem("pigbank_hide_balance") || "0"; } catch(_){}
  applyHideBalance(v === "1");
})();

// ── Pro gates (visivel + desabilitado por feature) ───────────────────────
// A fonte da verdade é o BACKEND: /auth/dashboard-profile devolve feature_gates
// já resolvido (get_user_limits + is_pro cobrem assinatura expirada com webhook
// perdido E o freio de emergência PLANS_V2_ENABLED). O front só consome — nada
// de reconstruir tier do valor cru do plano, que divergiria nesses casos.
// Default: tudo bloqueado até o perfil chegar (conservador — melhor travar de
// leve por um instante que liberar indevido). USER_GATES é setado no topo.
function featureAllowed(feature) {
  return !!USER_GATES[feature];
}
// "Tem o plano pago principal?" (Plus+): o gate de Novidades é exatamente is_pro.
function isProUser() {
  return featureAllowed("changelog");
}

const UPGRADE_MESSAGES = {
  investments: "Acompanhe sua carteira de investimentos com cálculo automático de rendimento, IR e IOF. Disponível nos planos pagos.",
  export: "Exportar seus lançamentos (PDF, planilha) por email faz parte dos planos pagos.",
  pockets_unlimited: "No Grátis você cria 1 caixinha. Com um plano pago fica ilimitado: separe sua reserva, viagens, presentes…",
  cards_unlimited: "No Grátis você cadastra 1 cartão. Com um plano pago fica ilimitado: controle todos os seus cartões em um lugar.",
  ofx_import: "Importar extrato bancário e fatura de cartão por OFX faz parte dos planos pagos.",
  history_unlimited: "Histórico além de 30 dias faz parte dos planos pagos.",
  changelog: "As notícias e resumos do mercado feitos pela Piggy fazem parte dos planos Plus e Pro. Assine pra desbloquear.",
  recurring_expenses: "A agenda de boletos e os gastos fixos fazem parte dos planos pagos. Cadastre suas contas a pagar e nunca mais perca um vencimento.",
  agents: "Seu plano atual não ativa mais agentes. Fazendo upgrade, a equipe de porquinhos trabalha pra você: Xerife, Repórter, Carteiro e os próximos que chegarem.",
  forecast: "A previsão de saldo a 30, 60 e 90 dias é do plano Pro. Veja pra onde seu caixa caminha e planeje com folga antes do aperto chegar.",
  generic: "Essa feature faz parte dos planos pagos do PigBank. Escolha o que faz mais sentido pra você."
};

// Banner de trial (B1): oferta proativa dos 30d de Plus pro Grátis. Só aparece
// pra quem é free e não está em trial ativo; some no app iOS (CTA de compra
// externa = rejeição Apple 3.1.1, a checagem é feita por quem chama). O "Agora
// não" silencia por TRIAL_BANNER_SNOOZE_DAYS via localStorage.
const TRIAL_BANNER_SNOOZE_KEY = "pb_trial_banner_snooze_until";
const TRIAL_BANNER_SNOOZE_DAYS = 1;

function maybeShowTrialBanner() {
  const el = document.getElementById("trial-banner");
  if (!el) return;
  try {
    const until = parseInt(localStorage.getItem(TRIAL_BANNER_SNOOZE_KEY) || "0", 10);
    if (until && Date.now() < until) return;  // ainda no período de silêncio
  } catch (e) { /* localStorage indisponível → mostra assim mesmo */ }
  el.style.display = "block";
}

function dismissTrialBanner() {
  const el = document.getElementById("trial-banner");
  if (el) el.style.display = "none";
  try {
    const until = Date.now() + TRIAL_BANNER_SNOOZE_DAYS * 86400000;
    localStorage.setItem(TRIAL_BANNER_SNOOZE_KEY, String(until));
  } catch (e) { /* sem localStorage → some só nesta sessão */ }
}

function showUpgradeModal(feature) {
  const overlay = document.getElementById("upgrade-overlay");
  if (!overlay) return;
  // Fecha qualquer outro overlay aberto (ex: "Nova caixinha") pra evitar
  // que o modal de criacao fique por cima do upgrade.
  document.querySelectorAll(".overlay.open").forEach(el => {
    if (el.id !== "upgrade-overlay") el.classList.remove("open");
  });
  const msgEl = document.getElementById("upg-feat-msg");
  if (msgEl) msgEl.textContent = UPGRADE_MESSAGES[feature] || UPGRADE_MESSAGES.generic;
  overlay.classList.add("open");
}

function closeUpgradeModal() {
  const overlay = document.getElementById("upgrade-overlay");
  if (overlay) overlay.classList.remove("open");
}

// Aplica estado visual disabled em todos os elementos com data-pro-feature
// quando o user e Free. Idempotente — pode ser chamada varias vezes.
function applyProGates() {
  // Gate POR FEATURE: cada controle libera no seu tier mínimo (Essencial já
  // solta investimentos/OFX/export/etc; Novidades só do Plus pra cima). Antes
  // era um único booleano is_pro, que trancava tudo pra quem era Essencial.
  document.querySelectorAll("[data-pro-feature]").forEach(el => {
    if (featureAllowed(el.dataset.proFeature)) {
      el.classList.remove("pro-locked");
      el.removeAttribute("aria-disabled");
      // Remove o tooltip de upgrade que o ramo bloqueado seta: como o bootstrap
      // trava tudo primeiro (USER_GATES vazio), sem isto o title "clica pra ver
      // os planos" ficava preso em controle liberado (Export/Investimentos).
      el.removeAttribute("title");
      const b = el.querySelector(":scope > .pro-badge");
      if (b) b.remove();
    } else {
      el.classList.add("pro-locked");
      el.setAttribute("aria-disabled", "true");
      el.setAttribute("title", "Funcionalidade de um plano pago: clica pra ver os planos");
      if (!el.querySelector(":scope > .pro-badge")) {
        const b = document.createElement("span");
        b.className = "pro-badge";
        b.textContent = "PigBank+";
        el.appendChild(b);
      }
    }
  });
  // Titulo do grafico de historico reflete a janela real: 6+ meses (Plus/Pro)
  // vs 30 dias (Free/Essencial cai no rótulo curto).
  const histTitle = document.getElementById("history-card-title");
  if (histTitle) {
    histTitle.textContent = isProUser()
      ? "Receita vs Despesa (Últimos 6 Meses)"
      : "Receita vs Despesa (Últimos 30 dias)";
  }
}

// Intercepta click em qualquer elemento .pro-locked ANTES de cair no onclick
// original. Em vez de deixar o fetch sair, retornar 403 e o interceptor abrir
// o modal, abre o modal direto — corta ~500ms de UX. O fetch interceptor
// continua como backup pra casos onde o user fura o gate visual.
document.addEventListener("click", (e) => {
  const locked = e.target.closest(".pro-locked[data-pro-feature]");
  if (!locked) return;
  if (featureAllowed(locked.dataset.proFeature)) return;
  e.preventDefault();
  e.stopPropagation();
  showUpgradeModal(locked.dataset.proFeature || "generic");
}, true);  // capture: pega antes dos onclicks inline

// Esc fecha o modal
// Tinha Esc próprio e nenhum trap de Tab — o foco vazava para o dashboard
// atrás do overlay. Pelo helper, ganha os dois (issue #76).
window.pigModalKeys && pigModalKeys("upgrade-overlay", closeUpgradeModal);

// Interceptor global: qualquer fetch que volte 403 com pro_required abre o
// modal automaticamente. Cobre casos onde o user fura o gate visual (ex:
// tenta criar 2a caixinha pelo botao "+ Nova" sem que a UI tenha bloqueado).
const _origFetch = window.fetch;
window.fetch = async function(...args) {
  const res = await _origFetch.apply(this, args);
  if (res.status === 403) {
    // Clona pra nao consumir o body do caller
    try {
      const clone = res.clone();
      const data = await clone.json();
      const feature = data && data.detail && data.detail.error === "pro_required"
        ? data.detail.feature
        : null;
      if (feature) showUpgradeModal(feature);
    } catch {}
  }
  return res;
};

// ── Modal de investimento ─────────────────────────────────────────────────
function openInvestmentModal(tab) {
  const overlay = document.getElementById("invest-overlay");
  if (!overlay) return;
  setInvestModalTab(tab || "deposit");
  overlay.classList.add("open");
}

function closeInvestmentModal() {
  const overlay = document.getElementById("invest-overlay");
  if (!overlay) return;
  overlay.classList.remove("open");
}

function setInvestModalTab(tab) {
  // tab: "deposit" | "withdraw" | "create"
  const isCreate = tab === "create";
  document.querySelectorAll("#invest-overlay [data-invtab]").forEach(b => {
    const active = b.dataset.invtab === tab;
    b.classList.toggle("active", active);
    b.setAttribute("aria-checked", active ? "true" : "false");
  });
  const paneMove = document.getElementById("invest-pane-move");
  const paneCreate = document.getElementById("invest-pane-create");
  if (paneMove) paneMove.style.display = isCreate ? "none" : "";
  if (paneCreate) paneCreate.style.display = isCreate ? "" : "none";

  if (isCreate && typeof resetInvestmentCreateForm === "function") {
    resetInvestmentCreateForm();
  }

  const title = document.getElementById("invest-modal-title");
  const sub = document.getElementById("invest-modal-sub");
  if (tab === "create") {
    if (title) title.textContent = "Novo investimento";
    if (sub) sub.textContent = "Cadastre um novo ativo na sua carteira.";
  } else if (tab === "withdraw") {
    if (title) title.textContent = "Resgatar investimento";
    if (sub) sub.textContent = "Retira valor do investimento de volta para a conta.";
  } else {
    if (title) title.textContent = "Aporte em investimento";
    if (sub) sub.textContent = "Adiciona valor ao investimento. Tesouro IPCA+/Prefixado aceitam taxa por compra.";
  }

  // Em aporte/resgate, alinha o select de operação com a tab clicada.
  if (!isCreate) {
    const moveKind = document.getElementById("move-kind");
    if (moveKind && moveKind.value !== tab) moveKind.value = tab;
    if (typeof updateMoveFormVisibility === "function") updateMoveFormVisibility();
  }
}

function marketRate(key) {
  return lastData && lastData.market_rates ? lastData.market_rates[key] : null;
}

function updateInvestmentRateHint() {
  const p = document.getElementById("inv-period").value;
  const label = document.getElementById("inv-rate-label");
  const input = document.getElementById("inv-rate");
  if (p === "pct_cdi") {
    label.textContent = "Taxa (% do CDI)";
    input.placeholder = "Ex: 110 = 110% do CDI";
    input.min = "0.01";
  } else if (p === "cdi_spread") {
    label.textContent = "Spread sobre CDI (% a.a.)";
    input.placeholder = "Ex: 2.5 = CDI + 2,5% ao ano";
    input.min = "0";
  } else if (p === "ipca_spread") {
    label.textContent = "Spread sobre IPCA (% a.a.)";
    input.placeholder = "Ex: 7.43 = IPCA + 7,43% ao ano";
    input.min = "0.01";
  } else if (p === "selic_spread") {
    label.textContent = "Spread sobre SELIC (% a.a.)";
    input.placeholder = "Ex: 0 = SELIC pura, 0.07 = SELIC + 0,07%";
    input.min = "0";
  } else if (p === "fixed") {
    label.textContent = "Taxa prefixada (% a.a.)";
    input.placeholder = "Ex: 13.59 = 13,59% ao ano fixo";
    input.min = "0.01";
  }
}

// Limpa o form de "Novo ativo" sempre que o modal abrir nessa tab.
// Inputs de texto/numero e selects voltam para "" (placeholder).
// Data de compra fica preenchida com hoje (default util).
function resetInvestmentCreateForm() {
  ["inv-issuer","inv-name","inv-asset-type","inv-period",
   "inv-rate","inv-frequency","inv-initial-amount",
   "inv-maturity-date","inv-note"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const purchase = document.getElementById("inv-purchase-date");
  if (purchase) {
    const today = new Date();
    const pad = n => String(n).padStart(2, "0");
    purchase.value = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
  }
}

function rateToPayload(indexer, pct) {
  return Number(pct) / 100;
}

function updateInvestmentTaxHint() {
  const asset = document.getElementById("inv-asset-type").value;
  const indexerSelect = document.getElementById("inv-period");
  const guide = INVESTMENT_GUIDES[asset] || INVESTMENT_GUIDES.CDB;
  indexerSelect.value = guide.indexer;
  updateInvestmentRateHint();
  const rateInput = document.getElementById("inv-rate");
  const frequencySelect = document.getElementById("inv-frequency");
  const issuerInput = document.getElementById("inv-issuer");
  const nameInput = document.getElementById("inv-name");
  rateInput.value = guide.rate;
  frequencySelect.value = guide.frequency;
  issuerInput.placeholder = guide.issuerPlaceholder;
  nameInput.placeholder = guide.namePlaceholder;
  const profile = taxProfileForAsset(asset);
  const label = profile === "exempt_ir_iof"
    ? "LCI, LCA, CRI e CRA: isentos de IR e IOF."
    : profile === "etf_rf_15"
      ? "ETF de renda fixa: IR fixo de 15% e sem IOF."
      : "IR regressivo e IOF nos primeiros 30 dias para CDB, Debênture e Tesouro.";
  document.getElementById("inv-tax-hint").textContent = label;
}

function monthlyRate(inv) {
  const rate = Number(effectiveInvestmentRate(inv).rate || 0);
  if (inv.period === "cdi") {
    const cdi = marketRate("cdi_aa")?.value || 0;
    return Math.pow(1 + ((cdi / 100) * rate), 1 / 12) - 1;
  }
  if (inv.period === "yearly") return Math.pow(1 + rate, 1 / 12) - 1;
  if (inv.period === "monthly") return rate;
  if (inv.period === "daily") return Math.pow(1 + rate, 21) - 1;
  return 0;
}

function fmtDateOnly(iso) {
  if (!iso) return "—";
  return new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString("pt-BR");
}

function taxProfileLabel(profile) {
  if (profile === "exempt_ir_iof") return "Isento de IR/IOF";
  if (profile === "etf_rf_15") return "IR fixo 15%";
  return "IR regressivo + IOF";
}

function frequencyLabel(value) {
  return {
    maturity: "Só no vencimento",
    monthly: "Mensal",
    quarterly: "Trimestral",
    semiannual: "Semestral",
    annual: "Anual"
  }[value] || "Só no vencimento";
}

// Tag "fatura <Mês>" pra compras no cartão: a linha entra na lista pelo mês
// da FATURA (igual ao cabeçalho), mas exibe a data da compra — a tag explica
// por que uma compra de 09/07 aparece em agosto.
function _billMonthTag(l) {
  if (l.tipo !== "credito" || !l.bill_period_end) return "";
  const d = new Date(`${l.bill_period_end}T12:00:00`);
  if (isNaN(d)) return "";
  return `<span class="tag x" title="Entra na fatura que fecha em ${d.toLocaleDateString("pt-BR")}"><i class="ph ph-credit-card" aria-hidden="true"></i> fatura ${PT_MONTHS[d.getMonth()].substring(0, 3)}</span>`;
}

function describeLaunch(l) {
  const target = l.alvo || "investimento";
  if (l.nota && l.nota.startsWith("dashboard:create")) return `Criou investimento ${target}`;
  if (l.nota && l.nota.startsWith("dashboard:aporte")) return `Aporte em ${target}`;
  if (l.nota && l.nota.startsWith("dashboard:resgate")) return `Resgate de ${target}`;
  if (l.tipo === "create_investment" && !l.nota) return `Criou investimento ${target}`;
  if (l.tipo === "aporte_investimento" && !l.nota) return `Aporte em ${target}`;
  if (l.tipo === "resgate_investimento" && !l.nota) return `Resgate de ${target}`;

  const base = l.nota || l.alvo || "—";
  // Crédito parcelado: mostra "descrição · N/X" pra ficar claro que são parcelas
  // do mesmo grupo (3 linhas com mesmo valor não são duplicatas).
  if (l.tipo === "credito" && l.installments_total && l.installments_total > 1) {
    const n = l.installment_no || "?";
    return `${base} · ${n}/${l.installments_total}`;
  }
  return base;
}

function irRate(days) {
  if (days <= 180) return .225;
  if (days <= 360) return .20;
  if (days <= 720) return .175;
  return .15;
}

function iofRate(days) {
  const table = [0,96,93,90,86,83,80,76,73,70,66,63,60,56,53,50,46,43,40,36,33,30,26,23,20,16,13,10,6,3,0];
  const d = Math.max(0, Math.min(30, Math.floor(days)));
  return (table[d] || 0) / 100;
}

async function readApiError(resp) {
  try {
    const data = await resp.json();
    // `detail` chega em três formas nesta API e só duas são texto pra usuário:
    //   • STRING — o caso comum;
    //   • OBJETO com `message`/`code` — plan_limit (:5059), same_plan (:4114),
    //     no_change (:4198); ignorá-lo trocava "Você atingiu o limite de 50
    //     lançamentos" por "erro 403";
    //   • LISTA — o 422 do FastAPI, diagnóstico de programador ({loc, msg,
    //     type}), e objeto sem message/code: caem no genérico, senão vira
    //     `{"detail":…}` cru ou "[object Object]" na tela.
    const d = data && data.detail;
    if (typeof d === "string" && d.trim()) return d;
    if (d && typeof d === "object" && !Array.isArray(d)) {
      const m = d.message || d.code;
      if (typeof m === "string" && m.trim()) return m;
    }
  } catch (_) { /* corpo não-JSON (HTML de proxy, vazio): cai no genérico */ }
  /* A cópia irmã em settings.html:1773 NÃO está alinhada com esta, e o
     alinhamento não é deste PR: lá o teste é `typeof detail === "object"`, que é
     TRUE para array, então o 422 do FastAPI (detail = lista de {loc,msg,type})
     cai em `detail.message || detail.code || raw` e sai JSON cru na tela. Aqui o
     `!Array.isArray` fecha esse buraco. Consertar settings.html toca 10 fluxos
     que nada têm a ver com categorias — PR separado. */
  return `Não foi possível concluir agora (erro ${resp.status}).`;
}

async function refreshDashboardAfterInvestment(msg) {
  showToast(msg);
  launchesPage = 1;
  sendRefresh();
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    await fetchMonthHttp(viewYear, viewMonth, 1, LAUNCHES_LIMIT);
  }
}

async function createInvestment(event) {
  event.preventDefault();
  const issuer = document.getElementById("inv-issuer").value.trim();
  const name = document.getElementById("inv-name").value.trim();
  const note = document.getElementById("inv-note").value.trim();
  const assetType = document.getElementById("inv-asset-type").value;
  const indexer = document.getElementById("inv-period").value;
  const period = INDEXER_TO_PERIOD[indexer] || "yearly";
  const ratePct = Number(document.getElementById("inv-rate").value);
  const purchaseDate = document.getElementById("inv-purchase-date").value || null;
  const maturityDate = document.getElementById("inv-maturity-date").value || null;
  const initialAmount = Number(document.getElementById("inv-initial-amount").value || 0);
  const interestPaymentFrequency = document.getElementById("inv-frequency").value;
  const taxProfile = taxProfileForAsset(assetType);
  if (!name || Number.isNaN(ratePct) || (ratePct <= 0 && indexer !== "selic_spread")) {
    await alertModal("Preencha o nome do ativo e a taxa.", { title: "Campos obrigatórios" });
    return;
  }

  const resp = await fetch(`${API}/investments/${USER_ID}`, {
    method: "POST",
    credentials: "same-origin",
    headers: csrfHeaders({"Content-Type":"application/json"}),
    body: JSON.stringify({
      name,
      period,
      rate: rateToPayload(indexer, ratePct),
      initial_amount: initialAmount,
      asset_type: assetType,
      indexer,
      issuer,
      purchase_date: purchaseDate,
      maturity_date: maturityDate,
      interest_payment_frequency: interestPaymentFrequency,
      tax_profile: taxProfile,
      note
    })
  });
  if (!resp.ok) { await alertModal(await readApiError(resp), { title: "Erro" }); return; }
  document.getElementById("inv-name").value = "";
  document.getElementById("inv-note").value = "";
  document.getElementById("inv-initial-amount").value = "";
  closeInvestmentModal();
  await refreshDashboardAfterInvestment("✓ Investimento criado");
}

// Indexadores onde a taxa varia por compra (Tesouro IPCA+/Prefixado,
// Debêntures, CRI/CRA, CDB prefixado etc.). Para % CDI/SELIC/daily/monthly
// a taxa é estável dentro do investimento e o campo extra fica oculto.
const VARIABLE_RATE_PERIODS = new Set(["ipca_spread", "selic_spread", "cdi_spread", "yearly"]);

function updateMoveFormVisibility() {
  const name = document.getElementById("move-name").value;
  const kind = document.getElementById("move-kind").value;
  const row = document.getElementById("move-rate-row");
  const hint = document.getElementById("move-rate-hint");
  const rateInput = document.getElementById("move-rate");
  const dateInput = document.getElementById("move-purchase-date");
  if (!row || !hint || !rateInput || !dateInput) return;

  // Mantém a tab visual do modal alinhada com o select de operação.
  document.querySelectorAll("#invest-overlay [data-invtab]").forEach(b => {
    if (b.dataset.invtab === "deposit" || b.dataset.invtab === "withdraw") {
      const active = b.dataset.invtab === kind;
      b.classList.toggle("active", active);
      b.setAttribute("aria-checked", active ? "true" : "false");
    }
  });

  const inv = (lastData?.investments || []).find(i => i.name === name);
  const showRate = kind === "deposit" && inv && VARIABLE_RATE_PERIODS.has(inv.period);

  row.style.display = showRate ? "" : "none";
  hint.style.display = showRate ? "" : "none";

  if (showRate) {
    if (!rateInput.value && inv.rate != null) {
      const pctSuggestion = Number(inv.rate) * 100;
      rateInput.placeholder = pctSuggestion ? pctSuggestion.toFixed(2) : "6.85";
    }
    if (!dateInput.value) {
      const today = new Date();
      const pad = n => String(n).padStart(2, "0");
      dateInput.value = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
      dateInput.max = dateInput.value;
    }
  } else {
    rateInput.value = "";
    dateInput.value = "";
  }

  const allBtn = document.getElementById("move-withdraw-all");
  if (allBtn) allBtn.style.display = kind === "withdraw" ? "" : "none";
}

async function moveInvestment(event) {
  event.preventDefault();
  const name = document.getElementById("move-name").value;
  const kind = document.getElementById("move-kind").value;
  const amount = Number(document.getElementById("move-amount").value);
  if (!name || !amount || amount <= 0) { await alertModal("Selecione um investimento e informe o valor.", { title: "Campos obrigatórios" }); return; }

  const body = { name, amount };
  if (kind === "deposit") {
    const inv = (lastData?.investments || []).find(i => i.name === name);
    if (inv && VARIABLE_RATE_PERIODS.has(inv.period)) {
      const rateRaw = document.getElementById("move-rate").value;
      const dateRaw = document.getElementById("move-purchase-date").value;
      if (rateRaw !== "") {
        const rate = Number(rateRaw);
        if (!Number.isFinite(rate) || rate < 0) { await alertModal("Taxa inválida.", { title: "Taxa" }); return; }
        // Backend espera taxa em fração (ex.: 0.0685 para 6.85%).
        body.rate = rate / 100;
        body.period = inv.period;
      }
      if (dateRaw) body.purchase_date = dateRaw;
    }
  }

	  const resp = await fetch(`${API}/investments/${USER_ID}/${kind}`, {
	    method: "POST",
	    credentials: "same-origin",
	    headers: csrfHeaders({"Content-Type":"application/json"}),
	    body: JSON.stringify(body)
	  });
	  if (!resp.ok) { await alertModal(await readApiError(resp), { title: "Erro" }); return; }
	  const result = await resp.json();
	  document.getElementById("move-amount").value = "";
	  document.getElementById("move-rate").value = "";
	  document.getElementById("move-purchase-date").value = "";
	  updateMoveFormVisibility();
	  closeInvestmentModal();
	  let message = kind === "deposit" ? "✓ Aporte salvo" : "✓ Resgate salvo";
	  if (kind === "withdraw" && result.tax_summary) {
	    const tax = Number(result.tax_summary.ir || 0) + Number(result.tax_summary.iof || 0);
	    message = tax > 0
	      ? `✓ Resgate salvo · líquido ${fmt(result.tax_summary.net)}`
	      : "✓ Resgate salvo · sem IR/IOF";
	  }
	  await refreshDashboardAfterInvestment(message);
	}

async function investmentWithdrawAll(nameArg) {
  const name = nameArg || document.getElementById("move-name")?.value;
  if (!name) { await alertModal("Selecione um investimento.", { title: "Campos obrigatórios" }); return; }
  const ok = await confirmModal(
    `Resgatar todo o saldo de "${name}" e zerar o investimento? O IR/IOF sobre o rendimento é descontado automaticamente.`,
    { title: "Resgatar tudo", confirmText: "Resgatar tudo" },
  );
  if (!ok) return;
  const resp = await fetch(`${API}/investments/${USER_ID}/withdraw`, {
    method: "POST",
    credentials: "same-origin",
    headers: csrfHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name, withdraw_all: true }),
  });
  if (!resp.ok) { await alertModal(await readApiError(resp), { title: "Erro" }); return; }
  const result = await resp.json();
  const moveAmount = document.getElementById("move-amount");
  if (moveAmount) moveAmount.value = "";
  closeInvestmentModal();
  let message = "✓ Investimento zerado";
  if (result.tax_summary) {
    const t = result.tax_summary;
    const tax = Number(t.ir || 0) + Number(t.iof || 0);
    message = tax > 0
      ? `✓ Investimento zerado · líquido ${fmt(t.net)} (IR/IOF ${fmt(tax)})`
      : `✓ Investimento zerado · resgatado ${fmt(t.gross || 0)}`;
  }
  await refreshDashboardAfterInvestment(message);
}

async function deleteInvestment(name) {
  const ok = await confirmModal(`Remover ${name}? O saldo precisa estar zerado.`, {
    title: "Excluir investimento",
    confirmText: "Excluir",
    destructive: true,
  });
  if (!ok) return;
  const resp = await fetch(`${API}/investments/${USER_ID}/${encodeURIComponent(name)}`, {
    method: "DELETE",
    credentials: "same-origin",
    headers: csrfHeaders()
  });
  if (!resp.ok) { await alertModal(await readApiError(resp), { title: "Erro" }); return; }
  await refreshDashboardAfterInvestment("✓ Investimento removido");
}

function openInvestmentDetail(name) {
  const inv = (lastData?.investments || []).find(i => i.name === name);
  if (!inv) return;

  const visibleName = displayInvestmentName(inv);
  const mRate = monthlyRate(inv);
  const grossMonth = Number(inv.balance || 0) * mRate;
  const netMonth = grossMonth * (inv.tax_profile === "exempt_ir_iof" ? 1 : .85);
  const history = (lastData?.recent_launches || [])
    .filter(l => String(l.alvo || "").toLowerCase() === String(inv.name || "").toLowerCase())
    .slice(0, 6);

  document.getElementById("investment-detail-title").textContent = visibleName;
  document.getElementById("investment-detail-sub").textContent = [
    inv.asset_type || "Investimento",
    inv.issuer,
    PERIOD_LABELS[inv.period] || inv.period
  ].filter(Boolean).join(" · ");

  const cells = [
    ["Saldo atual", fmt(inv.balance || 0)],
    ["Taxa", investmentRateLabel(inv)],
    ["Rendimento bruto/mês (simulado)", fmt(grossMonth)],
    ["Líquido estimado/mês (simulado)", fmt(netMonth)],
    ["Emissor", inv.issuer || "—"],
    ["Tributação", taxProfileLabel(inv.tax_profile)],
    ["Compra", fmtDateOnly(inv.purchase_date)],
    ["Vencimento", fmtDateOnly(inv.maturity_date)],
    ["Juros", frequencyLabel(inv.interest_payment_frequency)],
    ["Base de cálculo", fmtDateOnly(inv.last_date)],
    ["Indexador", PERIOD_LABELS[inv.period] || inv.period || "—"],
    ["Tipo", inv.asset_type || "—"]
  ];

	  const historyHtml = history.length
	    ? history.map(l => `
	        <div class="row">
	          <span class="lbl">${esc(describeLaunch(l))}</span>
	          <span style="display:flex;flex-direction:column;align-items:flex-end;gap:2px">
	            <span class="val" data-val="${l.valor}">${fmt(l.valor)}</span>
	            <span style="font-size:.65rem;color:var(--text-3)">${fmtDate(l.criado_em)}</span>
	          </span>
	        </div>
	      `).join("")
	    : `<div class="empty">Sem movimentações recentes.</div>`;
	  const lots = (inv.lots || []).filter(l => l.status === "open" && Number(l.balance || 0) > 0);
	  const lotsHtml = lots.length
	    ? lots.map(l => {
	        const lotPeriod = l.period || inv.period;
	        const lotRate = l.rate != null ? l.rate : inv.rate;
	        const rateLabel = investmentRateLabel({ ...inv, period: lotPeriod, rate: lotRate });
	        return `
	        <div class="row">
	          <span class="lbl">Lote de ${fmtDateOnly(l.opened_at)} · ${Number(l.age_days || 0)} dias</span>
	          <span style="display:flex;flex-direction:column;align-items:flex-end;gap:2px">
	            <span class="val">${fmt(l.balance)}</span>
	            <span style="font-size:.65rem;color:var(--text-3)">${esc(rateLabel)} · principal ${fmt(l.principal_remaining)}</span>
	          </span>
	        </div>
	      `;
	      }).join("")
	    : `<div class="empty">Sem lotes abertos.</div>`;

	  document.getElementById("investment-detail-body").innerHTML = `
	    <div class="detail-grid">
	      ${cells.map(([k, v]) => `
	        <div class="detail-cell">
	          <div class="detail-k">${esc(k)}</div>
	          <div class="detail-v">${esc(v)}</div>
	        </div>
	      `).join("")}
	    </div>
	    <h2 style="margin-top:10px">Lotes de aporte</h2>
	    <div class="detail-history">${lotsHtml}</div>
	    <h2 style="margin-top:10px">Movimentações recentes</h2>
	    <div class="detail-history">${historyHtml}</div>
	  `;

  document.getElementById("investment-detail-move").onclick = () => {
    closeInvestmentDetail();
    openInvestmentModal("deposit");
    const moveName = document.getElementById("move-name");
    if (moveName) moveName.value = inv.name;
    if (typeof updateMoveFormVisibility === "function") updateMoveFormVisibility();
    setTimeout(() => document.getElementById("move-amount")?.focus(), 50);
  };
  document.getElementById("investment-detail-delete").onclick = () => {
    closeInvestmentDetail();
    deleteInvestment(inv.name);
  };
  const detailWithdrawAll = document.getElementById("investment-detail-withdraw-all");
  if (detailWithdrawAll) {
    detailWithdrawAll.style.display = Number(inv.balance || 0) > 0 ? "" : "none";
    detailWithdrawAll.onclick = () => {
      closeInvestmentDetail();
      investmentWithdrawAll(inv.name);
    };
  }
  document.getElementById("investment-detail-overlay").classList.add("open");
}

function closeInvestmentDetail() {
  document.getElementById("investment-detail-overlay").classList.remove("open");
}

function openInvestmentHelp() {
  document.getElementById("investment-help-overlay").classList.add("open");
}

function closeInvestmentHelp() {
  document.getElementById("investment-help-overlay").classList.remove("open");
}

function renderInvestmentsPanel(d) {
  const rates = d.market_rates || {};
  const rateHtml = [
    ["CDI a.a.", rates.cdi_aa],
    ["SELIC a.a.", rates.selic_aa],
    ["IPCA 12m", rates.ipca_12m],
  ].map(([label, item]) => `
    <div class="rate-tile">
      <div class="rate-name">${label}</div>
      <div class="rate-val">${fmtPct(item && item.value)}</div>
      <div class="rate-date">${item && item.date ? new Date(item.date + "T00:00:00").toLocaleDateString("pt-BR") : "sem cache"}</div>
    </div>
  `).join("");
  document.getElementById("invest-rate-grid").innerHTML = rateHtml;

  const invs = d.investments || [];
  const move = document.getElementById("move-name");
  move.innerHTML = invs.length
    ? invs.map(i => `<option value="${esc(i.name)}">${esc(displayInvestmentName(i))}</option>`).join("")
    : `<option value="">Nenhum investimento</option>`;
  // Recalcula visibilidade dos campos extras do form de aporte sempre que
  // o select é repopulado (refresh do dashboard, troca de investimento etc.).
  if (typeof updateMoveFormVisibility === "function") updateMoveFormVisibility();

  const total = invs.reduce((s, i) => s + Number(i.balance || 0), 0);
  const grossMonth = invs.reduce((s, i) => s + Number(i.balance || 0) * monthlyRate(i), 0);
  const netMonth = grossMonth * (1 - .15);
  document.getElementById("invest-summary").innerHTML = `
    <div class="chips" style="margin-top:0;margin-bottom:6px">
      <div class="chip"><div class="chip-lbl">Patrimônio</div><div class="chip-val b">${fmt(total)}</div></div>
      <div class="chip"><div class="chip-lbl">Rend. bruto/mês <span style="opacity:.6;font-weight:400">(simulado)</span></div><div class="chip-val g">${fmt(grossMonth)}</div></div>
      <div class="chip"><div class="chip-lbl">Líquido estimado <span style="opacity:.6;font-weight:400">(simulado)</span></div><div class="chip-val">${fmt(netMonth)}</div></div>
    </div>
    <div style="color:var(--text-3);font-size:.75rem;margin-bottom:12px;line-height:1.35"><i class="ph ph-lightbulb" aria-hidden="true"></i> Rendimentos exibidos são simulações baseadas na taxa informada. O PigBank não custodia os valores aplicados.</div>
  `;

	  document.getElementById("invest-list").innerHTML = invs.length ? invs.map(i => {
	    const mRate = monthlyRate(i);
	    const visibleName = displayInvestmentName(i);
	    const useProj = (i.projected_days || 0) > 0 && i.projected_balance != null;
	    const displayBal = useProj ? i.projected_balance : i.balance;
	    return `
	      <div class="invest-card" data-invest-name="${esc(i.name)}" onclick="openInvestmentDetail(this.dataset.investName)">
	        <div class="invest-head">
	          <div style="min-width:0">
	            <div class="invest-name">${esc(visibleName)}</div>
	            <div class="invest-meta">
	              <span class="mini-tag">${esc(i.asset_type || "CDB")}</span>
	              ${i.issuer ? `<span class="mini-tag">${esc(i.issuer)}</span>` : ""}
	              <span class="mini-tag">${PERIOD_LABELS[i.period] || esc(i.period)}</span>
              <span class="mini-tag">${investmentRateLabel(i)}</span>
	              <span class="mini-tag">base ${i.last_date || "—"}</span>
            </div>
          </div>
          <div style="text-align:right">
            <div class="val b">${fmt(displayBal)}</div>
          </div>
        </div>
        <div class="tax-note">Simulação mensal: bruto ${fmt(Number(displayBal || 0) * mRate)} · líquido ${fmt(Number(displayBal || 0) * mRate * .85)}</div>
      </div>
    `;
  }).join("") : `<div class="empty">Nenhum investimento cadastrado.</div>`;

  renderVariableIncomePanel(d);
  renderOfFixedIncomePanel(d);
}

// Renda fixa do banco (CDB/Tesouro via Open Finance) — agregada, read-only.
function renderOfFixedIncomePanel(d) {
  const card = document.getElementById("of-rf-card");
  if (!card) return;
  const items = d.of_fixed_income || [];
  const sum = d.of_fixed_income_summary || {};
  if (!items.length) { card.style.display = "none"; return; }
  card.style.display = "";

  const elS = document.getElementById("of-rf-summary");
  if (elS) elS.innerHTML = `
    <div class="chips" style="margin-top:0;margin-bottom:6px">
      <div class="chip"><div class="chip-lbl">Saldo</div><div class="chip-val b">${fmt(sum.balance || 0)}</div></div>
      <div class="chip"><div class="chip-lbl">Investido</div><div class="chip-val">${fmt(sum.invested || 0)}</div></div>
      <div class="chip"><div class="chip-lbl">Rendeu</div><div class="chip-val">${fmtPnl(sum.pnl || 0)}</div></div>
    </div>`;

  const elL = document.getElementById("of-rf-list");
  if (!elL) return;
  elL.innerHTML = items.map(i => {
    const n = Number(i.count || 1);
    const papeis = n > 1 ? `<span class="mini-tag">${n} papéis</span>` : "";
    return `
      <div class="invest-card rv-card-item">
        <div class="invest-head">
          <div style="min-width:0">
            <div class="invest-name">${esc(i.name)} <span class="rv-badge">via banco</span></div>
            <div class="invest-meta"><span class="mini-tag">Renda fixa</span>${papeis}</div>
          </div>
          <div style="text-align:right">
            <div class="val b">${fmt(i.balance)}</div>
            <div style="font-size:.8rem;margin-top:2px">${fmtPnl(i.pnl, i.pnl_pct)}</div>
          </div>
        </div>
      </div>`;
  }).join("");
}

// Renda variável (ações/FIIs) vinda do Open Finance — read-only, marcada a mercado.
const RV_KIND_LABELS = { stock: "Ação", fii: "FII", etf: "ETF", bdr: "BDR", crypto: "Cripto", fund: "Fundo" };

function fmtPnl(v, pct) {
  const up = Number(v) >= 0;
  const arrow = up ? "↑" : "↓";
  const cls = up ? "pnl-up" : "pnl-down";
  const pctTxt = (pct != null) ? ` (${(Number(pct) * 100).toFixed(2).replace(".", ",")}%)` : "";
  return `<span class="${cls}">${arrow} ${fmt(Math.abs(Number(v)))}${pctTxt}</span>`;
}

function renderVariableIncomePanel(d) {
  const pos = d.rv_positions || [];
  const sum = d.rv_summary || {};

  const summaryHtml = `
    <div class="chips" style="margin-top:0;margin-bottom:6px">
      <div class="chip"><div class="chip-lbl">Valor de mercado</div><div class="chip-val b">${fmt(sum.market_value || 0)}</div></div>
      <div class="chip"><div class="chip-lbl">Investido</div><div class="chip-val">${fmt(sum.invested || 0)}</div></div>
      <div class="chip"><div class="chip-lbl">Resultado</div><div class="chip-val">${fmtPnl(sum.pnl || 0)}</div></div>
    </div>`;
  const listHtml = pos.map(p => {
    const day = (p.last_month_rate != null)
      ? `<span class="mini-tag">${Number(p.last_month_rate).toFixed(2).replace(".", ",")}% no mês</span>` : "";
    const qty = (p.quantity != null) ? `${Number(p.quantity).toLocaleString("pt-BR")} cotas` : "";
    const px = (p.market_price != null) ? ` × ${fmt(p.market_price)}` : "";
    return `
      <div class="invest-card rv-card-item">
        <div class="invest-head">
          <div style="min-width:0">
            <div class="invest-name">${esc(p.ticker || p.name)}
              <span class="rv-badge">via corretora</span></div>
            <div class="invest-meta">
              <span class="mini-tag">${RV_KIND_LABELS[p.kind] || esc(p.kind)}</span>
              ${qty ? `<span class="mini-tag">${qty}${px}</span>` : ""}
              ${day}
            </div>
          </div>
          <div style="text-align:right">
            <div class="val b">${fmt(p.market_value)}</div>
            <div style="font-size:.8rem;margin-top:2px">${fmtPnl(p.pnl, p.pnl_pct)}</div>
          </div>
        </div>
      </div>`;
  }).join("");

  // Card na aba de Investimentos: some quando não há posições.
  const card = document.getElementById("rv-card");
  if (card) {
    if (!pos.length) {
      card.style.display = "none";
    } else {
      card.style.display = "";
      const s = document.getElementById("rv-summary"); if (s) s.innerHTML = summaryHtml;
      const l = document.getElementById("rv-list"); if (l) l.innerHTML = listHtml;
    }
  }
}

function runInvestmentSimulator() {
  if (!document.getElementById("sim-output")) return;
  const indexer = document.getElementById("sim-indexer").value;
  const rate = Number(document.getElementById("sim-rate").value || 0);
  const amount = Number(document.getElementById("sim-amount").value || 0);
  const years = Number(document.getElementById("sim-years").value || 0);
  const taxed = document.getElementById("sim-tax").value === "taxed";
  const cdi = marketRate("cdi_aa")?.value || 0;
  const selic = marketRate("selic_aa")?.value || 0;
  const ipca = marketRate("ipca_12m")?.value || 0;

  let annual = rate;
  if (indexer === "cdi") annual = cdi * (rate / 100);
  if (indexer === "selic") annual = selic * (rate / 100);
  if (indexer === "ipca") annual = ipca + rate;

  const days = Math.max(1, Math.round(years * 365));
  const grossGain = amount * (Math.pow(1 + annual / 100, years) - 1);
  const iof = taxed ? grossGain * iofRate(days) : 0;
  const ir = taxed ? (grossGain - iof) * irRate(days) : 0;
  const netGain = grossGain - iof - ir;

  document.getElementById("sim-output").innerHTML = `
    <div class="sim-result">
      <div class="rate-tile"><div class="rate-name">Taxa usada</div><div class="rate-val">${fmtPct(annual)}</div><div class="rate-date">a.a.</div></div>
      <div class="rate-tile"><div class="rate-name">Ganho bruto</div><div class="rate-val">${fmt(grossGain)}</div><div class="rate-date">${days} dias</div></div>
      <div class="rate-tile"><div class="rate-name">Ganho líquido</div><div class="rate-val">${fmt(netGain)}</div><div class="rate-date">IR ${fmtPct(taxed ? irRate(days) * 100 : 0)}</div></div>
    </div>
  `;
}

/* ═══════════════════════════════════════════════════════════════════════
   COUNTER ANIMATION
═══════════════════════════════════════════════════════════════════════ */
function animateCounters() {
  document.querySelectorAll("[data-num]").forEach(el => {
    const key    = el.dataset.num;
    const target = parseFloat(el.dataset.val);
    const from   = prevNums[key] !== undefined ? prevNums[key] : 0;
    prevNums[key] = target;
    if (Math.abs(from - target) < 0.005) { el.textContent = fmt(target); return; }
    const dur = 650, t0 = performance.now();
    const tick = now => {
      const p = Math.min((now - t0) / dur, 1);
      const e = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(from + (target - from) * e);
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

/* ═══════════════════════════════════════════════════════════════════════
   MONTH SELECTOR
═══════════════════════════════════════════════════════════════════════ */
function updateMonthLabel() {
  document.getElementById("month-label").textContent =
    PT_MONTHS[viewMonth - 1] + " " + viewYear;
  const isCurrent = viewYear * 12 + viewMonth >= latestKnownMonth;
  document.getElementById("btn-next").disabled = isCurrent;
  const earliestMonth = historyEarliestDate
    ? Number(historyEarliestDate.slice(0, 4)) * 12 + Number(historyEarliestDate.slice(5, 7))
    : null;
  const viewedMonth = viewYear * 12 + viewMonth;
  document.getElementById("btn-prev").disabled = earliestMonth !== null && viewedMonth <= earliestMonth;
}

function changeMonth(d) {
  const target = new Date(viewYear, viewMonth - 1 + d, 1);
  const targetYear = target.getFullYear();
  const targetMonth = target.getMonth() + 1;
  if (historyEarliestDate) {
    const earliestMonth = Number(historyEarliestDate.slice(0, 4)) * 12 + Number(historyEarliestDate.slice(5, 7));
    if (targetYear * 12 + targetMonth < earliestMonth) return;
  }
  viewYear = targetYear;
  viewMonth = targetMonth;
  userNavigatedMonth = true; // a partir daqui o snapshot não corrige mais o mês

  launchesPage = 1;
  updateMonthLabel();

  const btn = document.getElementById("refresh-btn");
  btn.classList.add("spinning");
  btn.disabled = true;

  const cached = monthDataCache.get(monthCacheKey(viewYear, viewMonth, 1));
  if (cached) {
    lastData = cached;
    render(cached);
    stopSpin();
    setLaunchesLoading(false);
  }

  requestMonthPage(1, { smoothScroll: false, preferHttp: true, background: Boolean(cached) });
}

async function fetchMonthHttp(year, month, page = 1, limit = LAUNCHES_LIMIT, { background = false } = {}) {
  const seq = ++monthRequestSeq;
  if (monthAbortController) {
    monthAbortController.abort();
  }
  monthAbortController = new AbortController();

  try {
    const params = new URLSearchParams({
      year: String(year),
      month: String(month),
      page: String(page),
      limit: String(limit),
      filter_type: filterType || "all",
      q: getFilterText(),
    });
    const r = await fetch(
      `${API}/data/${USER_ID}?${params.toString()}`,
      {
        credentials: "same-origin",
        signal: monthAbortController.signal,
      }
    );
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (seq !== monthRequestSeq) return;
    if (!isCurrentViewData(data)) {
      if (!background) {
        stopSpin();
        setLaunchesLoading(false);
      }
      return;
    }

    lastData = data;
    cacheMonthData(data);
    render(data);
    stopSpin();
    setLaunchesLoading(false);
  } catch(err) {
    if (err.name === "AbortError") return;   // superado por outro pedido: neutro
    console.error("fetchMonthHttp error:", err);
    if (seq === monthRequestSeq && !background) {
      stopSpin();
      setLaunchesLoading(false);
    }
    // O render antigo fica (certo), mas quem chamou precisa saber que nada
    // veio — o puxar pra atualizar usa isto pra ficar âmbar em vez de
    // recolher como sucesso. Callers antigos ignoram o retorno.
    return false;
  } finally {
    if (seq === monthRequestSeq) {
      monthAbortController = null;
    }
  }
}

/* ═══════════════════════════════════════════════════════════════════════
   WEBSOCKET
═══════════════════════════════════════════════════════════════════════ */
function sendRefresh() {
  _doRefresh({ silent: false });
}

// Refresh em background — não dim o card nem trava o botão. Use depois de
// updates otimistas pra sincronizar saldo/totais sem flash visual.
function sendRefreshSilent() {
  _doRefresh({ silent: true });
}

// Agrupa a rajada de `open_finance_synced` (ver ws.onmessage) num refresh só.
let _ofSyncDebounce;

function _doRefresh({ silent }) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const msg = {
      type: "get_month",
      year: viewYear,
      month: viewMonth,
      page: launchesPage,
      limit: LAUNCHES_LIMIT,
      filter_type: filterType || "all",
      q: getFilterText()
    };
    ws.send(JSON.stringify(msg));

    if (!silent) {
      const btn = document.getElementById("refresh-btn");
      btn.classList.add("spinning");
      btn.disabled = true;
      setLaunchesLoading(true);
    }
    fetchHistory();
  }
}
function stopSpin() {
  const btn = document.getElementById("refresh-btn");
  btn.classList.remove("spinning"); btn.disabled = false;
}

let toastT;
function showToast(msg = "✓ Atualizado") {
  const t = document.getElementById("toast");
  const g = document.getElementById("grid");
  g.classList.remove("grid-flash"); void g.offsetWidth; g.classList.add("grid-flash");
  if (/^\s*✓/.test(msg)) {
    t.innerHTML = `<img class="toast-sticker" src="/brand/stickers/ok.webp" alt="" />` +
                  escapeHtmlSafe(msg.replace(/^\s*✓\s*/, ""));
  } else {
    t.textContent = msg;
  }
  t.classList.add("show");
  clearTimeout(toastT);
  toastT = setTimeout(() => t.classList.remove("show"), 2000);
}

function setStatus(s) {
  const dot = document.getElementById("dot"), txt = document.getElementById("status-text");
  dot.className = "";
  if (s === "connected")    { dot.classList.add("connected");  txt.textContent = "Live"; }
  if (s === "connecting")   { dot.classList.add("connecting"); txt.textContent = "Conectando…"; }
  if (s === "disconnected") { txt.textContent = "Desconectado – reconectando…"; }
}

// Veredito de acesso do /auth/me: false = a tela vai embora (paywall/erro), e
// o que essa saída exige já foi executado aqui. Uma fonte só, usada pelo boot
// e pela revalidação disparada por reconexões rejeitadas.
function applyAccessVerdict(me) {
  if (me && me.needs_plan_selection && !window.PB_IN_APP) {
    clearSessionSnapshots();  // veredito negativo: reload não repinta saldo
    stopWsRetries();
    window.location.replace("/precos?escolha=1");
    return false;
  }
  if (me && me.app_access === false) {
    clearSessionSnapshots();
    // O socket já pode estar conectado/reconectando — para tudo antes de
    // trocar de tela, senão a tela de erro fica reconectando por baixo.
    stopWsRetries();
    if (window.PB_IN_APP) {
      // App iOS: tela neutra, sem link de compra (diretriz 3.1.1).
      _showAccessError("Conta sem plano ativo", "Sua conta não tem um plano ativo no momento.");
    } else {
      window.location.replace("/precos?ativar=1");
    }
    return false;
  }
  return true;
}

// Reconexões seguidas rejeitadas: pergunta ao /auth/me se o acesso caiu.
// Falha de rede aqui não decide nada (segue tentando; o 402 protege os dados).
async function revalidateAccess() {
  try {
    const r = await fetch(`${API}/auth/me`, { credentials: "same-origin" });
    if (r.ok) applyAccessVerdict(await r.json());
  } catch {}
}

// Para de reconectar e mata o socket em voo. Usada quando o veredito de
// acesso NEGA — sem a flag, um onclose já disparado reagendaria o retry.
function stopWsRetries() {
  wsRetryStopped = true;
  clearTimeout(wsReconnectTimer);
  try { if (ws) { ws.onclose = null; ws.onmessage = null; ws.close(); } } catch {}
}

function connect() {
  if (wsRetryStopped) return;
  setStatus("connecting");
  wsOpenedLastAttempt = false;
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    wsOpenedLastAttempt = true;
    wsFailStreak = 0;
    wsRetryDelay = 3000;
    setStatus("connected");
    // Server já manda snapshot automático ao conectar (linha 5368 do backend).
    // O get_month aqui era redundante — gerava 2 chamadas a get_financial_data
    // por refresh. Mantemos só pra navegação entre meses (não no onopen).
    fetchHistory();
  };
    ws.onmessage = e => {
    const msg = JSON.parse(e.data);

    if (msg.type === "snapshot" || msg.type === "month_data") {
      // Virada de mês servidor×dispositivo. A causa PRIMÁRIA foi resolvida no
      // servidor (main 8ea113a, #215): o snapshot passou a usar now_tz() — o
      // fuso do APP — em vez de UTC cru, o que zera a divergência para quem
      // está no fuso do app (era ~3 h/mês para todo mundo).
      // Este guard cobre o RESÍDUO: now_tz() é um fuso único do servidor, e o
      // dashboard calcula viewYear/viewMonth no relógio do DISPOSITIVO — quem
      // está em outro fuso (viagem, aparelho configurado diferente) ainda
      // diverge no último dia do mês (ex.: Tóquio +12 h de São Paulo). Sem
      // adotar, o snapshot é descartado e a tela fica em skeleton.
      // O servidor é a fonte da verdade do mês (é a decisão do 8ea113a), então
      // se o usuário ainda não navegou manualmente, o mês do snapshot vence.
      // (month_data fica de fora: é resposta a um get_month explícito, e
      // um mês que não é mais o da visão tem mesmo que ser descartado.)
      if (msg.type === "snapshot" && !userNavigatedMonth &&
          msg.data?.year && msg.data?.month && !isCurrentViewData(msg.data)) {
        viewYear = Number(msg.data.year);
        viewMonth = Number(msg.data.month);
        // O servidor conhece um mês mais novo que o relógio local: o teto do
        // btn-next avança junto (senão "próximo mês" abre um mês vazio).
        latestKnownMonth = Math.max(latestKnownMonth, viewYear * 12 + viewMonth);
        updateMonthLabel();
      }
      if (!isCurrentViewData(msg.data)) return;
      lastData = msg.data;
      cacheMonthData(msg.data);
      persistSnapshotToSession(msg.data);
      render(msg.data);
      stopSpin();
      setLaunchesLoading(false);
    } else if (msg.type === "update") {
      const serverYear  = msg.data.year  || NOW.getFullYear();
      const serverMonth = msg.data.month || (NOW.getMonth() + 1);

      if (serverYear === viewYear && serverMonth === viewMonth) {
        lastData = msg.data;
        cacheMonthData(msg.data);
        persistSnapshotToSession(msg.data);
        render(msg.data);
        showToast();
      }

      stopSpin();
      setLaunchesLoading(false);
    } else if (msg.type === "open_finance_synced") {
      // O backend já mandava este evento e NENHUM arquivo do front o tratava —
      // o banco sincronizava e o dashboard só mostrava depois de um F5. O
      // debounce agrupa a rajada de webhooks da Pluggy (item/updated e
      // transactions/created chegam com segundos de diferença) num pedido só.
      clearTimeout(_ofSyncDebounce);
      _ofSyncDebounce = setTimeout(sendRefreshSilent, 1500);
    }
  };
  ws.onclose = () => {
    setStatus("disconnected");
    if (wsRetryStopped) return;
    let delay = 3000;
    if (!wsOpenedLastAttempt) {          // não chegou a abrir: rejeitado/outage
      wsFailStreak++;
      wsRetryDelay = Math.min(wsRetryDelay * 2, 60000);
      delay = wsRetryDelay;
      // Rejeição repetida pode ser plano revogado no meio da sessão — o
      // cliente não lê o motivo (1006), então PERGUNTA ao /auth/me.
      if (wsFailStreak % WS_REVALIDATE_AFTER === 0) revalidateAccess();
    }
    wsReconnectTimer = setTimeout(connect, delay);
  };
  ws.onerror = () => ws.close();
}

/* ═══════════════════════════════════════════════════════════════════════
   ALERTS
═══════════════════════════════════════════════════════════════════════ */
// "hoje" / "ontem" / "em 09/07" a partir do timestamp real da cobrança.
// Antes o texto hardcodava "hoje" — uma cobrança de 09/07 não-reconhecida
// continuava anunciada como "hoje" semanas depois (lançamento fantasma).
function _alertWhenLabel(iso) {
  if (!iso) return "hoje";
  const d = new Date(iso);
  if (isNaN(d)) return "hoje";
  const now = new Date();
  const day = x => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
  const yest = new Date(now); yest.setDate(now.getDate() - 1);
  if (day(d) === day(now)) return "hoje";
  if (day(d) === day(yest)) return "ontem";
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return `em ${dd}/${mm}`;
}

let _lastAlerts = [];

function renderAlerts(alerts) {
  const b = document.getElementById("alert-banner");
  _lastAlerts = alerts || [];
  if (!alerts || !alerts.length || alertsDismissed) {
    b.style.display = "none";
    b.innerHTML = "";
    return;
  }
  const exceeded = alerts.filter(a => a.type === "budget_exceeded");
  let html = "";
  alerts.forEach(a => {
    if (a.type === "recurring_charged") {
      const where = a.payment_type === "credit_card" ? "no cartão" : "da conta";
      html += `<div class="alert-row"><i class="ph ph-piggy-bank" aria-hidden="true"></i> Piggy lançou <b>${escapeHtmlSafe(a.name)}</b> ${fmt(a.amount)} ${where} ${_alertWhenLabel(a.charged_at)}. <button onclick="ackRecurringCharge(${a.charge_id})" aria-label="Marcar como visto" title="Marcar como visto" style="background:none;border:none;color:var(--text-3);cursor:pointer;font-size:.85rem;line-height:1;padding:2px 6px;margin-left:6px;border-radius:6px;opacity:.7;transition:opacity .15s,background .15s" onmouseover="this.style.opacity=1;this.style.background='rgba(255,255,255,.08)'" onmouseout="this.style.opacity=.7;this.style.background='none'"><i class="ph ph-x" aria-hidden="true"></i></button></div>`;
    } else if (a.type === "recurring_credited") {
      html += `<div class="alert-row"><i class="ph ph-piggy-bank" aria-hidden="true"></i> Piggy recebeu <b>${escapeHtmlSafe(a.name)}</b> ${fmt(a.amount)} na conta ${_alertWhenLabel(a.credited_at)}. <button onclick="ackRecurringIncomeCredit(${a.credit_id})" aria-label="Marcar como visto" title="Marcar como visto" style="background:none;border:none;color:var(--text-3);cursor:pointer;font-size:.85rem;line-height:1;padding:2px 6px;margin-left:6px;border-radius:6px;opacity:.7;transition:opacity .15s,background .15s" onmouseover="this.style.opacity=1;this.style.background='rgba(255,255,255,.08)'" onmouseout="this.style.opacity=.7;this.style.background='none'"><i class="ph ph-x" aria-hidden="true"></i></button></div>`;
    } else {
      const icon = a.type === "budget_exceeded" ? '<i class="ph ph-warning-circle" aria-hidden="true"></i>' : '<i class="ph ph-warning" aria-hidden="true"></i>';
      html += `<div class="alert-row">${icon} <b>${escapeHtmlSafe(a.categoria)}</b>: ${fmt(a.spent)} de ${fmt(a.budget)} (${a.pct}%)</div>`;
    }
  });
  b.innerHTML = `
    <div class="alert-banner-head">
      <div class="alert-banner-body">${html}</div>
      <button class="alert-close" type="button" aria-label="Fechar aviso" onclick="dismissAlerts()"><i class="ph ph-x" aria-hidden="true"></i></button>
    </div>
  `;
  b.className = exceeded.length ? "exceeded" : "warning";
  b.style.display = "block";
}

function dismissAlerts() {
  // Esconde já (responsivo) e PERSISTE o reconhecimento das cobranças
  // exibidas. Antes só setava a flag client-side — no reload o banner
  // ressuscitava, re-anunciando cobranças antigas.
  alertsDismissed = true;
  const b = document.getElementById("alert-banner");
  b.style.display = "none";
  _lastAlerts.forEach(a => {
    if (a.type === "recurring_charged" && a.charge_id) ackRecurringCharge(a.charge_id, { silent: true });
    else if (a.type === "recurring_credited" && a.credit_id) ackRecurringIncomeCredit(a.credit_id, { silent: true });
  });
}

async function ackRecurringCharge(chargeId, opts) {
  if (!chargeId || !USER_ID) return;
  try {
    const res = await fetch(`${API}/recurring-expenses/${USER_ID}/charges/${chargeId}/ack`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!res.ok) {
      console.warn(`[alerts] ack charge ${chargeId} falhou: HTTP ${res.status}`);
      return;
    }
    if (!(opts && opts.silent)) sendRefresh();
  } catch (err) {
    console.warn(`[alerts] ack charge ${chargeId} falhou:`, err);
  }
}

async function ackRecurringIncomeCredit(creditId, opts) {
  if (!creditId || !USER_ID) return;
  try {
    const res = await fetch(`${API}/recurring-incomes/${USER_ID}/credits/${creditId}/ack`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!res.ok) {
      console.warn(`[alerts] ack credit ${creditId} falhou: HTTP ${res.status}`);
      return;
    }
    if (!(opts && opts.silent)) sendRefresh();
  } catch (err) {
    console.warn(`[alerts] ack credit ${creditId} falhou:`, err);
  }
}

/* ═══════════════════════════════════════════════════════════════════════
   FILTER + LAUNCHES
═══════════════════════════════════════════════════════════════════════ */
function setLaunchesLoading(on) {
  launchesLoading = on;
  const card = document.getElementById("launches-card");
  if (!card) return;

  card.style.transition = "opacity .18s ease";
  card.style.opacity = on ? "0.45" : "1";
}

function scrollLaunchesToTop() {
  const wrap = document.getElementById("launches-wrap");
  if (wrap) wrap.scrollIntoView({ behavior: "smooth", block: "start" });
}

function requestMonthPage(page, { smoothScroll = true, preferHttp = false, background = false } = {}) {
  launchesPage = page;
  if (!background) {
    setLaunchesLoading(true);
  }

  if (smoothScroll) {
    scrollLaunchesToTop();
  }

  if (!preferHttp && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: "get_month",
      year: viewYear,
      month: viewMonth,
      page: launchesPage,
      limit: LAUNCHES_LIMIT,
      filter_type: filterType || "all",
      q: getFilterText()
    }));
  } else {
    fetchMonthHttp(viewYear, viewMonth, launchesPage, LAUNCHES_LIMIT, { background });
  }
}

function setTab(el) {
  document.querySelectorAll(".ftab").forEach(t => t.classList.remove("active"));
  el.classList.add("active");
  filterType = el.dataset.type;
  requestMonthPage(1, { smoothScroll: false, preferHttp: true });
}
function applyFilter() {
  clearTimeout(filterDebounceTimer);
  filterDebounceTimer = setTimeout(() => {
    requestMonthPage(1, { smoothScroll: false, preferHttp: true });
  }, 220);
}

function setLaunchesPage(page) {
  requestMonthPage(page);
}

function renderLaunchesPagination(totalItems, totalPages) {
  if (totalPages <= 1) return "";

  let html = `<div style="display:flex;justify-content:center;align-items:center;gap:8px;flex-wrap:wrap;margin-top:16px;">`;

  html += `
    <button class="ftab"
            onclick="setLaunchesPage(1)"
            ${launchesPage === 1 ? 'disabled style="opacity:.5;cursor:default;"' : ""}>
      «
    </button>
  `;

  html += `
    <button class="ftab"
            onclick="setLaunchesPage(${Math.max(1, launchesPage - 1)})"
            ${launchesPage === 1 ? 'disabled style="opacity:.5;cursor:default;"' : ""}>
      ‹
    </button>
  `;

  const start = Math.max(1, launchesPage - 2);
  const end   = Math.min(totalPages, launchesPage + 2);

  for (let p = start; p <= end; p++) {
    html += `
      <button class="ftab ${p === launchesPage ? "active" : ""}" onclick="setLaunchesPage(${p})">
        ${p}
      </button>
    `;
  }

  html += `
    <button class="ftab"
            onclick="setLaunchesPage(${Math.min(totalPages, launchesPage + 1)})"
            ${launchesPage === totalPages ? 'disabled style="opacity:.5;cursor:default;"' : ""}>
      ›
    </button>
  `;

  html += `
    <button class="ftab"
            onclick="setLaunchesPage(${totalPages})"
            ${launchesPage === totalPages ? 'disabled style="opacity:.5;cursor:default;"' : ""}>
      »
    </button>
  `;

  html += `</div>`;

  html += `
    <div style="margin-top:8px;text-align:center;font-size:.72rem;color:var(--text-3);">
      Mostrando ${totalItems === 0 ? 0 : ((launchesPage - 1) * LAUNCHES_LIMIT + 1)}
      –
      ${Math.min(launchesPage * LAUNCHES_LIMIT, totalItems)}
      de ${totalItems} lançamentos
    </div>
  `;

  return html;
}

const LAUNCH_TYPE_LABELS = {
  deposito_caixinha: "dep. caixinha",
  saque_caixinha: "saque caixinha",
  aporte_investimento: "aporte invest.",
  resgate_investimento: "resgate invest.",
  transferencia_interna: "transf. interna",
  pagamento_fatura: "pgto. fatura",
  ajuste_saldo: "ajuste saldo",
  criar_caixinha: "criar caixinha",
  create_investment: "criar invest.",
  delete_pocket: "remover caixinha",
  delete_investment: "remover invest.",
  credito: "crédito",
};
// Guarda os lançamentos renderizados pra o clique na linha abrir o detalhe.
let _renderedLaunches = [];

// Guarda o detalhamento do "Sobrou este mês" pro modal explicativo (clique no card).
let _sobrouDetail = null;
// Elemento que tinha o foco antes de abrir o modal (pra restaurar ao fechar).
let _sobrouReturnFocus = null;

function renderLaunches() {
  if (!lastData) return;

  const items = lastData.recent_launches || [];
  _renderedLaunches = items;

  const card = document.getElementById("launches-card");

  if (!items.length) {
    card.innerHTML = '<div class="empty">Nenhum lançamento encontrado.</div>';
    return;
  }

  const meta = lastData.launches_pagination || {
    page: launchesPage,
    limit: LAUNCHES_LIMIT,
    total: items.length,
    total_pages: 1
  };

  launchesPage = meta.page || 1;

	  const TYPE_LABELS = LAUNCH_TYPE_LABELS;

		  card.innerHTML =
		    items.map((l, idx) => {
      const isInternal = l.is_internal_movement;
      const valClass   = isInternal ? '' : (l.tipo==='receita'||l.tipo==='entrada' ? 'g' : 'r');
      const valStyle   = isInternal ? 'color:var(--text-2)' : '';
      const typeLabel  = TYPE_LABELS[l.tipo] || l.tipo.replaceAll("_", " ");
      // Editar/Excluir migraram pro modal de detalhe (clique na linha) — sem
      // ícones inline, que causavam toque errado no celular.
      return `
      <div class="row" style="cursor:pointer;${isInternal?'opacity:.75':''}" onclick="openLaunchDetail(${idx})">
        <span class="lbl">
	          <span class="tag ${l.tipo}">${typeLabel}</span>
	          ${isInternal ? '<span class="tag interno">mov. interna</span>' : ''}
	          ${escapeHtmlSafe(describeLaunch(l))}
	          ${l.categoria ? `<span class="tag x">${escapeHtmlSafe(l.categoria)}</span>` : ''}
	          ${_billMonthTag(l)}
	        </span>
        <span style="display:flex;flex-direction:column;align-items:flex-end;gap:2px">
          <span class="val ${valClass}" style="${valStyle}"
                data-num="lnc_${l.criado_em}_${l.valor}" data-val="${l.valor}">${fmt(l.valor)}</span>
          <span style="font-size:.65rem;color:var(--text-3)">${fmtLaunchWhen(l)}</span>
        </span>
      </div>
    `}).join("")
    + renderLaunchesPagination(meta.total || items.length, meta.total_pages || 1);
}

// Clique numa linha (Visão Geral OU Histórico): abre o detalhe com a descrição
// COMPLETA + campos principais e ações Editar/Excluir. Modal dedicado com
// altura mínima e respiro. Edit/delete roteiam por tipo (crédito vs launch),
// então funcionam nas duas origens sem colisão de id.
let _launchDetailCurrent = null;
let _launchDetailSource = "overview";   // 'overview' | 'history'
let _editDeleteReturnTo = null;         // 'history' → recarrega o histórico após a ação

function _ensureLaunchDetailModal() {
  if (document.getElementById("launch-detail-overlay")) return;
  const html = `
    <div class="overlay" id="launch-detail-overlay">
      <div class="modal launch-detail">
        <h3>Detalhe do lançamento</h3>
        <div class="ld-desc" id="ld-desc"></div>
        <div class="ld-meta" id="ld-meta"></div>
        <div class="modal-acts ld-acts">
          <button type="button" class="ld-del" id="ld-delete"><i class="ph ph-trash" aria-hidden="true"></i> Excluir</button>
          <span class="ld-acts-right">
            <button type="button" class="btn-cancel" id="ld-edit"><i class="ph ph-pencil-simple" aria-hidden="true"></i> Editar</button>
            <button type="button" class="btn-save" id="ld-close">Fechar</button>
          </span>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML("beforeend", html);
  const ov = document.getElementById("launch-detail-overlay");
  ov.addEventListener("click", e => { if (e.target === ov) closeLaunchDetail(); });
  document.getElementById("ld-close").addEventListener("click", closeLaunchDetail);
  document.getElementById("ld-edit").addEventListener("click", _launchDetailEdit);
  document.getElementById("ld-delete").addEventListener("click", _launchDetailDelete);
  document.addEventListener("keydown", e => {
    if (!ov.classList.contains("open")) return;
    if (e.key === "Escape") { closeLaunchDetail(); return; }
    /* Trap de Tab: sem ele o foco saía do diálogo e chegava no que está ATRÁS do
       overlay. Deixou de ser só cosmético quando a linha da Distribuição virou
       `role=button tabindex=0`: o Tab parava nela e o Enter abria a lista de
       lançamentos POR CIMA do detalhe — dois overlays no mesmo z-index, que é a
       invariante que esta feature inteira depende. Helper compartilhado do
       modals.js (`window.pigTrapTab`), não a quinta cópia do bloco. */
    if (window.pigTrapTab) window.pigTrapTab(e, ov.querySelector(".modal"));
  });
}

function closeLaunchDetail() {
  const ov = document.getElementById("launch-detail-overlay");
  if (ov) ov.classList.remove("open");
  // Veio da lista de uma categoria: o detalhe ESCONDEU a lista pra não empilhar
  // dois .overlay (mesmo z-index, e cada ESC document-level fecha o seu).
  if (_launchDetailSource === "category" && _catLaunchesCtx) {
    _launchDetailSource = "overview";
    // Reexibe o que já está na tela (páginas do "Carregar mais", scroll, cursor)
    // e NÃO pede nada ao servidor. Refetch só quando não sobrou lista montada.
    if (!_showCategoryLaunches()) openCategoryLaunches(_catLaunchesCtx.nome, _catLaunchesCtx);
  }
}

function _renderLaunchDetail(l) {
  _ensureLaunchDetailModal();
  const typeLabel = LAUNCH_TYPE_LABELS[l.tipo] || String(l.tipo || "").replaceAll("_", " ");
  const desc = describeLaunch(l).replace(/<[^>]+>/g, "").trim() || "—";
  document.getElementById("ld-desc").textContent = desc;

  const rows = [["Valor", fmt(l.valor)], ["Tipo", typeLabel]];
  if (l.categoria) rows.push(["Categoria", l.categoria]);
  if (l.is_internal_movement) rows.push(["Movimentação", "interna"]);
  rows.push(["Data", fmtLaunchWhen(l)]);
  document.getElementById("ld-meta").innerHTML = rows.map(([k, v]) =>
    `<div class="ld-row"><span class="ld-k">${escapeHtmlSafe(k)}</span>` +
    `<span class="ld-v">${escapeHtmlSafe(String(v))}</span></div>`
  ).join("");

  // Editar/Excluir só com id e fora de movimentação interna (que não tem edição).
  const editable = l.id != null && !l.is_internal_movement;
  document.getElementById("ld-edit").style.display = editable ? "" : "none";
  document.getElementById("ld-delete").style.display = editable ? "" : "none";

  document.getElementById("launch-detail-overlay").classList.add("open");
}

function openLaunchDetail(idx) {
  const l = (_renderedLaunches || [])[idx];
  if (!l) return;
  _launchDetailCurrent = l;
  _launchDetailSource = "overview";
  _renderLaunchDetail(l);
}

/* ═══════════════════════════════════════════════════════════════════════
   "SOBROU ESTE MÊS" — detalhamento (clique no card da Visão Geral)
   Mostra a conta (receitas − gastos − aportes = sobrou) e explica por que
   isso ≠ saldo: o saldo é acumulado (arrasta meses, ajustes e movimentações)
   e pode estar negativo mesmo num mês que sobrou.
═══════════════════════════════════════════════════════════════════════ */
function _ensureSobrouDetailModal() {
  if (document.getElementById("sobrou-detail-overlay")) return;
  const html = `
    <div class="overlay" id="sobrou-detail-overlay">
      <div class="modal launch-detail sobrou-detail" role="dialog" aria-modal="true" aria-labelledby="sd-title">
        <h3 id="sd-title">Sobrou este mês</h3>
        <div class="msub" id="sd-sub"></div>
        <div class="ld-meta" id="sd-rows"></div>
        <div class="sd-note" id="sd-note"></div>
        <div class="modal-acts">
          <button type="button" class="btn-save" id="sd-close">Entendi</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML("beforeend", html);
  const ov = document.getElementById("sobrou-detail-overlay");
  ov.addEventListener("click", e => { if (e.target === ov) closeSobrouDetail(); });
  document.getElementById("sd-close").addEventListener("click", closeSobrouDetail);
  document.addEventListener("keydown", e => {
    if (!ov.classList.contains("open")) return;
    if (e.key === "Escape") { closeSobrouDetail(); return; }
    // Trap de foco: o diálogo tem um único controle (Fechar). Sem isso o Tab
    // vazaria pro dashboard atrás do overlay, contrariando aria-modal.
    if (e.key === "Tab") {
      e.preventDefault();
      const closeBtn = document.getElementById("sd-close");
      if (closeBtn) closeBtn.focus();
    }
  });
}

function closeSobrouDetail() {
  const ov = document.getElementById("sobrou-detail-overlay");
  if (ov) ov.classList.remove("open");
  // Devolve o foco pra quem abriu o modal (o card), pra teclado/leitor de tela
  // não ficarem perdidos atrás do overlay.
  if (_sobrouReturnFocus && typeof _sobrouReturnFocus.focus === "function") {
    _sobrouReturnFocus.focus();
  }
  _sobrouReturnFocus = null;
}

function openSobrouDetail() {
  const s = _sobrouDetail;
  if (!s) return;
  _ensureSobrouDetailModal();
  const deficit = s.sav < 0;
  const monthLbl = (PT_MONTHS[(s.month || 1) - 1] || "") +
    (s.year ? "/" + String(s.year).slice(-2) : "");

  document.getElementById("sd-title").textContent = deficit ? "Déficit do mês" : "Sobrou este mês";
  document.getElementById("sd-sub").textContent =
    "É o fluxo de " + monthLbl + ": o que entrou menos o que saiu no mês. Não é o seu saldo.";

  const row = (k, v, cls) =>
    `<div class="ld-row"><span class="ld-k">${escapeHtmlSafe(k)}</span>` +
    `<span class="ld-v ${cls || ""}">${v}</span></div>`;

  document.getElementById("sd-rows").innerHTML =
    row("Receitas do mês", "+ " + fmt(s.inc), "sd-plus") +
    row("Gastos do mês", "− " + fmt(s.exp), "sd-minus") +
    row("Aportes (investimentos + caixinhas)", "− " + fmt(s.apt), "sd-minus") +
    `<div class="ld-row sd-total"><span class="ld-k">${deficit ? "Déficit do mês" : "Sobrou este mês"}</span>` +
    `<span class="ld-v ${deficit ? "neg" : "pos"}">${fmt(s.sav)}</span></div>`;

  // Explica a divergência que confunde: saldo (acumulado) vs sobrou (só o mês).
  // Em mês histórico NÃO comparamos com o saldo: o snapshot só traz o saldo
  // ATUAL da conta (não o saldo daquele mês) e ainda exclui Open Finance, então
  // afirmar "seu saldo está negativo" ali seria enganoso. Mostra só a natureza
  // do número (fluxo daquele mês).
  let note;
  if (s.hist) {
    note = "Este é o fluxo de " + monthLbl + ": só receitas, gastos e aportes daquele mês. " +
      "Não é um saldo: o saldo é acumulado e reflete o momento atual, não o fim de um mês passado.";
  } else if (s.saldoAtual < 0) {
    note = "Seu <b>saldo</b> está negativo (" + fmt(s.saldoAtual) + "), mas ainda assim " +
      (deficit ? "o mês fechou como está acima" : "sobrou dinheiro <b>neste mês</b>") + ". " +
      "Não é contradição: o saldo é acumulado, arrasta meses anteriores, ajustes e movimentações entre contas, " +
      "enquanto este valor olha só receitas, gastos e aportes de " + monthLbl + ".";
  } else {
    note = "O <b>saldo</b> é acumulado (arrasta meses anteriores, ajustes e movimentações entre contas). " +
      "Este valor considera só receitas, gastos e aportes de " + monthLbl + ". Por isso os dois podem divergir.";
  }
  if (s.apt > 0) {
    note += " Aportes não são gasto: viram patrimônio seu (investimentos e caixinhas), mas saem do que “sobra livre” no mês.";
  }
  document.getElementById("sd-note").innerHTML = note;

  // Guarda quem tinha o foco (o card) pra restaurar ao fechar, e joga o foco
  // pro botão de fechar — assim o leitor de tela anuncia o diálogo e o Tab não
  // vaza pro dashboard atrás do overlay.
  _sobrouReturnFocus = document.activeElement;
  document.getElementById("sobrou-detail-overlay").classList.add("open");
  const closeBtn = document.getElementById("sd-close");
  if (closeBtn) closeBtn.focus();
}

function openHistoryDetail(idx) {
  const l = (_renderedHistoryItems || [])[idx];
  if (!l) return;
  _launchDetailCurrent = l;
  _launchDetailSource = "history";
  _renderLaunchDetail(l);
}

function _launchDetailEdit() {
  const l = _launchDetailCurrent;
  if (!l || l.id == null) return;
  _editDeleteReturnTo = (_launchDetailSource === "history") ? "history" : null;
  // Sai da lista da categoria PRA VALER: quem fecha o editor/confirmação volta
  // pro dashboard, não pra lista. Zerar o ctx (em vez de mexer no
  // _launchDetailSource) é o que impede closeLaunchDetail de reabri-la por
  // cima E o que evita o ctx + foco guardado ficarem de pé pra sempre.
  _forgetCategoryLaunches(false);
  closeLaunchDetail();
  openEditLaunchModal(l.id, l);
}

function _launchDetailDelete() {
  const l = _launchDetailCurrent;
  if (!l || l.id == null) return;
  _editDeleteReturnTo = (_launchDetailSource === "history") ? "history" : null;
  // Sai da lista da categoria PRA VALER: quem fecha o editor/confirmação volta
  // pro dashboard, não pra lista. Zerar o ctx (em vez de mexer no
  // _launchDetailSource) é o que impede closeLaunchDetail de reabri-la por
  // cima E o que evita o ctx + foco guardado ficarem de pé pra sempre.
  _forgetCategoryLaunches(false);
  closeLaunchDetail();
  const descTxt = describeLaunch(l).replace(/<[^>]+>/g, "").trim();
  confirmDeleteLaunch(l.id, descTxt, l.valor, l.tipo === "credito", l.installments_total || null);
}

/* ═══════════════════════════════════════════════════════════════════════
   BUDGET MODAL
═══════════════════════════════════════════════════════════════════════ */
function openBudget(cat) {
  bgtTarget = cat;
  document.getElementById("bgt-cat-label").textContent = "Categoria: " + cat;
  const cur = lastData && lastData.budgets && lastData.budgets[cat];
  document.getElementById("bgt-input").value = cur ? cur.toFixed(2) : "";
  document.getElementById("bgt-overlay").classList.add("open");
  setTimeout(() => document.getElementById("bgt-input").focus(), 50);
}
function closeBudget() {
  document.getElementById("bgt-overlay").classList.remove("open");
  bgtTarget = null;
}
async function saveBudget() {
  if (!bgtTarget) return;
  const val = parseFloat(document.getElementById("bgt-input").value);
  if (!val || val <= 0) { await alertModal("Digite um valor maior que zero.", { title: "Valor inválido" }); return; }
  try {
    const r = await fetch(`${API}/budgets/${USER_ID}`, {
      method:"POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type":"application/json" }),
      body: JSON.stringify({categoria: bgtTarget, budget: val})
    });
    if (!r.ok) throw new Error(await r.text());
    closeBudget(); sendRefresh(); showToast("✓ Orçamento salvo");
  } catch(err) { await alertModal(err.message, { title: "Erro" }); }
}
document.getElementById("bgt-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closeBudget();
});
document.getElementById("investment-detail-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closeInvestmentDetail();
});
document.getElementById("investment-help-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closeInvestmentHelp();
});
document.getElementById("bgt-input").addEventListener("keydown", e => {
  if (e.key === "Enter") saveBudget();
  if (e.key === "Escape") closeBudget();
});
/* Condicionado a cada overlay, um por um. Antes fechava os três SEMPRE — e
   fechar o que já está fechado não é inofensivo aqui: o Esc de um diálogo
   aberto POR CIMA levava junto os de baixo. Irmão do listener das faturas
   (issue #76); a enumeração dos 8 keydown globais deste arquivo achou este,
   que a issue não listava. */
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  _fechaSeAberto("investment-detail-overlay", closeInvestmentDetail);
  _fechaSeAberto("investment-help-overlay", closeInvestmentHelp);
  _fechaSeAberto("launch-overlay", closeLaunchModal);
});

/* ═══════════════════════════════════════════════════════════════════════
   LAUNCH MODAL — registra receita/despesa pelo dashboard
═══════════════════════════════════════════════════════════════════════ */
const LAUNCH_CATEGORIES = {
  despesa: [
    "alimentação","mercado","transporte","moradia","saúde","educação",
    "lazer","assinaturas","compras online","pets","beleza","outros",
  ],
  receita: ["salário","freela","reembolso","rendimentos","outros"],
  credito: [
    "alimentação","mercado","transporte","moradia","saúde","educação",
    "lazer","assinaturas","compras online","pets","beleza","outros",
  ],
};

let launchTipo = "despesa";
let launchSubmitting = false;
// Quando parcelado, o "Valor (R$)" digitado pode ser o da PARCELA ("12x de
// 79,90", já com juros embutido) ou o TOTAL financiado. Default = parcela,
// que é o número que o app/maquininha mostra — poupa a conta na mão.
let launchValorMode = "parcela";

function setLaunchTipo(tipo) {
  if (!["receita","despesa","credito"].includes(tipo)) return;
  launchTipo = tipo;
  document.querySelectorAll(".tipo-opt").forEach(el => {
    const isActive = el.dataset.tipo === tipo;
    el.classList.toggle("active", isActive);
    el.setAttribute("aria-checked", String(isActive));
  });
  // Mostra/esconde linha do cartão e parcelas — só fazem sentido pra crédito
  const cardRow = document.getElementById("launch-card-row");
  const parcRow = document.getElementById("launch-parcelas-row");
  if (tipo === "credito") {
    cardRow.style.display = "";
    parcRow.style.display = "";
    populateLaunchCards();
  } else {
    cardRow.style.display = "none";
    parcRow.style.display = "none";
    document.getElementById("launch-parcelas").value = "1";
  }
  syncParcUI();
  populateLaunchCategories();
}

/* ─── Parcelamento: toggle parcela/total + preview ao vivo ──────────────── */
function setValorMode(mode) {
  if (!["parcela","total"].includes(mode)) return;
  launchValorMode = mode;
  document.querySelectorAll("#launch-valor-mode-row .vmode-opt").forEach(el => {
    const on = el.dataset.vmode === mode;
    el.classList.toggle("active", on);
    el.setAttribute("aria-checked", String(on));
  });
  updateParcPreview();
}

function _launchParcCount() {
  const parc = parseInt(document.getElementById("launch-parcelas").value || "1", 10);
  return Number.isFinite(parc) ? parc : 1;
}

// Visibilidade do toggle (só crédito parcelado) + atualiza o preview.
function syncParcUI() {
  const row = document.getElementById("launch-valor-mode-row");
  if (!row) return;
  const show = launchTipo === "credito" && _launchParcCount() > 1;
  row.style.display = show ? "" : "none";
  updateParcPreview();
}

// "12× de R$ 79,90 = total R$ 958,80" — deriva o lado que falta a partir do modo.
function updateParcPreview() {
  const el = document.getElementById("launch-parc-preview");
  if (!el) return;
  const parc = _launchParcCount();
  const raw = (document.getElementById("launch-valor").value || "").replace(",", ".");
  const v = parseFloat(raw);
  if (launchTipo !== "credito" || parc <= 1 || !v || isNaN(v) || v <= 0) {
    el.classList.add("empty");
    el.textContent = "";
    return;
  }
  let parcela, total;
  if (launchValorMode === "parcela") {
    parcela = v;
    total = Math.round(v * parc * 100) / 100;
  } else {
    total = v;
    parcela = Math.round((v / parc) * 100) / 100;
  }
  el.classList.remove("empty");
  el.innerHTML = `${parc}× de <strong>${fmt(parcela)}</strong> = total <strong>${fmt(total)}</strong>`;
}

function populateLaunchCards() {
  const sel = document.getElementById("launch-card");
  const previous = sel.value;
  const cards = (lastData && lastData.credit_cards) || [];
  if (cards.length === 0) {
    sel.innerHTML = '<option value="">— Nenhum cartão cadastrado —</option>';
    return;
  }
  sel.innerHTML = '<option value="">— Selecione um cartão —</option>' +
    cards.map(c => `<option value="${c.id}">${c.name}</option>`).join("");
  if (previous && [...sel.options].some(o => o.value === previous)) {
    sel.value = previous;
  } else if (cards.length === 1) {
    sel.value = String(cards[0].id);
  }
}

function populateLaunchCategories() {
  const sel = document.getElementById("launch-categoria");
  const previous = sel.value;
  const list = LAUNCH_CATEGORIES[launchTipo] || ["outros"];
  sel.innerHTML = '<option value="">— Detectar automaticamente —</option>' +
    list.map(c => `<option value="${c}">${c}</option>`).join("");
  if (previous && [...sel.options].some(o => o.value === previous)) {
    sel.value = previous;
  }
}

function openLaunchModal(opts) {
  opts = opts || {};
  setValorMode("parcela");           // reseta o toggle pro default a cada abertura
  setLaunchTipo(opts.tipo || "despesa");
  document.getElementById("launch-valor").value = opts.valor || "";
  document.getElementById("launch-alvo").value = opts.alvo || "";
  document.getElementById("launch-nota").value = opts.nota || "";
  document.getElementById("launch-categoria").value = opts.categoria || "";
  if (opts.card_id) document.getElementById("launch-card").value = String(opts.card_id);
  if (opts.parcelas) document.getElementById("launch-parcelas").value = String(opts.parcelas);
  syncParcUI();                       // recalcula visibilidade + preview com os valores já setados
  hideLaunchError();
  document.getElementById("launch-overlay").classList.add("open");
  setTimeout(() => document.getElementById("launch-valor").focus(), 50);
}

function closeLaunchModal() {
  document.getElementById("launch-overlay").classList.remove("open");
}

/* ─── Editar lançamento (categoria + descrição) ────────────────────── */
const EDIT_LAUNCH_CATEGORIES = [
  "alimentação","mercado","transporte","saúde","moradia","lazer","educação",
  "assinaturas","pets","compras online","beleza",
  "investimento_aporte","criptomoedas","rendimentos","outros",
];
const EDIT_LAUNCH_CUSTOM_VALUE = "__custom__";
let editingLaunchId = null;
let editLaunchSubmitting = false;

/* Lançamento SEM categoria (o que a barra "sem categoria" do donut abre) ganha
   uma opção própria, value "" — e `submitEditLaunch` OMITE `categoria` do PATCH
   quando ela é a escolhida. Sem isso, um `<select>` sem valor casado cai na
   PRIMEIRA opção ("alimentação"): editar só a nota ou a data de um lançamento
   sem categoria gravava "alimentação" (ou, quando o SQL fabricava o rótulo,
   "outros") numa transação que o usuário nunca categorizou. */
function _renderEditCategoriaOptions(currentCategoria) {
  const sel = document.getElementById("edit-launch-categoria");
  const opts = [];
  // Só aparece pra quem JÁ está sem categoria: esta tela não tem "descategorizar"
  // (a rota recusa categoria vazia — finance_bot_websocket_custom.py).
  if (!currentCategoria) {
    opts.push(`<option value="">— sem categoria —</option>`);
  }
  // Se a categoria atual é "custom" (não está na lista canônica), aparece no topo
  // como opção pré-selecionada, pra não perder o valor existente.
  const isCustomCurrent = currentCategoria
    && !EDIT_LAUNCH_CATEGORIES.includes(currentCategoria);
  if (isCustomCurrent) {
    opts.push(`<option value="${currentCategoria}">${currentCategoria}</option>`);
  }
  for (const c of EDIT_LAUNCH_CATEGORIES) {
    opts.push(`<option value="${c}">${c}</option>`);
  }
  opts.push(`<option value="${EDIT_LAUNCH_CUSTOM_VALUE}"> Outra (digitar)…</option>`);
  sel.innerHTML = opts.join("");
  sel.value = currentCategoria || "";
}

function _onEditCategoriaChange() {
  const sel = document.getElementById("edit-launch-categoria");
  const row = document.getElementById("edit-launch-categoria-custom-row");
  const inp = document.getElementById("edit-launch-categoria-custom");
  if (sel.value === EDIT_LAUNCH_CUSTOM_VALUE) {
    row.style.display = "";
    inp.value = "";
    setTimeout(() => inp.focus(), 30);
  } else {
    row.style.display = "none";
    inp.value = "";
  }
}

let editingLaunchIsCredit = false;
let editingLaunchOriginal = { categoria: "", nota: "", data: "" };

// ISO instant → "YYYY-MM-DDTHH:MM" no fuso local do navegador (formato do
// input datetime-local). Espelha o que o fmtDate mostra na lista.
function toLocalDatetimeInput(iso) {
  // Renderiza o campo datetime-local na hora de PAREDE de APP_TZ (não do
  // device), pra bater com o que fmtDate exibe. Sem isso, no WebView UTC do
  // iOS o campo mostrava 3h a mais que o resumo.
  const d = _isoToDate(iso);
  if (!d) return "";
  const p = _wallPartsInTZ(d, APP_TZ);
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

function openEditLaunchModal(launchId, launchObj = null) {
  if (!launchId) return;
  // launchObj vem do modal de detalhe (inclui itens do Histórico, que não
  // estão em recent_launches). Fallback: procura no snapshot da Visão Geral.
  const launch = launchObj || (lastData?.recent_launches || []).find(l => l.id === launchId);
  if (!launch) return;

  editingLaunchId = launchId;
  editingLaunchIsCredit = (launch.tipo === 'credito');
  const valStr = fmt(launch.valor);
  const prefix = editingLaunchIsCredit ? "compra crédito" : launch.tipo;
  document.getElementById("edit-launch-summary").textContent =
    `${prefix} • ${valStr} • ${fmtLaunchWhen(launch)}`;

  _renderEditCategoriaOptions(launch.categoria);
  document.getElementById("edit-launch-categoria-custom-row").style.display = "none";
  document.getElementById("edit-launch-categoria-custom").value = "";
  // Pra crédito, `alvo` é o nome do cartão — não faz sentido pré-preencher
  // como descrição. Usa só `nota` (que tem a descrição da compra).
  document.getElementById("edit-launch-nota").value = editingLaunchIsCredit
    ? (launch.nota || "")
    : (launch.nota || launch.alvo || "");

  // Data — só editável em lançamentos normais. Crédito não muda data aqui
  // (alteraria a janela de fechamento da fatura).
  // ponytail: o campo também é INÚTIL numa linha do Open Finance (a data é do
  // provedor e a rota devolve 409, db/accounts.py) — mas ele continua visível,
  // e o usuário só descobre ao salvar. Desabilitar exigiria `source` nos TRÊS
  // payloads que abrem este modal (Visão Geral, Histórico e detalhe de
  // categoria), e nenhum deles traz a coluna hoje. Fazer quando alguém
  // reclamar, ou junto do próximo PR que já mexa nessas queries.
  const dataRow = document.getElementById("edit-launch-data-row");
  const dataInp = document.getElementById("edit-launch-data");
  if (editingLaunchIsCredit) {
    dataRow.style.display = "none";
    dataInp.value = "";
  } else {
    dataRow.style.display = "";
    // Sem hora confiável quem manda no DIA é o `posted_at` — a MESMA regra do
    // `fmtLaunchWhen` (dashboard.js:485), que escreve o resumo três linhas
    // acima nesta caixa. Sem isto o modal se contradizia sozinho: cabeçalho
    // "10/03" e campo "09/03, 21:00" numa linha do Open Finance legado, cujo
    // `criado_em` é meia-noite UTC (medido em 390x844). 12:00 é a hora que os
    // importadores gravam quando o dia vem sem hora (statement_import.py:666).
    dataInp.value = (launch.has_time === false && launch.posted_at)
      ? `${String(launch.posted_at).slice(0, 10)}T12:00`
      : (launch.criado_em ? toLocalDatetimeInput(launch.criado_em) : "");
  }

  // Estado ORIGINAL do formulário, lido do próprio DOM depois do preenchimento
  // (e não do objeto `launch`): é exatamente o que o usuário está vendo, então
  // a comparação no submit não depende da regra de preenchimento acima.
  // A data é comparada como STRING do input (`YYYY-MM-DDTHH:MM`): o
  // datetime-local é de minuto, e comparar instante cru marcaria como
  // "mudou" todo lançamento com segundos != 0.
  editingLaunchOriginal = {
    categoria: document.getElementById("edit-launch-categoria").value,
    nota: document.getElementById("edit-launch-nota").value.trim(),
    data: dataInp.value,
  };

  hideEditLaunchError();
  document.getElementById("edit-launch-overlay").classList.add("open");
}

function closeEditLaunchModal() {
  document.getElementById("edit-launch-overlay").classList.remove("open");
  editingLaunchId = null;
  _editDeleteReturnTo = null;  // cancelou → não recarrega o histórico
}

function hideEditLaunchError() {
  const el = document.getElementById("edit-launch-error");
  el.classList.remove("show");
  el.textContent = "";
}
function showEditLaunchError(msg) {
  const el = document.getElementById("edit-launch-error");
  el.textContent = msg;
  el.classList.add("show");
}

async function submitEditLaunch() {
  if (editLaunchSubmitting || !editingLaunchId) return;
  hideEditLaunchError();
  const _returnToHistory = (_editDeleteReturnTo === "history");

  let categoria = document.getElementById("edit-launch-categoria").value;
  if (categoria === EDIT_LAUNCH_CUSTOM_VALUE) {
    categoria = document.getElementById("edit-launch-categoria-custom").value.trim();
    if (!categoria) {
      showEditLaunchError("Digite a categoria personalizada.");
      return;
    }
  }
  // categoria === "" só existe na opção "— sem categoria —", e só num lançamento
  // que JÁ está sem categoria (`_renderEditCategoriaOptions`). Nesse caso o PATCH
  // vai sem a chave `categoria` e a rota não toca na coluna — salvar a nota ou a
  // data não pode inventar categoria pra transação de ninguém. Mesma disciplina
  // vale agora pros outros dois campos (ver `notaMudou`/`criadoEmISO` abaixo).

  const nota = document.getElementById("edit-launch-nota").value.trim();

  // O PATCH carrega SÓ o campo que o usuário mexeu. Reenviar um valor igual ao
  // que já está no banco não é inócuo: `criado_em` reescreve `posted_at`
  // (db/accounts.py) e, numa linha importada sem hora confiável, o `posted_at`
  // é o único campo certo — editar só a descrição jogava o dia pra trás.
  const notaMudou = nota !== editingLaunchOriginal.nota;
  const categoriaMudou = !!categoria && categoria !== editingLaunchOriginal.categoria;

  // Data — só pra lançamentos normais. Converte o wall-clock local do input
  // pra um instante ISO (com fuso) que o backend grava direto.
  let criadoEmISO = null;
  if (!editingLaunchIsCredit) {
    const dataVal = document.getElementById("edit-launch-data").value;
    if (dataVal && dataVal !== editingLaunchOriginal.data) {
      // O input é hora de parede em APP_TZ (mesmo fuso do display/edição).
      // Converte pro instante UTC correto — não usa new Date(dataVal), que
      // interpretaria no fuso do device (UTC no WebView iOS) e deslocaria 3h.
      criadoEmISO = appTzWallClockToISO(dataVal);
      if (!criadoEmISO) { showEditLaunchError("Data inválida."); return; }
    }
  }

  if (!notaMudou && !categoriaMudou && !criadoEmISO) {
    // Nada mudou: PATCH nenhum. Fechar é o feedback honesto — um toast de
    // "atualizado" afirmaria uma escrita que não aconteceu.
    closeEditLaunchModal();
    return;
  }

  const btn = document.getElementById("edit-launch-submit-btn");
  editLaunchSubmitting = true;
  btn.disabled = true;
  try {
    const url = editingLaunchIsCredit
      ? `${API}/credit-transactions/${USER_ID}/${editingLaunchId}`
      : `${API}/launches/${USER_ID}/${editingLaunchId}`;
    const reqBody = {};
    if (notaMudou) reqBody.nota = nota;
    if (categoriaMudou) reqBody.categoria = categoria;
    if (criadoEmISO) reqBody.criado_em = criadoEmISO;
    const r = await fetch(url, {
      method: "PATCH",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(reqBody),
    });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${r.status}`);
    }
    // Update otimista — atualiza o item local imediatamente pra UI não
    // ficar travada esperando o roundtrip do refresh. Pra crédito parcelado,
    // tambem propaga pra outras linhas com mesmo nota+alvo+valor+installments_
    // total (heurística: refresh corrige caso erre).
    const items = lastData?.recent_launches || [];
    const target = items.find(l => l.id === editingLaunchId);
    if (target) {
      const oldNota = target.nota;
      const oldAlvo = target.alvo;
      const oldTotal = target.installments_total;
      // `categoria` vazia = não foi no PATCH (ver acima): o otimista não pode
      // fingir uma escrita que o servidor não fez.
      if (categoriaMudou) target.categoria = categoria;
      if (notaMudou) target.nota = nota;
      if (criadoEmISO) target.criado_em = criadoEmISO;
      if (editingLaunchIsCredit && oldTotal && oldTotal > 1) {
        for (const l of items) {
          if (l !== target && l.tipo === "credito"
              && l.alvo === oldAlvo
              && l.nota === oldNota
              && l.installments_total === oldTotal) {
            if (categoriaMudou) l.categoria = categoria;
            if (notaMudou) l.nota = nota;
          }
        }
      }
      renderLaunches();
    }
    closeEditLaunchModal();
    showLaunchSuccessToast(editingLaunchIsCredit ? "Compra atualizada" : "Lançamento atualizado");
    sendRefreshSilent();
    // Veio do Histórico → recarrega a timeline resetando a paginação (senão,
    // se o usuário tinha dado "Carregar mais", recarregaria só a página N).
    if (_returnToHistory) _historyResetAndReload();
  } catch (err) {
    showEditLaunchError("Erro: " + err.message);
  } finally {
    editLaunchSubmitting = false;
    btn.disabled = false;
  }
}

document.getElementById("edit-launch-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closeEditLaunchModal();
});

let deleteLaunchInFlight = false;
async function confirmDeleteLaunch(launchId, descricao, valor, isCredit = false, installmentsTotal = null) {
  if (deleteLaunchInFlight) return;
  if (!launchId) return;
  const _returnToHistory = (_editDeleteReturnTo === "history");
  _editDeleteReturnTo = null;  // consome o flag (independe do usuário confirmar)
  const valFmt = (typeof valor === "number") ? fmt(valor) : "";
  const desc   = (descricao || "").trim() || "este lançamento";

  const isInstallment = isCredit && installmentsTotal && installmentsTotal > 1;
  const body = isCredit
    ? (isInstallment
        ? `${desc}${valFmt ? ` · ${valFmt}` : ""}\n\nEsta compra faz parte de um parcelamento em ${installmentsTotal}x. **TODAS as ${installmentsTotal} parcelas** serão apagadas. Essa ação não pode ser desfeita.`
        : `${desc}${valFmt ? ` · ${valFmt}` : ""}\n\nA compra será removida da fatura. Essa ação não pode ser desfeita.`)
    : `${desc}${valFmt ? ` · ${valFmt}` : ""}\n\nO efeito no saldo (e em caixinhas/investimentos, se houver) será revertido. Essa ação não pode ser desfeita.`;

  const ok = await confirmModal(body, {
    title: isCredit ? "Apagar compra no crédito" : "Apagar lançamento",
    confirmText: "Apagar",
    destructive: true,
  });
  if (!ok) return;

  deleteLaunchInFlight = true;
  try {
    const url = isCredit
      ? `${API}/credit-transactions/${USER_ID}/${launchId}`
      : `${API}/launches/${USER_ID}/${launchId}`;
    const r = await fetch(url, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders(),
    });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${r.status}`);
    }
    let msg = "Lançamento apagado";
    if (isCredit) {
      const data = await r.json().catch(() => ({}));
      msg = data.mode === "group" && data.removed_count > 1
        ? `Parcelamento apagado (${data.removed_count} parcelas)`
        : "Compra apagada";
    }
    // Update otimista — remove o item local imediatamente. Pra parcelado,
    // remove todas as linhas do grupo (heurística: mesmo alvo+nota+
    // installments_total). O refresh silencioso corrige se errou.
    const items = lastData?.recent_launches || [];
    const target = items.find(l => l.id === launchId);
    let removeIds = new Set([launchId]);
    if (isCredit && installmentsTotal && installmentsTotal > 1 && target) {
      for (const l of items) {
        if (l.tipo === "credito"
            && l.alvo === target.alvo
            && l.nota === target.nota
            && l.installments_total === installmentsTotal) {
          removeIds.add(l.id);
        }
      }
    }
    lastData.recent_launches = items.filter(l => !removeIds.has(l.id));
    renderLaunches();
    showLaunchSuccessToast(msg);
    sendRefreshSilent();
    // Veio do Histórico → recarrega resetando a paginação (ver edição acima).
    if (_returnToHistory) _historyResetAndReload();
  } catch (err) {
    await alertModal(err.message, { title: "Erro ao apagar" });
  } finally {
    deleteLaunchInFlight = false;
  }
}

function hideLaunchError() {
  const el = document.getElementById("launch-error");
  el.classList.remove("show");
  el.textContent = "";
}
function showLaunchError(msg) {
  const el = document.getElementById("launch-error");
  el.textContent = msg;
  el.classList.add("show");
}

function showLaunchSuccessToast(msg, isError = false) {
  const t = document.getElementById("launch-success-toast");
  if (!isError && /^\s*✓/.test(msg)) {
    t.innerHTML = `<img class="toast-sticker" src="/brand/stickers/ok.webp" alt="" />` +
                  escapeHtmlSafe(msg.replace(/^\s*✓\s*/, ""));
  } else {
    t.textContent = msg;
  }
  t.classList.toggle("error", !!isError);   // vermelho quando erro, verde no sucesso
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2600);
}

async function submitLaunch() {
  if (launchSubmitting) return;
  hideLaunchError();

  const valorRaw = (document.getElementById("launch-valor").value || "").replace(",", ".");
  const valor = parseFloat(valorRaw);
  if (!valor || isNaN(valor) || valor <= 0) {
    showLaunchError("Informe um valor maior que zero.");
    return;
  }

  const alvo = document.getElementById("launch-alvo").value.trim();
  const nota = document.getElementById("launch-nota").value.trim();
  const categoria = document.getElementById("launch-categoria").value;

  let cardId = null;
  let parcelas = null;
  let valorToSend = valor;
  if (launchTipo === "credito") {
    cardId = document.getElementById("launch-card").value;
    if (!cardId) {
      showLaunchError("Selecione um cartão para registrar a compra no crédito.");
      return;
    }
    const parc = parseInt(document.getElementById("launch-parcelas").value || "1", 10);
    if (Number.isFinite(parc) && parc > 1) {
      parcelas = parc;
      // Modo "parcela": o valor digitado é o de cada parcela → total = parcela × N.
      // O endpoint sempre recebe o TOTAL e divide de volta por N (mesma lógica do bot).
      if (launchValorMode === "parcela") {
        valorToSend = Math.round(valor * parc * 100) / 100;
      }
    }
  }

  const btn = document.getElementById("launch-submit-btn");
  launchSubmitting = true;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Registrando...";

  try {
    const res = await fetch(`${API}/launches/${USER_ID}`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        tipo: launchTipo,
        valor: valorToSend,
        alvo: alvo || null,
        nota: nota || null,
        categoria: categoria || null,
        card_id: cardId ? Number(cardId) : null,
        parcelas: parcelas,
      }),
    });
    const data = await readResponsePayload(res);
    if (!res.ok) throw new Error(data.detail || "Erro ao registrar lançamento.");

    closeLaunchModal();
    const tipoLabel = launchTipo === "receita" ? "Receita"
                    : launchTipo === "credito" ? "Compra no crédito"
                    : "Despesa";
    const idLabel = data.launch_id ? `#${data.launch_id}` : "";
    showLaunchSuccessToast(`✓ ${tipoLabel} registrada${idLabel ? " · " + idLabel : ""}`);
    sendRefresh();
  } catch (err) {
    showLaunchError(err.message || "Erro ao registrar lançamento.");
  } finally {
    launchSubmitting = false;
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

document.getElementById("launch-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closeLaunchModal();
});
document.getElementById("launch-valor").addEventListener("keydown", e => {
  if (e.key === "Enter") submitLaunch();
});
document.getElementById("launch-valor").addEventListener("input", updateParcPreview);
document.getElementById("invest-overlay")?.addEventListener("click", e => {
  if (e.target === e.currentTarget) closeInvestmentModal();
});

// ─── Caixinha (criar) ────────────────────────────────────────────────────────
let pocketSubmitting = false;

function syncPocketInterestConfig() {
  const enabled = document.getElementById("pocket-interest-enabled")?.checked ?? true;
  const config = document.getElementById("pocket-interest-config");
  if (config) config.style.display = enabled ? "" : "none";
}

function openPocketModal() {
  document.getElementById("pocket-name").value = "";
  document.getElementById("pocket-description").value = "";
  document.getElementById("pocket-interest-enabled").checked = true;
  document.getElementById("pocket-interest-rate").value = "100";
  syncPocketInterestConfig();
  hidePocketError();
  document.getElementById("pocket-overlay").classList.add("open");
  setTimeout(() => document.getElementById("pocket-name").focus(), 50);
}

function closePocketModal() {
  document.getElementById("pocket-overlay").classList.remove("open");
}

function hidePocketError() {
  const el = document.getElementById("pocket-error");
  el.classList.remove("show");
  el.textContent = "";
}

function showPocketError(msg) {
  const el = document.getElementById("pocket-error");
  el.textContent = msg;
  el.classList.add("show");
}

async function submitPocket() {
  if (pocketSubmitting) return;
  hidePocketError();

  const name = document.getElementById("pocket-name").value.trim();
  const description = document.getElementById("pocket-description").value.trim();
  const interestEnabled = document.getElementById("pocket-interest-enabled").checked;
  const cdiPct = parseFloat((document.getElementById("pocket-interest-rate").value || "100").replace(",", "."));
  if (!name) {
    showPocketError("Informe o nome da caixinha.");
    return;
  }
  if (interestEnabled && (!Number.isFinite(cdiPct) || cdiPct <= 0)) {
    showPocketError("Informe um percentual do CDI maior que zero.");
    return;
  }

  const btn = document.getElementById("pocket-submit-btn");
  pocketSubmitting = true;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Criando...";

  try {
    const res = await fetch(`${API}/pockets/${USER_ID}`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        name,
        description: description || null,
        interest_enabled: interestEnabled,
        interest_rate: interestEnabled ? cdiPct / 100 : 1.0,
      }),
    });
    const data = await readResponsePayload(res);
    if (!res.ok) throw new Error(data.detail || "Erro ao criar caixinha.");

    closePocketModal();
    const created = data.created !== false;
    const canon = (data.pocket && data.pocket.name) || name;
    showLaunchSuccessToast(created ? `✓ Caixinha "${canon}" criada` : `Caixinha "${canon}" já existe`);
    sendRefresh();
  } catch (err) {
    showPocketError(err.message || "Erro ao criar caixinha.");
  } finally {
    pocketSubmitting = false;
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

document.getElementById("pocket-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closePocketModal();
});
document.getElementById("pocket-name").addEventListener("keydown", e => {
  if (e.key === "Enter") submitPocket();
});

// ─── Caixinha (histórico) ────────────────────────────────────────────────────
const PKT_HIST_LABELS = {
  deposito_caixinha: { label: "Depósito",        cls: "dep" },
  saque_caixinha:    { label: "Saque",           cls: "wd"  },
  criar_caixinha:    { label: "Caixinha criada", cls: "crt" },
};

function closePocketHistory() {
  document.getElementById("pocket-history-overlay").classList.remove("open");
  closePocketMove();
  _currentPocketName = null;
}

function openPocketMove(mode) {
  if (!_currentPocketName) return;
  _pocketMoveMode = mode;
  const form = document.getElementById("pkt-move-form");
  const title = document.getElementById("pkt-move-form-title");
  const submitBtn = document.getElementById("pkt-move-submit");
  const isDep = mode === "deposit";
  title.textContent = isDep ? `Depositar em "${_currentPocketName}"` : `Sacar de "${_currentPocketName}"`;
  title.className = "pkt-move-form-title " + (isDep ? "dep" : "wd");
  submitBtn.textContent = isDep ? "Depositar" : "Sacar";
  submitBtn.className = "pkt-move-submit " + (isDep ? "dep" : "wd");
  form.classList.add("show");
  document.getElementById("pkt-move-amount").value = "";
  document.getElementById("pkt-move-nota").value = "";
  setTimeout(() => document.getElementById("pkt-move-amount").focus(), 50);
}

function closePocketMove() {
  const form = document.getElementById("pkt-move-form");
  if (form) form.classList.remove("show");
  _pocketMoveMode = null;
}

async function submitPocketMove(event) {
  event.preventDefault();
  if (!_currentPocketName || !_pocketMoveMode) return;
  const amount = parseFloat(document.getElementById("pkt-move-amount").value);
  if (!Number.isFinite(amount) || amount <= 0) {
    await alertModal("Digite um valor maior que zero.", { title: "Valor inválido" });
    return;
  }
  const nota = document.getElementById("pkt-move-nota").value.trim() || null;
  const submitBtn = document.getElementById("pkt-move-submit");
  const original = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.textContent = "Enviando…";
  try {
    const path = _pocketMoveMode === "deposit" ? "deposit" : "withdraw";
    const res = await fetch(
      `${API}/pockets/${USER_ID}/${encodeURIComponent(_currentPocketName)}/${path}`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ amount, nota }),
      },
    );
    const data = await readResponsePayload(res);
    if (!res.ok) throw new Error(data.detail || "Erro ao movimentar caixinha.");
    showLaunchSuccessToast(_pocketMoveMode === "deposit" ? "✓ Depósito feito" : "✓ Saque feito");
    closePocketMove();
    sendRefresh();
    // Recarrega o histórico no mesmo modal pra refletir o novo movimento
    openPocketHistory(_currentPocketName || data.name);
  } catch (err) {
    await alertModal(err.message || "Erro ao movimentar caixinha.", { title: "Erro" });
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = original;
  }
}

async function pocketWithdrawAll() {
  if (!_currentPocketName) return;
  const ok = await confirmModal(
    `Sacar todo o saldo de "${_currentPocketName}" e zerar a caixinha? O IR/IOF sobre o rendimento é descontado automaticamente.`,
    { title: "Sacar tudo", confirmText: "Sacar tudo" },
  );
  if (!ok) return;
  try {
    const res = await fetch(
      `${API}/pockets/${USER_ID}/${encodeURIComponent(_currentPocketName)}/withdraw`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ withdraw_all: true }),
      },
    );
    const data = await readResponsePayload(res);
    if (!res.ok) throw new Error(data.detail || "Erro ao sacar.");
    const t = data.tax_summary || {};
    const tax = Number(t.ir || 0) + Number(t.iof || 0);
    const msg = tax > 0
      ? `✓ Caixinha zerada · líquido ${fmt(t.net || 0)} (IR/IOF ${fmt(tax)})`
      : `✓ Caixinha zerada · sacado ${fmt(t.gross || 0)}`;
    showLaunchSuccessToast(msg);
    closePocketMove();
    sendRefresh();
    openPocketHistory(_currentPocketName || data.name);
  } catch (err) {
    await alertModal(err.message || "Erro ao sacar.", { title: "Erro" });
  }
}

let _currentPocketName = null;
let _pocketMoveMode = null;
let _currentPocketForEdit = null;

async function openPocketHistory(pocketName) {
  const overlay  = document.getElementById("pocket-history-overlay");
  const titleEl  = document.getElementById("pkt-hist-title");
  const subEl    = document.getElementById("pkt-hist-sub");
  const sumEl    = document.getElementById("pkt-hist-summary");
  const bodyEl   = document.getElementById("pkt-hist-body");
  const actionsEl = document.getElementById("pkt-move-actions");

  _currentPocketName = pocketName;
  _currentPocketForEdit = null;
  closePocketMove();
  titleEl.textContent = `Histórico: ${pocketName}`;
  subEl.textContent   = "Depósitos e saques desta caixinha.";
  sumEl.style.display = "none";
  if (actionsEl) actionsEl.style.display = "none";
  bodyEl.innerHTML    = `<div class="pkt-hist-loading">Carregando…</div>`;
  overlay.classList.add("open");

  try {
    const res = await fetch(`${API}/pockets/${USER_ID}/${encodeURIComponent(pocketName)}/history`, {
      method: "GET",
      credentials: "same-origin",
      headers: csrfHeaders({}),
    });
    const data = await readResponsePayload(res);
    if (!res.ok) throw new Error(data.detail || "Erro ao carregar histórico.");

    const p = data.pocket || {};
    const t = data.totals || {};
    _currentPocketForEdit = p;
    titleEl.textContent = `Histórico: ${p.name || pocketName}`;
    const interestTxt = p.interest_enabled === false ? "Sem rendimento" : _formatCdiRate(p.interest_rate);
    subEl.textContent = p.description ? `${p.description} · ${interestTxt}` : interestTxt;

    document.getElementById("pkt-hist-balance").textContent      = fmt(p.balance || 0);
    document.getElementById("pkt-hist-deposits").textContent     = fmt(t.deposits || 0);
    document.getElementById("pkt-hist-withdrawals").textContent  = fmt(t.withdrawals || 0);
    sumEl.style.display = "grid";
    const actionsEl = document.getElementById("pkt-move-actions");
    if (actionsEl) actionsEl.style.display = "flex";

    const items = data.history || [];
    if (!items.length) {
      bodyEl.innerHTML = `<div class="pkt-hist-empty">Nenhum movimento registrado nesta caixinha ainda.</div>`;
      return;
    }

    bodyEl.innerHTML = `<div class="pkt-hist-list">${
      items.map(h => {
        const meta = PKT_HIST_LABELS[h.tipo] || { label: h.tipo, cls: "crt" };
        const sign = h.tipo === "deposito_caixinha" ? "+" : (h.tipo === "saque_caixinha" ? "−" : "");
        const valTxt = h.tipo === "criar_caixinha" ? "—" : `${sign} ${fmt(h.valor)}`;
        return `<div class="pkt-hist-row">
          <div class="pkt-l">
            <span class="pkt-tag ${meta.cls}">${meta.label}</span>
            <span class="pkt-date">${fmtDate(h.criado_em)}</span>
            ${h.nota ? `<span class="pkt-note" title="${esc(h.nota)}">${esc(h.nota)}</span>` : ""}
          </div>
          <span class="pkt-val ${meta.cls}">${valTxt}</span>
        </div>`;
      }).join("")
    }</div>`;
  } catch (err) {
    bodyEl.innerHTML = `<div class="pkt-hist-error">${esc(err.message || "Erro ao carregar histórico.")}</div>`;
  }
}

function editCurrentPocketFromHistory() {
  if (!_currentPocketForEdit) return;
  const p = _currentPocketForEdit;
  closePocketHistory();
  openGoalEditModal({
    id: p.id,
    name: p.name,
    balance: p.balance,
    description: p.description,
    interest_enabled: p.interest_enabled,
    interest_rate: p.interest_rate,
    interest_period: p.interest_period,
    target_amount: p.target_amount,
    target_date: p.target_date,
    emoji: p.emoji,
    color: p.color,
    status: p.status,
    is_goal: p.target_amount != null,
  });
}

document.getElementById("pocket-history-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closePocketHistory();
});

// ─── Caixinha (excluir) ──────────────────────────────────────────────────────
async function confirmDeletePocket(pocketName, balance) {
  const saldo = Math.abs(Number(balance) || 0);
  if (saldo >= 0.005) {
    await alertModal(
      `Esta caixinha tem ${fmt(balance)} em saldo. Faça um saque para a conta principal antes de excluir.`,
      { title: "Saldo não zerado" },
    );
    return;
  }

  const ok = await confirmModal(
    `O histórico de aportes e saques desta caixinha será apagado permanentemente. Esta ação não pode ser desfeita.`,
    {
      title: `Excluir caixinha "${pocketName}"?`,
      confirmText: "Excluir",
      destructive: true,
    },
  );
  if (!ok) return;

  try {
    const res = await fetch(`${API}/pockets/${USER_ID}/${encodeURIComponent(pocketName)}`, {
      method: "DELETE",
      credentials: "same-origin",
      headers: csrfHeaders({}),
    });
    const data = await readResponsePayload(res);
    if (!res.ok) throw new Error(data.detail || "Erro ao excluir caixinha.");

    showLaunchSuccessToast(`✓ Caixinha "${data.name || pocketName}" removida`);
    sendRefresh();
  } catch (err) {
    await alertModal(err.message || "Erro ao excluir caixinha.", { title: "Erro" });
  }
}

// ─── Cartão de crédito (criar) ───────────────────────────────────────────────
let cardSubmitting = false;

function openCardModal() {
  document.getElementById("card-name").value = "";
  document.getElementById("card-closing-day").value = "";
  document.getElementById("card-due-day").value = "";
  hideCardError();
  document.getElementById("card-overlay").classList.add("open");
  setTimeout(() => document.getElementById("card-name").focus(), 50);
}

function closeCardModal() {
  document.getElementById("card-overlay").classList.remove("open");
}

function hideCardError() {
  const el = document.getElementById("card-error");
  el.classList.remove("show");
  el.textContent = "";
}

function showCardError(msg) {
  const el = document.getElementById("card-error");
  el.textContent = msg;
  el.classList.add("show");
}

async function submitCard() {
  if (cardSubmitting) return;
  hideCardError();

  const name = document.getElementById("card-name").value.trim();
  const closingDay = parseInt(document.getElementById("card-closing-day").value, 10);
  const dueDay = parseInt(document.getElementById("card-due-day").value, 10);

  if (!name) {
    showCardError("Informe o nome do cartão.");
    return;
  }
  if (!closingDay || closingDay < 1 || closingDay > 31) {
    showCardError("Dia de fechamento deve ser entre 1 e 31.");
    return;
  }
  if (!dueDay || dueDay < 1 || dueDay > 31) {
    showCardError("Dia de vencimento deve ser entre 1 e 31.");
    return;
  }

  const btn = document.getElementById("card-submit-btn");
  cardSubmitting = true;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Criando...";

  try {
    const res = await fetch(`${API}/cards/${USER_ID}`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, closing_day: closingDay, due_day: dueDay }),
    });
    const data = await readResponsePayload(res);
    if (!res.ok) throw new Error(data.detail || "Erro ao criar cartão.");

    closeCardModal();
    const canon = (data.card && data.card.name) || name;
    showLaunchSuccessToast(`✓ Cartão "${canon}" criado`);
    sendRefresh();
  } catch (err) {
    showCardError(err.message || "Erro ao criar cartão.");
  } finally {
    cardSubmitting = false;
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

document.getElementById("card-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closeCardModal();
});
["card-name", "card-closing-day", "card-due-day"].forEach(id => {
  document.getElementById(id).addEventListener("keydown", e => {
    if (e.key === "Enter") submitCard();
  });
});

// Auto-abre quando vem com ?action=launch (atalho da home)
function whenDashboardReady(maxWaitMs = 8000) {
  return new Promise(resolve => {
    if (lastData) { resolve(true); return; }
    const start = Date.now();
    const interval = setInterval(() => {
      if (lastData || Date.now() - start > maxWaitMs) {
        clearInterval(interval);
        resolve(Boolean(lastData));
      }
    }, 50);
  });
}

(async function maybeOpenFromQuery() {
  try {
    const action = params.get("action");
    if (action !== "launch" && action !== "pay-bill") return;
    await whenDashboardReady();
    if (action === "launch") {
      const tipoParam = params.get("tipo");
      const opts = (tipoParam === "receita" || tipoParam === "despesa" || tipoParam === "credito")
        ? { tipo: tipoParam } : {};
      openLaunchModal(opts);
    } else if (action === "pay-bill") {
      openPayBillModal();
    }
  } catch {}
})();

/* ═══════════════════════════════════════════════════════════════════════
   BILL MODALS — detalhe + pagamento + comprovante
═══════════════════════════════════════════════════════════════════════ */
let payBillState = {
  balance: 0,
  bills: [],
  selectedId: null,
  submitting: false,
};

function fmtBillValue(n) {
  return fmt(Number(n) || 0);
}

// Estado de navegação entre faturas do mesmo cartão. Setado por
// `onCardRowClick` (passa cardId + lista) e usado pelas setas ◀ ▶.
let _billNav = { cardId: null, billIds: [], currentIdx: -1 };

function _renderBillNav() {
  const nav = document.getElementById("bill-detail-nav");
  const prev = document.getElementById("bill-detail-prev");
  const next = document.getElementById("bill-detail-next");
  const label = document.getElementById("bill-detail-nav-label");
  if (!_billNav.billIds.length || _billNav.currentIdx < 0) {
    nav.style.display = "none";
    return;
  }
  nav.style.display = "flex";
  prev.disabled = _billNav.currentIdx <= 0;
  next.disabled = _billNav.currentIdx >= _billNav.billIds.length - 1;
  label.textContent = `${_billNav.currentIdx + 1} de ${_billNav.billIds.length}`;
}

window.navBill = function(direction) {
  const idx = _billNav.currentIdx + direction;
  if (idx < 0 || idx >= _billNav.billIds.length) return;
  _billNav.currentIdx = idx;
  openBillDetailModal(_billNav.billIds[idx], { preserveNav: true });
};

async function openBillDetailModal(billId, opts = {}) {
  if (!opts.preserveNav) {
    _billNav = { cardId: null, billIds: [billId], currentIdx: 0 };
  } else {
    // Sincroniza o índice com o billId mostrado
    const found = _billNav.billIds.indexOf(billId);
    if (found >= 0) _billNav.currentIdx = found;
  }

  const overlay = document.getElementById("bill-detail-overlay");
  document.getElementById("bill-detail-title").textContent = "Carregando...";
  document.getElementById("bill-detail-sub").textContent = "—";
  document.getElementById("bill-detail-total").textContent = "—";
  document.getElementById("bill-detail-paid").textContent = "—";
  document.getElementById("bill-detail-due").textContent = "—";
  document.getElementById("bill-detail-tx").innerHTML = "";
  document.getElementById("bill-detail-pay-btn").disabled = true;
  document.getElementById("bill-detail-pay-btn").dataset.billId = "";
  _renderBillNav();
  overlay.classList.add("open");

  try {
    const res = await fetch(`${API}/bills/${USER_ID}/${billId}`, { credentials: "same-origin" });
    if (!res.ok) throw new Error(await readApiError(res));
    const data = await res.json();
    const b = data.bill || {};
    document.getElementById("bill-detail-title").textContent = `Fatura · ${b.card_name || "—"}`;
    document.getElementById("bill-detail-sub").textContent = b.label || "—";
    document.getElementById("bill-detail-total").textContent = fmtBillValue(b.total);
    document.getElementById("bill-detail-paid").textContent = fmtBillValue(b.paid_amount);
    document.getElementById("bill-detail-due").textContent = fmtBillValue(b.due_amount);

    const list = data.transactions || [];
    const txEl = document.getElementById("bill-detail-tx");
    if (!list.length) {
      txEl.innerHTML = '<div class="empty" style="padding:14px;text-align:center;font-size:.78rem;color:var(--text-3)">Nenhuma compra nesta fatura.</div>';
    } else {
      txEl.innerHTML = list.map(t => {
        const desc = t.nota || t.categoria || "Compra";
        const dt = t.purchased_at ? new Date(t.purchased_at).toLocaleDateString("pt-BR",{day:"2-digit",month:"2-digit"}) : "";
        const inst = (t.installments_total && t.installments_total > 1)
          ? ` · ${t.installment_no || "?"}/${t.installments_total}` : "";
        const sign = t.is_refund ? "+" : "-";
        return `
          <div class="bill-tx">
            <div style="min-width:0;flex:1">
              <div class="tx-desc">${escapeHtmlSafe(desc)}${inst}</div>
              <div class="tx-cat">${dt}${t.categoria ? " · " + escapeHtmlSafe(t.categoria) : ""}</div>
            </div>
            <div class="tx-val${t.is_refund ? " refund" : ""}">${sign} ${fmtBillValue(t.valor)}</div>
          </div>
        `;
      }).join("");
    }

    const payBtn = document.getElementById("bill-detail-pay-btn");
    payBtn.disabled = !(b.due_amount > 0);
    payBtn.dataset.billId = String(b.id || "");
    payBtn.textContent = b.due_amount > 0 ? "Pagar fatura" : "Sem saldo em aberto";
  } catch (err) {
    document.getElementById("bill-detail-title").textContent = "Erro";
    document.getElementById("bill-detail-sub").textContent = err.message || "Não foi possível carregar a fatura.";
  }
}

function closeBillDetailModal() {
  document.getElementById("bill-detail-overlay").classList.remove("open");
}

function escapeHtmlSafe(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

function payFromDetail() {
  const billId = Number(document.getElementById("bill-detail-pay-btn").dataset.billId || 0);
  if (!billId) return;
  closeBillDetailModal();
  openPayBillModal({ preselectId: billId });
}

async function onCardRowClick(rowEl) {
  const cardId = Number(rowEl && rowEl.dataset && rowEl.dataset.cardId);
  if (!cardId) return;
  const openBillId = Number(rowEl && rowEl.dataset && rowEl.dataset.openBillId) || null;
  // Busca TODAS as faturas desse cartão (abertas + pagas/fechadas) pra
  // popular as setas de navegação ◀ ▶. Ordenadas por period_end ASC, abre
  // primeiro a corrente (mais antiga em aberto). Backend já ordena.
  try {
    const res = await fetch(
      `${API}/bills/${USER_ID}?card_id=${cardId}&include_closed=true`,
      { credentials: "same-origin" },
    );
    if (!res.ok) throw new Error(await readApiError(res));
    const data = await res.json();
    const billsForCard = data.bills || [];
    if (!billsForCard.length) {
      showLaunchSuccessToast("Sem faturas pra esse cartão ainda.");
      return;
    }

    // Abre a fatura que o card mostra (open_bill). Só se não vier, cai pra
    // fatura aberta com valor mais RECENTE (lista é period_end ASC → varre de
    // trás). Antes pegava a mais antiga em aberto, que com parcelamento (várias
    // faturas futuras "open") abria uma fatura errada.
    let currentIdx = openBillId
      ? billsForCard.findIndex(b => b.id === openBillId)
      : -1;
    if (currentIdx < 0) {
      for (let i = billsForCard.length - 1; i >= 0; i--) {
        if (billsForCard[i].status === "open" && (billsForCard[i].due_amount > 0 || billsForCard[i].total > 0)) {
          currentIdx = i;
          break;
        }
      }
    }
    if (currentIdx < 0) currentIdx = billsForCard.length - 1;

    _billNav = {
      cardId,
      billIds: billsForCard.map(b => b.id),
      currentIdx,
    };
    openBillDetailModal(billsForCard[currentIdx].id, { preserveNav: true });
  } catch (err) {
    showLaunchSuccessToast(err.message || "Erro ao carregar fatura.");
  }
}

/* ─── Drag-to-reorder dos cartões (desktop only) ───────────────────────────
   Drag manual com pointer events: o card arrastado segue o cursor via
   transform translateY; outros cards "abrem espaço" também via transform.
   Nenhum elemento é duplicado no DOM, e o layout do stack (margin-top
   negativo) fica intacto durante toda a operação. */
function setupCardWalletSort() {
  if (!window.matchMedia("(pointer: fine)").matches) return; // mobile: skip
  document.querySelectorAll(".wallet").forEach(wallet => {
    if (wallet.__dragInstalled) return;
    wallet.__dragInstalled = true;
    _installWalletDrag(wallet);
  });
}

function _installWalletDrag(wallet) {
  const STEP = 46;          // = --wallet-peek; espaçamento entre slots
  const HOLD_MS = 50;       // tempo de hold antes de iniciar drag
  const MOVE_THRESHOLD = 4; // px de movimento que já cancela o "hold" timer

  let state = null;
  let holdTimer = null;

  wallet.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    // Não inicia drag se gesto começou no badge (que abre fatura)
    if (e.target.closest(".wallet-status")) return;
    const item = e.target.closest(".wallet-item");
    if (!item) return;

    const items = [...wallet.querySelectorAll(".wallet-item")];
    const fromIndex = items.indexOf(item);
    if (fromIndex < 0) return;

    const startY = e.clientY;
    const startX = e.clientX;
    let armed = false; // vira true após HOLD_MS sem movimento brusco

    clearTimeout(holdTimer);
    holdTimer = setTimeout(() => {
      armed = true;
      wallet.classList.add("wallet--dragging");
      item.classList.add("wallet-item--drag");
      try { wallet.setPointerCapture && wallet.setPointerCapture(e.pointerId); } catch (_) {}
      state = { items, item, fromIndex, toIndex: fromIndex, startY, pointerId: e.pointerId };
    }, HOLD_MS);

    const onMove = (ev) => {
      if (!armed) {
        if (Math.hypot(ev.clientX - startX, ev.clientY - startY) > MOVE_THRESHOLD) {
          clearTimeout(holdTimer); holdTimer = null;
        }
        return;
      }
      if (!state) return;
      const dy = ev.clientY - state.startY;
      state.item.style.transform = `translateY(${dy}px) scale(1.015) rotate(-1deg)`;
      state.item.style.zIndex = 100;
      // Calcula slot alvo baseado em quantos passos de STEP percorreu.
      let slotShift = Math.round(dy / STEP);
      let toIndex = Math.max(0, Math.min(state.items.length - 1, state.fromIndex + slotShift));
      state.toIndex = toIndex;
      // Anima outros cards pra abrir espaço
      state.items.forEach((other, i) => {
        if (other === state.item) return;
        let shift = 0;
        if (state.fromIndex < toIndex && i > state.fromIndex && i <= toIndex) shift = -STEP;
        else if (state.fromIndex > toIndex && i < state.fromIndex && i >= toIndex) shift = STEP;
        other.style.transform = shift ? `translateY(${shift}px)` : "";
      });
    };

    const onUp = async () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      clearTimeout(holdTimer); holdTimer = null;
      if (!armed || !state) { state = null; return; }
      const { items, item, fromIndex, toIndex } = state;
      // Reset transforms (animação volta visualmente, depois reordenamos DOM)
      items.forEach(el => { el.style.transform = ""; el.style.zIndex = ""; });
      item.classList.remove("wallet-item--drag");
      wallet.classList.remove("wallet--dragging");
      state = null;
      if (toIndex !== fromIndex) {
        // Reordena DOM
        const moving = items[fromIndex];
        const ref = items[toIndex];
        if (toIndex > fromIndex) ref.after(moving); else ref.before(moving);
        // Persiste
        const orderedIds = [...wallet.querySelectorAll(".wallet-item")]
          .map(el => Number(el.querySelector(".wallet-trigger")?.dataset.cardId))
          .filter(id => Number.isFinite(id) && id > 0);
        if (orderedIds.length) await persistCardOrder(orderedIds);
      }
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  });
}

/* Drag-to-reorder na view Cartões (#cards-grid). Cards são <details>, então
   `delay` evita conflito com click no <summary> (expandir/colapsar). */
async function setupCardsGridSort() {
  // Drag só faz sentido em ponteiro fino (mouse/trackpad). Checa antes de
  // carregar a lib pra não baixar nada em touch.
  if (!window.matchMedia("(pointer: fine)").matches) return;
  const grid = document.getElementById("cards-grid");
  if (!grid) return;
  await ensureSortable();
  if (typeof Sortable === "undefined") return;   // load falhou → segue sem drag
  if (grid.__sortable) {
    try { grid.__sortable.destroy(); } catch (_) {}
  }
  grid.__sortable = Sortable.create(grid, {
    animation: 180,
    forceFallback: true,
    delay: 180,
    delayOnTouchOnly: false,
    ghostClass: "cc-card-grid--ghost",
    dragClass: "cc-card-grid--drag",
    // Botões/ações dentro do detail não devem iniciar drag.
    filter: ".cc-detail-actions, .cc-detail-actions *, button, .mock-cta, .inst-delete-btn",
    preventOnFilter: false,
    onEnd: async () => {
      const orderedIds = [...grid.querySelectorAll("details.cc-details[data-card-id]")]
        .map(el => Number(el.dataset.cardId))
        .filter(id => Number.isFinite(id) && id > 0);
      if (orderedIds.length) await persistCardOrder(orderedIds);
    },
  });
}

async function persistCardOrder(orderedIds) {
  if (!USER_ID) return;
  try {
    const r = await fetch(`${API}/cards/${USER_ID}/reorder`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ ordered_ids: orderedIds }),
    });
    if (!r.ok) throw new Error(await readApiError(r));
  } catch (e) {
    console.warn("[reorder] erro:", e);
  }
}

async function openPayBillModal(opts) {
  opts = opts || {};
  const overlay = document.getElementById("pay-bill-overlay");
  document.getElementById("pay-bill-balance").textContent = "—";
  document.getElementById("pay-bill-list").innerHTML = '<div class="empty" style="padding:14px;text-align:center;font-size:.78rem;color:var(--text-3)">Carregando...</div>';
  document.getElementById("pay-bill-empty").style.display = "none";
  document.getElementById("pay-bill-form").style.display = "none";
  document.getElementById("pay-bill-submit-btn").disabled = true;
  hidePayBillError();
  payBillState = { balance: 0, bills: [], selectedId: null, submitting: false };
  overlay.classList.add("open");

  try {
    const res = await fetch(`${API}/bills/${USER_ID}`, { credentials: "same-origin" });
    if (!res.ok) throw new Error(await readApiError(res));
    const data = await res.json();
    payBillState.balance = Number(data.balance) || 0;
    payBillState.bills = data.bills || [];
    document.getElementById("pay-bill-balance").textContent = fmtBillValue(payBillState.balance);
    renderPayBillList();
    if (opts.preselectId) selectPayBill(Number(opts.preselectId));
    else if (payBillState.bills.length === 1) selectPayBill(payBillState.bills[0].id);
  } catch (err) {
    document.getElementById("pay-bill-list").innerHTML = `<div class="empty" style="padding:14px;text-align:center;font-size:.78rem;color:var(--red)">${escapeHtmlSafe(err.message || "Erro ao carregar faturas.")}</div>`;
  }
}

function renderPayBillList() {
  const list = payBillState.bills;
  const el = document.getElementById("pay-bill-list");
  if (!list.length) {
    el.innerHTML = "";
    document.getElementById("pay-bill-empty").style.display = "";
    return;
  }
  el.innerHTML = list.map(b => `
    <button type="button" class="bill-opt${payBillState.selectedId === b.id ? ' selected' : ''}" data-id="${b.id}" onclick="selectPayBill(${b.id})">
      <div style="min-width:0">
        <div class="b-name">${escapeHtmlSafe(b.card_name)}</div>
        <div class="b-period">${escapeHtmlSafe(b.label || "")}</div>
      </div>
      <div style="text-align:right">
        <div class="b-due">${fmtBillValue(b.due_amount)}</div>
        <div class="b-due-sub">em aberto</div>
      </div>
    </button>
  `).join("");
}

function selectPayBill(billId) {
  payBillState.selectedId = Number(billId);
  renderPayBillList();
  const b = payBillState.bills.find(x => x.id === payBillState.selectedId);
  if (!b) return;
  document.getElementById("pay-bill-form").style.display = "";
  const amountEl = document.getElementById("pay-bill-amount");
  amountEl.value = b.due_amount.toFixed(2);
  amountEl.max = b.due_amount;
  document.getElementById("pay-bill-hint").textContent = `Total ${fmtBillValue(b.total)} · Pago ${fmtBillValue(b.paid_amount)} · Em aberto ${fmtBillValue(b.due_amount)}`;
  hidePayBillError();
  document.getElementById("pay-bill-submit-btn").disabled = false;
  setTimeout(() => amountEl.focus(), 30);
}

function hidePayBillError() {
  const el = document.getElementById("pay-bill-error");
  el.classList.remove("show");
  el.textContent = "";
}
function showPayBillError(msg) {
  const el = document.getElementById("pay-bill-error");
  el.textContent = msg;
  el.classList.add("show");
}

function closePayBillModal() {
  document.getElementById("pay-bill-overlay").classList.remove("open");
}

// ══ Ajustar Carteira (dinheiro fora de banco conectado — Open Finance) ══════
// A "Carteira" é o saldo manual (accounts.balance): dinheiro em espécie + contas
// não conectadas. O saldo dos bancos conectados vem do Open Finance e é somado
// à parte. Ao conectar o 1º banco o usuário zera aqui o que era controle manual
// daquele banco, senão o mesmo dinheiro conta 2x. Reusa POST /adjust-balance
// (cria um launch de ajuste, mantendo rastreabilidade no histórico).
let _adjustWalletState = { banks: 0, submitting: false };

function _updateAdjustWalletTotal() {
  const v = parseFloat(document.getElementById("adjust-wallet-input").value);
  const carteira = isNaN(v) ? 0 : v;
  document.getElementById("adjust-wallet-total").textContent = _fmtBRL(carteira + _adjustWalletState.banks);
}

function openAdjustWalletModal() {
  const d = lastData || {};
  const banks = Number(d.of_bank_balance || 0);
  const carteira = Number(d.balance || 0);
  _adjustWalletState = { banks, submitting: false };
  document.getElementById("adjust-wallet-banks").textContent = _fmtBRL(banks);
  const inp = document.getElementById("adjust-wallet-input");
  inp.value = carteira.toFixed(2);
  document.getElementById("adjust-wallet-error").textContent = "";
  document.getElementById("adjust-wallet-overlay").classList.add("open");
  inp.oninput = _updateAdjustWalletTotal;
  _updateAdjustWalletTotal();
  setTimeout(() => { inp.focus(); inp.select(); }, 50);
}

function closeAdjustWalletModal() {
  document.getElementById("adjust-wallet-overlay").classList.remove("open");
}

function _adjustWalletError(msg) {
  const errEl = document.getElementById("adjust-wallet-error");
  errEl.textContent = msg || "";
  errEl.classList.toggle("show", Boolean(msg));  // .modal-error é display:none sem .show
}

async function submitAdjustWallet() {
  if (_adjustWalletState.submitting) return;
  _adjustWalletError("");
  const raw = document.getElementById("adjust-wallet-input").value.trim();
  if (raw === "") { _adjustWalletError("Digite um valor (use 0 se está tudo no banco)."); return; }
  const v = parseFloat(raw);
  if (isNaN(v) || v < 0) { _adjustWalletError("Digite um valor válido (zero ou positivo)."); return; }
  const btn = document.getElementById("adjust-wallet-submit");
  _adjustWalletState.submitting = true;
  btn.disabled = true;
  try {
    const resp = await fetch(`${API}/account/${USER_ID}/adjust-balance`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ target_balance: v }),
    });
    if (!resp.ok) throw new Error(await readApiError(resp));
    closeAdjustWalletModal();
    showToast("✓ Carteira ajustada");
    sendRefresh();
  } catch (err) {
    errEl.textContent = String(err.message || err);
  } finally {
    _adjustWalletState.submitting = false;
    btn.disabled = false;
  }
}

async function submitPayBill() {
  if (payBillState.submitting) return;
  hidePayBillError();
  const billId = payBillState.selectedId;
  if (!billId) { showPayBillError("Selecione uma fatura."); return; }
  const b = payBillState.bills.find(x => x.id === billId);
  if (!b) { showPayBillError("Fatura inválida."); return; }

  const valorRaw = (document.getElementById("pay-bill-amount").value || "").replace(",", ".");
  const valor = parseFloat(valorRaw);
  if (!valor || isNaN(valor) || valor <= 0) {
    showPayBillError("Informe um valor maior que zero.");
    return;
  }
  if (valor > b.due_amount + 0.005) {
    showPayBillError(`Valor maior que o em aberto (${fmtBillValue(b.due_amount)}).`);
    return;
  }
  if (valor > payBillState.balance + 0.005) {
    showPayBillError(`Saldo insuficiente. Saldo atual: ${fmtBillValue(payBillState.balance)}.`);
    return;
  }

  // Confirmação extra ao antecipar fatura futura — comum em parcelamento
  // (paga 3/3 antes de 1/3 e 2/3). Não bloqueia, só avisa pra evitar erro.
  const todayIso = new Date().toISOString().slice(0, 10);
  if (b.period_end && b.period_end > todayIso) {
    const ok = await confirmModal(
      `${b.card_name} · ${b.label || ""} ainda não fechou (vence depois de hoje). ` +
      `Você está pagando uma fatura futura. Tem certeza?`,
      { title: "Antecipar fatura?", confirmText: "Sim, pagar", destructive: false },
    );
    if (!ok) return;
  }

  const btn = document.getElementById("pay-bill-submit-btn");
  payBillState.submitting = true;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Pagando...";

  try {
    const res = await fetch(`${API}/bills/${USER_ID}/${billId}/pay`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ amount: valor }),
    });
    const data = await readResponsePayload(res);
    if (!res.ok) throw new Error(data.detail || "Erro ao pagar fatura.");

    closePayBillModal();
    showPayReceiptModal(data, b);
    sendRefresh();
  } catch (err) {
    showPayBillError(err.message || "Erro ao pagar fatura.");
  } finally {
    payBillState.submitting = false;
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

function showPayReceiptModal(data, bill) {
  document.getElementById("receipt-amount").textContent = fmtBillValue(data.paid);
  const cardName = data.card_name || (bill && bill.card_name) || "—";
  const period = (bill && bill.label) ? ` · ${bill.label}` : "";
  document.getElementById("receipt-card-line").textContent = `${cardName}${period}`;
  document.getElementById("receipt-balance").textContent = fmtBillValue(data.new_balance);
  document.getElementById("receipt-open").textContent = fmtBillValue(data.bill_due_amount);
  document.getElementById("receipt-status").textContent = data.bill_status === "paid" ? "Paga" : "Aberta";
  document.getElementById("receipt-launch-id").textContent = data.launch_id ? `#${data.launch_id}` : "—";
  document.getElementById("pay-bill-receipt-overlay").classList.add("open");
}

function closePayReceiptModal() {
  document.getElementById("pay-bill-receipt-overlay").classList.remove("open");
}

document.getElementById("bill-detail-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closeBillDetailModal();
});
document.getElementById("pay-bill-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closePayBillModal();
});
document.getElementById("pay-bill-receipt-overlay").addEventListener("click", e => {
  if (e.target === e.currentTarget) closePayReceiptModal();
});
document.getElementById("pay-bill-amount").addEventListener("keydown", e => {
  if (e.key === "Enter") submitPayBill();
});
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  _fechaSeAberto("bill-detail-overlay", closeBillDetailModal);
  _fechaSeAberto("pay-bill-overlay", closePayBillModal);
  _fechaSeAberto("pay-bill-receipt-overlay", closePayReceiptModal);
});


/* ═══════════════════════════════════════════════════════════════════════
   EXPORT
═══════════════════════════════════════════════════════════════════════ */
// ── Importar OFX (extrato bancario ou fatura de cartao) ────────────────
function openOfxImport() {
  // Free: nao abre o picker — vai direto pro modal de upgrade.
  if (!featureAllowed("ofx_import")) {
    showUpgradeModal("ofx_import");
    return;
  }
  const inp = document.getElementById("ofx-file-input");
  if (inp) { inp.value = ""; inp.click(); }
}

async function handleOfxFileSelected(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".ofx")) {
    showLaunchSuccessToast("Arquivo precisa ter extensão .ofx.");
    return;
  }

  showLaunchSuccessToast("Enviando arquivo OFX…");
  const form = new FormData();
  form.append("file", file, file.name);

  try {
    const resp = await fetch(`${API}/ofx/import/${USER_ID}`, {
      method: "POST",
      body: form,
      credentials: "same-origin",
      // CSRF: o interceptor global em window.fetch nao mexe nos headers,
      // entao incluimos manualmente igual outros POSTs.
      headers: { "x-csrf-token": getCsrfToken() },
    });
    let data = {};
    try { data = await resp.json(); } catch {}
    if (!resp.ok) {
      // 403 pro_required ja eh tratado pelo interceptor global (abre modal).
      // Outros erros mostram toast com o detail amigavel.
      if (resp.status !== 403) {
        const msg = (data && (data.detail || data.error)) || `Erro ${resp.status}.`;
        showLaunchSuccessToast(typeof msg === "string" ? msg : "Erro ao importar OFX.");
      }
      return;
    }
    // Sucesso: mostra resultado no modal e atualiza dashboard.
    document.getElementById("ofx-result-body").textContent = data.message || "Importação concluída.";
    document.getElementById("ofx-result-overlay").classList.add("open");
    sendRefresh && sendRefresh();
  } catch (e) {
    showLaunchSuccessToast("Erro ao importar OFX. Tente novamente.");
  }
}

function closeOfxResult() {
  const ov = document.getElementById("ofx-result-overlay");
  if (ov) ov.classList.remove("open");
}
window.pigModalKeys && pigModalKeys("ofx-result-overlay", closeOfxResult);

function getCsrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function exportToEmail() {
  const url = `${API}/export/${USER_ID}?year=${viewYear}&month=${viewMonth}`;
  showLaunchSuccessToast(" Gerando e enviando o extrato…");
  try {
    const resp = await fetch(url, { method: "POST", credentials: "same-origin", headers: csrfHeaders() });
    if (resp.status === 404) {
      showLaunchSuccessToast("Nenhum lançamento neste mês para exportar.", true);
      return;
    }
    if (resp.status === 429) {
      showLaunchSuccessToast("Você exportou agora há pouco. Aguarde um instante e tente de novo.", true);
      return;
    }
    if (!resp.ok) {
      showLaunchSuccessToast("Não consegui enviar agora. Tente novamente.", true);
      return;
    }
    const data = await resp.json().catch(() => ({}));
    showLaunchSuccessToast(` Extrato enviado pro seu email ${data.email || "cadastrado"}.`);
  } catch (e) {
    showLaunchSuccessToast("Não consegui enviar agora. Tente novamente.", true);
  }
}

/* ═══════════════════════════════════════════════════════════════════════
   CHARTS
═══════════════════════════════════════════════════════════════════════ */
function buildCatChart(cats) {
  const el = document.getElementById("chart-cat"); if (!el) return;
  const labels = cats.map(c => c.categoria === "sem categoria" ? " Sem Cat." : c.categoria);
  const data   = cats.map(c => c.total);
  const _pal = catColors();
  const colors = cats.map((_, i) => _pal[i % _pal.length]);
  if (chartCat) chartCat.destroy();
  chartCat = new Chart(el, {
    type:"doughnut",
    data:{ labels, datasets:[{ data, backgroundColor:colors.map(c=>c+"bb"), borderColor:colors, borderWidth:1.5, hoverOffset:8 }] },
    options:{
      cutout:"68%",
      plugins:{
        legend:{ position:"bottom", labels:{color:_isLightMode()?"rgba(15,23,42,.6)":"rgba(255,255,255,.5)",font:{size:10},padding:10,boxWidth:9,usePointStyle:true} },
        tooltip:{ backgroundColor:"rgba(10,12,24,.88)",borderColor:"rgba(255,255,255,.1)",borderWidth:1,
          titleColor:"rgba(255,255,255,.9)",bodyColor:"rgba(255,255,255,.6)",
          callbacks:{label:ctx=>" "+fmt(ctx.parsed)} }
      },
      animation:{duration:700,easing:"easeInOutQuart"}
    }
  });
}

// Fallback: compute daily totals from recent_launches if backend doesn't send daily_expenses
function computeDailyFromLaunches(launches, year, month) {
  const result = {};
  launches.forEach(l => {
    if (!l.criado_em) return;
    if (l.tipo === "receita" || l.tipo === "entrada") return;
    const d = new Date(l.criado_em);
    if (d.getFullYear() !== year || d.getMonth() + 1 !== month) return;
    const day = d.getDate();
    result[day] = (result[day] || 0) + Math.abs(l.valor || 0);
  });
  return Object.entries(result).map(([day, total]) => ({ day: parseInt(day), total }));
}

// Gráfico de evolução dos gastos com janela rolante (7D / 30D / 3M).
// Série vem de /expenses/daily?days=N como [{date:"YYYY-MM-DD", total}].
let _expensePeriod = 30;
let _expenseSeries = null;

function buildExpenseChart(series, days) {
  const el = document.getElementById("chart-day"); if (!el) return;
  const byDate = {};
  (series || []).forEach(r => { byDate[r.date] = r.total; });
  const pad = n => String(n).padStart(2, "0");
  const labels = [], totals = [];
  const base = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const dt = new Date(base.getFullYear(), base.getMonth(), base.getDate() - i);
    const key = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
    totals.push(byDate[key] || 0);
    labels.push(`${pad(dt.getDate())}/${pad(dt.getMonth() + 1)}`);
  }
  const step = days <= 7 ? 1 : days <= 30 ? 5 : 15;
  const light = _isLightMode();
  if (chartDay) chartDay.destroy();
  chartDay = new Chart(el, {
    type:"line",
    data:{ labels, datasets:[{
      label:"Gastos", data:totals,
      borderColor:"#FF2D8E", borderWidth:2.5,
      fill:true, backgroundColor:"rgba(255,45,142,.14)",
      tension:.4, pointRadius:0, pointHoverRadius:5,
      pointHoverBackgroundColor:"#FF2D8E", pointHoverBorderColor:"#fff", pointHoverBorderWidth:2
    }] },
    options:{
      responsive:true,maintainAspectRatio:false,
      scales:{
        x:{ grid:{color:light?"rgba(15,23,42,.06)":"rgba(255,255,255,.04)"}, ticks:{color:light?"rgba(15,23,42,.55)":"rgba(255,255,255,.35)",font:{size:9},maxRotation:0,autoSkip:false,callback:(v,i)=>(i%step===0||i===labels.length-1)?labels[i]:""} },
        y:{ grid:{color:light?"rgba(15,23,42,.08)":"rgba(255,255,255,.05)"}, ticks:{color:light?"rgba(15,23,42,.55)":"rgba(255,255,255,.35)",font:{size:9},callback:v=>fmtShort(v)} }
      },
      plugins:{
        legend:{display:false},
        tooltip:{ backgroundColor:"rgba(10,12,24,.88)",borderColor:"rgba(255,255,255,.1)",borderWidth:1,
          titleColor:"rgba(255,255,255,.9)",bodyColor:"rgba(255,255,255,.6)",
          callbacks:{title:ctx=>ctx[0].label,label:ctx=>" "+fmt(ctx.parsed.y)} }
      },
      animation:{duration:600,easing:"easeInOutQuart"}
    }
  });
}

// Dedup de VOO: no quente há 2 render() na mesma abertura ⇒ 2 fetches
// idênticos a /expenses/daily. Mesma janela já em voo devolve a promise em
// curso; período diferente (7D/30D/3M) ou nada em voo segue no fetch novo.
// (Não é o makeFetchChannel: a semântica dele é abortar/superar, não juntar.)
let _expenseChartFlight = null; // { days, promise }
function loadExpenseChart(days) {
  // Antes do dedup: `_expensePeriod` é SEMPRE o último período pedido, mesmo
  // quando a chamada só reaproveita um voo em curso. É ele que a guarda
  // abaixo usa para decidir o que pode pintar.
  _expensePeriod = days;
  if (_expenseChartFlight && _expenseChartFlight.days === days) return _expenseChartFlight.promise;
  const promise = (async () => {
    try {
      const r = await fetch(`${API}/expenses/daily/${USER_ID}?days=${days}`, { credentials: "same-origin" });
      if (!r.ok) return;
      const payload = await r.json();
      // Pedido superado não sobrescreve resposta mais nova — mesmo princípio
      // do monthRequestSeq no fetchMonthHttp, sem contador próprio porque
      // `_expensePeriod` já é a fonte da verdade do período selecionado.
      // Sem isto, trocar 7D→30D→7D com a 1ª pendente deixava o gráfico com a
      // série de 30D na aba 7D (last-writer-wins — comportamento que já
      // existia antes do dedup desta Onda).
      if (days !== _expensePeriod) return;
      _expenseSeries = payload.data || [];
      buildExpenseChart(_expenseSeries, days);
    } catch (e) { console.warn("[expenses] fetch error:", e); }
    finally {
      if (_expenseChartFlight && _expenseChartFlight.promise === promise) _expenseChartFlight = null;
    }
  })();
  _expenseChartFlight = { days, promise };
  return promise;
}

function setExpensePeriod(days, btn) {
  const tabs = document.getElementById("expense-period-tabs");
  if (tabs) tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", b === btn));
  loadExpenseChart(days);
}

/* ═══════════════════════════════════════════════════════════════════════
   MONTHLY HISTORY CHART
═══════════════════════════════════════════════════════════════════════ */
function buildHistoryChart(history) {
  const el = document.getElementById("chart-history");
  if (!el || !history || !history.length) return;

  const labels   = history.map(h => {
    const [y, m] = h.month.split("-");
    return PT_MONTHS[parseInt(m) - 1].substring(0, 3) + "/" + y.substring(2);
  });
  const incomes  = history.map(h => h.income  || 0);
  const expenses = history.map(h => h.expense || 0);

  if (chartHistory) chartHistory.destroy();
  chartHistory = new Chart(el, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Receita",
          data: incomes,
          backgroundColor: "rgba(0,240,120,.55)",
          borderColor: "rgba(0,240,120,1)",
          borderWidth: 1.5,
          borderRadius: 5,
          borderSkipped: false
        },
        {
          label: "Despesa",
          data: expenses,
          backgroundColor: "rgba(255,45,45,.55)",
          borderColor: "rgba(255,45,45,1)",
          borderWidth: 1.5,
          borderRadius: 5,
          borderSkipped: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: {
            color: _isLightMode() ? "rgba(15,23,42,.7)" : "rgba(255,255,255,.55)",
            font: { size: 11 },
            usePointStyle: true,
            padding: 16
          }
        },
        tooltip: {
          backgroundColor: "rgba(10,12,24,.9)",
          borderColor: "rgba(255,255,255,.1)",
          borderWidth: 1,
          titleColor: "rgba(255,255,255,.9)",
          bodyColor: "rgba(255,255,255,.6)",
          callbacks: { label: ctx => " " + fmt(ctx.parsed.y) }
        }
      },
      scales: {
        x: {
          grid: { color: _isLightMode() ? "rgba(15,23,42,.06)" : "rgba(255,255,255,.04)" },
          ticks: { color: _isLightMode() ? "rgba(15,23,42,.55)" : "rgba(255,255,255,.4)", font: { size: 10 } }
        },
        y: {
          grid: { color: _isLightMode() ? "rgba(15,23,42,.08)" : "rgba(255,255,255,.05)" },
          ticks: { color: _isLightMode() ? "rgba(15,23,42,.55)" : "rgba(255,255,255,.4)", font: { size: 10 }, callback: v => fmtShort(v) }
        }
      },
      animation: { duration: 700, easing: "easeInOutQuart" }
    }
  });
}

function _isLightMode() {
  return document.body.classList.contains("light");
}

let _lastHistory = null;
async function fetchHistory() {
  try {
    const r = await fetch(`${API}/history/${USER_ID}`, {
      credentials: "same-origin"
    });
    if (!r.ok) return;

    const payload = await r.json();
    const history = payload.data || [];
    _lastHistory = history;

    if (!history.length) return;

    document.getElementById("history-title").style.display = "";
    document.getElementById("history-wrap").style.display  = "";

    setTimeout(() => buildHistoryChart(history), 50);
  } catch(e) {
    console.warn("[history] fetch error:", e);
  }
}

/* ═══════════════════════════════════════════════════════════════════════
   RENDER
═══════════════════════════════════════════════════════════════════════ */
// Popula o card "Piggy notou" com o insight de maior prioridade vindo do LLM.
// Cache 6h em ai_proactive_cache + cron diário (proactive_ai_scheduler) garantem
// que a request seja rápida (~200ms quando cache quente). Em miss, fallback heurístico
// preenche pra não deixar vazio. Falha silenciosa: log no console, card escondido.
let _piggyInsightLoaded = false;
async function loadPiggyInsight() {
  if (!USER_ID || _piggyInsightLoaded) return;
  _piggyInsightLoaded = true;
  const card = document.getElementById("piggy-insight-card");
  if (!card) return;
  try {
    const r = await fetch(`${API}/insights/${USER_ID}/current`, { credentials: "same-origin" });
    if (!r.ok) { card.style.display = "none"; return; }
    const data = await r.json();
    const list = (data && data.insights) || [];
    if (!list.length) { card.style.display = "none"; return; }

    // Backend já prioriza por severidade — pega o primeiro
    const insight = list[0];

    document.getElementById("piggy-insight-title").textContent = (insight.icon ? insight.icon + " " : "") + (insight.title || "");

    const msgEl = document.getElementById("piggy-insight-message");
    if (insight.message) {
      msgEl.textContent = insight.message;
      msgEl.style.display = "";
    } else {
      msgEl.style.display = "none";
    }

    // Border colorida por severity
    const borderBySeverity = {
      critical: "rgba(239,68,68,.45)",
      warning:  "rgba(245,158,11,.45)",
      info:     "rgba(255,45,142,.3)",
    };
    card.style.borderColor = borderBySeverity[insight.severity] || borderBySeverity.info;

    // CTA condicional: se LLM apontou uma view com label, navega
    const cta = document.getElementById("piggy-insight-cta");
    if (insight.action_view && typeof navigateTo === "function") {
      cta.style.display = "";
      cta.textContent = `${insight.action_label || "Ajustar"} →`;
      cta.onclick = () => navigateTo(insight.action_view);
    } else {
      cta.style.display = "none";
    }

    card.style.display = "";
  } catch (e) {
    console.warn("[piggy-insight] erro:", e);
    card.style.display = "none";
  }
}

function render(d) {
  cacheMonthData(d);
  // Insight do Piggy: kick em paralelo, não bloqueia o render
  loadPiggyInsight();
  const grid = document.getElementById("grid");
  if (d.launches_pagination?.page) {
    launchesPage = d.launches_pagination.page;
}
  const ry   = d.year  || viewYear;
  const rm   = d.month || viewMonth;
  const ni   = (d.investments||[]).reduce((s,i)=>s+i.balance,0);
  const np   = (d.pockets||[]).reduce((s,p)=>s+p.balance,0);
  const inc  = d.monthly_income  || 0;
  const exp  = d.monthly_expense || 0;
  const allocSrc = d.monthly_allocations || {investments:{total:0}, pockets:{total:0}};
  const apt  = (allocSrc.investments?.total || 0) + (allocSrc.pockets?.total || 0);
  const sav  = inc - exp - apt;
  const rate = inc > 0 ? Math.round(apt/inc*100) : 0;
  // Déficit (sav<0): despesas+aportes passaram da renda. Nesse caso NÃO exibir
  // "X% da renda poupada" — soa positivo num mês negativo (você aportou puxando
  // do saldo, não é poupança sustentável). Mostra o motivo, em vermelho.
  const savDeltaCls = sav < 0 ? "down" : (rate>=20?"up":rate>=10?"":"down");
  const savDeltaTxt = sav < 0 ? "Aportes e gastos passaram da renda" : `${rate}% da renda poupada`;
  const rc   = rate>=20?"var(--green)":rate>=10?"var(--yellow)":"var(--red)";
  const hist = d.is_current_month !== undefined
    ? !d.is_current_month
    : (ry !== NOW.getFullYear() || rm !== NOW.getMonth() + 1);

  // Saldo consolidado: Carteira (dinheiro FORA de banco conectado — espécie, contas
  // não conectadas) + saldos dos bancos conectados (Open Finance). Só no mês atual
  // (o saldo do banco é "agora"); em mês histórico mostra só a Carteira manual.
  // `d.balance` é a Carteira: ao conectar um banco, o usuário zera aqui o que era
  // controle manual daquele banco (senão conta o mesmo dinheiro 2x). Ver "Ajustar carteira".
  const ofBankCount = Number(d.of_bank_count || 0);
  const hasBanks = !hist && ofBankCount > 0;
  const ofBank = hist ? 0 : Number(d.of_bank_balance || 0);
  const carteira = Number(d.balance || 0);
  const saldoAtual = carteira + ofBank;
  const pat = saldoAtual + ni + np;

  // Detalhamento do "Sobrou este mês" pro modal explicativo (clique no card).
  // Guarda exatamente o que está na tela, inclusive em mês histórico.
  _sobrouDetail = { inc, exp, apt, sav, rate, saldoAtual, month: rm, year: ry, hist };

  const nPk = (d.pockets||[]).length;
  const nCc = (d.credit_cards||[]).length;

  const pocketTiles = (d.pockets||[]).map((p,i)=>{
    const emoji = p.emoji || "🐷";
    const tgt = p.target_amount;
    const hasGoal = tgt != null && tgt > 0;
    const pct = hasGoal ? Math.min(100, Math.round((p.balance||0)/tgt*100)) : 0;
    const barCls = (i%2===1) ? "neon" : "";
    const jn = escapeJsString(p.name);
    return `<div class="ov-pk" role="button" tabindex="0" onclick="openPocketHistory('${jn}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openPocketHistory('${jn}');}">
      <div class="ov-pk-top"><span class="ov-pk-ico">${phIcon(emoji)}</span><span>${esc(p.name)}</span></div>
      <div class="ov-pk-val"><span data-num="pk_${esc(p.name)}" data-val="${p.balance||0}">${fmt(p.balance||0)}</span></div>
      ${hasGoal
        ? `<div class="ov-pk-bar"><i class="${barCls}" style="width:${pct}%"></i></div><div class="ov-pk-goal">${pct}% de ${fmt(tgt)}</div>`
        : `<div class="ov-pk-goal">Depósitos livres</div>`}
    </div>`;
  }).join("");

  const cardTiles = (d.credit_cards||[]).map((c,i)=>{
    const due   = c.due_amount != null ? c.due_amount : Math.max(0, (c.total||0) - (c.paid_amount||0));
    const total = c.total || 0;
    const paid  = c.paid_amount || 0;
    const hasData = total > 0 || paid > 0;
    const periodLabel = c.period_label || '';
    let statusCls = 's-empty', statusTxt = 'Sem fatura';
    if (c.status === 'paid') { statusCls = 's-paid'; statusTxt = 'Paga'; }
    else if (c.status === 'overdue' || c.status === 'vencida') { statusCls = 's-due'; statusTxt = 'Vencida'; }
    else if (hasData) { statusCls = 's-open'; statusTxt = 'Em aberto'; }
    const showProgress = paid > 0 && total > 0 && c.status !== 'paid';
    const pctPaid = showProgress ? Math.min(100, Math.round((paid/total)*100)) : 0;
    const dueLbl = c.due_day ? `Vence dia ${c.due_day}` : (c.closing_day ? `Fecha dia ${c.closing_day}` : '');
    const barCls = (i%2===1) ? "neon" : "";
    const clickAttr = c.id ? ` role="button" tabindex="0" data-card-id="${c.id}" data-open-bill-id="${c.bill_id || ''}" onclick="onCardRowClick(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();onCardRowClick(this);}"` : '';
    return `<div class="ov-pk ov-cc"${clickAttr}>
      <div class="ov-pk-top"><span class="ov-pk-ico"><i class="ph ph-credit-card" aria-hidden="true"></i></span><span class="ov-cc-name">${esc(c.name)}</span><span class="ov-cc-status ${statusCls}">${statusTxt}</span></div>
      <div class="ov-cc-sub">${hasData ? (periodLabel ? `Fatura ${periodLabel}` : 'Fatura atual') : 'Sem fatura aberta'}</div>
      <div class="ov-pk-val"><span data-num="cc_${esc(c.name)}" data-val="${due}">${hasData ? fmt(due) : 'R$ —'}</span></div>
      ${showProgress ? `<div class="ov-pk-bar"><i class="${barCls}" style="width:${pctPaid}%"></i></div>` : ''}
      <div class="ov-pk-goal">${[dueLbl, showProgress ? `${pctPaid}% pago` : ''].filter(Boolean).join(' · ') || '—'}</div>
    </div>`;
  }).join("");

  const cardActions = `
    <button class="hbtn hbtn-pink" type="button" onclick="openCardModal()" style="font-size:.7rem;padding:4px 10px;min-height:28px">+ Novo</button>
    <button class="btn-pay-bill" type="button" onclick="openPayBillModal()"><i class="ph ph-credit-card" aria-hidden="true"></i> Pagar fatura</button>`;

  const pocketActions = `
    <button class="hbtn hbtn-pink" type="button" onclick="openGoalEditModal()" style="font-size:.7rem;padding:4px 10px;min-height:28px">+ Nova</button>`;

  // Poucos itens dos dois lados → mescla numa faixa só; senão, seções separadas.
  const mergeStrips = nPk > 0 && nCc > 0 && (nPk + nCc) <= 4;
  let stripsHtml;
  if (mergeStrips) {
    stripsHtml = `
    <div class="ov-section-head-merged">
      <div class="ov-merge-half">
        <div class="ov-section-lbl" style="margin:0">Caixinhas</div>
        <div class="ov-section-actions">${pocketActions}</div>
      </div>
      <div class="ov-merge-half">
        <div class="ov-section-lbl" style="margin:0">Cartões</div>
        <div class="ov-section-actions">${cardActions}</div>
      </div>
    </div>
    <div class="ov-pockets">${pocketTiles}${cardTiles}</div>`;
  } else {
    stripsHtml = `
    ${nPk ? `<div class="ov-section-head">
      <div class="ov-section-lbl" style="margin:0">Caixinhas</div>
      <div class="ov-section-actions">${pocketActions}</div>
    </div>
    <div class="ov-pockets ov-strip-capped">${pocketTiles}</div>` : ""}
    ${nCc ? `<div class="ov-section-head">
      <div class="ov-section-lbl" style="margin:0">Cartões</div>
      <div class="ov-section-actions">${cardActions}</div>
    </div>
    <div class="ov-pockets ov-cc-grid ov-strip-capped">${cardTiles}</div>` : ""}`;
  }

  const svgWallet  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v3m0 4v3a1 1 0 0 1-1 1H5a2 2 0 0 1-2-2V5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/></svg>';
  const svgTrend   = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 7 13.5 15.5l-4-4L2 19"/><path d="M16 7h6v6"/></svg>';
  const svgReceipt = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 2v20l2-1.5L8 22l2-1.5L12 22l2-1.5L16 22l2-1.5L20 22V2l-2 1.5L16 2l-2 1.5L12 2l-2 1.5L8 2 6 3.5 4 2Z"/><path d="M8 7h8M8 11h8M8 15h5"/></svg>';
  const svgIncome  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="m19 12-7 7-7-7"/></svg>';

  grid.innerHTML = `
    <div class="ov-wrap">
    <div class="ov-stats">
      <div class="ov-stat" style="animation-delay:0ms">
        <div class="ov-ico">${svgWallet}</div>
        <div class="ov-lbl">Saldo atual${hist?' (do mês)':''}</div>
        <div class="ov-val"><span data-num="balance" data-val="${saldoAtual}">${fmt(saldoAtual)}</span></div>
        ${hasBanks
          ? `<div class="ov-delta"><i class="ph ph-wallet" aria-hidden="true"></i> Carteira <b style="color:var(--text-2)">${fmt(carteira)}</b> · <i class="ph ph-bank" aria-hidden="true"></i> Bancos <b style="color:var(--text-2)">${fmt(ofBank)}</b> · <button type="button" class="ov-adjust-lnk" onclick="openAdjustWalletModal()">ajustar</button></div>
             <div class="ov-delta" style="opacity:.8">Patrimônio total <b style="color:var(--text-2)"><span data-num="pat" data-val="${pat}">${fmt(pat)}</span></b></div>`
          : `<div class="ov-delta">Patrimônio total <b style="color:var(--text-2)"><span data-num="pat" data-val="${pat}">${fmt(pat)}</span></b></div>`}
      </div>
      <div class="ov-stat ov-stat-clickable" style="animation-delay:60ms" role="button" tabindex="0" aria-label="${escapeHtmlSafe((sav>=0?'Sobrou este mês':'Déficit do mês') + ': ' + fmt(sav) + '. ' + savDeltaTxt + '. Toque para ver como este valor foi calculado.')}" onclick="openSobrouDetail()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openSobrouDetail();}">
        <div class="ov-ico neon">${svgTrend}</div>
        <div class="ov-lbl">${sav>=0?'Sobrou este mês':'Déficit do mês'} <i class="ph ph-info ov-lbl-info" aria-hidden="true"></i></div>
        <div class="ov-val ${sav>=0?'pos':'neg'}"><span data-num="sav" data-val="${sav}">${fmt(sav)}</span></div>
        <div class="ov-delta ${savDeltaCls}">${savDeltaTxt}</div>
      </div>
      <div class="ov-stat" style="animation-delay:120ms">
        <div class="ov-ico">${svgReceipt}</div>
        <div class="ov-lbl">Gastos do mês</div>
        <div class="ov-val"><span data-num="exp" data-val="${exp}">${fmt(exp)}</span></div>
        <div class="ov-delta"><i class="ph ph-diamond" aria-hidden="true"></i> Aportes <b style="color:var(--text-2)"><span data-num="apt" data-val="${apt}">${fmt(apt)}</span></b></div>
      </div>
      <div class="ov-stat" style="animation-delay:180ms">
        <div class="ov-ico neon">${svgIncome}</div>
        <div class="ov-lbl">Receitas do mês</div>
        <div class="ov-val"><span data-num="inc" data-val="${inc}">${fmt(inc)}</span></div>
        <div class="ov-delta up"><i class="ph ph-trend-up" aria-hidden="true"></i> entradas de ${PT_MONTHS[rm-1]}</div>
      </div>
    </div>
    ${stripsHtml}
    <div class="ov-cards">

    <div class="card" style="animation-delay:180ms">
      <h2>Categorias (mês)</h2>
      ${!(d.expense_categories||[]).length?'<div class="empty">Sem despesas este mês.</div>':
        (() => {
          const mx = Math.max(...d.expense_categories.map(c=>c.total));
          return d.expense_categories.map((c,i)=>{
            const hb  = c.budget != null;
            const bw  = hb ? Math.min(c.budget_pct,100) : Math.round(c.total/mx*100);
            const bc  = hb ? (c.budget_pct>100?"var(--red)":c.budget_pct>85?"var(--yellow)":"var(--green)") : catColors()[i%catColors().length];
            const catSafe = escapeJsString(c.categoria);
            return `<div class="cat-row">
              <div class="cat-hdr">
                <span class="cat-lbl">${c.categoria==="sem categoria"?"<i class='ph ph-warning' aria-hidden='true'></i> Sem Categoria":esc(c.categoria)}</span>
                <span class="cat-val" data-num="cat_${esc(c.categoria)}" data-val="${c.total}">${fmt(c.total)}</span>
                <button class="bgt-btn" onclick="openBudget('${catSafe}')" title="Definir limite de orçamento"><i class="ph ph-pencil-simple" aria-hidden="true"></i></button>
              </div>
              ${hb?`<div class="cat-budget-info">${c.budget_pct}% de ${fmt(c.budget)}</div>`:""}
              <div class="bar-wrap"><div class="bar-fill" style="width:${bw}%;background:${bc}"></div></div>
            </div>`;
          }).join("");
        })()
      }
    </div>

    <div class="card" style="animation-delay:300ms">
      <h2>Aportes do mês</h2>
      ${(() => {
        const a = d.monthly_allocations || {investments:{total:0,by_target:[]}, pockets:{total:0,by_target:[]}};
        const inv = a.investments || {total:0,by_target:[]};
        const pkt = a.pockets     || {total:0,by_target:[]};
        const totalAll = (inv.total || 0) + (pkt.total || 0);
        if (!totalAll) return '<div class="empty">Sem aportes este mês.</div>';

        const mkBlock = (title, item, color) => {
          if (!item.total) return '';
          const tgts = item.by_target.slice(0, 6).map(t => `
            <div class="aporte-row">
              <span class="aporte-name">${esc(t.alvo)}</span>
              <span class="aporte-val">${fmt(t.total)}</span>
            </div>`).join("");
          const more = item.by_target.length > 6 ? `<div class="aporte-more">+${item.by_target.length - 6} outros</div>` : '';
          return `
            <div class="aporte-block">
              <div class="aporte-head">
                <span class="aporte-bucket" style="color:${color}">${title}</span>
                <span class="aporte-total" style="color:${color}">${fmt(item.total)}</span>
              </div>
              ${tgts}${more}
            </div>`;
        };

        return `
          <div class="aporte-summary">
            <div class="aporte-sum-k">Total alocado</div>
            <div class="aporte-sum-v">${fmt(totalAll)}</div>
          </div>
          ${mkBlock("Investimentos", inv, "var(--blue)")}
          ${mkBlock("Caixinhas",     pkt, "var(--purple)")}
          <div class="aporte-foot">Não conta como despesa: é alocação de patrimônio.</div>
        `;
      })()}
    </div>
    </div>
    </div>
  `;

  const _oh = document.getElementById("overview-heading");
  if (_oh) _oh.textContent = `Visão geral · ${PT_MONTHS[rm-1]} ${ry}`;

  // Show launches section
  document.getElementById("launches-title").style.display = "";
  document.getElementById("launches-wrap").style.display  = "";
  renderLaunches();
  renderAlerts(d.alerts || []);

  // Charts — use setTimeout so the browser fully lays out the
  // newly-visible container before Chart.js reads canvas dimensions
  document.getElementById("charts-title").style.display = "";
  document.getElementById("charts-grid").style.display  = "";
  setTimeout(() => {
    if ((d.expense_categories||[]).length) buildCatChart(d.expense_categories);
    // Gráfico de evolução: janela rolante via /expenses/daily (7D/30D/3M).
    loadExpenseChart(_expensePeriod);
  }, 50);

  requestAnimationFrame(animateCounters);

  document.getElementById("last-update").textContent =
    "Última atualização: " + fmtDate(d.timestamp);
  renderInvestmentsPanel(d);
  runInvestmentSimulator();

  // Re-aplicar gates Pro nos elementos recem-renderizados (ex: card de
  // investimentos da visao geral, criado via innerHTML).
  applyProGates();

  // Reinicializa drag-to-reorder na wallet stack (recriada por innerHTML).
  setupCardWalletSort();
}

/* ═══════════════════════════════════════════════════════════════════════
   PROGRAMA DE AFILIADOS — aba só aparece pra quem é afiliado
═══════════════════════════════════════════════════════════════════════ */
let _affiliateCache = null;
const _affiliateChannel = makeFetchChannel(); // dedup + abort + geração

async function _fetchAffiliate({ force = false } = {}) {
  return _affiliateChannel.run(async (signal) => {
    const res = await fetch(`${API}/api/affiliate/me`, { credentials: "same-origin", signal });
    const data = await readResponsePayload(res);
    if (!res.ok) throw new Error(data.detail || "Não foi possível carregar seus dados de afiliado.");
    return data;
  }, { force });
}

// Chamado no init: se o user é afiliado, mostra o item "Afiliados" no sidenav.
async function initAffiliateNav() {
  try {
    const res = await fetch(`${API}/api/affiliate/me`, { credentials: "same-origin" });
    if (!res.ok) return; // 404 = não é afiliado
    _affiliateCache = await res.json();
    const item = document.getElementById("sidenav-affiliate");
    if (item) item.style.display = "";
  } catch {}
}

async function loadAffiliateView(forceFresh = false, { background = false } = {}) {
  const stats = document.getElementById("affiliate-stats");
  const body = document.getElementById("affiliate-body");
  if (!stats || !body) return;

  // Puxão: sem skeleton, fetch antes de render, falha real rejeita sem tocar
  // DOM (o body — e a chave Pix digitada nele — fica como está, indicador âmbar).
  // O _renderAffiliateView reconstrói o input Pix, então lê-se o valor VIVO no
  // último instante antes do render e restaura-se depois (o campo segue editável
  // durante o fetch; um snapshot tirado antes descartaria o que foi digitado).
  // Antes esse caminho vivia inline no _pbDashboardRefresh; agora mora aqui, no
  // canal, junto com os outros loaders.
  if (background) {
    const data = await _fetchAffiliate({ force: true });
    if (data === undefined) return;
    const pix = document.getElementById("affiliate-pix-input");
    const pending = pix ? pix.value : "";
    _affiliateCache = data;
    _renderAffiliateView(data);
    if (pending) {
      const el = document.getElementById("affiliate-pix-input");
      if (el) el.value = pending;
    }
    return;
  }

  if (_affiliateCache && !forceFresh) {
    _renderAffiliateView(_affiliateCache);
  } else {
    stats.innerHTML = `
      <div class="stat-tile"><div class="stat-label">Indicados</div><div class="sk sk-h2"></div></div>
      <div class="stat-tile"><div class="stat-label">Disponível</div><div class="sk sk-h2"></div></div>
      <div class="stat-tile"><div class="stat-label">Em carência</div><div class="sk sk-h2"></div></div>
      <div class="stat-tile"><div class="stat-label">Já recebido</div><div class="sk sk-h2"></div></div>
    `;
    body.innerHTML = "";
  }

  try {
    const data = await _fetchAffiliate({ force: true });
    if (data === undefined) return;
    _affiliateCache = data;
    _renderAffiliateView(data);
  } catch (err) {
    body.innerHTML = `<div class="empty" style="grid-column:1/-1;padding:30px;color:var(--red)">Erro: ${esc(String(err.message || err))}</div>`;
  }
}

function _affiliateCommissionStatus(c) {
  if (c.status === "reversed") return { label: "estornada", color: "var(--red)" };
  if (c.status === "paid") return { label: "paga", color: "var(--green)" };
  if (c.payout_id) return { label: "em saque", color: "var(--blue)" };
  if (new Date(c.available_at) <= new Date()) return { label: "disponível", color: "var(--green)" };
  const d = new Date(c.available_at).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  return { label: `libera ${d}`, color: "var(--text-3)" };
}

function _renderAffiliateView(data) {
  const stats = document.getElementById("affiliate-stats");
  const body = document.getElementById("affiliate-body");
  if (!stats || !body) return;

  const s = data.stats || {};
  stats.innerHTML = `
    <div class="stat-tile" style="animation-delay:0ms">
      <div class="stat-label">Indicados</div>
      <div class="stat-value">${Number(s.referrals || 0)}</div>
      <div class="stat-delta" style="color:var(--text-3)">cadastros pelo seu link</div>
    </div>
    <div class="stat-tile" style="animation-delay:60ms">
      <div class="stat-label">Disponível pra saque</div>
      <div class="stat-value" style="color:var(--green)">${fmt(s.available || 0)}</div>
      <div class="stat-delta" style="color:var(--text-3)">mínimo ${fmt(data.min_payout || 50)}</div>
    </div>
    <div class="stat-tile" style="animation-delay:120ms">
      <div class="stat-label">Em carência</div>
      <div class="stat-value">${fmt(s.held || 0)}</div>
      <div class="stat-delta" style="color:var(--text-3)">libera 30 dias após a cobrança</div>
    </div>
    <div class="stat-tile" style="animation-delay:180ms">
      <div class="stat-label">Já recebido</div>
      <div class="stat-value">${fmt(s.paid || 0)}</div>
      <div class="stat-delta" style="color:var(--text-3)">${s.requested ? fmt(s.requested) + " em saque pendente" : "comissão de " + Number(data.commission_percent || 10) + "% por cobrança"}</div>
    </div>
  `;

  // Lista compacta: mostra as N mais recentes; o resto fica atrás de
  // "Mostrar todas" (o backend já limita em 50 comissões / 20 saques).
  const AFF_LIST_VISIBLE = 8;
  const _collapsibleList = (rowsHtmlArr, keyId, visible = AFF_LIST_VISIBLE) => {
    if (rowsHtmlArr.length <= visible) return rowsHtmlArr.join("");
    const head = rowsHtmlArr.slice(0, visible).join("");
    const rest = rowsHtmlArr.slice(visible).join("");
    return `${head}
      <div id="${keyId}" style="display:none">${rest}</div>
      <button class="mock-cta" type="button" style="margin-top:10px;font-size:.72rem"
        onclick="toggleAffiliateList('${keyId}', this, ${rowsHtmlArr.length - visible})">
        Mostrar todas (+${rowsHtmlArr.length - visible})</button>`;
  };

  const commissionRowsArr = (data.commissions || []).map(c => {
    const st = _affiliateCommissionStatus(c);
    return `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--glass-border)">
        <div>
          <div style="font-weight:600">${fmt(c.amount)}</div>
          <div style="font-size:.72rem;color:var(--text-3)">fatura de ${fmt(c.invoice_amount)} · ${fmtDate(c.created_at)}</div>
        </div>
        <span style="font-size:.75rem;color:${st.color}">${st.label}</span>
      </div>`;
  });
  const commissionRows = _collapsibleList(commissionRowsArr, "aff-comm-extra");

  const payoutRowsArr = (data.payouts || []).map(p => {
    const label = p.status === "paid" ? "pago" : (p.status === "rejected" ? "rejeitado" : "em análise");
    const color = p.status === "paid" ? "var(--green)" : (p.status === "rejected" ? "var(--red)" : "var(--blue)");
    // Rejeição: motivo em destaque (linha própria, em vermelho) — o saldo
    // voltou pro disponível, mas o afiliado precisa entender o porquê.
    const noteHtml = p.note ? `
          <div style="font-size:.75rem;margin-top:3px;color:${p.status === "rejected" ? "var(--red)" : "var(--text-3)"}">
            ${p.status === "rejected" ? '<i class="ph ph-x-circle" aria-hidden="true"></i> Motivo da rejeição: ' : '<i class="ph ph-chat-circle" aria-hidden="true"></i> '}${esc(p.note)}
          </div>` : "";
    return `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--glass-border)">
        <div>
          <div style="font-weight:600">${fmt(p.amount)}</div>
          <div style="font-size:.72rem;color:var(--text-3)">pedido em ${fmtDate(p.requested_at)}</div>${noteHtml}
        </div>
        <span style="font-size:.75rem;color:${color}">${label}</span>
      </div>`;
  });
  const payoutRows = _collapsibleList(payoutRowsArr, "aff-payout-extra", 5);

  const canRequest = Number(s.available || 0) >= Number(data.min_payout || 50) && !(data.payouts || []).some(p => p.status === "requested");

  body.innerHTML = `
    <div class="mock-card">
      <h3 style="margin:0 0 6px">Seu link de divulgação</h3>
      <p style="font-size:.78rem;color:var(--text-3);margin:0 0 12px">
        Quem se cadastrar por ele e assinar o PigBank+ gera ${Number(data.commission_percent || 10)}% de comissão
        pra você na primeira cobrança da assinatura.
      </p>
      <div style="display:flex;gap:8px;align-items:center">
        <input id="affiliate-link-input" type="text" readonly value="${esc(data.link)}"
          style="flex:1;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--glass-border);background:var(--glass-bg);color:var(--text);font-size:.82rem">
        <button class="mock-cta" type="button" onclick="copyAffiliateLink()">Copiar</button>
      </div>
      ${data.status !== "active" ? '<p style="font-size:.75rem;color:var(--red);margin:10px 0 0">Seu cadastro de afiliado está desativado. O link não gera novas comissões.</p>' : ""}

      <h3 style="margin:18px 0 6px">Solicitar saque</h3>
      <p style="font-size:.78rem;color:var(--text-3);margin:0 0 10px">
        Saque mínimo de ${fmt(data.min_payout || 50)}. O pagamento é feito por Pix em até alguns dias úteis.
      </p>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input id="affiliate-pix-input" type="text" placeholder="Sua chave Pix (CPF, email, celular...)"
          style="flex:1;min-width:180px;padding:10px 12px;border-radius:10px;border:1px solid var(--glass-border);background:var(--glass-bg);color:var(--text);font-size:.82rem">
        <button class="mock-cta" type="button" id="affiliate-payout-btn" onclick="requestAffiliatePayout()" ${canRequest ? "" : "disabled style='opacity:.5;cursor:not-allowed'"}>
          Sacar ${fmt(s.available || 0)}
        </button>
      </div>
    </div>

    <div class="mock-card">
      <h3 style="margin:0 0 10px">Comissões</h3>
      <div class="tx-list">${commissionRows || '<div style="padding:16px 0;color:var(--text-3);font-size:.8rem">Nenhuma comissão ainda. Divulgue seu link! <i class="ph ph-piggy-bank" aria-hidden="true"></i></div>'}</div>
      <h3 style="margin:18px 0 10px">Saques</h3>
      <div class="tx-list">${payoutRows || '<div style="padding:16px 0;color:var(--text-3);font-size:.8rem">Nenhum saque solicitado.</div>'}</div>
    </div>
  `;
}

function toggleAffiliateList(keyId, btn, hiddenCount) {
  const el = document.getElementById(keyId);
  if (!el) return;
  const showing = el.style.display !== "none";
  el.style.display = showing ? "none" : "";
  if (btn) btn.textContent = showing ? `Mostrar todas (+${hiddenCount})` : "Mostrar menos";
}

async function copyAffiliateLink() {
  const input = document.getElementById("affiliate-link-input");
  if (!input) return;
  try {
    await navigator.clipboard.writeText(input.value);
    showToast("✓ Link copiado");
  } catch {
    input.select();
    document.execCommand("copy");
    showToast("✓ Link copiado");
  }
}

async function requestAffiliatePayout() {
  const pixInput = document.getElementById("affiliate-pix-input");
  const pixKey = (pixInput?.value || "").trim();
  if (pixKey.length < 5) {
    await alertModal("Informe sua chave Pix pra receber o pagamento.", { title: "Saque" });
    return;
  }
  const btn = document.getElementById("affiliate-payout-btn");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`${API}/api/affiliate/payout`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ pix_key: pixKey }),
    });
    const data = await readResponsePayload(res);
    if (!res.ok) {
      await alertModal(data.detail || "Não foi possível solicitar o saque.", { title: "Saque" });
      return;
    }
    showToast("✓ Saque solicitado");
    loadAffiliateView(true);
  } catch (err) {
    await alertModal("Erro ao solicitar o saque. Tente de novo.", { title: "Saque" });
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ═══════════════════════════════════════════════════════════════════════
   INIT — valida token antes de conectar
═══════════════════════════════════════════════════════════════════════ */
function _showAccessError(title, msg) {
  document.body.style.cssText = "background:#111111;display:flex;align-items:center;justify-content:center;min-height:100vh;";
  document.body.innerHTML = `
    <div style="text-align:center;color:rgba(255,255,255,0.85);font-family:system-ui;max-width:400px;padding:40px">
      <div style="font-size:3rem;margin-bottom:16px"><i class="ph ph-lock" aria-hidden="true"></i></div>
      <h2 style="margin-bottom:8px;font-size:1.4rem;font-weight:600">${title || "Link inválido ou expirado"}</h2>
      <p style="color:rgba(255,255,255,0.5);line-height:1.6;margin-bottom:24px">
        ${msg || 'Solicite um novo link digitando <strong style="color:rgba(255,255,255,0.8)">dashboard</strong> no bot.'}
      </p>
      <a href="/" style="display:inline-block;padding:10px 24px;background:rgba(255,45,142,0.5);
        border:1px solid rgba(255,45,142,0.6);border-radius:12px;color:white;text-decoration:none;font-size:.9rem;">
        ← Ir para a página inicial
      </a>
    </div>`;
}

(async () => {
  const view = params.get("view");

  // Fail-closed ANTES de qualquer await: trava os controles pagos logo no
  // primeiro tick do bootstrap, antes de disparar/esperar /auth/validate e
  // /auth/me. Se qualquer um travar/pendurar, os controles que dependem do
  // interceptor de clique .pro-locked (export, gastos fixos, Novidades) não
  // ficam no estado destravado do HTML a visita inteira. USER_GATES começa {}
  // → tudo bloqueado até o /auth/dashboard-profile confirmar. Idempotente.
  applyProGates();

  // /auth/validate e /auth/me são independentes (ambos por cookie — o /me não
  // precisa do USER_ID). Disparamos os dois em paralelo pra cortar uma ida ao
  // servidor do caminho crítico de abertura. O .catch no /me evita rejeição
  // não tratada caso o validate falhe e a gente saia antes de consumi-lo.
  const validatePromise = fetch(`${API}/auth/validate`, { credentials: "same-origin" });
  const mePromise = fetch(`${API}/auth/me`, { credentials: "same-origin" }).catch(() => null);

  try {
    const resp = await validatePromise;
    if (!resp.ok) { _showAccessError(); return; }
    const data = await resp.json();
    USER_ID = data.user_id;
  } catch(e) {
    _showAccessError();
    return;
  }

  WS_URL = `${BASE_WS}/ws/${USER_ID}`;

  // Paywall: o boot NÃO espera mais o /auth/me pra conectar — com o USER_ID em
  // mãos, connect()/menu/afiliados saem já (corta ~1 RTT do caminho crítico).
  // O bloco do /me virou .then: redirects/gates aplicam quando ele chegar.
  // (As rotas de dados também devolvem 402 como reforço server-side.)
  // Resolve `false` quando a tela vai embora (paywall/erro) — o deep-link de
  // ?view espera esse veredito antes de navegar.
  const meGate = mePromise.then(async (meResp) => {
    try {
      if (meResp && meResp.ok) {
        const me = await meResp.json();
        historyEarliestDate = me?.history_earliest_date || null;
        updateMonthLabel();
        // Beta dos Agentes: fora do allowlist, a nav some (a API também dá 404).
        if (me && me.agents_ui_enabled === false) {
          document.querySelectorAll('[data-nav="agentes"]').forEach(el => { el.style.display = "none"; });
        }
        // Gate de escolha de plano: cadastro novo passa pela /precos e assina um
        // plano pago antes de acessar o app (o Grátis não é mais uma escolha
        // oferecida na /precos). Só na web — no app iOS o gate fica de fora pra
        // não forçar a tela de planos/compra (diretriz 3.1.1).
        // Paywall/escolha de plano: mesmo veredito da revalidação por WS
        // rejeitado (applyAccessVerdict já limpa snapshot e para o retry).
        if (!applyAccessVerdict(me)) return false;
        // Banner de trial (B1): oferta dos 15d de Plus pro Grátis sem trial ativo.
        // Nunca no app iOS (CTA de compra externa fere a diretriz 3.1.1 da Apple).
        if (me && me.plan_tier === "free" && !(me.trial && me.trial.active) && !window.PB_IN_APP) {
          maybeShowTrialBanner();
        }
      }
    } catch (e) { /* se /auth/me falhar, segue; o 402 protege os dados */ }
    // Puxar pra atualizar: o contrato só nasce com o paywall vencido — nos
    // returns acima ele nunca é registrado e o puxão nessas telas cai no
    // reload, que é o que elas pedem (mesma regra de antes, agora async).
    window.PBRefresh = _pbDashboardRefresh;
    return true;
  });

	  updateInvestmentRateHint();
	  updateInvestmentTaxHint();
	  updateMonthLabel();
	  // Paint instantâneo: se há snapshot do mês corrente guardado na sessão
	  // (troca de aba /home <-> /app), pinta a Visão Geral AGORA, sem esperar o
	  // WebSocket. O connect() logo abaixo revalida e substitui pelos dados frescos.
	  restoreSnapshotFromSession();
	  // Dispara WS connect IMEDIATAMENTE (não espera /auth/dashboard-profile).
	  // Antes era serial: validate → profile → connect. Agora profile + connect
	  // rodam em paralelo, cortando ~3.5s do carregamento inicial.
	  // O if (view === "investments") precisa do USER_PLAN — defere pra depois
	  // do profile resolver.
	  connect();
	  Promise.all([loadUserMenuState(), meGate]).then(([, meOk]) => {
	    // O deep-link espera o veredito do /me (meGate): antes do O1-4 o /me
	    // resolvia inteiro antes do loadUserMenuState, então o check de
	    // visibilidade abaixo nunca corria com os beta-gates — preservado.
	    if (!meOk) return; // paywall/erro: a tela está indo embora, não navega
	    // Deep-link por ?view=X (gaveta da /home aponta pra cá). investments
	    // mantém o caminho antigo (setMainView direto); overview é o default.
	    // Os demais abrem via navigateTo — mas só se o item do sidenav existir
	    // e estiver visível, respeitando os beta-gates (ex.: agents_ui_enabled)
	    // e o pro-gate, igual ao clique manual.
	    // affiliate fica de fora: seu item (#sidenav-affiliate) só vira visível
	    // depois do initAffiliateNav() (fire-and-forget, abaixo), então o check
	    // de visibilidade correria com ele. A gaveta da /home não linka affiliate,
	    // e ele nunca foi deep-linkável por URL — manter fora não regride nada.
	    if (view === "investments") setMainView("investments");
	    else if (view && view !== "overview" && view !== "affiliate") {
	      const navEl = document.querySelector(`[data-nav="${view}"]`);
	      if (navEl && navEl.style.display !== "none") navigateTo(view);
	    }
	  });
	  // Afiliados: mostra o item do sidenav se o user for afiliado (não bloqueante).
	  initAffiliateNav();
	  // fetchHistory() removido daqui — connect() já chama via ws.onopen.
	})();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js")
      .then(r => console.log("[PWA] SW registered:", r.scope))
      .catch(e => console.warn("[PWA] SW failed:", e));
  });
}

/* ═══════════════════════════════════════════════════════════════════════
   AGENTES DO PIGGY — prateleira, ativação e feed de disparos
   ═══════════════════════════════════════════════════════════════════════ */
let _agentesCache = null;
const _agentesChannel = makeFetchChannel(); // dedup + abort + geração (shelf+feed, 1 signal)

async function _fetchAgentes({ force = false } = {}) {
  return _agentesChannel.run(async (signal) => {
    const [shelfRes, feedRes] = await Promise.all([
      fetch(`${API}/agents/${USER_ID}`, { credentials: "same-origin", signal }),
      fetch(`${API}/agents/${USER_ID}/feed?limit=20`, { credentials: "same-origin", signal }),
    ]);
    const data = await readResponsePayload(shelfRes);
    if (!shelfRes.ok) throw new Error(data.detail || "Não foi possível carregar os agentes.");
    const feed = await readResponsePayload(feedRes);
    data.events = feedRes.ok ? (feed.events || []) : [];
    return data;
  }, { force });
}

// Marca o feed como lido (fire-and-forget; não bloqueia nem falha a view).
function _markAgentesFeedSeen() {
  fetch(`${API}/agents/${USER_ID}/feed/seen`, {
    method: "POST", credentials: "same-origin", headers: csrfHeaders(),
  }).catch(() => {});
}

async function loadAgentesView(forceFresh = false, { background = false } = {}) {
  const shelf = document.getElementById("agentes-shelf");
  const feedEl = document.getElementById("agentes-feed");
  if (!shelf) return;

  // Puxão: sem "Chamando os porquinhos…", fetch antes de render, falha real
  // rejeita sem tocar DOM (indicador âmbar). Superado sai neutro.
  if (background) {
    const data = await _fetchAgentes({ force: true });
    if (data === undefined) return;
    _agentesCache = data;
    _renderAgentes(data);
    _markAgentesFeedSeen();
    return;
  }

  if (_agentesCache && !forceFresh) {
    _renderAgentes(_agentesCache);
  } else {
    shelf.innerHTML = `<div class="empty" style="grid-column:1/-1;padding:26px">Chamando os porquinhos… <i class="ph ph-piggy-bank" aria-hidden="true"></i></div>`;
    if (feedEl) feedEl.innerHTML = "";
  }

  try {
    const data = await _fetchAgentes({ force: true });
    if (data === undefined) return;
    _agentesCache = data;
    _renderAgentes(data);
    _markAgentesFeedSeen();
  } catch (err) {
    shelf.innerHTML = `<div class="empty" style="grid-column:1/-1;padding:30px;color:var(--red)">Erro: ${esc(String(err.message || err))}</div>`;
  }
}

function _renderAgentes(data) {
  const counters = document.getElementById("agentes-counters");
  const shelf = document.getElementById("agentes-shelf");
  const feedEl = document.getElementById("agentes-feed");
  if (!shelf) return;

  const s = data.summary || {};
  // Modelo de energia só vale com a escada v2 ligada (energy_enabled). Com v2 off
  // (freio de emergência) o gate legado decide e a UI não mostra medidor nem
  // trava por energia — senão travaria botões que o backend legado aceitaria.
  const energyOn = data.energy_enabled === true;
  const budget = Number(data.energy_budget || 0);
  const used = Number(data.energy_used || 0);
  if (counters) {
    counters.innerHTML = `
      <span class="ag-counter"><i class="ag-dot ag-dot-on"></i> Ativos <b>${s.ativos || 0}</b></span>
      <span class="ag-counter"><i class="ag-dot ag-dot-off"></i> Pausados <b>${s.pausados || 0}</b></span>
      <span class="ag-counter"><i class="ag-dot ag-dot-fire"></i> Disparos <b>${s.disparos_mes || 0}</b></span>
      ${energyOn ? _energyMeter(used, budget) : ""}
    `;
  }

  shelf.innerHTML = (data.catalog || []).map(card => {
    const active = card.status === "active";
    const cost = Number(card.energy_cost || 0);
    const chips = [
      `<span class="ag-chip">${esc(card.freq)}</span>`,
      (energyOn && card.disponivel && cost > 0) ? `<span class="ag-chip ag-chip-energy"><i class="ph ph-lightning" aria-hidden="true"></i> ${cost}</span>` : "",
    ].filter(Boolean).join("");
    // can_activate vem do backend (Grátis/Essencial: orçamento 0 → sem agentes).
    // Gate visível: o botão vira cadeado que abre o upgrade direto.
    const canActivate = data.can_activate !== false;
    // Energia: com plano, todos os agentes ficam liberados, mas só ativa quem
    // ainda cabe no orçamento. Com v2 off (energyOn false), nunca trava por aqui.
    const affordable = !energyOn || (used + cost <= budget);
    const btn = !card.disponivel
      ? `<button class="ag-btn ag-btn-soon" disabled>Em breve</button>`
      : active
        ? `<button class="ag-btn ag-btn-active" onclick="pauseAgent('${card.kind}')"><i class="ph ph-check" aria-hidden="true"></i> Ativo · Pausar</button>`
        : !canActivate
          ? `<button class="ag-btn ag-btn-on" onclick="showUpgradeModal('agents')"><i class="ph ph-lock" aria-hidden="true"></i> Ativar</button>`
          : affordable
            ? `<button class="ag-btn ag-btn-on" onclick="activateAgent('${card.kind}')">Ativar${energyOn && cost > 0 ? ` · <i class="ph ph-lightning" aria-hidden="true"></i> ${cost}` : ""}</button>`
            : `<button class="ag-btn ag-btn-noenergy" disabled title="Pause um agente ou vá pro Pro"><i class="ph ph-lightning-slash" aria-hidden="true"></i> Sem energia</button>`;
    // Opt-out por agente: quando ativo, deixa ligar/desligar o e-mail (o feed
    // continua). Padrão = ligado. Estilo inline pra não exigir bump de cache CSS.
    const emailOn = ((card.config || {}).email_enabled) !== false;
    const emailToggle = (active && card.disponivel)
      ? `<button onclick="toggleAgentEmail('${card.kind}', ${emailOn ? "false" : "true"})"
           title="Receber os avisos deste agente por e-mail"
           style="margin-top:8px;width:100%;padding:7px 10px;border-radius:9px;border:1px solid rgba(255,255,255,.12);background:transparent;color:rgba(255,255,255,.6);font-size:.72rem;cursor:pointer">
           <i class="ph ph-envelope" aria-hidden="true"></i> E-mail: <b style="color:${emailOn ? "#22c55e" : "rgba(255,255,255,.4)"}">${emailOn ? "ligado" : "desligado"}</b>
         </button>`
      : "";
    return `
      <div class="ag-card${!card.disponivel ? " ag-card-soon" : ""}">
        <div class="ag-avatar ag-bg-${esc(card.kind)}">
          ${_agentArt(card.kind, true)}
        </div>
        <h3>${esc(card.nome)}</h3>
        <p class="ag-desc">${esc(card.desc)}</p>
        <div class="ag-chips">${chips}</div>
        ${btn}
        ${emailToggle}
      </div>
    `;
  }).join("");

  if (feedEl) {
    const events = data.events || [];
    feedEl.innerHTML = events.length === 0
      ? `<div class="empty" style="padding:22px">Nenhum disparo ainda. Ative um agente e ele fala assim que tiver algo que vale a pena. <i class="ph ph-piggy-bank" aria-hidden="true"></i></div>`
      : events.map(ev => {
          const p = ev.payload || {};
          return `
            <div class="ag-event${ev.seen_at ? "" : " ag-event-new"}">
              <div class="ag-event-face ag-bg-${esc(ev.kind)}">
                ${_agentArt(ev.kind)}
              </div>
              <div class="ag-event-body">
                <p class="ag-event-msg">${esc(p.mensagem || p.titulo || "Disparo")}</p>
                <p class="ag-event-when">${esc(_agentName(ev.kind))} · ${fmtDate(ev.fired_at)}${ev.channel === "email" ? " · <i class='ph ph-envelope' aria-hidden='true'></i> no seu e-mail" : ""}</p>
              </div>
            </div>
          `;
        }).join("");
  }
}

// Kinds com arte PNG real em /brand/agents/. Kind fora deste set cai no
// porquinho SVG placeholder — nada quebra até a arte chegar.
const _AGENT_ART = new Set(["xerife", "reporter", "carteiro", "detetive", "cofre", "barao", "aviador", "faria_limer"]);
function _agentArt(kind, hero = false) {
  if (_AGENT_ART.has(kind)) {
    // hero = a cena cinematográfica do e-mail ({kind}_hero.png, 1200x600),
    // usada no card (object-fit:cover preenche o avatar). Sem hero = o sticker
    // (usado no medalhão pequeno do feed).
    if (hero)
      return `<img src="/brand/agents/${esc(kind)}_hero.png?v=1" alt="" loading="lazy"`
        + ` style="width:100%;height:100%;object-fit:cover;object-position:center;display:block" />`;
    return `<img class="ag-pig-img" src="/brand/agents/${esc(kind)}.png?v=3" alt="" loading="lazy" />`;
  }
  return `<svg viewBox="0 6 120 114" aria-hidden="true"><use href="#ag-pig-${esc(kind)}"/></svg>`;
}

function _agentName(kind) {
  const card = ((_agentesCache || {}).catalog || []).find(c => c.kind === kind);
  return card ? card.nome : kind;
}

// Medidor de energia do plano: pips preenchidos = energia usada. Só aparece
// pra quem tem orçamento (Plus/Pro); Grátis/Essencial (0) não veem barra.
function _energyMeter(used, budget) {
  if (!budget || budget <= 0) return "";
  const over = used > budget;
  let pips = "";
  for (let i = 0; i < budget; i++) {
    const on = i < used;
    pips += `<i class="ag-pip${on ? (over ? " ag-pip-over" : " ag-pip-on") : ""}"></i>`;
  }
  const rem = budget - used;
  const hint = over ? "acima do plano" : (rem > 0 ? `${rem} sobrando` : "cheio");
  return `<span class="ag-energy${over ? " ag-energy-over" : ""}">
    <span class="ag-energy-label"><i class="ph ph-lightning" aria-hidden="true"></i> Energia <b>${used}/${budget}</b></span>
    <span class="ag-pips">${pips}</span>
    <span class="ag-energy-hint">${hint}</span>
  </span>`;
}

async function activateAgent(kind) {
  try {
    const res = await fetch(`${API}/agents/${USER_ID}/${kind}/activate`, {
      method: "POST", credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({}),
    });
    const data = await readResponsePayload(res);
    const detail = data.detail || {};
    if (res.status === 403 && detail.error === "pro_required") {
      showUpgradeModal("agents");
      return;
    }
    if (res.status === 403 && detail.error === "no_energy") {
      alert("⚡ Sem energia no seu plano pra ativar mais esse agente. Pause um que você usa menos, ou vá pro Pro pra ter energia pra todos.");
      return;
    }
    if (!res.ok) throw new Error((data.detail && data.detail.error) || data.detail || "Não deu pra ativar o agente.");
    loadAgentesView(true);
  } catch (err) {
    alert(String(err.message || err));
  }
}

async function pauseAgent(kind) {
  try {
    const res = await fetch(`${API}/agents/${USER_ID}/${kind}/pause`, {
      method: "POST", credentials: "same-origin", headers: csrfHeaders(),
    });
    if (!res.ok) throw new Error("Não deu pra pausar o agente.");
    loadAgentesView(true);
  } catch (err) {
    alert(String(err.message || err));
  }
}

async function toggleAgentEmail(kind, enabled) {
  try {
    const res = await fetch(`${API}/agents/${USER_ID}/${kind}/email`, {
      method: "POST", credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) throw new Error("Não deu pra mudar o e-mail do agente.");
    loadAgentesView(true);
  } catch (err) {
    alert(String(err.message || err));
  }
}

/* ═══════════════════════════════════════════════════════════════════════
   PUXAR PRA ATUALIZAR (app iOS / PWA)
   ═══════════════════════════════════════════════════════════════════════
   O gesto mora no app-mode.js — aqui só respondemos o que "atualizar"
   significa no dashboard: refazer a aba que está aberta, sem recarregar a
   página (o reload perderia filtro, mês escolhido e posição da rolagem).

   Devolve promise: o indicador do puxão só some quando ela resolve.

   Registrado no bootstrap (não aqui): expor na definição do script deixava
   um puxão precoce rodar com USER_ID=0 (/data/0) durante um launch lento —
   e seguir ativo depois do _showAccessError trocar o body inteiro. */
function _pbDashboardRefresh() {
  const active = DASH_VIEWS.find(v => {
    const el = document.getElementById(v + "-view");
    return el && el.classList.contains("active");
  }) || "overview";

  // Todos no MODO BACKGROUND: sem skeleton, fetch antes de render, e falha REAL
  // rejeita sem tocar no DOM (o render bom fica, o indicador do gesto vira
  // âmbar). Superado/abortado sai neutro (conta como sucesso). O canal
  // compartilhado (abort + geração) de cada loader garante que um pedido velho
  // não sobrescreva o novo.
  switch (active) {
    case "analytics":    return loadAnalyticsView(true, null, { background: true });
    case "history":      return loadHistoryView(true, { background: true });
    case "cards":        return loadCardsView(true, { background: true });
    case "installments": return loadInstallmentsView(true, { background: true });
    case "categories":   return loadCategoriesView(true, { background: true });
    case "budgets":      return loadBudgetsView(true, { background: true });
    // "Recorrentes" tem quatro abas internas e só uma está visível. Recarregar
    // sempre a de gastos deixaria a que o usuário está vendo parada, com o
    // indicador dando a entender que atualizou. Espelha o setRecurringTab.
    case "fixed":
      if (_recurringTab === "overview") return loadRecurringOverview({ background: true });
      if (_recurringTab === "incomes")  return loadRecurringIncomeView(true, { background: true });
      if (_recurringTab === "bills")    return loadBillsView(true, { background: true });
      return loadFixedView(true, { background: true });
    case "goals":        return loadGoalsView(true, { background: true });
    // Afiliado: o fetch-antes-de-render + preservação da chave Pix agora vive
    // dentro do loadAffiliateView (modo background), não mais inline aqui.
    case "affiliate":    return loadAffiliateView(true, { background: true });
    case "agentes":      return loadAgentesView(true, { background: true });
    default:
      // Visão geral e investimentos vivem do snapshot do mês.
      // preferHttp: com o WS aberto o pedido volta do cache dele — puxar pra
      // atualizar tem que ir na fonte, senão o gesto mente.
      // smoothScroll off: o usuário está no topo, jogar a página nos
      // lançamentos seria roubar o lugar dele.
      // false = a busca falhou (o render antigo ficou): rejeita pro indicador
      // ficar âmbar. undefined (pedido superado/stale) conta como sucesso.
      return fetchMonthHttp(viewYear, viewMonth, launchesPage, LAUNCHES_LIMIT)
        .then(ok => { if (ok === false) throw new Error("refresh do mês falhou"); });
  }
}
