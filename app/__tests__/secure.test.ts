import {
  guardarCredenciais,
  lerCredenciais,
  limparCredenciais,
  guardarCredenciaisSe,
  jtiDe,
  limparSe,
  trocarSe,
} from "@/storage/secure";

/** O cofre em memória do dublê de `expo-secure-store` (ver `jest.setup.js`). */
const cofre = (globalThis as unknown as { __cofreDeTeste: Map<string, string> })
  .__cofreDeTeste;

/** Faz a LIMPEZA no cofre falhar. Separado da gravação de propósito. */
const falharApagar = (
  globalThis as unknown as { __falharApagarNoCofre: (v: boolean) => void }
).__falharApagarNoCofre;

/** Faz a próxima escrita no cofre falhar, como um keychain recusando. */
const falharEscrita = (
  globalThis as unknown as { __falharEscritaNoCofre: (v: boolean) => void }
).__falharEscritaNoCofre;

beforeEach(async () => {
  falharEscrita(false);
  falharApagar(false);
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

  it.each([
    ['{"access":{},"refresh":{}}', "objeto no lugar do token"],
    ['{"access":123,"refresh":456}', "número no lugar do token"],
    ['{"access":"a"}', "metade do par"],
    ["null", "nulo"],
  ])("JSON válido com forma errada (%s) não vira sessão", async (bruto) => {
    // Só truthy deixava `{"access":{}}` passar, e a falha apareceria lá adiante
    // no cabeçalho da requisição, longe da causa.
    cofre.set("pb.credenciais", bruto);
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

describe("as operações de sessão são serializadas", () => {
  it("trocarSe recusa quando a sessão já é de outro dono", async () => {
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    await guardarCredenciais({ access: "b1", refresh: "rt_B" });

    const trocou = await trocarSe("rt_A", { access: "a2", refresh: "rt_A2" });
    expect(trocou).toBe(false);
    await expect(lerCredenciais()).resolves.toEqual({
      access: "b1",
      refresh: "rt_B",
    });
  });

  it("trocarSe grava quando a sessão ainda é a mesma", async () => {
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });
    await expect(
      trocarSe("rt_A", { access: "a2", refresh: "rt_A2" }),
    ).resolves.toBe(true);
    await expect(lerCredenciais()).resolves.toEqual({
      access: "a2",
      refresh: "rt_A2",
    });
  });

  it("uma troca em voo NÃO sobrescreve a conta que entrou no meio", async () => {
    // Sem a fila, "ler, comparar e gravar" tem dois pontos de `await` no meio,
    // e a entrada de outra conta cabe em qualquer um deles: a sessão antiga
    // voltaria por cima da nova. É o caso que a revisão apontou.
    await guardarCredenciais({ access: "a1", refresh: "rt_A" });

    const trocaDaA = trocarSe("rt_A", { access: "a2", refresh: "rt_A2" });
    const entradaDaB = guardarCredenciais({ access: "b1", refresh: "rt_B" });
    const [trocou] = await Promise.all([trocaDaA, entradaDaB]);

    const guardado = await lerCredenciais();
    // Ou a troca da A aconteceu ANTES da entrada da B (e a B venceu, porque
    // veio depois), ou ela foi recusada. Em nenhum caso a A volta por cima.
    expect(guardado).toEqual({ access: "b1", refresh: "rt_B" });
    expect(typeof trocou).toBe("boolean");
  });

  it("limparSe só apaga a própria sessão", async () => {
    await guardarCredenciais({ access: "b1", refresh: "rt_B" });
    await expect(limparSe("rt_A")).resolves.toBe(false);
    await expect(lerCredenciais()).resolves.toEqual({
      access: "b1",
      refresh: "rt_B",
    });
    await expect(limparSe("rt_B")).resolves.toBe(true);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("uma falha não mata a fila", async () => {
    falharEscrita(true);
    await expect(
      guardarCredenciais({ access: "x", refresh: "rt_x" }),
    ).rejects.toThrow();
    falharEscrita(false);

    // A corrente continua viva: a operação seguinte roda normalmente.
    await guardarCredenciais({ access: "ok", refresh: "rt_ok" });
    await expect(lerCredenciais()).resolves.toEqual({
      access: "ok",
      refresh: "rt_ok",
    });
  });
});

describe("jtiDe", () => {
  it("lê o jti sem depender de Buffer nem de atob", () => {
    // `Buffer` é do Node e não existe no Hermes; `atob` não é garantido em toda
    // versão do runtime. A primeira versão usava `Buffer`, o `ReferenceError`
    // era engolido pelo `catch`, e TODO token passava a "não ter jti" — um
    // conserto inerte no aparelho, verde no Jest, que roda no Node.
    expect(jtiDe("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAic2Vzc2FvLXh5eiJ9.assinatura")).toBe(
      "sessao-xyz",
    );
  });

  it("aguenta carga com `-` e `_` (o alfabeto base64URL)", () => {
    expect(jtiDe("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSIsICJqdGkiOiAiYT9iPmN-ZC9lK2YifQ.assinatura")).toBe("a?b>c~d/e+f");
  });

  it("token sem jti, malformado ou vazio devolve null", () => {
    expect(jtiDe("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAiMSJ9.assinatura")).toBeNull();
    expect(jtiDe("nao.e.jwt")).toBeNull();
    expect(jtiDe("")).toBeNull();
    expect(jtiDe("a.!!!!.c")).toBeNull();
  });
});

describe("guardarCredenciaisSe", () => {
  it("grava quando permitido", async () => {
    await expect(
      guardarCredenciaisSe(() => true, { access: "a", refresh: "rt_a" }),
    ).resolves.toBe(true);
    await expect(lerCredenciais()).resolves.toEqual({
      access: "a",
      refresh: "rt_a",
    });
  });

  it("não grava quando já chegou proibido", async () => {
    await expect(
      guardarCredenciaisSe(() => false, { access: "a", refresh: "rt_a" }),
    ).resolves.toBe(false);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("desfazer RESTAURA a sessão que já estava lá, não apaga", async () => {
    // A conta C já estava no cofre, a entrada da A é superada. Apagar deixaria
    // deslogado quem não pediu nada.
    await guardarCredenciais({ access: "c1", refresh: "rt_C" });
    let vezes = 0;
    await expect(
      guardarCredenciaisSe(() => ++vezes === 1, { access: "a1", refresh: "rt_A" }),
    ).resolves.toBe(false);
    await expect(lerCredenciais()).resolves.toEqual({
      access: "c1",
      refresh: "rt_C",
    });
  });

  it("desfazer com cofre VAZIO antes apaga, e só", async () => {
    let vezes = 0;
    await expect(
      guardarCredenciaisSe(() => ++vezes === 1, { access: "a1", refresh: "rt_A" }),
    ).resolves.toBe(false);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("falha ao DESFAZER propaga, em vez de mentir que não persistiu", async () => {
    // Devolver `false` com a credencial velha no cofre seria o pior dos dois
    // mundos: quem chamou traduz `false` em "outra entrada assumiu" e segue
    // tranquilo, com o aparelho guardando a sessão errada.
    let vezes = 0;
    falharApagar(true);
    await expect(
      guardarCredenciaisSe(() => ++vezes === 1, { access: "a", refresh: "rt_a" }),
    ).rejects.toThrow();
    falharApagar(false);
  });
});
