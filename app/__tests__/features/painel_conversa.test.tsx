import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { KeyboardAvoidingView, StyleSheet, View } from "react-native";
import { claro } from "@/ui/tokens";
import { guardarCredenciais } from "@/storage/secure";
import { mesAtual, mesesAnteriores, nomeMes } from "@/features/painel/catalogo";
import { BY_PROFILE, COMMON } from "@/features/painel/perguntas";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
// O 1º caso monta o app inteiro a frio; no runner do CI isso passa dos 5 s padrão.
jest.setTimeout(20_000);

type Segurar = ReturnType<typeof segurar>;
const ASK_ECONOMIZAR = "Onde eu consigo economizar este mês?", ASK_META = "Quanto falta pra minha próxima meta e quando eu chego lá?";
let perfilDoServidor = "padrao", uso: number, limite: number, historico: unknown[], statusPendencia: number, cancelada: string | null, segurarDelete: Segurar | null, soUma: boolean, armada: boolean, segurarGet: Segurar | null, statusGet: number, chat: (n: number) => Promise<Response> | Response, nChat: number;
const caminho = (u: unknown) => new URL(String(u)).pathname;
const delecoes = () => fetchFalso.mock.calls.filter(([u, r]) => caminho(u) === "/ai/pending" && r.method === "DELETE");
const posts = () => fetchFalso.mock.calls.filter(([u]) => caminho(u) === "/ai/chat");
const bela = (req: RequestInit) => (req.headers as Record<string, string>).Authorization === "Bearer access-bela";
function servidor() {
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const p = caminho(url);
  if (p === "/auth/me") return resposta(200, bela(req) ? { user_id: 2, display_name: "Bela", app_access: true } : { user_id: 1, display_name: "Ana", app_access: true });
  if (p === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (/^\/open-finance\/\d\/limite$/.test(p)) return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (p === "/api/app/perfil") return resposta(200, { perfil: perfilDoServidor });
  if (/^\/(goals|cards|installments|analytics|insights)\/2\//.test(p)) return resposta(200, (fixture as Record<string, unknown>)[p.replace("/2/", "/1/")]);
  if (p === "/ai/pending") {
   if (segurarDelete) { const g = segurarDelete; if (soUma) segurarDelete = null; await g.promessa; } // soUma: segura só o 1º DELETE seguinte
   armada = false; return statusPendencia === 200 ? resposta(200, { cancelada }) : resposta(statusPendencia, { detail: "indisponível" });
  }
  if (p === "/ai/chat") { const r = await chat(++nChat); armada = r.ok; return r; } // armada: pendência viva no servidor; só o DELETE que o servidor resolve a desarma
  if (p === "/ai/messages") { const lido = { used: uso, limit: limite }; if (segurarGet) await segurarGet.promessa; if (statusGet !== 200) return resposta(statusGet, { detail: "x" }); return resposta(200, { messages: historico, usage: lido }); }
  return resposta(200, (fixture as Record<string, unknown>)[p] ?? {});
 });
 const impl = fetchFalso.getMockImplementation()!; // fetch real: abortar rejeita no CLIENTE, mas o servidor segue processando o pedido já enviado
 fetchFalso.mockImplementation((u: string, r: RequestInit) => { const srv = impl(u, r), sg = r?.signal; return !sg ? srv : new Promise((res, rej) => { const err = () => rej(Object.assign(new Error("Aborted"), { name: "AbortError" })); if (sg.aborted) err(); sg.addEventListener("abort", err); srv.then(res, rej); }); });
}
const ok = (reply: string) => () => { uso++; return resposta(200, { reply, usage: { used: uso, limit: limite } }); };
beforeEach(async () => {
 prepararCaso(); desligarTrava(); perfilDoServidor = "padrao"; uso = 2; limite = 100; historico = []; statusPendencia = 200; cancelada = null; segurarDelete = null; soUma = false; armada = false; segurarGet = null; statusGet = 200; nChat = 0; chat = ok("Resposta do Piggy");
 await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor();
});
afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers(); });
const campo = () => screen.getByLabelText("Mensagem para o Piggy");
const apertar = async (nome: string | RegExp) => { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); };
const digitar = async (t: string) => { await act(async () => { fireEvent.changeText(campo(), t); await drenar(); }); };
const enviar = async (t: string) => { await digitar(t); await apertar("Enviar mensagem"); };
const ir = async (rota: "/piggy" | "/resumo" | "/gastos") => { await act(async () => { router.navigate(rota); await drenar(); }); };
const abrir = async () => { renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(screen.getByText(/de \d+ mensagens utilizadas/)).toBeTruthy()); };
const noResumo = async () => { await ir("/resumo"); await waitFor(() => expect(screen.getByRole("button", { name: "Organizar" })).toBeTruthy()); };
const voltar = async () => { await ir("/piggy"); await waitFor(() => expect(screen.getByText(/de \d+ mensagens utilizadas/)).toBeTruthy()); };
const trocarParaBela = async () => { await appVai("background"); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); await appVai("active"); };

