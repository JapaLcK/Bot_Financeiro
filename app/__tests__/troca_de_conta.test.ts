/**
 * Corrida entre CONTAS: o que acontece quando alguém sai e outra pessoa entra
 * com requisições da primeira ainda no ar.
 *
 * Separado por assunto (§0.5). Todo caso aqui prende a mesma invariante: uma
 * requisição que nasceu numa sessão nunca pode ser concluída com a credencial
 * de outra — num POST de dinheiro, seria escrita na conta errada.
 */
import { z } from "zod";

import {
  ErroDeApi,
  RequisicaoSuperada,
  SessaoExpirada,
  _resetRenovacao,
  chamar,
} from "@/api/client";
import * as secure from "@/storage/secure";
import {
  guardarCredenciais,
  lerCredenciais,
  limparCredenciais,
} from "@/storage/secure";

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
  _resetRenovacao();
  await limparCredenciais();
});

describe("corrida entre contas", () => {
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

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(RequisicaoSuperada);

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

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(RequisicaoSuperada);
    // O cofre continua com a sessão da B, intacta.
    await expect(lerCredenciais()).resolves.toEqual({
      access: "token-de-B",
      refresh: "rt_de_B",
    });
  });


  it("401 atrasado DEPOIS de outra conta entrar não usa a credencial dela", async () => {
    // A conta A renova, a conta B entra, e só então chega o 401 atrasado da A.
    // Guardar apenas o token consumido faria o teste de dono passar e devolver
    // a credencial da B para repetir uma operação da A — num POST de dinheiro,
    // escrita na conta errada. É por isso que a memória guarda o PAR.
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    let abrirPortao: () => void = () => {};
    const portao = new Promise<void>((r) => (abrirPortao = r));

    fetchFalso.mockImplementation(async (url: string, o: RequestInit) => {
      const u = String(url);
      if (u.includes("/auth/refresh")) {
        return resposta(200, {
          access_token: "a2",
          refresh_token: "rt_A2",
          dashboard_token: "d",
          expires_in: 900,
        });
      }
      const auth = (o.headers as Record<string, string>)["Authorization"];
      if (auth === "Bearer a2") return resposta(200, { ok: true });
      if (u.includes("/atrasada")) await portao;
      return resposta(401, { detail: "expirado" });
    });

    const atrasada = chamar("/atrasada", schema);
    await expect(chamar("/primeira", schema)).resolves.toEqual({ ok: true });
    // A conta B entra DEPOIS da rotação da A e ANTES do 401 atrasado chegar.
    await guardarCredenciais({ access: "b1", refresh: "rt_B" });
    abrirPortao();

    await expect(atrasada).rejects.toBeInstanceOf(RequisicaoSuperada);
    const usados = fetchFalso.mock.calls.map(
      ([, o]: [string, RequestInit]) =>
        (o.headers as Record<string, string>)["Authorization"],
    );
    expect(usados).not.toContain("Bearer b1");
    await expect(lerCredenciais()).resolves.toEqual({
      access: "b1",
      refresh: "rt_B",
    });
  });


  it("a linhagem NÃO atravessa contas", async () => {
    // A conta A roda A0→A1, a conta B entra e roda B0→B1. Sem verificar que o
    // token consumido era a cabeça da cadeia, a linhagem viraria [A0, B0] com
    // cabeça B1 — e uma requisição atrasada da A receberia a credencial da B.
    await guardarCredenciais({ access: "aA0", refresh: "A0" });
    const expirados = new Set(["aA0", "aB0"]);
    const sucessor: Record<string, string> = { A0: "A1", B0: "B1" };
    let soltar: () => void = () => {};
    const portao = new Promise<void>((r) => (soltar = r));

    fetchFalso.mockImplementation(async (url: string, o: RequestInit) => {
      const u = String(url);
      const auth = (o.headers as Record<string, string>)["Authorization"] ?? "";
      const token = auth.replace("Bearer ", "");
      if (u.includes("/auth/refresh")) {
        const novo = sucessor[token];
        if (!novo) return resposta(401, { detail: "invalid_refresh_token" });
        return resposta(200, {
          access_token: `a${novo}`,
          refresh_token: novo,
          dashboard_token: "d",
          expires_in: 900,
        });
      }
      if (u.includes("/atrasada")) await portao;
      return expirados.has(token)
        ? resposta(401, { detail: "expirado" })
        : resposta(200, { ok: true });
    });

    const atrasada = chamar("/atrasada", schema);
    await expect(chamar("/da-A", schema)).resolves.toEqual({ ok: true });
    // A conta B assume e roda a própria linhagem.
    await guardarCredenciais({ access: "aB0", refresh: "B0" });
    await expect(chamar("/da-B", schema)).resolves.toEqual({ ok: true });
    soltar();

    // A atrasada é da conta A: tem de morrer, não herdar a sessão da B.
    await expect(atrasada).rejects.toBeInstanceOf(RequisicaoSuperada);
    const usados = fetchFalso.mock.calls.map(
      ([, o]: [string, RequestInit]) =>
        (o.headers as Record<string, string>)["Authorization"],
    );
    expect(usados.filter((u) => u === "Bearer aB1")).toHaveLength(1);
  });

  it("resposta da conta A não é entregue depois de a B assumir", async () => {
    // A tela renderizaria saldo, transação e nome de outra pessoa com o app já
    // mostrando a conta nova. Num app financeiro isso é vazamento entre contas,
    // mesmo sendo o próprio aparelho.
    await guardarCredenciais({ access: "aA", refresh: "A" });
    let soltar: () => void = () => {};
    const portao = new Promise<void>((r) => (soltar = r));

    fetchFalso.mockImplementation(async (url: string) => {
      if (String(url).includes("/da-A")) await portao;
      return resposta(200, { ok: true });
    });

    const daA = chamar("/da-A", schema);
    await new Promise<void>((r) => setImmediate(() => r()));
    // A conta B assume enquanto a resposta da A está no ar.
    await guardarCredenciais({ access: "aB", refresh: "B" });
    soltar();

    // `RequisicaoSuperada`, não `SessaoExpirada`: a sessão da B está viva, e
    // mandar a pessoa para o login logo depois de ela entrar seria o oposto do
    // que aconteceu.
    await expect(daA).rejects.toBeInstanceOf(RequisicaoSuperada);
  });

  it("ERRO da conta A também não é entregue depois de a B assumir", async () => {
    // A pessoa veria "não foi possível" sobre uma operação que ela não pediu
    // nesta sessão. A conferência precisa vir antes de olhar o status.
    await guardarCredenciais({ access: "aA", refresh: "A" });
    let soltar: () => void = () => {};
    const portao = new Promise<void>((r) => (soltar = r));
    fetchFalso.mockImplementation(async (url: string) => {
      if (String(url).includes("/da-A")) await portao;
      return resposta(500, { detail: "explodiu" });
    });

    const daA = chamar("/da-A", schema);
    await new Promise<void>((r) => setImmediate(() => r()));
    await guardarCredenciais({ access: "aB", refresh: "B" });
    soltar();

    await expect(daA).rejects.toBeInstanceOf(RequisicaoSuperada);
  });

  it("renovação legítima da PRÓPRIA sessão não é confundida com troca", async () => {
    // Controle positivo: a sessão rodou, mas é a mesma linhagem. A resposta tem
    // de ser entregue — senão a proteção acima viraria logout a cada renovação.
    await guardarCredenciais({ access: "a0", refresh: "R0" });
    fetchFalso.mockImplementation(async (url: string, o: RequestInit) => {
      const auth = (o.headers as Record<string, string>)["Authorization"] ?? "";
      if (String(url).includes("/auth/refresh")) {
        return resposta(200, {
          access_token: "a1",
          refresh_token: "R1",
          dashboard_token: "d",
          expires_in: 900,
        });
      }
      return auth === "Bearer a1"
        ? resposta(200, { ok: true })
        : resposta(401, { detail: "expirado" });
    });

    await expect(chamar("/qualquer", schema)).resolves.toEqual({ ok: true });
  });

  it("cofre vazio ANTES da renovação continua fim de sessão, não corrida", async () => {
    // Ninguém entra: a própria sessão é encerrada (a limpeza do keychain, por
    // exemplo) antes de `renovar` ler o cofre. Sem sessão nova para herdar, o
    // veredito é `SessaoExpirada`, e não `RequisicaoSuperada`.
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    fetchFalso.mockImplementation(async (url: string) => {
      if (String(url).includes("/auth/refresh")) {
        throw new Error("não deveria chamar o refresh: o cofre já estava vazio");
      }
      await limparCredenciais();
      return resposta(401, { detail: "expirado" });
    });

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
  });

  it.each([
    [
      "o refresh recusa a A depois de a B já ter assumido o cofre",
      async (url: string) => {
        if (url.includes("/auth/refresh")) {
          await guardarCredenciais({ access: "b1", refresh: "rt_B" });
          return resposta(401, { detail: "invalid_refresh_token" });
        }
        return resposta(401, { detail: "expirado" });
      },
    ],
    [
      "o refresh renova a A, e a repetição encontra a B no cofre",
      async (url: string, o: RequestInit) => {
        if (url.includes("/auth/refresh")) {
          return resposta(200, {
            access_token: "a2",
            refresh_token: "rt_A2",
            dashboard_token: "d",
            expires_in: 900,
          });
        }
        const auth = (o.headers as Record<string, string>)["Authorization"];
        if (auth === "Bearer a2") {
          await guardarCredenciais({ access: "b1", refresh: "rt_B" });
          return resposta(401, { detail: "sessão revogada" });
        }
        return resposta(401, { detail: "expirado" });
      },
    ],
  ])("%s: RequisicaoSuperada, e a B fica intacta", async (_nome, mock) => {
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    fetchFalso.mockImplementation((url: string, o: RequestInit) => mock(url, o));

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(RequisicaoSuperada);
    await expect(lerCredenciais()).resolves.toEqual({
      access: "b1",
      refresh: "rt_B",
    });
  });

  it.each([
    [200, { ok: true }],
    [400, { detail: "x" }],
  ])(
    "P1, status %i: o corpo lido depois de a B assumir não chega à tela da A",
    async (status, corpo) => {
      // A resposta é montada à mão: `resposta()` devolve o corpo pronto, e aqui
      // é a LEITURA do corpo que precisa ficar presa no portão — é ela que o
      // conserto tem de esperar antes de conferir o dono.
      await guardarCredenciais({ access: "aA", refresh: "A" });
      let abrirPortao: () => void = () => {};
      const portao = new Promise<void>((r) => (abrirPortao = r));
      fetchFalso.mockImplementation(
        async () =>
          ({
            ok: status >= 200 && status < 300,
            status,
            json: async () => {
              await portao;
              return corpo;
            },
          }) as Response,
      );

      const daA = chamar("/x", schema);
      await new Promise<void>((r) => setImmediate(() => r()));
      // A conta B assume enquanto o corpo da resposta da A está sendo lido.
      await guardarCredenciais({ access: "aB", refresh: "B" });
      abrirPortao();

      await expect(daA).rejects.toBeInstanceOf(RequisicaoSuperada);
    },
  );

  it.each([
    [200, { ok: true }],
    [400, { detail: "x" }],
  ])(
    "P1, status %i: cofre fica VAZIO (logout) antes da resposta chegar não entrega o resultado da A",
    async (status, corpo) => {
      // Ninguém entra: o cofre é esvaziado (logout) enquanto o corpo da
      // resposta da A está sendo lido. Diferente do "cofre vazio ANTES da
      // renovação" (que é 401 e fica `SessaoExpirada`): aqui é sucesso/erro
      // comum, e o resultado da A não pode chegar à tela depois do logout.
      await guardarCredenciais({ access: "aA", refresh: "A" });
      let abrirPortao: () => void = () => {};
      const portao = new Promise<void>((r) => (abrirPortao = r));
      fetchFalso.mockImplementation(
        async () =>
          ({
            ok: status >= 200 && status < 300,
            status,
            json: async () => {
              await portao;
              return corpo;
            },
          }) as Response,
      );

      const daA = chamar("/x", schema);
      await new Promise<void>((r) => setImmediate(() => r()));
      await limparCredenciais();
      abrirPortao();

      await expect(daA).rejects.toBeInstanceOf(RequisicaoSuperada);
    },
  );

  it("falha ao LER o cofre no catch não troca o erro original por erro cru", async () => {
    // Refresh 401 com sessão morta de verdade: a conferência de dono, no
    // catch, lê o cofre de novo. Se essa leitura falhar (keychain recusou), o
    // erro original (`SessaoExpirada`) não pode virar o erro cru da leitura.
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    const espiaoLeitura = jest.spyOn(secure, "lerCredenciais");
    fetchFalso.mockImplementation(async (url: string) => {
      if (String(url).includes("/auth/refresh")) {
        // Só falha a leitura DEPOIS de a requisição de refresh já ter saído.
        espiaoLeitura.mockRejectedValue(new Error("keychain recusou"));
        return resposta(401, { detail: "expirado" });
      }
      return resposta(401, { detail: "expirado" });
    });

    try {
      await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    } finally {
      espiaoLeitura.mockRestore();
    }
  });

  it("falha ao LER o cofre no catch NÃO relança erro 400 com dado da conta A", async () => {
    // `e` aqui é `ErroDeApi` (400 com o `detail` da A), não `SessaoExpirada` —
    // se essa leitura falhar, relançar `e` vazaria o corpo da A para quem
    // estiver vendo o erro. O fail-closed é a falha de leitura, sem nada da A.
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    const espiaoLeitura = jest.spyOn(secure, "lerCredenciais");
    fetchFalso.mockImplementation(async () => {
      // Só falha a leitura DEPOIS de a requisição já ter saído.
      espiaoLeitura.mockRejectedValue(new Error("keychain recusou"));
      return resposta(400, { detail: "dado da A" });
    });

    try {
      const daA = chamar("/x", schema);
      await expect(daA).rejects.not.toBeInstanceOf(ErroDeApi);
      await expect(daA).rejects.toThrow("keychain recusou");
    } finally {
      espiaoLeitura.mockRestore();
    }
  });
});
