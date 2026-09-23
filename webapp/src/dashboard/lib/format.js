const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const brl0 = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });

export const money = (n) => brl.format(n).replace(/\u00a0/g, " ");
export const money0 = (n) => brl0.format(n).replace(/\u00a0/g, " ");
export const signed = (n) => `${n >= 0 ? "+" : "−"}${money(Math.abs(n))}`;
export const signed0 = (n) => `${n >= 0 ? "+" : "−"}${money0(Math.abs(n))}`;
export const axisMoney = (n) => (Math.abs(n) >= 1000 ? `R$ ${compact.format(n)}` : `R$ ${Math.round(n)}`);
// "#rrggbb" + alfa -> rgba(), para lavar uma superfície com a cor de identidade.
export const tint = (hex, a) => `rgba(${parseInt(hex.slice(1, 3), 16)}, ${parseInt(hex.slice(3, 5), 16)}, ${parseInt(hex.slice(5, 7), 16)}, ${a})`;
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