it("pergunta à direita em bolha, fala do Piggy à esquerda, sem bolha", async () => {
 await abrir(); await enviar("Minha pergunta"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
 expect(StyleSheet.flatten(screen.getByTestId("bolha-usuario").props.style)).toMatchObject({ alignSelf: "flex-end", maxWidth: "80%" });
 expect(StyleSheet.flatten(screen.getByTestId("fala-piggy").props.style)).toMatchObject({ alignSelf: "flex-start" });
 expect(StyleSheet.flatten(screen.getByTestId("fala-piggy").props.style).backgroundColor).toBeUndefined();
 expect(screen.getByText("Converse com o Piggy")).toBeTruthy(); expect(screen.getByLabelText("Mensagem para o Piggy").props.placeholder).toBe("Converse com o Piggy…");
 expect(screen.getByLabelText("Mensagem para o Piggy").props.maxLength).toBe(2000); expect(screen.queryByText("PigBank")).toBeNull();
});
it("o teclado desconta o Y do pai do campo na janela (measureInWindow), não um número fixo", async () => {
 const medir = jest.spyOn(View.prototype as unknown as { measureInWindow: (cb: (x: number, y: number) => void) => void }, "measureInWindow").mockImplementation((cb) => cb(0, 91));
 await abrir();
 const kav = () => screen.UNSAFE_getByType(KeyboardAvoidingView);
 await act(async () => { fireEvent(kav().parent!, "layout", { nativeEvent: { layout: { x: 0, y: 0, width: 390, height: 700 } } }); });
 expect(medir).toHaveBeenCalled(); expect(kav().props.keyboardVerticalOffset).toBe(91);
});
it("bolha do usuário é cinza (surfaceControl) e sem borda; nome do Piggy é neutro", async () => {
 await abrir(); await enviar("Minha pergunta"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
 const bolha = StyleSheet.flatten(screen.getByTestId("bolha-usuario").props.style);
 expect(bolha.backgroundColor).toBe(claro.surfaceControl); expect(bolha.borderWidth).toBeUndefined();
 expect(StyleSheet.flatten(screen.getByTestId("fala-piggy-nome").props.style).color).not.toBe("#FF2D8E");
});
it("o histórico do servidor não aparece ao abrir; o GET só lê a cota (limit=1)", async () => {
 historico = [{ role: "user", content: "Pergunta antiga", created_at: null }, { role: "assistant", content: "Resposta antiga", created_at: null }];
 await abrir(); expect(screen.queryByText("Pergunta antiga")).toBeNull(); expect(screen.queryByText("Resposta antiga")).toBeNull();
 const gets = fetchFalso.mock.calls.filter(([u]) => caminho(u) === "/ai/messages"); expect(gets).toHaveLength(1); expect(new URL(String(gets[0]![0])).searchParams.get("limit")).toBe("1");
 expect(screen.getByText("2 de 100 mensagens utilizadas")).toBeTruthy();
});
it("enviar, ir ao Resumo e voltar: chat limpo (mensagens e rascunho) e leitura refeita", async () => {
 await abrir(); await enviar("Pergunta que some"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
 await digitar("rascunho que some"); await noResumo(); await voltar();
 expect(screen.queryByText("Pergunta que some")).toBeNull(); expect(screen.queryByText("Resposta do Piggy")).toBeNull(); expect(campo().props.value).toBe("");
 expect(fetchFalso.mock.calls.filter(([u]) => caminho(u) === "/ai/messages")).toHaveLength(2); expect(screen.getByText("3 de 100 mensagens utilizadas")).toBeTruthy();
});
it("POST lento: sair e voltar mostra o turno e o POST não é abortado", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta tardia")(); };
 await abrir(); await enviar("Pergunta lenta"); const post = posts()[0]!;
 await noResumo(); g.soltar(); await act(drenar); await voltar();
 expect(post[1].signal.aborted).toBe(false); expect(screen.getByText("Pergunta lenta")).toBeTruthy(); expect(screen.getByText("Resposta tardia")).toBeTruthy();
});
it("privacidade: turno inteiro vira marcador, sem card; campo, chips e cota desligados", async () => {
 await abrir(); await apertar(ASK_META); await waitFor(() => expect(screen.getByText("Metas e caixinhas")).toBeTruthy());
 await apertar("Ocultar valores");
 expect(screen.getAllByText("Mensagem oculta enquanto os valores estão privados.")).toHaveLength(1); expect(screen.queryByText("Metas e caixinhas")).toBeNull(); expect(screen.queryByText("Resposta do Piggy")).toBeNull();
 expect(screen.queryByText(/mensagens utilizadas/)).toBeNull(); expect(campo().props.editable).toBe(false); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled();
 await apertar("Mostrar valores"); expect(screen.getByText("Resposta do Piggy")).toBeTruthy();
});
it("cota: a última mensagem bloqueia novos envios, e a cota cheia desliga chips e envio", async () => {
 uso = 99; await abrir(); await enviar("Última"); await waitFor(() => expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy());
 await digitar("Mais uma"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled(); await apertar("Enviar mensagem"); expect(posts()).toHaveLength(1);
 await noResumo(); await voltar(); expect(screen.getByRole("button", { name: ASK_ECONOMIZAR })).toBeDisabled();
 await digitar("Com cota cheia"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled();
});
it.each([
 ["Gastou *R$ 50* no mercado", "R$ 50"], ["Cancelei *apagar o gasto* e *outro*", "apagar o gasto"],
])("negrito do servidor: %s", async (reply, negrito) => {
 chat = ok(reply); await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getByText(negrito)).toBeTruthy());
 expect(StyleSheet.flatten(screen.getByText(negrito).props.style).fontFamily).toBe("Inter-SemiBold"); expect(screen.queryByText(/\*/)).toBeNull();
});
it.each(["5 * 3 = 15", "**", "um * dois\ne * três", "*linha\nquebrada*", "2 * 3 = 6 e 4 * 5 = 20", "* item", "**x**", "5*3*4", "2*3 e 4*5"])("asterisco que não é negrito fica como veio: %j", async (reply) => {
 chat = ok(reply); await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getByTestId("fala-piggy")).toBeTruthy());
 expect(screen.getByTestId("fala-piggy").findAll((n) => n.props.style && StyleSheet.flatten(n.props.style)?.fontFamily === "Inter-SemiBold" && (n.type as unknown) === "Text" && n.props.children !== "Piggy")).toHaveLength(0);
 expect(screen.getByText(reply, { exact: true })).toBeTruthy();
});
it.each([["*R$ 454*", "R$ 454"], ["*a*", "a"], ["_*x*_", "x"], ["ção*x*", "x"]])("negrito colado aos asteriscos: %s", async (reply, negrito) => {
 chat = ok(reply); await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getByText(negrito)).toBeTruthy());
 expect(StyleSheet.flatten(screen.getByText(negrito).props.style).fontFamily).toBe("Inter-SemiBold");
});
it("troca de conta com voo e a tela fechada: o POST da Ana é abortado, nenhum DELETE sai depois e o chat da Bela abre vazio", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta da Ana")(); };
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
 await trocarParaBela(); await waitFor(() => expect(screen.getByText(/de \d+ mensagens utilizadas/)).toBeTruthy());
 expect(screen.queryByText("Pergunta da Ana")).toBeNull(); expect(screen.queryByText("Resposta do Piggy")).toBeNull(); expect(campo().props.value).toBe("");
});

