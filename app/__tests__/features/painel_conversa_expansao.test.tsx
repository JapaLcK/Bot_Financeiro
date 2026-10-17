import { router } from "expo-router";
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";
import { mesAtual } from "@/features/painel/catalogo";
import { prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, ir, reiniciar } from "./conversa_apoio";
import { F, bloco, chamadasLanc, comBlocos, consultaLanc, instalarCards, lanc, lancamentos, pagina, perguntar } from "./conversa_cards_apoio";
// Tocar numa linha do card do chat (categoria, dia, conta, categoria do período) EXPANDE os detalhes dentro do card; nada navega, nunca só texto.
jest.setTimeout(30_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); lancamentos.resp = () => pagina([lanc({ descricao: "Mercado do bairro" }), lanc({ descricao: "Padaria" })]); });
afterEach(() => { jest.restoreAllMocks(); });
const definirReduzir = (v: boolean) => (global as unknown as { __definirReduzirMovimento: (v: boolean) => void }).__definirReduzirMovimento(v);

const MES = "2025-03"; // nunca é o mês do provider (o mês atual)
const detalhesDe = (mes: string, categorias = F["/api/app/mes-detalhes"].categorias) => ({ detalhes: { ...F["/api/app/mes-detalhes"], mes, categorias } });
const linha = (nome: RegExp) => screen.getByRole("button", { name: nome });
const aberta = (nome: RegExp) => linha(nome).props.accessibilityState.selected; // a coluna do `CardGastos` (selected), não a linha expansível
const tocar = async (nome: RegExp) => { await act(async () => { fireEvent.press(linha(nome)); await drenar(); }); };

it("categoria de OUTRO mês: pede o mês da aba, expande no lugar, recolhe ao tocar de novo, e nunca navega", async () => {
  const push = jest.spyOn(router, "push"); comBlocos(bloco("categorias", detalhesDe(MES), MES)); await perguntar();
  expect(aberta(/Alimentação/)).toBe(false);
  await tocar(/Alimentação/);
  await waitFor(() => expect(screen.getByText("Mercado do bairro")).toBeTruthy());
  const q = consultaLanc(); expect([q.get("mes"), q.get("categoria"), q.get("tipo"), q.get("limite")]).toEqual([MES, "Alimentação", "saida", "10"]);
  expect(aberta(/Alimentação/)).toBe(true);
  await tocar(/Alimentação/); expect(aberta(/Alimentação/)).toBe(false); expect(screen.queryByText("Mercado do bairro")).toBeNull();
  expect(push).not.toHaveBeenCalled();
});
it("controle positivo: no painel (aba Gastos) a mesma linha segue levando ao Extrato", async () => {
  instalarCards(true); const push = jest.spyOn(router, "push"); await abrir(); await ir("/gastos");
  await waitFor(() => expect(screen.getByRole("button", { name: /Alimentação/ })).toBeTruthy());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: /Alimentação/ })); await drenar(); }); expect(push).toHaveBeenCalledWith({ pathname: "/extrato", params: { categoria: "Alimentação", entrada: expect.any(Number) } });
  await waitFor(() => expect(screen.getByText("Mercado do bairro")).toBeTruthy()); await act(drenar); // espera o Extrato aberto terminar a leitura (senão ela atualiza o estado depois do teste)
});
it("categoria null: pede `categoria=` vazio (casa 'sem categoria')", async () => {
  comBlocos(bloco("categorias", detalhesDe(MES, [{ categoria: null, valor: "100.00", quantidade: 1 }]), MES)); await perguntar();
  await tocar(/Sem categoria/); await waitFor(() => expect(chamadasLanc()).toHaveLength(1));
  expect(consultaLanc().has("categoria")).toBe(true); expect(consultaLanc().get("categoria")).toBe("");
});

