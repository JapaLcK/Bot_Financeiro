import { useState, type ReactNode } from "react";
import { MONTHS, PLAN } from "../lib/api";
import { ask } from "../lib/conversation";
import type { TopicId } from "../lib/topics";
import { locked } from "../lib/profiles.js";
import { BY_PROFILE, COMMON, pick } from "../lib/prompts.js";
import type { DashState } from "../lib/types";
import { go } from "../router";
import { insights } from "../widgets/Piggy";

// Faixa fixa no topo do Resumo: um convite para conversar com o Piggy, sorteado a cada
// visita entre os insights do dia e perguntas (as do perfil pesam mais). O clique abre a
// conversa (#/piggy) com a pergunta já respondida. Os alertas não dependem da sorte: ficam
// no "Piggy notou". A conversa é de todo plano; os insights, só onde o "Piggy notou" abre.
type Option = { key: string; weight: number; head: string; ask: string | null; topic?: TopicId; cat?: string; text?: ReactNode };
type Prompt = { key: string; head: string; ask: string | null; topic?: TopicId; cat?: string };

const LAST = "pigbank.dashboard.band.last";
const lastShown = () => { try { return sessionStorage.getItem(LAST); } catch { return null; } };
const remember = (key: string) => { try { sessionStorage.setItem(LAST, key); } catch { /* sem storage, pode repetir */ } };

function options(s: DashState, profile: string): Option[] {
  const today = locked("piggy", PLAN) ? [] : insights({ ...s, month: MONTHS[MONTHS.length - 1] })
    .filter((i) => i.head && i.ask)
    .map((i) => ({ key: `insight-${i.key}`, weight: 2, head: i.head!, ask: i.ask!, topic: i.topic, cat: i.cat, text: i.text }));
  const mine = ((BY_PROFILE as Record<string, Prompt[]>)[profile] ?? []).map((p) => ({ ...p, weight: 2 }));
  return [...today, ...mine, ...(COMMON as Prompt[]).map((p) => ({ ...p, weight: 1 }))];
}

export function PiggyBand({ s, profile }: { s: DashState; profile: string }) {
  const [o] = useState<Option>(() => {
    const chosen = pick(options(s, profile), lastShown()) as Option;
    remember(chosen.key);
    return chosen;
  });

  return (
    <button type="button" className="piggy-band" data-band={o.key} onClick={() => (o.ask ? ask({ text: o.ask, topic: o.topic, cat: o.cat, key: o.key }) : go("/piggy"))}>
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
