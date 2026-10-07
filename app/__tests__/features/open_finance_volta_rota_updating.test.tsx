import { redirectSystemPath } from "../../app/+native-intent";
import { origemDaTentativa } from "./open_finance_volta_apoio";
/**
 * A rota `open-finance-volta` com o item em `updating` ("Atualizando…"), pelo
 * roteador de verdade. O `renderRouter` liga o relógio falso: cada
 * `umIntervalo()` é uma espera do laço. A lógica tem os casos dela em
 * `open_finance_volta_updating.test.ts`.
 */
import { router, type Href } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { AccessibilityInfo } from "react-native";

import { JANELA_MS, INTERVALO_MS } from "@/features/openFinance/volta";
import { guardarSessaoOf as guardarCredenciais } from "./open_finance_volta_apoio";

import { prepararCaso, resposta, S, segurar } from "./auth_apoio";
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
  titulo,
  umIntervalo,
} from "./open_finance_volta_rota_apoio";

declare const global: typeof globalThis & { __definirAppState: (v: string) => void };

const INSTAVEL = "A conexão está instável. Seguimos tentando.";
const ORGANIZANDO = "Seu banco foi conectado. Estamos organizando seus dados; eles aparecem sozinhos quando terminar.";
/** Só existe em `conectado/updating` (ou `conferindo` há ≥ 30 s): o marcador de "atualizando". */
const sair = () => screen.getByRole("button", { name: "Sair" });

beforeEach(async () => {
  prepararCaso();
  global.__definirAppState("active");
  await guardarCredenciais(S);
});

