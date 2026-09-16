import {
  guardarCredenciais,
  lerCredenciais,
  limparCredenciais,
} from "@/storage/secure";

/** O cofre em memória do dublê de `expo-secure-store` (ver `jest.setup.js`). */
const cofre = (global as unknown as { __cofreDeTeste: Map<string, string> })
  .__cofreDeTeste;

beforeEach(async () => {
  cofre.clear();
  await limparCredenciais();
});

describe("o par de credenciais é atômico", () => {
  it("guarda e lê o par inteiro", async () => {
    await guardarCredenciais({ access: "a1", refresh: "rt_1" });
    await expect(lerCredenciais()).resolves.toEqual({
      access: "a1",
      refresh: "rt_1",
    });
  });

  it("o par ocupa UMA chave, então não existe estado intermediário", async () => {
    // A prova é por construção, e é o ponto do desenho: com duas chaves, uma
    // escrita podia commitar e a outra não — processo morto no meio da rotação,
    // keychain recusando. Sobrava um access NOVO ao lado de um refresh já
    // CONSUMIDO, a renovação seguinte reapresentava o token gasto, e o servidor
    // trata reapresentação como ROUBO: revoga tudo do usuário. Deslogar de
    // todos os aparelhos por causa de uma escrita parcial.
    //
    // Com uma chave só não há meio: ou o par novo está lá, ou o antigo
    // continua — e os dois são pares coerentes.
    await guardarCredenciais({ access: "a1", refresh: "rt_1" });
    expect([...cofre.keys()]).toHaveLength(1);

    await guardarCredenciais({ access: "a2", refresh: "rt_2" });
    expect([...cofre.keys()]).toHaveLength(1);
    await expect(lerCredenciais()).resolves.toEqual({
      access: "a2",
      refresh: "rt_2",
    });
  });

  it("meia credencial no cofre NÃO vira sessão", async () => {
    // Cobre o caso legado: um cofre que ficou com o formato antigo, ou um valor
    // truncado. Metade não é sessão.
    cofre.set("pb.credenciais", JSON.stringify({ access: "a1" }));
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("valor corrompido vira sessão ausente, não exceção na tela", async () => {
    cofre.set("pb.credenciais", "{isso não é json");
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("sem nada guardado, não há sessão", async () => {
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("limpar remove tudo", async () => {
    await guardarCredenciais({ access: "a1", refresh: "rt_1" });
    await limparCredenciais();
    expect([...cofre.keys()]).toHaveLength(0);
  });
});
