/* global document */
// Final · Segurança + marca (tempo de composição 16–22s). Cards → formas (toggle, linha, escudo, nós) →
// toggle DESLIGADO: a conexão bate no escudo · toggle LIGADO: a conexão passa ao PigBank → o dinheiro
// não passa → contorno do Piggy → marca.
import { W, H, E, seg, bell, lerp, clamp, PINK, NEON, rgba, mixHex, rrc, txt, tw, icon, rollText, rrPts, shieldPts, morph, strokePts, bez, resample, IMG } from "./lib.js";
import { cardRects } from "./payoff.js";

const N = 96;
const NODE_R = 80;
const B = [175, 980], P = [905, 980], C = [540, 980];
const X0 = B[0] + NODE_R, X1 = P[0] - NODE_R; // a linha vai da borda do banco à borda do PigBank
const SH = { w: 260, h: 320 }, SH_L = C[0] - SH.w / 2 + 6, SH_R = C[0] + SH.w / 2 - 6;
const TOG = { x: 540, y: 1290, w: 280, h: 140 };

const MAIN = Array.from({ length: N }, (_, i) => [lerp(X0, X1, i / (N - 1)), C[1]]);
const ARC_UP = bez([X0, C[1]], [X0 + 140, C[1] - 230], [X1 - 140, C[1] - 230], [X1, C[1]], N);
const ARC_DN = bez([X0, C[1]], [X0 + 140, C[1] + 230], [X1 - 140, C[1] + 230], [X1, C[1]], N);
const SHIELD = shieldPts(C[0], C[1], SH.w, SH.h, N);
const PILL = rrPts(TOG.x, TOG.y, TOG.w, TOG.h, TOG.h / 2, N);
const RING_B = rrPts(B[0], B[1], NODE_R * 2, NODE_R * 2, NODE_R, N);
const RING_P = rrPts(P[0], P[1], NODE_R * 2, NODE_R * 2, NODE_R, N);
const SOURCES = [MAIN, ARC_UP, ARC_DN, SHIELD, PILL, RING_B, RING_P];

// ------------------------------------------------------------------ contorno do Piggy (do alfa do mascote oficial)
const MASCOT = { cx: 540, cy: 900, bh: 720, bbox: [28, 33, 282, 460] };
let PIG = []; // um trecho de N pontos por fonte, em coordenadas de tela
export function prepareFinale() {
  const img = IMG.mascot, c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
  const g = c.getContext("2d", { willReadFrequently: true }); g.drawImage(img, 0, 0);
  const { data, width: w, height: h } = g.getImageData(0, 0, img.width, img.height);
  const on = (x, y) => x >= 0 && y >= 0 && x < w && y < h && data[(y * w + x) * 4 + 3] > 110;
  // Moore-neighbor: começa no primeiro pixel do alto e contorna a silhueta externa
  let sx = -1, sy = -1;
  outer: for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (on(x, y)) { sx = x; sy = y; break outer; }
  const D = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
  const pts = [[sx, sy]];
  let x = sx, y = sy, dir = 6;
  for (let guard = 0; guard < 20000; guard++) {
    let found = false;
    for (let i = 0; i < 8; i++) {
      const d = (dir + 6 + i) % 8, nx = x + D[d][0], ny = y + D[d][1];
      if (on(nx, ny)) { x = nx; y = ny; dir = d; found = true; break; }
    }
    if (!found || (x === sx && y === sy)) break;
    pts.push([x, y]);
  }
  const sm = pts.map((_, i) => { let ax = 0, ay = 0; for (let k = -4; k <= 4; k++) { const p = pts[(i + k + pts.length) % pts.length]; ax += p[0]; ay += p[1]; } return [ax / 9, ay / 9]; });
  let area = 0; sm.forEach((p, i) => { const q = sm[(i + 1) % sm.length]; area += p[0] * q[1] - q[0] * p[1]; });
  if (area < 0) sm.reverse(); // sentido horário na tela
  const all = resample(sm, N * SOURCES.length, true);
  const [bx0, by0, bx1, by1] = MASCOT.bbox, k = MASCOT.bh / (by1 - by0), bcx = (bx0 + bx1) / 2, bcy = (by0 + by1) / 2;
  const scr = all.map(p => [MASCOT.cx + (p[0] - bcx) * k, MASCOT.cy + (p[1] - bcy) * k]);
  PIG = SOURCES.map((_, s) => resample(scr.slice(s * N, (s + 1) * N + 1), N, false));
}

