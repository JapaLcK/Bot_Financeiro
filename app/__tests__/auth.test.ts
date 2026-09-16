import {
  EntradaSuperada,
  entrar,
  sair,
  verificarMfa,
} from "@/services/auth";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";

const cofre = (globalThis as unknown as { __cofreDeTeste: Map<string, string> })
  .__cofreDeTeste;
const fetchFalso = jest.fn();

/** Faz a GRAVAÇÃO no cofre falhar, como um keychain recusando. */
const falharEscrita = (
  globalThis as unknown as { __falharEscritaNoCofre: (v: boolean) => void }
).__falharEscritaNoCofre;

/** Prende a próxima gravação no cofre até a promessa resolver. */
const atrasarEscrita = (
  globalThis as unknown as { __atrasarEscritaNoCofre: (p: Promise<void>) => void }
).__atrasarEscritaNoCofre;

/** Dois access tokens da MESMA sessão: `jti` igual, conteúdo diferente. */
const ACCESS_A = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAic2Vzc2FvLUEifQ.assinatura";
const ACCESS_A2 = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAic2Vzc2FvLUEifQ.assinatura".replace("assinatura", "outra");
/** E um de OUTRA sessão. */
const ACCESS_B = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAic2Vzc2FvLUIifQ.assinatura";

beforeEach(() => {
  falharEscrita(false);
  fetchFalso.mockReset();
  globalThis.fetch = fetchFalso as unknown as typeof fetch;
  cofre.clear();
});

function resposta(status: number, corpo: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => corpo,
  } as Response;
}