const DIA = "2026-10-03", MES_DIA = "2026-10";
const doDia = (id: string) => lanc({ id, data: DIA, descricao: `Do dia ${id}` });
const tocarDia = async () => { comBlocos(bloco("calendario", detalhesDe(MES_DIA), MES_DIA)); await perguntar(); fireEvent.press(screen.getByTestId(`calendario-dia-${DIA}`)); await act(drenar); };
it("dia: pede `dia=` dentro do mês do bloco (10 por vez), mostra os itens, 'Carregar mais' segue o cursor e nada navega", async () => {
  // O servidor falso respeita `dia` como o endpoint: sem ele devolveria um lançamento de OUTRO dia, que apareceria na tela.
  lancamentos.resp = (u) => { const c = u.searchParams.get("cursor"), dia = u.searchParams.get("dia"); return c ? pagina([doDia("d2")]) : pagina(dia === DIA ? [doDia("d1")] : [lanc({ id: "outro", data: "2026-10-20", descricao: "Outro dia" })], "c2"); };
  const push = jest.spyOn(router, "push"); await tocarDia(); expect(push).not.toHaveBeenCalled();
  expect(screen.getByText("Do dia d1")).toBeTruthy(); expect(screen.queryByText("Outro dia")).toBeNull();
  expect([consultaLanc(0).get("mes"), consultaLanc(0).get("dia"), consultaLanc(0).get("limite")]).toEqual([MES_DIA, DIA, "10"]);
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Carregar mais lançamentos" })); await drenar(); });
  expect(screen.getByText("Do dia d1")).toBeTruthy(); expect(screen.getByText("Do dia d2")).toBeTruthy(); expect(chamadasLanc()).toHaveLength(2);
  expect([consultaLanc().get("cursor"), consultaLanc().get("dia"), consultaLanc().get("mes")]).toEqual(["c2", DIA, MES_DIA]);
});
it("dia sem lançamento: 'Nenhum lançamento encontrado.' e não fica carregando", async () => {
  lancamentos.resp = () => pagina([]); await tocarDia();
  expect(screen.getByText("Nenhum lançamento encontrado.")).toBeTruthy(); expect(screen.queryByLabelText("Carregando lançamentos")).toBeNull();
});

it("controle positivo: no painel (aba Resumo) a conta segue levando a /conexoes", async () => {
  instalarCards(true); const push = jest.spyOn(router, "push"); await abrir(); await ir("/resumo");
  await waitFor(() => expect(screen.getByRole("button", { name: /Nubank/ })).toBeTruthy());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: /Nubank/ })); await drenar(); }); expect(push).toHaveBeenCalledWith("/conexoes"); expect(chamadasLanc()).toHaveLength(0);
  await act(drenar); await act(drenar);
});
it("conta: pede a conta e o mês atual, expande no lugar e não navega", async () => {
  comBlocos(bloco("contas")); await perguntar(); const push = jest.spyOn(router, "push");
  await tocar(/Nubank/); await waitFor(() => expect(screen.getByText("Mercado do bairro")).toBeTruthy());
  const q = consultaLanc(); expect([q.get("conta"), q.get("mes"), q.get("limite")]).toEqual(["1", mesAtual(), "10"]); expect(screen.getByText("Lançamentos deste mês")).toBeTruthy(); // só o mês atual: a legenda diz, para o vazio do dia 1 não parecer conta sem histórico
  expect(push).not.toHaveBeenCalled();
});