// ------------------------------------------------------------------ estado do toggle e da conexão
const knobAt = t => seg(t, 17.1, 17.55, E.io3); // 0 = desligado, 1 = ligado (suave, ~0,45s)
const V = (SH_L - X0) / .4; // px por segundo de composição: a conexão leva 0,4s até o escudo
const BLOCKED = [16.45, 16.78]; // chegam com o toggle desligado e batem no escudo
const PASSING = Array.from({ length: 10 }, (_, i) => 17.45 + .22 * i); // com ele ligado, atravessam
const COINS = [18.05, 18.6]; // o dinheiro tenta passar e volta

function packet(tl, t) {
  const dt = t - tl;
  if (dt < 0) return null;
  const ta = tl + .4;
  if (t < ta) return { x: X0 + V * dt, a: clamp(dt / .08) };
  if (knobAt(ta) < .5) { const q = (t - ta) / .3; return q > 1 ? null : { x: SH_L - 90 * E.out2(q), a: 1 - q }; }
  const x = SH_L + V * (t - ta);
  return x > X1 ? null : { x, a: 1 - clamp((x - (X1 - 50)) / 50) };
}

function coin(tl, t) {
  const dt = t - tl;
  if (dt < 0 || dt > .8) return null;
  if (dt < .4) return { x: lerp(X0 + 40, SH_L - 44, E.in2(dt / .4)), sq: 0, a: 1 };
  const q = (dt - .4) / .4;
  return { x: lerp(SH_L - 44, X0 + 70, E.out3(q)), sq: clamp(1 - q * 3), a: 1 - seg(q, .6, 1) };
}

// ------------------------------------------------------------------ peças
const glow = (ctx, pts, a, w = 6, col = PINK) => {
  ctx.save(); ctx.globalAlpha *= a; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.lineWidth = w; ctx.strokeStyle = col;
  ctx.shadowColor = rgba(col, .8); ctx.shadowBlur = 20; strokePts(ctx, pts); ctx.stroke(); ctx.restore();
};

function node(ctx, th, at, label, pic, a, s = 1) {
  if (a <= 0) return;
  ctx.save(); ctx.translate(at[0], at[1]); ctx.scale(s, s); ctx.globalAlpha *= a;
  ctx.shadowColor = th.shadow; ctx.shadowBlur = 50; ctx.shadowOffsetY = 16;
  ctx.beginPath(); ctx.arc(0, 0, NODE_R, 0, 7); ctx.fillStyle = th.card; ctx.fill(); ctx.shadowColor = "transparent";
  ctx.lineWidth = 4; ctx.strokeStyle = pic === "pig" ? PINK : th.line; ctx.stroke();
  if (pic === "pig") { const w = 88, h = w * IMG.simbolo.height / IMG.simbolo.width; ctx.drawImage(IMG.simbolo, -w / 2, -h / 2 - 2, w, h); }
  else icon(ctx, pic, 0, 0, 66, th.ink);
  txt(ctx, label, 0, NODE_R + 46, { size: 28, weight: 650, color: th.ink2, align: "center" });
  ctx.restore();
}

function coinDraw(ctx, x, sq, a) {
  ctx.save(); ctx.globalAlpha *= a; ctx.translate(x, C[1]); ctx.scale(1 - .2 * sq, 1 + .2 * sq);
  ctx.shadowColor = rgba(NEON, .6); ctx.shadowBlur = 30;
  ctx.beginPath(); ctx.arc(0, 0, 40, 0, 7); ctx.fillStyle = NEON; ctx.fill(); ctx.shadowBlur = 0;
  ctx.lineWidth = 5; ctx.strokeStyle = "#8fb612"; ctx.stroke();
  txt(ctx, "R$", 0, 11, { size: 30, weight: 850, color: "#111", align: "center", ls: -1 });
  ctx.restore();
}

