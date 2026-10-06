import { origemDaTentativa } from "./open_finance_volta_apoio";
/**
 * A rota `open-finance-volta` pelo roteador de verdade (`renderRouter("./app")`):
 * a guarda de sessão, a trava, o desmonte e a pilha. A lógica do laço tem os
 * casos dela em `open_finance_volta.test.ts`; aqui, o que só a rota decide. O
 * item em `updating` tem os casos dele em `open_finance_volta_rota_updating.test.tsx`.
 */
import * as Linking from "expo-linking";
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { definirWidgetAberto, INTERVALO_MS, JANELA_MS } from "@/features/openFinance/volta";
import { iniciarTentativaBancaria, lerTentativaBancaria } from "@/storage/secure";
import { SESSAO_OF, guardarSessaoOf as guardarCredenciais } from "./open_finance_volta_apoio";

import { redirectSystemPath } from "../../app/+native-intent";
import { chamadas, cofre, prepararCaso, rotear, S, segurar } from "./auth_apoio";
import {
  A,
  appVai,
  B,
  deOpenFinance,
  desligarTrava,
  drenar,
  ligarTrava,
  liberar,
  lista,
  pendentes,
  postsDe,
  prompts,
  servidor,
  titulo,
} from "./open_finance_volta_rota_apoio";

declare const global: typeof globalThis & { __definirAppState: (v: string) => void };

beforeEach(() => {
  prepararCaso();
  pendentes.length = 0;
  global.__definirAppState("active");
});

describe("open-finance-volta — guarda de sessão", () => {
  it("1 — sem sessão: cai em /boas-vindas, ZERO pedidos", async () => {
    servidor();
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    await act(drenar);
    expect(chamadas()).toEqual([]);
  });
});