// ---- cancelamento da pendência (Q7/Q8)
it("abertura: envio e chips ficam desligados até o DELETE dar 2xx", async () => {
 segurarDelete = segurar(); renderRouter("./app", { initialUrl: "/piggy" });
 await waitFor(() => expect(delecoes()).toHaveLength(1)); await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
 await digitar("Pergunta"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled(); expect(screen.getByRole("button", { name: ASK_ECONOMIZAR })).toBeDisabled();
 await apertar("Enviar mensagem"); expect(posts()).toHaveLength(0);
 segurarDelete.soltar(); await waitFor(() => expect(screen.getByText(/de \d+ mensagens utilizadas/)).toBeTruthy());
 expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeEnabled(); expect(screen.getByRole("button", { name: ASK_ECONOMIZAR })).toBeEnabled();
});
it("DELETE com 404 ou rede caída: aviso com Tentar novamente, envio bloqueado até o 2xx", async () => {
 statusPendencia = 404; renderRouter("./app", { initialUrl: "/piggy" });
 await waitFor(() => expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy());
 await digitar("Pergunta"); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled(); expect(screen.getByRole("button", { name: ASK_META })).toBeDisabled(); expect(posts()).toHaveLength(0);
 statusPendencia = 200; const antes = delecoes().length; await apertar("Tentar novamente"); await waitFor(() => expect(screen.getByText(/de \d+ mensagens utilizadas/)).toBeTruthy()); expect(delecoes()).toHaveLength(antes + 1);
 expect(screen.queryByRole("button", { name: "Tentar novamente" })).toBeNull(); await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
});
it("saída sem voo: exatamente 1 DELETE se enviou; abrir e sair sem enviar não manda", async () => {
 await abrir(); expect(delecoes()).toHaveLength(1); await noResumo(); expect(delecoes()).toHaveLength(1); // abriu e saiu sem enviar
 await voltar(); expect(delecoes()).toHaveLength(2); await enviar("Pergunta"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
 await noResumo(); await act(drenar); expect(delecoes()).toHaveLength(3);
});
it("saída com voo: nenhum DELETE enquanto o POST voa, 1 quando termina com a tela fechada, e a nota ao voltar", async () => {
 const g = segurar(); cancelada = "Apagar o gasto do mercado"; chat = async () => { await g.promessa; return ok("Posso apagar o gasto?")(); };
 await abrir(); await enviar("apaga meu último gasto"); await noResumo(); await act(drenar); expect(delecoes()).toHaveLength(1);
 g.soltar(); await act(drenar); await act(drenar); expect(delecoes()).toHaveLength(2);
 await voltar(); expect(screen.getByText("apaga meu último gasto")).toBeTruthy(); expect(screen.getByText("Posso apagar o gasto?")).toBeTruthy();
 expect(screen.getByText(/Cancelei Apagar o gasto do mercado porque você saiu do chat\./)).toBeTruthy();
});
it("saída com voo e nada a cancelar (cancelada null): sem nota", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta")(); };
 await abrir(); await enviar("pergunta"); await noResumo(); g.soltar(); await act(drenar); await act(drenar); await voltar();
 expect(screen.getByText("Resposta")).toBeTruthy(); expect(screen.queryByText(/Cancelei/)).toBeNull();
});
it("voltar antes da resposta: a resposta aparece com a tela aberta e a conclusão não manda DELETE", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta enfim")(); };
 await abrir(); await enviar("pergunta"); const post = posts()[0]!; await noResumo(); await voltar();
 const antes = delecoes().length; g.soltar(); await act(drenar); await act(drenar);
 expect(screen.getByText("Resposta enfim")).toBeTruthy(); expect(delecoes()).toHaveLength(antes); expect(post[1].signal.aborted).toBe(false);
});
it("reabrir com POST em voo: nenhum DELETE sai até o POST acabar; com a tela aberta, a saída seguinte cancela", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Posso apagar?")(); };
 await abrir(); await enviar("apaga meu último gasto"); await noResumo(); await voltar(); await act(drenar);
 expect(delecoes()).toHaveLength(1); // só o da primeira abertura; a reabertura só leu a cota
 g.soltar(); await act(drenar); await act(drenar); expect(screen.getByText("Posso apagar?")).toBeTruthy(); expect(delecoes()).toHaveLength(1);
 await noResumo(); await act(drenar); expect(delecoes()).toHaveLength(2);
});
it("reabrir com POST em voo e a cota falhando: Tentar novamente também não manda DELETE", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta")(); };
 await abrir(); await enviar("pergunta"); await noResumo(); statusGet = 500; await ir("/piggy"); await act(drenar);
 statusGet = 200; await apertar("Tentar novamente"); await act(drenar);
 expect(delecoes()).toHaveLength(1); g.soltar(); await act(drenar);
});
it("reabrir com POST em voo: o GET da cota que resolve depois do POST não sobrescreve o uso", async () => {
 limite = 100; uso = 98; const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta")(); };
 await abrir(); await enviar("pergunta"); await noResumo(); segurarGet = segurar(); await ir("/piggy"); await act(drenar); // GET lê 98 e fica preso
 g.soltar(); await act(drenar); await act(drenar); expect(screen.getByText("Resposta")).toBeTruthy(); // POST grava 99
 const sg = segurarGet; segurarGet = null; sg.soltar(); await act(drenar); await act(drenar);
 expect(screen.getByText("99 de 100 mensagens utilizadas")).toBeTruthy();
});
it("voo que termina com a tela aberta de novo: a saída seguinte manda o DELETE", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta enfim")(); };
 await abrir(); await enviar("pergunta"); await noResumo(); await voltar();
 g.soltar(); await act(drenar); await act(drenar); const antes = delecoes().length;
 await noResumo(); await act(drenar); expect(delecoes()).toHaveLength(antes + 1);
});
it("POST que falha com a tela fechada: o texto velho não volta ao campo", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return resposta(500, { detail: "x" }); };
 await abrir(); await enviar("Pergunta velha"); await noResumo(); g.soltar(); await act(drenar); await act(drenar); await voltar();
 expect(campo().props.value).toBe("");
});
it("POST que falha com a tela aberta: o que o usuário digitou no voo não é sobrescrito", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return resposta(500, { detail: "x" }); };
 await abrir(); await enviar("Pergunta"); await digitar("outra coisa"); g.soltar(); await act(drenar); await act(drenar);
 expect(campo().props.value).toBe("outra coisa");
});
it("POST que falha com a tela fechada e reaberta: o texto da abertura anterior não volta", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return resposta(500, { detail: "x" }); };
 await abrir(); await enviar("Pergunta velha"); await noResumo(); await voltar(); g.soltar(); await act(drenar); await act(drenar);
 expect(campo().props.value).toBe(""); // voltou à mesma abertura nova: a falha é de outra abertura
});
it("ocultar valores durante o DELETE bloqueante: nenhum POST sai", async () => {
 chat = (n) => n === 1 ? resposta(500, { detail: "x" }) : ok("Não devia")();
 await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 segurarDelete = segurar(); await apertar("Enviar mensagem"); await apertar("Ocultar valores"); segurarDelete.soltar(); await act(drenar); await act(drenar);
 expect(posts()).toHaveLength(1);
});
// guardas do provider chamadas pelo onPress capturado (a UI desabilita o botão; aqui o handler velho é chamado de qualquer jeito)
const onPressEnviar = () => screen.UNSAFE_getByProps({ accessibilityLabel: "Enviar mensagem", accessibilityRole: "button" }).props.onPress as () => void;
it("enviar chamado com o DELETE da abertura em curso: nenhum POST", async () => {
 segurarDelete = segurar(); renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(delecoes()).toHaveLength(1));
 await digitar("Pergunta"); const press = onPressEnviar(); await act(async () => { press(); await drenar(); });
 expect(posts()).toHaveLength(0);
});
it("enviar chamado com o DELETE bloqueante em curso (cota já lida): não abre outro DELETE", async () => {
 chat = () => resposta(500, { detail: "x" });
 await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 segurarDelete = segurar(); await apertar("Enviar mensagem"); const total = delecoes().length;
 await act(async () => { onPressEnviar()(); await drenar(); }); expect(delecoes()).toHaveLength(total);
 segurarDelete.soltar(); await act(drenar);
});
it("enviar chamado de novo com um POST em voo: só um POST", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta")(); };
 await abrir(); await digitar("Pergunta"); const press = onPressEnviar();
 await act(async () => { press(); await drenar(); }); await act(async () => { press(); await drenar(); });
 expect(posts()).toHaveLength(1); g.soltar(); await act(drenar);
});
it("segundo plano com voo: o POST não é abortado e o turno aparece ao voltar", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta do segundo plano")(); };
 await abrir(); await enviar("pergunta"); const post = posts()[0]!;
 await appVai("background"); expect(post[1].signal.aborted).toBe(false);
 g.soltar(); await act(drenar); await appVai("active"); await waitFor(() => expect(screen.getByText(/de \d+ mensagens utilizadas/)).toBeTruthy());
 expect(screen.getByText("pergunta")).toBeTruthy(); expect(screen.getByText("Resposta do segundo plano")).toBeTruthy();
});
it("depois de um POST com erro, o DELETE sai antes do próximo envio; se ele falhar, nada é enviado", async () => {
 chat = (n) => n === 1 ? resposta(500, { detail: "x" }) : ok("Agora foi")();
 await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 expect(campo().props.value).toBe("Pergunta que falha"); // o texto volta ao campo
 const apos = delecoes().length; expect(apos).toBe(1);
 statusPendencia = 404; await apertar("Enviar mensagem");
 expect(delecoes()).toHaveLength(2); expect(posts()).toHaveLength(1); expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy();
 statusPendencia = 200; await apertar("Tentar novamente"); await waitFor(() => expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeEnabled());
 const total = fetchFalso.mock.calls.length; await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Agora foi")).toBeTruthy());
 expect(fetchFalso.mock.calls.slice(total).filter(([u]) => caminho(u) === "/ai/chat")).toHaveLength(1);
});
it("depois de um POST com erro, o próximo envio faz DELETE bloqueante antes do POST", async () => {
 chat = (n) => n === 1 ? resposta(500, { detail: "x" }) : ok("Agora foi")();
 await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Agora foi")).toBeTruthy());
 const ordem = fetchFalso.mock.calls.map(([u, r]) => `${r.method ?? "GET"} ${caminho(u)}`).filter((x) => x === "DELETE /ai/pending" || x === "POST /ai/chat");
 expect(ordem).toEqual(["DELETE /ai/pending", "POST /ai/chat", "DELETE /ai/pending", "POST /ai/chat"]);
});