function shield(ctx, th, t, pulse, a = 1) {
  const on = knobAt(t), swap = seg(t, 17.3, 17.6);
  ctx.save(); ctx.globalAlpha *= a;
  ctx.translate(C[0], C[1]); ctx.scale(1 + pulse, 1 + pulse); ctx.translate(-C[0], -C[1]);
  ctx.shadowColor = th.shadow; ctx.shadowBlur = 50; ctx.shadowOffsetY = 14;
  strokePts(ctx, SHIELD, true); ctx.fillStyle = mixHex(th.card, "#1d2a08", .0 + on * (th.name === "dark" ? .35 : 0)); ctx.fill(); ctx.shadowColor = "transparent";
  ctx.lineWidth = 7; ctx.lineJoin = "round"; ctx.strokeStyle = mixHex(th.line, NEON, on);
  ctx.shadowColor = rgba(NEON, .55 * on); ctx.shadowBlur = 26 * on; ctx.stroke(); ctx.shadowBlur = 0;
  ctx.globalAlpha *= 1 - swap; icon(ctx, "lock-key", C[0], C[1] + 4, 120, th.ink);
  ctx.globalAlpha = a * swap; icon(ctx, "check", C[0], C[1] + 4, 128, NEON);
  ctx.restore();
}

function toggle(ctx, th, t, a = 1) {
  const on = knobAt(t);
  ctx.save(); ctx.globalAlpha *= a;
  ctx.shadowColor = th.shadow; ctx.shadowBlur = 40; ctx.shadowOffsetY = 12;
  rrc(ctx, TOG.x, TOG.y, TOG.w, TOG.h, TOG.h / 2); ctx.fillStyle = mixHex(th.card2, NEON, on); ctx.fill(); ctx.shadowColor = "transparent";
  ctx.lineWidth = 4; ctx.strokeStyle = mixHex(th.line, NEON, on); ctx.stroke();
  const kx = lerp(TOG.x - 70, TOG.x + 70, on);
  ctx.beginPath(); ctx.arc(kx, TOG.y, 52, 0, 7); ctx.fillStyle = "#fff"; ctx.shadowColor = "rgba(0,0,0,.35)"; ctx.shadowBlur = 18; ctx.shadowOffsetY = 4; ctx.fill();
  ctx.shadowColor = "transparent";
  const lab = on > .5 ? "Ligado" : "Desligado";
  txt(ctx, lab, TOG.x, TOG.y + 118, { size: 30, weight: 700, color: on > .5 ? (th.name === "dark" ? NEON : "#3FA400") : th.ink3, align: "center" });
  ctx.restore();
}

function touch(ctx, t) {
  const a = seg(t, 16.85, 16.98) * (1 - seg(t, 17.6, 17.75));
  if (a <= 0) return;
  const arrive = seg(t, 16.85, 17.05, E.out3), drag = knobAt(t);
  const x = lerp(lerp(760, TOG.x - 70, arrive), TOG.x + 70, drag), y = lerp(1560, TOG.y, arrive);
  const press = bell(t, 17.05, 17.2);
  const rp = seg(t, 17.06, 17.45, E.out3);
  ctx.save(); ctx.globalAlpha = a;
  if (rp > 0 && rp < 1) { ctx.beginPath(); ctx.arc(TOG.x - 70, TOG.y, lerp(30, 120, rp), 0, 7); ctx.lineWidth = 5 * (1 - rp); ctx.strokeStyle = rgba("#ffffff", .8 * (1 - rp)); ctx.stroke(); }
  ctx.translate(x, y); ctx.scale(1 - .2 * press, 1 - .2 * press);
  ctx.beginPath(); ctx.arc(0, 0, 40, 0, 7); ctx.fillStyle = "rgba(255,255,255,.28)"; ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.stroke();
  ctx.beginPath(); ctx.arc(0, 0, 14, 0, 7); ctx.fillStyle = "#fff"; ctx.fill();
  ctx.restore();
}

