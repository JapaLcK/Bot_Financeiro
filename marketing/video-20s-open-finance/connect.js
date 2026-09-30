// 3–6s · Conecte: vórtice → celular PigBank → botão → bordas viram linhas até as instituições → hub → tela.
import { W, H, E, seg, bell, lerp, clamp, inv, DARK, PINK, NEON, GAIN, rgba, mixHex, rr, rrc, txt, tw, icon, brl, IMG, rrPts, bez, morph, strokePts, rollText, FONT } from "./lib.js";
import { VORTEX, heroItems } from "./hook.js";
import { drawRow, ROWS } from "./items.js";

const th = DARK;
export const HUB = [540, 1000];
const PW = 620, PH = 1240, PR = 92;
const NODES = [["Conta", "bank"], ["Cartão", "credit-card"], ["Investimentos", "chart-line-up"], ["Conta digital", "wallet"], ["Poupança", "piggy-bank"], ["Financiamento", "house"]]
  .map(([label, ic], k) => {
    const a = -Math.PI / 2 + k * Math.PI / 3;
    return { label, ic, x: HUB[0] + Math.cos(a) * 372, y: HUB[1] + Math.sin(a) * 470, k };
  });
const BTN = { x: 310, y: 1108, w: 540, h: 112 }; // em coordenadas locais do celular

// ------------------------------------------------------------------ geometria do celular ↔ hub
export function phoneGeom(t) {
  const build = seg(t, 2.95, 3.6, E.outBack), buildW = seg(t, 2.95, 3.4, E.outExpo);
  const p = seg(t, 4.6, 5.15, E.io3);
  let w = lerp(150, PW, buildW), h = lerp(150, PH, build), r = lerp(75, PR, buildW);
  const cy0 = lerp(VORTEX[1], 1110, build);
  w = lerp(w, 222, p); h = lerp(h, 222, p); r = lerp(r, 111, p);
  return { w, h, r, cx: 540, cy: lerp(cy0, HUB[1], p), sx: w / PW, sy: h / PH, p };
}

// ponto local do celular → tela
function toScreen(g, lx, ly) { return [g.cx + (lx - PW / 2) * g.sx, g.cy + (ly - PH / 2) * g.sy]; }

// ------------------------------------------------------------------ UI do celular
const SLOTS = [0, 1, 2, 3].map(i => [310, 560 + 112 * i]);
const HERO_ROWS = [ROWS[0], ROWS[6], ROWS[8], ROWS[1]];

