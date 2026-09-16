import { sair } from "@/services/auth";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";

const cofre = (global as unknown as { __cofreDeTeste: Map<string, string> })
  .__cofreDeTeste;
const fetchFalso = jest.fn();

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
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    fetchFalso.mockResolvedValue(resposta(200, {}));

    await sair();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("NÃO apaga a conta que entrou durante a saída", async () => {
    // A requisição de logout passa pela rede, e nesse tempo outra conta pode
    // entrar. Uma limpeza incondicional no fim deletaria a sessão nova — e o
    // efeito para o usuário seria entrar e ser deslogado em seguida, sem
    // explicação nenhuma.
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    fetchFalso.mockImplementation(async () => {
      await guardarCredenciais({ access: "b1", refresh: "rt_B" });
      return resposta(200, {});
    });

    await sair();
    await expect(lerCredenciais()).resolves.toEqual({
      access: "b1",
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
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    fetchFalso.mockResolvedValue(resposta(200, {}));

    const saida = sair();
    await guardarCredenciais({ access: "b1", refresh: "rt_B" });
    await saida;

    const usados = fetchFalso.mock.calls.map(
      ([, o]: [string, RequestInit]) =>
        (o.headers as Record<string, string>)["Authorization"],
    );
    expect(usados).toEqual(["Bearer a1"]);
    // E a sessão da B continua no cofre: a limpeza também é condicional.
    await expect(lerCredenciais()).resolves.toEqual({
      access: "b1",
      refresh: "rt_B",
    });
  });

  it("apaga mesmo quando o servidor não responde", async () => {
    // Se a limpeza dependesse da rede, um logout no metrô deixaria a credencial
    // no keychain e o próximo a abrir o app entraria na conta de quem achou que
    // tinha saído.
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    fetchFalso.mockRejectedValue(new Error("rede fora"));

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