export function drawFinale(ctx, t, th) {
  if (t < 15.85) return;

  // -------- cards → formas: cobrem o conteúdo e viram toggle, linha, escudo e os dois nós
  const R0 = cardRects(15.9);
  const cover = seg(t, 15.92, 16.15, E.out2);
  const order = ["saldo", "trans", "gastos", "parc", "proj"], targets = [PILL, MAIN, SHIELD, RING_B, RING_P], keep = [true, false, true, true, true];
  const e = order.map((_, i) => seg(t, 16.05 + .05 * i, 16.55 + .05 * i, E.io3));
  if (t < 16.85) order.forEach((k, i) => {
    const r = R0.find(q => q.k === k); if (!r || e[i] >= 1) return;
    const pts = morph(rrPts(r.x + r.w / 2, r.y + r.h / 2, r.w, r.h, r.r, N), targets[i], e[i]);
    ctx.save();
    const fillA = keep[i] ? cover : cover * (1 - seg(e[i], 0, .35));
    if (fillA > 0) { strokePts(ctx, pts, true); ctx.globalAlpha = fillA; ctx.fillStyle = th.card; ctx.fill(); ctx.globalAlpha = 1; }
    ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.lineWidth = lerp(2, 5, e[i]); ctx.strokeStyle = mixHex(th.line, PINK, seg(e[i], 0, .5));
    if (e[i] > .2) { ctx.shadowColor = rgba(PINK, .6); ctx.shadowBlur = 16 * e[i]; }
    strokePts(ctx, pts, i !== 1 || e[i] < .6); ctx.stroke();
    ctx.restore();
  });

  const done = seg(t, 16.55, 16.85); // a partir daqui os objetos já são os de verdade
  const fade = 1 - seg(t, 19.85, 20.1); // na convergência, preenchimentos e ícones somem e ficam só os contornos
  if (done > 0 && t < 20.5) {
    ctx.save(); ctx.globalAlpha = done;
    // fios: um principal e dois bem discretos
    glow(ctx, ARC_UP, .22 * seg(t, 16.8, 17.1) * fade, 3); glow(ctx, ARC_DN, .22 * seg(t, 16.8, 17.1) * fade, 3);
    const on = knobAt(t), iL = Math.round(((SH_L - X0) / (X1 - X0)) * (N - 1)), iR = Math.round(((SH_R - X0) / (X1 - X0)) * (N - 1));
    if (fade > 0) {
      glow(ctx, MAIN.slice(0, iL + 1), fade, 6);
      glow(ctx, MAIN.slice(iR), (.18 + .82 * on) * fade, 6);
    }
    // conexão: bate no escudo com o toggle desligado, atravessa ligado
    ctx.save(); ctx.globalAlpha *= fade;
    let hit = 0, recv = 0;
    [...BLOCKED, ...PASSING].forEach(tl => {
      const p = packet(tl, t); if (!p) return;
      ctx.globalAlpha = p.a * fade * done; ctx.beginPath(); ctx.arc(p.x, C[1], 9, 0, 7); ctx.fillStyle = "#fff"; ctx.shadowColor = PINK; ctx.shadowBlur = 20; ctx.fill(); ctx.shadowBlur = 0;
    });
    ctx.globalAlpha = done * fade;
    BLOCKED.forEach(tl => { hit += bell(t, tl + .4, tl + .6) * .05 * (knobAt(tl + .4) < .5 ? 1 : 0); });
    PASSING.forEach(tl => { recv += bell(t, tl + .4 + (X1 - SH_L) / V - .05, tl + .4 + (X1 - SH_L) / V + .25) * .05; });
    COINS.forEach(tl => { const c = coin(tl, t); if (c) { coinDraw(ctx, c.x, c.sq, c.a); hit += bell(t, tl + .4, tl + .6) * .06; } });
    ctx.restore();
    ctx.globalAlpha = done * fade;
    node(ctx, th, B, "Seu banco", "bank", 1);
    node(ctx, th, P, "PigBank", "pig", 1, 1 + recv);
    // "só leitura" junto do PigBank
    const ro = seg(t, 18.1, 18.4, E.outBack) * fade;
    if (ro > 0) {
      ctx.save(); ctx.translate(P[0], P[1] - NODE_R - 50); ctx.scale(ro, ro);
      const s = "Só leitura", w = tw(ctx, s, 25, 700) + 84;
      rrc(ctx, 0, 0, w, 54, 27); ctx.fillStyle = th.card; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = th.line; ctx.stroke();
      icon(ctx, "eye", -w / 2 + 36, 0, 32, NEON); txt(ctx, s, 26, 9, { size: 25, weight: 700, color: th.ink, align: "center" });
      ctx.restore();
    }
    ctx.globalAlpha = done * fade;
    shield(ctx, th, t, hit, 1);
    toggle(ctx, th, t, 1);
    // ondas: ao ligar e a cada batida do dinheiro
    [[17.3, NEON, 300], ...COINS.map(c => [c + .4, PINK, 240])].forEach(([t0, col, r1]) => {
      const q = seg(t, t0, t0 + .5, E.out3);
      if (q > 0 && q < 1) { ctx.beginPath(); ctx.arc(C[0], C[1], lerp(150, r1, q), 0, 7); ctx.lineWidth = 6 * (1 - q); ctx.strokeStyle = rgba(col, .8 * (1 - q)); ctx.stroke(); }
    });
    ctx.restore();
    touch(ctx, t);
  }

  // -------- convergência: contornos viram o contorno do Piggy; depois o mascote e a marca
  if (PIG.length && t >= 19.85) {
    const out = 1 - seg(t, 20.45, 20.7, E.out2);
    SOURCES.forEach((p, k) => {
      const m = seg(t, 19.85 + .04 * k, 20.4 + .04 * k, E.io3);
      glow(ctx, morph(p, PIG[k], m), out * seg(t, 19.85, 19.95), lerp(5, 8, m));
    });
    const hs = seg(t, 20.3, 20.9, E.out3);
    if (hs > 0) { const g = ctx.createRadialGradient(MASCOT.cx, MASCOT.cy, 0, MASCOT.cx, MASCOT.cy, 620); g.addColorStop(0, rgba(PINK, .42 * hs)); g.addColorStop(1, rgba(PINK, 0)); ctx.fillStyle = g; ctx.fillRect(0, 0, W, H); }
    const pa = seg(t, 20.45, 20.68, E.out2), ps = lerp(.92, 1, E.outBack(seg(t, 20.45, 20.8)));
    if (pa > 0) {
      const [bx0, by0, bx1, by1] = MASCOT.bbox, k = MASCOT.bh / (by1 - by0);
      ctx.save(); ctx.globalAlpha = pa; ctx.translate(MASCOT.cx, MASCOT.cy); ctx.scale(ps, ps);
      ctx.drawImage(IMG.mascot, -((bx0 + bx1) / 2) * k, -((by0 + by1) / 2) * k, IMG.mascot.width * k, IMG.mascot.height * k);
      ctx.restore();
    }
    const la = seg(t, 20.65, 20.95, E.outBack);
    if (la > 0) {
      const w = 640, h = w * IMG.logo.height / IMG.logo.width;
      ctx.save(); ctx.translate(W / 2, 1425); ctx.scale(lerp(.85, 1, la), lerp(.85, 1, la)); ctx.globalAlpha = clamp(la);
      ctx.drawImage(IMG.logo, -w / 2, -h / 2, w, h); ctx.restore();
    }
    const words = ["Conecte.", "Entenda.", "Planeje."], gap = 30, ws = words.map(w => tw(ctx, w, 68, 800, -2)), tot = ws.reduce((x, y) => x + y, 0) + gap * 2;
    let xx = W / 2 - tot / 2;
    words.forEach((wd, i) => {
      const t0 = 20.8 + .12 * i, p = seg(t, t0, t0 + .35, E.outBack), x0 = xx; xx += ws[i] + gap;
      if (p <= 0) return;
      ctx.save(); ctx.globalAlpha = clamp(p); ctx.translate(0, (1 - p) * 40);
      txt(ctx, wd, x0, 1650, { size: 68, weight: 800, color: i === 2 ? PINK : "#fff", ls: -2 });
      ctx.restore();
    });
  }

  // -------- títulos
  const o = { ink: th.ink, stagger: .014, dur: .45, ls: -4, x: 70 };
  rollText(ctx, t, [{ t0: 16.3, lines: ["Você autoriza."], hl: "autoriza" }], { ...o, y: 205, size: 100, tEnd: 17.95, outDur: .3 });
  rollText(ctx, t, [{ t0: 17.6, lines: ["Você controla."], hl: "controla" }], { ...o, y: 312, size: 100, tEnd: 17.95, outDur: .3 });
  rollText(ctx, t, [{ t0: 18.0, lines: ["O PigBank", "não movimenta", "seu dinheiro."], hl: "não" }], { ...o, y: 195, size: 94, lh: 1.04, tEnd: 18.98, outDur: .3 });
  rollText(ctx, t, [{ t0: 19.0, lines: ["Open Finance oficial", "e regulado pelo", "Banco Central."], hl: "Banco Central" }], { ...o, y: 190, size: 82, lh: 1.06, tEnd: 20.35, ls: -3 });
}

export function drawFootnote(ctx, t, th) {
  const a = seg(t, 13.8, 14.3);
  if (a > 0) txt(ctx, "Valores ilustrativos.", W / 2, 1866, { size: 23, weight: 500, color: rgba(t > 16.6 ? "#ffffff" : th.ink, .42 * a), align: "center" });
}