describe("open-finance-volta — com sessão, trava desligada", () => {
  beforeEach(() => guardarCredenciais(S));

  it("21 — user_id no link é ignorado: tudo vai para o uid do perfil; conecta", async () => {
    servidor();
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}&user_id=999` });
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(deOpenFinance().map((c) => c.caminho)).toEqual(["/open-finance/1", "/open-finance/1/pluggy-item"]);
    expect(chamadas().some((c) => c.caminho.includes("999"))).toBe(false);
  });

  it("pilha — link frio, Continuar volta ao Início", async () => {
    servidor(() => lista(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    titulo(false);
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Continuar" }));
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/"));
    expect(screen.getByText("Olá, S")).toBeTruthy();
  });

  it("4 — link sem itemId e sem tentativa: texto neutro, nenhum pedido de OF", async () => {
    cofre.delete("pb.of.tentativa");
    servidor();
    renderRouter("./app", { initialUrl: "/open-finance-volta" });
    await waitFor(() => expect(screen.getByText("Se você conectou um banco, ele aparece em instantes.")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    titulo(false);
    expect(deOpenFinance()).toEqual([]);
  });

  // O expo-router entrega `itemId` repetido como ARRAY e decodifica `%0A`/`%20`.
  it.each([
    ["repetido (array)", `itemId=${A}&itemId=${B}`],
    ["com quebra de linha", "itemId=abc%0A"],
    ["com espaço", "itemId=abc%20def"],
  ])("4b — itemId %s: texto neutro, nenhum pedido de OF", async (_nome, query) => {
    servidor();
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?${query}` });
    await waitFor(() => expect(screen.getByText("Se você conectou um banco, ele aparece em instantes.")).toBeTruthy());
    await act(drenar);
    expect(deOpenFinance()).toEqual([]);
  });

  it("18 — desmonte com o GET em voo: a resposta é ignorada, nenhum POST", async () => {
    const portao = segurar();
    servidor(async () => {
      await portao.promessa;
      return lista();
    });
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(deOpenFinance()).toHaveLength(1));

    await act(async () => {
      router.back();
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/"));
    await act(async () => {
      portao.soltar();
      await drenar();
    });

    expect(deOpenFinance()).toHaveLength(1);
    expect(postsDe(A)).toEqual([]);
  });

  it("15 — janela esgotada: o toque em 'Conferir de novo' recomeça o laço (um GET e um POST novos); sem o toque, nada novo", async () => {
    // O `renderRouter` liga o relógio falso (como no 3/17). Cada GET salta a janela
    // inteira: a rodada é UMA tentativa (GET + POST; o item nunca aparece) e cai em
    // "ainda-conferindo".
    rotear({
      "/open-finance/1": () => {
        jest.setSystemTime(Date.now() + JANELA_MS);
        return lista();
      },
      "/open-finance/1/pluggy-item": () => lista(),
    });
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    const gets = () => deOpenFinance().filter((c) => c.caminho === "/open-finance/1").length;
    const conferirDeNovo = await screen.findByRole("button", { name: "Conferir de novo" });
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    titulo(false);
    expect([gets(), postsDe(A).length]).toEqual([1, 1]);

    // Sem o toque, nada: nem a espera de `INTERVALO_MS` traz outra tentativa.
    await act(async () => {
      jest.advanceTimersByTime(INTERVALO_MS);
      await drenar();
    });
    expect([gets(), postsDe(A).length]).toEqual([1, 1]);

    // O toque recomeça o laço: um GET e um POST novos (sem o toque, nada novo,
    // como medido acima). Como cada GET salta o relógio inteiro, este caso NÃO
    // mede se a 2ª rodada tem janela e orçamento de POSTs próprios: isso é
    // provado no caso 15 de `open_finance_volta.test.ts`, que compartilha um
    // relógio só entre as duas chamadas de `conferirVolta`.
    await act(async () => {
      fireEvent.press(conferirDeNovo);
      await drenar();
    });
    await waitFor(() => expect([gets(), postsDe(A).length]).toEqual([2, 2]));
    expect(await screen.findByRole("button", { name: "Conferir de novo" })).toBeTruthy();
  });

  it("19 — 2ª abertura com OUTRO itemId: o laço do 1º para, nenhum POST dele depois", async () => {
    const portao = segurar();
    let primeiro = true;
    servidor(async () => {
      if (primeiro) {
        primeiro = false;
        await portao.promessa;
      }
      return lista(...chamadas().filter((c) => c.caminho.endsWith("/pluggy-item")).map((c) => (c.corpo as { item: { id: string } }).item.id));
    });
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(deOpenFinance()).toHaveLength(1));

    await act(async () => {
      await iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, (await lerTentativaBancaria(1)) ?? undefined);
      router.navigate(redirectSystemPath({ path: `/open-finance-volta/${origemDaTentativa()}?itemId=${B}`, initial: false })!);
      await drenar();
    });
    await waitFor(() => expect(postsDe(B)).toHaveLength(1));
    await act(async () => {
      portao.soltar();
      await drenar();
    });

    expect(postsDe(A)).toEqual([]);
  });
});

