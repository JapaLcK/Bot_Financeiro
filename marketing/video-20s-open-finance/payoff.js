// 6–16s · Payoff + Organização. Uma câmera viaja pelo dashboard; cada funcionalidade nasce da anterior:
// linha → Gastos (donut) → cartão → parcelas → timeline → gráfico → dashboard inteiro.
import { W, H, E, seg, bell, inv, lerp, clamp, PINK, rgba, mixHex, rr, rrc, txt, tw, brl, rollText, strokePts } from "./lib.js";
import { ROWS, drawRow, drawCreditCard } from "./items.js";
import { SLOT, cardBg, ghost, cardTitle, DONUT, SEGS, ANG, TOTAL_GASTO, drawDonut, drawHeader, drawSaldo, listRows, RAD } from "./dash.js";

// ------------------------------------------------------------------ câmera
const KEYS = [
  [5.85, -255, 700, 1.3, E.lin], [6.55, -255, 690, 1.85, E.out3], [6.95, -255, 690, 1.85, E.lin],
  [7.5, 255, 690, 1.85, E.io3], [7.85, 255, 690, 1.85, E.lin], [8.45, 0, 1270, 1.0, E.io3],
  [10.0, 0, 1270, 1.03, E.lin], [10.65, 0, 1560, 1.0, E.io3], [11.6, 0, 1560, 1.05, E.lin],
  [13.4, 0, 905, .78, E.io3], [15.9, 0, 890, .805, E.out2],
];
export function camAt(t) {
  let a = KEYS[0];
  for (let i = 1; i < KEYS.length; i++) {
    const b = KEYS[i];
    if (t <= b[0]) { const e = b[4](inv(a[0], b[0], t)); return fin(t, lerp(a[1], b[1], e), lerp(a[2], b[2], e), lerp(a[3], b[3], e)); }
    a = b;
  }
  return fin(t, a[1], a[2], a[3]);
}
function fin(t, x, y, z) {
  z *= 1 - .16 * bell(t, 6.95, 7.5);
  const rot = .022 * Math.sin(Math.PI * inv(6.95, 7.5, t)) - .018 * Math.sin(Math.PI * inv(7.85, 8.45, t)) + .012 * Math.sin(Math.PI * inv(10, 10.65, t)) - .01 * Math.sin(Math.PI * inv(11.6, 13.4, t));
  return { x, y, z, rot };
}
export function toScreen(cam, x, y) {
  const dx = (x - cam.x) * cam.z, dy = (y - cam.y) * cam.z, c = Math.cos(cam.rot), s = Math.sin(cam.rot);
  return [W / 2 + dx * c - dy * s, H / 2 + dx * s + dy * c];
}
function applyCam(ctx, cam) { ctx.translate(W / 2, H / 2); ctx.rotate(cam.rot); ctx.scale(cam.z, cam.z); ctx.translate(-cam.x, -cam.y); }

// ------------------------------------------------------------------ dados ilustrativos
const MONTHS = ["Out", "Nov", "Dez", "Jan", "Fev", "Mar"];
const XS = [0, 1, 2, 3, 4, 5].map(i => -412 + i * 165);
const BAL = [3120, 3340, 3290, 3720, 3980, 4380];
const Y_DOT = BAL.map(v => 1750 - (v - 2800) / 1800 * 250);
const CHIP_Y = 1190, LINE_Y = 1290;
const CARD_W = 520, CARD_H = 328, CARD_C = [0, 1215];

// Catmull-Rom sobre os pontos do gráfico (estendido plano nas pontas)
const KN = [[-460, Y_DOT[0] + 4], ...XS.map((x, i) => [x, Y_DOT[i]]), [460, Y_DOT[5] - 30]];
function curveY(x) {
  let i = 0; while (i < KN.length - 2 && x > KN[i + 1][0]) i++;
  const p0 = KN[Math.max(0, i - 1)], p1 = KN[i], p2 = KN[i + 1], p3 = KN[Math.min(KN.length - 1, i + 2)];
  const u = clamp((x - p1[0]) / (p2[0] - p1[0])), u2 = u * u, u3 = u2 * u;
  return .5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * u + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * u2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * u3);
}

