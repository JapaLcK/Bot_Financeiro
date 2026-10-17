import { Keyboard, TextInput } from "react-native";
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";
import { prepararCaso, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, apertar, digitar, enviar, feedbacks, pronto, trocarParaBela, fim, ok, posts, reiniciar, respostaFluxo, srv } from "./conversa_apoio";
// Motivo do 👎 (PR 4b): pílulas sob a última resposta, "Outro" põe a caixa principal do chat em modo comentário.
jest.setTimeout(20_000);

beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); });
afterEach(() => { jest.restoreAllMocks(); });
const corpos = () => feedbacks().map(([, r]) => JSON.parse(String(r.body)));
const pilula = (t: string) => screen.queryByRole("button", { name: t });
const marcada = (t: string) => pilula(t)!.props.accessibilityState?.selected;
const comentario = () => screen.queryByLabelText("Comentário sobre a resposta");
const caixa = () => screen.getByLabelText("Mensagem para o Piggy");
const escrever = (t: string) => act(async () => { fireEvent.changeText(comentario()!, t); await drenar(); });
const MOTIVOS = ["Número errado", "Não entendeu", "Faltou card", "Outro"];
const NAO_GOSTEI = "Não gostei da resposta";
const comResposta = async (extra: object = {}) => {
  srv.chat = () => { const f = respostaFluxo(); f.empurrar(fim("Resposta", extra)); f.fechar(); return f; };
  await abrir(); await enviar("p1"); await waitFor(() => expect(screen.getByLabelText(NAO_GOSTEI)).toBeTruthy());
};
const comDownvote = async () => { await comResposta(); await apertar(NAO_GOSTEI); await waitFor(() => expect(pilula("Outro")).toBeTruthy()); };

