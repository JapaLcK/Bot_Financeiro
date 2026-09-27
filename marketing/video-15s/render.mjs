// Renderiza index.html em MP4 quadro a quadro (determinístico: cada quadro é __seek(t)).
//   npm ci && FFMPEG=/caminho/ffmpeg node marketing/video-15s/render.mjs
//   (CHROMIUM=/caminho/chromium se o navegador do Playwright não bater com a versão instalada)
//   ... render.mjs --stills 0.5,2.6,8.9   → PNGs soltos em out/, sem vídeo
// Se out/soundtrack.wav existir (python3 soundtrack.py), ele entra no MP4.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdir, access } from "node:fs/promises";
import { extname, join, dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const OUT = join(HERE, "out");
const FPS = 60, DUR = 15;
const arg = (k) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
const stills = arg("--stills");

const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".webp": "image/webp", ".woff2": "font/woff2" };
const server = createServer(async (req, res) => {
  const p = join(ROOT, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!p.startsWith(ROOT + sep)) { res.writeHead(403).end(); return; } // com sep: "Bot_Financeiro2" não passa
  try { const body = await readFile(p); res.writeHead(200, { "content-type": TYPES[extname(p)] || "application/octet-stream" }).end(body); }
  catch { res.writeHead(404).end(); }
}).listen(0, "127.0.0.1"); // só loopback: o handler serve qualquer arquivo do repo, inclusive .env
await new Promise(r => server.once("listening", r)); // com host, o bind é assíncrono
const url = `http://127.0.0.1:${server.address().port}/marketing/video-15s/index.html?render=1`;

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
const missing = [];
page.on("response", r => { if (r.status() >= 400) missing.push(r.url()); });
await page.goto(url, { waitUntil: "networkidle" });
await page.evaluate(() => window.__ready);
if (missing.length) { console.error("recursos faltando:", missing); process.exit(1); }
const clip = { x: 0, y: 0, width: 1080, height: 1920 };

if (stills) {
  for (const t of stills.split(",").map(Number)) {
    await page.evaluate(t => window.__seek(t), t);
    await page.screenshot({ path: join(OUT, `still-${t.toFixed(2)}.png`), clip });
  }
} else {
  const ff = process.env.FFMPEG || "ffmpeg";
  const wav = join(OUT, "soundtrack.wav");
  const hasAudio = await access(wav).then(() => true, () => false);
  const args = ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    ...(hasAudio ? ["-i", wav, "-c:a", "aac", "-b:a", "192k", "-shortest"] : []),
    "-c:v", "libx264", "-preset", "slow", "-crf", "22", "-pix_fmt", "yuv420p", "-movflags", "+faststart", join(OUT, "pigbank-15s.mp4")];
  const enc = spawn(ff, args, { stdio: ["pipe", "inherit", "inherit"] });
  const done = new Promise((ok, ko) => enc.on("close", c => c === 0 ? ok() : ko(new Error("ffmpeg " + c))));
  const N = FPS * DUR;
  for (let i = 0; i < N; i++) {
    await page.evaluate(t => window.__seek(t), i / FPS);
    const jpg = await page.screenshot({ type: "jpeg", quality: 96, clip });
    if (!enc.stdin.write(jpg)) await new Promise(r => enc.stdin.once("drain", r));
    if (i % 60 === 0) process.stdout.write(`\r${i}/${N}`);
  }
  enc.stdin.end(); await done;
  console.log(`\nok: ${join(OUT, "pigbank-15s.mp4")}${hasAudio ? " (com trilha)" : " (sem trilha)"}`);
}
await browser.close(); server.close();
