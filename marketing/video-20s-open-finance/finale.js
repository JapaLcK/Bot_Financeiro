/* global document */
// 16–20s · Segurança + marca. Cards → formas → linhas → conexão protegida → contorno do Piggy → PigBank.
import { W, H, E, seg, bell, inv, lerp, clamp, PINK, NEON, rgba, mixHex, rrc, txt, tw, icon, rollText, rrPts, shieldPts, morph, strokePts, bez, resample, IMG } from "./lib.js";
import { cardRects } from "./payoff.js";

const N = 96;
const B = [190, 1040], P = [890, 1040], C = [540, 1040];
const NODE_R = 78;

const ARCS = [-110, 110, -210, 210].map(a => bez([B[0] + NODE_R, 1040], [B[0] + NODE_R + 150, 1040 + a * 1.3], [P[0] - NODE_R - 150, 1040 + a * 1.3], [P[0] - NODE_R, 1040], N));
const CENTER_LINE = bez([B[0] + NODE_R, 1040], [B[0] + NODE_R + 100, 1040], [P[0] - NODE_R - 100, 1040], [P[0] - NODE_R, 1040], N);
const TOGGLE = { w: 300, h: 150 };
const SHIELD = { w: 250, h: 300 };

// ------------------------------------------------------------------ contorno do Piggy (do alfa do mascote oficial)
const MASCOT = { cx: 540, cy: 900, bh: 720, bbox: [28, 33, 282, 460] };
let PIG = []; // seis trechos de N pontos em coordenadas de tela
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
  let x = sx, y = sy, dir = 6; // veio de cima
  for (let guard = 0; guard < 20000; guard++) {
    let found = false;
    for (let i = 0; i < 8; i++) {
      const d = (dir + 6 + i) % 8, nx = x + D[d][0], ny = y + D[d][1];
      if (on(nx, ny)) { x = nx; y = ny; dir = d; found = true; break; }
    }
    if (!found || (x === sx && y === sy)) break;
    pts.push([x, y]);
  }
  // suaviza (média móvel fechada) e reamostra
  const sm = pts.map((_, i) => { let ax = 0, ay = 0; for (let k = -4; k <= 4; k++) { const p = pts[(i + k + pts.length) % pts.length]; ax += p[0]; ay += p[1]; } return [ax / 9, ay / 9]; });
  let area = 0; sm.forEach((p, i) => { const q = sm[(i + 1) % sm.length]; area += p[0] * q[1] - q[0] * p[1]; });
  if (area < 0) sm.reverse(); // sentido horário na tela
  const all = resample(sm, N * 6, true);
  const [bx0, by0, bx1, by1] = MASCOT.bbox, k = MASCOT.bh / (by1 - by0), bcx = (bx0 + bx1) / 2, bcy = (by0 + by1) / 2;
  const scr = all.map(p => [MASCOT.cx + (p[0] - bcx) * k, MASCOT.cy + (p[1] - bcy) * k]);
  PIG = [];
  for (let s = 0; s < 6; s++) PIG.push(resample(scr.slice(s * N, (s + 1) * N + 1), N, false));
}

// ------------------------------------------------------------------ peças
function node(ctx, th, at, label, pic, a, s = 1) {
  if (a <= 0) return;
  ctx.save(); ctx.translate(at[0], at[1]); ctx.scale(s, s); ctx.globalAlpha *= a;
  ctx.shadowColor = th.shadow; ctx.shadowBlur = 50; ctx.shadowOffsetY = 16;
  ctx.beginPath(); ctx.arc(0, 0, NODE_R, 0, 7); ctx.fillStyle = th.card; ctx.fill(); ctx.shadowColor = "transparent";
  ctx.lineWidth = 4; ctx.strokeStyle = pic === "pig" ? PINK : th.line; ctx.stroke();
  if (pic === "pig") { const w = 84, h = w * IMG.simbolo.height / IMG.simbolo.width; ctx.drawImage(IMG.simbolo, -w / 2, -h / 2 - 2, w, h); }
  else icon(ctx, pic, 0, 0, 64, th.ink);
  txt(ctx, label, 0, NODE_R + 44, { size: 28, weight: 650, color: th.ink2, align: "center" });
  ctx.restore();
}

function coin(ctx, x, y, sq, a) {
  ctx.save(); ctx.globalAlpha *= a; ctx.translate(x, y); ctx.scale(1 - .18 * sq, 1 + .18 * sq);
  ctx.shadowColor = rgba(NEON, .6); ctx.shadowBlur = 30;
  ctx.beginPath(); ctx.arc(0, 0, 40, 0, 7); ctx.fillStyle = NEON; ctx.fill(); ctx.shadowBlur = 0;
  ctx.lineWidth = 5; ctx.strokeStyle = "#8fb612"; ctx.stroke();
  txt(ctx, "R$", 0, 11, { size: 30, weight: 850, color: "#111", align: "center", ls: -1 });
  ctx.restore();
}