it("as pílulas só aparecem com 👎 na última resposta: sem nota, com 👍 e depois de retirar o 👎 não há pílula", async () => {
  await comResposta(); expect(screen.queryByText("O que houve?")).toBeNull(); expect(pilula("Outro")).toBeNull();
  await apertar("Gostei da resposta"); expect(pilula("Outro")).toBeNull();
  await apertar(NAO_GOSTEI); expect(screen.getByText("O que houve?")).toBeTruthy(); for (const m of MOTIVOS) expect(pilula(m)).toBeTruthy();
  await apertar(NAO_GOSTEI); expect(pilula("Outro")).toBeNull(); expect(screen.queryByText("O que houve?")).toBeNull();
});
it("resposta sem message_id não tem 👎, logo não tem pílula", async () => {
  srv.chat = () => { const f = respostaFluxo(); f.empurrar(fim("Limite", { message_id: null })); f.fechar(); return f; };
  await abrir(); await enviar("p1"); await waitFor(() => expect(screen.getByText("Limite")).toBeTruthy()); expect(pilula("Outro")).toBeNull();
});
it("o 👎 sozinho continua mandando só {nota} (build sem motivo)", async () => {
  await comDownvote(); expect(corpos()).toEqual([{ nota: -1 }]);
});
it.each([["Número errado", "numero_errado"], ["Não entendeu", "nao_entendeu"], ["Faltou card", "faltou_card"]])("a pílula %s manda {nota:-1, motivo} e fica marcada; tocar de novo desmarca com {nota:-1}", async (rotulo, chave) => {
  await comDownvote(); await apertar(rotulo);
  expect(corpos()[1]).toEqual({ nota: -1, motivo: chave }); expect(marcada(rotulo)).toBe(true); expect(comentario()).toBeNull();
  await apertar(rotulo); expect(corpos()[2]).toEqual({ nota: -1 }); expect(marcada(rotulo)).toBe(false);
});
it("trocar de pílula manda o motivo novo e marca só ela", async () => {
  await comDownvote(); await apertar("Número errado"); await apertar("Faltou card");
  expect(corpos()[2]).toEqual({ nota: -1, motivo: "faltou_card" }); expect(marcada("Faltou card")).toBe(true); expect(marcada("Número errado")).toBe(false);
});
it("Outro grava na hora e põe a caixa principal em modo comentário (sem campo novo); ↑ manda o texto aparado como feedback, não à IA", async () => {
  await comDownvote(); await digitar("rascunho do chat"); const antes = posts().length; await apertar("Outro");
  expect(corpos()[1]).toEqual({ nota: -1, motivo: "outro" }); expect(marcada("Outro")).toBe(true);
  const c = comentario()!; expect(c.props.maxLength).toBe(500); expect(c.props.placeholder).toBe("Conte o que houve (opcional)");
  expect(c.props.value).toBe(""); expect(screen.queryByLabelText("Mensagem para o Piggy")).toBeNull(); expect(screen.UNSAFE_getAllByType(TextInput)).toHaveLength(1); // a mesma caixa, não uma segunda
  expect(screen.getByRole("button", { name: "Enviar comentário" })).toBeDisabled(); // vazio: nada a enviar
  await escrever("   "); expect(screen.getByRole("button", { name: "Enviar comentário" })).toBeDisabled();
  await escrever("  o saldo está errado  "); await apertar("Enviar comentário");
  expect(corpos()[2]).toEqual({ nota: -1, motivo: "outro", texto: "o saldo está errado" }); expect(posts()).toHaveLength(antes);
  await waitFor(() => expect(screen.getByText("Obrigado pelo retorno")).toBeTruthy()); expect(comentario()).toBeNull(); expect(marcada("Outro")).toBe(true);
  expect(caixa().props.value).toBe("rascunho do chat"); // o rascunho do chat voltou intacto
});
it("tocar de novo em Outro desmarca: manda {nota:-1}, sai do modo e o texto do comentário não vira rascunho do chat", async () => {
  await comDownvote(); await apertar("Outro"); await escrever("comentário");
  await apertar("Outro"); expect(corpos()[2]).toEqual({ nota: -1 }); expect(marcada("Outro")).toBe(false); expect(comentario()).toBeNull(); expect(caixa().props.value).toBe("");
  await apertar("Outro"); expect(comentario()!.props.value).toBe(""); // reabre limpo
});
it("escolher outra pílula, 👍 ou retirar o 👎 também saem do modo comentário", async () => {
  await comDownvote(); await apertar("Outro"); await apertar("Faltou card"); expect(comentario()).toBeNull();
  await apertar("Outro"); await apertar("Gostei da resposta"); expect(comentario()).toBeNull();
  await apertar(NAO_GOSTEI); await apertar("Outro"); expect(comentario()).not.toBeNull(); await apertar(NAO_GOSTEI); expect(comentario()).toBeNull();
});
it("uma pergunta nova por chip sai do modo comentário e o rascunho do chat segue intacto", async () => {
  await comResposta({ chips: ["Me mostra mais"] }); await digitar("rascunho"); await apertar(NAO_GOSTEI); await apertar("Outro"); expect(comentario()).not.toBeNull();
  srv.chat = ok("Segunda"); await apertar("Me mostra mais"); await waitFor(() => expect(screen.getByText("Segunda")).toBeTruthy());
  expect(comentario()).toBeNull(); expect(caixa().props.value).toBe("rascunho");
});
it("tocar em Outro foca a caixa principal (cursor piscando), uma vez, e não ao digitar", async () => {
  const foco = jest.spyOn(TextInput.prototype as unknown as { focus: () => void }, "focus").mockImplementation(() => {});
  await comDownvote(); const n = foco.mock.calls.length; await apertar("Outro"); expect(foco).toHaveBeenCalledTimes(n + 1);
  await escrever("a"); await escrever("ab"); expect(foco).toHaveBeenCalledTimes(n + 1);
});
it("enviar o comentário com sucesso fecha o teclado (ele cobriria o toast); na falha o teclado fica", async () => {
  const fecha = jest.spyOn(Keyboard, "dismiss").mockImplementation(() => {});
  await comDownvote(); await apertar("Outro"); await escrever("x"); srv.feedback = { status: 500 }; await apertar("Enviar comentário");
  await waitFor(() => expect(screen.getByText("Não conseguimos registrar sua avaliação.")).toBeTruthy()); expect(fecha).not.toHaveBeenCalled();
  srv.feedback = { status: 200 }; await apertar("Enviar comentário"); await waitFor(() => expect(screen.getByText("Obrigado pelo retorno")).toBeTruthy()); expect(fecha).toHaveBeenCalledTimes(1);
});
it("falha ao desmarcar o Outro devolve o modo comentário E o texto digitado", async () => {
  await comDownvote(); await apertar("Outro"); await escrever("meu texto longo");
  srv.feedback = { status: 500 }; await apertar("Outro");
  await waitFor(() => expect(screen.getByText("Não conseguimos registrar sua avaliação.")).toBeTruthy());
  expect(marcada("Outro")).toBe(true); expect(comentario()!.props.value).toBe("meu texto longo");
});
it("dois toques no ↑ no mesmo quadro: o segundo é descartado, sem agradecer; se o 1º POST falha o texto fica", async () => {
  await comDownvote(); await apertar("Outro"); await escrever("não perca");
  const g = segurar(); srv.feedback = { status: 500, seguro: g }; const n = feedbacks().length;
  await act(async () => { const b = screen.getByRole("button", { name: "Enviar comentário" }); fireEvent.press(b); fireEvent.press(b); await drenar(); });
  expect(feedbacks()).toHaveLength(n + 1); expect(screen.queryByText("Obrigado pelo retorno")).toBeNull(); expect(comentario()).not.toBeNull(); // o descarte não fechou o modo
  g.soltar(); await act(drenar);
  await waitFor(() => expect(screen.getByText("Não conseguimos registrar sua avaliação.")).toBeTruthy()); expect(screen.queryByText("Obrigado pelo retorno")).toBeNull(); expect(comentario()!.props.value).toBe("não perca");
});
it("toque duplo no 👎 no mesmo quadro: o descarte não vira toast de erro", async () => {
  await comResposta(); const g = segurar(); srv.feedback = { status: 200, seguro: g };
  await act(async () => { const b = screen.getByLabelText(NAO_GOSTEI); fireEvent.press(b); fireEvent.press(b); await drenar(); });
  g.soltar(); await act(drenar); expect(feedbacks()).toHaveLength(1); expect(screen.queryByText("Não conseguimos registrar sua avaliação.")).toBeNull();
});
it("entrar no Outro numa resposta nova não herda o texto do comentário da anterior", async () => {
  await comResposta({ chips: ["Me mostra mais"] }); await apertar(NAO_GOSTEI); await apertar("Outro"); await escrever("texto da resposta 1");
  srv.chat = ok("Segunda", { chips: [] }); await apertar("Me mostra mais"); await waitFor(() => expect(screen.getByText("Segunda")).toBeTruthy());
  expect(comentario()).toBeNull(); await apertar(NAO_GOSTEI); expect(comentario()).toBeNull(); // 👎 sem motivo: nada de modo
  await apertar("Outro"); expect(comentario()!.props.value).toBe("");
});
it("troca de dono com o POST do comentário em voo: a gravação abortada não agradece nem fecha o teclado para o outro", async () => {
  const fecha = jest.spyOn(Keyboard, "dismiss").mockImplementation(() => {});
  await comDownvote(); await apertar("Outro"); await escrever("texto da ana");
  const g = segurar(); srv.feedback = { status: 200, seguro: g }; await apertar("Enviar comentário"); expect(feedbacks()).toHaveLength(3);
  await trocarParaBela(); await act(drenar); await pronto(); g.soltar(); await act(drenar);
  expect(fecha).not.toHaveBeenCalled(); expect(screen.queryByText("Obrigado pelo retorno")).toBeNull();
});
it("valores ocultos: no modo comentário a caixa fica vazia, não editável e o ↑ desligado; ao mostrar, o texto volta", async () => {
  await comDownvote(); await apertar("Outro"); await escrever("segredo");
  await apertar("Ocultar valores"); expect(comentario()!.props.value).toBe(""); expect(comentario()!.props.editable).toBe(false);
  expect(screen.getByRole("button", { name: "Enviar comentário" })).toBeDisabled();
  await apertar("Mostrar valores"); expect(comentario()!.props.value).toBe("segredo"); expect(screen.getByRole("button", { name: "Enviar comentário" })).toBeEnabled();
});
it("falha de rede na pílula reverte a marca e mostra o toast", async () => {
  await comDownvote(); srv.feedback = { status: 500 }; await apertar("Número errado");
  await waitFor(() => expect(screen.getByText("Não conseguimos registrar sua avaliação.")).toBeTruthy()); expect(marcada("Número errado")).toBe(false);
});
it("falha ao desmarcar volta a marca; falha no Outro sai do modo comentário", async () => {
  await comDownvote(); await apertar("Número errado"); srv.feedback = { status: 500 }; await apertar("Número errado");
  await waitFor(() => expect(marcada("Número errado")).toBe(true));
  await apertar("Outro"); await waitFor(() => expect(marcada("Outro")).toBe(false)); expect(marcada("Número errado")).toBe(true); expect(comentario()).toBeNull();
});
it("falha ao enviar o comentário mantém o modo e o texto, sem toast de agradecimento", async () => {
  await comDownvote(); await apertar("Outro"); await escrever("perdi?");
  srv.feedback = { status: 500 }; await apertar("Enviar comentário");
  await waitFor(() => expect(screen.getByText("Não conseguimos registrar sua avaliação.")).toBeTruthy());
  expect(screen.queryByText("Obrigado pelo retorno")).toBeNull(); expect(comentario()!.props.value).toBe("perdi?"); expect(marcada("Outro")).toBe(true);
});
it("com um POST em voo as pílulas e o ↑ do comentário ficam desativados", async () => {
  await comDownvote(); const g = segurar(); srv.feedback = { status: 200, seguro: g }; await apertar("Outro"); // o modo abre otimista, o POST do Outro ainda não voltou
  for (const m of MOTIVOS) expect(pilula(m)).toBeDisabled();
  await escrever("texto"); expect(screen.getByRole("button", { name: "Enviar comentário" })).toBeDisabled();
  await act(async () => { fireEvent.press(pilula("Faltou card")!); await drenar(); }); expect(feedbacks()).toHaveLength(2); // 👎 e Outro: o toque em voo não gera POST
  g.soltar(); await act(drenar);
  for (const m of MOTIVOS) expect(pilula(m)).toBeEnabled();
  expect(screen.getByRole("button", { name: "Enviar comentário" })).toBeEnabled();
});
it("👍 e retirar a nota zeram o motivo: ao voltar o 👎 nenhuma pílula vem marcada", async () => {
  await comDownvote(); await apertar("Faltou card");
  await apertar("Gostei da resposta"); expect(corpos().at(-1)).toEqual({ nota: 1 }); expect(pilula("Outro")).toBeNull();
  await apertar(NAO_GOSTEI); expect(corpos().at(-1)).toEqual({ nota: -1 }); for (const m of MOTIVOS) expect(marcada(m)).toBe(false);
  await apertar("Faltou card"); await apertar(NAO_GOSTEI); expect(corpos().at(-1)).toEqual({ nota: null }); expect(pilula("Outro")).toBeNull();
  await apertar(NAO_GOSTEI); for (const m of MOTIVOS) expect(marcada(m)).toBe(false);
});
it("só a última resposta tem pílulas: depois de uma pergunta nova, o 👎 antigo não mostra as suas", async () => {
  await comDownvote(); srv.chat = ok("Segunda"); await enviar("p2"); await waitFor(() => expect(screen.getByText("Segunda")).toBeTruthy());
  expect(pilula("Outro")).toBeNull();
});
