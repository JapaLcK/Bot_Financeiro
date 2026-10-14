import { lerEventos } from "@/api/sse";

const enc = new TextEncoder();
/** Corpo falso com os chunks dados; `cancelado` registra se o leitor foi cancelado. */
function corpo(chunks: (string | Uint8Array)[]) {
  const fila = chunks.map((c) => (typeof c === "string" ? enc.encode(c) : c)); const estado = { cancelado: false };
  const body = { getReader: () => ({ read: async () => (fila.length ? { done: false, value: fila.shift()! } : { done: true, value: undefined }), cancel: async () => { estado.cancelado = true; } }) } as unknown as ReadableStream<Uint8Array>;
  return { body, estado };
}
const ler = async (chunks: (string | Uint8Array)[]) => { const vistos: unknown[] = []; await lerEventos(corpo(chunks).body, (e) => vistos.push(e)); return vistos; };

it("evento partido entre chunks e `ç` partido no meio dos bytes chega inteiro", async () => {
  const bytes = enc.encode('data: {"texto":"ação"}\n\n'), corte = bytes.indexOf(0xc3) + 1; // entre os 2 bytes do `ç`
  expect(await ler([bytes.slice(0, corte), bytes.slice(corte, corte + 4), bytes.slice(corte + 4)])).toEqual([{ texto: "ação" }]);
  expect(await ler(['data: {"a"', ':1}\r\n\r\ndata: {"b":2}\n'])).toEqual([{ a: 1 }, { b: 2 }]);
});
it("`: ping`, linha vazia e JSON quebrado são ignorados; o resto segue", async () => {
  expect(await ler([': ping\n\n', '\n', 'data: {quebrado\n\n', 'data: {"ok":1}\n\n', 'event: x\n'])).toEqual([{ ok: 1 }]);
});
it("resto sem `\\n` no fim: `data:` com JSON inteiro é entregue; lixo parcial é descartado", async () => {
  expect(await ler(['data: {"a":1}\n\ndata: {"b":2}'])).toEqual([{ a: 1 }, { b: 2 }]);
  expect(await ler(['data: {"a":1}\n\ndata: {"b":'])).toEqual([{ a: 1 }]);
  expect(await ler(['data: {"a":1}\n\nevent: x'])).toEqual([{ a: 1 }]);
});
it("`aoEvento` que lança cancela o leitor e relança", async () => {
  const { body, estado } = corpo(['data: {"a":1}\n\n']);
  await expect(lerEventos(body, () => { throw new Error("x"); })).rejects.toThrow("x");
  expect(estado.cancelado).toBe(true);
});
it("sem corpo: lança", async () => { await expect(lerEventos(null, () => undefined)).rejects.toThrow(); });
