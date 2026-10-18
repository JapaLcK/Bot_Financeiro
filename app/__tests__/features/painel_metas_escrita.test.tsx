import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Alert, StyleSheet } from "react-native";
import { isoDoSeletor } from "@/features/painel/metasEscrita";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// A folha de meta (PR 2 do app v2): criar, editar e apagar pela aba Metas, com servidor falso. O seletor de data é nativo: o dublê expõe o `onChange`.
jest.mock("@react-native-community/datetimepicker", () => ({ __esModule: true, default: (p: object) => jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "seletor-data", ...p }) }));
jest.setTimeout(30_000);
type Corpo = Record<string, unknown>;
const TODAS = (v2 as unknown as { metas: { varios: { metas: Record<string, unknown>[]; custo_mensal: unknown }; vazio: unknown; sem_custo: unknown } }).metas;
let lista: unknown, escrita: (rota: string, corpo: Corpo) => Response | Promise<Response>, investido: () => Response, metasGet: (() => Response) | null;
const posts = () => fetchFalso.mock.calls.filter(([, o]) => (o as RequestInit)?.method === "POST").map(([u, o]) => ({ rota: new URL(String(u)).pathname, corpo: JSON.parse(String((o as RequestInit).body)) as Corpo }));
const gets = () => fetchFalso.mock.calls.filter(([u, o]) => new URL(String(u)).pathname === "/api/app/metas" && (o as RequestInit).method === "GET").length;
function servidor() {
  fetchFalso.mockImplementation(async (url: string, o: RequestInit) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    if (path === "/api/app/perfil") return resposta(200, { perfil: "padrao" });
    if (path === "/api/app/metas" && o.method === "GET") return metasGet ? metasGet() : resposta(200, lista);
    if (path === "/api/app/investido") return investido();
    if (path === "/api/app/contas") { const c = (fixture as unknown as Record<string, { contas: object[] }>)[path]!; return resposta(200, { ...c, contas: [...c.contas, { ...c.contas[0], id: 3, instituicao: "Wise", nome: "Dólar", moeda: "USD" }] }); } // uma conta em outra moeda: a meta é em reais, ela não entra na lista de fontes
    if (o.method === "POST" && path.startsWith("/api/app/metas")) return escrita(path, JSON.parse(String(o.body)));
    return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
  });
}
beforeEach(async () => {
  prepararCaso(); desligarTrava(); lista = TODAS.varios; metasGet = null; escrita = () => resposta(200, { id: 99 }); investido = () => resposta(200, v2.investido.com_banco);
  await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor(); jest.spyOn(Alert, "alert").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());
const botao = (nome: string | RegExp) => screen.getByRole("button", { name: nome });
const apertar = async (nome: string | RegExp) => { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); };
const abrirAba = async () => { renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(screen.getByTestId("bloco-metas")).toBeTruthy()); await waitFor(() => expect(gets()).toBeGreaterThan(0)); await act(drenar); };
const folha = async (titulo: string) => { await waitFor(() => expect(screen.getByText(titulo)).toBeTruthy()); await waitFor(() => expect(screen.getByLabelText(/^Nome/)).toBeTruthy()); };
const digitar = (rotulo: RegExp | string, valor: string) => fireEvent.changeText(screen.getByLabelText(rotulo), valor);
const digitarValor = (centavos: string) => fireEvent.changeText(screen.getByTestId("valor-input"), centavos);
const fechou = (titulo: string) => waitFor(() => expect(screen.queryByText(titulo)).toBeNull());
const salvar = () => apertar("Salvar");

