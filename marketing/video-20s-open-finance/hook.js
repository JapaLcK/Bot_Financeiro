// 0–3s · Gancho: excesso de informação em profundidade, câmera avançando, tudo atraído para um ponto.
import { W, H, E, seg, lerp, clamp, rng, DARK, PINK, rgba, FONT } from "./lib.js";
import { HOOK, SIZE, drawHookItem } from "./items.js";

export const VORTEX = [540, 1110];
const F = 1250;
const cz = t => 2500 * E.out2(clamp(t / 3.3));
const roll = t => .035 * Math.sin(t * 2.3) + .02 * Math.sin(t * 5.1);

// heróis: viram as linhas do app no match cut (índices em HOOK_ITEMS)
export const HEROES = [0, 5, 3, 8]; // Mercado, Conta de luz, Celular 3/12, Delivery (posições em HOOK)

const items = (() => {
  const r = rng(2026), out = [];
  const total = 88;
  for (let i = 0; i < total; i++) {
    const hero = i < 4, si = hero ? HEROES[i] : i % HOOK.length;
    const z = hero ? 3500 + i * 150 : r.between(520, 4800);
    // posição escolhida em espaço de tela no t=0, para o quadro inicial já vir cheio
    const sx = hero ? [470, 650, 500, 620][i] : r.between(-140, 1220), sy = hero ? [840, 1040, 1190, 700][i] : r.between(-160, 2080);
    const dz0 = z - cz(0);
    out.push({
      spec: HOOK[si], hero, i,
      x: (sx - W / 2) * dz0 / F, y: (sy - H / 2) * dz0 / F, z,
      vx: r.between(-40, 40), vy: r.between(-40, 40),
      rot: hero ? r.between(-.12, .12) : r.between(-.5, .5), vrot: r.between(-.18, .18),
      t0: hero ? 0.05 * i : i < 34 ? r.between(-.6, .05) : r.between(.1, 1.4),
      sc: r.between(.85, 1.25), ph: r() * 6.28, rank: r(),
    });
  }
  return out;
})();
export const heroItems = items.slice(0, 4);

// posição livre (sem atração) de um item no instante t
function place(it, t) {
  const dz = it.z - cz(t);
  if (dz < 240) return null;
  const s = F / dz;
  const x = it.x + it.vx * t, y = it.y + it.vy * t + Math.sin(t * 1.6 + it.ph) * 14;
  return { sx: W / 2 + x * s, sy: H / 2 + y * s, s, dz, rot: it.rot + it.vrot * t };
}

function rolled(p, t) { // câmera rolando em torno do centro
  const a = roll(t), c = Math.cos(a), sn = Math.sin(a), dx = p.sx - W / 2, dy = p.sy - H / 2;
  return { ...p, sx: W / 2 + dx * c - dy * sn, sy: H / 2 + dx * sn + dy * c, rot: p.rot + a };
}

export function spiral(sx, sy, w, rank, turns = 2.4) {
  const dx = sx - VORTEX[0], dy = sy - VORTEX[1], r0 = Math.hypot(dx, dy), a0 = Math.atan2(dy, dx);
  const r = r0 * (1 - w) ** 1.35, a = a0 + w * Math.PI * turns * (.7 + .5 * rank);
  return [VORTEX[0] + Math.cos(a) * r, VORTEX[1] + Math.sin(a) * r * .92];
}

export function drawHook(ctx, t, th = DARK) {
  if (t > 3.6) return;
  const order = [];
  for (const it of items) {
    const p = place(it, t);
    if (!p) continue;
    order.push([it, rolled(p, t)]);
  }
  order.sort((a, b) => b[1].dz - a[1].dz); // do fundo para a frente

  for (const [it, p] of order) {
    const born = seg(t, it.t0, it.t0 + .5, E.outBack);
    if (born <= 0) continue;
    const w = seg(t, 1.8 + .45 * it.rank, 2.8 + .2 * it.rank, E.in3);
    if (it.hero && t > 2.85) continue; // os heróis seguem para o app (connect.js)
    let [x, y] = w > 0 ? spiral(p.sx, p.sy, w, it.rank) : [p.sx, p.sy];
    let sc = p.s * it.sc * born * (1 - .9 * w ** 1.4);
    const near = clamp((p.dz - 240) / 260);
    const far = clamp(1 - (p.dz - 3800) / 3200);
    let a = near * (.35 + .65 * far) * (w > .9 ? 1 - (w - .9) * 10 : 1);
    if (a <= .01 || sc < .015) continue;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(p.rot + w * 5 * (.4 + it.rank));
    ctx.scale(sc, sc);
    ctx.globalAlpha = a;
    // profundidade: o que está longe perde brilho (sem filter, para ficar barato)
    drawHookItem(ctx, it.spec, th);
    if (far < .75) { ctx.globalAlpha = (1 - far) * .55 * a; ctx.fillStyle = th.bg; const [bw, bh] = SIZE[it.spec.k]; ctx.beginPath(); ctx.roundRect(-bw / 2 - 2, -bh / 2 - 2, bw + 4, bh + 4, 26); ctx.fill(); }
    ctx.restore();
  }
}

