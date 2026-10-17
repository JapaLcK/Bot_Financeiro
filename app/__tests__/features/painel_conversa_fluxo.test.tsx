import { act, screen, waitFor } from "expo-router/testing-library";
import { AccessibilityInfo } from "react-native";
import { fetchFalso, prepararCaso, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, apertar, campo, caminho, delecoes, enviar, fim, ir, noResumo, ok, posts, reiniciar, respostaFluxo, srv, voltar, type Fluxo } from "./conversa_apoio";
// Resposta em stream pela tela, com o servidor falso: o teste empurra os eventos e vê o que a tela mostra.
jest.setTimeout(20_000);

let f: Fluxo | undefined;
const ERRO = "Não conseguimos carregar agora. Tente novamente.";
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); f = undefined; srv.chat = () => (f = respostaFluxo()); });
afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers(); });
const empurrar = async (...evs: object[]) => { await waitFor(() => expect(f).toBeDefined()); await act(async () => { evs.forEach((e) => f!.empurrar(e)); await drenar(); }); };
const delta = (texto: string) => ({ tipo: "delta", texto });
const texto = (t: string) => screen.queryByText(t);
const ordem = () => fetchFalso.mock.calls.map(([u, r]) => `${r.method ?? "GET"} ${caminho(u)}`).filter((x) => x === "DELETE /ai/pending" || x === "POST /ai/chat/stream");
const parar = () => apertar("Parar resposta");