// ------------------------------------------------------------------ cursor (toque) em coordenadas do mundo
const CUR = [
  [6.2, 330, 780, 0], [6.3, 330, 780, 1], [6.5, -215, 575, 1], [6.52, -215, 575, 1], [6.75, -215, 575, 0],
  [7.25, 420, 960, 0], [7.35, 420, 960, 1], [7.6, 330, 840, 1], [7.9, 330, 840, 1], [8.05, 330, 840, 0],
  [9.2, -470, 1300, 0], [9.3, -470, 1300, 1], [10.0, 450, 1300, 1], [10.15, 450, 1300, 0],
  [11.05, -360, 1620, 0], [11.15, -360, 1620, 1], [11.6, 413, 1620, 1], [11.75, 413, 1620, 0],
];
function cursorAt(t) {
  if (t < CUR[0][0] || t > CUR[CUR.length - 1][0]) return null;
  let i = 1; while (CUR[i][0] < t) i++;
  const a = CUR[i - 1], b = CUR[i], e = E.io2(inv(a[0], b[0], t));
  return { x: lerp(a[1], b[1], e), y: lerp(a[2], b[2], e), a: lerp(a[3], b[3], e) };
}
const prox = (cx, x, w = 120) => { const d = (cx - x) / w; return Math.exp(-d * d); };

// ------------------------------------------------------------------ helpers de morph
const lerpRect = (a, b, e) => ({ x: lerp(a.x, b.x, e), y: lerp(a.y, b.y, e), w: lerp(a.w, b.w, e), h: lerp(a.h, b.h, e) });
const ROW0 = { x: SLOT.trans.x + 24, y: SLOT.trans.y + 130 - 40, w: SLOT.trans.w - 48, h: 80 };
const rowIcon = i => [SLOT.trans.x + SLOT.trans.w / 2 - (SLOT.trans.w - 48) / 2 + 80 * .52, SLOT.trans.y + 130 + i * 92];
const ringPos = i => { const R = DONUT.r - DONUT.w / 2, a = ANG[i][2]; return [DONUT.x + Math.cos(a) * R, DONUT.y + Math.sin(a) * R]; };

