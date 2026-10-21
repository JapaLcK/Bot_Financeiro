// O stream do chat com a IA (`POST /ai/chat/stream`): só linhas `data: <json>` (um JSON por linha, sem `\n` dentro),
// `: ping` de keep-alive e linha vazia. Cópia do `lerEventos` do app (app/src/api/sse.ts): não é um parser SSE completo
// (sem `event:`/`id:`). JSON quebrado descarta só aquela linha; o que sobra sem `\n` no fim é lido como a última linha
// (um proxy pode tirar o `\n\n` final). Safari 14: sem TextDecoderStream, sem AbortSignal.timeout/any.
import { ErroApi, erroDe, urlDe } from "./v2";

export async function lerEventos(corpo: ReadableStream<Uint8Array> | null, aoEvento: (bruto: unknown) => void): Promise<void> {
  if (!corpo) throw new Error("resposta sem corpo");
  const leitor = corpo.getReader(), decoder = new TextDecoder();
  let resto = "";
  const linha = (l: string) => {
    if (l.slice(0, 5) !== "data:") return;
    let bruto: unknown;
    try { bruto = JSON.parse(l.slice(5)); } catch { return; }
    aoEvento(bruto);
  };
  try {
    for (;;) {
      const { done, value } = await leitor.read();
      if (done) { linha((resto + decoder.decode()).replace(/\r$/, "")); break; }
      const partes = (resto + decoder.decode(value, { stream: true })).split("\n");
      resto = partes.pop()!;
      for (const p of partes) linha(p.replace(/\r$/, ""));
    }
  } catch (e) {
    await leitor.cancel().catch(() => undefined);
    throw e;
  }
}

/** POST que devolve um stream. O CSRF sai como nas escritas (`pbCsrfHeaders`); fora de 2xx o erro é o MESMO de `chamar` (`erroDe`).
 * O `signal` corta a conexão: parar, prazo e troca de conta são todos um abort. O 401 vem ANTES do stream (nada gasto) e o auth-refresh.js
 * renova e repete. */
export async function fluxo(path: string, corpo: unknown, signal: AbortSignal, aoEvento: (bruto: unknown) => void): Promise<void> {
  const json = { Accept: "text/event-stream", "Content-Type": "application/json" };
  let r: Response;
  try {
    r = await fetch(urlDe(path), { method: "POST", credentials: "same-origin", body: JSON.stringify(corpo), headers: window.pbCsrfHeaders?.(json) ?? json, signal });
  } catch (e) {
    if (signal.aborted) throw e;
    throw new ErroApi(0, "network");
  }
  if (!r.ok) {
    let body: unknown;
    try { body = await r.json(); } catch (e) { if (signal.aborted) throw e; body = undefined; }
    throw erroDe(r, body);
  }
  await lerEventos(r.body, aoEvento);
}
