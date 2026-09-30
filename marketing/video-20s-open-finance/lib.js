// Base da composição: matemática, easings, cores/temas, texto e morph de caminhos.
// Tudo aqui é puro (função do tempo); quem desenha recebe o ctx.

export const W = 1080, H = 1920, FPS = 60, DUR = 20;

export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a, b, t) => a + (b - a) * t;
export const inv = (a, b, x) => clamp((x - a) / (b - a));

const c1 = 1.70158, c3 = c1 + 1;
export const E = {
  lin: t => t,
  in2: t => t * t,
  in3: t => t * t * t,
  out2: t => 1 - (1 - t) ** 2,
  out3: t => 1 - (1 - t) ** 3,
  out4: t => 1 - (1 - t) ** 4,
  io2: t => t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2,
  io3: t => t < .5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2,
  io5: t => t < .5 ? 16 * t ** 5 : 1 - (-2 * t + 2) ** 5 / 2,
  outExpo: t => t >= 1 ? 1 : 1 - 2 ** (-10 * t),
  inExpo: t => t <= 0 ? 0 : 2 ** (10 * t - 10),
  ioExpo: t => t <= 0 ? 0 : t >= 1 ? 1 : t < .5 ? 2 ** (20 * t - 10) / 2 : (2 - 2 ** (-20 * t + 10)) / 2,
  outBack: (t, s = c1) => 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2,
  inBack: t => c3 * t ** 3 - c1 * t * t,
  ioBack: t => t < .5 ? ((2 * t) ** 2 * ((c1 * 1.525 + 1) * 2 * t - c1 * 1.525)) / 2 : ((2 * t - 2) ** 2 * ((c1 * 1.525 + 1) * (t * 2 - 2) + c1 * 1.525) + 2) / 2,
  outElastic: t => t <= 0 ? 0 : t >= 1 ? 1 : 2 ** (-10 * t) * Math.sin((t * 10 - .75) * (2 * Math.PI / 3)) + 1,
  spring: t => t <= 0 ? 0 : t >= 1 ? 1 : 1 - Math.exp(-7 * t) * Math.cos(11 * t),
};

// progresso eased entre a e b
export const seg = (t, a, b, f = E.io3) => f(inv(a, b, t));
// pulso 0→1→0 (sino) entre a e b
export const bell = (t, a, b) => Math.sin(Math.PI * inv(a, b, t));

