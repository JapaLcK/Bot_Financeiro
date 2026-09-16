/**
 * A tela de entrada, uma ação por vez: o critério da Fase 1 (e-mail →
 * `/auth/me` → Sair), os textos de falha e o Sair que não consegue apagar.
 * As corridas entre ações ficam em `inicio_corridas.test.ts`.
 *
 * Com os serviços reais, o `fetch` falso e o dublê do cofre.
 */
import { temSessao } from "@/services/auth";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";
import {
  entrarNaTela,
  montar,
  sairNaTela,
  tocar,
  type Estado,
} from "@/ui/inicio";

import {
  C,
  E,
  GENERICO,
  S,
  chamadas,
  falharApagar,
  falharLeitura,
  fetchFalso,
  gravador,
  prepararCaso,
  resposta,
  rotear,
  type Rota,
} from "./inicio_apoio";

beforeEach(prepararCaso);

describe("tela de entrada", () => {
  it("critério da Fase 1: entra, lê o nome no /auth/me, e sai", async () => {
    rotear();
    const { aplicados, aplicar } = gravador();

    await tocar(() => entrarNaTela(" ana@x.com ", " s3nha "), aplicar);
    await tocar(sairNaTela, aplicar);

    expect(aplicados).toEqual([C, { fase: "pronto", nome: "Ana" }, C, E]);
    const [login, perfil, logout] = chamadas();
    // O e-mail sem espaços; a senha como foi digitada.
    expect(login).toMatchObject({
      caminho: "/auth/login",
      corpo: { email: "ana@x.com", password: " s3nha " },
    });
    expect(perfil).toMatchObject({ caminho: "/auth/me", auth: "Bearer access-ana" });
    expect(logout).toMatchObject({ caminho: "/auth/logout" });
    await expect(temSessao()).resolves.toBe(false);
  });

  it("abrir sem sessão mostra o formulário sem tocar a rede", async () => {
    // A rede fora é o que separa: sem a pergunta ao cofre, a abertura offline
    // mostraria a falha de rede no lugar do formulário.
    fetchFalso.mockRejectedValue(new TypeError("Network request failed"));
    const { aplicados, aplicar } = gravador();

    await montar(aplicar);

    expect(aplicados).toEqual([C, E]);
    expect(fetchFalso).not.toHaveBeenCalled();
  });

  it("conta com dois fatores recebe aviso e fica no formulário", async () => {
    rotear({
      "/auth/login": () =>
        resposta(200, { mfa_required: true, mfa_challenge: "d-1", email: "m@x.com" }),
    });
    const { aplicados, aplicar } = gravador();

    await tocar(() => entrarNaTela("m@x.com", "s"), aplicar);

    expect(aplicados).toEqual([
      C,
      { fase: "entrada", aviso: expect.stringMatching(/duas etapas/) },
    ]);
    await expect(lerCredenciais()).resolves.toBeNull();
    expect(fetchFalso).toHaveBeenCalledTimes(1);
  });

  it.each<[string, Rota, string]>([
    ["401 com detail", () => resposta(401, { detail: "E-mail ou senha incorretos." }), "E-mail ou senha incorretos."],
    ["rede fora", () => Promise.reject(new TypeError("Network request failed")), GENERICO],
    ["200 com forma errada", () => resposta(200, { token: "x" }), GENERICO],
    ["401 com detail em branco", () => resposta(401, { detail: "  " }), GENERICO],
  ])("falha de login (%s) vira texto para a pessoa", async (_nome, login, aviso) => {
    rotear({ "/auth/login": login });
    const { aplicados, aplicar } = gravador();

    await tocar(() => entrarNaTela("a@x.com", "s"), aplicar);

    expect(aplicados).toEqual([C, { fase: "entrada", aviso }]);
  });

  it("servidor instável na renovação é erro com refazer, não logout", async () => {
    await guardarCredenciais(S);
    rotear({
      "/auth/me": () => resposta(401, { detail: "expirado" }),
      "/auth/refresh": () => resposta(503, {}),
    });
    const { aplicados, aplicar } = gravador();

    await montar(aplicar);

    expect(aplicados).toEqual([
      C,
      {
        fase: "erro",
        mensagem: "Não conseguimos falar com o PigBank agora. Tente de novo.",
        refazer: "abrir",
      },
    ]);
    await expect(lerCredenciais()).resolves.toEqual(S);
  });

  it("sessão que o servidor encerrou volta ao formulário com aviso", async () => {
    await guardarCredenciais(S);
    rotear({
      "/auth/me": () => resposta(401, { detail: "expirado" }),
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
    });
    const { aplicados, aplicar } = gravador();

    await montar(aplicar);

    expect(aplicados).toEqual([
      C,
      { fase: "entrada", aviso: "Sua sessão expirou. Entre de novo." },
    ]);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("Sair não depende do /auth/me (conta agendada para exclusão)", async () => {
    // Mede que `sairNaTela` não chama o `/auth/me`: as rotas chamadas são
    // afirmadas abaixo. O botão Sair do estado de erro, em `index.tsx`, é JSX e
    // não tem teste automático: tirá-lo deixa a suíte verde. A verificar no
    // simulador.
    await guardarCredenciais(S);
    rotear({
      "/auth/me": () => resposta(403, { detail: "Conta agendada para exclusão." }),
    });
    const { aplicados, aplicar } = gravador();

    await montar(aplicar);
    await tocar(sairNaTela, aplicar);

    expect(aplicados).toEqual([
      C,
      { fase: "erro", mensagem: "Conta agendada para exclusão.", refazer: "abrir" },
      C,
      E,
    ]);
    expect(chamadas().map((c) => c.caminho)).toEqual(["/auth/me", "/auth/logout"]);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it.each([
    ["só com espaços", { user_id: 1, email: "ze@x.com", display_name: "   " }, "ze"],
    ["e e-mail ausentes", { user_id: 1, email: null, display_name: null }, "por aí"],
  ])("nome de reserva quando display_name %s", async (_nome, corpo, nome) => {
    await guardarCredenciais(S);
    rotear({ "/auth/me": () => resposta(200, corpo) });
    const { aplicados, aplicar } = gravador();

    await montar(aplicar);

    expect(aplicados).toEqual([C, { fase: "pronto", nome }]);
  });

  it("leitura do cofre falhando no Sair não afirma logout nem toca a rede", async () => {
    await guardarCredenciais(S);
    falharLeitura(true);
    rotear();
    const { aplicados, aplicar } = gravador();

    await tocar(sairNaTela, aplicar);

    expect(aplicados).toEqual([
      C,
      {
        fase: "erro",
        mensagem: "Não conseguimos encerrar a sessão neste aparelho. Tente de novo.",
        refazer: "sair",
      },
    ]);
    expect(fetchFalso).not.toHaveBeenCalled();
    falharLeitura(false);
    await expect(lerCredenciais()).resolves.toEqual(S);
  });

  it("leitura do cofre falhando na abertura é erro, não formulário", async () => {
    falharLeitura(true);
    const { aplicados, aplicar } = gravador();

    await montar(aplicar);

    expect(aplicados).toEqual([C, { fase: "erro", mensagem: GENERICO, refazer: "abrir" }]);
  });

  it("Sair que não apagou o cofre não afirma logout, e refazer sai", async () => {
    await guardarCredenciais(S);
    rotear();
    falharApagar(true);
    const { aplicados, aplicar } = gravador();

    await tocar(sairNaTela, aplicar);

    const erro: Estado = {
      fase: "erro",
      mensagem: "Não conseguimos encerrar a sessão neste aparelho. Tente de novo.",
      refazer: "sair",
    };
    expect(aplicados).toEqual([C, erro]);
    // A credencial continua no aparelho — é exatamente por isso que a tela
    // não pode dizer que saiu.
    await expect(lerCredenciais()).resolves.toEqual(S);

    falharApagar(false);
    await tocar(sairNaTela, aplicar);
    expect(aplicados).toEqual([C, erro, C, E]);
    await expect(lerCredenciais()).resolves.toBeNull();
    // Os DOIS Sair mandaram logout: a revogação sai mesmo quando a limpeza
    // local falha, e o segundo Sair revoga de novo um refresh já capturado.
    expect(chamadas().map((c) => c.caminho)).toEqual([
      "/auth/logout",
      "/auth/logout",
    ]);
  });
});
