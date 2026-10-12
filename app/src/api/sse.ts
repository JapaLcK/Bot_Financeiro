/**
 * Lê um corpo `text/event-stream` do servidor do PigBank: só linhas `data: <json>` (um JSON por linha,
 * sem `\n` dentro), `: ping` de keep-alive e linha vazia. Não é um parser SSE completo (sem `event:`/`id:`).
 * JSON quebrado descarta só aquela linha; o que sobra sem `\n` no fim do stream é lido como a última linha (um proxy pode tirar o `\n\n` final).
 */
export async function lerEventos(corpo: ReadableStream<Uint8Array> | null, aoEvento: (bruto: unknown) => void): Promise<void> {
  if (!corpo) throw new Error("resposta sem corpo");
  const leitor = corpo.getReader(), decoder = new TextDecoder();
  let resto = "";
  const linha = (l: string) => {
    if (!l.startsWith("data:")) return;
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