describe("open-finance-volta — item em updating", () => {
  it("R1 — GET updating, updating, updated: Sair (sem Continuar), um GET por intervalo, depois 'Atualizado'", async () => {
    servidor(emSequencia(() => atualizando(A), () => atualizando(A), () => lista(A)));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(sair()).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Continuar" })).toBeNull();

    await umIntervalo();
    await waitFor(() => expect(contarGets()).toBe(2));
    expect(sair()).toBeTruthy();

    await umIntervalo();
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    expect([contarGets(), postsDe(A).length]).toEqual([3, 0]);
  });

  it("R2 — sempre updating até a janela fechar: texto do organizando, 'Conferir de novo' e 'Continuar', sem 'Atualizando…'", async () => {
    // Cada GET salta a janela inteira (como o caso 15): a 1ª leitura já fecha o prazo.
    servidor(() => {
      jest.setSystemTime(Date.now() + JANELA_MS);
      return atualizando(A);
    });
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(screen.getByText(ORGANIZANDO)).toBeTruthy());
    expect(screen.getByRole("button", { name: "Conferir de novo" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continuar" })).toBeTruthy();
    expect(screen.queryByText("Atualizando…")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
    titulo(true);
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    expect(postsDe(A)).toEqual([]);
  });

  it("R3 — updating, 503, updated: o aviso de instável nunca aparece e Sair segue na tela depois do 503", async () => {
    servidor(emSequencia(() => atualizando(A), () => resposta(503, {}), () => lista(A)));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(sair()).toBeTruthy());

    await umIntervalo();
    await waitFor(() => expect(contarGets()).toBe(2));
    await act(drenar);
    expect(sair()).toBeTruthy();
    expect(screen.queryByText(INSTAVEL)).toBeNull();

    await umIntervalo();
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(screen.queryByText(INSTAVEL)).toBeNull();
  });

  it("R4 — sempre updating, desmonte por router.back(): nenhum GET novo em 3 intervalos", async () => {
    servidor(() => atualizando(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(sair()).toBeTruthy());
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
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(contarGets()).toBe(1));
    for (let i = 1; i <= porJanela; i++) {
      await umIntervalo();
      await waitFor(() => expect(contarGets()).toBe(i + 1));
    }
    const conferirDeNovo = await screen.findByRole("button", { name: "Conferir de novo" });
    expect(screen.getByText(ORGANIZANDO)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();

    // Sem o toque, nada: a 1ª rodada terminou.
    await umIntervalo();
    expect(contarGets()).toBe(porJanela + 1);

    await act(async () => {
      fireEvent.press(conferirDeNovo);
      await drenar();
    });
    await waitFor(() => expect(contarGets()).toBe(porJanela + 2));
    expect(await screen.findByRole("button", { name: "Sair" })).toBeTruthy();
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
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(contarGets()).toBe(1));
    await umIntervalo();
    // Espera o laço terminar, em qualquer dos dois estados finais, antes de olhar
    // o POST: a 1ª asserção que um POST derruba é a de POST, não uma anterior.
    await waitFor(() => expect(screen.queryByText(ORGANIZANDO) ?? screen.queryByText("Atualizado")).toBeTruthy());
    expect(postsDe(A)).toEqual([]);
    expect(screen.getByText(ORGANIZANDO)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    expect(contarGets()).toBe(2);
  });
});

describe("open-finance-volta — tela de espera (Organizando seus dados)", () => {
  const AVISO = "Você pode sair desta tela: seus dados aparecem sozinhos quando terminar.";
  const DEMORA = "Está demorando mais que o normal. Você pode sair desta tela e conferir depois.";
  const segundos = (ms: number) =>
    act(async () => {
      jest.advanceTimersByTime(ms);
      await drenar();
    });

  it("R8 — updating: barra sem porcentagem, 'há 0 s', aviso e Sair; sem Continuar", async () => {
    servidor(() => atualizando(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(sair()).toBeTruthy());
    const barra = screen.getByRole("progressbar", { name: "Organizando seus dados" });
    expect(barra.props.accessibilityValue?.now).toBeUndefined();
    expect(screen.getByText("há 0 s")).toBeTruthy();
    expect(screen.getByText("Isso pode levar alguns minutos.")).toBeTruthy();
    expect(screen.getByText(AVISO)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continuar" })).toBeNull();
    expect(screen.queryByText(/%/)).toBeNull();
    titulo(true);
  });

  it("R9 — conferindo (GET em voo): barra e contador sem botão antes de 30 s; Sair e o aviso de demora a partir de 30 s", async () => {
    const portao = segurar();
    servidor(async () => {
      await portao.promessa;
      return lista();
    });
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(contarGets()).toBe(1));
    expect(screen.getByText("Estamos conferindo com o banco.")).toBeTruthy();
    expect(screen.getByRole("progressbar")).toBeTruthy();
    titulo(true);

    await segundos(29_000);
    expect(screen.getByText("há 29 s")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Continuar" })).toBeNull();
    expect(screen.queryByText(DEMORA)).toBeNull();

    await segundos(1_000);
    expect(sair()).toBeTruthy();
    expect(screen.getByText(DEMORA)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continuar" })).toBeNull();
  });

  it("R14 — 'Conferir de novo' recomeça os 30 s: sem Sair logo depois do toque, Sair 30 s depois", async () => {
    // 1ª rodada: updating com a janela inteira saltada → organizando. 2ª: o GET nunca volta (conferindo).
    let pendurar = false;
    servidor(() => {
      if (pendurar) return new Promise<never>(() => undefined);
      jest.setSystemTime(Date.now() + JANELA_MS);
      return atualizando(A);
    });
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    const conferirDeNovo = await screen.findByRole("button", { name: "Conferir de novo" });
    pendurar = true;
    await act(async () => {
      fireEvent.press(conferirDeNovo);
      await drenar();
    });
    expect(screen.getByText("Estamos conferindo com o banco.")).toBeTruthy();
    await segundos(29_000);
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    await segundos(1_000);
    expect(sair()).toBeTruthy();
    expect(screen.getByText(DEMORA)).toBeTruthy();
  });

  it("R10 — tocar em Sair volta ao Início e o laço para: nenhum GET em 3 intervalos", async () => {
    servidor(() => atualizando(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(sair()).toBeTruthy());
    await act(async () => {
      fireEvent.press(sair());
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/"));
    const antes = contarGets();
    for (let i = 0; i < 3; i++) await umIntervalo();
    expect(contarGets()).toBe(antes);
  });

  it("R11 — o contador é relógio de parede: +90 s sem disparar timers, um tick depois mostra 'há 1 min 31 s'", async () => {
    servidor(() => atualizando(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(sair()).toBeTruthy());
    jest.setSystemTime(Date.now() + 90_000);
    await segundos(1_000);
    expect(screen.getByText("há 1 min 31 s").props.accessibilityLabel).toBe("há 1 minuto e 31 segundos");
  });

  it("R12 — updating → updated: o VoiceOver anuncia 'Atualizado' uma vez; barra, contador e Sair somem; Continuar aparece", async () => {
    const anunciar = jest.mocked(AccessibilityInfo.announceForAccessibility);
    anunciar.mockClear();
    servidor(emSequencia(() => atualizando(A), () => lista(A)));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(sair()).toBeTruthy());
    expect(anunciar).not.toHaveBeenCalled();

    await umIntervalo();
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    await umIntervalo();
    expect(anunciar.mock.calls).toEqual([["Atualizado"]]);
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/^há /)).toBeNull();
    expect(screen.queryByRole("button", { name: "Sair" })).toBeNull();
    expect(screen.getByRole("button", { name: "Continuar" })).toBeTruthy();
  });

  it("R13 — o mesmo itemId navegado de novo durante updating: segue um GET por intervalo (sem 2º laço)", async () => {
    servidor(() => atualizando(A));
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(sair()).toBeTruthy());
    await act(async () => {
      router.navigate(redirectSystemPath({ path: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}`, initial: false }) as Href);
      await drenar();
    });
    const antes = contarGets();
    for (let i = 1; i <= 3; i++) {
      await umIntervalo();
      await waitFor(() => expect(contarGets()).toBe(antes + i));
    }
    await act(drenar);
    expect(contarGets()).toBe(antes + 3);
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
    renderRouter("./app", { initialUrl: `/open-finance-volta/${origemDaTentativa()}?itemId=${A}` });
    await waitFor(() => expect(prompts()).toBe(1));
    await liberar();
    await waitFor(() => expect(sair()).toBeTruthy());
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
