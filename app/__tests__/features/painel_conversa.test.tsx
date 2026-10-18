import { KeyboardAvoidingView, StyleSheet, View } from "react-native";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { claro } from "@/ui/tokens";
import { BY_PROFILE, COMMON } from "@/features/painel/perguntas";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, apertar, bela, campo, caminho, delecoes, digitar, enviar, fim, ir, noResumo, ok, posts, pronto, reiniciar, respostaFluxo, srv, trocarParaBela, voltar, type Segurar } from "./conversa_apoio";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// O 1º caso monta o app inteiro a frio; no runner do CI isso passa dos 5 s padrão.
jest.setTimeout(20_000);

const ASK_ECONOMIZAR = "Onde eu consigo economizar este mês?", ASK_META = "Quanto falta pra minha próxima meta e quando eu chego lá?";
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); });
afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers(); });

it("pergunta à direita em bolha, fala do Piggy à esquerda, sem bolha", async () => {
 await abrir(); await enviar("Minha pergunta"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
 expect(StyleSheet.flatten(screen.getByTestId("bolha-usuario").props.style)).toMatchObject({ alignSelf: "flex-end", maxWidth: "80%" });
 expect(StyleSheet.flatten(screen.getByTestId("fala-piggy").props.style)).toMatchObject({ alignSelf: "flex-start" });
 expect(StyleSheet.flatten(screen.getByTestId("fala-piggy").props.style).backgroundColor).toBeUndefined();
 expect(screen.queryByText("Converse com o Piggy")).toBeNull(); expect(within(screen.getByTestId("fala-piggy")).queryByText("Piggy")).toBeNull(); expect(screen.getByLabelText("Mensagem para o Piggy").props.placeholder).toBe("Converse com o Piggy…"); // sem título e sem a linha "Piggy" acima da fala
 expect(screen.getByLabelText("Mensagem para o Piggy").props.maxLength).toBe(2000); expect(screen.queryByText("PigBank")).toBeNull();
});
it("o teclado desconta o Y do pai do campo na janela (measureInWindow), não um número fixo", async () => {
 const medir = jest.spyOn(View.prototype as unknown as { measureInWindow: (cb: (x: number, y: number) => void) => void }, "measureInWindow").mockImplementation((cb) => cb(0, 91));
 await abrir();
 const kav = () => screen.UNSAFE_getByType(KeyboardAvoidingView);
 await act(async () => { fireEvent(kav().parent!, "layout", { nativeEvent: { layout: { x: 0, y: 0, width: 390, height: 700 } } }); });
 expect(medir).toHaveBeenCalled(); expect(kav().props.keyboardVerticalOffset).toBe(91);
});
it("bolha do usuário é cinza (surfaceControl) e sem borda", async () => {
 await abrir(); await enviar("Minha pergunta"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
 const bolha = StyleSheet.flatten(screen.getByTestId("bolha-usuario").props.style);
 expect(bolha.backgroundColor).toBe(claro.surfaceControl); expect(bolha.borderWidth).toBeUndefined();
 });
it("o histórico do servidor não aparece ao abrir; o GET só lê a cota (limit=1)", async () => {
 srv.uso = 80; srv.historico = [{ role: "user", content: "Pergunta antiga", created_at: null }, { role: "assistant", content: "Resposta antiga", created_at: null }];
 await abrir(); expect(screen.queryByText("Pergunta antiga")).toBeNull(); expect(screen.queryByText("Resposta antiga")).toBeNull();
 const gets = fetchFalso.mock.calls.filter(([u]) => caminho(u) === "/ai/messages"); expect(gets).toHaveLength(1); expect(new URL(String(gets[0]![0])).searchParams.get("limit")).toBe("1");
 expect(screen.getByText("80 de 100 mensagens utilizadas")).toBeTruthy(); // 80% = o primeiro ponto em que o contador aparece
});
it("enviar, ir ao Resumo e voltar: chat limpo (mensagens e rascunho) e leitura refeita", async () => {
 srv.uso = 80; await abrir(); await enviar("Pergunta que some"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
 await digitar("rascunho que some"); await noResumo(); await voltar();
 expect(screen.queryByText("Pergunta que some")).toBeNull(); expect(screen.queryByText("Resposta do Piggy")).toBeNull(); expect(campo().props.value).toBe("");
 expect(fetchFalso.mock.calls.filter(([u]) => caminho(u) === "/ai/messages")).toHaveLength(2); expect(screen.getByText("81 de 100 mensagens utilizadas")).toBeTruthy();
});
it("POST lento: sair e voltar mostra o turno e o POST não é abortado", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Resposta tardia")(); };
 await abrir(); await enviar("Pergunta lenta"); const post = posts()[0]!;
 await noResumo(); g.soltar(); await act(drenar); await voltar();
 expect(post[1].signal.aborted).toBe(false); expect(screen.getByText("Pergunta lenta")).toBeTruthy(); expect(screen.getByText("Resposta tardia")).toBeTruthy();
});
it("privacidade: turno inteiro vira marcador, sem card; campo, chips e cota desligados", async () => {
 srv.uso = 90; srv.chat = () => { srv.uso++; const f = respostaFluxo(); f.empurrar({ tipo: "bloco", bloco: { card: "metas", abas: [{ mes: null, recursos: { metas: v2.metas.varios } }] } }); f.empurrar(fim("Resposta do Piggy")); f.fechar(); return f; };
 await abrir(); await enviar("metas"); await waitFor(() => expect(screen.getByTestId("bloco-metas")).toBeTruthy());
 await apertar("Ocultar valores");
 expect(screen.getAllByText("Mensagem oculta enquanto os valores estão privados.")).toHaveLength(1); expect(screen.queryByTestId("bloco-metas")).toBeNull(); expect(screen.queryByText("Resposta do Piggy")).toBeNull();
 expect(screen.queryByText(/mensagens utilizadas/)).toBeNull(); expect(campo().props.editable).toBe(false); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled();
 await apertar("Mostrar valores"); expect(screen.getByText("Resposta do Piggy")).toBeTruthy();
});
it("cota: a última mensagem bloqueia novos envios, e a cota cheia desliga chips e envio", async () => {
 srv.uso = 99; await abrir(); await enviar("Última"); await waitFor(() => expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy());
 await digitar("Mais uma"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled(); await apertar("Enviar mensagem"); expect(posts()).toHaveLength(1);
 await noResumo(); await voltar(); expect(screen.getByRole("button", { name: ASK_ECONOMIZAR })).toBeDisabled();
 await digitar("Com cota cheia"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled();
});
it.each([
 ["Gastou *R$ 50* no mercado", "R$ 50"], ["Cancelei *apagar o gasto* e *outro*", "apagar o gasto"],
])("negrito do servidor: %s", async (reply, negrito) => {
 srv.chat = ok(reply); await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getByText(negrito)).toBeTruthy());
 expect(StyleSheet.flatten(screen.getByText(negrito).props.style).fontFamily).toBe("Inter-SemiBold"); expect(screen.queryByText(/\*/)).toBeNull();
});
it.each(["5 * 3 = 15", "**", "um * dois\ne * três", "*linha\nquebrada*", "2 * 3 = 6 e 4 * 5 = 20", "* item", "**x**", "5*3*4", "2*3 e 4*5"])("asterisco que não é negrito fica como veio: %j", async (reply) => {
 srv.chat = ok(reply); await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getByTestId("fala-piggy")).toBeTruthy());
 expect(screen.getByTestId("fala-piggy").findAll((n) => n.props.style && StyleSheet.flatten(n.props.style)?.fontFamily === "Inter-SemiBold" && (n.type as unknown) === "Text")).toHaveLength(0);
 expect(screen.getByText(reply, { exact: true })).toBeTruthy();
});
it.each([["*R$ 454*", "R$ 454"], ["*a*", "a"], ["_*x*_", "x"], ["ção*x*", "x"]])("negrito colado aos asteriscos: %s", async (reply, negrito) => {
 srv.chat = ok(reply); await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getByText(negrito)).toBeTruthy());
 expect(StyleSheet.flatten(screen.getByText(negrito).props.style).fontFamily).toBe("Inter-SemiBold");
});
it("troca de conta com voo e a tela fechada: o POST da Ana é abortado, nenhum DELETE sai depois e o chat da Bela abre vazio", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Resposta da Ana")(); };
 await abrir(); await enviar("Pergunta da Ana"); const post = posts()[0]!; await noResumo(); const antes = delecoes().length;
 await trocarParaBela(); await waitFor(() => expect(screen.getByRole("button", { name: "Organizar" })).toBeTruthy());
 expect(post[1].signal.aborted).toBe(true);
 g.soltar(); await act(drenar); await act(drenar);
 expect(delecoes()).toHaveLength(antes); // nem o da Ana (conta errada) nem o da Bela (nunca abriu o chat)
 await voltar(); expect(screen.queryByText("Pergunta da Ana")).toBeNull(); expect(screen.queryByText("Resposta da Ana")).toBeNull();
 expect(delecoes().filter(([, r]) => bela(r))).toHaveLength(1); // só a abertura da Bela
});
it("troca de conta com a tela aberta e resposta concluída: o chat da Bela abre vazio", async () => {
 await abrir(); await enviar("Pergunta da Ana"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
 await trocarParaBela(); await pronto();
 expect(screen.queryByText("Pergunta da Ana")).toBeNull(); expect(screen.queryByText("Resposta do Piggy")).toBeNull(); expect(campo().props.value).toBe("");
});

// ---- cancelamento da pendência (Q7/Q8)
it("abertura: envio e chips ficam desligados até o DELETE dar 2xx", async () => {
 srv.segurarDelete = segurar(); renderRouter("./app", { initialUrl: "/piggy" });
 await waitFor(() => expect(delecoes()).toHaveLength(1)); await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
 await digitar("Pergunta"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled(); expect(screen.getByRole("button", { name: ASK_ECONOMIZAR })).toBeDisabled();
 await apertar("Enviar mensagem"); expect(posts()).toHaveLength(0);
 srv.segurarDelete.soltar(); await pronto();
 expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeEnabled(); expect(screen.getByRole("button", { name: ASK_ECONOMIZAR })).toBeEnabled();
});
it("DELETE com 404 ou rede caída: aviso com Tentar novamente, envio bloqueado até o 2xx", async () => {
 srv.statusPendencia = 404; renderRouter("./app", { initialUrl: "/piggy" });
 await waitFor(() => expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy());
 await digitar("Pergunta"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled(); expect(screen.getByRole("button", { name: ASK_META })).toBeDisabled(); expect(posts()).toHaveLength(0);
 srv.statusPendencia = 200; const antes = delecoes().length; await apertar("Tentar novamente"); await pronto(); expect(delecoes()).toHaveLength(antes + 1);
 expect(screen.queryByRole("button", { name: "Tentar novamente" })).toBeNull(); await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
});
it("saída sem voo: exatamente 1 DELETE se enviou; abrir e sair sem enviar não manda", async () => {
 await abrir(); expect(delecoes()).toHaveLength(1); await noResumo(); expect(delecoes()).toHaveLength(1); // abriu e saiu sem enviar
 await voltar(); expect(delecoes()).toHaveLength(2); await enviar("Pergunta"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
 await noResumo(); await act(drenar); expect(delecoes()).toHaveLength(3);
});
it("saída com voo: nenhum DELETE enquanto o POST voa, 1 quando termina com a tela fechada, e a nota ao voltar", async () => {
 const g = segurar(); srv.cancelada = "Apagar o gasto do mercado"; srv.chat = async () => { await g.promessa; return ok("Posso apagar o gasto?")(); };
 await abrir(); await enviar("apaga meu último gasto"); await noResumo(); await act(drenar); expect(delecoes()).toHaveLength(1);
 g.soltar(); await act(drenar); await act(drenar); expect(delecoes()).toHaveLength(2);
 await voltar(); expect(screen.getByText("apaga meu último gasto")).toBeTruthy(); expect(screen.getByText("Posso apagar o gasto?")).toBeTruthy();
 expect(screen.getByText(/Cancelei Apagar o gasto do mercado porque você saiu do chat\./)).toBeTruthy();
});
it("saída com voo e nada a cancelar (cancelada null): sem nota", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Resposta")(); };
 await abrir(); await enviar("pergunta"); await noResumo(); g.soltar(); await act(drenar); await act(drenar); await voltar();
 expect(screen.getByText("Resposta")).toBeTruthy(); expect(screen.queryByText(/Cancelei/)).toBeNull();
});
it("voltar antes da resposta: a resposta aparece com a tela aberta e a conclusão não manda DELETE", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Resposta enfim")(); };
 await abrir(); await enviar("pergunta"); const post = posts()[0]!; await noResumo(); await voltar();
 const antes = delecoes().length; g.soltar(); await act(drenar); await act(drenar);
 expect(screen.getByText("Resposta enfim")).toBeTruthy(); expect(delecoes()).toHaveLength(antes); expect(post[1].signal.aborted).toBe(false);
});
it("reabrir com POST em voo: nenhum DELETE sai até o POST acabar; com a tela aberta, a saída seguinte cancela", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Posso apagar?")(); };
 await abrir(); await enviar("apaga meu último gasto"); await noResumo(); await voltar(); await act(drenar);
 expect(delecoes()).toHaveLength(1); // só o da primeira abertura; a reabertura só leu a cota
 g.soltar(); await act(drenar); await act(drenar); expect(screen.getByText("Posso apagar?")).toBeTruthy(); expect(delecoes()).toHaveLength(1);
 await noResumo(); await act(drenar); expect(delecoes()).toHaveLength(2);
});
it("reabrir com POST em voo e a cota falhando: Tentar novamente também não manda DELETE", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Resposta")(); };
 await abrir(); await enviar("pergunta"); await noResumo(); srv.statusGet = 500; await ir("/piggy"); await act(drenar);
 srv.statusGet = 200; await apertar("Tentar novamente"); await act(drenar);
 expect(delecoes()).toHaveLength(1); g.soltar(); await act(drenar);
});
it("reabrir com POST em voo: o GET da cota que resolve depois do POST não sobrescreve o uso", async () => {
 srv.limite = 100; srv.uso = 98; const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Resposta")(); };
 await abrir(); await enviar("pergunta"); await noResumo(); srv.segurarGet = segurar(); await ir("/piggy"); await act(drenar); // GET lê 98 e fica preso
 g.soltar(); await act(drenar); await act(drenar); expect(screen.getByText("Resposta")).toBeTruthy(); // POST grava 99
 const sg = srv.segurarGet; srv.segurarGet = null; sg.soltar(); await act(drenar); await act(drenar);
 expect(screen.getByText("99 de 100 mensagens utilizadas")).toBeTruthy();
});
it("voo que termina com a tela aberta de novo: a saída seguinte manda o DELETE", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Resposta enfim")(); };
 await abrir(); await enviar("pergunta"); await noResumo(); await voltar();
 g.soltar(); await act(drenar); await act(drenar); const antes = delecoes().length;
 await noResumo(); await act(drenar); expect(delecoes()).toHaveLength(antes + 1);
});
it("POST que falha com a tela fechada: o texto velho não volta ao campo", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return resposta(500, { detail: "x" }); };
 await abrir(); await enviar("Pergunta velha"); await noResumo(); g.soltar(); await act(drenar); await act(drenar); await voltar();
 expect(campo().props.value).toBe("");
});
it("POST que falha com a tela aberta: o que o usuário digitou no voo não é sobrescrito", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return resposta(500, { detail: "x" }); };
 await abrir(); await enviar("Pergunta"); await digitar("outra coisa"); g.soltar(); await act(drenar); await act(drenar);
 expect(campo().props.value).toBe("outra coisa");
});
it("POST que falha com a tela fechada e reaberta: o texto da abertura anterior não volta", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return resposta(500, { detail: "x" }); };
 await abrir(); await enviar("Pergunta velha"); await noResumo(); await voltar(); g.soltar(); await act(drenar); await act(drenar);
 expect(campo().props.value).toBe(""); // voltou à mesma abertura nova: a falha é de outra abertura
});
it("ocultar valores durante o DELETE bloqueante: nenhum POST sai", async () => {
 srv.chat = (n) => n === 1 ? resposta(500, { detail: "x" }) : ok("Não devia")();
 await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 srv.segurarDelete = segurar(); await apertar("Enviar mensagem"); await apertar("Ocultar valores"); srv.segurarDelete.soltar(); await act(drenar); await act(drenar);
 expect(posts()).toHaveLength(1);
});
// guardas do provider chamadas pelo onPress capturado (a UI desabilita o botão; aqui o handler velho é chamado de qualquer jeito)
const onPressEnviar = () => screen.UNSAFE_getByProps({ accessibilityLabel: "Enviar mensagem", accessibilityRole: "button" }).props.onPress as () => void;
it("enviar chamado com o DELETE da abertura em curso: nenhum POST", async () => {
 srv.segurarDelete = segurar(); renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(delecoes()).toHaveLength(1));
 await digitar("Pergunta"); const press = onPressEnviar(); await act(async () => { press(); await drenar(); });
 expect(posts()).toHaveLength(0);
});
it("enviar chamado com o DELETE bloqueante em curso (cota já lida): não abre outro DELETE", async () => {
 srv.chat = () => resposta(500, { detail: "x" });
 await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 srv.segurarDelete = segurar(); await apertar("Enviar mensagem"); const total = delecoes().length;
 await act(async () => { onPressEnviar()(); await drenar(); }); expect(delecoes()).toHaveLength(total);
 srv.segurarDelete.soltar(); await act(drenar);
});
it("enviar chamado de novo com um POST em voo: só um POST", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Resposta")(); };
 await abrir(); await digitar("Pergunta"); const press = onPressEnviar();
 await act(async () => { press(); await drenar(); }); await act(async () => { press(); await drenar(); });
 expect(posts()).toHaveLength(1); g.soltar(); await act(drenar);
});
it("segundo plano com voo: o POST não é abortado e o turno aparece ao voltar", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Resposta do segundo plano")(); };
 await abrir(); await enviar("pergunta"); const post = posts()[0]!;
 await appVai("background"); expect(post[1].signal.aborted).toBe(false);
 g.soltar(); await act(drenar); await appVai("active"); await pronto();
 expect(screen.getByText("pergunta")).toBeTruthy(); expect(screen.getByText("Resposta do segundo plano")).toBeTruthy();
});
it("depois de um POST com erro, o DELETE sai antes do próximo envio; se ele falhar, nada é enviado", async () => {
 srv.chat = (n) => n === 1 ? resposta(500, { detail: "x" }) : ok("Agora foi")();
 await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 expect(campo().props.value).toBe("Pergunta que falha"); // o texto volta ao campo
 const apos = delecoes().length; expect(apos).toBe(1);
 srv.statusPendencia = 404; await apertar("Enviar mensagem");
 expect(delecoes()).toHaveLength(2); expect(posts()).toHaveLength(1); expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy();
 srv.statusPendencia = 200; await apertar("Tentar novamente"); await waitFor(() => expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeEnabled());
 const total = fetchFalso.mock.calls.length; await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Agora foi")).toBeTruthy());
 expect(fetchFalso.mock.calls.slice(total).filter(([u]) => caminho(u) === "/ai/chat/stream")).toHaveLength(1);
});
it("depois de um POST com erro, o próximo envio faz DELETE bloqueante antes do POST", async () => {
 srv.chat = (n) => n === 1 ? resposta(500, { detail: "x" }) : ok("Agora foi")();
 await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Agora foi")).toBeTruthy());
 const ordem = fetchFalso.mock.calls.map(([u, r]) => `${r.method ?? "GET"} ${caminho(u)}`).filter((x) => x === "DELETE /ai/pending" || x === "POST /ai/chat/stream");
 expect(ordem).toEqual(["DELETE /ai/pending", "POST /ai/chat/stream", "DELETE /ai/pending", "POST /ai/chat/stream"]);
});