// posição do cursor ao longo de um caminho (pontos equidistantes)
const at = (pts, u) => { const f = clamp(u) * (pts.length - 1), i = Math.min(pts.length - 2, Math.floor(f)); return [lerp(pts[i][0], pts[i + 1][0], f - i), lerp(pts[i][1], pts[i + 1][1], f - i)]; };

function packets(ctx, t, paths, on, col = "#fff") {
  if (on <= .05) return;
  paths.forEach((p, k) => {
    for (let j = 0; j < 3; j++) {
      const u = ((t * .8 + k * .21 + j / 3) % 1), [x, y] = at(p, u);
      ctx.globalAlpha = on * Math.sin(Math.PI * u) ** .5;
      ctx.beginPath(); ctx.arc(x, y, 8, 0, 7); ctx.fillStyle = col; ctx.shadowColor = PINK; ctx.shadowBlur = 18; ctx.fill(); ctx.shadowBlur = 0;
    }
  });
  ctx.globalAlpha = 1;
}

function glowLine(ctx, pts, a, w = 6) {
  ctx.save(); ctx.globalAlpha *= a; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.lineWidth = w; ctx.strokeStyle = PINK;
  ctx.shadowColor = rgba(PINK, .8); ctx.shadowBlur = 20; strokePts(ctx, pts); ctx.stroke(); ctx.restore();
}

