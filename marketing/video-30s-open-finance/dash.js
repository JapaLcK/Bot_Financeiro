// O dashboard do PigBank vive no "mundo": a câmera viaja por ele durante o payoff e,
// no fim, recua para revelar o conjunto. Cada widget é desenhado aqui; a coreografia mora em payoff.js.
import { E, clamp, PINK, NEON, CAT, rgba, rr, txt, tw, brl, IMG, rng } from "./lib.js";
import { drawRow, ROWS } from "./items.js";

export const SLOT = {
  header: { x: -500, y: 10, w: 1000, h: 120 },
  saldo: { x: -500, y: 160, w: 1000, h: 250 },
  trans: { x: -500, y: 440, w: 490, h: 560 },
  gastos: { x: 10, y: 440, w: 490, h: 560 },
  parc: { x: -500, y: 1030, w: 1000, h: 340 },
  proj: { x: -500, y: 1400, w: 1000, h: 440 },
};
export const cx = s => s.x + s.w / 2, cy = s => s.y + s.h / 2;
export const RAD = 38;

// Categorias do donut (cores dos tokens do DESIGN.md)
export const SEGS = [
  { v: 34, col: CAT.mercado, name: "Mercado" },
  { v: 22, col: CAT.delivery, name: "Delivery" },
  { v: 14, col: CAT.transporte, name: "Transporte" },
  { v: 12, col: CAT.assinaturas, name: "Streaming" },
  { v: 10, col: CAT.lazer, name: "Academia" },
  { v: 8, col: CAT.outros, name: "Outros" },
];
export const DONUT = { x: cx(SLOT.gastos), y: 742, r: 168, w: 50 };
export const TOTAL_GASTO = 2847.30;

export function cardBg(ctx, s, th, o = {}) {
  const { alpha = 1, lift = 0, glow = 0, fill } = o;
  ctx.save();
  ctx.globalAlpha *= alpha;
  ctx.translate(0, -lift * 10);
  ctx.shadowColor = th.shadow; ctx.shadowBlur = 50 + lift * 30; ctx.shadowOffsetY = 18 + lift * 10;
  rr(ctx, s.x, s.y, s.w, s.h, RAD); ctx.fillStyle = fill || th.card; ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.lineWidth = 2; ctx.strokeStyle = th.line; ctx.stroke();
  if (glow > 0) { ctx.shadowColor = rgba(PINK, .7 * glow); ctx.shadowBlur = 40 * glow; ctx.lineWidth = 4 * glow; ctx.strokeStyle = rgba(PINK, glow); ctx.stroke(); }
  ctx.restore();
}

// card ainda "não construído": contorno tracejado
export function ghost(ctx, s, th, a = 1) {
  if (a <= 0) return;
  ctx.save(); ctx.globalAlpha *= a;
  rr(ctx, s.x, s.y, s.w, s.h, RAD); ctx.fillStyle = th.ghost; ctx.fill();
  ctx.setLineDash([16, 14]); ctx.lineWidth = 2; ctx.strokeStyle = th.line; ctx.stroke();
  ctx.restore();
}

export function cardTitle(ctx, s, th, text, tag) {
  txt(ctx, text, s.x + 34, s.y + 58, { size: 27, weight: 700, color: th.ink2, ls: -.3 });
  if (tag) {
    const w = tw(ctx, tag, 21, 650) + 28;
    rr(ctx, s.x + s.w - 34 - w, s.y + 32, w, 40, 20); ctx.fillStyle = th.card2; ctx.fill();
    txt(ctx, tag, s.x + s.w - 34 - w / 2, s.y + 59, { size: 21, weight: 650, color: th.ink3, align: "center" });
  }
}

// ------------------------------------------------------------------ donut
export function segAngles() {
  const tot = SEGS.reduce((a, s) => a + s.v, 0);
  let a = -Math.PI / 2;
  return SEGS.map(s => { const a0 = a, a1 = a + (s.v / tot) * Math.PI * 2; a = a1; return [a0, a1, (a0 + a1) / 2]; });
}
export const ANG = segAngles();

