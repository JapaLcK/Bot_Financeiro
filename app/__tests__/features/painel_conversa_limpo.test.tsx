import { ScrollView } from "react-native";
import { renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { Screen } from "@/ui/componentes/Screen";
import { COMMON } from "@/features/painel/perguntas";
import { prepararCaso } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import { abrir, apertar, enviar, feedbacks, fim, ok, reiniciar, respostaFluxo, srv } from "./conversa_apoio";
// Chat limpo (estilo Pierre): sem título, chat novo só com a saudação, sugestões numa linha, ícones só na última resposta, contador só perto do limite.
jest.setTimeout(20_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); });
afterEach(() => { jest.restoreAllMocks(); });

it("chat novo: logo, 'Olá, {nome}' e 'Como posso te ajudar?' no centro; sem título, sem subtítulo e sem o texto de apresentação", async () => {
  await abrir();
  expect(screen.getByText("Olá, Ana")).toBeTruthy(); expect(screen.getByText("Como posso te ajudar?")).toBeTruthy();
  for (const velho of [/Converse com o Piggy$/, /Pergunte sobre gastos/, /Oi, eu sou o Piggy/]) expect(screen.queryByText(velho)).toBeNull();
  expect(screen.getByRole("button", { name: "Ocultar valores" })).toBeTruthy(); // o olho continua, sozinho no canto
});
it("a saudação some depois da 1ª pergunta", async () => {
  await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
  expect(screen.queryByText("Olá, Ana")).toBeNull(); expect(screen.queryByText("Como posso te ajudar?")).toBeNull();
});
it("as 5 sugestões do chat novo são pílulas numa ÚNICA linha que rola na horizontal", async () => {
  await abrir(); const linha = screen.getByTestId("sugestoes-piggy"), rolagem = linha.findAll((n) => (n.type as unknown) === ScrollView || n.props.horizontal === true);
  expect(rolagem.length).toBeGreaterThan(0); expect(rolagem[0]!.props.horizontal).toBe(true);
  expect(within(linha).getAllByRole("button").map((b) => b.props.accessibilityLabel ?? within(b).getByText(/./).props.children)).toEqual(COMMON.slice(0, 5).map((c) => c.ask));
});
it("os chips do servidor usam a mesma linha, só no último turno", async () => {
  srv.chat = (n) => { const f = respostaFluxo(); f.empurrar(fim(`R${n}`, { chips: [`Chip ${n}`] })); f.fechar(); return f; };
  await abrir(); await enviar("p1"); await waitFor(() => expect(within(screen.getByTestId("chips-piggy")).getByText("Chip 1")).toBeTruthy());
  expect(screen.queryByTestId("sugestoes-piggy")).toBeNull();
});
it("a aba Piggy não soma a área segura de baixo (a tab bar já cobre): Screen com sobAbas", async () => {
  await abrir(); const doChat = screen.UNSAFE_getAllByType(Screen).filter((t) => t.findAll((n) => n.props.accessibilityLabel === "Mensagem para o Piggy").length > 0);
  expect(doChat.map((t) => t.props.sobAbas)).toEqual([true]); // controle: sem a prop no conversa.tsx, o vão de insets.bottom volta
});

describe("copiar, curtir e descurtir só na última resposta", () => {
  it("2 turnos: uma vez cada ícone, na resposta de baixo; o curtir ainda grava", async () => {
    srv.chat = ok("Uma resposta");
    await abrir(); await enviar("p1"); await waitFor(() => expect(screen.getAllByLabelText("Copiar resposta")).toHaveLength(1));
    await enviar("p2"); await waitFor(() => expect(screen.getAllByText("Uma resposta")).toHaveLength(2));
    expect(screen.getAllByLabelText("Copiar resposta")).toHaveLength(1); expect(screen.getAllByLabelText("Gostei da resposta")).toHaveLength(1); expect(screen.getAllByLabelText("Não gostei da resposta")).toHaveLength(1);
    const respostas = screen.getAllByTestId("fala-piggy"); expect(within(respostas[0]!).queryByLabelText("Copiar resposta")).toBeNull(); expect(within(respostas[1]!).getByLabelText("Copiar resposta")).toBeTruthy();
    await apertar("Gostei da resposta"); expect(feedbacks()).toHaveLength(1);
  });
});

describe("contador de mensagens só perto do limite (80%) ou cheio", () => {
  it.each([[2, false], [79, false], [80, true], [99, true]])("uso %i de 100: contador %s", async (uso, aparece) => {
    srv.uso = uso; await abrir();
    if (aparece) expect(screen.getByText(`${uso} de 100 mensagens utilizadas`)).toBeTruthy(); else expect(screen.queryByText(/mensagens utilizadas/)).toBeNull();
  });
  it("cheio: o contador aparece (e as sugestões ficam desligadas)", async () => {
    srv.uso = 100; renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy());
  });
});
