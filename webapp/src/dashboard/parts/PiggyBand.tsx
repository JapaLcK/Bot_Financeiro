import { useState, type ReactNode } from "react";
import { MONTHS, PLAN } from "../lib/api";
import { askPiggy } from "../lib/chat";
import { locked } from "../lib/profiles.js";
import { BY_PROFILE, COMMON, pick } from "../lib/prompts.js";
import type { DashState } from "../lib/types";
import { insights } from "../widgets/Piggy";

// Faixa fixa no topo do Resumo: um convite para conversar com o Piggy, sorteado a cada
// visita entre os insights do dia e perguntas (as do perfil pesam mais). O clique abre o
// chat com a pergunta já enviada. Os alertas não dependem da sorte: ficam no "Piggy notou".
type Option = { key: string; weight: number; head: string; ask: string | null; text?: ReactNode };
type Prompt = { key: string; head: string; ask: string | null };

const LAST = "pigbank.dashboard.band.last";
const lastShown = () => { try { return sessionStorage.getItem(LAST); } catch { return null; } };
const remember = (key: string) => { try { sessionStorage.setItem(LAST, key); } catch { /* sem storage, pode repetir */ } };

function options(s: DashState, profile: string): Option[] {
  const today = insights({ ...s, month: MONTHS[MONTHS.length - 1] })
    .filter((i) => i.head && i.ask)
    .map((i) => ({ key: `insight-${i.key}`, weight: 2, head: i.head!, ask: i.ask!, text: i.text }));
  const mine = ((BY_PROFILE as Record<string, Prompt[]>)[profile] ?? []).map((p) => ({ ...p, weight: 2 }));
  return [...today, ...mine, ...(COMMON as Prompt[]).map((p) => ({ ...p, weight: 1 }))];
}

export function PiggyBand({ s, profile }: { s: DashState; profile: string }) {
  const [o] = useState<Option>(() => {
    const chosen = pick(options(s, profile), lastShown()) as Option;
    remember(chosen.key);
    return chosen;
  });

  if (locked("piggy", PLAN)) {
    return (
      <a className="piggy-band" href="../frontend/precos.html" data-band="plus">
        <span className="piggy-band-by"><img src="../frontend/brand/icon.png" alt="" width={28} height={28} />Piggy</span>
        <span className="piggy-band-head">O Piggy lê seus gastos e te conta o que mudou.</span>
        <span className="piggy-band-text">Os insights e a conversa com o Piggy vêm no plano Plus.</span>
        <span className="piggy-band-cta">Conhecer o Plus<i className="ph ph-arrow-right" aria-hidden="true" /></span>
      </a>
    );
  }
  return (
    <button type="button" className="piggy-band" data-band={o.key} onClick={() => askPiggy(o.ask)}>
      <span className="piggy-band-by"><img src="../frontend/brand/icon.png" alt="" width={28} height={28} />Piggy · hoje</span>
      <span className="piggy-band-head">{o.head}</span>
      {o.text && <span className="piggy-band-text">{o.text}</span>}
      <span className="piggy-band-cta">
        {o.ask ? <>Perguntar ao Piggy: “{o.ask}”</> : "Abrir a conversa com o Piggy"}
        <i className="ph ph-arrow-right" aria-hidden="true" />
      </span>
    </button>
  );
}