export function drawDonut(ctx, th, prog, hover = -1, hoverAmt = 0, scale = 1) {
  ctx.save();
  ctx.translate(DONUT.x, DONUT.y); ctx.scale(scale, scale);
  const R = DONUT.r - DONUT.w / 2;
  ctx.beginPath(); ctx.arc(0, 0, R, 0, 7); ctx.lineWidth = DONUT.w; ctx.strokeStyle = th.card2; ctx.stroke();
  SEGS.forEach((s, i) => {
    const p = prog[i]; if (p <= 0) return;
    const [a0, a1, am] = ANG[i], gap = .035, sa = am - (am - a0 - gap) * p, e = am + (a1 - am - gap) * p;
    const k = i === hover ? hoverAmt : 0;
    ctx.save();
    ctx.translate(Math.cos(am) * 16 * k, Math.sin(am) * 16 * k);
    ctx.beginPath(); ctx.arc(0, 0, R, sa - .001, Math.max(sa + .001, e));
    ctx.lineWidth = DONUT.w + 14 * k; ctx.lineCap = "butt"; ctx.strokeStyle = s.col;
    if (k > 0) { ctx.shadowColor = rgba(s.col, .7); ctx.shadowBlur = 30 * k; }
    ctx.stroke();
    ctx.restore();
  });
  ctx.restore();
}

// ------------------------------------------------------------------ cabeçalho e saldo
export function drawHeader(ctx, th, a = 1) {
  const s = SLOT.header;
  ctx.save(); ctx.globalAlpha *= a;
  ctx.save(); ctx.beginPath(); ctx.arc(s.x + 62, s.y + 60, 46, 0, 7); ctx.clip(); ctx.drawImage(IMG.avatar, s.x + 16, s.y + 14, 92, 92); ctx.restore();
  ctx.beginPath(); ctx.arc(s.x + 62, s.y + 60, 49, 0, 7); ctx.lineWidth = 4; ctx.strokeStyle = PINK; ctx.stroke();
  txt(ctx, "Setembro", s.x + 132, s.y + 58, { size: 46, weight: 800, color: th.ink, ls: -1.5 });
  txt(ctx, "Tudo em ordem", s.x + 132, s.y + 96, { size: 25, weight: 500, color: th.ink3 });
  const label = "Open Finance conectado", lw = tw(ctx, label, 23, 650) + 78;
  rr(ctx, s.x + s.w - lw, s.y + 32, lw, 56, 28); ctx.fillStyle = th.card; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = th.line; ctx.stroke();
  ctx.beginPath(); ctx.arc(s.x + s.w - lw + 30, s.y + 60, 10, 0, 7); ctx.fillStyle = NEON; ctx.fill();
  txt(ctx, label, s.x + s.w - lw + 52, s.y + 68, { size: 23, weight: 650, color: th.ink });
  ctx.restore();
}

export function drawSaldo(ctx, th, p, o = {}) {
  const s = SLOT.saldo;
  cardBg(ctx, s, th, o);
  ctx.save(); ctx.globalAlpha *= clamp(p * 3);
  txt(ctx, "Saldo total", s.x + 40, s.y + 62, { size: 27, weight: 600, color: th.ink3 });
  txt(ctx, brl(4280.5 * E.outExpo(p)), s.x + 40, s.y + 156, { size: 92, weight: 800, color: th.ink, ls: -4 });
  const chip = (x, label, val, col) => {
    const w = tw(ctx, label + "  " + val, 24, 650) + 40;
    rr(ctx, x, s.y + 182, w, 48, 24); ctx.fillStyle = th.card2; ctx.fill();
    txt(ctx, label, x + 20, s.y + 214, { size: 24, weight: 500, color: th.ink3 });
    txt(ctx, val, x + 20 + tw(ctx, label + "  ", 24, 500), s.y + 214, { size: 24, weight: 700, color: col });
    return w;
  };
  const w1 = chip(s.x + 40, "Entradas", "+R$ 6.200", th.gain);
  chip(s.x + 40 + w1 + 14, "Saídas", "−R$ 2.847", th.ink);
  // barrinhas do mês
  const r = rng(5), n = 14, bx = s.x + s.w - 40 - n * 26;
  for (let i = 0; i < n; i++) {
    const v = (.3 + r() * .7) * clamp(p * 2 - i / n * .8), h = 120 * v;
    rr(ctx, bx + i * 26, s.y + 206 - h, 17, Math.max(2, h), 7); ctx.fillStyle = i === n - 1 ? PINK : th.card2; ctx.fill();
  }
  ctx.restore();
}

// caixa de título que nasce para cada card
export function listRows(ctx, th, vis) {
  // vis: por linha {a (alpha 0..1), y (deslocamento), s (escala)}
  const s = SLOT.trans;
  for (let i = 0; i < 5; i++) {
    const v = vis[i]; if (!v || v.a <= 0) continue;
    ctx.save();
    ctx.translate(s.x + s.w / 2, s.y + 130 + i * 92 + (v.y || 0));
    ctx.scale(v.s ?? 1, v.s ?? 1);
    ctx.globalAlpha *= v.a;
    drawRow(ctx, s.w - 48, 80, ROWS[i], th, { shadow: false });
    ctx.restore();
  }
}
