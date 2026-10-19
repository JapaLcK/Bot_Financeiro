import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import * as SecureStore from "expo-secure-store";
import { AccessibilityInfo } from "react-native";
import { __resetAvisoBanco } from "@/features/painel/avisoBanco";
import { PRAZO_TENTATIVA_MS } from "@/storage/secure";
import { chamadas, cofre, falharApagar, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { guardarSessaoOf } from "./open_finance_volta_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";

// O dono tocou num banco sem querer e o aviso ficou para sempre: "Descartar" apaga o marcador do cofre. Roda pela rota, como o `painel_pilha_avisos.test.tsx`.
const PREVISAO = "/api/app/previsao", AVISO = "Conexão com o banco pendente", ERRO = "Não conseguimos descartar agora. Tente de novo.";
const RECEITAS = [{ chave: "extrato#sal", nome: "Salário Acme", valor: "5000.00", dia: 5, status: "pendente" }];
const pendente = () => screen.queryByText(AVISO);
const marcador = () => JSON.parse(cofre.get("pb.of.tentativa")!) as { tentativa_id: string; iniciada_em: number };
const idade = (ms: number) => cofre.set("pb.of.tentativa", JSON.stringify({ ...marcador(), iniciada_em: Date.now() - ms }));
async function abrir(esperar = AVISO) {
  rotear({
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null }),
    [PREVISAO]: () => resposta(200, { ...fixture[PREVISAO], receitas_do_banco: RECEITAS }),
  });
  renderRouter("./app", { initialUrl: "/resumo" });
  await waitFor(() => expect(screen.getByText(esperar)).toBeTruthy());
  await act(drenar);
}
const descartar = () => act(async () => { fireEvent.press(screen.getByRole("button", { name: "Descartar" })); await drenar(); });
beforeEach(async () => { __resetAvisoBanco(); prepararCaso(); desligarTrava(); await guardarSessaoOf(S); (AccessibilityInfo.announceForAccessibility as jest.Mock).mockClear(); });
afterEach(() => { falharApagar(false); jest.restoreAllMocks(); });

it("Descartar tira o aviso e o marcador, não chama o servidor do banco e mantém os outros avisos", async () => {
  await abrir();
  await waitFor(() => expect(screen.getByTestId("pilha-ponto-1")).toBeTruthy()); // a receita está na pilha, atrás da conexão
  await descartar();
  await waitFor(() => expect(pendente()).toBeNull());
  expect(cofre.has("pb.of.tentativa")).toBe(false);
  expect(chamadas().filter((c) => c.caminho.endsWith("/pluggy-item"))).toEqual([]);
  expect(screen.getByText("Salário Acme")).toBeTruthy(); // os outros avisos ficam
});

it("o aviso lido com a tentativa A não apaga a B gravada depois", async () => {
  await abrir();
  cofre.set("pb.of.tentativa", JSON.stringify({ ...marcador(), tentativa_id: "tentativa-b" }));
  await descartar();
  expect(marcador().tentativa_id).toBe("tentativa-b");
  expect(pendente()).toBeTruthy(); // a B continua aparecendo
});

it("marcador vencido na montagem não mostra o aviso", async () => {
  idade(PRAZO_TENTATIVA_MS + 1_000);
  await abrir("Salário Acme");
  expect(pendente()).toBeNull();
});
it("marcador recente na montagem mostra o aviso", async () => {
  idade(PRAZO_TENTATIVA_MS - 60_000);
  await abrir();
  expect(pendente()).toBeTruthy();
});

it("falha ao apagar no cofre: erro visível e o aviso fica", async () => {
  await abrir();
  falharApagar(true);
  await descartar();
  await waitFor(() => expect(screen.getByText(ERRO)).toBeTruthy());
  expect(pendente()).toBeTruthy();
  expect(cofre.has("pb.of.tentativa")).toBe(true);
  expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalled();
  // Tocar de novo com o cofre liberado limpa o aviso de erro: sem botão de fechar, ele ficava em todas as telas.
  falharApagar(false);
  await descartar();
  await waitFor(() => expect(pendente()).toBeNull());
  expect(screen.queryByText(ERRO)).toBeNull();
  expect(cofre.has("pb.of.tentativa")).toBe(false);
});

it("falha ao apagar e o marcador vence depois: a pendência some e o erro vai junto, sem botão para tentar de novo", async () => {
  await abrir();
  falharApagar(true);
  await descartar();
  await waitFor(() => expect(screen.getByText(ERRO)).toBeTruthy());
  falharApagar(false);
  idade(PRAZO_TENTATIVA_MS + 1_000);
  await appVai("background");
  await appVai("active");
  await waitFor(() => expect(pendente()).toBeNull());
  expect(screen.queryByText(ERRO)).toBeNull();
});

it("o marcador vence com o painel aberto: o aviso sai sozinho, sem esperar o próximo foco", async () => {
  idade(PRAZO_TENTATIVA_MS - 60_000);
  await abrir();
  await act(async () => { jest.advanceTimersByTime(30_000); await drenar(); });
  expect(pendente()).toBeTruthy(); // ainda dentro do prazo
  await act(async () => { jest.advanceTimersByTime(31_000); await drenar(); });
  expect(pendente()).toBeNull();
});

it("a falha do apagar chega DEPOIS de a pendência sair da tela (prazo): não deixa erro órfão", async () => {
  idade(PRAZO_TENTATIVA_MS - 60_000);
  await abrir();
  let falhar!: (e: Error) => void;
  jest.spyOn(SecureStore, "deleteItemAsync").mockImplementation(() => new Promise<void>((_, rej) => { falhar = rej; }));
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Descartar" })); await drenar(); });
  await act(async () => { jest.advanceTimersByTime(61_000); await drenar(); });
  expect(pendente()).toBeNull();
  await act(async () => { falhar(new Error("keychain")); await drenar(); });
  expect(screen.queryByText(ERRO)).toBeNull();
});

it("a legenda do aviso diz como sair, sem supor que a pessoa desistiu", async () => {
  await abrir();
  expect(screen.getByText("Não vai concluir? Descarte e comece de novo quando quiser.")).toBeTruthy();
});

it("acessibilidade: botão com papel e rótulo 'Descartar'; o leitor ouve que descartou", async () => {
  await abrir();
  expect(screen.getByRole("button", { name: "Descartar" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Retomar conexão" })).toBeEnabled();
  await descartar();
  await waitFor(() => expect(pendente()).toBeNull());
  expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith("Conexão pendente descartada");
});
