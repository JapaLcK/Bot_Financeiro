const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const brl0 = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
const brlBig = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", notation: "compact", minimumFractionDigits: 0, maximumFractionDigits: 1 });

// O que arredonda a zero na precisão mostrada sai sem sinal: nunca "−R$ 0" nem "+R$ 0".
const zero = (n, digits) => (Math.abs(n) < 0.5 / 10 ** digits ? 0 : n);
const sign = (n) => (n > 0 ? "+" : n < 0 ? "−" : "");
// Espaço comum no lugar do fixo e o menos tipográfico (−) no lugar do hífen do Intl.
const out = (f, n) => f.format(n).replace(/\u00a0/g, " ").replace(/^-/, "−");
export const money = (n) => out(brl, zero(n, 2));
export const money0 = (n) => out(brl0, zero(n, 0));
export const signed = (n) => { const v = zero(n, 2); return `${sign(v)}${money(Math.abs(v))}`; };
export const signed0 = (n) => { const v = zero(n, 0); return `${sign(v)}${money0(Math.abs(v))}`; };
// A partir de R$ 1 milhão, "R$ 1,2 mi": o valor digitado no simulador não tem teto e não pode estourar o layout.
export const moneyBig = (n, fmt = money0) => (Math.abs(n) >= 1e6 ? out(brlBig, n) : fmt(n));
export const signedBig = (n) => { const v = zero(n, 0); return `${sign(v)}${moneyBig(Math.abs(v))}`; };
export const axisMoney = (n) => (n < 0 ? `−${axisMoney(-n)}` : n >= 1000 ? `R$ ${compact.format(n)}` : `R$ ${Math.round(n)}`);
// "#rrggbb" + alfa -> rgba(), para lavar uma superfície com a cor de identidade.
export const tint = (hex, a) => `rgba(${parseInt(hex.slice(1, 3), 16)}, ${parseInt(hex.slice(3, 5), 16)}, ${parseInt(hex.slice(5, 7), 16)}, ${a})`;
// Cor de um ganho/perda da simulação: arredonda como a tela mostra, então "R$ 0" fica neutro.
export const tone = (d) => { const r = Math.round(d); return r > 0 ? "gain" : r < 0 ? "warn" : ""; };
export const pct = (n) => `${n >= 0 ? "+" : "−"}${Math.abs(Math.round(n * 100))}%`;

const MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const WEEK = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];

export const monthName = (d) => MONTHS[d.getMonth()];
export const monthShort = (d) => MONTHS[d.getMonth()].slice(0, 3);
export const monthTitle = (key) => {
  const [y, m] = key.split("-").map(Number);
  const name = MONTHS[m - 1];
  return `${name[0].toUpperCase()}${name.slice(1)} ${y}`;
};
export const dayMonth = (d) => `${d.getDate()} ${monthShort(d)}`;
export const weekday = (d) => WEEK[d.getDay()];
export const longDate = (d) => `${weekday(d)}, ${d.getDate()} de ${monthName(d)}`;
export const monthYear = (d) => `${monthShort(d)}/${String(d.getFullYear()).slice(2)}`;

export function relativeDays(n) {
  if (n === 0) return "hoje";
  if (n === 1) return "amanhã";
  if (n === -1) return "ontem";
  return n > 0 ? `em ${n} dias` : `há ${-n} dias`;
}