// ------------------------------------------------------------------ título cinético
const LINES = [["Sua vida"], ["financeira"], ["está", "espalhada?"]];
const TS = 132;
let letters = null;
export function prepareHook(ctx) {
  const r = rng(9);
  letters = [];
  ctx.font = `850 ${TS}px ${FONT}`; ctx.letterSpacing = "-5px";
  const ys = [790, 935, 1080];
  LINES.forEach((words, li) => {
    const line = words.join(" ");
    const lw = ctx.measureText(line).width, bx = W / 2 - lw / 2;
    let off = 0;
    words.forEach((wd, wi) => {
      const start = line.indexOf(wd, off); off = start + wd.length;
      for (let k = 0; k < wd.length; k++) {
        const i = start + k;
        letters.push({
          ch: wd[k], x: bx + ctx.measureText(line.slice(0, i)).width, y: ys[li],
          w: ctx.measureText(wd[k]).width, li, wi: li * 1.2 + (wd === "espalhada?" ? 2.2 : wi * .7),
          sc: wd === "espalhada?", ph: r() * 6.28, rank: r(), ph2: r() * 6.28, n: letters.length,
        });
      }
    });
  });
}

export function drawHookText(ctx, t) {
  if (!letters || t > 3.3) return;
  // escurece o miolo para o texto ler sobre o caos
  const sc = seg(t, .05, .5, E.out2) * (1 - seg(t, 2.3, 3.0, E.io2));
  if (sc > 0) {
    const g = ctx.createRadialGradient(W / 2, 930, 60, W / 2, 930, 720);
    g.addColorStop(0, `rgba(9,9,11,${.62 * sc})`); g.addColorStop(1, "rgba(9,9,11,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  }
  ctx.textBaseline = "alphabetic"; ctx.textAlign = "left"; ctx.letterSpacing = "-5px";
  ctx.font = `850 ${TS}px ${FONT}`;
  for (const L of letters) {
    const t0 = .08 + L.wi * .17;
    const pin = seg(t, t0, t0 + .55, E.outExpo);
    if (pin <= 0) continue;
    let s = lerp(2.6, 1, pin), x = L.x + L.w / 2, y = L.y - TS * .34, rot = lerp(L.ph - 3.14 > 0 ? .25 : -.25, 0, pin);
    // "espalhada?" realmente se espalha
    if (L.sc) {
      const amp = seg(t, .9, 2.0, E.outBack) * 95;
      x += Math.cos(L.ph + t * 1.4) * amp * (.5 + L.rank);
      y += Math.sin(L.ph2 + t * 1.1) * amp * .8;
      rot += Math.sin(L.ph + t * 2) * .3 * (amp / 95);
      s *= 1 + .12 * Math.sin(L.ph2 + t * 3) * (amp / 95);
    }
    const w = seg(t, 2.2 + L.n * .006, 2.98, E.in3);
    if (w > 0) { [x, y] = spiral(x, y, w, L.rank, 2.0); s *= 1 - .92 * w ** 1.2; }
    if (s < .02) continue;
    const a = pin * (w > .92 ? 1 - (w - .92) * 12 : 1);
    ctx.save();
    ctx.translate(x, y); ctx.rotate(rot); ctx.scale(s, s);
    ctx.globalAlpha = clamp(a);
    ctx.fillStyle = L.sc ? PINK : "#fff";
    if (L.sc) { ctx.shadowColor = rgba(PINK, .55); ctx.shadowBlur = 38; }
    ctx.fillText(L.ch, -L.w / 2, TS * .34);
    ctx.restore();
  }
}

// brilho do ponto de convergência
export function drawVortexCore(ctx, t) {
  const a = seg(t, 1.9, 3.0, E.in2) * (1 - seg(t, 3.0, 3.5, E.out2));
  if (a <= 0) return;
  const r = lerp(30, 210, a) * (1 + .06 * Math.sin(t * 40));
  const g = ctx.createRadialGradient(VORTEX[0], VORTEX[1], 0, VORTEX[0], VORTEX[1], r);
  g.addColorStop(0, `rgba(255,255,255,${.95 * a})`); g.addColorStop(.25, `rgba(255,120,186,${.85 * a})`);
  g.addColorStop(.6, `rgba(255,45,142,${.35 * a})`); g.addColorStop(1, "rgba(255,45,142,0)");
  ctx.fillStyle = g; ctx.fillRect(VORTEX[0] - r, VORTEX[1] - r, r * 2, r * 2);
}
