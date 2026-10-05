/**
 * A rota `open-finance-volta`: o "Sair" da demora amarrado ao `itemId` (outro
 * item recomeça os 30 s e o contador) e o estado `erro` (só "Continuar"). Pelo
 * roteador de verdade, com o relógio falso do `renderRouter`.
 */
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { JANELA_MS } from "@/features/openFinance/volta";
import { guardarSessaoOf as guardarCredenciais } from "./open_finance_volta_apoio";

import { prepararCaso, resposta, S } from "./auth_apoio";
import {
  A,
  appVai,
  atualizando,
  B,
  contarGets,
  desligarTrava,
  drenar,
  liberar,
  ligarTrava,
  pendentes,
  prompts,
  servidor,
  titulo,
  umIntervalo,
} from "./open_finance_volta_rota_apoio";

declare const global: typeof globalThis & { __definirAppState: (v: string) => void };

const DEMORA = "Está demorando mais que o normal. Você pode sair desta tela e conferir depois.";
const semSair = () => expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
const segundos = (ms: number) =>
  act(async () => {
    jest.advanceTimersByTime(ms);
    await drenar();
  });

beforeEach(async () => {
  prepararCaso();
  global.__definirAppState("active");
  await guardarCredenciais(S);
});

describe("open-finance-volta — demora e erro", () => {
  it("D1 — OUTRO itemId depois da demora: Sair, aviso e contador recomeçam; Sair só volta 30 s depois da 2ª abertura", async () => {
    servidor(() => new Promise<never>(() => undefined));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(contarGets()).toBe(1));
    await segundos(31_000);
    expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy();
    expect(screen.getByText(DEMORA)).toBeTruthy();

    await act(async () => {
      router.navigate(`/open-finance-volta?itemId=${B}`);
      await drenar();
    });
    await waitFor(() => expect(contarGets()).toBe(2));
    semSair();
    expect(screen.queryByText(DEMORA)).toBeNull();
    expect(screen.getByText("há 0 s")).toBeTruthy();

    await segundos(29_000);
    semSair();
    await segundos(1_000);
    expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy();
    expect(screen.getByText(DEMORA)).toBeTruthy();
  });

  it("D2 — erro antes de ver o item (403): Banner e Continuar, sem Sair nem barra, e o laço para", async () => {
    servidor(() => resposta(403, { detail: "Este item não pertence a esta conta." }));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(screen.getByText("Este item não pertence a esta conta.")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Continuar" })).toBeTruthy();
    semSair();
    expect(screen.queryByRole("progressbar")).toBeNull();
    titulo(false);
    const antes = contarGets();
    for (let i = 0; i < 3; i++) await umIntervalo();
    expect(contarGets()).toBe(antes);
  });

  it("D3 — 'Conferir de novo' (mesmo item): o contador NÃO zera (espera total), mas os 30 s do Sair recomeçam", async () => {
    // 1ª rodada: updating com a janela inteira saltada → organizando. 2ª: o GET nunca volta (conferindo).
    let pendurar = false;
    servidor(() => {
      if (pendurar) return new Promise<never>(() => undefined);
      jest.setSystemTime(Date.now() + JANELA_MS);
      return atualizando(A);
    });
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    const conferirDeNovo = await screen.findByRole("button", { name: "Conferir de novo" });
    pendurar = true;
    await act(async () => {
      fireEvent.press(conferirDeNovo);
      await drenar();
    });
    expect(screen.getByText(/^há 5 min \d+ s$/)).toBeTruthy();
    expect(screen.queryByText("há 0 s")).toBeNull();
    await segundos(29_000);
    semSair();
    expect(screen.queryByText(DEMORA)).toBeNull();
    await segundos(1_000);
    expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy();
    expect(screen.getByText(DEMORA)).toBeTruthy();
  });
});

describe("open-finance-volta — demora com a trava ligada", () => {
  beforeEach(() => {
    pendentes.length = 0;
    ligarTrava();
  });
  afterEach(desligarTrava);

  it("D4 — esperando-trava por mais de 5 min: nem Sair nem aviso de demora; ao liberar (GET sem resolver), aparecem", async () => {
    servidor(() => new Promise<never>(() => undefined));
    renderRouter("./app", { initialUrl: `/open-finance-volta?itemId=${A}` });
    await waitFor(() => expect(prompts()).toBe(1));
    await liberar();
    await waitFor(() => expect(contarGets()).toBe(1));
    semSair();

    // A trava sobe (fundo > 60 s) e fica: a rota segue montada sob a tampa, em `esperando-trava`.
    await appVai("inactive");
    await appVai("background");
    jest.setSystemTime(Date.now() + 61_000);
    await appVai("active");
    await waitFor(() => expect(prompts()).toBe(2));
    const oculto = { includeHiddenElements: true };
    titulo(true, oculto);
    // A tampa tem o "Sair" dela (sair da conta): conta só o que a rota acrescentaria.
    const saires = () => screen.queryAllByRole("button", { name: "Sair", ...oculto }).length;
    const daTampa = saires();
    for (const ms of [31_000, JANELA_MS]) {
      await segundos(ms);
      expect(saires()).toBe(daTampa);
      expect(screen.queryByText(DEMORA, oculto)).toBeNull();
      expect(screen.getByText(/^há \d+ min \d+ s$/, oculto)).toBeTruthy();
    }

    await liberar();
    await waitFor(() => expect(contarGets()).toBe(2));
    await segundos(1_000);
    expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy();
    expect(screen.getByText(DEMORA)).toBeTruthy();
  });
});
