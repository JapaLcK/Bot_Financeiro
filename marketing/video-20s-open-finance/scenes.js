/* global document */
// Orquestra as cenas no tempo. Cada cena é uma função de t; as janelas se sobrepõem de propósito,
// porque cada uma nasce de um objeto da anterior.
import { W, H, DARK, LIGHT, PINK, rgba, lerp, seg, E } from "./lib.js";
import { drawConnect, hubRadius, HUB } from "./connect.js";
import { drawHook, drawHookText, drawVortexCore, prepareHook } from "./hook.js";
import { drawPayoff } from "./payoff.js";
import { drawFinale, drawFootnote, prepareFinale } from "./finale.js";

export async function prepare() {
  const c = document.createElement("canvas").getContext("2d");
  prepareHook(c);
  prepareFinale();
}

function bg(ctx, t, th) {
  ctx.fillStyle = th.bg; ctx.fillRect(0, 0, W, H);
  if (th.name === "dark") {
    const g = ctx.createRadialGradient(W * .5, H * .55, 0, W * .5, H * .55, 1100);
    g.addColorStop(0, rgba(PINK, .11)); g.addColorStop(1, rgba(PINK, 0));
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  }
}

// Uma "passada" desenha o mundo inteiro num tema. A troca de tema é uma íris: a passada nova
// aparece dentro de um círculo que cresce, sobre a anterior.
function scene(ctx, t, th) {
  bg(ctx, t, th);
  if (t < 6.5) { drawHook(ctx, t, th); drawHookText(ctx, t); drawVortexCore(ctx, t); drawConnect(ctx, t); }
  if (t >= 5.85 && t < 16.4) {
    ctx.save();
    if (t < 6.5) { const R = hubRadius(t); ctx.beginPath(); ctx.arc(HUB[0], HUB[1], R, 0, 7); ctx.clip(); }
    drawPayoff(ctx, t, th);
    ctx.restore();
    if (t < 6.5) { // anel do hub
      const R = hubRadius(t), a = 1 - seg(t, 6.15, 6.45);
      if (a > 0) { ctx.beginPath(); ctx.arc(HUB[0], HUB[1], R, 0, 7); ctx.lineWidth = 8; ctx.strokeStyle = rgba(PINK, a); ctx.shadowColor = PINK; ctx.shadowBlur = 40; ctx.stroke(); ctx.shadowBlur = 0; }
    }
  }
}

function withFinale(ctx, t, th) {
  scene(ctx, t, th);
  drawFinale(ctx, t, th);
  drawFootnote(ctx, t, th);
}

export function draw(ctx, t) {
  if (t >= 13.0 && t < 14.0) {
    withFinale(ctx, t, DARK);
    ctx.save();
    ctx.beginPath(); ctx.arc(540, 1000, lerp(0, 1900, E.io2(seg(t, 13.0, 14.0))), 0, 7); ctx.clip();
    withFinale(ctx, t, LIGHT);
    ctx.restore();
  } else if (t >= 16.0 && t < 17.0) { // íris escura: o dashboard claro dá lugar à conexão protegida
    withFinale(ctx, t, LIGHT);
    ctx.save();
    ctx.beginPath(); ctx.arc(540, 1040, lerp(0, 2000, E.io2(seg(t, 16.0, 17.0))), 0, 7); ctx.clip();
    withFinale(ctx, t, DARK);
    ctx.restore();
  } else withFinale(ctx, t, t >= 14.0 && t < 16.0 ? LIGHT : DARK);
}