export function rng(seed) {
  let s = seed >>> 0;
  const r = () => {
    s = (s + 0x6D2B79F5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  r.between = (a, b) => a + (b - a) * r();
  r.pick = arr => arr[Math.floor(r() * arr.length)];
  return r;
}

// ------------------------------------------------------------------ cor
export const hex = h => {
  if (h.length === 4) h = "#" + h[1] + h[1] + h[2] + h[2] + h[3] + h[3];
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
export const rgba = (h, a = 1) => { const [r, g, b] = hex(h); return `rgba(${r},${g},${b},${a})`; };
export const mixHex = (a, b, t) => {
  const A = hex(a), B = hex(b);
  return `rgb(${Math.round(lerp(A[0], B[0], t))},${Math.round(lerp(A[1], B[1], t))},${Math.round(lerp(A[2], B[2], t))})`;
};

export const PINK = "#FF2D8E", NEON = "#C6F11A", GAIN = "#3DDC97";
export const CAT = { mercado: "#3987e5", delivery: "#d95926", transporte: "#2fa0c8", lazer: "#c98500", assinaturas: "#9085e9", compras: "#e66767", outros: "#6b7280" };

// Tokens do DESIGN.md (dark) e do brand.css (light)
export const DARK = { name: "dark", bg: "#09090b", plane: "#101013", card: "#16161a", card2: "#1d1d22", line: "#26262c", ink: "#f4f4f6", ink2: "#a8a8b3", ink3: "#6e6e78", pink: PINK, gain: GAIN, shadow: "rgba(0,0,0,.55)", ghost: "rgba(255,255,255,.07)" };
export const LIGHT = { name: "light", bg: "#F6F4F1", plane: "#EFECE7", card: "#FFFFFF", card2: "#F3F0EC", line: "#E6E1DA", ink: "#111111", ink2: "#5f5f63", ink3: "#9a9aa0", pink: PINK, gain: "#22a96b", shadow: "rgba(40,30,20,.16)", ghost: "rgba(0,0,0,.06)" };

// ------------------------------------------------------------------ desenho
export function rr(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, Math.max(0, w), Math.max(0, h), Math.max(0, Math.min(r, w / 2, h / 2)));
}
export function rrc(ctx, cx, cy, w, h, r) { rr(ctx, cx - w / 2, cy - h / 2, w, h, r); }

export const FONT = "Inter, system-ui, sans-serif";
export function txt(ctx, s, x, y, o = {}) {
  const { size = 32, weight = 600, color = "#fff", align = "left", base = "alphabetic", ls = 0 } = o;
  ctx.font = `${weight} ${size}px ${FONT}`;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = base;
  ctx.letterSpacing = `${ls}px`;
  ctx.fillText(s, x, y);
}
export function tw(ctx, s, size, weight = 600, ls = 0) {
  ctx.font = `${weight} ${size}px ${FONT}`;
  ctx.letterSpacing = `${ls}px`;
  return ctx.measureText(s).width;
}

export const ICON = {}; // preenchido em main.js a partir do phosphor.css
export function icon(ctx, name, x, y, size, color) {
  const g = ICON[name];
  if (!g) return;
  ctx.font = `${size}px Phosphor`;
  ctx.fillStyle = color;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.letterSpacing = "0px";
  ctx.fillText(g, x, y + size * 0.02);
}

export const IMG = {}; // imagens oficiais carregadas em main.js

export function brl(v, dec = 2, sign = false) {
  const neg = v < 0;
  const s = Math.abs(v).toFixed(dec).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${neg ? "−" : sign ? "+" : ""}R$ ${s}`;
}

// ------------------------------------------------------------------ morph de caminhos
// Pontos equidistantes (por comprimento de arco) de uma polilinha.
export function resample(pts, n, closed = false) {
  const p = closed ? [...pts, pts[0]] : pts;
  const d = [0];
  for (let i = 1; i < p.length; i++) d.push(d[i - 1] + Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]));
  const L = d[d.length - 1], out = [];
  let k = 1;
  for (let i = 0; i < n; i++) {
    const s = closed ? (i / n) * L : (i / (n - 1)) * L;
    while (k < d.length - 1 && d[k] < s) k++;
    const u = (s - d[k - 1]) / Math.max(1e-9, d[k] - d[k - 1]);
    out.push([lerp(p[k - 1][0], p[k][0], u), lerp(p[k - 1][1], p[k][1], u)]);
  }
  return out;
}

// Contorno de retângulo arredondado, começando no topo-centro e indo no sentido horário.
export function rrPts(cx, cy, w, h, r, n = 96) {
  r = Math.min(r, w / 2, h / 2);
  const x0 = cx - w / 2, x1 = cx + w / 2, y0 = cy - h / 2, y1 = cy + h / 2, raw = [[cx, y0]];
  const arc = (ax, ay, a0) => { for (let i = 0; i <= 8; i++) { const a = a0 + (i / 8) * Math.PI / 2; raw.push([ax + r * Math.cos(a), ay + r * Math.sin(a)]); } };
  raw.push([x1 - r, y0]); arc(x1 - r, y0 + r, -Math.PI / 2);
  raw.push([x1, y1 - r]); arc(x1 - r, y1 - r, 0);
  raw.push([x0 + r, y1]); arc(x0 + r, y1 - r, Math.PI / 2);
  raw.push([x0, y0 + r]); arc(x0 + r, y0 + r, Math.PI);
  raw.push([cx, y0]);
  return resample(raw, n, false);
}

// Escudo: topo plano, lados retos, ponta embaixo. Mesmo ponto de partida do rrPts.
export function shieldPts(cx, cy, w, h, n = 96) {
  const x0 = cx - w / 2, x1 = cx + w / 2, y0 = cy - h / 2, y1 = cy + h / 2, r = w * .16, raw = [[cx, y0]];
  raw.push([x1 - r, y0]);
  for (let i = 0; i <= 6; i++) { const a = -Math.PI / 2 + (i / 6) * Math.PI / 2; raw.push([x1 - r + r * Math.cos(a), y0 + r + r * Math.sin(a)]); }
  const sy = cy + h * .12;
  raw.push([x1, sy]);
  for (let i = 1; i <= 14; i++) { const u = i / 14; raw.push([lerp(x1, cx, E.io2(u) * .999), lerp(sy, y1, u ** 1.55)]); }
  for (let i = 13; i >= 0; i--) { const u = i / 14; raw.push([lerp(x0, cx, E.io2(u) * .999), lerp(sy, y1, u ** 1.55)]); }
  raw.push([x0, sy]);
  raw.push([x0, y0 + r]);
  for (let i = 0; i <= 6; i++) { const a = Math.PI + (i / 6) * Math.PI / 2; raw.push([x0 + r + r * Math.cos(a), y0 + r + r * Math.sin(a)]); }
  raw.push([cx, y0]);
  return resample(raw, n, false);
}

export const morph = (A, B, e) => A.map((p, i) => [lerp(p[0], B[i][0], e), lerp(p[1], B[i][1], e)]);

export function strokePts(ctx, pts, closed = false) {
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  if (closed) ctx.closePath();
}

// Curva cúbica amostrada
export function bez(p0, p1, p2, p3, n = 48) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1), u = 1 - t;
    out.push([
      u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
      u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1],
    ]);
  }
  return out;
}

// ------------------------------------------------------------------ tipografia cinética
// Texto que "rola" (odômetro) letra a letra dentro de uma máscara.
// phrases: [{ t0, lines: ["Gastos."], hl: "palavra destacada" }]; cada frase sai quando a próxima entra.
export function rollText(ctx, t, phrases, o = {}) {
  const { x = 70, y = 200, size = 120, weight = 850, ls = -5, lh = 1.0, ink = "#fff", align = "left", stagger = .02, dur = .55, outDur = .4, tEnd = Infinity } = o;
  const accent = o.accent || PINK;
  phrases.forEach((ph, pi) => {
    const tIn = ph.t0, nxt = phrases[pi + 1], tOut = nxt ? nxt.t0 - (o.overlap ?? .08) : tEnd;
    if (t < tIn - .001 || t > tOut + outDur + .6) return;
    const sz = ph.size || size;
    ph.lines.forEach((line, li) => {
      ctx.save();
      ctx.font = `${weight} ${sz}px ${FONT}`;
      ctx.letterSpacing = `${ls}px`;
      const lw = ctx.measureText(line).width;
      const bx = align === "center" ? x - lw / 2 : align === "right" ? x - lw : x;
      const by = y + li * sz * lh;
      ctx.beginPath();
      ctx.rect(bx - 40, by - sz * .92, lw + 80, sz * 1.25);
      ctx.clip();
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
      const hlS = ph.hl ? line.indexOf(ph.hl) : -1;
      for (let i = 0; i < line.length; i++) {
        const cx = bx + ctx.measureText(line.slice(0, i)).width;
        const d = (li * 4 + i) * stagger;
        const pin = seg(t, tIn + d, tIn + d + dur, E.outBack);
        const pout = seg(t, tOut + d * .6, tOut + d * .6 + outDur, E.in3);
        const ty = lerp(sz * 1.15, 0, pin) - pout * sz * 1.15;
        const ch = line[i];
        const punct = ch === "." || ch === "?" || ch === ",";
        ctx.fillStyle = punct || (hlS >= 0 && i >= hlS && i < hlS + ph.hl.length) ? accent : ink;
        ctx.fillText(ch, cx, by + ty);
      }
      ctx.restore();
    });
  });
}
