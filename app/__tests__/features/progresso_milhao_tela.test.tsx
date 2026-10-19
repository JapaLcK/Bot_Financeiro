import { RefreshControl } from "react-native";
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { VALOR_OCULTO_FALADO } from "@/ui/dinheiro";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// Tela Progresso ao milhão: `renderRouter` + servidor falso. A conta de juros tem o seu teste (`milhao_paridade`); aqui, os estados e as falhas. Relógio em 2026-10.
jest.setTimeout(30_000);
const F = v2.progresso_milhao, ROTA = "/api/app/progresso-milhao";
let milhao: () => Response | Promise<Response>;
const erro = (status: number, code: string) => resposta(status, { error: { code, message: "texto cru em inglês" } });
const leituras = () => fetchFalso.mock.calls.filter(([u]) => new URL(String(u)).pathname === ROTA);
const AGORA = "2026-10-19T16:00:00Z"; // só o `Date` é falso: o resto do teste usa timers reais
const SO_DATE = ["hrtime", "nextTick", "performance", "queueMicrotask", "requestAnimationFrame", "cancelAnimationFrame", "requestIdleCallback", "cancelIdleCallback", "setImmediate", "clearImmediate", "setInterval", "clearInterval", "setTimeout", "clearTimeout"] as const;
beforeEach(async () => {
  jest.useFakeTimers({ now: new Date(AGORA), doNotFake: [...SO_DATE] });
  prepararCaso(); desligarTrava(); milhao = () => resposta(200, F.padrao);
  await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
  fetchFalso.mockImplementation(async (url: string) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    if (path === ROTA) return milhao();
    if (path === "/auth/refresh") return resposta(401, { detail: "invalid_refresh_token" });
    return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
  });
});
afterEach(() => { jest.useRealTimers(); });
async function abrir(url = "/progresso-milhao") { renderRouter("./app", { initialUrl: url }); await act(drenar); }
const puxar = async () => { await act(async () => { screen.UNSAFE_getByType(RefreshControl).props.onRefresh(); await drenar(); }); };
const apertar = async (nome: string) => { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); };
const guardar = (v: number) => act(async () => { fireEvent(screen.getByLabelText("Guardar por mês"), "valueChange", v); await drenar(); });
const CONVITE = "O Progresso ao milhão é do Plus e do Pro.", NAO_DEU = "Não deu para carregar", AVISO = "Não deu para atualizar. Mostrando o que carregou antes.";

