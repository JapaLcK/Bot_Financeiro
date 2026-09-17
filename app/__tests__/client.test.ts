import { z } from "zod";

import {
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

/** Faz a GRAVAÇÃO no cofre falhar, como um keychain recusando. */
const falharEscrita = (
  globalThis as unknown as { __falharEscritaNoCofre: (v: boolean) => void }
).__falharEscritaNoCofre;

/** Faz a LIMPEZA no cofre falhar. Separado da gravação de propósito. */
const falharApagar = (
  globalThis as unknown as { __falharApagarNoCofre: (v: boolean) => void }
).__falharApagarNoCofre;

beforeEach(async () => {
  fetchFalso.mockReset();
  globalThis.fetch = fetchFalso as unknown as typeof fetch;
  _resetRenovacao();
  falharEscrita(false);
  falharApagar(false);
  await limparCredenciais();
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

  it("401 ATRASADO não manda o usuário para o login com a sessão viva", async () => {
    // Duas requisições saem com o MESMO access expirado. A primeira renova e
    // termina; só então a segunda recebe o 401 dela. Nesse instante o cofre já
    // tem o token novo, e a conferência de dono acusaria "a sessão trocou" —
    // com a sessão perfeitamente viva. O sintoma seria a tela de login
    // aparecendo de forma intermitente em tela que carrega várias coisas juntas.
    //
    // O portão abaixo é o que força a ORDEM: sem ele as duas leem o cofre antes
    // da rotação e a corrida não acontece, e o teste passa com e sem o conserto
    // (medido — esta é a segunda versão).
    await guardarCredenciais({ access: "velho", refresh: "rt_velho" });
    let abrirPortao: () => void = () => {};
    const portao = new Promise<void>((r) => (abrirPortao = r));

    fetchFalso.mockImplementation(async (url: string, o: RequestInit) => {
      const u = String(url);
      if (u.includes("/auth/refresh")) {
        return resposta(200, {
          access_token: "novo",
          refresh_token: "rt_novo",
          dashboard_token: "d",
          expires_in: 900,
        });
      }
      const auth = (o.headers as Record<string, string>)["Authorization"];
      if (auth === "Bearer novo") return resposta(200, { ok: true });
      // O 401 da segunda requisição só chega depois que a primeira terminou.
      if (u.includes("/atrasada")) await portao;
      return resposta(401, { detail: "expirado" });
    });

    const atrasada = chamar("/atrasada", schema);
    await expect(chamar("/primeira", schema)).resolves.toEqual({ ok: true });
    abrirPortao();

    // A atrasada renova com um token já consumido e, mesmo assim, conclui: a
    // sessão está viva, só foi renovada por um vizinho.
    await expect(atrasada).resolves.toEqual({ ok: true });
  });

  it("gravação que falha DEPOIS da rotação apaga o token já consumido", async () => {
    // Aqui o servidor respondeu 200 e rotacionou: o token velho está
    // COMPROVADAMENTE gasto. Deixá-lo no cofre garantiria o replay na renovação
    // seguinte, e o servidor trata replay como roubo — revoga tudo do usuário,
    // em todos os aparelhos. Apagar troca isso por um login a mais AQUI.
    //
    // É o que separa este caso do ambíguo (resposta perdida), onde não se sabe
    // se houve rotação e preservar é o certo.
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
      );
    falharEscrita(true);

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    falharEscrita(false);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("corpo MALFORMADO num 200 apaga o token: ele já foi consumido", async () => {
    // O servidor respondeu 200 e rotacionou; o corpo é que veio truncado. O
    // token de origem está gasto do mesmo jeito, e preservá-lo garantiria o
    // replay na renovação seguinte — que o servidor trata como roubo.
    await guardarCredenciais({ access: "velho", refresh: "rt_velho" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
      .mockResolvedValueOnce(resposta(200, { access_token: "só isso" }));

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("limpeza que falha no 401 NÃO transforma fim de sessão em erro temporário", async () => {
    // O 401 prova que a sessão morreu. Se o keychain recusar a limpeza, o
    // veredito continua sendo o mesmo: mostrar "tente de novo" guardando
    // credencial inútil seria a tela errada e o estado errado.
    await guardarCredenciais({ access: "velho", refresh: "rt_morto" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
      .mockResolvedValueOnce(resposta(401, { detail: "invalid_refresh_token" }));
    falharApagar(true);

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    falharApagar(false);
  });

  it("401 MUITO atrasado, depois de DUAS rotações, ainda é a mesma sessão", async () => {
    // Uma requisição pode ficar parada enquanto a sessão roda duas ou três
    // vezes (rt0 → rt1 → rt2). Lembrar só do último salto trataria a mais
    // atrasada de todas como conta trocada — justamente a que mais precisa da
    // tolerância.
    //
    // `expirados` é o que força a SEGUNDA rotação: sem ele o mock devolvia 200
    // no primeiro token novo e só havia um salto, e o caso ficava verde com e
    // sem o conserto (medido — esta é a segunda versão).
    await guardarCredenciais({ access: "a0", refresh: "rt0" });
    const expirados = new Set(["a0"]);
    let rodada = 0;
    let soltar: () => void = () => {};
    const portao = new Promise<void>((r) => (soltar = r));

    fetchFalso.mockImplementation(async (url: string, o: RequestInit) => {
      const u = String(url);
      if (u.includes("/auth/refresh")) {
        rodada += 1;
        return resposta(200, {
          access_token: `a${rodada}`,
          refresh_token: `rt${rodada}`,
          dashboard_token: "d",
          expires_in: 900,
        });
      }
      if (u.includes("/atrasada")) await portao;
      const auth = (o.headers as Record<string, string>)["Authorization"] ?? "";
      const token = auth.replace("Bearer ", "");
      return expirados.has(token)
        ? resposta(401, { detail: "expirado" })
        : resposta(200, { ok: true });
    });

    const atrasada = chamar("/atrasada", schema);
    await expect(chamar("/um", schema)).resolves.toEqual({ ok: true });
    // Segundo salto: o token recém-emitido também vence.
    expirados.add("a1");
    await expect(chamar("/dois", schema)).resolves.toEqual({ ok: true });
    expect(rodada).toBe(2);
    soltar();

    // A atrasada partiu de `rt0`, duas rotações atrás, e mesmo assim conclui.
    await expect(atrasada).resolves.toEqual({ ok: true });
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

  it("401 no refresh apaga a sessão: é terminal", async () => {
    await guardarCredenciais({ access: "velho", refresh: "rt_morto" });
    fetchFalso
      .mockResolvedValueOnce(resposta(401, { detail: "expirado" }))
      .mockResolvedValueOnce(resposta(401, { detail: "invalid_refresh_token" }));

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("limpeza que falha DEPOIS do retry não engole o fim de sessão", async () => {
    // Renovou, repetiu, e o 401 voltou: a sessão morreu entre as duas
    // requisições. Se a limpeza do keychain falhar aqui, o erro dela não pode
    // escapar no lugar do `SessaoExpirada` — a tela ficaria sem veredito e a
    // credencial morta continuaria visível para `temSessao()`.
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
    falharApagar(true);

    await expect(chamar("/x", schema)).rejects.toBeInstanceOf(SessaoExpirada);
    falharApagar(false);
  });

  it("não tenta renovar quando a rota é pública", async () => {
    fetchFalso.mockResolvedValue(resposta(401, { detail: "nao" }));
    await expect(chamar("/publica", schema, { semAuth: true })).rejects.toBeInstanceOf(
      ErroDeApi,
    );
    expect(fetchFalso).toHaveBeenCalledTimes(1);
  });
});