it("erro do POST que chega com o DELETE da reabertura em voo: o próximo envio ainda faz DELETE antes do POST", async () => {
 const g = segurar(); chat = async (n) => { if (n === 1) { await g.promessa; return resposta(500, { detail: "x" }); } return ok("executou")(); };
 await abrir(); await enviar("apaga meu último gasto"); await noResumo();
 segurarDelete = segurar(); await ir("/piggy"); await act(drenar);
 g.soltar(); await act(drenar); await act(drenar); // o POST falha com o DELETE da reabertura ainda em voo
 const sd = segurarDelete; segurarDelete = null; sd.soltar(); await act(drenar); await act(drenar);
 await waitFor(() => expect(screen.getByText(/de \d+ mensagens utilizadas/)).toBeTruthy());
 const antes = delecoes().length; await enviar("sim"); await waitFor(() => expect(screen.getByText("executou")).toBeTruthy());
 expect(delecoes().length).toBeGreaterThan(antes);
 const ordem = fetchFalso.mock.calls.map(([u, r]) => `${r.method ?? "GET"} ${caminho(u)}`).filter((x) => x === "DELETE /ai/pending" || x === "POST /ai/chat");
 expect(ordem.slice(-2)).toEqual(["DELETE /ai/pending", "POST /ai/chat"]);
});

