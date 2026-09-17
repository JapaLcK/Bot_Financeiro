/**
 * Como o erro do servidor chega à tela, e o que NUNCA chega.
 *
 * Separado do arquivo de renovação por assunto (§0.5): aquele mede o ciclo de
 * vida da sessão, este mede tradução de erro e contrato.
 */
import { z } from "zod";

import { ContratoInvalido, chamar } from "@/api/client";

const schema = z.object({ ok: z.boolean() });

function resposta(status: number, corpo: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => corpo,
  } as Response;
}

const fetchFalso = jest.fn();

beforeEach(() => {
  fetchFalso.mockReset();
  globalThis.fetch = fetchFalso as unknown as typeof fetch;
});

describe("erro não vira JSON na tela", () => {
  it.each([
    [{ detail: "E-mail ou senha incorretos." }, "E-mail ou senha incorretos."],
    [{ detail: { error: "pro_required", message: "Recurso do Pro." } }, "Recurso do Pro."],
    [{ detail: { error: "subscription_required" } }, "subscription_required"],
    [{ detail: [{ loc: ["body"], msg: "campo" }] }, "Não foi possível completar a ação."],
  ])("formato %#", async (corpo, esperado) => {
    fetchFalso.mockResolvedValue(resposta(400, corpo));
    await expect(chamar("/x", schema)).rejects.toThrow(esperado);
  });

  it("500 não expõe detalhe do servidor", async () => {
    fetchFalso.mockResolvedValue(resposta(500, { detail: "psycopg.OperationalError" }));
    await expect(chamar("/x", schema)).rejects.toThrow(
      "Tivemos um problema aqui. Tente de novo em instantes.",
    );
  });
});

describe("contrato", () => {
  it("resposta com forma diferente vira erro nomeado, não undefined solto", async () => {
    fetchFalso.mockResolvedValue(resposta(200, { okk: "sim" }));
    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(ContratoInvalido);
  });
});
