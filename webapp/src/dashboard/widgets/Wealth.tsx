import { BALANCE_TODAY, GOALS, INVESTMENTS } from "../lib/api";
import { money0 } from "../lib/format.js";
import { Frame } from "../parts/Frame";

// Onde o patrimônio está hoje: uma barra de composição e a lista por lugar.
const PARTS = [
  // Cores de identidade da paleta categórica validada (verde é só para ganho; rosa, para agora).
  { label: "Conta", tone: "#3987e5" },
  { label: "Caixinhas", tone: "#9085e9" },
  { label: "Investimentos", tone: "#2fa0c8" },
];

export function Wealth() {
  const caixinhas = GOALS.reduce((a, g) => a + g.saved, 0);
  const invest = INVESTMENTS.reduce((a: number, x: { amount: number }) => a + x.amount, 0);
  const values = [BALANCE_TODAY, caixinhas, invest];
  const total = values.reduce((a, b) => a + b, 0);
  const rows = [
    { label: "Conta corrente", group: 0, v: BALANCE_TODAY },
    ...GOALS.map((g) => ({ label: g.label, group: 1, v: g.saved })),
    ...INVESTMENTS.map((x: { label: string; amount: number }) => ({ label: x.label, group: 2, v: x.amount })),
  ];

  return (
    <Frame id="composicao" title="Onde está o dinheiro">
      <div className="comp-bar" role="img" aria-label={PARTS.map((p, i) => `${p.label} ${Math.round((values[i] / total) * 100)}%`).join(", ")}>
        {PARTS.map((p, i) => <span key={p.label} style={{ flexGrow: values[i], background: p.tone }} />)}
      </div>
      <ul className="legend comp-legend">
        {PARTS.map((p, i) => (
          <li key={p.label}><span className="key" style={{ background: p.tone, height: 8, width: 8, borderRadius: 2 }} />{p.label} <b className="num">{Math.round((values[i] / total) * 100)}%</b></li>
        ))}
      </ul>
      <ul className="wealth-rows">
        {rows.map((r) => (
          <li key={r.label}>
            <span className="tip-key" style={{ background: PARTS[r.group].tone, width: 8, height: 8 }} aria-hidden="true" />
            <span className="wealth-name">{r.label}</span>
            <span className="faint num">{Math.round((r.v / total) * 100)}%</span>
            <b className="num">{money0(r.v)}</b>
          </li>
        ))}
      </ul>
    </Frame>
  );
}
