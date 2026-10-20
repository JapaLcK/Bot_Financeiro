// Ícone e cor de cada pote no /painel. A paleta é só do /painel: `BUCKETS` (servidor) segue
// com a cor que o /app antigo lê. Chave desconhecida cai na cor do servidor (se for hex) e no círculo.
import type { Pote } from "./api-v2.gen";

const POTES: Record<string, { icone: string; cor: string }> = {
  custos_fixos: { icone: "ph-house", cor: "#ff2d8e" },
  conforto: { icone: "ph-shopping-cart", cor: "#ff8a4c" },
  metas: { icone: "ph-target", cor: "#e15bf0" },
  prazeres: { icone: "ph-confetti", cor: "#8b5cf6" },
  liberdade_financeira: { icone: "ph-tree", cor: "#34d399" },
  conhecimento: { icone: "ph-book-open", cor: "#60a5fa" },
};
const HEX = /^#[0-9a-f]{6}$/i;

export function visualDoPote(p: Pote): { icone: string; cor: string; hex: boolean } {
  const v = POTES[p.chave];
  const cor = v?.cor ?? (HEX.test(p.cor) ? p.cor : "var(--ink-3)");
  return { icone: v?.icone ?? "ph-circle", cor, hex: HEX.test(cor) };
}

export const pctTxt = (v: string | null) => v === null ? "—" : `${Math.round(Number(v))}%`;
export const largura = (v: string | null) => v === null ? 0 : Math.min(Math.max(Number(v), 0), 100);