it("R$ 100 mil: dezembro de 2050 daqui a 24 anos e 2 meses", async () => {
  milhao = () => resposta(200, { patrimonio: "100000.00", motivos: [] }); await abrir();
  expect(screen.getByText("dezembro de 2050")).toBeTruthy(); expect(screen.getByText("Daqui a 24 anos e 2 meses")).toBeTruthy();
  expect(screen.getByText("Faltam")).toBeTruthy(); expect(screen.getByText("· 10%")).toBeTruthy();
  expect(screen.getByLabelText("Guardar por mês")).toBeTruthy(); expect(screen.getByLabelText("Rendimento ao ano")).toBeTruthy();
  expect(screen.getByText("Conta de juros compostos com taxa fixa: não é promessa de rendimento. Patrimônio = Carteira, contas, caixinhas e investimentos.")).toBeTruthy();
});
it("padrão (R$ 48.250,37): 4% e 'Voltar ao padrão' desativado", async () => {
  await abrir(); expect(screen.getByText("· 4%")).toBeTruthy(); expect(screen.UNSAFE_queryAllByType(ProgressBar)).toHaveLength(1); expect(screen.getByRole("button", { name: "Voltar ao padrão" }).props.accessibilityState).toMatchObject({ disabled: true });
});
it("guardar 1033 vira 1050 (passo de 50), a data muda e 'Voltar ao padrão' devolve o inicial", async () => {
  milhao = () => resposta(200, F.pouco); await abrir(); const mes = () => screen.queryAllByText(/ de \d{4}$/).length;
  expect(screen.getByText("Sem previsão")).toBeTruthy(); // R$ 500 a 10% sem aporte não chegam em 50 anos
  await guardar(1033);
  expect(screen.getByLabelText("Guardar por mês").props.value).toBe(1050); expect(screen.queryByText("Sem previsão")).toBeNull(); expect(mes()).toBe(1);
  expect(screen.getByRole("button", { name: "Voltar ao padrão" }).props.accessibilityState).toMatchObject({ disabled: false });
  await apertar("Voltar ao padrão");
  expect(screen.getByLabelText("Guardar por mês").props.value).toBe(0); expect(screen.getByText("Sem previsão")).toBeTruthy();
});
it("passou do milhão: 'Você já passou do milhão', barra até R$ 2 milhões e chegada aos R$ 2 milhões", async () => {
  milhao = () => resposta(200, F.passou); await abrir();
  expect(screen.getByText("Você já passou do milhão")).toBeTruthy(); expect(screen.getByText("R$ 2 milhões")).toBeTruthy(); expect(screen.getByText("Você chega aos R$ 2 milhões em")).toBeTruthy();
});
it("sem previsão: 'Sem previsão' e a dica; os controles seguem", async () => {
  milhao = () => resposta(200, F.pouco); await abrir();
  expect(screen.getByText("Sem previsão")).toBeTruthy(); expect(screen.getByText("Arraste o Guardar por mês para ver quando você chega.")).toBeTruthy(); expect(screen.getByLabelText("Guardar por mês")).toBeTruthy();
});
it("sem banco: só o texto e 'Conectar banco' (leva a /conexoes); nenhum controle", async () => {
  milhao = () => resposta(200, F.sem_banco); await abrir();
  expect(screen.getByText("Conecte um banco para ver quanto falta para o seu primeiro milhão.")).toBeTruthy(); expect(screen.queryByLabelText("Guardar por mês")).toBeNull(); expect(screen.queryByLabelText("Rendimento ao ano")).toBeNull();
  await apertar("Conectar banco"); await waitFor(() => expect(screen).toHavePathname("/conexoes"));
});
it("com motivos: um selo por motivo, com o rótulo do web", async () => {
  milhao = () => resposta(200, F.com_motivos); await abrir();
  for (const t of ["banco desatualizado", "manual e banco juntos", "caixinha do banco fora da soma"]) expect(screen.getByText(t)).toBeTruthy();
});
it("403 pro_required: só o convite, sem valor e sem controle", async () => {
  milhao = () => erro(403, "pro_required"); await abrir();
  expect(screen.getByText(CONVITE)).toBeTruthy(); expect(screen.queryByText(/R\$/)).toBeNull(); expect(screen.queryByLabelText("Guardar por mês")).toBeNull();
});
it("403 password_required: o texto de criar a senha", async () => {
  milhao = () => erro(403, "password_required"); await abrir(); expect(screen.getByText(/crie a senha da sua conta/)).toBeTruthy();
});
it("500 sem dado: 'Não deu para carregar'; 'Tentar de novo' com 200 mostra o corpo", async () => {
  milhao = () => resposta(500, undefined); await abrir(); expect(screen.getByText(NAO_DEU)).toBeTruthy();
  milhao = () => resposta(200, F.padrao); await apertar("Tentar de novo");
  await waitFor(() => expect(screen.getByLabelText("Guardar por mês")).toBeTruthy()); expect(screen.queryByText(NAO_DEU)).toBeNull();
});
it("404 (outro 4xx) sem dado: 'Não deu para carregar'", async () => { milhao = () => erro(404, "nao_achou"); await abrir(); expect(screen.getByText(NAO_DEU)).toBeTruthy(); });
describe("releitura", () => {
  it("200 e depois 500: o corpo fica e entra o aviso; 'Tentar de novo' do aviso com 200 tira o aviso", async () => {
    await abrir(); expect(screen.getByLabelText("Guardar por mês")).toBeTruthy();
    milhao = () => resposta(500, undefined); await puxar();
    expect(screen.getByText(AVISO)).toBeTruthy(); expect(screen.getByLabelText("Guardar por mês")).toBeTruthy();
    milhao = () => resposta(200, F.padrao); await apertar("Tentar de novo"); expect(screen.queryByText(AVISO)).toBeNull();
  });
  it("200 e depois 403: o corpo SOME (4xx não mostra dado guardado) e entra o convite", async () => {
    await abrir(); expect(screen.getByLabelText("Guardar por mês")).toBeTruthy();
    milhao = () => erro(403, "pro_required"); await puxar();
    expect(screen.getByText(CONVITE)).toBeTruthy(); expect(screen.queryByLabelText("Guardar por mês")).toBeNull(); expect(screen.queryByText(AVISO)).toBeNull();
  });
});
it("401 (com o refresh também 401): a sessão expira e volta a /entrar", async () => {
  milhao = () => resposta(401, { detail: "expirado" }); await abrir();
  await waitFor(() => expect(screen).toHavePathname("/entrar")); expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
});
it("o GET sai sem query (o uid é só da sessão)", async () => {
  await abrir(); const [u] = leituras()[0]!, url = new URL(String(u)); expect(leituras()).toHaveLength(1); expect(url.pathname + url.search).toBe(ROTA);
});
it("modo oculto: 'Faltam' e 'Hoje' mascarados, sem barra e sem %, e o mês de chegada continua", async () => {
  milhao = () => resposta(200, { patrimonio: "100000.00", motivos: [] });
  renderRouter("./app", { initialUrl: "/gastos" }); await act(drenar); await apertar("Ocultar valores");
  await act(async () => { router.push("/progresso-milhao"); await drenar(); }); await waitFor(() => expect(screen.getByText("dezembro de 2050")).toBeTruthy());
  expect(screen.queryAllByLabelText(VALOR_OCULTO_FALADO)).toHaveLength(2); expect(screen.UNSAFE_queryAllByType(ProgressBar)).toHaveLength(0); expect(screen.queryByText("· 10%")).toBeNull(); // a barra e o % existem no teste do padrão
});