// ------------------------------------------------------------------ mundo
function world(ctx, t, th) {
  const cur = cursorAt(t);

  // --- cards do dashboard (fantasmas até serem construídos)
  const built = {
    trans: seg(t, 5.85, 6.1, E.out2), gastos: seg(t, 6.6, 7.0, E.out2), parc: seg(t, 8.3, 8.6, E.out2),
    proj: seg(t, 10.1, 10.4, E.out2), saldo: seg(t, 11.9, 12.2, E.out2),
  };
  const float = (k, i) => Math.sin(t * 1.4 + i * 1.7) * 3 * seg(t, 13.6, 14.6);
  const glowA = seg(t, 13.3, 13.6) * (1 - seg(t, 14.3, 14.5)), glowB = seg(t, 14.6, 14.9) * (1 - seg(t, 15.8, 16.0));

  ghost(ctx, SLOT.saldo, th, 1 - built.saldo);
  ghost(ctx, SLOT.gastos, th, 1 - built.gastos);
  ghost(ctx, SLOT.parc, th, 1 - built.parc);
  ghost(ctx, SLOT.proj, th, 1 - built.proj);

  // --- Transações
  ctx.save(); ctx.translate(0, float("trans", 0));
  cardBg(ctx, SLOT.trans, th, { alpha: built.trans, glow: glowA });
  ctx.globalAlpha = built.trans;
  cardTitle(ctx, SLOT.trans, th, "Últimas movimentações");
  ctx.globalAlpha = 1;
  const vis = [];
  for (let i = 0; i < 5; i++) {
    const t0 = 6.0 + .07 * i, tl = 6.7 + .07 * Math.max(0, i - 1), tr = 12.3 + .07 * i;
    const inn = seg(t, t0, t0 + .4, E.out2), yin = (1 - seg(t, t0, t0 + .55, E.outBack)) * 80;
    let a = inn, y = yin, s = 1, x = 0;
    if (i === 0) { if (t > 6.5) { a = t < 6.6 ? a : 0; s = 1 + .05 * seg(t, 6.42, 6.56, E.out2); } }
    else { const q = seg(t, tl, tl + .35, E.in2); a *= 1 - q; s = 1 - .35 * q; }
    if (t > tr) { const q = seg(t, tr, tr + .5, E.outBack); a = seg(t, tr, tr + .3); y = 0; s = 1; x = (1 - q) * -260; }
    vis[i] = { a, y, s };
    if (x) vis[i].x = x;
  }
  // linhas fantasma no lugar das que saíram
  if (t > 6.65) for (let i = 0; i < 5; i++) {
    const v = vis[i]; const q = 1 - clamp(v.a);
    if (q > 0) { rr(ctx, SLOT.trans.x + 24, SLOT.trans.y + 130 - 40 + i * 92, SLOT.trans.w - 48, 80, 24); ctx.fillStyle = th.ghost; ctx.globalAlpha = q * seg(t, 6.7, 7.0); ctx.fill(); ctx.globalAlpha = 1; }
  }
  for (let i = 0; i < 5; i++) {
    const v = vis[i]; if (v.a <= 0) continue;
    ctx.save(); ctx.translate(v.x || 0, 0); listRows(ctx, th, [null, null, null, null, null].map((_, j) => j === i ? v : null)); ctx.restore();
  }
  ctx.restore();

  // --- Gastos: a linha 0 cresce até virar o card
  const m = seg(t, 6.6, 7.25, E.io3);
  const gRect = t < 6.6 ? null : lerpRect(ROW0, SLOT.gastos, m);
  ctx.save(); ctx.translate(0, float("gastos", 1));
  if (gRect) {
    const r = { x: gRect.x, y: gRect.y, w: gRect.w, h: gRect.h };
    ctx.save();
    ctx.shadowColor = th.shadow; ctx.shadowBlur = 50; ctx.shadowOffsetY = 18;
    rr(ctx, r.x, r.y, r.w, r.h, lerp(22, RAD, m)); ctx.fillStyle = th.card; ctx.fill(); ctx.shadowColor = "transparent";
    ctx.lineWidth = 2; ctx.strokeStyle = mixHex(th.line, th.line, 1); ctx.stroke();
    if (glowA > 0) { rr(ctx, r.x, r.y, r.w, r.h, RAD); ctx.lineWidth = 4 * glowA; ctx.strokeStyle = rgba(PINK, glowA); ctx.shadowColor = rgba(PINK, .7 * glowA); ctx.shadowBlur = 40 * glowA; ctx.stroke(); }
    ctx.restore();
    // conteúdo da linha some enquanto o card cresce
    const ra = 1 - seg(t, 6.6, 6.85, E.out2);
    if (ra > 0) {
      ctx.save(); ctx.translate(r.x + r.w / 2, r.y + r.h / 2); ctx.globalAlpha = ra;
      drawRow(ctx, SLOT.trans.w - 48, 80, ROWS[0], th, { shadow: false }); ctx.restore();
    }
  }
  if (t > 6.9) {
    ctx.globalAlpha = seg(t, 6.95, 7.3); cardTitle(ctx, SLOT.gastos, th, "Gastos", "Setembro"); ctx.globalAlpha = 1;
  }
  // bolinhas voam das linhas para o anel; depois cada fatia se abre a partir do ponto
  const prog = SEGS.map((_, i) => {
    if (i === 5) return seg(t, 7.15, 7.6, E.out3);
    const t0 = 6.68 + .06 * i; return seg(t, t0 + .5, t0 + 1.0, E.out3);
  });
  if (t > 6.9) {
    ctx.globalAlpha = seg(t, 6.9, 7.1);
    const hv = 1, hoverAmt = seg(t, 7.5, 7.65, E.outBack) * (1 - seg(t, 7.85, 8.0, E.out2));
    drawDonut(ctx, th, prog, hv, hoverAmt, 1);
    ctx.globalAlpha = 1;
  }
  for (let i = 0; i < 5; i++) {
    const t0 = 6.68 + .06 * i, q = seg(t, t0, t0 + .55, E.io3);
    if (q <= 0 || q >= 1) continue;
    const a = rowIcon(i), b = ringPos(i), arc = Math.sin(Math.PI * q) * (i % 2 ? 90 : -90);
    const x = lerp(a[0], b[0], q) + arc * .6, y = lerp(a[1], b[1], q) - Math.abs(arc) * .35;
    ctx.beginPath(); ctx.arc(x, y, lerp(24, DONUT.w / 2, q), 0, 7); ctx.fillStyle = ROWS[i].col; ctx.shadowColor = rgba(ROWS[i].col, .8); ctx.shadowBlur = 24; ctx.fill(); ctx.shadowBlur = 0;
  }
  // centro do donut
  const cA = seg(t, 7.2, 7.5) * (1 - seg(t, 7.72, 7.85)) + seg(t, 8.6, 8.9) ;
  if (cA > 0 && t > 7) {
    ctx.save(); ctx.globalAlpha = clamp(cA);
    const v = TOTAL_GASTO * seg(t, 7.15, 7.9, E.outExpo);
    txt(ctx, brl(v, 0), DONUT.x, DONUT.y + 10, { size: 56, weight: 800, color: th.ink, align: "center", ls: -2 });
    txt(ctx, "gastos no mês", DONUT.x, DONUT.y + 52, { size: 23, weight: 500, color: th.ink3, align: "center" });
    ctx.restore();
  }
  // tooltip do segmento em foco
  const tip = seg(t, 7.55, 7.7, E.outBack) * (1 - seg(t, 7.85, 7.95));
  if (tip > 0) {
    const am = ANG[1][2], R = DONUT.r + 26, px = DONUT.x + Math.cos(am) * R, py = DONUT.y + Math.sin(am) * R - 8;
    const s = "Delivery · 22%", w = tw(ctx, s, 22, 700) + 34;
    ctx.save(); ctx.translate(px, py); ctx.scale(tip, tip);
    rrc(ctx, w / 2 - 6, 0, w, 46, 23); ctx.fillStyle = th.ink; ctx.fill();
    txt(ctx, s, w / 2 - 6, 8, { size: 22, weight: 700, color: th.bg, align: "center" });
    ctx.restore();
  }
  ctx.restore();

  // --- Parcelas: o painel + (cartão → tiras → chips → timeline → dots)
  ctx.save(); ctx.translate(0, float("parc", 2));
  cardBg(ctx, SLOT.parc, th, { alpha: built.parc, glow: glowB });
  if (t > 8.6) { ctx.globalAlpha = seg(t, 8.8, 9.1); cardTitle(ctx, SLOT.parc, th, "Parcelas", "Cartão •••• 4821"); ctx.globalAlpha = 1; }
  parcelas(ctx, t, th, cur);
  ctx.restore();

  // --- Projeção
  ctx.save(); ctx.translate(0, float("proj", 3));
  cardBg(ctx, SLOT.proj, th, { alpha: built.proj, glow: glowB });
  chart(ctx, t, th, cur, built.proj);
  ctx.restore();

  // --- Saldo e cabeçalho (completam o quadro na hora do recuo)
  ctx.save(); ctx.translate(0, float("saldo", 4));
  if (built.saldo > 0) {
    ctx.save(); const s = lerp(.94, 1, E.outBack(built.saldo)); ctx.translate(0, SLOT.saldo.y + SLOT.saldo.h / 2); ctx.scale(s, s); ctx.translate(0, -(SLOT.saldo.y + SLOT.saldo.h / 2));
    drawSaldo(ctx, th, seg(t, 12.0, 12.95, E.outExpo), { alpha: built.saldo, glow: glowA });
    ctx.restore();
  }
  ctx.restore();
  drawHeader(ctx, th, seg(t, 12.25, 12.75, E.out2));
}

