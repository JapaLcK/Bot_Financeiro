// Boot: fontes, imagens oficiais, canvas e window.__seek(t) (motion blur por subamostragem + grão).
import { W, H, DUR, ICON, IMG } from "./lib.js";
import { draw } from "./scenes.js";

const Q = new URLSearchParams(location.search);
const SAMPLES = +(Q.get("ss") || 4), SHUTTER = 0.55 / 60;

const cv = document.getElementById("c");
cv.width = W; cv.height = H;
const out = cv.getContext("2d");
const mk = () => { const c = document.createElement("canvas"); c.width = W; c.height = H; return c; };
const accC = mk(), acc = accC.getContext("2d"), frC = mk(), fr = frC.getContext("2d");

const load = src => new Promise((ok, ko) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => ko(new Error(src)); i.src = src; });

// grão: um tile de ruído deslocado a cada quadro (deterministico)
const grainC = document.createElement("canvas"); grainC.width = grainC.height = 256;
{
  const g = grainC.getContext("2d"), d = g.createImageData(256, 256);
  let s = 7;
  for (let i = 0; i < d.data.length; i += 4) { s = (s * 1664525 + 1013904223) >>> 0; const v = 96 + ((s >>> 24) % 64); d.data[i] = d.data[i + 1] = d.data[i + 2] = v; d.data[i + 3] = 255; }
  g.putImageData(d, 0, 0);
}

function post(t) {
  const f = Math.round(t * 60);
  out.save();
  out.globalCompositeOperation = "overlay";
  out.globalAlpha = 0.07;
  const ox = (f * 97) % 256, oy = (f * 61) % 256;
  out.translate(-ox, -oy);
  out.fillStyle = out.createPattern(grainC, "repeat");
  out.fillRect(0, 0, W + 256, H + 256);
  out.restore();
  const v = out.createRadialGradient(W / 2, H / 2, H * 0.36, W / 2, H / 2, H * 0.78);
  v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, "rgba(0,0,0,.30)");
  out.fillStyle = v; out.fillRect(0, 0, W, H);
}

window.__seek = t => {
  t = Math.min(Math.max(t, 0), DUR - 1e-4);
  acc.clearRect(0, 0, W, H);
  for (let i = 0; i < SAMPLES; i++) {
    const ts = SAMPLES === 1 ? t : t + (i / (SAMPLES - 1) - .5) * SHUTTER * 60 * (1 / 60);
    fr.clearRect(0, 0, W, H);
    fr.save(); draw(fr, Math.max(0, ts)); fr.restore();
    acc.globalAlpha = 1 / (i + 1);
    acc.drawImage(frC, 0, 0);
  }
  out.globalAlpha = 1;
  out.drawImage(accC, 0, 0);
  post(t);
};

window.__ready = (async () => {
  const fonts = [
    new FontFace("Inter", "url(/frontend/fonts/Inter-Variable.woff2)", { weight: "100 900" }),
    new FontFace("Phosphor", "url(/frontend/fonts/Phosphor.woff2)"),
  ];
  await Promise.all(fonts.map(async f => { await f.load(); document.fonts.add(f); }));
  const css = await (await fetch("/frontend/phosphor.css")).text();
  for (const m of css.matchAll(/\.ph-([a-z0-9-]+):before\s*\{\s*content:\s*"\\([0-9a-f]+)"/gi)) ICON[m[1]] = String.fromCodePoint(parseInt(m[2], 16));
  const imgs = {
    mascot: "/frontend/brand/mascot.webp", logo: "/frontend/brand/logo.webp", simbolo: "/app/assets/brand/simbolo.png", avatar: "/frontend/brand/avatar.webp",
  };
  await Promise.all(Object.entries(imgs).map(async ([k, v]) => { IMG[k] = await load(v); }));
  const { prepare } = await import("./scenes.js");
  await prepare();
  window.__seek(0);
})();

if (!Q.has("render")) window.__ready.then(() => {
  const t0 = performance.now();
  const loop = () => { window.__seek(((performance.now() - t0) / 1000) % DUR); requestAnimationFrame(loop); };
  loop();
});
