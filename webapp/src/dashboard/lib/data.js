// Dados sintéticos de demonstração. Nada aqui vem de usuário real.
// Determinístico (PRNG com semente), "hoje" fixo em 23/09/2026 para a
// avaliação ser reproduzível.

export const TODAY = new Date(2026, 8, 23);

export const CATEGORIES = [
  { id: "mercado", label: "Mercado", icon: "ph-shopping-cart", color: "#3987e5", variable: true },
  { id: "delivery", label: "Delivery", icon: "ph-hamburger", color: "#d95926", variable: true },
  { id: "transporte", label: "Transporte", icon: "ph-taxi", color: "#2fa0c8", variable: true },
  { id: "lazer", label: "Lazer", icon: "ph-beer-stein", color: "#c98500", variable: true },
  { id: "assinaturas", label: "Assinaturas", icon: "ph-television", color: "#9085e9", variable: false },
  { id: "compras", label: "Compras", icon: "ph-handbag", color: "#e66767", variable: true },
  { id: "casa", label: "Moradia", icon: "ph-house", color: "#7c8391", variable: false },
  { id: "outros", label: "Outros", icon: "ph-tag", color: "#5a5f6a", variable: true },
];

// Pool de lançamentos variáveis: [estabelecimento, min, max, fonte preferida, mensagem]
const POOL = {
  mercado: [["Carrefour Express", 22, 118, "openfinance"], ["Pão de Açúcar", 35, 140, "cartao"], ["Hortifruti da Vila", 18, 64, "whatsapp", "hortifruti {v}"], ["Oxxo", 9, 38, "whatsapp", "gastei {v} no oxxo"]],
  delivery: [["iFood", 29, 68, "cartao"], ["iFood · Poke", 38, 59, "cartao"], ["Rappi", 27, 61, "whatsapp", "rappi {v} pizza"], ["iFood · Hambúrguer", 34, 72, "whatsapp", "ifood {v} lanche"]],
  transporte: [["Uber", 11, 38, "openfinance"], ["99", 9, 29, "whatsapp", "99 {v} pra facul"], ["Bilhete Único", 20, 50, "whatsapp", "recarreguei o bilhete {v}"]],
  lazer: [["Bar do Zé", 32, 96, "whatsapp", "bar {v} com a galera"], ["Cinemark", 28, 56, "cartao"], ["Sympla · Show", 80, 160, "cartao"], ["Boliche Strike", 40, 70, "whatsapp", "boliche {v}"]],
  compras: [["Shein", 64, 210, "cartao"], ["Mercado Livre", 45, 260, "cartao"], ["Renner", 79, 189, "cartao"], ["Amazon", 39, 149, "cartao"]],
  outros: [["Drogasil", 18, 64, "openfinance"], ["Papelaria Central", 12, 40, "whatsapp", "papelaria {v}"]],
};

// Gasto variável do mês inteiro por categoria (a história: delivery sobe em setembro).
const TARGET = {
  "2026-07": { mercado: 520, delivery: 260, transporte: 310, lazer: 380, compras: 180, outros: 70 },
  "2026-08": { mercado: 560, delivery: 300, transporte: 290, lazer: 460, compras: 240, outros: 60 },
  "2026-09": { mercado: 540, delivery: 470, transporte: 240, lazer: 350, compras: 210, outros: 50 },
};

// Compromissos fixos por dia do mês.
export const RECURRING = [
  { day: 10, label: "Aluguel (sua parte)", category: "casa", amount: 950, source: "openfinance" },
  { day: 15, label: "Internet (dividida)", category: "casa", amount: 60, source: "openfinance" },
  { day: 20, label: "Plano de celular", category: "casa", amount: 55, source: "openfinance" },
  { day: 22, label: "Conta de luz", category: "casa", amount: 78.4, source: "whatsapp", msg: "paguei a luz 78,40" },
  { day: 8, label: "Spotify", category: "assinaturas", amount: 21.9, source: "cartao" },
  { day: 18, label: "Netflix", category: "assinaturas", amount: 44.9, source: "cartao" },
  { day: 28, label: "Academia", category: "assinaturas", amount: 99.9, source: "openfinance" },
];

// Transferência automática para as caixinhas: sai do saldo, não é gasto.
export const TRANSFER_DAY = 6;

export const INCOMES = [
  { day: 5, label: "Bolsa do estágio", amount: 3400, source: "openfinance" },
];
const FREELAS = { "2026-07": [20, 400], "2026-08": [12, 650], "2026-09": [15, 900] };

export const CARD = { label: "Cartão de crédito ••4821", limit: 2500, dueDay: 10 };
export const MONTHS = ["2026-07", "2026-08", "2026-09"];
export const OPENING_BALANCE = 1920; // saldo em 1º de julho

