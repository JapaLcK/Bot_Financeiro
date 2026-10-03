/**
 * A rota `open-finance-volta` com o item em `updating` ("Atualizando…"), pelo
 * roteador de verdade. O `renderRouter` liga o relógio falso: cada
 * `umIntervalo()` é uma espera do laço. A lógica tem os casos dela em
 * `open_finance_volta_updating.test.ts`.
 */
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { JANELA_MS, INTERVALO_MS } from "@/features/openFinance/volta";
import { guardarCredenciais } from "@/storage/secure";

import { prepararCaso, resposta, S } from "./auth_apoio";
import { emSequencia } from "./open_finance_volta_apoio";
import {
  A,
  appVai,
  atualizando,
  contarGets,
  desligarTrava,
  drenar,
  ligarTrava,
  liberar,
  lista,
  pendentes,
  postsDe,
  prompts,
  servidor,
  umIntervalo,
} from "./open_finance_volta_rota_apoio";

declare const global: typeof globalThis & { __definirAppState: (v: string) => void };

const INSTAVEL = "A conexão está instável. Seguimos tentando.";
const ORGANIZANDO = "Seu banco foi conectado. Estamos organizando seus dados; eles aparecem sozinhos quando terminar.";

beforeEach(async () => {
  prepararCaso();
  global.__definirAppState("active");
  await guardarCredenciais(S);
});

describe("open-finance-volta — item em updating", () => {
  it("R1 — GET updating, updating, updated: 'Atualizando…' com Continuar, um GET por intervalo, depois 'Atualizado'", async () => {
    servidor(emSequencia(() => atualizando(A), () => atualizando(A), () => lista(A)));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(screen.getByText("Atualizando…")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Continuar" })).toBeTruthy();

    await umIntervalo();
    await waitFor(() => expect(contarGets()).toBe(2));
    expect(screen.getByText("Atualizando…")).toBeTruthy();

    await umIntervalo();
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(screen.queryByText("Atualizando…")).toBeNull();
    expect([contarGets(), postsDe(A).length]).toEqual([3, 0]);
  });

  it("R2 — sempre updating até a janela fechar: texto do organizando, 'Conferir de novo' e 'Continuar', sem 'Atualizando…'", async () => {
    // Cada GET salta a janela inteira (como o caso 15): a 1ª leitura já fecha o prazo.
    servidor(() => {
      jest.setSystemTime(Date.now() + JANELA_MS);
      return atualizando(A);
    });
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(screen.getByText(ORGANIZANDO)).toBeTruthy());
    expect(screen.getByRole("button", { name: "Conferir de novo" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continuar" })).toBeTruthy();
    expect(screen.queryByText("Atualizando…")).toBeNull();
    expect(postsDe(A)).toEqual([]);
  });

  it("R3 — updating, 503, updated: o aviso de instável nunca aparece e 'Atualizando…' segue na tela depois do 503", async () => {
    servidor(emSequencia(() => atualizando(A), () => resposta(503, {}), () => lista(A)));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(screen.getByText("Atualizando…")).toBeTruthy());

    await umIntervalo();
    await waitFor(() => expect(contarGets()).toBe(2));
    await act(drenar);
    expect(screen.getByText("Atualizando…")).toBeTruthy();
    expect(screen.queryByText(INSTAVEL)).toBeNull();

    await umIntervalo();
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(screen.queryByText(INSTAVEL)).toBeNull();
  });

  it("R4 — sempre updating, desmonte por router.back(): nenhum GET novo em 3 intervalos", async () => {
    servidor(() => atualizando(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(screen.getByText("Atualizando…")).toBeTruthy());
    await act(async () => {
      router.back();
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/"));
    const antes = contarGets();
    for (let i = 0; i < 3; i++) await umIntervalo();
    expect(contarGets()).toBe(antes);
  });

  it("R5 (A14) — 'Conferir de novo' no organizando recomeça com janela nova: volta a consultar", async () => {
    // Sem salto de relógio: a 1ª rodada gasta a janela inteira em intervalos.
    const porJanela = JANELA_MS / INTERVALO_MS;
    servidor(() => atualizando(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(contarGets()).toBe(1));
    for (let i = 1; i <= porJanela; i++) {
      await umIntervalo();
      await waitFor(() => expect(contarGets()).toBe(i + 1));
    }
    const conferirDeNovo = await screen.findByRole("button", { name: "Conferir de novo" });
    expect(screen.getByText(ORGANIZANDO)).toBeTruthy();

    // Sem o toque, nada: a 1ª rodada terminou.
    await umIntervalo();
    expect(contarGets()).toBe(porJanela + 1);

    await act(async () => {
      fireEvent.press(conferirDeNovo);
      await drenar();
    });
    await waitFor(() => expect(contarGets()).toBe(porJanela + 2));
    expect(await screen.findByText("Atualizando…")).toBeTruthy();
    // Janela nova: com o prazo da 1ª rodada (já vencido) o laço pararia depois
    // deste GET; com janela nova ele espera e consulta de novo.
    await umIntervalo();
    await waitFor(() => expect(contarGets()).toBe(porJanela + 3));
    expect(screen.queryByText(ORGANIZANDO)).toBeNull();
  });

  it("R6 (A6) — visto em updating, depois o item SOME da lista: nenhum POST e a tela termina em organizando", async () => {
    // A 2ª leitura vem sem o item e salta a janela: o código certo não faz POST
    // e fecha em organizando. Um POST aqui registraria o item de novo (o
    // `servidor` devolve "Atualizado"): a tela pararia em outro estado.
    servidor(
      emSequencia(
        () => atualizando(A),
        () => {
          jest.setSystemTime(Date.now() + JANELA_MS);
          return lista();
        },
      ),
    );
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(contarGets()).toBe(1));
    await umIntervalo();
    // Espera o laço terminar, em qualquer dos dois estados finais, antes de olhar
    // o POST: a 1ª asserção que um POST derruba é a de POST, não uma anterior.
    await waitFor(() => expect(screen.queryByText(ORGANIZANDO) ?? screen.queryByText("Atualizado")).toBeTruthy());
    expect(postsDe(A)).toEqual([]);
    expect(screen.getByText(ORGANIZANDO)).toBeTruthy();
    expect(contarGets()).toBe(2);
  });
});

describe("open-finance-volta — item em updating, trava ligada", () => {
  beforeEach(() => {
    pendentes.length = 0;
    ligarTrava();
  });
  afterEach(desligarTrava);

  it("R7 — a trava sobe no meio do repoll: nenhum GET enquanto travado; ao liberar, recomeça (GET novo, sem POST) e termina em updated", async () => {
    servidor(emSequencia(() => atualizando(A), () => atualizando(A), () => atualizando(A), () => lista(A)));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(prompts()).toBe(1));
    await liberar();
    await waitFor(() => expect(screen.getByText("Atualizando…")).toBeTruthy());
    await umIntervalo();
    await waitFor(() => expect(contarGets()).toBe(2));

    // Como o 3/17: ao fundo por mais de 60 s, a trava sobe na volta.
    await appVai("inactive");
    await appVai("background");
    jest.setSystemTime(Date.now() + 61_000);
    await appVai("active");
    await waitFor(() => expect(prompts()).toBe(2));
    for (let i = 0; i < 3; i++) await umIntervalo();
    expect(contarGets()).toBe(2);

    await liberar();
    await waitFor(() => expect(contarGets()).toBe(3));
    await umIntervalo();
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect([contarGets(), postsDe(A).length]).toEqual([4, 0]);
  });
});