export function drawFinale(ctx, t, th) {
  if (t < 15.85) return;

  // -------- fase 1: cards viram formas (cobrem o conteúdo e deixam só o contorno)
  const R0 = cardRects(15.9).filter(r => r.k !== "header");
  const order = ["saldo", "trans", "gastos", "parc", "proj"];
  const cover = seg(t, 15.92, 16.15, E.out2);
  const knob = clamp(seg(t, 16.42, 16.58, E.io3) - seg(t, 16.62, 16.74, E.io3) + seg(t, 16.78, 16.9, E.io3));
  const tog = rrPts(C[0], C[1], TOGGLE.w, TOGGLE.h, 75, N);
  const cablesOn = seg(t, 16.35, 16.45) * (.25 + .75 * knob);
  const cableAlpha = seg(t, 16.3, 16.5);

  if (t < 16.75) order.forEach((k, i) => {
    const r = R0.find(q => q.k === k); if (!r) return;
    const pts = rrPts(r.x + r.w / 2, r.y + r.h / 2, r.w, r.h, r.r, N);
    const e = seg(t, 16.1 + .045 * i, 16.6 + .045 * i, E.io3);
    const target = i === 0 ? tog : ARCS[i - 1];
    const P2 = morph(pts, target, e);
    ctx.save();
    if (i === 0) { strokePts(ctx, P2, true); ctx.globalAlpha = cover * (1 - e * .0); ctx.fillStyle = th.card; ctx.fill(); ctx.globalAlpha = 1; }
    else if (e < .35) { strokePts(ctx, P2, true); ctx.globalAlpha = cover * (1 - e / .35); ctx.fillStyle = th.card; ctx.fill(); ctx.globalAlpha = 1; }
    // contorno: cinza → rosa enquanto vira linha
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.lineWidth = lerp(2, 6, e); ctx.strokeStyle = mixHex(th.line, PINK, seg(e, 0, .4));
    if (e > .2) { ctx.shadowColor = rgba(PINK, .7); ctx.shadowBlur = 20 * e; }
    strokePts(ctx, P2, i === 0 ? true : e < .6); ctx.stroke();
    ctx.restore();
  });

  // -------- fase 2: conexão protegida
  const nodeA = seg(t, 16.25, 16.6, E.outBack), nodeOut = seg(t, 18.05, 18.4, E.inBack);
  const nodeS = nodeA * (1 - nodeOut);
  const c2 = seg(t, 16.3, 16.5);
  if (t >= 16.3 && t < 18.4) {
    ctx.save(); ctx.globalAlpha = c2;
    glowLine(ctx, CENTER_LINE, .35 + .65 * knob, 5);
    ctx.restore();
    ARCS.forEach(p => glowLine(ctx, p, cableAlpha * (.25 + .75 * knob), 5));
    packets(ctx, t, [CENTER_LINE, ...ARCS], cablesOn);
    node(ctx, th, B, "Seu banco", "bank", nodeA * (1 - nodeOut), nodeS);
    node(ctx, th, P, "PigBank", "pig", nodeA * (1 - nodeOut), nodeS);
    // "só leitura" no lado do PigBank
    const ro = seg(t, 17.0, 17.3, E.outBack) * (1 - nodeOut);
    if (ro > 0) {
      ctx.save(); ctx.translate(P[0], P[1] - NODE_R - 46); ctx.scale(ro, ro);
      const s = "Só leitura", w = tw(ctx, s, 25, 700) + 84;
      rrc(ctx, 0, 0, w, 54, 27); ctx.fillStyle = th.card; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = th.line; ctx.stroke();
      icon(ctx, "eye", -w / 2 + 36, 0, 32, NEON); txt(ctx, s, 26, 9, { size: 25, weight: 700, color: th.ink, align: "center" });
      ctx.restore();
    }
  }

  // toggle (você autoriza / você controla) → escudo
  const sh = seg(t, 16.92, 17.2, E.io3), shOut = 1 - seg(t, 18.05, 18.3, E.in3);
  const hit = bell(t, 17.2, 17.36) + bell(t, 17.56, 17.7);
  if (t >= 16.5 && t < 18.35) {
    ctx.save();
    const pulse = 1 + .05 * hit;
    ctx.translate(C[0], C[1]); ctx.scale(pulse * shOut, pulse * shOut); ctx.translate(-C[0], -C[1]);
    const body = morph(rrPts(C[0], C[1], TOGGLE.w, TOGGLE.h, 75, N), shieldPts(C[0], C[1], SHIELD.w, SHIELD.h, N), sh);
    const okA = seg(t, 17.72, 17.92);
    const trackCol = mixHex(th.card2, NEON, knob * (1 - sh));
    ctx.shadowColor = th.shadow; ctx.shadowBlur = 50; ctx.shadowOffsetY = 14;
    strokePts(ctx, body, true); ctx.fillStyle = sh > 0 ? mixHex(trackCol, th.card, sh) : trackCol; ctx.fill(); ctx.shadowColor = "transparent";
    ctx.lineWidth = 6; ctx.strokeStyle = mixHex(mixHex(th.line, NEON, knob * (1 - sh)), PINK, sh); ctx.lineJoin = "round";
    if (okA > 0) ctx.strokeStyle = mixHex(PINK, NEON, okA);
    ctx.shadowColor = rgba(sh > 0 ? PINK : NEON, .55); ctx.shadowBlur = 24; ctx.stroke(); ctx.shadowBlur = 0;
    if (sh < .5) { // botão do toggle
      ctx.globalAlpha = 1 - sh * 2;
      const kx = lerp(C[0] - 75, C[0] + 75, knob);
      ctx.beginPath(); ctx.arc(kx, C[1], 56, 0, 7); ctx.fillStyle = "#fff"; ctx.shadowColor = "rgba(0,0,0,.4)"; ctx.shadowBlur = 16; ctx.fill(); ctx.shadowBlur = 0;
      ctx.globalAlpha = 1;
    }
    if (sh > .5) {
      ctx.globalAlpha = seg(sh, .5, 1);
      const ic = okA > .5 ? "check" : "lock-key", s = 1 + .25 * bell(t, 17.72, 18.0);
      ctx.save(); ctx.translate(C[0], C[1] + 6); ctx.scale(s, s);
      icon(ctx, ic, 0, 0, 118, okA > .5 ? NEON : th.ink);
      ctx.restore();
      ctx.globalAlpha = 1;
    }
    ctx.restore();
    // ondas quando algo bate no escudo e quando vira "oficial"
    [17.22, 17.58, 17.74].forEach((t0, i) => {
      const q = seg(t, t0, t0 + .5, E.out3);
      if (q > 0 && q < 1) { ctx.beginPath(); ctx.arc(C[0], C[1], lerp(120, i === 0 ? 230 : 330, q), 0, 7); ctx.lineWidth = 6 * (1 - q); ctx.strokeStyle = rgba(i ? NEON : PINK, .85 * (1 - q)); ctx.stroke(); }
    });
  }
  // o dinheiro tenta passar e volta; só os dados atravessam
  if (t >= 16.95 && t < 18.0) {
    const a1 = inv(16.95, 17.22, t), b1 = inv(17.22, 17.42, t), a2 = inv(17.42, 17.58, t);
    const stop = C[0] - 120;
    let x, sq = 0;
    if (t < 17.22) x = lerp(B[0] + NODE_R + 30, stop, E.in2(a1));
    else if (t < 17.42) { x = lerp(stop, B[0] + NODE_R + 70, E.out3(b1)); sq = 1 - b1; }
    else { x = lerp(B[0] + NODE_R + 70, stop, E.in2(a2)); sq = a2 > .98 ? 1 : 0; }
    if (t >= 17.58) { x = lerp(stop, B[0] + NODE_R + 50, E.out3(inv(17.58, 17.85, t))); sq = 1 - inv(17.58, 17.75, t); }
    const fade = seg(t, 16.95, 17.05) * (1 - seg(t, 17.8, 17.95));
    coin(ctx, x, 1040, clamp(sq), fade);
  }

  // -------- fase 3: linhas → contorno do Piggy → marca
  if (PIG.length && t >= 18.05) {
    const src = [CENTER_LINE, ...ARCS, shieldPts(C[0], C[1], SHIELD.w, SHIELD.h, N)];
    src.forEach((p, k) => {
      const e = seg(t, 18.05 + .04 * k, 18.6 + .04 * k, E.io3);
      const a = 1 - seg(t, 19.0, 19.25, E.out2);
      glowLine(ctx, morph(p, PIG[k], e), a, lerp(5, 8, e));
    });
    // halo atrás do Piggy
    const hs = seg(t, 18.7, 19.3, E.out3);
    if (hs > 0) { const g = ctx.createRadialGradient(MASCOT.cx, MASCOT.cy, 0, MASCOT.cx, MASCOT.cy, 620); g.addColorStop(0, rgba(PINK, .42 * hs)); g.addColorStop(1, rgba(PINK, 0)); ctx.fillStyle = g; ctx.fillRect(0, 0, W, H); }
    // Piggy
    const pa = seg(t, 18.75, 18.97, E.out2), ps = lerp(.92, 1, E.outBack(seg(t, 18.75, 19.1)));
    if (pa > 0) {
      const [bx0, by0, bx1, by1] = MASCOT.bbox, k = MASCOT.bh / (by1 - by0);
      ctx.save(); ctx.globalAlpha = pa; ctx.translate(MASCOT.cx, MASCOT.cy); ctx.scale(ps, ps);
      ctx.drawImage(IMG.mascot, -((bx0 + bx1) / 2) * k, -((by0 + by1) / 2) * k, IMG.mascot.width * k, IMG.mascot.height * k);
      ctx.restore();
    }
    // marca: símbolo + wordmark oficial e tagline
    const la = seg(t, 18.95, 19.25, E.outBack);
    if (la > 0) {
      const w = 640, h = w * IMG.logo.height / IMG.logo.width;
      ctx.save(); ctx.translate(W / 2, 1425); ctx.scale(lerp(.85, 1, la), lerp(.85, 1, la)); ctx.globalAlpha = clamp(la);
      ctx.drawImage(IMG.logo, -w / 2, -h / 2, w, h); ctx.restore();
    }
    const words = ["Conecte.", "Entenda.", "Planeje."], gap = 30, ws = words.map(w => tw(ctx, w, 68, 800, -2)), tot = ws.reduce((x, y) => x + y, 0) + gap * 2;
    let xx = W / 2 - tot / 2;
    words.forEach((wd, i) => {
      const t0 = 19.1 + .13 * i, p = seg(t, t0, t0 + .35, E.outBack), x0 = xx; xx += ws[i] + gap;
      if (p <= 0) return;
      ctx.save(); ctx.globalAlpha = clamp(p); ctx.translate(0, (1 - p) * 40);
      txt(ctx, wd, x0, 1650, { size: 68, weight: 800, color: i === 2 ? PINK : "#fff", ls: -2 });
      ctx.restore();
    });
  }

  // -------- títulos
  const o = { ink: th.ink, stagger: .014, dur: .45, ls: -4, x: 70 };
  rollText(ctx, t, [{ t0: 16.15, lines: ["Você autoriza."], hl: "autoriza" }], { ...o, y: 205, size: 100, tEnd: 16.86, outDur: .3 });
  rollText(ctx, t, [{ t0: 16.5, lines: ["Você controla."], hl: "controla" }], { ...o, y: 312, size: 100, tEnd: 16.86, outDur: .3 });
  rollText(ctx, t, [{ t0: 16.98, lines: ["O PigBank", "não movimenta", "seu dinheiro."], hl: "não" }], { ...o, y: 195, size: 94, lh: 1.04, tEnd: 17.66, outDur: .3 });
  rollText(ctx, t, [{ t0: 17.74, lines: ["Open Finance oficial", "e regulado pelo", "Banco Central."], hl: "Banco Central" }], { ...o, y: 190, size: 82, lh: 1.06, tEnd: 18.75, ls: -3 });
}

export function drawFootnote(ctx, t, th) {
  const a = seg(t, 13.8, 14.3) * (t > 19.0 ? 1 : 1);
  if (a > 0) txt(ctx, "Valores ilustrativos.", W / 2, 1866, { size: 23, weight: 500, color: rgba(t > 16.6 ? "#ffffff" : th.ink, .42 * a), align: "center" });
}