export const GOALS = [
  { id: "intercambio", label: "Intercâmbio em Dublin", icon: "ph-airplane-tilt", color: "#3987e5", target: 18000, saved: 6240, monthly: 350 },
  { id: "reserva", label: "Reserva de emergência", icon: "ph-shield-check", color: "#d95926", target: 6000, saved: 4380, monthly: 150 },
  { id: "notebook", label: "Notebook novo", icon: "ph-laptop", color: "#9085e9", target: 5500, saved: 1150, monthly: 150 },
  { id: "festival", label: "Festival em dezembro", icon: "ph-ticket", color: "#c98500", target: 1200, saved: 960, monthly: 80 },
];

// Caixinhas do Nubank como chegam pelo Open Finance: cada depósito é um CDB com o mesmo
// nome e emissor. As posições são só o dado cru: a tela mostra apenas o total, como caixinhas.
export const BANK_CDB = { bank: "Nubank", via: "Open Finance", name: "CDB - NU FINANCEIRA S.A.", positions: [612.4, 388.15, 201.73, 150, 96.52, 64] };
export const BANK_CDB_TOTAL = Math.round(BANK_CDB.positions.reduce((a, b) => a + b, 0) * 100) / 100;

export const INVESTMENTS = [
  { label: "Tesouro Selic", amount: 2310.45 },
  { label: "Bitcoin", amount: 418.3 },
];

function prng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const daysIn = (key) => {
  const [y, m] = key.split("-").map(Number);
  return new Date(y, m, 0).getDate();
};

const round2 = (n) => Math.round(n * 100) / 100;

function monthLaunches(key, index) {
  const rand = prng(20260700 + index * 97);
  const [y, m] = key.split("-").map(Number);
  const last = key === "2026-09" ? TODAY.getDate() : daysIn(key);
  const out = [];
  let seq = 0;
  const push = (day, o) => out.push({ id: `${key}-${seq++}`, date: new Date(y, m - 1, day), ...o });

  // Cada categoria recebe o seu total do mês (proporcional aos dias já vividos),
  // repartido em lançamentos de valor e dia sorteados.
  for (const [cat, total] of Object.entries(TARGET[key])) {
    const pool = POOL[cat];
    const ticket = pool.reduce((a, p) => a + (p[1] + p[2]) / 2, 0) / pool.length;
    const budget = (total * last) / daysIn(key);
    const n = Math.max(1, Math.round(budget / ticket));
    const weights = Array.from({ length: n }, () => 0.55 + rand() * 0.9);
    const sum = weights.reduce((a, b) => a + b, 0);
    weights.forEach((w) => {
      let day = 1 + Math.floor(rand() * last);
      if (cat === "lazer" && ![0, 5, 6].includes(new Date(y, m - 1, day).getDay()) && rand() < 0.6) day = Math.min(last, day + ((5 - new Date(y, m - 1, day).getDay() + 7) % 7));
      const [name, , , source, msg] = pool[Math.floor(rand() * pool.length)];
      const amount = round2((budget * w) / sum);
      const v = amount.toFixed(2).replace(".", ",").replace(",00", "");
      push(day, { kind: "expense", category: cat, label: name, amount, source, msg: msg ? msg.replace("{v}", v) : null });
    });
  }
  for (let day = 1; day <= last; day++) {
    for (const r of RECURRING) if (r.day === day) push(day, { kind: "expense", category: r.category, label: r.label, amount: r.amount, source: r.source, msg: r.msg || null });
    for (const i of INCOMES) if (i.day === day) push(day, { kind: "income", category: null, label: i.label, amount: i.amount, source: i.source });
    if (day === TRANSFER_DAY) push(day, { kind: "transfer", category: null, label: "Guardado nas caixinhas", amount: GOALS.reduce((a, g) => a + g.monthly, 0), source: "openfinance" });
    if (FREELAS[key][0] === day) push(day, { kind: "income", category: null, label: "Pix · freela de design", amount: FREELAS[key][1], source: "whatsapp", msg: `recebi ${FREELAS[key][1]} do freela` });
  }
  return out;
}

export const LAUNCHES = Object.fromEntries(MONTHS.map((k, i) => [k, monthLaunches(k, i)]));

// Patrimônio dos últimos 12 meses (out/2025 a set/2026), já com a composição.
export const NET_WORTH = (() => {
  const rand = prng(777);
  const rows = [];
  let conta = 1400, caixinhas = 7600, invest = 2600;
  for (let i = 0; i < 12; i++) {
    const d = new Date(2025, 9 + i, 1);
    conta = Math.max(600, conta + (rand() - 0.45) * 520);
    caixinhas += 780 + rand() * 420 - (i === 3 ? 1900 : 0); // jan: viagem de férias
    invest += 90 + rand() * 180 + (rand() - 0.5) * 140;
    rows.push({ date: d, conta: round2(conta), caixinhas: round2(caixinhas), investimentos: round2(invest) });
  }
  const last = rows[rows.length - 1];
  last.investimentos = round2(INVESTMENTS.reduce((s, x) => s + x.amount, 0));
  return rows;
})();