describe("criar", () => {
  it("a folha ROLA: o corpo dela é um ScrollView (Fonte com muitas contas, 24 blocos ou fonte grande passam da tela; sem rolagem Salvar/Apagar ficam inalcançáveis)", async () => {
    lista = TODAS.vazio; await abrirAba();
    await apertar("Criar meta"); await folha("Nova meta");
    expect(screen.getByTestId("sheet-conteudo").type).toBe("RCTScrollView");
  });
  it("meta nova: manda nome, tipo, fonte e alvo em texto exato; fecha a folha e a aba relê (a meta nova aparece)", async () => {
    lista = TODAS.vazio; await abrirAba();
    await apertar("Criar meta"); await folha("Nova meta");
    digitar(/^Nome/, "Viagem"); digitarValor("123450");
    lista = { ...TODAS.varios, metas: [{ ...TODAS.varios.metas[2]!, id: 99, nome: "Viagem" }] };
    await salvar();
    await waitFor(() => expect(posts()).toEqual([{ rota: "/api/app/metas", corpo: { nome: "Viagem", tipo: "meta", fonte: { tipo: "investido" }, alvo: "1234.50" } }]));
    await fechou("Nova meta"); await waitFor(() => expect(screen.getByTestId("meta-99")).toBeTruthy()); // a releitura da aba é a prova de que o foco voltou
  });
  it("Montar reserva (aba sem reserva): 6 blocos, custo mensal, manda `meses` e nunca `alvo`", async () => {
    lista = TODAS.vazio; await abrirAba();
    await apertar("Montar reserva de emergência"); await folha("Montar reserva");
    expect(screen.getByText("6 meses")).toBeTruthy(); expect(screen.getAllByTestId("bloco-vazio").length).toBeGreaterThanOrEqual(6);
    await apertar("Mais um mês"); await apertar("Mais um mês"); await salvar();
    await waitFor(() => expect(posts()).toEqual([{ rota: "/api/app/metas", corpo: { nome: "Reserva de emergência", tipo: "reserva", fonte: { tipo: "investido" }, meses: 8 } }]));
  });
  it("prazo: 'Sem prazo' abre o seletor nativo; a data sai AAAA-MM-DD; prazo no passado não envia", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta");
    digitar(/^Nome/, "Casa"); digitarValor("100000");
    expect(screen.queryByTestId("seletor-data")).toBeNull(); await apertar(/Prazo/); // a linha vira o seletor
    const pega = (d: Date) => act(async () => { screen.getByTestId("seletor-data").props.onChange({}, d); await drenar(); });
    await pega(new Date(2020, 0, 5, 9)); await salvar();
    expect(posts()).toHaveLength(0); expect(screen.getByText("De hoje em diante")).toBeTruthy();
    await pega(new Date(2030, 0, 5, 9)); await salvar();
    await waitFor(() => expect(posts()[0]!.corpo).toMatchObject({ data_alvo: "2030-01-05" }));
  });
  it("nome vazio e valor 0: erro no campo, nada é enviado (positivo: preenchido, envia)", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta");
    await salvar(); expect(posts()).toHaveLength(0); expect(screen.getByLabelText(/^Nome, erro: 1 a 60 letras/)).toBeTruthy(); expect(screen.getByText("Informe o valor")).toBeTruthy();
    digitar(/^Nome/, "Moto"); digitarValor("500000"); await salvar(); await waitFor(() => expect(posts()).toHaveLength(1));
  });
});

describe("trava de duplo envio", () => {
  it("dois toques no MESMO tick (antes de renderizar o `pendente`) gravam uma meta só", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    const preso = segurar(); escrita = async () => { await preso.promessa; return resposta(200, { id: 99 }); };
    const salvarBtn = botao("Salvar");
    await act(async () => { fireEvent.press(salvarBtn); fireEvent.press(salvarBtn); await drenar(); }); // act externo: o 2º toque vê o render antigo
    expect(posts()).toHaveLength(1);
    preso.soltar(); await act(drenar); await fechou("Nova meta");
    expect(posts()).toHaveLength(1);
  });
  it("positivo: depois de uma FALHA conhecida (400) a trava solta e o toque seguinte grava (sem reenvio automático)", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    escrita = () => resposta(400, { error: { code: "valor_invalido", message: "x" } }); await salvar();
    await waitFor(() => expect(screen.getByText("Não foi possível salvar.")).toBeTruthy()); expect(posts()).toHaveLength(1); // nada reenviou sozinho
    await act(drenar); await act(drenar); expect(posts()).toHaveLength(1);
    escrita = () => resposta(200, { id: 7 }); await salvar(); await waitFor(() => expect(posts()).toHaveLength(2));
  });
  it("resultado DESCONHECIDO (500): a folha trava, sem Salvar (outro toque duplicaria a meta) e sem editar; só 'Fechar', que fecha e relê a lista", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    escrita = () => resposta(500, undefined); await salvar();
    await waitFor(() => expect(screen.getByText("Pode ter salvo. Confira a lista.")).toBeTruthy()); expect(posts()).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Salvar" })).toBeNull(); digitar(/^Nome/, "Outro"); expect(screen.getByLabelText(/^Nome/).props.value).toBe("Viagem"); // editar não destrava
    await act(async () => { fireEvent.press(screen.getByRole("tab", { name: /Reserva/ })); await drenar(); }); expect(screen.queryByRole("button", { name: "Salvar" })).toBeNull(); // nem o seletor Meta/Reserva (o único controle sem `desativado`)
    const antes = gets(); await apertar("Fechar"); await fechou("Nova meta"); await waitFor(() => expect(gets()).toBeGreaterThan(antes)); expect(posts()).toHaveLength(1);
  });
  it("rede caída (fetch rejeita): 'Pode ter salvo', um POST só", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    escrita = () => { throw new TypeError("Network request failed"); }; await salvar();
    await waitFor(() => expect(screen.getByText("Pode ter salvo. Confira a lista.")).toBeTruthy()); expect(posts()).toHaveLength(1);
  });
});