// ------------------------------------------------------------------ cartão → parcelas → timeline
function parcelas(ctx, t, th, cur) {
  // fase 1: o cartão nasce do centro do donut, gira e desce até o painel
  if (t >= 7.7 && t < 8.55) {
    const q = seg(t, 7.72, 8.5, E.io3), spin = seg(t, 7.72, 8.6, E.outBack);
    const w = lerp(120, CARD_W, E.out3(q)), h = w * CARD_H / CARD_W;
    const x = lerp(DONUT.x, CARD_C[0], q), y = lerp(DONUT.y, CARD_C[1], E.inBack(q) * .35 + q * .65) - Math.sin(Math.PI * q) * 60;
    const phi = spin * Math.PI * 2, c = Math.cos(phi);
    ctx.save(); ctx.translate(x, y); ctx.rotate(-.12 * Math.sin(Math.PI * q));
    ctx.scale(Math.max(.02, Math.abs(c)), 1 + .05 * Math.sin(phi));
    ctx.shadowColor = rgba(PINK, .5); ctx.shadowBlur = 60;
    drawCreditCard(ctx, w, h, "pink", { back: c < 0, shadow: false });
    // sombra de borda conforme gira
    ctx.restore();
  }
  // fase 2: o cartão se desdobra em seis tiras que viram chips
  const SN = 6, sw = CARD_W / SN;
  for (let i = 0; i < SN; i++) {
    const t0 = 8.5 + .05 * i, lin = inv(t0, t0 + .7, t);
    if (t < 8.5 || lin <= 0) continue;
    const sx = CARD_C[0] - CARD_W / 2 + sw * (i + .5), sy = CARD_C[1];
    const ex = XS[i], ey = CHIP_Y;
    const e = E.io3(clamp(lin));
    const lift = Math.sin(Math.PI * clamp(lin)) * (-70 - 12 * (i % 2));
    let x = lerp(sx, ex, e), y = lerp(sy, ey, e) + lift;
    const flip = Math.cos(Math.PI * clamp(lin * 1.0)); // 1 → -1: passa por 0 no meio
    const w = lerp(sw, 132, e), h = lerp(CARD_H, 128, e);
    const face = lin < .5; // primeira metade mostra a tira do cartão, depois o chip
    // hover do cursor e timeline
    const pr = cur && t > 9.3 && t < 10.2 ? prox(cur.x, ex) : 0;
    const chipT = seg(t, 10.25, 11.15);
    const eC = E.io3(clamp(chipT * 1.6 - ((ex + 440) / 880) * .6));
    if (eC > 0) y = lerp(y, Y_DOT[i], eC);
    ctx.save();
    ctx.translate(x, y - pr * 26 * (1 - eC));
    const pop = 1 + .1 * pr;
    ctx.rotate(Math.sin(Math.PI * clamp(lin)) * .22 * (i % 2 ? 1 : -1));
    ctx.scale(Math.max(.03, Math.abs(flip)) * pop, pop);
    if (face) {
      ctx.save(); rr(ctx, -w / 2, -h / 2, w, h, lerp(6, 18, e)); ctx.clip();
      ctx.translate(-(-CARD_W / 2 + sw * (i + .5)), 0);
      drawCreditCard(ctx, CARD_W, CARD_H, "pink", { shadow: false });
      ctx.restore();
    } else {
      chipBody(ctx, th, i, w, h, 1 - eC, eC);
    }
    ctx.restore();
  }
  // linha do tempo
  const lp = seg(t, 9.0, 9.6, E.io3), chipT = seg(t, 10.25, 11.15);
  if (lp > 0 && chipT < 1) {
    const aLine = 1 - seg(chipT, 0, .05);
    ctx.save(); ctx.globalAlpha = aLine;
    // a linha é a mesma que depois vira a curva: pontos que se deslocam em onda
    const n = 45, pts = [];
    for (let j = 0; j < n; j++) {
      const u = j / (n - 1), x = -440 + 880 * u;
      const e = E.io3(clamp(chipT * 1.6 - u * .6));
      pts.push([x, lerp(LINE_Y, curveY(x), e)]);
    }
    const last = Math.floor(lp * (n - 1));
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.strokeStyle = rgba(PINK, .25); ctx.lineWidth = 4; ctx.setLineDash([2, 12]);
    strokePts(ctx, pts.slice(0, last + 2)); ctx.stroke(); ctx.setLineDash([]);
    ctx.restore();
  }
  for (let i = 0; i < 6; i++) {
    const tl = 9.0 + .6 * ((XS[i] + 440) / 880), p = seg(t, tl, tl + .35, E.outBack);
    if (p <= 0 || chipT >= .05 + i * .02 && chipT > .2) continue;
    const eC = E.io3(clamp(chipT * 1.6 - ((XS[i] + 440) / 880) * .6)), a = 1 - seg(eC, 0, .35);
    if (a <= 0) continue;
    ctx.save(); ctx.globalAlpha = a;
    ctx.beginPath(); ctx.arc(XS[i], LINE_Y, 9 * p, 0, 7); ctx.fillStyle = i === 0 ? PINK : th.ink3; ctx.fill();
    if (i === 0) { ctx.beginPath(); ctx.arc(XS[i], LINE_Y, 9 + 18 * bell(t % 1.2, 0, 1.2), 0, 7); ctx.lineWidth = 3; ctx.strokeStyle = rgba(PINK, .4); ctx.stroke(); }
    ctx.strokeStyle = th.line; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(XS[i], CHIP_Y + 64 + 6); ctx.lineTo(XS[i], LINE_Y - 10 * p); ctx.stroke();
    txt(ctx, MONTHS[i], XS[i], LINE_Y + 48 + lerp(0, 450, eC), { size: 25, weight: i === 0 ? 800 : 600, color: i === 0 ? PINK : th.ink3, align: "center" });
    ctx.restore();
  }
  // resumo no card depois que as parcelas viram pontos
  const sm = seg(t, 10.75, 11.15, E.outBack);
  if (sm > 0) {
    ctx.save(); ctx.globalAlpha = clamp(sm); ctx.translate(0, (1 - sm) * 30);
    const s = SLOT.parc;
    txt(ctx, "6 parcelas em aberto", s.x + 40, s.y + 148, { size: 26, weight: 600, color: th.ink3 });
    txt(ctx, brl(1499.4 * seg(t, 10.75, 11.4, E.outExpo)), s.x + 40, s.y + 244, { size: 84, weight: 800, color: th.ink, ls: -3.5 });
    for (let i = 0; i < 6; i++) { rr(ctx, s.x + s.w - 40 - 6 * 50 + i * 50, s.y + 214, 40, 14, 7); ctx.fillStyle = i < 2 ? PINK : th.card2; ctx.fill(); }
    txt(ctx, "2 de 6 pagas", s.x + s.w - 40, s.y + 192, { size: 22, weight: 600, color: th.ink3, align: "right" });
    ctx.restore();
  }
}