// ---- máquina estados x eventos (scratchpad/maquina-chat.md): DELETE de saída em voo, cota relida, Tentar novamente
const bloqueado = async (d: Segurar) => { // o handler velho é chamado de qualquer jeito: nada de POST até o DELETE preso terminar
 await act(drenar); await act(drenar); await digitar("sim"); await act(async () => { onPressEnviar()(); await drenar(); }); expect(posts()).toHaveLength(1); d.soltar(); await act(drenar); await act(drenar); await waitFor(() => expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeEnabled());
};
it("sair após turno concluído e reabrir: o envio só libera quando o DELETE da saída terminar (senão ele apagaria a confirmação nova)", async () => {
 await abrir(); await enviar("p1"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy()); const d = segurar(); segurarDelete = d; soUma = true; await noResumo(); await ir("/piggy"); await bloqueado(d); await apertar("Enviar mensagem"); await waitFor(() => expect(posts()).toHaveLength(2));
});
it("turno que termina com a tela fechada (sair do finally) e reabrir: o envio espera esse DELETE", async () => {
 const g = segurar(), d = segurar(); chat = async () => { await g.promessa; return ok("Posso apagar?")(); }; await abrir(); await enviar("apaga"); await noResumo(); segurarDelete = d; soUma = true; g.soltar(); await act(drenar); await act(drenar); await ir("/piggy"); await bloqueado(d);
});
it("depois de um erro do POST, cota relida cheia: o próximo envio não sai", async () => {
 chat = (n) => n === 1 ? resposta(500, { detail: "x" }) : ok("Não devia")(); await abrir(); await enviar("Pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
 uso = limite; await apertar("Enviar mensagem"); await act(drenar); expect(posts()).toHaveLength(1); await waitFor(() => expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy());
});
it("Tentar novamente depois que o voo acabou: não manda DELETE (apagaria a confirmação mostrada)", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Posso apagar?")(); }; await abrir(); await enviar("apaga"); await noResumo(); statusGet = 500; await ir("/piggy"); await act(drenar); g.soltar(); await act(drenar); await act(drenar);
 expect(screen.getByText("Posso apagar?")).toBeTruthy(); const antes = delecoes().length; statusGet = 200; await apertar("Tentar novamente"); await act(drenar); expect(delecoes()).toHaveLength(antes);
});