it("erro do POST que chega com o DELETE da reabertura em voo: o próximo envio ainda faz DELETE antes do POST", async () => {
 const g = segurar(); srv.chat = async (n) => { if (n === 1) { await g.promessa; return resposta(500, { detail: "x" }); } return ok("executou")(); };
 await abrir(); await enviar("apaga meu último gasto"); await noResumo();
 srv.segurarDelete = segurar(); await ir("/piggy"); await act(drenar);
 g.soltar(); await act(drenar); await act(drenar); // o POST falha com o DELETE da reabertura ainda em voo
 const sd = srv.segurarDelete; srv.segurarDelete = null; sd.soltar(); await act(drenar); await act(drenar);
 await pronto();
 const antes = delecoes().length; await enviar("sim"); await waitFor(() => expect(screen.getByText("executou")).toBeTruthy());
 expect(delecoes().length).toBeGreaterThan(antes);
 const ordem = fetchFalso.mock.calls.map(([u, r]) => `${r.method ?? "GET"} ${caminho(u)}`).filter((x) => x === "DELETE /ai/pending" || x === "POST /ai/chat/stream");
 expect(ordem.slice(-2)).toEqual(["DELETE /ai/pending", "POST /ai/chat/stream"]);
});

// ---- máquina estados x eventos (scratchpad/maquina-chat.md): DELETE de saída em voo, cota relida, Tentar novamente
const bloqueado = async (d: Segurar) => { // o handler velho é chamado de qualquer jeito: nada de POST até o DELETE preso terminar
 await act(drenar); await act(drenar); await digitar("sim"); await act(async () => { onPressEnviar()(); await drenar(); }); expect(posts()).toHaveLength(1); d.soltar(); await act(drenar); await act(drenar); await waitFor(() => expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeEnabled());
};
it("sair após turno concluído e reabrir: o envio só libera quando o DELETE da saída terminar (senão ele apagaria a confirmação nova)", async () => {
 await abrir(); await enviar("p1"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy()); const d = segurar(); srv.segurarDelete = d; srv.soUma = true; await noResumo(); await ir("/piggy"); await bloqueado(d); await apertar("Enviar mensagem"); await waitFor(() => expect(posts()).toHaveLength(2));
});
it("turno que termina com a tela fechada (sair do finally) e reabrir: o envio espera esse DELETE", async () => {
 const g = segurar(), d = segurar(); srv.chat = async () => { await g.promessa; return ok("Posso apagar?")(); }; await abrir(); await enviar("apaga"); await noResumo(); srv.segurarDelete = d; srv.soUma = true; g.soltar(); await act(drenar); await act(drenar); await ir("/piggy"); await bloqueado(d);
});
it("depois de um erro do POST, cota relida cheia: o próximo envio não sai", async () => {
 srv.chat = (n) => n === 1 ? resposta(500, { detail: "x" }) : ok("Não devia")(); await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 srv.uso = srv.limite; await apertar("Enviar mensagem"); await act(drenar); expect(posts()).toHaveLength(1); await waitFor(() => expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy());
});
it("Tentar novamente depois que o voo acabou: não manda DELETE (apagaria a confirmação mostrada)", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Posso apagar?")(); }; await abrir(); await enviar("apaga"); await noResumo(); srv.statusGet = 500; await ir("/piggy"); await act(drenar); g.soltar(); await act(drenar); await act(drenar);
 expect(screen.getByText("Posso apagar?")).toBeTruthy(); const antes = delecoes().length; srv.statusGet = 200; await apertar("Tentar novamente"); await act(drenar); expect(delecoes()).toHaveLength(antes);
});

