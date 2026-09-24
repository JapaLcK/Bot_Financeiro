// Perfis do Resumo. Um perfil é só o layout inicial do painel: a lista ordenada de
// blocos. O ajuste fino fica salvo por perfil. `padrao` (o "Pular") é o painel de
// sempre, com a lista em parts/Board.tsx.
// Os presets já citam blocos que ainda não existem (renda, rendimento, parcelas):
// a leitura descarta id desconhecido, então eles só aparecem quando existirem.
import { TIERS } from "./data.js";

export const PROFILES = [
  { id: "economizar", label: "Economizar", icon: "ph-piggy-bank",
    line: "Pra guardar mais todo mês. Mostra quanto sobrou, suas metas e onde dá pra cortar.",
    preset: ["resumo", "metas", "piggy", "categorias", "simulador", "compromissos"] },
  { id: "investir", label: "Investir", icon: "ph-trend-up",
    line: "Pra fazer o dinheiro trabalhar. Mostra seu patrimônio, onde ele está e quanto falta pras metas.",
    preset: ["patrimonio", "rendimento", "wealth", "simulador", "metas", "resumo", "piggy"] },
  { id: "controlar", label: "Controlar gastos", icon: "ph-chart-bar",
    line: "Pra saber pra onde vai cada real. Mostra os gastos por categoria, dia a dia, e as contas que vêm aí.",
    preset: ["categorias", "calendario", "resumo", "compromissos", "fatura", "piggy"] },
  { id: "dividas", label: "Sair das dívidas", icon: "ph-credit-card",
    line: "Pra pagar o que deve e respirar. Mostra a fatura, os próximos vencimentos e o saldo previsto.",
    preset: ["fatura", "parcelas", "compromissos", "hero", "resumo", "categorias", "piggy"] },
  { id: "autonomo", label: "Autônomo", icon: "ph-briefcase",
    line: "Pra quem tem renda que muda todo mês. Mostra o saldo previsto, as contas fixas e as metas.",
    preset: ["renda", "hero", "resumo", "compromissos", "metas", "categorias", "piggy"] },
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

/** Perfil salvo, ou null (nunca escolheu: abre o modal). */
export function readProfile() {
  const p = parse(read(PROFILE_KEY));
  return IDS.includes(p) ? p : null;
}
export const saveProfile = (p) => write(PROFILE_KEY, JSON.stringify(p));

/**
 * Blocos visíveis do perfil, em ordem. Sem layout salvo (ou salvo ilegível) vale o
 * preset. Some o que o painel não conhece, o repetido e o que o plano não libera;
 * o salvo não é reescrito aqui.
 */
export function readLayout(profile, preset, known, plan) {
  const saved = parse(read(layoutKey(profile)));
  const ids = Array.isArray(saved) ? saved : preset;
  return [...new Set(ids)].filter((id) => known.includes(id) && !locked(id, plan));
}
/** `null` apaga o ajuste: o perfil volta ao preset. */
export const saveLayout = (profile, ids) => write(layoutKey(profile), ids && JSON.stringify(ids));
