// Renderiza index.html em MP4 de 30s quadro a quadro (determinístico: cada quadro é __seek(t)).
//   FFMPEG=/caminho/ffmpeg node marketing/video-30s-open-finance/render.mjs
//   render.mjs --stills 0.5,2.6,8.9     → PNGs soltos em out/, sem vídeo
//   render.mjs --sheet 0,0.25,...       → uma folha de contato única (out/sheet.png), para revisar a sequência
// Se out/soundtrack.wav existir (soundtrack.py), ele entra no MP4.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdir, access } from "node:fs/promises";
import { extname, join, dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const OUT = join(HERE, "out");
const FPS = 60, DUR = 30;
const arg = k => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
const stills = arg("--stills"), sheet = arg("--sheet");

const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript", ".png": "image/png", ".webp": "image/webp", ".woff2": "font/woff2" };
const server = createServer(async (req, res) => {
  const p = join(ROOT, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!p.startsWith(ROOT + sep)) { res.writeHead(403).end(); return; }
  try { const body = await readFile(p); res.writeHead(200, { "content-type": TYPES[extname(p)] || "application/octet-stream" }).end(body); }
  catch { res.writeHead(404).end(); }
}).listen(0, "127.0.0.1"); // só loopback: o handler serve qualquer arquivo do repo
await new Promise(r => server.once("listening", r));
const ss = arg("--ss") || (stills || sheet ? "2" : "4");
const url = `http://127.0.0.1:${server.address().port}/marketing/video-30s-open-finance/index.html?render=1&ss=${ss}`;

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
const bad = [];
page.on("response", r => { if (r.status() >= 400) bad.push(r.url()); });
page.on("pageerror", e => { console.error("erro na página:", e.message); process.exit(1); });
page.on("console", m => { if (m.type() === "error") console.error("console:", m.text()); });
await page.goto(url, { waitUntil: "networkidle" });
await page.evaluate(() => window.__ready);
if (bad.length) { console.error("recursos faltando:", bad); process.exit(1); }
const clip = { x: 0, y: 0, width: 1080, height: 1920 };
const seek = t => page.evaluate(t => window.__seek(t), t);

if (stills) {
  for (const t of stills.split(",").map(Number)) { await seek(t); await page.screenshot({ path: join(OUT, `still-${t.toFixed(2)}.png`), clip }); }
} else if (sheet) {
  // folha de contato: capta cada quadro e monta uma grade na própria página (sem dependência extra)
  const ts = sheet.split(",").map(Number), cols = +(arg("--cols") || 6), cw = 270, ch = 480;
  const shots = [];
  for (const t of ts) { await seek(t); shots.push((await page.screenshot({ type: "jpeg", quality: 85, clip })).toString("base64")); }
  const png = await page.evaluate(async ({ shots, ts, cols, cw, ch }) => {
    const rows = Math.ceil(shots.length / cols), c = document.createElement("canvas");
    c.width = cols * cw; c.height = rows * ch; const g = c.getContext("2d"); g.fillStyle = "#222"; g.fillRect(0, 0, c.width, c.height);
    await Promise.all(shots.map((b, i) => new Promise(ok => { const im = new Image(); im.onload = () => { g.drawImage(im, (i % cols) * cw, Math.floor(i / cols) * ch, cw - 4, ch - 4); g.fillStyle = "#fff"; g.font = "bold 18px sans-serif"; g.fillText(ts[i].toFixed(2), (i % cols) * cw + 8, Math.floor(i / cols) * ch + 24); ok(); }; im.src = "data:image/jpeg;base64," + b; })));
    return c.toDataURL("image/png").split(",")[1];
  }, { shots, ts, cols, cw, ch });
  (await import("node:fs/promises")).writeFile(join(OUT, "sheet.png"), Buffer.from(png, "base64"));
} else {
  const ff = process.env.FFMPEG || "ffmpeg";
  const wav = join(OUT, "soundtrack.wav");
  const hasAudio = await access(wav).then(() => true, () => false);
  const dest = join(OUT, "pigbank-open-finance-30s.mp4");
  const args = ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    ...(hasAudio ? ["-i", wav, "-c:a", "aac", "-b:a", "192k", "-shortest"] : []),
    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", dest];
  const enc = spawn(ff, args, { stdio: ["pipe", "inherit", "inherit"] });
  const done = new Promise((ok, ko) => enc.on("close", c => c === 0 ? ok() : ko(new Error("ffmpeg " + c))));
  const N = FPS * DUR;
  for (let i = 0; i < N; i++) {
    await seek(i / FPS);
    const jpg = await page.screenshot({ type: "jpeg", quality: 97, clip });
    if (!enc.stdin.write(jpg)) await new Promise(r => enc.stdin.once("drain", r));
    if (i % 60 === 0) process.stdout.write(`\r${i}/${N}`);
  }
  enc.stdin.end(); await done;
  console.log(`\nok: ${dest}${hasAudio ? " (com trilha)" : " (sem trilha)"}`);
}
await browser.close(); server.close();
