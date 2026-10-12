import { lerEvento } from "@/api/schemas/painel";
import exemplos from "../../../tests/fixtures/ai_chat_eventos.json"; // lido também pelo pytest (tests/test_ai_chat_stream.py)

const ex = exemplos as Record<string, Record<string, unknown>>;
const sem = (tipo: string, chave: string) => { const { [chave]: _, ...resto } = ex[tipo]!; return resto; };
const bloco = (b: object) => ({ ...ex.bloco, bloco: { ...(ex.bloco!.bloco as object), ...b } });
const aba = (mes: string | null) => ({ mes, recursos: {} });

it.each(Object.keys(ex))("a fixture compartilhada: o exemplo de `%s` é aceito", (tipo) => { expect(lerEvento(ex[tipo])?.tipo).toBe(tipo); });
it.each([["passo", "texto"], ["bloco", "bloco"], ["delta", "texto"], ["fim", "reply"], ["fim", "usage"], ["fim", "message_id"]])("`%s` sem `%s` é descartado", (tipo, chave) => { expect(lerEvento(sem(tipo, chave))).toBeNull(); });
it("tipo desconhecido, sem tipo e lixo: null", () => { expect(lerEvento({ tipo: "novo" })).toBeNull(); expect(lerEvento({})).toBeNull(); expect(lerEvento(null)).toBeNull(); expect(lerEvento({ tipo: "toString" })).toBeNull(); });
it("bloco com card fora do catálogo, com 4 abas, sem aba ou com mês malformado: null", () => {
  expect(lerEvento(bloco({ card: "inventado" }))).toBeNull();
  expect(lerEvento(bloco({ abas: [aba(null), aba(null), aba(null), aba(null)] }))).toBeNull();
  expect(lerEvento(bloco({ abas: [] }))).toBeNull();
  for (const mes of ["2026-13", "2026-1", "outubro"]) expect(lerEvento(bloco({ abas: [aba(mes)] }))).toBeNull();
  expect(lerEvento(bloco({ abas: [aba("2026-10"), aba(null)] }))?.tipo).toBe("bloco");
});
it("fim com chips inválidos continua válido, com chips vazios; passo com prévia desconhecida vira null", () => {
  for (const chips of [["", "x"], ["a".repeat(41)], ["1", "2", "3", "4", "5"], "texto", [1]]) { const e = lerEvento({ ...ex.fim, chips }); expect(e).toMatchObject({ tipo: "fim", reply: "Oi!", chips: [] }); }
  expect(lerEvento({ ...ex.passo, previa: "inventado" })).toMatchObject({ tipo: "passo", previa: null });
  expect(lerEvento({ ...ex.fim, message_id: 0 })).toBeNull();
});