function chipBody(ctx, th, i, w, h, a, dotT) {
  ctx.save(); ctx.globalAlpha *= a;
  ctx.shadowColor = th.shadow; ctx.shadowBlur = 28; ctx.shadowOffsetY = 10;
  rrc(ctx, 0, 0, w, h, 24); ctx.fillStyle = th.card2; ctx.fill(); ctx.shadowColor = "transparent";
  ctx.lineWidth = 2; ctx.strokeStyle = i === 0 ? rgba(PINK, .7) : th.line; ctx.stroke();
  txt(ctx, `${i + 1}/6`, 0, -h * .1, { size: 38, weight: 800, color: i === 0 ? PINK : th.ink, align: "center", ls: -1 });
  txt(ctx, "R$ 249,90", 0, h * .27, { size: 22, weight: 600, color: th.ink2, align: "center" });
  ctx.restore();
  if (dotT > 0) {
    ctx.save(); ctx.globalAlpha *= dotT;
    ctx.beginPath(); ctx.arc(0, 0, 13, 0, 7); ctx.fillStyle = PINK; ctx.shadowColor = rgba(PINK, .9); ctx.shadowBlur = 24; ctx.fill(); ctx.shadowBlur = 0;
    ctx.lineWidth = 5; ctx.strokeStyle = th.card; ctx.stroke();
    ctx.restore();
  }
}

