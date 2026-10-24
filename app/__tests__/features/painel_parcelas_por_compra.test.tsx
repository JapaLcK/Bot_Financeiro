import { fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { prepararCaso } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import { apertar, noResumo, reiniciar, srv } from "./conversa_apoio";
import { F, bloco, comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
// Parcelas em uma linha por COMPRA ("n de N pagas" e a barra; tocar abre as parcelas no card; 5 compras e "+ N") e o vazio das caixinhas (decisões do dono, 2026-10-23, as mesmas do /painel).
jest.setTimeout(30_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); });
afterEach(() => { jest.restoreAllMocks(); });

const BASE = F["/installments/1/list"], NOTEBOOK = BASE.installments[0];
const compra = (i: number, extra: Record<string, unknown> = {}) => ({ ...NOTEBOOK, group_id: `g${i}`, name: `Compra ${i}`, ...extra });
const parcelas = (installments: unknown[]) => bloco("parcelas", { parcelas: { ok: true, installments } });
const card = () => within(screen.getByTestId("bloco-parcelas"));

it("uma linha por compra: nome, cartão e \"2 de 3 pagas\"; as parcelas só aparecem ao tocar e somem ao tocar de novo", async () => {
  comBlocos(bloco("parcelas")); await perguntar();
  const c = card(); expect(c.getByText("Notebook")).toBeTruthy(); expect(c.getByText("Nubank · 2 de 3 pagas")).toBeTruthy();
  expect(c.queryByText("Parcela 1 de 6")).toBeNull(); expect(c.queryByText("Parcela 3 de 6")).toBeNull();
  fireEvent.press(c.getByText("Notebook"));
  expect(c.getByText("Parcela 1 de 6")).toBeTruthy(); expect(c.getByText("Parcela 3 de 6")).toBeTruthy(); expect(c.getByText("15/10/2026 · próxima")).toBeTruthy(); expect(c.getByText("15/08/2026 · paga")).toBeTruthy();
  fireEvent.press(c.getByText("Notebook")); expect(c.queryByText("Parcela 1 de 6")).toBeNull();
});

it("uma compra aberta por vez: abrir a 2ª fecha a 1ª", async () => {
  comBlocos(parcelas([compra(1), compra(2)])); await perguntar();
  const c = card(); fireEvent.press(c.getByText("Compra 1")); expect(c.getAllByText("Parcela 1 de 6")).toHaveLength(1);
  fireEvent.press(c.getByText("Compra 2")); expect(c.getAllByText("Parcela 1 de 6")).toHaveLength(1); // só a da Compra 2
});

it("7 compras: 5 à vista e \"+ 2 compras\", que abre o resto no próprio card e vira \"Mostrar menos\"", async () => {
  comBlocos(parcelas([1, 2, 3, 4, 5, 6, 7].map((i) => compra(i)))); await perguntar();
  const c = card(); expect(c.getByText("Compra 5")).toBeTruthy(); expect(c.queryByText("Compra 6")).toBeNull();
  fireEvent.press(c.getByText("+ 2 compras")); expect(c.getByText("Compra 7")).toBeTruthy();
  fireEvent.press(c.getByText("Mostrar menos")); expect(c.queryByText("Compra 6")).toBeNull();
});
it("6 compras: \"+ 1 compra\" (singular); 5 compras: sem botão", async () => {
  comBlocos(parcelas([1, 2, 3, 4, 5, 6].map((i) => compra(i))), parcelas([1, 2, 3, 4, 5].map((i) => compra(i + 10)))); await perguntar();
  expect(screen.getByText("+ 1 compra")).toBeTruthy(); expect(screen.getAllByText(/^\+ \d/)).toHaveLength(1);
});

it("a barra fica verde só com todas pagas; compra sem parcelas não abre (sem botão)", async () => {
  const todas = NOTEBOOK.parcelas.map((p: Record<string, unknown>) => ({ ...p, is_paid: true, is_next: false }));
  comBlocos(parcelas([compra(1, { parcelas: todas }), compra(2), compra(3, { parcelas: [] })])); await perguntar();
  const c = card(); expect(c.getByText("Nubank · 3 de 3 pagas")).toBeTruthy(); expect(c.getByText("Nubank · 0 de 0 pagas")).toBeTruthy();
  expect(screen.UNSAFE_getAllByType(ProgressBar).map((b) => b.props.tom)).toEqual(["positive", "brand", "brand"]);
  expect(c.getAllByRole("button").filter((b) => b.props.accessibilityState?.expanded !== undefined)).toHaveLength(2); // a Compra 3 não é expansível
});

it("o vazio do card `caixinhas` é \"Nenhuma caixinha ainda.\" (o mesmo do /painel)", async () => {
  comBlocos(bloco("caixinhas", { caixinhas: { ...F["/goals/1/status"], goals: [] } })); await perguntar();
  const c = within(screen.getByTestId("bloco-caixinhas")); expect(c.getByText("Nenhuma caixinha ainda.")).toBeTruthy(); expect(c.queryByText("Nenhuma meta ainda.")).toBeNull();
});

it("cartão sem nome: o detalhe é só \"2 de 3 pagas\", sem o separador solto (como o /painel)", async () => {
  comBlocos(parcelas([compra(1, { card_name: "" })])); await perguntar();
  expect(card().getByText("2 de 3 pagas")).toBeTruthy(); expect(card().queryByText(/^ · /)).toBeNull();
});

it("na HOME (perfil Sair das dívidas): o mesmo bloco; com valores ocultos a barra some, \"n de N pagas\" fica e a parcela aberta não mostra valor", async () => {
  instalarCards(true); srv.perfilDoServidor = "dividas"; renderRouter("./app", { initialUrl: "/resumo" }); await noResumo();
  const c = () => within(screen.getByTestId("bloco-parcelas")); await waitFor(() => expect(c().getByText("Nubank · 2 de 3 pagas")).toBeTruthy());
  const barras = () => c().UNSAFE_queryAllByType(ProgressBar);
  expect(barras()).toHaveLength(1); fireEvent.press(c().getByText("Notebook")); expect(c().getAllByLabelText("250 reais").length).toBeGreaterThan(0);
  await apertar("Ocultar valores");
  expect(barras()).toHaveLength(0); expect(c().getByText("Nubank · 2 de 3 pagas")).toBeTruthy(); expect(c().getByText("Parcela 3 de 6")).toBeTruthy(); expect(c().queryByLabelText("250 reais")).toBeNull();
});