const semEnviar = async () => { await digitar("sim"); await apertar("Enviar mensagem"); expect(posts()).toHaveLength(0); }; // o DELETE velho ainda preso: o envio tem de esperar
const soltarEEnviar = async (d: Segurar) => { d.soltar(); await act(drenar); await act(drenar); await waitFor(() => expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeEnabled()); await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy()); expect(srv.armada).toBe(true); };
it("DELETE da abertura preso, sair e reabrir: o envio espera o DELETE velho (abortá-lo no cliente não o desfaz, e ele apagaria a confirmação)", async () => {
 const d = segurar(); srv.segurarDelete = d; srv.soUma = true; renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(delecoes()).toHaveLength(1));
 await noResumo(); await ir("/piggy"); await act(drenar); await act(drenar); await semEnviar(); await soltarEEnviar(d);
});
it("Tentar novamente com o DELETE da abertura ainda preso: o novo preparar espera o velho e não o perde", async () => {
 const d = segurar(); srv.segurarDelete = d; srv.soUma = true; srv.statusGet = 500; renderRouter("./app", { initialUrl: "/piggy" });
 await waitFor(() => expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy()); srv.statusGet = 200; await apertar("Tentar novamente"); await act(drenar); await semEnviar(); await soltarEEnviar(d);
});
it("Tentar novamente depois de abertura com voo e nova abertura sem voo: o retry MANDA o DELETE (semDeleteUlt volta a false)", async () => {
 const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Posso apagar?")(); }; await abrir(); await enviar("apaga"); await noResumo(); await ir("/piggy"); g.soltar(); await act(drenar); await act(drenar); await noResumo();
 srv.statusPendencia = 404; await ir("/piggy"); await waitFor(() => expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy());
 srv.statusPendencia = 200; const antes = delecoes().length; await apertar("Tentar novamente"); await act(drenar); expect(delecoes()).toHaveLength(antes + 1);
});

it("estado vazio: 5 chips do perfil primeiro, depois COMMON", async () => {
 await abrir(); const esperados = COMMON.slice(0, 5).map((c) => c.ask);
 for (const ask of esperados) expect(screen.getByRole("button", { name: ask })).toBeTruthy();
 expect(screen.queryByRole("button", { name: COMMON[5]!.ask })).toBeNull();
});
it("estado vazio com perfil: as sugestões do perfil vêm antes das comuns", async () => {
 srv.perfilDoServidor = "dividas"; await abrir();
 const esperados = [...BY_PROFILE.dividas!, ...COMMON].slice(0, 5).map((c) => c.ask);
 for (const ask of esperados) expect(screen.getByRole("button", { name: ask })).toBeTruthy();
 expect(screen.queryByRole("button", { name: COMMON[2]!.ask })).toBeNull();
});
