import { CATEGORIES } from "../lib/api";
import { dayMonth, money, tint } from "../lib/format.js";
import type { Launch } from "../lib/types";
import { Frame } from "../parts/Frame";

// Lista curta de lançamentos para a conversa do Piggy. No lugar do logo, um monograma
// com cor fixa por estabelecimento (os logos reais entram no corte para produção).
const TONES = CATEGORIES.map((c) => c.color);
const SMALL = new Set(["de", "do", "da", "dos", "das", "e", "·"]);

export function monogram(label: string) {
  const words = label.split(/\s+/).filter((w) => w && !SMALL.has(w.toLowerCase()));
  const text = words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2);
  let h = 0;
  for (const ch of label) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return { text: text.toUpperCase(), color: TONES[h % TONES.length] };
}

export function LaunchList({ title, launches }: { title: string; launches: Launch[] }) {
  return (
    <Frame id="lancamentos-lista" title={title}>
      <ul className="inv-list launch-list">
        {launches.map((l, i) => {
          const m = monogram(l.label);
          return (
            <li key={l.id ?? i}>
              <span className="mono" style={{ background: tint(m.color, 0.18), color: m.color }} aria-hidden="true">{m.text}</span>
              <span className="inv-name">{l.label}</span>
              <span className="faint num">{dayMonth(l.date)}</span>
              <b className="num">{money(l.amount ?? 0)}</b>
            </li>
          );
        })}
      </ul>
    </Frame>
  );
}