// ------------------------------------------------------------------ gráfico de projeção
function chart(ctx, t, th, cur, built) {
  const s = SLOT.proj, cT = seg(t, 10.25, 11.15);
  if (built > 0.3) { ctx.save(); ctx.globalAlpha = seg(t, 10.3, 10.6); cardTitle(ctx, s, th, "Projeção de saldo", "6 meses"); ctx.restore(); }
  if (t < 10.2) return;
  ctx.save();
  // grade
  const gA = seg(t, 10.4, 10.8);
  [[1500, "R$ 4,6 mil"], [1625, "R$ 3,7 mil"], [1750, "R$ 2,8 mil"]].forEach(([y, l]) => {
    ctx.globalAlpha = gA; ctx.beginPath(); ctx.moveTo(-470, y); ctx.lineTo(470, y); ctx.lineWidth = 1.5; ctx.strokeStyle = th.line; ctx.setLineDash([6, 10]); ctx.stroke(); ctx.setLineDash([]);
    txt(ctx, l, -470, y - 10, { size: 19, weight: 500, color: th.ink3 });
  });
  ctx.globalAlpha = 1;
  // curva: a mesma polilinha da timeline
  const n = 57, pts = [];
  for (let j = 0; j < n; j++) {
    const u = j / (n - 1), x = -440 + 880 * u, e = E.io3(clamp(cT * 1.6 - u * .6));
    pts.push([x, lerp(LINE_Y, curveY(x), e)]);
  }
  if (cT > 0) {
    // área + faixa de incerteza (só dentro do card do gráfico)
    ctx.save(); rr(ctx, s.x, s.y, s.w, s.h, RAD); ctx.clip();
    const g = ctx.createLinearGradient(0, 1480, 0, 1790); g.addColorStop(0, rgba(PINK, .34)); g.addColorStop(1, rgba(PINK, 0));
    ctx.globalAlpha = seg(cT, .35, .9);
    ctx.beginPath(); ctx.moveTo(pts[0][0], 1790); pts.forEach(p => ctx.lineTo(p[0], p[1])); ctx.lineTo(pts[n - 1][0], 1790); ctx.closePath(); ctx.fillStyle = g; ctx.fill();
    const bandA = seg(t, 10.9, 11.3) * .16;
    ctx.globalAlpha = bandA;
    ctx.beginPath(); const k0 = Math.floor(n * .22);
    for (let j = k0; j < n; j++) { const u = (j - k0) / (n - 1 - k0), spread = 10 + 36 * u; j === k0 ? ctx.moveTo(pts[j][0], pts[j][1] - spread) : ctx.lineTo(pts[j][0], pts[j][1] - spread); }
    for (let j = n - 1; j >= k0; j--) { const u = (j - k0) / (n - 1 - k0), spread = 10 + 36 * u; ctx.lineTo(pts[j][0], pts[j][1] + spread); }
    ctx.closePath(); ctx.fillStyle = PINK; ctx.fill();
    ctx.restore();
    // linha
    ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.lineWidth = lerp(4, 8, cT); ctx.strokeStyle = PINK;
    ctx.shadowColor = rgba(PINK, .6); ctx.shadowBlur = 18 * cT; strokePts(ctx, pts); ctx.stroke(); ctx.shadowBlur = 0;
    // rótulo final
    const fv = seg(t, 10.95, 11.35, E.outBack);
    if (fv > 0) {
      const v = lerp(3120, 4380, seg(t, 10.95, 11.45, E.outExpo)), lab = brl(v, 0), w = tw(ctx, lab, 30, 800) + 44;
      ctx.save(); ctx.translate(XS[5], Y_DOT[5] - 64); ctx.scale(fv, fv);
      rrc(ctx, -30, 27, w, 54, 27); ctx.fillStyle = th.ink; ctx.fill();
      txt(ctx, lab, -30, 38, { size: 30, weight: 800, color: th.bg, align: "center", ls: -.5 });
      ctx.restore();
    }
    // varredura do cursor: linha vertical, ponto e valor
    if (cur && t > 11.1 && t < 11.8) {
      const cxp = clamp(cur.x, -412, 413), yy = curveY(cxp), a = cur.a;
      ctx.globalAlpha = a; ctx.beginPath(); ctx.moveTo(cxp, 1440); ctx.lineTo(cxp, 1790); ctx.lineWidth = 2; ctx.strokeStyle = rgba(PINK, .5); ctx.stroke();
      ctx.beginPath(); ctx.arc(cxp, yy, 14, 0, 7); ctx.fillStyle = "#fff"; ctx.lineWidth = 6; ctx.strokeStyle = PINK; ctx.fill(); ctx.stroke(); ctx.globalAlpha = 1;
    }
  }
  ctx.restore();
}

