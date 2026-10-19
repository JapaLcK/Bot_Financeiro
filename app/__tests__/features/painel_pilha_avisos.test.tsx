import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import * as SecureStore from "expo-secure-store";
import { AccessibilityInfo, Animated, Dimensions, StyleSheet } from "react-native";
import { __resetAvisoBanco } from "@/features/painel/avisoBanco";
import { prepararCaso, resposta, rotear, S, segurar, type Rota } from "./auth_apoio";
import { guardarSessaoOf } from "./open_finance_volta_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import { guardarCredenciais } from "@/storage/secure";
import { claro } from "@/ui/tokens";

// Pilha de avisos do topo do painel. Roda a conversa pela rota, com o "servidor" guardando chave -> status das receitas (o molde do `painel_receitas.test.tsx`).
const PREVISAO = "/api/app/previsao", POST = "/api/app/previsao/receita", ERRO_NAV = "Não conseguimos abrir a tela. Tente de novo.";
const BASE = [{ chave: "extrato#sal", nome: "Salário Acme", valor: "5000.00", dia: 5 }, { chave: "extrato#free", nome: "Freela Beta", valor: "800.00", dia: 20 }];
const ST = { sim: "confirmada", nao: "negada", desfazer: "pendente" } as const;
const BANCO = ["banco_desatualizado"], dias = ["carteira_nao_confirmada", "banco_desatualizado", "conciliacao_pendente"];
const reduzir = (v: boolean) => (globalThis as unknown as { __definirReduzirMovimento: (v: boolean) => void }).__definirReduzirMovimento(v);
let marcas: Record<string, string>, extra: Record<string, Rota>;
const previsao = () => ({ ...fixture[PREVISAO], receitas_do_banco: BASE.filter((r) => r.chave in marcas).map((r) => ({ ...r, status: marcas[r.chave] })) });
function comBanco() {
  const c = fixture["/api/app/contas"], pat = fixture["/api/app/patrimonio"];
  const dado = (corpo: unknown): Rota => () => resposta(200, corpo);
  extra["/api/app/contas"] = dado({ ...c, motivos: BANCO, contas: [{ ...c.contas[0], instituicao: "Nu Pagamentos S.A.", conexao: "partial", motivos: BANCO }, c.contas[1]] });
  extra["/api/app/patrimonio"] = dado({ ...pat, motivos: BANCO, historico: pat.historico.map((h) => ({ ...h, motivos: dias })) });
  extra["/api/app/resumo-do-mes"] = dado({ ...fixture["/api/app/resumo-do-mes"], motivos: BANCO });
  extra["/api/app/mes-detalhes"] = dado({ ...fixture["/api/app/mes-detalhes"], motivos: BANCO });
}
function servidor() {
  rotear({
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null }),
    [PREVISAO]: () => resposta(200, previsao()),
    [POST]: (o) => { const c = JSON.parse(String(o.body)) as { chave: string; resposta: keyof typeof ST }; marcas[c.chave] = ST[c.resposta]; return resposta(200, previsao()); },
    ...extra,
  });
}
beforeEach(async () => {
  __resetAvisoBanco(); prepararCaso(); desligarTrava(); reduzir(false); marcas = { "extrato#sal": "pendente", "extrato#free": "pendente" }; extra = {};
  await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
});
afterEach(() => jest.restoreAllMocks());
const abrir = async (url = "/resumo", aguardar = "Disponível agora") => { servidor(); renderRouter("./app", { initialUrl: url }); await waitFor(() => expect(screen.getByText(aguardar)).toBeTruthy()); await act(drenar); };
const ponto = (k: number) => screen.getByTestId(`pilha-ponto-${k}`);
const pontos = () => screen.queryAllByTestId(/^pilha-ponto-/).length;
const ajustavel = () => screen.getByRole("adjustable", { name: "Avisos" });
const valor = () => ajustavel().props.accessibilityValue.text as string;
const titulo = () => within(screen.getByTestId("pilha-avisos")).getByRole("header").props.accessibilityLabel as string;
// Cor da borda do aviso visível: sobe do cabeçalho até o card (o único com `borderColor`).
const fio = () => { let no = within(screen.getByTestId("pilha-avisos")).getByRole("header").parent; while (no && !StyleSheet.flatten(no.props.style)?.borderColor) no = no.parent; return StyleSheet.flatten(no!.props.style).borderColor; };
const alvo44 = (no: { props: { style?: unknown } }) => { const e = StyleSheet.flatten(no.props.style as never) as { width: number; height: number }; return Math.min(e.width, e.height) >= 44; };
const acao = (nome: "increment" | "decrement") => act(async () => { fireEvent(ajustavel(), "accessibilityAction", { nativeEvent: { actionName: nome } }); await drenar(); });
const apertar = async (nome: string) => { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); };
// O card Saldo previsto é compacto: a linha das respondidas só existe na folha que o toque nele abre.
const abrirSaldoPrevisto = async () => { await act(async () => { fireEvent.press(screen.getByTestId("bloco-hero")); await drenar(); }); };
const toqueNoPonto = (k: number) => act(async () => { fireEvent.press(ponto(k)); await drenar(); });