describe("editar", () => {
  it("abre sem mudança: Salvar desligado; mudar e voltar ao valor original: desligado; mudar o nome: manda SÓ o nome", async () => {
    await abrirAba(); await apertar(/^Notebook novo.*editar$/); await folha("Editar meta");
    const desligado = () => botao("Salvar").props.accessibilityState.disabled;
    expect(desligado()).toBe(true);
    digitar(/^Nome/, "Notebook"); expect(desligado()).toBe(false); digitar(/^Nome/, "Notebook novo"); expect(desligado()).toBe(true);
    digitar(/^Nome/, "Notebook"); await salvar();
    await waitFor(() => expect(posts()).toEqual([{ rota: "/api/app/metas/editar", corpo: { id: 3, nome: "Notebook" } }]));
  });
  it("valor: manda `alvo` em texto exato e nunca `meses`", async () => {
    await abrirAba(); await apertar(/^Notebook novo.*editar$/); await folha("Editar meta"); digitarValor("900050"); await salvar();
    await waitFor(() => expect(posts()[0]!.corpo).toEqual({ id: 3, alvo: "9000.50" }));
  });
  it("reserva: mais um mês manda só `meses`", async () => {
    await abrirAba(); await apertar(/^Reserva de emergência.*editar$/); await folha("Editar meta"); await apertar("Mais um mês"); await salvar();
    await waitFor(() => expect(posts()[0]!.corpo).toEqual({ id: 1, meses: 7 }));
  });
  it("prazo: 'Tirar prazo' manda `data_alvo: null`; prazo já vencido e intocado não bloqueia salvar o nome", async () => {
    await abrirAba(); await apertar(/^Viagem pro Japão.*editar$/); await folha("Editar meta"); await apertar("Tirar prazo"); await salvar();
    await waitFor(() => expect(posts()[0]!.corpo).toEqual({ id: 2, data_alvo: null }));
  });
  it("meta com prazo vencido (2026-08-15): trocar só o nome envia, sem erro de data", async () => {
    await abrirAba(); await apertar(/^Intercâmbio.*editar$/); await folha("Editar meta"); digitar(/^Nome/, "Intercâmbio 2"); await salvar();
    await waitFor(() => expect(posts()[0]!.corpo).toEqual({ id: 8, nome: "Intercâmbio 2" }));
  });
  it("reserva sem custo: 'Definir o valor eu mesmo' vira meta comum (tipo + alvo, sem meses)", async () => {
    lista = { ...TODAS.sem_custo as object, metas: [{ ...TODAS.varios.metas[0]!, alvo: null, atual: null, falta: null, percentual: null }] }; await abrirAba();
    await apertar(/^Reserva de emergência.*editar$/); await folha("Editar meta"); await apertar("Definir o valor eu mesmo"); digitarValor("1000000"); await salvar();
    await waitFor(() => expect(posts()[0]!.corpo).toEqual({ id: 1, tipo: "meta", alvo: "10000.00" }));
  });
});