it("delta mostra o texto antes do fim; reinicio zera; o fim troca pelo reply", async () => {
  await abrir(); await enviar("oi"); await empurrar(delta("Vou ver"));
  expect(texto("Vou ver")).toBeTruthy(); expect(screen.getByRole("button", { name: "Parar resposta" })).toBeEnabled();
  await empurrar({ tipo: "reinicio" }); expect(texto("Vou ver")).toBeNull();
  await empurrar(delta("Final")); expect(texto("Final")).toBeTruthy(); expect(texto("Vou verFinal")).toBeNull();
  await empurrar(fim("Resposta final")); expect(texto("Resposta final")).toBeTruthy(); expect(texto("Final")).toBeNull();
  expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled(); // voltou ao botão de enviar (campo vazio)
});
it("fim duplicado: vale o 1º reply e a 1ª cota", async () => {
  await abrir(); await enviar("oi"); await empurrar(fim("Primeira", { usage: { used: 81, limit: 100 } }), fim("Segunda", { usage: { used: 90, limit: 100 } }));
  expect(texto("Primeira")).toBeTruthy(); expect(texto("Segunda")).toBeNull(); expect(screen.getByText("81 de 100 mensagens utilizadas")).toBeTruthy();
});
it("evento desconhecido e bloco inválido no meio não quebram o turno", async () => {
  await abrir(); await enviar("oi");
  await empurrar(delta("A"), { tipo: "novo" }, { tipo: "bloco", bloco: { card: "inventado", abas: [] } }, { tipo: "passo" }, delta("B"));
  expect(texto("AB")).toBeTruthy(); expect(texto(ERRO)).toBeNull();
  await empurrar(fim("Pronto")); expect(texto("Pronto")).toBeTruthy(); expect(texto(ERRO)).toBeNull();
});
it("stream que fecha sem fim: erro, parcial some, rascunho volta, o próximo envio faz DELETE antes do POST", async () => {
  await abrir(); await enviar("Pergunta"); await empurrar(delta("meio da frase")); expect(texto("meio da frase")).toBeTruthy();
  await act(async () => { f!.fechar(); await drenar(); });
  await waitFor(() => expect(texto(ERRO)).toBeTruthy()); expect(texto("meio da frase")).toBeNull(); expect(campo().props.value).toBe("Pergunta");
  srv.chat = ok("Agora foi"); await apertar("Enviar mensagem"); await waitFor(() => expect(texto("Agora foi")).toBeTruthy());
  expect(ordem()).toEqual(["DELETE /ai/pending", "POST /ai/chat/stream", "DELETE /ai/pending", "POST /ai/chat/stream"]);
});
it("evento `erro`: mesma coisa (erro, parcial some, rascunho volta, DELETE antes do próximo POST)", async () => {
  await abrir(); await enviar("Pergunta"); await empurrar(delta("meio"), { tipo: "erro", mensagem: "x" });
  await waitFor(() => expect(texto(ERRO)).toBeTruthy()); expect(texto("meio")).toBeNull(); expect(campo().props.value).toBe("Pergunta");
  srv.chat = ok("Agora foi"); await apertar("Enviar mensagem"); await waitFor(() => expect(texto("Agora foi")).toBeTruthy());
  expect(ordem().slice(-2)).toEqual(["DELETE /ai/pending", "POST /ai/chat/stream"]);
});
it("60 s sem fim (timers falsos): erro, não 'parou'", async () => {
  await abrir(); jest.useFakeTimers(); await enviar("Pergunta"); await waitFor(() => expect(f).toBeDefined()); await empurrar(delta("parcial"));
  await act(async () => { jest.advanceTimersByTime(60_001); await drenar(); await drenar(); });
  await waitFor(() => expect(texto(ERRO)).toBeTruthy()); expect(texto("Você parou esta resposta.")).toBeNull(); expect(texto("parcial")).toBeNull();
});
it("parar: aborta o fetch, fica o parcial e a legenda, sem chips nem 👍👎, campo vazio, e o próximo envio faz DELETE antes do POST", async () => {
  await abrir(); await enviar("Pergunta"); await empurrar(delta("Começo da resposta"), { tipo: "passo", texto: "Vendo", previa: null });
  await parar(); expect(posts()[0]![1].signal.aborted).toBe(true);
  await waitFor(() => expect(texto("Você parou esta resposta.")).toBeTruthy()); expect(texto("Começo da resposta")).toBeTruthy();
  expect(screen.queryByLabelText("Gostei da resposta")).toBeNull(); expect(screen.queryByLabelText("Copiar resposta")).toBeNull(); expect(texto(ERRO)).toBeNull();
  expect(campo().props.value).toBe(""); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled();
  srv.chat = ok("Depois de parar"); await enviar("Outra"); await waitFor(() => expect(texto("Depois de parar")).toBeTruthy());
  expect(ordem()).toEqual(["DELETE /ai/pending", "POST /ai/chat/stream", "DELETE /ai/pending", "POST /ai/chat/stream"]);
});
it("controle positivo: depois de um fim normal o próximo envio vai direto ao POST", async () => {
  srv.chat = ok("Primeira"); await abrir(); await enviar("p1"); await waitFor(() => expect(texto("Primeira")).toBeTruthy());
  srv.chat = ok("Segunda"); await enviar("p2"); await waitFor(() => expect(texto("Segunda")).toBeTruthy());
  expect(ordem()).toEqual(["DELETE /ai/pending", "POST /ai/chat/stream", "POST /ai/chat/stream"]);
});
it("parar depois de todos os deltas e antes do fim (limite declarado): texto inteiro com a legenda, sem 👍👎, DELETE antes do próximo", async () => {
  await abrir(); await enviar("Pergunta"); await empurrar(delta("Texto "), delta("inteiro")); await parar();
  await waitFor(() => expect(texto("Você parou esta resposta.")).toBeTruthy()); expect(texto("Texto inteiro")).toBeTruthy(); expect(screen.queryByLabelText("Gostei da resposta")).toBeNull();
  srv.chat = ok("Seguinte"); await enviar("Outra"); await waitFor(() => expect(texto("Seguinte")).toBeTruthy());
  expect(ordem().slice(-2)).toEqual(["DELETE /ai/pending", "POST /ai/chat/stream"]);
});
it("fim com a aba fechada: chips descartados, DELETE da saída e a nota ao voltar", async () => {
  srv.cancelada = "Apagar o gasto"; await abrir(); await enviar("apaga"); await noResumo(); const antes = delecoes().length;
  await empurrar(fim("Posso apagar?", { chips: ["Chip A"] })); await act(drenar); expect(delecoes()).toHaveLength(antes + 1);
  await voltar(); expect(texto("Posso apagar?")).toBeTruthy(); expect(screen.queryByRole("button", { name: "Chip A" })).toBeNull(); expect(screen.getByText(/Cancelei Apagar o gasto/)).toBeTruthy();
});
it("fim chega com a reabertura em `abrindo`: chips escondidos; liberado o GET, aparecem", async () => {
  await abrir(); await enviar("p"); await noResumo(); srv.segurarGet = segurar(); await ir("/piggy"); await act(drenar);
  await empurrar(fim("Resposta", { chips: ["Chip A"] })); expect(texto("Resposta")).toBeTruthy(); expect(screen.queryByRole("button", { name: "Chip A" })).toBeNull();
  const g = srv.segurarGet; srv.segurarGet = null; g.soltar(); await act(drenar); await act(drenar);
  await waitFor(() => expect(screen.getByRole("button", { name: "Chip A" })).toBeTruthy());
});
it("anúncio 'Piggy respondeu' no fim, com a aba aberta", async () => {
  const anunciar = jest.mocked(AccessibilityInfo.announceForAccessibility); anunciar.mockClear();
  await abrir(); await enviar("oi"); await empurrar(delta("x")); expect(anunciar).not.toHaveBeenCalled();
  await empurrar(fim("Pronto")); expect(anunciar).toHaveBeenCalledWith("Piggy respondeu");
});
