/**
 * A rota `open-finance-volta` pelo roteador de verdade (`renderRouter("./app")`):
 * a guarda de sessão, a trava, o desmonte e a pilha. A lógica do laço tem os
 * casos dela em `open_finance_volta.test.ts`; aqui, o que só a rota decide.
 *
 * Sem `setTimeout(0)` dentro de `act` (ver `layout.test.tsx`): microtarefas à mão.
 */
import * as LA from "expo-local-authentication";
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { INTERVALO_MS, JANELA_MS } from "@/features/openFinance/volta";
import { guardarCredenciais } from "@/storage/secure";

import { chamadas, prepararCaso, resposta, rotear, S, segurar, type Rota } from "./auth_apoio";

declare const global: typeof globalThis & { __dispararAppState: (v: string) => void; __definirAppState: (v: string) => void };

const A = "item_a";
const B = "item_b";
const conexao = (id: string) => ({ provider_item_id: id, institution_name: "Nubank", ui: { state: "updated", label: "Atualizado", detail: null } });
const lista = (...ids: string[]) => resposta(200, { ok: true, connections: ids.map(conexao) });

const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

const deOpenFinance = () => chamadas().filter((c) => c.caminho.startsWith("/open-finance"));
const postsDe = (id: string) =>
  chamadas().filter((c) => c.caminho.endsWith("/pluggy-item") && (c.corpo as { item: { id: string } }).item.id === id);

/** O servidor de OF: o GET devolve o que já foi registrado; o POST registra. */
function servidor(get?: Rota) {
  const registrados: string[] = [];
  rotear({
    "/open-finance/1": get ?? (() => lista(...registrados)),
    "/open-finance/1/pluggy-item": (o) => {
      registrados.push((JSON.parse(String(o.body)) as { item: { id: string } }).item.id);
      return lista(...registrados);
    },
  });
}

let pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
async function liberar() {
  await act(async () => {
    pendentes.shift()!({ success: true });
    await drenar();
  });
}
const prompts = () => jest.mocked(LA.authenticateAsync).mock.calls.length;

async function appVai(valor: "active" | "inactive" | "background") {
  await act(async () => {
    global.__dispararAppState(valor);
    await drenar();
  });
}

beforeEach(() => {
  prepararCaso();
  pendentes = [];
  global.__definirAppState("active");
});

describe("open-finance-volta — guarda de sessão", () => {
  it("1 — sem sessão: cai em /boas-vindas, ZERO pedidos", async () => {
    servidor();
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    await act(drenar);
    expect(chamadas()).toEqual([]);
  });
});

describe("open-finance-volta — com sessão, trava desligada", () => {
  beforeEach(() => guardarCredenciais(S));

  it("21 — user_id no link é ignorado: tudo vai para o uid do perfil; conecta", async () => {
    servidor();
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}&user_id=999` });
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(deOpenFinance().map((c) => c.caminho)).toEqual(["/open-finance/1", "/open-finance/1/pluggy-item"]);
    expect(chamadas().some((c) => c.caminho.includes("999"))).toBe(false);
  });

  it("pilha — link frio, Continuar volta ao Início", async () => {
    servidor(() => lista(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Continuar" }));
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/"));
    expect(screen.getByText("Olá, S")).toBeTruthy();
  });

  it("4 — link sem itemId: texto neutro, nenhum pedido de OF", async () => {
    servidor();
    renderRouter("./app", { initialUrl: "/open-finance-volta" });
    await waitFor(() => expect(screen.getByText("Se você conectou um banco, ele aparece em instantes.")).toBeTruthy());
    expect(deOpenFinance()).toEqual([]);
  });

  // O expo-router entrega `itemId` repetido como ARRAY e decodifica `%0A`/`%20`.
  it.each([
    ["repetido (array)", `itemId=${A}&itemId=${B}`],
    ["com quebra de linha", "itemId=abc%0A"],
    ["com espaço", "itemId=abc%20def"],
  ])("4b — itemId %s: texto neutro, nenhum pedido de OF", async (_nome, query) => {
    servidor();
    renderRouter("./app", { initialUrl: `/open-finance-volta?${query}` });
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
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
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
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    const gets = () => deOpenFinance().filter((c) => c.caminho === "/open-finance/1").length;
    const conferirDeNovo = await screen.findByRole("button", { name: "Conferir de novo" });
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
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(deOpenFinance()).toHaveLength(1));

    await act(async () => {
      router.navigate(`/open-finance-volta?itemId=${B}`);
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

describe("open-finance-volta — trava ligada", () => {
  beforeEach(() => {
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
    jest.mocked(LA.authenticateAsync).mockClear().mockImplementation(() => new Promise((r) => pendentes.push(r)));
  });

  afterEach(() => {
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
    jest.mocked(LA.authenticateAsync).mockResolvedValue({ success: true });
  });

  it("2 — link frio com sessão salva: a trava no lugar da pilha, ZERO pedidos até liberar", async () => {
    await guardarCredenciais(S);
    servidor(() => lista(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(prompts()).toBe(1));
    expect(chamadas()).toEqual([]);

    await liberar();
    // Medido: depois de liberar, o roteador ABRE a rota do link (não o `/`),
    // e ela confere. Se um dia cair em `/`, o item fica para o webhook (ver o
    // `ponytail:` da rota) — este teste é quem avisa.
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(screen).toHavePathname("/open-finance-volta");
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
      router.navigate(`/open-finance-volta?itemId=${A}`);
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/open-finance-volta"));
    expect(deOpenFinance()).toEqual([]);

    await liberar();
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(postsDe(A)).toHaveLength(1);
  });
});