// ------------------------------------------------------------------ HUD
function cursor(ctx, t, cam) {
  const c = cursorAt(t);
  if (!c || c.a <= 0.01) return;
  const [sx, sy] = toScreen(cam, c.x, c.y);
  const press = bell(t, 6.5, 6.66) + bell(t, 7.72, 7.84) * 0;
  ctx.save(); ctx.globalAlpha = c.a; ctx.translate(sx, sy); ctx.scale(1 - .2 * press, 1 - .2 * press);
  ctx.beginPath(); ctx.arc(0, 0, 40, 0, 7); ctx.fillStyle = "rgba(255,255,255,.28)"; ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.stroke();
  ctx.beginPath(); ctx.arc(0, 0, 14, 0, 7); ctx.fillStyle = "#fff"; ctx.fill();
  ctx.restore();
  // ondinha no toque da linha
  const rp = seg(t, 6.52, 6.95, E.out3);
  if (rp > 0 && rp < 1) { const [px, py] = toScreen(cam, -215, 575); ctx.beginPath(); ctx.arc(px, py, lerp(30, 160, rp), 0, 7); ctx.lineWidth = 6 * (1 - rp); ctx.strokeStyle = rgba("#ffffff", .8 * (1 - rp)); ctx.stroke(); }
}

const TITLES = [
  { t0: 6.42, lines: ["Gastos."], size: 130 },
  { t0: 7.66, lines: ["Parcelamentos."], size: 108 },
  { t0: 9.12, lines: ["Próximos meses."], size: 108 },
  { t0: 10.4, lines: ["Projeções."], size: 130 },
  { t0: 11.75, lines: ["Tudo em um", "só lugar."], size: 104 },
  { t0: 13.35, lines: ["Entenda hoje."], size: 112 },
  { t0: 14.55, lines: ["Planeje o amanhã."], size: 98 },
];

