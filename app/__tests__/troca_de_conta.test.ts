/**
 * Corrida entre CONTAS: o que acontece quando alguém sai e outra pessoa entra
 * com requisições da primeira ainda no ar.
 *
 * Separado por assunto (§0.5). Todo caso aqui prende a mesma invariante: uma
 * requisição que nasceu numa sessão nunca pode ser concluída com a credencial
 * de outra — num POST de dinheiro, seria escrita na conta errada.
 */
import { z } from "zod";

import { SessaoExpirada, _resetRenovacao, chamar } from "@/api/client";
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

    await expect(atrasada).rejects.toBeInstanceOf(SessaoExpirada);
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
    await expect(atrasada).rejects.toBeInstanceOf(SessaoExpirada);
    const usados = fetchFalso.mock.calls.map(
      ([, o]: [string, RequestInit]) =>
        (o.headers as Record<string, string>)["Authorization"],
    );
    expect(usados.filter((u) => u === "Bearer aB1")).toHaveLength(1);
  });
});