describe("fonte", () => {
  it("com o plano: 'Tudo investido', bancos (Investido) e contas BRL (Saldo); escolher um banco manda a fonte e marca ✓", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "X"); digitarValor("100000");
    await apertar(/Fonte/); expect(screen.getByText("Nubank")).toBeTruthy(); expect(screen.getByText("XP")).toBeTruthy(); expect(screen.getAllByText("Tudo investido").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Saldo").length).toBeGreaterThan(0); expect(screen.getByText(/Conta principal/)).toBeTruthy(); expect(screen.queryByText(/Wise/)).toBeNull(); await apertar("XP"); await salvar(); // a conta em USD fica FORA (o positivo: a BRL está)
    await waitFor(() => expect(posts()[0]!.corpo).toMatchObject({ fonte: { tipo: "banco", banco: "XP" } }));
  });
  it("sem o plano (403 pro_required): só contas, a escolha fica por fazer e Salvar pede a fonte sem enviar", async () => {
    investido = () => resposta(403, { error: { code: "pro_required", message: "Forbidden" } }); lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta");
    digitar(/^Nome/, "X"); digitarValor("100000"); await salvar(); expect(posts()).toHaveLength(0); expect(screen.getByText("Escolha a fonte")).toBeTruthy();
    await apertar(/Fonte/); expect(screen.queryByText("Tudo investido")).toBeNull(); expect(screen.queryByText("Nubank")).toBeNull(); await apertar(/Conta principal/);
    await salvar(); await waitFor(() => expect(posts()[0]!.corpo).toMatchObject({ fonte: { tipo: "conta", conta: 1 } }));
  });
  it("banco que sumiu da lista (meta 10): a escolha aparece como 'Não disponível' em vez de sumir em silêncio, e não vai no diff", async () => {
    await abrirAba(); await apertar(/^Dinheiro que sumiu.*editar$/); await folha("Editar meta"); await apertar(/Fonte/);
    expect(screen.getByText("Não disponível")).toBeTruthy(); digitar(/^Nome/, "Outro nome"); await salvar();
    await waitFor(() => expect(posts()[0]!.corpo).toEqual({ id: 10, nome: "Outro nome" }));
  });
});

describe("erros do servidor", () => {
  const erro = (status: number, code: string, details?: unknown) => resposta(status, { error: { code, message: "x", ...(details ? { details } : {}) } });
  it("409 reserva_ja_existe: volta ao formulário como Meta, rascunho mantido, sem escolha de Reserva", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Montar reserva de emergência"); await folha("Montar reserva");
    escrita = () => erro(409, "reserva_ja_existe"); await salvar();
    await waitFor(() => expect(screen.getByText("Você já tem uma reserva.")).toBeTruthy());
    expect(screen.getByText("Nova meta")).toBeTruthy(); expect(screen.getByLabelText(/^Nome/).props.value).toBe("Reserva de emergência"); expect(screen.queryByText("Reserva")).toBeNull(); expect(screen.getByTestId("valor-input")).toBeTruthy();
  });
  it("404 no editar: avisa 'Essa meta não existe mais' e fecha ao confirmar; 404 no apagar: fecha como feito, sem aviso", async () => {
    await abrirAba(); await apertar(/^Notebook novo.*editar$/); await folha("Editar meta"); digitar(/^Nome/, "Outro");
    escrita = () => erro(404, "meta_nao_encontrada"); await salvar();
    await waitFor(() => expect(Alert.alert).toHaveBeenCalledWith("Essa meta não existe mais.", undefined, [expect.objectContaining({ text: "OK" })]));
    await act(async () => { (jest.mocked(Alert.alert).mock.calls.at(-1)![2]![0]!.onPress as () => void)(); await drenar(); }); await fechou("Editar meta");
    jest.mocked(Alert.alert).mockClear(); await apertar(/^Moto.*editar$/); await folha("Editar meta"); await apertar("Apagar");
    await act(async () => { (jest.mocked(Alert.alert).mock.calls[0]![2]!.find((b) => b.style === "destructive")!.onPress as () => void)(); await drenar(); });
    await fechou("Editar meta"); expect(Alert.alert).toHaveBeenCalledTimes(1); // só o "Apagar meta?"
  });
  it("422 com `loc`: o erro cai no campo do servidor (nome), sem texto do servidor", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "X"); digitarValor("100000");
    escrita = () => erro(422, "validation_error", [{ loc: ["body", "nome"], msg: "texto cru em inglês", type: "x" }]); await salvar();
    await waitFor(() => expect(screen.getByLabelText(/^Nome, erro: 1 a 60 letras/)).toBeTruthy()); expect(screen.queryByText("texto cru em inglês")).toBeNull();
  });
  it("403 pro_required na escrita: avisa e tira as opções de investimento", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "X"); digitarValor("100000");
    escrita = () => erro(403, "pro_required"); await salvar(); await waitFor(() => expect(screen.getByText("Seu plano não inclui investimentos.")).toBeTruthy());
    await apertar(/Fonte/); expect(screen.queryByText("Tudo investido")).toBeNull();
  });
  it("403 password_required: a mensagem de criar a senha (a mesma do Open Finance)", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "X"); digitarValor("100000");
    escrita = () => erro(403, "password_required"); await salvar(); await waitFor(() => expect(screen.getByText(/crie a senha da sua conta/)).toBeTruthy());
  });
});

