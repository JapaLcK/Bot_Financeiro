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
// Saldo na moeda da conta (código da API). Código fora do ISO (vazio, null, minúsculo) sai só
// o número, sem símbolo inventado; com a validação o Intl não lança RangeError.
export function moneyIn(n, moeda) {
  if (moeda === "BRL") return money(n);
  const f = /^[A-Z]{3}$/.test(moeda) ? { style: "currency", currency: moeda } : { minimumFractionDigits: 2, maximumFractionDigits: 2 };
  return out(new Intl.NumberFormat("pt-BR", f), zero(n, 2));
}
// Dinheiro da /api/v2 (texto decimal) sem passar por float nem pelo Intl, que no Safari 14
// converte a string em número: todas as casas que vierem (no mínimo 2), sem arredondar.
// Zero sai "R$ 0,00", sem sinal. Inválido (null, "", "NaN", "Infinity") → null.
const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d{1,3}))?$/;
export function moneyText(s) {
  const m = typeof s === "string" && DECIMAL.exec(s);
  if (!m) return null;
  let digitos = m[2] + (m[3] ?? "");
  let ponto = m[2].length + Number(m[4] ?? 0);
  if (!/[1-9]/.test(digitos)) return "R$ 0,00";
  if (ponto < 0) { digitos = "0".repeat(-ponto) + digitos; ponto = 0; }
  digitos = digitos.padEnd(ponto, "0");
  const inteiro = (digitos.slice(0, ponto).replace(/^0+/, "") || "0").replace(/\B(?=(\d{3})+$)/g, ".");
  return `${m[1] ? "−" : ""}R$ ${inteiro},${digitos.slice(ponto).padEnd(2, "0")}`;
}
// O mesmo texto decimal como inteiro exato: valor = n / 10^escala (BigInt, sem float). Inválido → null.
export function decimal(s) {
  const m = typeof s === "string" && DECIMAL.exec(s);
  if (!m) return null;
  const frac = m[3] ?? "";
  let n = BigInt(m[2] + frac), escala = frac.length - Number(m[4] ?? 0);
  if (escala < 0) { n *= BigInt(10) ** BigInt(-escala); escala = 0; }
  return { n: m[1] ? -n : n, escala };
}
// O valor que o usuário digita num formulário de escrita (Lançamento, Meta): vírgula vira ponto,
// no máximo 9 dígitos e 2 casas, maior que zero. Texto, nunca float.
export const ERRO_VALOR = "Use um valor maior que zero, até 9 dígitos e 2 casas decimais, sem separador de milhares.";
export const normalizaValor = (s) => s.trim().replace(",", ".");
export const valorInvalido = (norm) => !/^\d{1,9}(\.\d{1,2})?$/.test(norm) || !/[1-9]/.test(norm);
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
// "YYYY-MM-DD" da API como dia local: `new Date("2026-10-05")` é meia-noite UTC, dia 4 em SP.
export const isoDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };

export function relativeDays(n) {
  if (n === 0) return "hoje";
  if (n === 1) return "amanhã";
  if (n === -1) return "ontem";
  return n > 0 ? `em ${n} dias` : `há ${-n} dias`;
}

// "há 12 min", "há 3 h", "há 2 dias" a partir de um instante em ms (`instante()`). Inválido → null; menos de 1 min ou futuro → "agora".
export function haQuanto(ms, agora = Date.now()) {
  if (!Number.isFinite(ms)) return null;
  const min = Math.floor((agora - ms) / 60000);
  if (min < 1) return "agora";
  if (min < 60) return `há ${min} min`;
  if (min < 1440) return `há ${Math.floor(min / 60)} h`;
  return relativeDays(-Math.floor(min / 1440));
}
