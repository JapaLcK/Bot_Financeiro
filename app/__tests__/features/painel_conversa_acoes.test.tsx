import { setStringAsync } from "expo-clipboard";
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";
import { prepararCaso, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, apertar, campo, digitar, enviar, feedbacks, fim, ok, posts, reiniciar, respostaFluxo, srv } from "./conversa_apoio";
// Chips de continuação, copiar e 👍👎 sob a resposta do Piggy.
jest.setTimeout(20_000);

beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); jest.mocked(setStringAsync).mockClear(); });
afterEach(() => { jest.restoreAllMocks(); });
const texto = (t: string) => screen.queryByText(t);
const chip = (t: string) => screen.queryByRole("button", { name: t });
/** Um stream que já traz o `fim` (com `extra`) e fecha. */
const comFim = (reply: string, extra: object = {}) => () => { const f = respostaFluxo(); f.empurrar(fim(reply, extra)); f.fechar(); return f; };
const corpo = (i: number) => JSON.parse(String(posts()[i]![1].body));
const nota = (rotulo: string) => screen.getByLabelText(rotulo).props.accessibilityState?.selected;
const bodyFeedback = (i: number) => JSON.parse(String(feedbacks()[i]![1].body));

it("chip tocado: o POST leva o texto exato, a bolha mostra o chip e a linha some", async () => {
  srv.chat = (n) => n === 1 ? comFim("Primeira", { chips: ["E o mês passado?", "Chip B"] })() : ok("Segunda")();
  await abrir(); await enviar("p1"); await waitFor(() => expect(chip("E o mês passado?")).toBeTruthy());
  await apertar("E o mês passado?"); await waitFor(() => expect(texto("Segunda")).toBeTruthy());
  expect(corpo(1)).toMatchObject({ message: "E o mês passado?" }); expect(texto("E o mês passado?")).toBeTruthy(); expect(chip("E o mês passado?")).toBeNull(); expect(chip("Chip B")).toBeNull();
});
it("chips só do último turno; somem com valores ocultos e voltam ao mostrar", async () => {
  srv.chat = (n) => comFim(`R${n}`, { chips: [`Chip ${n}`] })();
  await abrir(); await enviar("p1"); await waitFor(() => expect(chip("Chip 1")).toBeTruthy());
  await apertar("Chip 1"); await waitFor(() => expect(chip("Chip 2")).toBeTruthy()); expect(chip("Chip 1")).toBeNull();
  await apertar("Ocultar valores"); expect(chip("Chip 2")).toBeNull(); await apertar("Mostrar valores"); expect(chip("Chip 2")).toBeTruthy();
});
it("chip tocado com rascunho no campo: o POST leva o chip e o campo mantém o rascunho; enviar pelo campo limpa", async () => {
  srv.chat = (n) => n === 1 ? comFim("Primeira", { chips: ["Chip A"] })() : ok("Segunda")();
  await abrir(); await enviar("p1"); await waitFor(() => expect(chip("Chip A")).toBeTruthy());
  await digitar("meu rascunho"); await apertar("Chip A"); await waitFor(() => expect(texto("Segunda")).toBeTruthy());
  expect(corpo(1)).toMatchObject({ message: "Chip A" }); expect(campo().props.value).toBe("meu rascunho");
  await apertar("Enviar mensagem"); await waitFor(() => expect(posts()).toHaveLength(3)); expect(corpo(2)).toMatchObject({ message: "meu rascunho" }); expect(campo().props.value).toBe("");
});
it("valores ocultos com turno em voo: Parar habilitado, parar não revela nada e a legenda aparece ao mostrar", async () => {
  const f = respostaFluxo(); srv.chat = () => f; await abrir(); await enviar("p1"); await waitFor(() => expect(posts()).toHaveLength(1));
  await apertar("Ocultar valores"); expect(screen.getByRole("button", { name: "Parar resposta" })).toBeEnabled();
  await apertar("Parar resposta"); expect(posts()[0]![1].signal.aborted).toBe(true); expect(texto("Você parou esta resposta.")).toBeNull();
  await apertar("Mostrar valores"); await waitFor(() => expect(texto("Você parou esta resposta.")).toBeTruthy());
});
it("cota cheia no fim: sem chips", async () => {
  srv.chat = comFim("Última", { chips: ["Chip A"], usage: { used: 100, limit: 100 } });
  await abrir(); await enviar("p1"); await waitFor(() => expect(texto("Última")).toBeTruthy()); expect(chip("Chip A")).toBeNull();
});
it("copiar: recebe o reply sem os `*` do negrito (com `5*3*4` intacto) e mostra o toast 'Copiado'", async () => {
  srv.chat = comFim("Gastou *R$ 50* e deu 5*3*4"); await abrir(); await enviar("p1"); await waitFor(() => expect(screen.getByLabelText("Copiar resposta")).toBeTruthy());
  await apertar("Copiar resposta"); expect(setStringAsync).toHaveBeenCalledWith("Gastou R$ 50 e deu 5*3*4"); await waitFor(() => expect(texto("Copiado")).toBeTruthy());
});
it("👍 é otimista: o POST leva {nota:1}; tocar de novo manda null; um 500 reverte e mostra o toast", async () => {
  srv.chat = comFim("Resposta"); await abrir(); await enviar("p1"); await waitFor(() => expect(screen.getByLabelText("Gostei da resposta")).toBeTruthy());
  const g = segurar(); srv.feedback = { status: 200, seguro: g }; await apertar("Gostei da resposta");
  expect(nota("Gostei da resposta")).toBe(true); expect(nota("Não gostei da resposta")).toBe(false); expect(feedbacks()).toHaveLength(1); expect(bodyFeedback(0)).toEqual({ nota: 1 }); // marcado antes de o servidor responder
  g.soltar(); await act(drenar); await apertar("Gostei da resposta"); expect(bodyFeedback(1)).toEqual({ nota: null }); expect(nota("Gostei da resposta")).toBe(false);
  await act(drenar); srv.feedback = { status: 500 }; await apertar("Não gostei da resposta");
  await waitFor(() => expect(texto("Não conseguimos registrar sua avaliação.")).toBeTruthy()); expect(nota("Não gostei da resposta")).toBe(false);
});
it("toque duplo durante o POST do feedback: um POST só", async () => {
  srv.chat = comFim("Resposta"); await abrir(); await enviar("p1"); await waitFor(() => expect(screen.getByLabelText("Gostei da resposta")).toBeTruthy());
  const g = segurar(); srv.feedback = { status: 200, seguro: g };
  await act(async () => { const b = screen.getByLabelText("Gostei da resposta"); fireEvent.press(b); fireEvent.press(b); await drenar(); });
  expect(feedbacks()).toHaveLength(1); g.soltar(); await act(drenar);
});
it("resposta sem message_id (erro/cota do servidor): sem 👍👎, o copiar continua", async () => {
  srv.chat = comFim("Limite atingido", { message_id: null }); await abrir(); await enviar("p1"); await waitFor(() => expect(texto("Limite atingido")).toBeTruthy());
  expect(screen.queryByLabelText("Gostei da resposta")).toBeNull(); expect(screen.queryByLabelText("Não gostei da resposta")).toBeNull(); expect(screen.getByLabelText("Copiar resposta")).toBeTruthy();
});
// Erro em chip/sugestão: o texto volta ao campo vazio (reenvio com um toque); rascunho digitado fica intacto.
const SEM_FIM = () => { const f = respostaFluxo(); f.fechar(); return f; };
const ERRO = "Não conseguimos carregar agora. Tente novamente.";
const SUGESTAO = "Onde eu consigo economizar este mês?";
it.each([["campo vazio", "", "Chip A"], ["com rascunho", "meu rascunho", "meu rascunho"]])("chip tocado, stream fecha sem fim (%s): erro e o campo recebe o texto só se vazio", async (_n, rascunho, esperado) => {
  srv.chat = (n) => n === 1 ? comFim("Primeira", { chips: ["Chip A"] })() : SEM_FIM();
  await abrir(); await enviar("p1"); await waitFor(() => expect(chip("Chip A")).toBeTruthy());
  if (rascunho) await digitar(rascunho);
  await apertar("Chip A"); await waitFor(() => expect(texto(ERRO)).toBeTruthy()); expect(campo().props.value).toBe(esperado);
});
it.each([["campo vazio", "", SUGESTAO], ["com rascunho", "meu rascunho", "meu rascunho"]])("sugestão do estado vazio, stream fecha sem fim (%s): erro e o campo recebe o texto só se vazio", async (_n, rascunho, esperado) => {
  srv.chat = SEM_FIM; await abrir(); if (rascunho) await digitar(rascunho);
  await apertar(SUGESTAO); await waitFor(() => expect(texto(ERRO)).toBeTruthy()); expect(campo().props.value).toBe(esperado);
});
