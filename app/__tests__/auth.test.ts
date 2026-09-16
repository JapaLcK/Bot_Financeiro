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

  it("apaga mesmo quando o servidor não responde", async () => {
    // Se a limpeza dependesse da rede, um logout no metrô deixaria a credencial
    // no keychain e o próximo a abrir o app entraria na conta de quem achou que
    // tinha saído.
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    fetchFalso.mockRejectedValue(new Error("rede fora"));

    await sair();
    await expect(lerCredenciais()).resolves.toBeNull();
  });
});
