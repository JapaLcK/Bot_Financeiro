import { z } from "zod";

import {
  ContratoInvalido,
  ErroDeApi,
  RenovacaoIndisponivel,
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

  it("falha de REDE no refresh não apaga a sessão nem finge que ela acabou", async () => {
    // O token pode estar vivo e o usuário só sem sinal. Apagar aqui deslogaria
    // quem entrou no elevador — e chamar de sessão expirada mandaria essa mesma
    // pessoa para a tela de login, o que é mentir sobre o estado da conta.
    await guardarCredenciais({ access: "velho", refresh: "rt_vivo" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
      .mockRejectedValueOnce(new Error("rede fora"));

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(
      RenovacaoIndisponivel,
    );
    await expect(lerCredenciais()).resolves.toEqual({
      access: "velho",
      refresh: "rt_vivo",
    });
  });

  it.each([429, 500, 503])(
    "%i no refresh NÃO apaga a sessão: é incidente passageiro",
    async (status) => {
      // Só o 401 prova que a sessão acabou. Apagar a credencial num 500
      // transformaria dois minutos de instabilidade do servidor em logout
      // definitivo de todo mundo que abrisse o app naquela janela.
      await guardarCredenciais({ access: "velho", refresh: "rt_vivo" });
      fetchFalso
        .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
        .mockResolvedValueOnce(resposta(status, { detail: "instável" }));

      // E o erro NÃO é de sessão: a tela que trata `SessaoExpirada` manda para
      // o login, e mandar alguém para lá por causa de um 500 é mentir sobre o
      // estado da conta dele.
      await expect(chamar("/x", schema)).rejects.toBeInstanceOf(
        RenovacaoIndisponivel,
      );
      await expect(lerCredenciais()).resolves.toEqual({
        access: "velho",
        refresh: "rt_vivo",
      });
    },
  );

  it("a conta trocar DURANTE o refresh não restaura a sessão antiga", async () => {
    // A conferência de entrada não basta: a troca pode acontecer com a
    // requisição no ar, e aí gravar o resultado poria a sessão antiga por cima
    // da nova — e a requisição da conta A seria repetida em seguida.
    await guardarCredenciais({ access: "token-de-A", refresh: "rt_de_A" });
    fetchFalso.mockImplementation(async (url: string) => {
      if (String(url).includes("/auth/refresh")) {
        // A conta B entra ENQUANTO o refresh da A está no ar.
        await guardarCredenciais({ access: "token-de-B", refresh: "rt_de_B" });
        return resposta(200, {
          access_token: "token-de-A2",
          refresh_token: "rt_de_A2",
          dashboard_token: "d",
          expires_in: 900,
        });
      }
      return resposta(401, { detail: "expirado" });
    });

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    // O cofre continua com a sessão da B, intacta.
    await expect(lerCredenciais()).resolves.toEqual({
      access: "token-de-B",
      refresh: "rt_de_B",
    });
  });

  it("401 no refresh apaga a sessão: é terminal", async () => {
    await guardarCredenciais({ access: "velho", refresh: "rt_morto" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
      .mockResolvedValueOnce(resposta(401, { detail: "invalid_refresh_token" }));

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("a sessão trocar no meio NÃO faz a conta A usar o token da B", async () => {
    // O usuário sai e outra conta entra entre o 401 e a renovação. Sem a
    // amarração ao token de origem, a requisição que nasceu na conta A seria
    // repetida com a credencial da B — num caminho de dinheiro, escrita na
    // conta errada.
    //
    // O mock deixa o refresh da B FUNCIONAR de propósito: se ele falhasse, o
    // teste ficaria verde com e sem a amarração e não mediria nada.
    await guardarCredenciais({ access: "token-de-A", refresh: "rt_de_A" });
    fetchFalso.mockImplementation(async (url: string) => {
      if (String(url).includes("/auth/refresh")) {
        return resposta(200, {
          access_token: "token-de-B-renovado",
          refresh_token: "rt_de_B2",
          dashboard_token: "d",
          expires_in: 900,
        });
      }
      // Entre a resposta 401 e a renovação, a conta B assume o cofre.
      await guardarCredenciais({ access: "token-de-B", refresh: "rt_de_B" });
      return resposta(401, { detail: "expirado" });
    });

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);

    // Nada foi renovado em nome da B, e nenhuma requisição saiu com token dela.
    const urls = fetchFalso.mock.calls.map(([u]: [string]) => String(u));
    expect(urls.filter((u) => u.includes("/auth/refresh"))).toHaveLength(0);
    const usados = fetchFalso.mock.calls.map(
      ([, o]: [string, RequestInit]) =>
        (o.headers as Record<string, string>)["Authorization"],
    );
    expect(usados).not.toContain("Bearer token-de-B-renovado");
    // E a sessão da B ficou intacta no cofre.
    await expect(lerCredenciais()).resolves.toEqual({
      access: "token-de-B",
      refresh: "rt_de_B",
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
