// Objetos financeiros reutilizados no caos do início, nas linhas do app e no dashboard.
// Todas as funções desenham centradas em (0,0) e se adaptam ao w/h recebido, para poder sofrer morph de tamanho.
import { CAT, PINK, GAIN, rgba, rr, rrc, txt, tw, icon, brl, rng } from "./lib.js";

export const ROWS = [
  { icon: "shopping-cart", col: CAT.mercado, title: "Mercado", sub: "Débito", val: -86.40 },
  { icon: "fork-knife", col: CAT.delivery, title: "Delivery", sub: "Crédito", val: -42.90 },
  { icon: "car", col: CAT.transporte, title: "Transporte", sub: "Pix", val: -23.50 },
  { icon: "film-slate", col: CAT.assinaturas, title: "Streaming", sub: "Assinatura", val: -39.90 },
  { icon: "barbell", col: CAT.lazer, title: "Academia", sub: "Mensalidade", val: -119.90 },
  { icon: "pill", col: CAT.compras, title: "Farmácia", sub: "Débito", val: -58.20 },
  { icon: "lightning", col: "#e0a100", title: "Conta de luz", sub: "vence 12/10", val: -184.30 },
  { icon: "house", col: CAT.outros, title: "Aluguel", sub: "vence 05/10", val: -1450 },
  { icon: "cards", col: PINK, title: "Celular", sub: "3/12 no cartão", val: -249.90, badge: "3/12" },
  { icon: "laptop", col: CAT.transporte, title: "Notebook", sub: "5/10 no cartão", val: -389.00, badge: "5/10" },
  { icon: "coffee", col: CAT.lazer, title: "Café", sub: "Pix", val: -12.90 },
  { icon: "taxi", col: CAT.transporte, title: "Corrida", sub: "Crédito", val: -31.40 },
];

export function drawRow(ctx, w, h, d, th, o = {}) {
  const { alpha = 1, shadow = true } = o;
  ctx.save();
  ctx.globalAlpha *= alpha;
  if (shadow) { ctx.shadowColor = th.shadow; ctx.shadowBlur = h * .45; ctx.shadowOffsetY = h * .16; }
  rrc(ctx, 0, 0, w, h, h * .28); ctx.fillStyle = th.card; ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.lineWidth = 1.5; ctx.strokeStyle = th.line; ctx.stroke();
  const x0 = -w / 2, ir = h * .3, ix = x0 + h * .52;
  ctx.beginPath(); ctx.arc(ix, 0, ir, 0, Math.PI * 2); ctx.fillStyle = rgba(d.col, th.name === "dark" ? .2 : .14); ctx.fill();
  icon(ctx, d.icon, ix, 0, h * .34, d.col);
  const tx = x0 + h * 1.0, fs = h * .27;
  const showSub = w > h * 3 && d.sub;
  txt(ctx, d.title, tx, showSub ? -h * .04 : h * .1, { size: fs, weight: 650, color: th.ink, ls: -.3 });
  if (showSub) txt(ctx, d.sub, tx, h * .25, { size: fs * .72, weight: 500, color: th.ink3 });
  const vs = h * .28, vc = d.val > 0 ? th.gain : th.ink;
  const vt = d.valText ?? brl(d.val);
  txt(ctx, vt, w / 2 - h * .3, h * .1, { size: vs, weight: 700, color: vc, align: "right", ls: -.3 });
  if (d.badge && w > h * 3.6) {
    const bw = tw(ctx, d.badge, fs * .7, 700) + h * .34;
    rrc(ctx, w / 2 - h * .3 - tw(ctx, vt, vs, 700) - bw / 2 - h * .22, -h * .02, bw, h * .36, h * .18);
    ctx.fillStyle = rgba(PINK, .18); ctx.fill();
    txt(ctx, d.badge, w / 2 - h * .3 - tw(ctx, vt, vs, 700) - bw / 2 - h * .22, h * .085, { size: fs * .7, weight: 700, color: PINK, align: "center" });
  }
  ctx.restore();
}

export const CARD_SKINS = {
  pink: { a: "#FF4BA0", b: "#C7186B", ink: "#fff" },
  black: { a: "#2b2b31", b: "#0c0c0e", ink: "#fff", edge: "rgba(255,255,255,.14)" },
  white: { a: "#fbfaf8", b: "#d8d3ca", ink: "#111" },
  neon: { a: "#D6FF3F", b: "#95C412", ink: "#111" },
};

// cartão de crédito (frente ou verso)
export function drawCreditCard(ctx, w, h, skin = "pink", o = {}) {
  const s = CARD_SKINS[skin], { back = false, last = "4821", shadow = true } = o;
  ctx.save();
  if (shadow) { ctx.shadowColor = "rgba(0,0,0,.5)"; ctx.shadowBlur = h * .25; ctx.shadowOffsetY = h * .1; }
  const g = ctx.createLinearGradient(-w / 2, -h / 2, w / 2, h / 2);
  g.addColorStop(0, s.a); g.addColorStop(1, s.b);
  rrc(ctx, 0, 0, w, h, h * .09); ctx.fillStyle = g; ctx.fill();
  ctx.shadowColor = "transparent";
  if (s.edge) { ctx.lineWidth = 2; ctx.strokeStyle = s.edge; ctx.stroke(); }
  ctx.clip();
  // brilho diagonal
  const sh = ctx.createLinearGradient(-w / 2, -h / 2, w / 4, h / 2);
  sh.addColorStop(0, "rgba(255,255,255,.22)"); sh.addColorStop(.45, "rgba(255,255,255,0)");
  ctx.fillStyle = sh; ctx.fillRect(-w / 2, -h / 2, w, h);
  if (back) {
    ctx.fillStyle = "rgba(0,0,0,.78)"; ctx.fillRect(-w / 2, -h * .3, w, h * .22);
    ctx.fillStyle = "rgba(255,255,255,.85)"; ctx.fillRect(-w * .38, h * .04, w * .5, h * .13);
  } else {
    rrc(ctx, -w * .34, -h * .1, w * .13, h * .16, h * .03);
    const cg = ctx.createLinearGradient(0, -h * .18, 0, h * .06); cg.addColorStop(0, "#f1d98b"); cg.addColorStop(1, "#b98f2e");
    ctx.fillStyle = cg; ctx.fill();
    txt(ctx, "crédito", w * .4, -h * .28, { size: h * .09, weight: 600, color: s.ink, align: "right", ls: 1 });
    txt(ctx, `•••• ${last}`, -w * .4, h * .3, { size: h * .115, weight: 600, color: s.ink, ls: 2 });
    ctx.strokeStyle = s.ink; ctx.globalAlpha *= .7; ctx.lineWidth = h * .014;
    for (let i = 1; i <= 3; i++) { ctx.beginPath(); ctx.arc(w * .3, -h * .02, h * .035 * i, -.9, .9); ctx.stroke(); }
  }
  ctx.restore();
}