it("a ordem é conexão pendente, banco e receitas; o adjustable lê 'Aviso N de 4' e troca de aviso sem sair da lista", async () => {
  comBanco(); await guardarSessaoOf(S); await abrir();
  await waitFor(() => expect(pontos()).toBe(4));
  const lidos: string[] = [], fios: string[] = [];
  for (let k = 0; k < 4; k++) { lidos.push(valor()); fios.push(fio()); await acao("increment"); }
  expect(fios).toEqual([claro.warning, claro.warning, claro.brand, claro.brand]); // conexão e banco = alerta (âmbar); receitas = decisão (rosa)
  expect(lidos).toEqual(["Aviso 1 de 4: Conexão com o banco pendente", "Aviso 2 de 4: Dados do banco desatualizados", "Aviso 3 de 4: Receitas do extrato", "Aviso 4 de 4: Receitas do extrato"]);
  expect(valor()).toBe("Aviso 4 de 4: Receitas do extrato"); // o último não dá a volta
  expect(screen.getByText("Freela Beta")).toBeTruthy(); // 4º = a 2ª receita, na ordem do servidor
  await acao("decrement"); await acao("decrement"); expect(titulo()).toBe("Aviso 2 de 4: Dados do banco desatualizados");
  expect(ajustavel().props.accessibilityActions.map((a: { name: string }) => a.name)).toEqual(["increment", "decrement"]);
});
it("só um aviso aparece por vez, e tocar no ponto troca (a conexão pendente ainda leva a Retomar)", async () => {
  comBanco(); await guardarSessaoOf(S); await abrir(); await waitFor(() => expect(pontos()).toBe(4));
  expect(screen.getByRole("button", { name: "Retomar conexão" })).toBeTruthy(); expect(screen.queryByText("Dados do banco desatualizados")).toBeNull(); expect(screen.queryByText("Salário Acme")).toBeNull();
  const peek = screen.getByTestId("pilha-avisos").children[1] as unknown as { props: Record<string, unknown> }; // a faixa do próximo aviso: só pista visual, escondida do leitor de tela
  expect(peek.props.accessibilityElementsHidden).toBe(true); expect(peek.props.importantForAccessibility).toBe("no-hide-descendants");
  await toqueNoPonto(1);
  expect(screen.getByText("Dados do banco desatualizados")).toBeTruthy(); expect(screen.queryByRole("button", { name: "Retomar conexão" })).toBeNull(); expect(screen.getByRole("button", { name: "Ver bancos conectados" })).toBeTruthy();
});
it("fechar o aviso do banco faz o próximo subir, sem tela vazia, e a contagem cai", async () => {
  comBanco(); await abrir(); await waitFor(() => expect(pontos()).toBe(3));
  expect(titulo()).toBe("Aviso 1 de 3: Dados do banco desatualizados");
  expect(alvo44(screen.getByRole("button", { name: "Fechar aviso" }))).toBe(true); for (let k = 0; k < 3; k++) expect(alvo44(ponto(k))).toBe(true); // alvo de 44pt
  await apertar("Fechar aviso");
  expect(screen.queryByText("Dados do banco desatualizados")).toBeNull(); expect(titulo()).toBe("Aviso 1 de 2: Receitas do extrato"); expect(screen.getByText("Salário Acme")).toBeTruthy(); expect(pontos()).toBe(2);
});
it("responder: o aviso só sai depois da releitura, o toast curto aparece sem botão e o leitor de tela ouve UMA vez", async () => {
  comBanco(); await abrir(); await apertar("Fechar aviso"); (AccessibilityInfo.announceForAccessibility as jest.Mock).mockClear();
  const g = segurar(); extra[PREVISAO] = () => g.promessa.then(() => resposta(200, previsao())); servidor();
  await apertar("Sim, é receita fixa: Salário Acme");
  expect(screen.getByText("Salário Acme")).toBeTruthy(); expect(screen.queryByTestId("toast")).toBeNull(); // POST feito, releitura no ar: nada de otimismo
  expect(screen.getByRole("button", { name: "Sim, é receita fixa: Salário Acme" }).props.accessibilityState.disabled).toBe(true);
  await act(async () => { g.soltar(); await drenar(); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("toast")).toBeTruthy());
  expect(within(screen.getByTestId("toast")).getByText("Receita confirmada")).toBeTruthy(); expect(within(screen.getByTestId("toast")).queryAllByRole("button")).toHaveLength(0);
  expect(screen.getByText("Freela Beta")).toBeTruthy(); expect(screen.queryByText("Salário Acme", { exact: true })).toBeNull(); expect(pontos()).toBe(0);
  expect((AccessibilityInfo.announceForAccessibility as jest.Mock).mock.calls.filter(([t]) => t === "Receita confirmada")).toHaveLength(1);
});
it("o selecionado some (receita respondida): cai no primeiro, sem índice fora da lista", async () => {
  comBanco(); await abrir(); await waitFor(() => expect(pontos()).toBe(3));
  await toqueNoPonto(2); expect(screen.getByText("Freela Beta")).toBeTruthy();
  await apertar("Não é receita fixa: Freela Beta");
  await waitFor(() => expect(titulo()).toBe("Aviso 1 de 2: Dados do banco desatualizados")); expect(pontos()).toBe(2); expect(screen.queryByText("Freela Beta")).toBeNull();
});
it("o selecionado some e VOLTA (Desfazer na linha 'Receitas na previsão'): a pilha continua no aviso que estava na tela, não salta de volta", async () => {
  await abrir(); await waitFor(() => expect(pontos()).toBe(2));
  await toqueNoPonto(1); expect(screen.getByText("Freela Beta")).toBeTruthy();
  await apertar("Não é receita fixa: Freela Beta");
  await waitFor(() => expect(pontos()).toBe(0)); await abrirSaldoPrevisto(); expect(screen.getByText("Receitas na previsão (1)")).toBeTruthy(); expect(screen.getByText("Salário Acme")).toBeTruthy();
  await act(async () => { fireEvent.press(screen.getByText("Receitas na previsão (1)")); await drenar(); });
  await apertar("Desfazer resposta: Freela Beta");
  await waitFor(() => expect(pontos()).toBe(2));
  expect(titulo()).toBe("Aviso 1 de 2: Receitas do extrato"); expect(screen.getByText("Salário Acme")).toBeTruthy(); expect(screen.queryByText("Freela Beta", { exact: true })).toBeNull();
});
// Receitas carregadas e os recursos do banco SEGURADOS: `soltar()` faz o banco chegar depois (sem gesto do usuário).
const abrirComBancoAtrasado = async () => {
  comBanco(); const g = segurar();
  for (const k of ["/api/app/contas", "/api/app/patrimonio", "/api/app/resumo-do-mes", "/api/app/mes-detalhes"]) { const r = extra[k]!; extra[k] = (o) => g.promessa.then(() => r(o)); }
  servidor(); renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("Salário Acme")).toBeTruthy()); await act(drenar);
  expect(pontos()).toBe(2); expect(valor()).toBe("Aviso 1 de 2: Receitas do extrato"); // só a receita carregou
  return () => act(async () => { g.soltar(); await drenar(); await drenar(); });
};
it("a receita chega ANTES do banco: o banco, que tem prioridade, passa a ser o 'Aviso 1 de 3' quando chega (sem gesto, a seleção não prende na receita)", async () => {
  const soltar = await abrirComBancoAtrasado(); await soltar();
  await waitFor(() => expect(pontos()).toBe(3)); expect(valor()).toBe("Aviso 1 de 3: Dados do banco desatualizados"); expect(screen.queryByText("Salário Acme")).toBeNull();
});
it("seleção morta e DEPOIS entra um aviso de prioridade: toca na Freela, responde Não (sobra só o Salário), o banco chega e vira o 'Aviso 1 de 2'", async () => {
  const soltar = await abrirComBancoAtrasado(); await toqueNoPonto(1); expect(screen.getByText("Freela Beta")).toBeTruthy();
  await apertar("Não é receita fixa: Freela Beta"); await waitFor(() => expect(pontos()).toBe(0)); expect(screen.getByText("Salário Acme")).toBeTruthy();
  await soltar(); await waitFor(() => expect(pontos()).toBe(2));
  expect(valor()).toBe("Aviso 1 de 2: Dados do banco desatualizados"); expect(screen.queryByText("Salário Acme")).toBeNull();
});
const puxar = (tique = 2) => act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); for (let k = 0; k < tique; k++) await drenar(); });
it("puxar para atualizar volta ao 1º aviso pela prioridade (decisão do produto), mesmo com o usuário em outro", async () => {
  comBanco(); await abrir(); await waitFor(() => expect(pontos()).toBe(3));
  await toqueNoPonto(2); expect(valor()).toBe("Aviso 3 de 3: Receitas do extrato"); expect(screen.getByText("Freela Beta")).toBeTruthy();
  await puxar(); await waitFor(() => expect(pontos()).toBe(3)); expect(valor()).toBe("Aviso 1 de 3: Dados do banco desatualizados");
});
it.each([["previsão (receitas)", true], ["banco", false]])("puxar para atualizar com %s chegando por último: sempre o banco primeiro, em qualquer ordem de chegada", async (_, soPrevisao) => {
  comBanco(); await abrir(); await waitFor(() => expect(pontos()).toBe(3)); await toqueNoPonto(2);
  const g = segurar();
  for (const k of soPrevisao ? [PREVISAO] : ["/api/app/contas", "/api/app/patrimonio", "/api/app/resumo-do-mes", "/api/app/mes-detalhes"]) { const r = extra[k] ?? (() => resposta(200, previsao())); extra[k] = (o) => g.promessa.then(() => r(o)); }
  servidor(); await puxar(); await act(async () => { g.soltar(); await drenar(); await drenar(); });
  await waitFor(() => expect(pontos()).toBe(3)); expect(valor()).toBe("Aviso 1 de 3: Dados do banco desatualizados");
});
it("responder uma receita: o 'Sim' da próxima NÃO reaproveita o nó do 'Sim' tocado (o foco do VoiceOver não fica num botão que mudou de receita)", async () => {
  await abrir(); await waitFor(() => expect(pontos()).toBe(2));
  const sim = screen.getByRole("button", { name: "Sim, é receita fixa: Salário Acme" });
  await apertar("Sim, é receita fixa: Salário Acme"); await waitFor(() => expect(screen.getByText("Freela Beta")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Sim, é receita fixa: Freela Beta" })).not.toBe(sim);
});
it("o foco do leitor vai para o título quando o visível SAI da lista (resposta), e NÃO muda no toque de ponto nem ao montar", async () => {
  const foco = AccessibilityInfo.sendAccessibilityEvent as jest.Mock; foco.mockClear();
  await abrir(); await waitFor(() => expect(pontos()).toBe(2)); expect(foco).not.toHaveBeenCalled(); // montar
  await toqueNoPonto(1); expect(foco).not.toHaveBeenCalled(); // ponto
  await apertar("Não é receita fixa: Freela Beta"); await waitFor(() => expect(foco).toHaveBeenCalledTimes(1)); expect(foco.mock.calls[0]![1]).toBe("focus"); // o visível saiu
});
it("a receita só aparece no Resumo; nas outras abas a pilha só tem o banco", async () => {
  comBanco(); await abrir("/gastos", "Para onde vai");
  expect(screen.getByText("Dados do banco desatualizados")).toBeTruthy(); expect(pontos()).toBe(0); expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText("Receitas na previsão (1)")).toBeNull();
});
it("sem banco, receita sem aviso nenhum em outra aba: nem a pilha existe (controle positivo: sem nada, nada)", async () => {
  await abrir("/gastos", "Para onde vai");
  expect(screen.queryByTestId("pilha-avisos")).toBeNull(); expect(screen.queryByLabelText("Avisos")).toBeNull(); expect(pontos()).toBe(0);
});
it("a linha 'Receitas na previsão' fica fora da pilha, na folha do Saldo previsto, e abre com Desfazer", async () => {
  marcas["extrato#free"] = "negada"; await abrir();
  const pilha = screen.getByTestId("pilha-avisos"); expect(within(pilha).queryByText(/Receitas na previsão/)).toBeNull();
  expect(screen.queryByText(/Receitas na previsão/)).toBeNull(); await abrirSaldoPrevisto(); // compacto, a linha não aparece
  const linha = screen.getByText("Receitas na previsão (1)"); expect(linha).toBeTruthy();
  await act(async () => { fireEvent.press(linha); await drenar(); }); expect(screen.getByRole("button", { name: "Desfazer resposta: Freela Beta" })).toBeTruthy();
});
it("feedback de navegação fica FORA da pilha: aparece como Banner sem 'N de N' e sem virar ponto", async () => {
  comBanco(); await abrir(); await waitFor(() => expect(pontos()).toBe(3));
  jest.spyOn(router, "push").mockImplementation(() => { throw new Error("falhou"); });
  await act(async () => { fireEvent.press(ponto(0)); await drenar(); }); await apertar("Ver bancos conectados");
  expect(screen.getByText(ERRO_NAV)).toBeTruthy(); expect(within(screen.getByTestId("pilha-avisos")).queryByText(ERRO_NAV)).toBeNull(); expect(pontos()).toBe(3);
});
it("p.aviso do provider (organização que não leu do aparelho) também fica FORA da pilha, como Banner", async () => {
  const lido = SecureStore.getItemAsync; jest.spyOn(SecureStore, "getItemAsync").mockImplementation((k, o) => /^pigbank\.painel\.v1\./.test(k) ? Promise.reject(new Error("cofre")) : lido(k, o));
  comBanco(); await abrir(); await waitFor(() => expect(pontos()).toBe(3));
  const msg = screen.getByText("Não conseguimos ler sua organização neste aparelho."); expect(msg).toBeTruthy(); expect(within(screen.getByTestId("pilha-avisos")).queryByText(msg.props.children)).toBeNull();
});
it("o 'Não deu para salvar' da receita fica FORA da pilha e não conta como aviso", async () => {
  await abrir(); extra[POST] = () => resposta(500, {}); servidor();
  await apertar("Sim, é receita fixa: Salário Acme"); await act(drenar);
  const erro = screen.getByText("Não deu para salvar. Tente de novo."); expect(erro).toBeTruthy(); expect(within(screen.getByTestId("pilha-avisos")).queryByText("Não deu para salvar. Tente de novo.")).toBeNull(); expect(pontos()).toBe(2);
});
it("previsão negada (plano sem Previsão): sem receita e sem aviso de erro; o banco segue sozinho na pilha", async () => {
  comBanco(); extra[PREVISAO] = () => resposta(403, { detail: { error: "pro_required", feature: "forecast" } }); await abrir("/resumo", "Dados do banco desatualizados");
  expect(pontos()).toBe(0); expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText("Não deu para salvar. Tente de novo.")).toBeNull();
});
it("Economizar (sem o card Saldo previsto) continua mostrando a receita no topo", async () => {
  extra["/api/app/perfil"] = () => resposta(200, { perfil: "economizar" }); await abrir();
  expect(screen.queryByText("Saldo previsto")).toBeNull(); expect(screen.getByText("Receitas do extrato")).toBeTruthy(); expect(screen.getByRole("button", { name: "Sim, é receita fixa: Salário Acme" })).toBeTruthy();
});
it("fonte ampliada: Sim e Não em coluna (com quebra) e nenhum texto do aviso do banco com limite de linhas", async () => {
  const janela = Dimensions.get("window");
  try {
    Dimensions.set({ window: { ...janela, fontScale: 2 } }); comBanco(); await abrir(); await waitFor(() => expect(pontos()).toBe(3));
    expect(screen.getByText(/precisam ser atualizados/).props.numberOfLines).toBeUndefined(); expect(screen.getByText("Dados do banco desatualizados").props.numberOfLines).toBeUndefined();
    await toqueNoPonto(1); let no = screen.getByRole("button", { name: "Sim, é receita fixa: Salário Acme" }).parent;
    while (no && !StyleSheet.flatten(no.props.style)?.flexWrap) no = no.parent;
    expect(StyleSheet.flatten(no!.props.style)).toMatchObject({ flexDirection: "column", flexWrap: "wrap" });
  } finally { Dimensions.set({ window: janela }); }
});
it("reduzir movimento: trocar de aviso não anima", async () => {
  reduzir(true); comBanco(); await abrir(); await waitFor(() => expect(pontos()).toBe(3));
  const timing = jest.spyOn(Animated, "timing"); await toqueNoPonto(1);
  expect(valor()).toBe("Aviso 2 de 3: Receitas do extrato"); expect(timing).not.toHaveBeenCalled();
});
it("controle do anterior: sem a preferência de reduzir movimento a troca anima (a subida do próximo aviso)", async () => {
  comBanco(); await abrir(); await waitFor(() => expect(pontos()).toBe(3));
  const timing = jest.spyOn(Animated, "timing"); await toqueNoPonto(1); expect(timing).toHaveBeenCalled();
});