it("o mês de chegada vem do relógio: patrimônio igual, mês de partida outro, data outra", async () => {
  jest.setSystemTime(new Date("2026-12-19T16:00:00Z")); milhao = () => resposta(200, { patrimonio: "100000.00", motivos: [] }); await abrir();
  expect(screen.getByText("fevereiro de 2051")).toBeTruthy();
});
it("duas leituras: a resposta lenta da 1ª, que chega depois da 2ª, não sobrescreve", async () => {
  let solta!: (r: Response) => void;
  milhao = () => new Promise<Response>((r) => { solta = r; }); renderRouter("./app", { initialUrl: "/progresso-milhao" }); await act(drenar);
  milhao = () => resposta(200, { patrimonio: "100000.00", motivos: [] }); await puxar(); await waitFor(() => expect(screen.getByText("· 10%")).toBeTruthy());
  await act(async () => { solta(resposta(200, { patrimonio: "500000.00", motivos: [] })); await drenar(); });
  expect(screen.getByText("· 10%")).toBeTruthy(); expect(screen.queryByText("· 50%")).toBeNull();
});
it("duas leituras: a falha lenta da 1ª, depois da 2ª que deu certo, não põe aviso de erro", async () => {
  let falha!: (r: Response) => void;
  milhao = () => new Promise<Response>((r) => { falha = r; }); renderRouter("./app", { initialUrl: "/progresso-milhao" }); await act(drenar);
  milhao = () => resposta(200, F.padrao); await puxar(); await waitFor(() => expect(screen.getByLabelText("Guardar por mês")).toBeTruthy());
  await act(async () => { falha(resposta(500, undefined)); await drenar(); });
  expect(screen.queryByText(AVISO)).toBeNull(); expect(screen.queryByText(NAO_DEU)).toBeNull();
});
it("'Faltam' arredonda para cima: R$ 999.999,50 falta R$ 1,00 (não R$ 0,00)", async () => {
  milhao = () => resposta(200, { patrimonio: "999999.50", motivos: [] }); await abrir();
  expect(screen.getByText("R$ 1,", { exact: false })).toBeTruthy();
});
it("meta acima do milhão ganha o milhar: R$ 999.500.000 -> 'R$ 1.000 milhões'", async () => {
  milhao = () => resposta(200, { patrimonio: "999500000.00", motivos: [] }); await abrir(); expect(screen.getByText("R$ 1.000 milhões")).toBeTruthy();
});
it("taxa: 10,3 vira 10,5% (passo de 0,5); os dois sliders falam o valor", async () => {
  await abrir(); await act(async () => { fireEvent(screen.getByLabelText("Rendimento ao ano"), "valueChange", 10.3); await drenar(); });
  expect(screen.getByText("10,5%")).toBeTruthy(); expect(screen.getByLabelText("Rendimento ao ano").props.accessibilityValue).toEqual({ text: "10,5% ao ano" });
  await guardar(1050); expect(screen.getByLabelText("Guardar por mês").props.accessibilityValue).toEqual({ text: "R$ 1.050 por mês" });
});
