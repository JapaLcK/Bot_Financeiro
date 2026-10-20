/**
 * "Descartar" na tela `open-finance-volta`, pela rota de verdade. Só na fase
 * `conferindo`, em um toque, apagando o marcador DESTA rodada (sem tocar o
 * servidor). O relógio é falso (o `renderRouter` o liga): `umIntervalo` é uma
 * espera do laço. Modelo: R10 de `open_finance_volta_rota_updating.test.tsx` e
 * `painel_conexao_pendente_descartar.test.tsx`.
 */
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import * as SecureStore from "expo-secure-store";
import { AccessibilityInfo } from "react-native";

import { cofre, falharApagar, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { guardarSessaoOf, origemDaTentativa } from "./open_finance_volta_apoio";
import { A, atualizando, contarGets, desligarTrava, drenar, lista, postsDe, servidor, umIntervalo } from "./open_finance_volta_rota_apoio";

declare const global: typeof globalThis & { __definirAppState: (v: string) => void };

const AVISO = "Conexão com o banco pendente";
const url = (q = `itemId=${A}`) => `/open-finance-volta/${origemDaTentativa()}?${q}`;
const botao = () => screen.queryByRole("button", { name: "Descartar" });
const sair = () => screen.queryByRole("button", { name: "Sair" });
const marcador = () => JSON.parse(cofre.get("pb.of.tentativa")!) as { tentativa_id: string };
const segundos = (ms: number) =>
  act(async () => {
    jest.advanceTimersByTime(ms);
    await drenar();
  });
const tocar = () =>
  act(async () => {
    fireEvent.press(botao()!);
    await drenar();
  });

/** O GET fica em voo até `soltar(resposta)`: a tela está em `conferindo`. */
function emVoo() {
  const portao = segurar();
  let corpo: () => Response = () => lista(A);
  rotear({
    "/open-finance/1": async () => {
      await portao.promessa;
      return corpo();
    },
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1/pluggy-item": () => lista(A),
  });
  return { soltar: (r: () => Response = () => lista(A)) => { corpo = r; portao.soltar(); } };
}
async function abrirConferindo(q?: string) {
  const g = emVoo();
  renderRouter("./app", { initialUrl: url(q) });
  await waitFor(() => expect(contarGets()).toBe(1));
  return g;
}

beforeEach(async () => {
  prepararCaso();
  desligarTrava();
  global.__definirAppState("active");
  await guardarSessaoOf(S);
  (AccessibilityInfo.announceForAccessibility as jest.Mock).mockClear();
});
afterEach(() => {
  falharApagar(false);
  jest.restoreAllMocks();
});

describe("open-finance-volta — Descartar", () => {
  it("C1 — um toque, GET em voo, 5 s: Início, marcador apagado, zero POST, zero GET novo, sem o aviso pendente", async () => {
    const g = await abrirConferindo();
    await segundos(5_000);
    await tocar();
    await waitFor(() => expect(screen).toHavePathname("/resumo"));
    expect(cofre.has("pb.of.tentativa")).toBe(false);
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith("Conexão pendente descartada");

    g.soltar();
    await act(drenar);
    for (let i = 0; i < 3; i++) await umIntervalo();
    expect([contarGets(), postsDe(A).length]).toEqual([1, 0]);
    expect(screen.queryByText(AVISO)).toBeNull();
  });

  it("C2 — aparece em conferindo (antes de 30 s e, depois, junto do Sair); some em updating", async () => {
    const g = await abrirConferindo();
    expect(botao()).toBeTruthy();
    expect(sair()).toBeNull();
    await segundos(30_000);
    expect(botao()).toBeTruthy();
    expect(sair()).toBeTruthy();
    g.soltar(() => atualizando(A));
    await act(drenar);
    await waitFor(() => expect(screen.getByText("Isso pode levar alguns minutos.")).toBeTruthy());
    expect(botao()).toBeNull();
    expect(sair()).toBeTruthy();
  });

  it("C2b — item pronto (estado final): sem Descartar", async () => {
    servidor(() => lista(A));
    renderRouter("./app", { initialUrl: url() });
    await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
    expect(botao()).toBeNull();
  });

  it("C2c — modo=acompanhar com marcador no cofre: conferindo, mas sem Descartar", async () => {
    await abrirConferindo(`itemId=${A}&modo=acompanhar`);
    expect(screen.getByText("Estamos conferindo com o banco.")).toBeTruthy();
    expect(botao()).toBeNull();
    expect(cofre.has("pb.of.tentativa")).toBe(true);
  });

  it("C3 — o marcador mudou para a tentativa B depois de a tela abrir: Descartar mantém a B e o aviso pendente aparece", async () => {
    await abrirConferindo();
    cofre.set("pb.of.tentativa", JSON.stringify({ ...marcador(), tentativa_id: "tentativa-b" }));
    await tocar();
    await waitFor(() => expect(screen).toHavePathname("/resumo"));
    expect(marcador().tentativa_id).toBe("tentativa-b");
    expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalled(); // nada foi descartado: a B segue pendente
    await waitFor(() => expect(screen.getByText(AVISO)).toBeTruthy());
  });

  it("C4 — o cofre falha ao apagar: vai ao Início, o marcador fica, o aviso pendente aparece e o laço parou", async () => {
    const g = await abrirConferindo();
    falharApagar(true);
    await tocar();
    await waitFor(() => expect(screen).toHavePathname("/resumo"));
    expect(cofre.has("pb.of.tentativa")).toBe(true);
    expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText(AVISO)).toBeTruthy());

    g.soltar();
    await act(drenar);
    for (let i = 0; i < 3; i++) await umIntervalo();
    expect([contarGets(), postsDe(A).length]).toEqual([1, 0]);
  });

  it("C5 — toque duplo: uma só saída, sem erro", async () => {
    await abrirConferindo();
    const replace = jest.spyOn(router, "replace");
    await act(async () => {
      const b = botao()!;
      fireEvent.press(b);
      fireEvent.press(b);
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/resumo"));
    // O `replace("/")` de `voltarAoInicio`; o Início ainda se redireciona sozinho para "/resumo".
    expect(replace.mock.calls.filter(([destino]) => destino === "/")).toHaveLength(1);
    expect(cofre.has("pb.of.tentativa")).toBe(false);
  });

  it("C7 — a pessoa sai da tela enquanto o cofre apaga: o fim do apagar não a puxa de volta para o Início nem anuncia", async () => {
    await abrirConferindo();
    const apagarDeVerdade = SecureStore.deleteItemAsync;
    const apagar = segurar();
    jest.spyOn(SecureStore, "deleteItemAsync").mockImplementation(async (k) => {
      await apagar.promessa;
      return apagarDeVerdade(k);
    });
    await tocar();
    expect(screen).toHavePathname("/open-finance-volta");
    await act(async () => { router.replace("/conexoes"); await drenar(); });
    await act(async () => {
      apagar.soltar();
      await drenar();
    });
    expect(screen).toHavePathname("/conexoes");
    expect(cofre.has("pb.of.tentativa")).toBe(false);
    expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalled();
  });

  it("C6 — o item termina durante o apagar (cofre lento e que falha): o laço já desistiu, nenhum POST", async () => {
    const g = await abrirConferindo();
    // O apagar do cofre fica pendente e depois falha: o marcador SEGUE lá e a tela ainda está montada quando o GET volta.
    const apagar = segurar();
    jest.spyOn(SecureStore, "deleteItemAsync").mockImplementation(async () => {
      await apagar.promessa;
      throw new Error("keychain");
    });
    await tocar();
    expect(screen).toHavePathname("/open-finance-volta");
    // O GET volta SEM o item. Aqui o laço ainda está preso na fila do cofre (atrás do apagar pendente), então esta checagem passa com ou sem `desistiu`;
    // quem discrimina é a última (`[1, 0]`, depois de soltar o apagar): sem `desistiu` o laço registra o item (POST) com o marcador ainda no cofre.
    g.soltar(() => lista());
    await act(drenar);
    expect(postsDe(A)).toEqual([]);
    await act(async () => {
      apagar.soltar();
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/resumo"));
    for (let i = 0; i < 3; i++) await umIntervalo();
    expect([contarGets(), postsDe(A).length]).toEqual([1, 0]);
    expect(cofre.has("pb.of.tentativa")).toBe(true);
  });
});