it("estados: carregando, erro com 'Tentar de novo' e vazio", async () => {
  const presa = segurar(); lancamentos.resp = async () => { await presa.promessa; return pagina([lanc({ descricao: "Chegou" })]); };
  comBlocos(bloco("categorias", detalhesDe(MES), MES)); await perguntar();
  await tocar(/Alimentação/); expect(screen.getByLabelText("Carregando lançamentos")).toBeTruthy();
  await act(async () => { presa.soltar(); await drenar(); }); expect(screen.getByText("Chegou")).toBeTruthy(); expect(screen.queryByLabelText("Carregando lançamentos")).toBeNull();
  await tocar(/Alimentação/);
  lancamentos.resp = () => resposta(500, { detail: "x" }); await tocar(/Alimentação/);
  await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
  lancamentos.resp = () => pagina([lanc({ descricao: "Voltou" })]); await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Tentar de novo" })); await drenar(); });
  expect(screen.getByText("Voltou")).toBeTruthy();
  await tocar(/Alimentação/); lancamentos.resp = () => pagina([]); await tocar(/Alimentação/);
  await waitFor(() => expect(screen.getByText("Nenhum lançamento encontrado.")).toBeTruthy());
});
it("carregar mais: 'Carregar mais lançamentos' segue o cursor e junta as páginas", async () => {
  lancamentos.resp = (u) => u.searchParams.get("cursor") ? pagina([lanc({ descricao: "Segunda" })]) : pagina([lanc({ descricao: "Primeira" })], "c2");
  comBlocos(bloco("categorias", detalhesDe(MES), MES)); await perguntar(); await tocar(/Alimentação/);
  await waitFor(() => expect(screen.getByText("Primeira")).toBeTruthy());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Carregar mais lançamentos" })); await drenar(); });
  expect(screen.getByText("Primeira")).toBeTruthy(); expect(screen.getByText("Segunda")).toBeTruthy(); expect(consultaLanc().get("cursor")).toBe("c2");
});

it("recolher no meio da busca: o sinal é abortado e nada atualiza depois (sem aviso de act)", async () => {
  const erro = jest.spyOn(console, "error").mockImplementation(() => undefined);
  const presa = segurar(); lancamentos.resp = async () => { await presa.promessa; return pagina([lanc({ descricao: "Tarde demais" })]); };
  comBlocos(bloco("categorias", detalhesDe(MES), MES)); await perguntar();
  await tocar(/Alimentação/); await waitFor(() => expect(chamadasLanc()).toHaveLength(1)); const req = chamadasLanc()[0]![1] as RequestInit;
  expect(req.signal!.aborted).toBe(false);
  await tocar(/Alimentação/); expect(req.signal!.aborted).toBe(true);
  await act(async () => { presa.soltar(); await drenar(); });
  expect(screen.queryByText("Tarde demais")).toBeNull(); expect(erro).not.toHaveBeenCalled();
});

it("transação: a linha recolhida já diz 'parcela 3/10' e o toque abre o detalhe", async () => {
  lancamentos.resp = () => pagina([lanc({ descricao: "Geladeira", parcela: { n: 3, total: 10 }, instituicao: "Nubank", hora: "09:30", categoria: "Casa" })]);
  comBlocos(bloco("categorias", detalhesDe(MES), MES)); await perguntar(); await tocar(/Alimentação/);
  await waitFor(() => expect(screen.getByText("Geladeira")).toBeTruthy());
  expect(screen.getByText(/ · parcela 3\/10$/)).toBeTruthy(); expect(screen.queryByText(/Parcela 3\/10$/)).toBeNull();
  await act(async () => { fireEvent.press(screen.getByText("Geladeira")); await drenar(); });
  expect(screen.getByText("Casa · Nubank · 09:30 · Parcela 3/10")).toBeTruthy();
});

it("card `periodo`: a categoria expande os maiores lançamentos no lugar — sem busca e sem Folha", async () => {
  comBlocos(bloco("periodo")); await perguntar();
  await tocar(/Mercado/); expect(screen.getByText("Supermercado")).toBeTruthy();
  expect(chamadasLanc()).toHaveLength(0); expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull(); // sem Folha (só ela tem "Fechar")
});

it.each([[true, "undefined"], [false, "object"]])("reduzir movimento=%s: as transações da coluna entram com entering %s", async (reduzir, tipo) => {
  definirReduzir(reduzir); comBlocos(bloco("categorias", detalhesDe(MES), MES)); await perguntar(); await tocar(/Alimentação/);
  await waitFor(() => expect(typeof screen.getByTestId("gastos-transacoes").props.entering).toBe(tipo));
});
it("reduzir movimento: o dia (linha expansível) segue entrando com `linha-expandida`", async () => {
  definirReduzir(false); await tocarDia(); expect(typeof screen.getByTestId("linha-expandida").props.entering).toBe("object");
});