describe("apagar", () => {
  it("o botão é o `danger` do Button com o rótulo CENTRALIZADO, igual ao do Salvar (não uma linha de lista com o texto à esquerda)", async () => {
    await abrirAba(); await apertar(/^Notebook novo.*editar$/); await folha("Editar meta");
    const caixa = (nome: string) => StyleSheet.flatten(botao(nome).children.map((c) => (typeof c === "string" ? null : c.props.style)).find(Boolean));
    expect(caixa("Apagar")).toMatchObject({ alignItems: "center", justifyContent: "center", minHeight: 44 }); expect(caixa("Apagar").backgroundColor).not.toBe(caixa("Salvar").backgroundColor);
    expect(caixa("Salvar")).toMatchObject({ alignItems: "center", justifyContent: "center" });
  });
  it("pede confirmação nativa; Cancelar não grava; Apagar grava {id} uma vez e fecha", async () => {
    await abrirAba(); await apertar(/^Notebook novo.*editar$/); await folha("Editar meta"); await apertar("Apagar");
    expect(Alert.alert).toHaveBeenCalledWith("Apagar meta?", undefined, expect.arrayContaining([expect.objectContaining({ text: "Cancelar", style: "cancel" }), expect.objectContaining({ text: "Apagar", style: "destructive" })])); expect(posts()).toHaveLength(0);
    const destrutivo = jest.mocked(Alert.alert).mock.calls[0]![2]!.find((b) => b.style === "destructive")!.onPress as () => void;
    await act(async () => { destrutivo(); destrutivo(); await drenar(); }); // duplo toque no alerta: uma escrita
    await waitFor(() => expect(posts()).toEqual([{ rota: "/api/app/metas/apagar", corpo: { id: 3 } }])); await fechou("Editar meta");
  });
  it("Salvar na edição grava direto, sem Alert", async () => {
    await abrirAba(); await apertar(/^Notebook novo.*editar$/); await folha("Editar meta"); digitar(/^Nome/, "Novo"); await salvar();
    await waitFor(() => expect(posts()).toHaveLength(1)); expect(Alert.alert).not.toHaveBeenCalled();
  });
});

describe("a folha", () => {
  it("fechar sem salvar volta à aba e relê as metas; nada é gravado", async () => {
    await abrirAba(); const antes = gets(); await apertar("Criar meta"); await folha("Nova meta"); const comFolha = gets();
    act(() => router.back()); await fechou("Nova meta"); await waitFor(() => expect(gets()).toBeGreaterThan(comFolha)); expect(comFolha).toBeGreaterThan(antes); expect(posts()).toHaveLength(0);
  });
  it("sem conseguir carregar as metas: 'Não deu para carregar' e 'Tentar de novo' refaz a leitura", async () => {
    await abrirAba(); lista = { erro: true }; await apertar("Criar meta");
    await waitFor(() => expect(screen.getByText("Não deu para carregar.")).toBeTruthy()); lista = TODAS.vazio; await apertar("Tentar de novo"); await folha("Nova meta");
  });
});

