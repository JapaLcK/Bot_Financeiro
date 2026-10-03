/**
 * Como o erro do servidor chega à tela, e o que NUNCA chega.
 *
 * Separado do arquivo de renovação por assunto (§0.5): aquele mede o ciclo de
 * vida da sessão, este mede tradução de erro e contrato.
 */
import { z } from "zod";

import { ContratoInvalido, ErroDeApi, chamar } from "@/api/client";

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
    const erro = await chamar("/x", schema).catch((e: unknown) => e);
    expect(erro).toBeInstanceOf(ErroDeApi);
    expect((erro as ErroDeApi).message).toBe("Tivemos um problema aqui. Tente de novo em instantes.");
    expect((erro as ErroDeApi).corpo).toBeUndefined();
  });
});

describe("corpo do erro chega a quem chamou", () => {
  it("400 com `code`: o `ErroDeApi` carrega o corpo, e a mensagem continua vindo do `detail`", async () => {
    fetchFalso.mockResolvedValue(resposta(400, { detail: "Código inválido.", code: "mfa_code_invalid" }));
    const erro = (await chamar("/x", schema).catch((e: unknown) => e)) as ErroDeApi;
    expect(erro.message).toBe("Código inválido.");
    expect((erro.corpo as { code?: unknown }).code).toBe("mfa_code_invalid");
  });

  it("o corpo é lido UMA vez: num `Response` real a 2ª leitura rejeita, e o `code` sumiria", async () => {
    let leituras = 0;
    fetchFalso.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => {
        leituras += 1;
        if (leituras > 1) throw new TypeError("Body has already been consumed.");
        return { detail: "Código inválido.", code: "mfa_code_invalid" };
      },
    } as Response);
    const erro = (await chamar("/x", schema).catch((e: unknown) => e)) as ErroDeApi;
    expect(erro.message).toBe("Código inválido.");
    expect((erro.corpo as { code?: unknown } | undefined)?.code).toBe("mfa_code_invalid");
  });
});

describe("contrato", () => {
  it("resposta com forma diferente vira erro nomeado, não undefined solto", async () => {
    fetchFalso.mockResolvedValue(resposta(200, { okk: "sim" }));
    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(ContratoInvalido);
  });
});
