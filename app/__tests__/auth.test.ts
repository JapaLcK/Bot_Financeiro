import { entrar, sair, verificarMfa } from "@/services/auth";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";

const cofre = (global as unknown as { __cofreDeTeste: Map<string, string> })
  .__cofreDeTeste;
const fetchFalso = jest.fn();

/** Dois access tokens da MESMA sessão: `jti` igual, conteúdo diferente. */
const ACCESS_A = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAic2Vzc2FvLUEifQ.assinatura";
const ACCESS_A2 = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAic2Vzc2FvLUEifQ.assinatura".replace("assinatura", "outra");
/** E um de OUTRA sessão. */
const ACCESS_B = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAic2Vzc2FvLUIifQ.assinatura";

beforeEach(() => {
  fetchFalso.mockReset();
  global.fetch = fetchFalso as unknown as typeof fetch;
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
    expect(usados).toEqual([`Bearer ${ACCESS_A}`]);
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