describe("a folha some com a gravação em voo (deslizar para fechar)", () => {
  const gravandoPreso = async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    const preso = segurar(); escrita = async () => { await preso.promessa; return resposta(200, { id: 99 }); };
    await act(async () => { fireEvent.press(botao("Salvar")); await drenar(); }); expect(posts()).toHaveLength(1);
    act(() => router.back()); await fechou("Nova meta"); await act(drenar); // deslizou
    return preso;
  };
  it("a gravação termina DEPOIS de a aba reler ao focar: a aba relê de novo e mostra a meta (senão a lista fica velha e o usuário recria)", async () => {
    const preso = await gravandoPreso(); const antes = gets();
    lista = { ...TODAS.varios, metas: [{ ...TODAS.varios.metas[2]!, id: 99, nome: "Viagem" }] };
    preso.soltar(); await act(drenar); await waitFor(() => expect(screen.getByTestId("meta-99")).toBeTruthy()); expect(gets()).toBeGreaterThan(antes);
  });
  it("a leitura da aba ainda em voo (nasceu antes da gravação) quando ela termina: a releitura forçada a supera, a lista não fica velha", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    const presoPost = segurar(); escrita = async () => { await presoPost.promessa; return resposta(200, { id: 99 }); };
    await act(async () => { fireEvent.press(botao("Salvar")); await drenar(); });
    const base = fetchFalso.getMockImplementation()!, presaLeitura = segurar(); let velha = true;
    fetchFalso.mockImplementation(async (url: string, o: RequestInit) => { if (velha && new URL(url).pathname === "/api/app/metas" && o.method === "GET") { velha = false; const antiga = lista; await presaLeitura.promessa; return resposta(200, antiga); } return base(url, o); });
    act(() => router.back()); await fechou("Nova meta"); await act(drenar); expect(velha).toBe(false); // a leitura do foco está presa, com a lista de ANTES
    lista = { ...TODAS.varios, metas: [{ ...TODAS.varios.metas[2]!, id: 99, nome: "Viagem" }] };
    presoPost.soltar(); await act(drenar); presaLeitura.soltar(); await act(drenar); await act(drenar);
    await waitFor(() => expect(screen.getByTestId("meta-99")).toBeTruthy());
  });
  it("e se o usuário já abriu OUTRA folha: a gravação velha não a fecha nem some com o rascunho (positivo: a releitura acontece)", async () => {
    const preso = await gravandoPreso(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Segunda");
    lista = { ...TODAS.varios, metas: [{ ...TODAS.varios.metas[2]!, id: 99, nome: "Viagem" }] };
    preso.soltar(); await act(drenar); await act(drenar);
    expect(screen.getByText("Nova meta")).toBeTruthy(); expect(screen.getByLabelText(/^Nome/).props.value).toBe("Segunda");
    act(() => router.back()); await fechou("Nova meta"); await waitFor(() => expect(screen.getByTestId("meta-99")).toBeTruthy()); // fechada a 2ª, a aba (que volta ao foco) já mostra a meta gravada
  });
});

