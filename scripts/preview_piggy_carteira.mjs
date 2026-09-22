// Prévia local do chat React real com respostas fictícias, sem credenciais nem banco.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '../frontend');
const port = Number(process.env.PORT || 8765);
const investments = [
  { name: 'CDB de exemplo', institution_name: 'Banco fictício', type: 'FIXED_INCOME', subtype: 'CDB', currency: 'BRL', balance: 11633.50 },
  { name: 'Tesouro IPCA+ 2032', institution_name: 'Corretora fictícia', type: 'FIXED_INCOME', currency: 'BRL', balance: 8574.83 },
  { name: 'XPML11', institution_name: 'Corretora fictícia', type: 'EQUITY', currency: 'BRL', balance: 2184.63 },
  { name: 'WEGE3', institution_name: 'Corretora fictícia', type: 'EQUITY', currency: 'BRL', balance: 1966.12 },
];
const files = new Map([
  ['/chat-app.js', 'application/javascript'], ['/chat-app.css', 'text/css'],
  ['/dashboard-chat.js', 'application/javascript'], ['/brand/stickers/hello.webp', 'image/webp'],
]);
const html = `<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prévia da carteira · PigBank</title><link rel="stylesheet" href="/chat-app.css">
<style>
  body { margin:0; min-height:100vh; background:#111113; color:#f4f2f3; font:16px system-ui,sans-serif; }
  main { max-width:900px; margin:0 auto; padding:clamp(28px,5vw,72px) 24px; }
  .demo-label { display:inline-block; padding:7px 12px; border-radius:99px; background:#3a2332; color:#ffbad8; font-size:12px; }
  h1 { font-size:clamp(28px,5vw,50px); letter-spacing:-.04em; margin:20px 0 12px; }
  main p { color:#b6b2b6; line-height:1.6; max-width:570px; }
  #piggy-fab { position:fixed; bottom:24px; right:24px; width:70px; height:70px; border:0; border-radius:50%; background:#c7186b; color:#fff; font-size:17px; font-weight:700; cursor:pointer; box-shadow:0 12px 32px #0007; z-index:700; }
  @media (max-width:600px) { #piggy-fab { bottom:22px; right:22px; } }
</style><main><span class="demo-label">Demonstração local · dados fictícios</span><h1>Carteira no chat do Piggy</h1>
<p>Abra o Piggy no botão rosa. Pergunte “Quanto tenho nas minhas caixinhas?” ou escolha a sugestão de carteira. Expanda renda fixa e ações para explorar os ativos.</p></main>
<button id="piggy-fab" type="button" aria-label="Abrir Piggy IA">Piggy</button><div id="pigbank-chat-root"></div>
<script>const USER_ID=42;function isProUser(){return true}function csrfHeaders(h){return h}</script>
<script defer src="/chat-app.js"></script><script defer src="/dashboard-chat.js"></script></html>`;

const server = createServer(async (request, response) => {
  const path = new URL(request.url, 'http://localhost').pathname;
  if (path === '/ai/chat' && request.method === 'POST') {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ reply: 'Aqui está a carteira de exemplo compartilhada pelo Open Finance. Toque nos grupos para ver os ativos.', usage: { used: 1, limit: 100 } }));
  } else if (path === '/ai/messages') {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ usage: { used: 0, limit: 100 } }));
  } else if (path === '/open-finance/42') {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ ok: true, investments }));
  } else if (files.has(path)) {
    try {
      response.setHeader('Content-Type', files.get(path));
      response.end(await readFile(resolve(frontend, '.' + path)));
    } catch { response.writeHead(404).end(); }
  } else if (path === '/') {
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    response.end(html);
  } else response.writeHead(404).end();
});
server.listen(port, '127.0.0.1', () => console.log(`Prévia do Piggy: http://127.0.0.1:${port}/`));