export function drawPayoff(ctx, t, th) {
  const cam = camAt(t);
  ctx.save(); applyCam(ctx, cam); world(ctx, t, th); ctx.restore();
  // escurece (ou clareia) o topo para o título ler sobre o conteúdo
  const scr = seg(t, 6.0, 6.3) * (1 - seg(t, 13.0, 13.6));
  if (scr > 0) {
    const g = ctx.createLinearGradient(0, 0, 0, 470);
    g.addColorStop(0, rgba(th.bg, .96 * scr)); g.addColorStop(.6, rgba(th.bg, .8 * scr)); g.addColorStop(1, rgba(th.bg, 0));
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, 470);
  }
  cursor(ctx, t, cam);
  if (t > 6.3) rollText(ctx, t, TITLES, { x: 70, y: 214, size: 120, ls: -5, ink: th.ink, tEnd: 16.0, stagger: .016, dur: .5 });
}

// retângulos dos cards na tela (o finale os transforma em formas)
export function cardRects(t) {
  const cam = camAt(t);
  return Object.entries(SLOT).filter(([k]) => k !== "header").map(([k, s]) => {
    const [x0, y0] = toScreen(cam, s.x, s.y), [x1, y1] = toScreen(cam, s.x + s.w, s.y + s.h);
    return { k, x: x0, y: y0, w: x1 - x0, h: y1 - y0, r: RAD * cam.z };
  });
}