describe("open-finance-volta — link com o app aberto e o widget da Pluggy em foco (+native-intent)", () => {
  const LINK = `pigbank://open-finance-volta?itemId=${A}`;
  beforeEach(() => guardarCredenciais(S));
  afterEach(() => definirWidgetAberto(false));

  /** O app no Início; devolve o ouvinte de `url` que o roteador registrou no `expo-linking`. */
  async function noInicio() {
    const ouvir = jest.spyOn(Linking, "addEventListener");
    servidor();
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen.getByText("Olá, S")).toBeTruthy());
    const chamada = ouvir.mock.calls.find(([evento]) => evento === "url");
    ouvir.mockRestore();
    if (!chamada) throw new Error("o roteador não registrou ouvinte de url");
    return async (url: string) =>
      act(async () => {
        await (chamada[1] as (e: { url: string }) => unknown)({ url });
        await drenar();
      });
  }

  it("L1 — widget aberto: o link é descartado, a tela segue no Início e nenhum pedido de OF sai", async () => {
    const abrir = await noInicio();
    definirWidgetAberto(true);
    await abrir(LINK);
    await act(drenar);
    expect(screen).toHavePathname("/");
    expect(deOpenFinance()).toEqual([]);
  });

  it("L2 — widget fechado: o mesmo link abre a rota e ela confere (controle positivo)", async () => {
    const abrir = await noInicio();
    await abrir(LINK);
    await waitFor(() => expect(screen).toHavePathname("/open-finance-volta"));
    await waitFor(() => expect(deOpenFinance().filter((c) => c.caminho === "/open-finance/1")).toHaveLength(1));
  });

  it("L3 — abertura fria normaliza retorno legado e preserva outras rotas mesmo com widget aberto", () => {
    definirWidgetAberto(true);
    expect(redirectSystemPath({ path: LINK, initial: true })).toBe(`/open-finance-volta?itemId=${A}`);
    expect(redirectSystemPath({ path: "pigbank://seguranca", initial: false })).toBe("pigbank://seguranca");
    expect(redirectSystemPath({ path: "/seguranca", initial: false })).toBe("/seguranca");
  });

  it.each([`pigbank-staging://open-finance-volta?itemId=${A}`, `pigbank-dev://open-finance-volta?itemId=${A}`, `exp://192.168.0.10:8081/--/open-finance-volta?itemId=${A}`, `/open-finance-volta/${origemDaTentativa()}?itemId=${A}`])(
    "L4 — widget aberto: %s também é descartado",
    (path) => {
      definirWidgetAberto(true);
      expect(redirectSystemPath({ path, initial: false })).toBeNull();
    },
  );

  it.each([
    "/seguranca",
    "/seguranca?next=open-finance-volta",
    "/open-finance-volta-outra-coisa",
    "/x/open-finance-volta",
    "pigbank://seguranca?x=open-finance-volta",
    "pigbank://open-finance-volta-x",
    "pigbank://x/open-finance-volta",
  ])("L5 — widget aberto: %s NÃO é a rota da volta e passa intacto", (path) => {
    definirWidgetAberto(true);
    expect(redirectSystemPath({ path, initial: false })).toBe(path);
  });
});

describe("open-finance-volta — trava ligada", () => {
  beforeEach(ligarTrava);
  afterEach(desligarTrava);

  it("2 — link frio com sessão salva: a trava no lugar da pilha, ZERO pedidos até liberar", async () => {
    await guardarCredenciais(S);
    servidor(() => lista(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(prompts()).toBe(1));
    expect(chamadas()).toEqual([]);

    await liberar();
    // Medido: depois de liberar, o roteador ABRE a rota do link (não o `/`),
    // e ela confere. Se um dia cair em `/`, o item fica para o webhook (ver o
    // `ponytail:` da rota) — este teste é quem avisa.
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(screen).toHavePathname("/open-finance-volta");
  });

  it("2b — a trava sobe e libera 3 vezes durante conferindo: os 30 s contam da abertura, não do último desbloqueio", async () => {
    await guardarCredenciais(S);
    servidor(() => new Promise<never>(() => undefined));
    const passar = (ms: number) =>
      act(async () => {
        jest.advanceTimersByTime(ms);
        await drenar();
      });
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(prompts()).toBe(1));
    await liberar();
    await passar(10_000);
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    for (let n = 2; n <= 4; n++) {
      await appVai("inactive");
      await appVai("background");
      jest.setSystemTime(Date.now() + 61_000);
      await appVai("active");
      await waitFor(() => expect(prompts()).toBe(n));
      await liberar();
      await passar(10_000);
    }
    // Cada desbloqueio deu só 10 s de `conferindo`; a abertura foi há mais de 3 min.
    expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy();
    expect(screen.getByText("Está demorando mais que o normal. Você pode sair desta tela e conferir depois.")).toBeTruthy();
  });

  it("3/17 — trava por cima (já liberou antes): nenhum pedido até liberar; depois confere", async () => {
    await guardarCredenciais(S);
    servidor();
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(prompts()).toBe(1));
    await liberar();
    await waitFor(() => expect(screen.getByText("Olá, S")).toBeTruthy());

    await appVai("inactive");
    await appVai("background");
    jest.setSystemTime(Date.now() + 61_000);
    await appVai("active");
    await waitFor(() => expect(prompts()).toBe(2));

    await act(async () => {
      router.navigate(redirectSystemPath({ path: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}`, initial: false })!);
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/open-finance-volta"));
    expect(deOpenFinance()).toEqual([]);

    await liberar();
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(postsDe(A)).toHaveLength(1);
  });
});
