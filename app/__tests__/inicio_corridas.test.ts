/**
 * A tela de entrada com uma ação em andamento. Duas regras: TOQUE com ação em
 * voo é ignorado, e a MONTAGEM da rota espera na fila.
 *
 * Todo caso segura a primeira ação por um portão e só dispara a segunda depois
 * do sinal de que a rota foi chamada. Soltar o portão antes disso não segura
 * nada, porque a ação só começa numa microtarefa.
 *
 * Na remontagem a tela velha e a nova têm `aplicar` diferentes, e cada uma tem
 * o seu gravador. Só o da nova é afirmado: o `setState` de uma tela desmontada
 * não tem efeito.
 */
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";
import { entrarNaTela, montar, sairNaTela, tocar, type Estado } from "@/ui/inicio";

import {
  C,
  E,
  S,
  chamadas,
  credencialDe,
  gravador,
  prepararCaso,
  respirar,
  resposta,
  rotear,
  segurar,
  type Rota,
} from "./inicio_apoio";

type Portao = ReturnType<typeof segurar>;

/**
 * Login de `a@x.com` avisa a chegada e só responde quando o portão abre;
 * qualquer outra conta toma 429 na hora.
 */
const loginPresoA =
  (chegou: Portao, portao: Portao): Rota =>
  async (o) => {
    const { email } = JSON.parse(String(o.body)) as { email: string };
    if (email !== "a@x.com") {
      return resposta(429, {
        detail: "Muitas tentativas. Aguarde alguns minutos e tente novamente.",
      });
    }
    chegou.soltar();
    await portao.promessa;
    return resposta(200, credencialDe(email));
  };

beforeEach(prepararCaso);

describe("tela de entrada com ação em andamento", () => {
  it.each<[string, () => Promise<Estado>]>([
    ["Entrar com outra conta, que daria 429", () => entrarNaTela("b@x.com", "s")],
    ["Sair", sairNaTela],
  ])("toque com uma entrada em voo é ignorado: %s", async (_nome, segunda) => {
    const chegou = segurar();
    const portao = segurar();
    rotear({ "/auth/login": loginPresoA(chegou, portao) });
    const { aplicados, aplicar } = gravador();

    const primeira = tocar(() => entrarNaTela("a@x.com", "s"), aplicar);
    await chegou.promessa;
    const ignorada = tocar(segunda, aplicar);
    portao.soltar();
    await Promise.all([primeira, ignorada]);

    expect(aplicados).toEqual([C, { fase: "pronto", nome: "A" }]);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-a", refresh: "rt_a" });
    // A segunda nunca chegou à rede.
    expect(chamadas().map((c) => c.caminho)).toEqual(["/auth/login", "/auth/me"]);
  });

  it("a tela nova, montada com um Sair em voo, espera o Sair", async () => {
    // A rota remonta com o Sair em voo: a tela nova monta, e o abrir dela espera o Sair terminar.
    await guardarCredenciais(S);
    const chegou = segurar();
    const portao = segurar();
    rotear({
      "/auth/logout": async () => {
        chegou.soltar();
        await portao.promessa;
        return resposta(200, {});
      },
    });
    const velho = gravador();
    const novo = gravador();

    const saida = tocar(sairNaTela, velho.aplicar);
    await chegou.promessa;
    const montagem = montar(novo.aplicar);
    await respirar();
    portao.soltar();
    await Promise.all([saida, montagem]);

    expect(novo.aplicados).toEqual([C, E]);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("a tela nova, montada com um login em voo, mostra a conta que entrou", async () => {
    const chegou = segurar();
    const portao = segurar();
    rotear({ "/auth/login": loginPresoA(chegou, portao) });
    const velho = gravador();
    const novo = gravador();

    const entrada = tocar(() => entrarNaTela("a@x.com", "s"), velho.aplicar);
    await chegou.promessa;
    const montagem = montar(novo.aplicar);
    await respirar();
    portao.soltar();
    await Promise.all([entrada, montagem]);

    expect(novo.aplicados).toEqual([C, { fase: "pronto", nome: "A" }]);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-a", refresh: "rt_a" });
  });
});
