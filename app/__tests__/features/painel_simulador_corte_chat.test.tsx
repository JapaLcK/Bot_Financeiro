import { screen, within } from "expo-router/testing-library";
import { simuladorHabitosSchema } from "@/api/schemas/painel";
import { CardCorte } from "@/features/painel/simuladorCorte";
import { renderInterativo } from "../ui/_render";
import { fetchFalso, prepararCaso } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import { caminho, reiniciar } from "./conversa_apoio";
import { RECURSOS, bloco, comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
import v2 from "../../../tests/frontend/api_v2_respostas.json";

// "Valores ocultos": o provedor real mora no layout do app; aqui o hook lê esta chave.
let mockOculto = false;
jest.mock("@/features/painel/privacidade", () => ({ ...jest.requireActual("@/features/painel/privacidade"), usePrivacidade: () => ({ oculto: mockOculto, alternar: () => undefined, mostrar: () => undefined }) }));

// Card do chat quando a pessoa PEDIU um corte: o bloco traz `simulador_habitos` (o corpo do POST) e o card desenha só o resultado.
jest.setTimeout(30_000);
const H = (v2 as unknown as { simulador: { habitos: Record<string, any> } }).simulador.habitos;
const corte = (extra: Record<string, unknown> = {}) => ({ chave: "mercado", nome: "Mercado", despesa_mensal: "600.00", percentual: 30, economia_mensal: "180.00", valor_pedido: null, ...extra });
const card = (habitos: Record<string, unknown> | null) => bloco("simulador", habitos ? { ...RECURSOS.simulador!, simulador_habitos: habitos } : RECURSOS.simulador);
const mostra = async (habitos: Record<string, unknown> | null) => { comBlocos(card(habitos)); await perguntar(); return within(screen.getByTestId("bloco-simulador")); };
const posts = () => fetchFalso.mock.calls.filter(([u, o]) => caminho(u) === "/api/app/simulador/habitos" && (o as RequestInit)?.method === "POST");

beforeEach(async () => { mockOculto = false; prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(true); });
afterEach(() => { jest.restoreAllMocks(); });

it("com `simulador_habitos`: sobra, uma linha por corte e as duas curvas; sem slider e sem POST", async () => {
  const c = await mostra(H);
  expect(c.getByText("Sobra por mês")).toBeTruthy(); expect(c.getByText("Com corte")).toBeTruthy(); expect(c.getByText("Sem corte")).toBeTruthy();
  for (const l of H.cortes) expect(c.getByText(l.nome)).toBeTruthy();
  expect(H.cortes.length).toBeGreaterThan(1);
  expect(c.queryByLabelText(/^Corte em /)).toBeNull(); expect(c.queryByText("Zerar simulação")).toBeNull(); expect(posts()).toHaveLength(0);
});

it("sem `simulador_habitos`: os sliders de sempre (e a linha do widgets.tsx é o que separa os dois)", async () => {
  const c = await mostra(null);
  expect(c.getByLabelText("Corte em Mercado")).toBeTruthy(); expect(c.queryByText("Sobra por mês")).toBeNull();
});

it("estado indisponível: a sobra aparece, sem curva nem marcas", async () => {
  const c = await mostra({ ...H, estado: "indisponivel" });
  expect(c.getByText("Sobra por mês")).toBeTruthy(); expect(c.queryByText("Com corte")).toBeNull(); expect(c.queryByText("Sem corte")).toBeNull();
});

it("valores ocultos: nenhum dinheiro na tela e sem as marcas da curva", () => {
  mockOculto = true;
  const c = renderInterativo(<CardCorte r={simuladorHabitosSchema.parse({ ...H, economia_mensal: "180.00", cortes: [corte()] })} />);
  expect(c.getAllByLabelText("Valor oculto").length).toBeGreaterThan(0);
  expect(c.queryByLabelText(/reais/)).toBeNull(); expect(c.queryByText("Com corte")).toBeNull();
});

it("corte por percentual mostra o percentual e o valor; em reais mostra só os reais pedidos, sem percentual", async () => {
  const pct = await mostra({ ...H, cortes: [corte()] });
  expect(pct.getByText("−30% ·")).toBeTruthy(); expect(pct.getByLabelText("180 reais")).toBeTruthy(); expect(pct.getByText("/mês")).toBeTruthy();
});

it("corte em reais: R$ 200 pedidos aparecem, e o 33% efetivo não", async () => {
  const c = await mostra({ ...H, economia_mensal: "200.00", cortes: [corte({ percentual: 33, economia_mensal: "200.00", valor_pedido: "200.00" })] });
  expect(c.getByLabelText("200 reais")).toBeTruthy(); expect(c.queryByText(/33%/)).toBeNull(); expect(c.getByText("−")).toBeTruthy();
});

it("`todas`: uma linha por categoria cortada", async () => {
  const c = await mostra({ ...H, cortes: [corte(), corte({ chave: "lazer", nome: "Lazer", despesa_mensal: "300.00", economia_mensal: "90.00" }), corte({ chave: "saude", nome: "Saúde", despesa_mensal: "100.00", economia_mensal: "30.00" })] });
  for (const n of ["Mercado", "Lazer", "Saúde"]) expect(c.getAllByText(n)).toHaveLength(1);
  expect(c.getAllByText("−30% ·")).toHaveLength(3);
});

it("recurso inválido não derruba o bloco: `valor_pedido` quebrado vira percentual", async () => {
  const c = await mostra({ ...H, cortes: [corte({ valor_pedido: "x" })] });
  expect(c.getByText("−30% ·")).toBeTruthy();
});