describe("sair", () => {
  it("apaga a sessão que iniciou a saída", async () => {
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
    fetchFalso.mockResolvedValue(resposta(200, {}));

    await sair();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("NÃO apaga a conta que entrou durante a saída", async () => {
    // A requisição de logout passa pela rede, e nesse tempo outra conta pode
    // entrar. Uma limpeza incondicional no fim deletaria a sessão nova — e o
    // efeito para o usuário seria entrar e ser deslogado em seguida, sem
    // explicação nenhuma.
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
    fetchFalso.mockImplementation(async () => {
      await guardarCredenciais({ access: ACCESS_B, refresh: "rt_B" });
      return resposta(200, {});
    });

    await sair();
    await expect(lerCredenciais()).resolves.toEqual({
      access: ACCESS_B,
      refresh: "rt_B",
    });
  });

  it("o logout fala pela sessão que o INICIOU, não pela que estiver no cofre", async () => {
    // A captura acontece no começo do `sair()`; a requisição sai depois. Se ela
    // relesse o cofre, uma conta que entrasse nesse intervalo teria a própria
    // sessão revogada NO SERVIDOR pelo logout da anterior — e a limpeza local
    // condicional não desfaria isso.
    //
    // A fila do cofre é o que torna a corrida determinística aqui: a leitura do
    // `sair()` entra primeiro, a entrada da B em seguida, e só então a leitura
    // que o `chamar()` faria. Sem o portão a B entraria tarde demais e o caso
    // ficaria verde com e sem o conserto (medido — esta é a segunda versão).
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
    fetchFalso.mockResolvedValue(resposta(200, {}));

    const saida = sair();
    await guardarCredenciais({ access: ACCESS_B, refresh: "rt_B" });
    await saida;

    const usados = fetchFalso.mock.calls.map(
      ([, o]: [string, RequestInit]) =>
        (o.headers as Record<string, string>)["Authorization"],
    );
    // O REFRESH token, não o access: o access pode estar expirado, e aí o
    // servidor não decodificaria nada e o logout voltaria 200 sem encerrar
    // sessão nenhuma.
    expect(usados).toEqual(["Bearer rt_A"]);
    // E a sessão da B continua no cofre: a limpeza também é condicional.
    await expect(lerCredenciais()).resolves.toEqual({
      access: ACCESS_B,
      refresh: "rt_B",
    });
  });

  it("apaga mesmo quando o servidor não responde", async () => {
    // Se a limpeza dependesse da rede, um logout no metrô deixaria a credencial
    // no keychain e o próximo a abrir o app entraria na conta de quem achou que
    // tinha saído.
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
    fetchFalso.mockRejectedValue(new Error("rede fora"));

    await sair();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("renovação DURANTE a saída não salva a sessão do logout", async () => {
    // Entre a captura e a limpeza há tempo de rede, e outra tela pode renovar
    // esta mesma sessão. Comparar pelo refresh token recusaria apagar — e o
    // usuário sairia com uma credencial rotacionada e VÁLIDA no aparelho.
    // O `jti` é o que não muda na rotação, e é por ele que a saída identifica
    // a própria sessão.
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
    fetchFalso.mockImplementation(async () => {
      // Outra tela renova a MESMA sessão: refresh novo, `jti` igual.
      await guardarCredenciais({ access: ACCESS_A2, refresh: "rt_A2" });
      return resposta(200, {});
    });

    await sair();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("saída sem sessão capturada não manda requisição nenhuma", async () => {
    // Saída duplicada ou tardia. Tentar seria pior que não fazer: a requisição
    // releria o cofre e poderia sair autenticada por uma conta que entrou
    // depois, revogando no servidor a sessão de quem acabou de chegar.
    await sair();
    expect(fetchFalso).not.toHaveBeenCalled();
  });
});

describe("entrar", () => {
  it("conta com dois fatores devolve DESAFIO, não erro de contrato", async () => {
    // `/auth/login` não devolve credencial quando há MFA: devolve um desafio.
    // Com um esquema só, o login legítimo dessas contas virava
    // `ContratoInvalido` e a pessoa via "resposta inesperada" no lugar da tela
    // de código.
    fetchFalso.mockResolvedValue(
      resposta(200, {
        mfa_required: true,
        mfa_challenge: "desafio-123",
        email: "com@mfa.com",
      }),
    );

    const r = await entrar("com@mfa.com", "senha");
    expect(r).toEqual({
      fase: "mfa",
      desafio: "desafio-123",
      email: "com@mfa.com",
    });
    // E nada foi guardado: ainda não há sessão.
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("conta sem MFA entra direto e guarda a credencial", async () => {
    fetchFalso.mockResolvedValue(
      resposta(200, {
        user_id: 1,
        email: "sem@mfa.com",
        access_token: "a1",
        refresh_token: "rt_1",
        dashboard_token: "d",
        expires_in: 900,
      }),
    );

    const r = await entrar("sem@mfa.com", "senha");
    expect(r.fase).toBe("pronta");
    await expect(lerCredenciais()).resolves.toEqual({
      access: "a1",
      refresh: "rt_1",
    });
  });

  it("entrada VELHA não sobrescreve a mais nova, mesmo terminando depois", async () => {
    // Alguém erra a conta, corrige e envia de novo antes de a primeira
    // responder. Sem senha de chegada, o resultado aplicado é o de quem TERMINA
    // por último — e a pessoa acaba logada na conta que já tinha abandonado,
    // com a tela mostrando a outra.
    let soltarA: () => void = () => {};
    const esperaA = new Promise<void>((r) => (soltarA = r));

    fetchFalso.mockImplementation(async (_u: string, o: RequestInit) => {
      const corpo = JSON.parse(String(o.body)) as { email: string };
      if (corpo.email === "a@x.com") await esperaA;
      const sufixo = corpo.email === "a@x.com" ? "A" : "B";
      return resposta(200, {
        user_id: 1,
        email: corpo.email,
        access_token: `access-${sufixo}`,
        refresh_token: `rt_${sufixo}`,
        dashboard_token: "d",
        expires_in: 900,
      });
    });

    const a = entrar("a@x.com", "s");
    const b = await entrar("b@x.com", "s");
    expect(b.fase).toBe("pronta");
    soltarA();

    await expect(a).rejects.toBeInstanceOf(EntradaSuperada);
    // O cofre tem a conta B, que é a que a pessoa pediu por último.
    await expect(lerCredenciais()).resolves.toEqual({
      access: "access-B",
      refresh: "rt_B",
    });
  });

  it("DESAFIO velho também é descartado", async () => {
    // Um desafio atrasado levaria a tela para a etapa de código da conta
    // ERRADA, e o usuário digitaria o token de uma conta para completar a
    // entrada de outra. A conferência tem de vir antes deste retorno também.
    let soltarA: () => void = () => {};
    const esperaA = new Promise<void>((r) => (soltarA = r));
    fetchFalso.mockImplementation(async (_u: string, o: RequestInit) => {
      const corpo = JSON.parse(String(o.body)) as { email: string };
      if (corpo.email === "a@x.com") {
        await esperaA;
        return resposta(200, {
          mfa_required: true,
          mfa_challenge: "desafio-da-A",
          email: corpo.email,
        });
      }
      return resposta(200, {
        user_id: 2,
        email: corpo.email,
        access_token: "access-B",
        refresh_token: "rt_B",
        dashboard_token: "d",
        expires_in: 900,
      });
    });

    const a = entrar("a@x.com", "s");
    await entrar("b@x.com", "s");
    soltarA();
    await expect(a).rejects.toBeInstanceOf(EntradaSuperada);
  });

  it("entrada superada DURANTE a gravação não vence", async () => {
    // A janela é estreita e específica: a conta A já passou pela conferência e
    // sua GRAVAÇÃO está em andamento quando a conta B começa. Se a conferência
    // não estiver dentro da gravação, o aparelho fica logado como A enquanto a
    // tela mostra a outra.
    //
    // O portão do cofre é o que torna essa janela alcançável no teste; sem ele
    // a conta B terminava antes e a guarda anterior já bastava, deixando o caso
    // verde com e sem esta proteção (medido).
    let soltarGravacao: () => void = () => {};
    const gravacaoPresa = new Promise<void>((r) => (soltarGravacao = r));
    fetchFalso.mockImplementation(async (_u: string, o: RequestInit) => {
      const corpo = JSON.parse(String(o.body)) as { email: string };
      return resposta(200, {
        user_id: 1,
        email: corpo.email,
        access_token: `access-${corpo.email[0]}`,
        refresh_token: `rt_${corpo.email[0]}`,
        dashboard_token: "d",
        expires_in: 900,
      });
    });

    atrasarEscrita(gravacaoPresa);
    const a = entrar("a@x.com", "s");
    // Deixa a conta A percorrer TUDO até ficar presa na gravação. Sem isto ela
    // ainda estaria na requisição e morreria na guarda anterior, que já existe —
    // e o caso não mediria esta proteção (medido: passava com e sem ela).
    await new Promise<void>((r) => setImmediate(() => r()));
    // Só agora a conta B começa, com a gravação da A presa dentro da fila.
    const b = entrar("b@x.com", "s");
    soltarGravacao();

    await expect(a).rejects.toBeInstanceOf(EntradaSuperada);
    await expect(b).resolves.toMatchObject({ fase: "pronta" });
    await expect(lerCredenciais()).resolves.toEqual({
      access: "access-b",
      refresh: "rt_b",
    });
  });

  it("FALHA de entrada superada não vira erro na tela", async () => {
    // A conta A responde 401 depois de a entrada da conta B ter dado certo. A
    // pessoa veria "senha incorreta" enquanto a entrada que ela pediu por
    // último estava indo bem — escolher a falha velha é o pior dos dois.
    let soltarA: () => void = () => {};
    const esperaA = new Promise<void>((r) => (soltarA = r));
    fetchFalso.mockImplementation(async (_u: string, o: RequestInit) => {
      const corpo = JSON.parse(String(o.body)) as { email: string };
      if (corpo.email === "a@x.com") {
        await esperaA;
        return resposta(401, { detail: "E-mail ou senha incorretos." });
      }
      return resposta(200, {
        user_id: 2,
        email: corpo.email,
        access_token: "access-B",
        refresh_token: "rt_B",
        dashboard_token: "d",
        expires_in: 900,
      });
    });

    const a = entrar("a@x.com", "s");
    await entrar("b@x.com", "s");
    soltarA();

    await expect(a).rejects.toBeInstanceOf(EntradaSuperada);
    await expect(a).rejects.not.toThrow("E-mail ou senha incorretos.");
  });

  it("falha de GRAVAÇÃO de entrada superada também vira EntradaSuperada", async () => {
    // O invólucro tem de cobrir até o fim: uma falha de keychain numa tentativa
    // que já foi superada não é problema do keychain para o usuário, é uma
    // entrada que perdeu a vez. Mostrar o erro de armazenamento faria a pessoa
    // achar que o aparelho está com defeito.
    fetchFalso.mockImplementation(async (_u: string, o: RequestInit) => {
      const corpo = JSON.parse(String(o.body)) as { email: string };
      return resposta(200, {
        user_id: 1,
        email: corpo.email,
        access_token: "access",
        refresh_token: "rt",
        dashboard_token: "d",
        expires_in: 900,
      });
    });

    let soltar: () => void = () => {};
    const presa = new Promise<void>((r) => (soltar = r));
    atrasarEscrita(presa);
    const a = entrar("a@x.com", "s");
    await new Promise<void>((r) => setImmediate(() => r()));
    falharEscrita(true);
    const b = entrar("b@x.com", "s");
    soltar();

    await expect(a).rejects.toBeInstanceOf(EntradaSuperada);
    await expect(b).rejects.toThrow();
    falharEscrita(false);
  });

  it("sair invalida entrada em voo", async () => {
    // Sair é a intenção mais recente. Um login que terminasse depois gravaria
    // credencial numa sessão que o usuário acabou de encerrar.
    await guardarCredenciais({ access: ACCESS_A, refresh: "rt_A" });
    let soltar: () => void = () => {};
    const espera = new Promise<void>((r) => (soltar = r));
    fetchFalso.mockImplementation(async (u: string) => {
      if (String(u).includes("/auth/login")) {
        await espera;
        return resposta(200, {
          user_id: 9,
          email: "tardio@x.com",
          access_token: "tardio",
          refresh_token: "rt_tardio",
          dashboard_token: "d",
          expires_in: 900,
        });
      }
      return resposta(200, {});
    });

    const entrando = entrar("tardio@x.com", "s");
    await sair();
    soltar();

    await expect(entrando).rejects.toBeInstanceOf(EntradaSuperada);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("verificarMfa completa a entrada", async () => {
    fetchFalso.mockResolvedValue(
      resposta(200, {
        user_id: 1,
        email: "com@mfa.com",
        access_token: "a2",
        refresh_token: "rt_2",
        dashboard_token: "d",
        expires_in: 900,
      }),
    );

    await expect(verificarMfa("desafio-123", "000000")).resolves.toMatchObject({
      user_id: 1,
    });
    await expect(lerCredenciais()).resolves.toEqual({
      access: "a2",
      refresh: "rt_2",
    });
  });
});
