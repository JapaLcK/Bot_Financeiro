/**
 * A FORMA da requisição que o app manda: credencial, cabeçalhos e cookie.
 *
 * Separado por assunto (§0.5): aqui não há ciclo de vida de sessão nem
 * tradução de erro, só o que sai no fio.
 */
import { z } from "zod";

import { chamar } from "@/api/client";
import { guardarCredenciais, limparCredenciais } from "@/storage/secure";

const schema = z.object({ ok: z.boolean() });

function resposta(status: number, corpo: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => corpo,
  } as Response;
}

const fetchFalso = jest.fn();

beforeEach(async () => {
  fetchFalso.mockReset();
  globalThis.fetch = fetchFalso as unknown as typeof fetch;
  await limparCredenciais();
});

describe("credencial na requisição", () => {
  it("manda Bearer, o header do app e NENHUM cookie", async () => {
    await guardarCredenciais({ access: "tok-a", refresh: "rt_r" });
    fetchFalso.mockResolvedValue(resposta(200, { ok: true }));

    await chamar("/x", schema);

    const [, opcoes] = fetchFalso.mock.calls[0];
    expect(opcoes.headers["Authorization"]).toBe("Bearer tok-a");
    expect(opcoes.headers["X-PigBank-Client"]).toBe("app");
    // `credentials: omit` é o que impede o cookie jar do React Native de
    // guardar credencial ambiente sem querer — com ela, a escrita SEGUINTE
    // passaria a levar cookie e o servidor voltaria a exigir o par do CSRF.
    expect(opcoes.credentials).toBe("omit");
  });

  it("não manda credencial em rota pública", async () => {
    await guardarCredenciais({ access: "tok-a", refresh: "rt_r" });
    fetchFalso.mockResolvedValue(resposta(200, { ok: true }));

    await chamar("/publica", schema, { semAuth: true });

    expect(fetchFalso.mock.calls[0][1].headers["Authorization"]).toBeUndefined();
  });
});