// CARACTERIZAÇÃO, não prova de conserto: estes casos passam com o `forcar` desligado; quem os cobre é o reload do provider ao voltar o foco da aba.
describe("caracterização: a leitura da aba em voo com a folha VIVA quando a gravação termina", () => {
  /** A 1ª leitura da aba fica presa, com a lista de ANTES; a folha abre por cima (rota direta, a aba ainda não tem botões). */
  const abrirPorCimaDeLeituraPresa = async (url: string) => {
    const base = fetchFalso.getMockImplementation()!, presa = segurar(); let velha = true; const antiga = lista;
    fetchFalso.mockImplementation(async (u: string, o: RequestInit) => { if (velha && new URL(u).pathname === "/api/app/metas" && o.method === "GET") { velha = false; await presa.promessa; return resposta(200, antiga); } return base(u, o); });
    renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(velha).toBe(false));
    act(() => router.push(url as never)); await folha(url.includes("id=") ? "Editar meta" : "Nova meta");
    return presa;
  };
  const soltarDepois = async (presa: { soltar: () => void }) => { presa.soltar(); await act(drenar); await act(drenar); };
  it("criar: a folha fecha, o foco da aba trata a leitura em voo como fresca, e a lista nova aparece mesmo assim", async () => {
    lista = TODAS.vazio; const presa = await abrirPorCimaDeLeituraPresa("/meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    lista = { ...TODAS.varios, metas: [{ ...TODAS.varios.metas[2]!, id: 99, nome: "Viagem" }] };
    await salvar(); await fechou("Nova meta"); await soltarDepois(presa); await waitFor(() => expect(screen.getByTestId("meta-99")).toBeTruthy());
  });
  it("apagar: a meta apagada some da lista mesmo com a leitura velha em voo", async () => {
    const presa = await abrirPorCimaDeLeituraPresa("/meta?id=3"); lista = { ...TODAS.varios, metas: TODAS.varios.metas.filter((m) => m.id !== 3) };
    await apertar("Apagar"); const destrutivo = jest.mocked(Alert.alert).mock.calls[0]![2]!.find((b) => b.style === "destructive")!.onPress as () => void;
    await act(async () => { destrutivo(); await drenar(); }); await fechou("Editar meta"); await soltarDepois(presa);
    await waitFor(() => expect(screen.queryByTestId("meta-3")).toBeNull()); expect(screen.getByTestId("bloco-metas")).toBeTruthy();
  });
  it("resultado desconhecido (500): a folha fica com 'Fechar', mas a lista nova já é relida por cima da leitura velha", async () => {
    lista = TODAS.vazio; const presa = await abrirPorCimaDeLeituraPresa("/meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    escrita = () => resposta(500, {}); lista = { ...TODAS.varios, metas: [{ ...TODAS.varios.metas[2]!, id: 99, nome: "Viagem" }] };
    await salvar(); await waitFor(() => expect(screen.getByRole("button", { name: "Fechar" })).toBeTruthy()); await soltarDepois(presa);
    await apertar("Fechar"); await fechou("Nova meta"); await waitFor(() => expect(screen.getByTestId("meta-99")).toBeTruthy());
  });
});

describe("a trava depois do sucesso (a folha demora a sair)", () => {
  it("um 2º toque em Salvar durante a animação de fechar não grava de novo", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    jest.spyOn(router, "back").mockImplementation(() => {}); // a folha continua na tela
    await salvar(); await waitFor(() => expect(posts()).toHaveLength(1)); await act(drenar);
    await salvar(); await act(drenar); expect(posts()).toHaveLength(1);
  });
  it("Apagar: os dois alertas de dois toques, confirmados depois do sucesso, apagam uma vez só", async () => {
    await abrirAba(); await apertar(/^Notebook novo.*editar$/); await folha("Editar meta"); jest.spyOn(router, "back").mockImplementation(() => {});
    await apertar("Apagar"); await apertar("Apagar");
    const alertas = jest.mocked(Alert.alert).mock.calls.filter((c) => c[0] === "Apagar meta?"); expect(alertas).toHaveLength(2);
    for (const a of alertas) await act(async () => { (a[2]!.find((b) => b.style === "destructive")!.onPress as () => void)(); await drenar(); });
    expect(posts().filter((p) => p.rota.endsWith("/apagar"))).toHaveLength(1);
  });
});

const SO_DATE = ["setTimeout", "setInterval", "setImmediate", "clearTimeout", "clearInterval", "clearImmediate", "nextTick", "queueMicrotask", "requestAnimationFrame", "cancelAnimationFrame", "requestIdleCallback", "cancelIdleCallback", "performance", "hrtime"] as never; // congela só o `Date`
describe("prazo: o dia é o de Brasília, não o do aparelho nem o UTC", () => {
  afterEach(() => jest.useRealTimers());
  it.each([
    ["23:30 em SP (UTC já no dia 18)", "2026-10-18T02:30:00Z", "2026-10-17"],
    ["00:30 em SP (aparelhos a oeste ainda no dia 16)", "2026-10-17T03:30:00Z", "2026-10-17"],
    ["23:59:30 em SP", "2026-10-18T02:59:30Z", "2026-10-17"],
    ["00:00:30 em SP (já dia 18)", "2026-10-18T03:00:30Z", "2026-10-18"],
  ])("%s: tocar em 'Prazo' grava o dia de SP e o seletor não deixa dia anterior", async (_nome, agora, esperado) => {
    jest.useFakeTimers({ now: new Date(agora), doNotFake: SO_DATE });
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Casa"); digitarValor("100000"); await apertar(/Prazo/);
    const { minimumDate, value } = screen.getByTestId("seletor-data").props as { minimumDate: Date; value: Date };
    expect(isoDoSeletor(minimumDate)).toBe(esperado); expect(isoDoSeletor(value)).toBe(esperado);
    await salvar(); await waitFor(() => expect(posts()).toHaveLength(1)); expect(posts()[0]!.corpo.data_alvo).toBe(esperado);
  });
});