export function drawPill(ctx, w, h, d, th) {
  ctx.save();
  ctx.shadowColor = th.shadow; ctx.shadowBlur = h * .5; ctx.shadowOffsetY = h * .2;
  rrc(ctx, 0, 0, w, h, h / 2);
  ctx.fillStyle = d.val > 0 ? rgba(GAIN, .2) : th.card2; ctx.fill();
  ctx.shadowColor = "transparent"; ctx.lineWidth = 1.5; ctx.strokeStyle = d.val > 0 ? rgba(GAIN, .5) : th.line; ctx.stroke();
  txt(ctx, brl(d.val, 2, true), 0, h * .13, { size: h * .42, weight: 700, color: d.val > 0 ? GAIN : th.ink, align: "center", ls: -.3 });
  ctx.restore();
}

// gráfico pequeno: barras ou linha
export function drawMiniChart(ctx, w, h, d, th) {
  const r = rng(d.seed || 3);
  ctx.save();
  ctx.shadowColor = th.shadow; ctx.shadowBlur = h * .3; ctx.shadowOffsetY = h * .1;
  rrc(ctx, 0, 0, w, h, h * .1); ctx.fillStyle = th.card; ctx.fill();
  ctx.shadowColor = "transparent"; ctx.lineWidth = 1.5; ctx.strokeStyle = th.line; ctx.stroke();
  txt(ctx, d.title, -w / 2 + w * .07, -h / 2 + h * .18, { size: h * .09, weight: 600, color: th.ink3 });
  txt(ctx, d.big, -w / 2 + w * .07, -h / 2 + h * .36, { size: h * .15, weight: 750, color: th.ink, ls: -.5 });
  const n = 8, bx = -w / 2 + w * .07, bw = (w * .86) / n;
  if (d.type === "bars") {
    for (let i = 0; i < n; i++) {
      const v = .25 + r() * .75, bh = v * h * .34;
      rr(ctx, bx + i * bw + bw * .14, h / 2 - h * .09 - bh, bw * .72, bh, bw * .2);
      ctx.fillStyle = i === n - 1 ? PINK : th.card2 === "#1d1d22" ? "#34343b" : "#dcd6cd"; ctx.fill();
    }
  } else {
    ctx.beginPath();
    for (let i = 0; i < n; i++) { const y = h / 2 - h * .09 - (.15 + r() * .85) * h * .3; i ? ctx.lineTo(bx + i * bw + bw / 2, y) : ctx.moveTo(bx + bw / 2, y); }
    ctx.lineWidth = h * .028; ctx.strokeStyle = d.col || PINK; ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.stroke();
  }
  ctx.restore();
}

// especificações do caos inicial
export const HOOK = [
  { k: "row", d: ROWS[0] }, { k: "card", skin: "pink" }, { k: "pill", d: { val: -12.90 } }, { k: "row", d: ROWS[8] },
  { k: "chart", d: { title: "Gastos da semana", big: "R$ 612", type: "bars", seed: 4 } }, { k: "row", d: ROWS[6] },
  { k: "pill", d: { val: 3200 } }, { k: "card", skin: "black" }, { k: "row", d: ROWS[1] }, { k: "row", d: ROWS[9] },
  { k: "pill", d: { val: -58.20 } }, { k: "chart", d: { title: "Fatura", big: "R$ 1.840", type: "line", seed: 9 } },
  { k: "row", d: ROWS[7] }, { k: "row", d: ROWS[3] }, { k: "card", skin: "white" }, { k: "pill", d: { val: -239.90 } },
  { k: "row", d: ROWS[4] }, { k: "row", d: ROWS[2] }, { k: "chart", d: { title: "Saldo", big: "R$ 2.104", type: "line", seed: 2, col: GAIN } },
  { k: "row", d: ROWS[5] }, { k: "card", skin: "neon" }, { k: "pill", d: { val: -7.50 } }, { k: "row", d: ROWS[10] }, { k: "row", d: ROWS[11] },
];
export const SIZE = { row: [470, 108], card: [400, 252], pill: [236, 66], chart: [330, 214] };

export function drawHookItem(ctx, it, th, w = SIZE[it.k][0], h = SIZE[it.k][1], o = {}) {
  if (it.k === "row") drawRow(ctx, w, h, it.d, th, o);
  else if (it.k === "card") drawCreditCard(ctx, w, h, it.skin);
  else if (it.k === "pill") drawPill(ctx, w, h, it.d, th);
  else drawMiniChart(ctx, w, h, it.d, th);
}