const semEnviar = async () => { await digitar("sim"); await apertar("Enviar mensagem"); expect(posts()).toHaveLength(0); }; // o DELETE velho ainda preso: o envio tem de esperar
const soltarEEnviar = async (d: Segurar) => { d.soltar(); await act(drenar); await act(drenar); await waitFor(() => expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeEnabled()); await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy()); expect(armada).toBe(true); };
it("DELETE da abertura preso, sair e reabrir: o envio espera o DELETE velho (abortá-lo no cliente não o desfaz, e ele apagaria a confirmação)", async () => {
 const d = segurar(); segurarDelete = d; soUma = true; renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(delecoes()).toHaveLength(1));
 await noResumo(); await ir("/piggy"); await act(drenar); await act(drenar); await semEnviar(); await soltarEEnviar(d);
});
it("Tentar novamente com o DELETE da abertura ainda preso: o novo preparar espera o velho e não o perde", async () => {
 const d = segurar(); segurarDelete = d; soUma = true; statusGet = 500; renderRouter("./app", { initialUrl: "/piggy" });
 await waitFor(() => expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy()); statusGet = 200; await apertar("Tentar novamente"); await act(drenar); await semEnviar(); await soltarEEnviar(d);
});
it("Tentar novamente depois de abertura com voo e nova abertura sem voo: o retry MANDA o DELETE (semDeleteUlt volta a false)", async () => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Posso apagar?")(); }; await abrir(); await enviar("apaga"); await noResumo(); await ir("/piggy"); g.soltar(); await act(drenar); await act(drenar); await noResumo();
 statusPendencia = 404; await ir("/piggy"); await waitFor(() => expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy());
 statusPendencia = 200; const antes = delecoes().length; await apertar("Tentar novamente"); await act(drenar); expect(delecoes()).toHaveLength(antes + 1);
});

