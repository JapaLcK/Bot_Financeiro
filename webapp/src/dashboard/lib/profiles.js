// Perfis do Resumo. Um perfil é só o layout inicial do painel: a lista ordenada de
// blocos, com as contas no topo. O ajuste fino fica salvo por perfil, no navegador. `padrao`
// (o "Pular") é o painel de sempre, com a lista em parts/Board.tsx. O perfil escolhido mora
// no servidor (/api/v2/perfil); o salvo aqui só vale no protótipo, que não tem backend.
import { TIERS } from "./data.js";

export const PROFILES = [
  { id: "economizar", label: "Economizar", icon: "ph-piggy-bank",
    line: "Pra guardar mais todo mês. Mostra quanto sobrou, suas metas e onde dá pra cortar.",
    preset: ["contas", "resumo", "metas", "piggy", "categorias", "assinaturas", "simulador", "compromissos"] },
  { id: "investir", label: "Investir", icon: "ph-trend-up",
    line: "Pra fazer o dinheiro trabalhar. Mostra seu patrimônio, quanto rendeu perto do CDI e onde ele está.",
    preset: ["contas", "patrimonio", "rendimento", "wealth", "simulador", "metas", "resumo", "piggy"] },
  { id: "controlar", label: "Controlar gastos", icon: "ph-chart-bar",
    line: "Pra saber pra onde vai cada real. Mostra os gastos por categoria, dia a dia, e as contas que vêm aí.",
    preset: ["contas", "categorias", "calendario", "resumo", "compromissos", "assinaturas", "fatura", "piggy"] },
  { id: "dividas", label: "Sair das dívidas", icon: "ph-credit-card",
    line: "Pra pagar o que deve e respirar. Mostra a fatura, as parcelas que ainda vêm e os próximos vencimentos.",
    preset: ["contas", "parcelas", "fatura", "compromissos", "hero", "resumo", "categorias", "piggy"] },
  { id: "autonomo", label: "Autônomo", icon: "ph-briefcase",
    line: "Pra quem tem renda que muda todo mês. Mostra sua renda mês a mês, quanto a reserva segura e as contas fixas.",
    preset: ["contas", "renda", "hero", "resumo", "compromissos", "metas", "categorias", "piggy"] },
];
const IDS = ["padrao", ...PROFILES.map((p) => p.id)];

// Bloco pago → recurso → plano mínimo. Espelho de FEATURE_MIN_TIER_V2 em
// core/services/plan_service.py; tests/frontend/dashboard_v2_profiles.test.mjs compara.
export const WIDGET_FEATURE = { hero: "forecast", piggy: "insights", simulador: "simulator" };
export const FEATURE_TIER = { forecast: "plus", insights: "plus", simulator: "pro" };
export { TIERS };

/** Plano mínimo do bloco, ou null se ele é de todos. */
export const tierOf = (id) => FEATURE_TIER[WIDGET_FEATURE[id]] ?? null;
export const locked = (id, plan) => {
  const need = tierOf(id);
  return !!need && TIERS.indexOf(plan) < TIERS.indexOf(need);
};

// Armazenamento: o que foi escrito nesta visita vale primeiro (memória), depois o
// localStorage. Sem storage (modo privado, cota, bloqueio) tudo segue em memória.
const PROFILE_KEY = "pigbank.dashboard.profile.v1";
const layoutKey = (p) => `pigbank.dashboard.layout.v1.${p}`;
// Marca que o layout salvo deste perfil já conheceu o bloco `contas` (entrou uma vez, ou o usuário salvou depois dele).
const contasKey = (p) => `pigbank.dashboard.layout.v1.${p}.contas`;
const mem = new Map();
function read(key) {
  if (mem.has(key)) return mem.get(key);
  try { return localStorage.getItem(key); } catch { return null; }
}
function write(key, value) {
  mem.set(key, value);
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* fica só em memória */ }
}
const parse = (raw) => { try { return JSON.parse(raw); } catch { return null; } };

/** Perfil salvo no navegador (só o protótipo), ou null (nunca escolheu: abre o modal). */
export function readProfile() {
  const p = parse(read(PROFILE_KEY));
  return IDS.includes(p) ? p : null;
}
export const saveProfile = (p) => write(PROFILE_KEY, JSON.stringify(p));

/**
 * Blocos visíveis do perfil, em ordem. Sem layout salvo (ou salvo ilegível) vale o
 * preset. Some o que o painel não conhece, o repetido e o que o plano não libera. O salvo
 * só é reescrito uma vez, para o bloco `contas` entrar no topo de quem personalizou antes dele.
 */
export function readLayout(profile, preset, known, plan) {
  const saved = parse(read(layoutKey(profile)));
  let ids = Array.isArray(saved) ? saved : preset;
  // Layout salvo antes do bloco `contas` existir: ele entra uma vez, no topo. Depois disso o que o
  // usuário tirar fica tirado (todo saveLayout grava o marcador).
  if (Array.isArray(saved) && !saved.includes("contas") && known.includes("contas") && read(contasKey(profile)) === null) {
    ids = ["contas", ...saved];
    write(layoutKey(profile), JSON.stringify(ids));
    write(contasKey(profile), "1");
  }
  return [...new Set(ids)].filter((id) => known.includes(id) && !locked(id, plan));
}
/** `null` apaga o ajuste: o perfil volta ao preset. */
export function saveLayout(profile, ids) {
  write(layoutKey(profile), ids && JSON.stringify(ids));
  if (ids) write(contasKey(profile), "1");
}