describe("a folha de meta: entrada e botões", () => {
  it("dois toques rápidos em 'Criar meta' abrem UMA folha (um `back` volta à aba)", async () => {
    lista = TODAS.vazio; await abrirAba(); const b = botao("Criar meta");
    await act(async () => { fireEvent.press(b); fireEvent.press(b); await drenar(); }); await folha("Nova meta");
    act(() => router.back()); await fechou("Nova meta");
  });
  it("dois toques rápidos em 'Montar reserva' abrem UMA folha (um `back` volta à aba)", async () => {
    lista = TODAS.vazio; await abrirAba(); const b = botao("Montar reserva de emergência");
    await act(async () => { fireEvent.press(b); fireEvent.press(b); await drenar(); }); await folha("Montar reserva");
    act(() => router.back()); await waitFor(() => expect(screen).toHavePathname("/metas")); // pelo caminho: a linha "Montar reserva" da própria aba tem o mesmo texto do título da folha (antes o portão a escondia na volta)
  });
  it("dois toques rápidos numa pílula abrem UMA folha (um `back` volta à aba)", async () => {
    await abrirAba(); const p = screen.getByRole("button", { name: /^Notebook novo.*editar$/ });
    await act(async () => { fireEvent.press(p); fireEvent.press(p); await drenar(); }); await folha("Editar meta");
    act(() => router.back()); await fechou("Editar meta");
  });
  it("`?id=abc` (presente e inválido) é 'Essa meta não existe mais', nunca 'Nova meta'", async () => {
    lista = TODAS.vazio; await abrirAba(); act(() => router.push("/meta?id=abc" as never));
    await waitFor(() => expect(screen.getByText("Essa meta não existe mais.")).toBeTruthy()); expect(screen.queryByText("Nova meta")).toBeNull(); expect(screen.queryByLabelText(/^Nome/)).toBeNull();
  });
  it("402 na escrita: a folha fecha e a aba cai no PORTÃO (sem bloco de metas nem 'Criar meta'); um 400 comum NÃO fecha", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "X"); digitarValor("100000");
    escrita = () => resposta(400, { error: { code: "valor_invalido", message: "x" } }); await salvar(); await waitFor(() => expect(screen.getByText("Não foi possível salvar.")).toBeTruthy()); expect(screen.getByText("Nova meta")).toBeTruthy();
    escrita = () => resposta(402, { error: { code: "subscription_required", message: "x" } }); const antes = gets(); metasGet = () => resposta(402, { error: { code: "subscription_required", message: "x" } }); await salvar(); await fechou("Nova meta"); await waitFor(() => expect(gets()).toBeGreaterThan(antes));
    await waitFor(() => expect(screen.queryByTestId("bloco-metas")).toBeNull()); expect(screen.queryAllByTestId(/^bloco-/)).toHaveLength(0); expect(screen.queryByRole("button", { name: "Criar meta" })).toBeNull();
  });
  it("Apagar ocupa a MESMA largura do Salvar: nenhum wrapper o encolhe (alignItems/alignSelf/width/row) e o rótulo é centralizado", async () => {
    await abrirAba(); await apertar(/^Notebook novo.*editar$/); await folha("Editar meta");
    const estilo = (n: { props: { style?: unknown } }) => (StyleSheet.flatten(n.props.style as never) ?? {}) as Record<string, unknown>;
    type No = { parent: No | null; props: { style?: unknown } };
    const salvarCadeia = new Set<No>(); for (let n: No | null = botao("Salvar") as unknown as No; n; n = n.parent) salvarCadeia.add(n);
    const apagar = botao("Apagar") as unknown as No; expect(estilo(apagar).alignSelf ?? "stretch").toBe("stretch"); expect(estilo(apagar).width).toBeUndefined();
    for (let n = apagar.parent; n && !salvarCadeia.has(n); n = n.parent) { const e = estilo(n); expect(e.alignItems ?? "stretch").toBe("stretch"); expect(e.alignSelf ?? "stretch").toBe("stretch"); expect(e.width).toBeUndefined(); expect(e.flexDirection ?? "column").toBe("column"); }
  });
});