// ---- cards (Q4/Q9)
it("chip com assunto conhecido mostra o widget real abaixo da resposta; o mesmo texto digitado não", async () => {
 await abrir(); await apertar(ASK_META); await waitFor(() => expect(screen.getByText("Metas e caixinhas")).toBeTruthy());
 expect(screen.getByText("Resposta do Piggy")).toBeTruthy(); expect(screen.getByText("Viagem")).toBeTruthy();
 await noResumo(); await voltar(); await enviar(ASK_META); await waitFor(() => expect(screen.getAllByText("Resposta do Piggy")).toHaveLength(1));
 expect(screen.queryByText("Metas e caixinhas")).toBeNull();
});
it("o card do chat abre na Folha com o título do widget, fecha, e some ao sair da aba", async () => {
 await abrir(); await apertar(ASK_META); await waitFor(() => expect(screen.getByText("Metas e caixinhas")).toBeTruthy());
 expect(screen.queryByRole("header", { name: "Metas e caixinhas" })).toBeNull();
 await apertar("Abrir Metas e caixinhas"); expect(screen.getByRole("header", { name: "Metas e caixinhas" })).toBeTruthy();
 await apertar("Fechar"); expect(screen.queryByRole("header", { name: "Metas e caixinhas" })).toBeNull();
 await apertar("Abrir Metas e caixinhas"); await noResumo(); await voltar(); expect(screen.queryByRole("header", { name: "Metas e caixinhas" })).toBeNull();
});
it("chip com POST que falha não mostra card", async () => {
 chat = () => resposta(500, { detail: "x" }); await abrir(); await apertar(ASK_META);
 await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy()); expect(screen.queryByText("Metas e caixinhas")).toBeNull();
});
const comOutroMes = async (ask: string) => {
 const g = segurar(); chat = async () => { await g.promessa; return ok("Resposta do mês")(); };
 await abrir(); await apertar(ask); await ir("/gastos"); await waitFor(() => expect(screen.getByTestId("painel-conta")).toBeTruthy());
 await apertar(nomeMes(mesAtual()).slice(0, 3)); await apertar(nomeMes(mesesAnteriores(mesAtual())[1]!));
 g.soltar(); await act(drenar); await voltar(); await waitFor(() => expect(screen.getByText("Resposta do mês")).toBeTruthy());
};
it("mês do painel diferente do atual: o card de categorias some", async () => { await comOutroMes(ASK_ECONOMIZAR); expect(screen.queryByText("Para onde vai")).toBeNull(); });
it("mês do painel diferente do atual: o card de metas continua", async () => { await comOutroMes(ASK_META); expect(screen.getByText("Metas e caixinhas")).toBeTruthy(); });
it("no mês atual o card de categorias aparece e a linha navega para o Extrato sem erro de contexto", async () => {
 const push = jest.spyOn(router, "push"); await abrir(); await apertar(ASK_ECONOMIZAR); await waitFor(() => expect(screen.getByText("Para onde vai")).toBeTruthy());
 await apertar(/Alimentação/); expect(push).toHaveBeenCalledWith({ pathname: "/extrato", params: { categoria: "Alimentação" } });
});

it("estado vazio: 5 chips do perfil primeiro, depois COMMON", async () => {
 await abrir(); const esperados = COMMON.slice(0, 5).map((c) => c.ask);
 for (const ask of esperados) expect(screen.getByRole("button", { name: ask })).toBeTruthy();
 expect(screen.queryByRole("button", { name: COMMON[5]!.ask })).toBeNull();
});
it("estado vazio com perfil: as sugestões do perfil vêm antes das comuns", async () => {
 perfilDoServidor = "dividas"; await abrir();
 const esperados = [...BY_PROFILE.dividas!, ...COMMON].slice(0, 5).map((c) => c.ask);
 for (const ask of esperados) expect(screen.getByRole("button", { name: ask })).toBeTruthy();
 expect(screen.queryByRole("button", { name: COMMON[2]!.ask })).toBeNull();
});