function ui(ctx, t) {
  const a = seg(t, 3.3, 3.85, E.out3);
  if (a <= 0) return;
  ctx.save();
  ctx.globalAlpha *= a * (1 - seg(t, 4.6, 4.85, E.out2));
  ctx.translate(0, (1 - a) * 50);
  txt(ctx, "9:41", 44, 70, { size: 25, weight: 650, color: th.ink2 });
  rrc(ctx, PW / 2, 46, 168, 42, 21); ctx.fillStyle = "#000"; ctx.fill();
  // cabeçalho
  ctx.save(); ctx.beginPath(); ctx.arc(80, 140, 34, 0, 7); ctx.clip(); ctx.drawImage(IMG.avatar, 46, 106, 68, 68); ctx.restore();
  ctx.beginPath(); ctx.arc(80, 140, 36, 0, 7); ctx.lineWidth = 3; ctx.strokeStyle = PINK; ctx.stroke();
  txt(ctx, "PigBank", 132, 136, { size: 31, weight: 750, color: th.ink, ls: -.5 });
  txt(ctx, "Setembro", 132, 168, { size: 23, weight: 500, color: th.ink3 });
  icon(ctx, "bell", 560, 140, 38, th.ink2);
  // saldo
  rr(ctx, 30, 206, 560, 214, 34); ctx.fillStyle = th.card; ctx.fill(); ctx.strokeStyle = th.line; ctx.lineWidth = 1.5; ctx.stroke();
  txt(ctx, "Saldo total", 62, 262, { size: 26, weight: 550, color: th.ink3 });
  const bal = 4280.5 * seg(t, 3.5, 4.2, E.outExpo);
  txt(ctx, brl(bal), 62, 338, { size: 64, weight: 800, color: th.ink, ls: -2 });
  ctx.beginPath();
  const pts = [.2, .35, .28, .5, .42, .62, .55, .8];
  pts.forEach((v, i) => { const x = 62 + i * 70, y = 392 - v * 34; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.lineWidth = 5; ctx.strokeStyle = PINK; ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.stroke();
  // movimentações (as linhas vêm do caos)
  txt(ctx, "Movimentações", 36, 476, { size: 27, weight: 700, color: th.ink });
  for (let i = 0; i < 4; i++) { rr(ctx, 30, 514 + 112 * i, 560, 92, 26); ctx.fillStyle = th.ghost; ctx.fill(); }
  // botão
  const pulse = 1 + .018 * Math.sin(t * 7) * seg(t, 3.9, 4.1) * (1 - seg(t, 4.4, 4.45));
  const press = 1 - .06 * bell(t, 4.42, 4.6) * (t < 4.6 ? 1 : 0);
  const fillA = 1 - seg(t, 4.5, 4.66, E.out2);
  ctx.save();
  ctx.translate(BTN.x, BTN.y); ctx.scale(pulse * press, pulse * press);
  ctx.shadowColor = rgba(PINK, .55 * fillA); ctx.shadowBlur = 40; ctx.shadowOffsetY = 12;
  rrc(ctx, 0, 0, BTN.w, BTN.h, 56); ctx.fillStyle = rgba(PINK, fillA); ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.globalAlpha *= 1 - seg(t, 4.5, 4.62);
  icon(ctx, "bank", -BTN.w / 2 + 74, 0, 40, "#fff");
  txt(ctx, "Conectar Open Finance", 18, 11, { size: 31, weight: 720, color: "#fff", align: "center", ls: -.4 });
  ctx.restore();
  ctx.restore();
}

function drawPhone(ctx, t, g) {
  const fillMix = seg(t, 3.05, 3.5, E.out2);
  const fill = mixHex(PINK, "#050506", fillMix);
  ctx.save();
  ctx.shadowColor = rgba(PINK, .4 * (1 - g.p * .4)); ctx.shadowBlur = 90;
  rrc(ctx, g.cx, g.cy, g.w, g.h, g.r); ctx.fillStyle = fill; ctx.fill();
  ctx.restore();
  ctx.save();
  rrc(ctx, g.cx, g.cy, g.w, g.h, g.r); ctx.clip();
  // UI (em coordenadas locais do celular)
  ctx.translate(g.cx - g.w / 2, g.cy - g.h / 2); ctx.scale(g.sx, g.sy);
  ui(ctx, t);
  ctx.restore();
  // borda
  rrc(ctx, g.cx, g.cy, g.w, g.h, g.r);
  ctx.lineWidth = lerp(3, 7, g.p); ctx.strokeStyle = mixHex("#2f2f36", PINK, Math.max(seg(t, 3.0, 3.3) * (1 - fillMix), g.p)); ctx.stroke();
}

// heróis do caos pousam nas linhas do app (match cut)
function heroes(ctx, t, g) {
  heroItems.forEach((it, i) => {
    const t0 = 3.05 + i * .1, e = seg(t, t0, t0 + .55, E.outBack);
    if (e <= 0) return;
    const [lx, ly] = SLOTS[i];
    const [tx, ty] = toScreen(g, lx, ly);
    const x = lerp(VORTEX[0], tx, E.out3(clamp(e))), y = lerp(VORTEX[1], ty, E.out3(clamp(e)));
    const w = lerp(60, 560 * g.sx, e), h = lerp(40, 92 * g.sy, e);
    ctx.save();
    rrc(ctx, g.cx, g.cy, g.w, g.h, g.r); ctx.clip();
    ctx.translate(x, y);
    ctx.globalAlpha *= 1 - seg(t, 4.6, 4.85, E.out2);
    drawRow(ctx, w, h, HERO_ROWS[i], th, { shadow: false });
    ctx.restore();
  });
}

// ------------------------------------------------------------------ toque
function cursor(ctx, t) {
  const a = seg(t, 3.85, 4.0) * (1 - seg(t, 4.62, 4.8));
  if (a <= 0) return;
  const tx = 560, ty = 1612, e = seg(t, 3.85, 4.4, E.out4);
  const x = lerp(940, tx, e) + Math.sin(e * 3) * 10 * (1 - e), y = lerp(1800, ty, e);
  const press = bell(t, 4.4, 4.58);
  ctx.save(); ctx.globalAlpha = a;
  // ondinha do toque
  const rp = seg(t, 4.42, 4.85, E.out3);
  if (rp > 0 && rp < 1) { ctx.beginPath(); ctx.arc(tx, ty, lerp(20, 130, rp), 0, 7); ctx.lineWidth = 5 * (1 - rp); ctx.strokeStyle = rgba("#fff", .8 * (1 - rp)); ctx.stroke(); }
  ctx.translate(x, y); ctx.scale(1 - .22 * press, 1 - .22 * press);
  ctx.beginPath(); ctx.arc(0, 0, 38, 0, 7); ctx.fillStyle = "rgba(255,255,255,.35)"; ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.stroke();
  ctx.beginPath(); ctx.arc(0, 0, 14, 0, 7); ctx.fillStyle = "#fff"; ctx.fill();
  ctx.restore();
}

// ------------------------------------------------------------------ linhas fluidas
const K = NODES.length, SEGN = 24;
function strandTarget(k) {
  const n = NODES[k], dx = n.x - HUB[0], dy = n.y - HUB[1], L = Math.hypot(dx, dy), px = -dy / L, py = dx / L, side = k % 2 ? 1 : -1;
  return bez(HUB, [HUB[0] + dx * .35 + px * 130 * side, HUB[1] + dy * .35 + py * 130 * side], [HUB[0] + dx * .72 - px * 130 * side, HUB[1] + dy * .72 - py * 130 * side], [n.x, n.y], SEGN + 1);
}
const TARGET = NODES.map((_, k) => strandTarget(k));

function strands(ctx, t, g) {
  const s0 = 4.52;
  if (t < s0) return;
  // fonte: a borda do botão, levada pela transformação do celular
  const btn = rrPts(0, 0, BTN.w, BTN.h, 56, K * SEGN + 1);
  const expand = 1 + .1 * bell(t, 4.5, 4.75);
  const ret = []; // quanto cada linha já recolheu
  NODES.forEach((n, k) => {
    const src = [];
    for (let i = 0; i <= SEGN; i++) { const p = btn[k * SEGN + i]; src.push(toScreen(g, BTN.x + p[0] * expand, BTN.y + p[1] * expand)); }
    const tgt = TARGET[k];
    const s = inv(s0 + .03 * k, s0 + .03 * k + .8, t);
    const pts = src.map((p, i) => {
      const u = i / SEGN, e = E.io3(clamp(s * 1.45 - (1 - u) * .45 * 1.0));
      return [lerp(p[0], tgt[i][0], e), lerp(p[1], tgt[i][1], e)];
    });
    const r = seg(t, 5.52 + .035 * k, 5.88 + .035 * k, E.io3);
    const last = Math.max(1, Math.floor((1 - r) * SEGN));
    if (r >= 1) return;
    ctx.save();
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.shadowColor = rgba(PINK, .8); ctx.shadowBlur = 22;
    const grad = ctx.createLinearGradient(pts[0][0], pts[0][1], pts[last][0], pts[last][1]);
    grad.addColorStop(0, rgba(PINK, .95)); grad.addColorStop(1, rgba("#ff8cc4", .95));
    ctx.strokeStyle = grad; ctx.lineWidth = lerp(6, 4, s);
    strokePts(ctx, pts.slice(0, last + 1)); ctx.stroke();
    ctx.restore();
    // pacotes de dados
    const arrive = s0 + .03 * k + .75;
    if (t > arrive && r <= 0) {
      for (let j = 0; j < 3; j++) {
        const u = 1 - (((t - arrive) * .8 + j / 3) % 1), idx = u * SEGN, i0 = Math.floor(idx), f = idx - i0;
        const p = [lerp(pts[i0][0], pts[Math.min(SEGN, i0 + 1)][0], f), lerp(pts[i0][1], pts[Math.min(SEGN, i0 + 1)][1], f)];
        ctx.beginPath(); ctx.arc(p[0], p[1], 8, 0, 7); ctx.fillStyle = "#fff"; ctx.shadowColor = PINK; ctx.shadowBlur = 18; ctx.fill(); ctx.shadowBlur = 0;
      }
    }
    ret[k] = { tip: pts[last], arrive };
  });
  return ret;
}

function nodes(ctx, t, tips) {
  NODES.forEach((n, k) => {
    const tN = 4.52 + .03 * k + .6, born = seg(t, tN, tN + .4, E.outBack);
    const r = seg(t, 5.52 + .035 * k, 5.88 + .035 * k, E.io3);
    if (born <= 0 || r >= 1) return;
    const tp = TARGET[k], idx = (1 - r) * SEGN, i0 = Math.min(SEGN - 1, Math.floor(idx)), f = idx - i0;
    const x = lerp(tp[i0][0], tp[i0 + 1][0], f), y = lerp(tp[i0][1], tp[i0 + 1][1], f);
    const s = born * (1 - .75 * r);
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.globalAlpha = 1 - seg(t, 5.8 + .035 * k, 5.9 + .035 * k);
    const ok = seg(t, tN + .15, tN + .35, E.outBack);
    ctx.shadowColor = th.shadow; ctx.shadowBlur = 40; ctx.shadowOffsetY = 14;
    ctx.beginPath(); ctx.arc(0, 0, 66, 0, 7); ctx.fillStyle = th.card; ctx.fill(); ctx.shadowColor = "transparent";
    ctx.lineWidth = 4; ctx.strokeStyle = mixHex("#2f2f36", PINK, ok); ctx.stroke();
    icon(ctx, n.ic, 0, 0, 58, mixHex("#a8a8b3", "#fff", ok));
    txt(ctx, n.label, 0, 112, { size: 27, weight: 650, color: th.ink2, align: "center" });
    if (ok > 0) {
      ctx.save(); ctx.translate(46, -46); ctx.scale(ok, ok);
      ctx.beginPath(); ctx.arc(0, 0, 24, 0, 7); ctx.fillStyle = NEON; ctx.fill(); ctx.lineWidth = 4; ctx.strokeStyle = th.bg; ctx.stroke();
      icon(ctx, "check", 0, 0, 28, "#111"); ctx.restore();
    }
    ctx.restore();
  });
}

// ------------------------------------------------------------------ hub
export function hubRadius(t) {
  const grow = seg(t, 5.88, 6.4, E.ioExpo);
  return lerp(111, 1500, grow);
}
function hubPulse(t) {
  let v = 0;
  for (let k = 0; k < K; k++) v += bell(t, 5.78 + .035 * k, 5.98 + .035 * k) * .05;
  return 1 + v;
}
function hubLogo(ctx, t, g) {
  const a = seg(t, 4.85, 5.2, E.out2) * (1 - seg(t, 6.0, 6.25, E.in2));
  if (a <= 0) return;
  ctx.save(); ctx.globalAlpha = a;
  const s = hubPulse(t) * (1 + seg(t, 5.9, 6.3, E.in3) * 2.4);
  ctx.translate(g.cx, g.cy); ctx.scale(s, s);
  const w = 118, h = w * IMG.simbolo.height / IMG.simbolo.width;
  ctx.drawImage(IMG.simbolo, -w / 2, -h / 2 - 2, w, h);
  ctx.restore();
}

// ------------------------------------------------------------------ títulos
function titles(ctx, t) {
  rollText(ctx, t, [{ t0: 3.42, lines: ["Conecte seu", "Open Finance."], hl: "Open Finance" }], { x: 70, y: 232, size: 100, ls: -4, ink: "#fff", tEnd: 5.0, stagger: .018 });
  rollText(ctx, t, [{ t0: 5.0, lines: ["É simples."] }], { x: 540, y: 1735, size: 150, ls: -6, align: "center", ink: "#fff", tEnd: 5.92, stagger: .03 });
  // check que acompanha o "É simples."
}

export function drawConnect(ctx, t) {
  if (t < 2.9 || t > 6.5) return;
  const g = phoneGeom(t);
  const hubT = t > 5.88 ? hubRadius(t) : null;
  if (hubT && hubT > 1000) return;
  ctx.save();
  if (t < 5.6) {
    // a parte do celular some quando ele vira hub (continua visível como círculo)
  }
  drawPhone(ctx, t, g);
  heroes(ctx, t, g);
  hubLogo(ctx, t, g);
  ctx.restore();
  const tips = strands(ctx, t, g);
  nodes(ctx, t, tips);
  cursor(ctx, t);
  titles(ctx, t);
}
