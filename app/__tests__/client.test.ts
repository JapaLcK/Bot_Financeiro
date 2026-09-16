import { z } from "zod";

import {
  ContratoInvalido,
  ErroDeApi,
  SessaoExpirada,
  chamar,
  _resetRenovacao,
} from "@/api/client";
import { guardarCredenciais, lerCredenciais, limparCredenciais } from "@/storage/secure";

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
  global.fetch = fetchFalso as unknown as typeof fetch;
  _resetRenovacao();
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

describe("renovação em 401", () => {
  it("renova uma vez e repete a requisição original", async () => {
    await guardarCredenciais({ access: "velho", refresh: "rt_velho" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
      .mockResolvedValueOnce(
        resposta(200, {
          access_token: "novo",
          refresh_token: "rt_novo",
          dashboard_token: "d",
          expires_in: 900,
        }),
      )
      .mockResolvedValueOnce(resposta(200, { ok: true }));

    await expect(chamar("/x", schema)).resolves.toEqual({ ok: true });

    // O refresh token viaja no Authorization, não no corpo: no momento do
    // refresh o access token é justamente o que expirou.
    const [urlRefresh, opcoesRefresh] = fetchFalso.mock.calls[1];
    expect(urlRefresh).toContain("/auth/refresh");
    expect(opcoesRefresh.headers["Authorization"]).toBe("Bearer rt_velho");

    // E a credencial nova ficou guardada.
    await expect(lerCredenciais()).resolves.toEqual({
      access: "novo",
      refresh: "rt_novo",
    });
  });

  it("DEDUPLICA: seis chamadas simultâneas em 401 fazem UM refresh só", async () => {
    // Sem isto, cinco dos seis refresh apresentam um token já consumido — e o
    // backend trata reapresentação como ROUBO e revoga tudo do usuário
    // (core/refresh_tokens.py:129). O sintoma seria logout aleatório ao abrir
    // o app, com a causa no cliente.
    await guardarCredenciais({ access: "velho", refresh: "rt_velho" });
    fetchFalso.mockImplementation(async (url: string, opcoes: RequestInit) => {
      if (String(url).includes("/auth/refresh")) {
        return resposta(200, {
          access_token: "novo",
          refresh_token: "rt_novo",
          dashboard_token: "d",
          expires_in: 900,
        });
      }
      const auth = (opcoes.headers as Record<string, string>)["Authorization"];
      return auth === "Bearer novo"
        ? resposta(200, { ok: true })
        : resposta(401, { detail: "expirado" });
    });

    await Promise.all(Array.from({ length: 6 }, () => chamar("/x", schema)));

    const refreshes = fetchFalso.mock.calls.filter(([u]: [string]) =>
      String(u).includes("/auth/refresh"),
    );
    expect(refreshes).toHaveLength(1);
  });

  it("refresh recusado vira SessaoExpirada e apaga a credencial", async () => {
    await guardarCredenciais({ access: "velho", refresh: "rt_morto" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
      .mockResolvedValueOnce(resposta(401, { detail: "invalid_refresh_token" }));

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("401 DEPOIS de renovar apaga a credencial: é terminal", async () => {
    // A sessão morreu entre as duas requisições (revogada noutro aparelho,
    // logout, troca de senha). Deixar a credencial no keychain faria
    // `temSessao()` seguir dizendo que sim: o app abriria como logado e tentaria
    // renovar de novo a cada início, sem nunca chegar à tela de entrada.
    await guardarCredenciais({ access: "velho", refresh: "rt_velho" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
      .mockResolvedValueOnce(
        resposta(200, {
          access_token: "novo",
          refresh_token: "rt_novo",
          dashboard_token: "d",
          expires_in: 900,
        }),
      )
      .mockResolvedValueOnce(resposta(401, { detail: "sessão revogada" }));

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("falha de REDE no refresh NÃO apaga a sessão", async () => {
    // O token pode estar vivo e o usuário só sem sinal. Apagar aqui deslogaria
    // quem entrou no elevador.
    await guardarCredenciais({ access: "velho", refresh: "rt_vivo" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
      .mockRejectedValueOnce(new Error("rede fora"));

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    await expect(lerCredenciais()).resolves.toEqual({
      access: "velho",
      refresh: "rt_vivo",
    });
  });

  it("não tenta renovar quando a rota é pública", async () => {
    fetchFalso.mockResolvedValue(resposta(401, { detail: "nao" }));
    await expect(chamar("/publica", schema, { semAuth: true })).rejects.toBeInstanceOf(
      ErroDeApi,
    );
    expect(fetchFalso).toHaveBeenCalledTimes(1);
  });
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
