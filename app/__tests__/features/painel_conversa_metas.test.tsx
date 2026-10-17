import { StyleSheet } from "react-native";
import { render } from "@testing-library/react-native";
import { act, fireEvent, screen, waitFor, within } from "expo-router/testing-library";
import { metasV2Schema } from "@/api/schemas/painel";
import { BlocoContexto } from "@/features/painel/blocoContexto";
import { MetasChat, serieDaMeta } from "@/features/painel/metasChat";
import * as provider from "@/features/painel/provider";
import { falado } from "@/ui/dinheiro";
import { TemaProvider } from "@/ui/tema";
import { ALFA_WASH_SERIE, claro, escuro, SERIE_META } from "@/ui/tokens";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { prepararCaso, resposta, segurar } from "./auth_apoio";
import { apertar, enviar, ok, posts, reiniciar, srv } from "./conversa_apoio";
import { V2, bloco, comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
// O card `metas` do chat (formato v2 de `/api/v2/metas`, fixtures reais): pílulas com ícone, % e barra (blocos na reserva), sem frases; as frases vivem só no rótulo do VoiceOver.
jest.setTimeout(30_000);
// Os ícones viram `View testID="icone-<nome>"`: é assim que o teste sabe qual ícone a pílula desenha (mesmo recurso de painel_curva_tela).
// `cor` e `tom` seguem como props do host para o teste ler a cor do ícone.
// eslint-disable-next-line @typescript-eslint/no-require-imports -- a fábrica do jest.mock não importa módulos de fora (hoisting)
jest.mock("@/ui/componentes/Icone", () => ({ Icone: ({ nome, cor, tom }: { nome: string; cor?: string; tom?: string }) => require("react").createElement(require("react-native").View, { testID: `icone-${nome}`, cor, tom }) }));
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); });
afterEach(() => { jest.restoreAllMocks(); });
const mostrar = async (caso: string) => { comBlocos(bloco("metas", { metas: V2.metas[caso] })); await perguntar(); return within(screen.getByTestId("bloco-metas")); };
/** Uma meta/custo do `varios` com campos trocados (strings de dinheiro como o servidor manda) para um caso que a fixture não tem. */
type Cru = Record<string, any>;
const base = (id: number): Cru => V2.metas.varios.metas.find((m: Cru) => m.id === id);
const mostrarMeta = async (m: Cru, custo: Cru = {}) => { comBlocos(bloco("metas", { metas: { ...V2.metas.varios, metas: [m], custo_mensal: { ...V2.metas.varios.custo_mensal, ...custo } } })); await perguntar(); return within(screen.getByTestId(`meta-${m.id}`)); };
/** Só as metas de `ids` do `varios`, na ordem dada. */
const mostrarIds = async (ids: number[]) => { comBlocos(bloco("metas", { metas: { ...V2.metas.varios, metas: ids.map(base) } })); await perguntar(); return within(screen.getByTestId("bloco-metas")); };
const meta = (id: number) => within(screen.getByTestId(`meta-${id}`));
const rotulo = (id: number): string => screen.getByTestId(`meta-${id}`).props.accessibilityLabel;
const idsNaTela = () => screen.getAllByTestId(/^meta-\d+$/).map((e) => e.props.testID);
const cor = (el: { props: { style?: unknown } }) => (StyleSheet.flatten(el.props.style as object) as { backgroundColor: string }).backgroundColor;
const cheios = (c: ReturnType<typeof within>) => c.queryAllByTestId("bloco-cheio"), vazios = (c: ReturnType<typeof within>) => c.queryAllByTestId("bloco-vazio");
/** O `now` das barras do trecho. O `ProgressBar` é `View` com `accessibilityRole` mas sem `accessible`: o `getByRole` não o acha, então busca-se o host pela prop. */
type Consulta = { UNSAFE_queryAllByProps: (p: { [x: string]: unknown }) => { type: unknown; props: { [x: string]: any } }[] };
const barras = (c: Consulta): number[] => c.UNSAFE_queryAllByProps({ accessibilityRole: "progressbar" }).filter((b) => typeof b.type === "string").map((b) => b.props.accessibilityValue.now);
/** Cor do ícone de tipo (PiggyBank/Target) da pílula `id`; o ✓ e o ⚠ têm `tom`, não `cor`. */
const corIcone = (id: number) => (meta(id).getByTestId(/^icone-(PiggyBank|Target)$/).props as { cor: string }).cor;
/** Fundo do círculo do ícone: o primeiro ancestral host do ícone com `backgroundColor`. */
const circulo = (id: number): string => { let e: any = meta(id).getByTestId(/^icone-(PiggyBank|Target)$/); while (e && !(typeof e.type === "string" && StyleSheet.flatten(e.props.style)?.backgroundColor)) e = e.parent; return cor(e); };
/** Cor de preenchimento da barra de `id` (o filho do trilho com `accessibilityRole="progressbar"`). */
const corBarra = (id: number): string => cor(meta(id).UNSAFE_queryAllByProps({ accessibilityRole: "progressbar" }).filter((b) => typeof b.type === "string")[0]!.children[0] as never);
const wash = (hex: string) => `${hex}${Math.round(ALFA_WASH_SERIE * 255).toString(16).padStart(2, "0")}`;
const R = (centavos: number) => falado(centavos)!;
const SEM_FRASES = /Falta|Precisa|[Rr]itmo|medido em|Seu ritmo|inclui rendimento|a conferir|Meta batida/;

describe("varios", () => {
  it("a reserva sobe ao topo mesmo se o servidor a mandar por último; as outras ficam na ordem do servidor", async () => {
    const [reserva, ...resto] = V2.metas.varios.metas; comBlocos(bloco("metas", { metas: { ...V2.metas.varios, metas: [...resto, reserva] } })); await perguntar();
    expect(idsNaTela()).toEqual(["meta-1", "meta-2", "meta-3", "meta-4", "meta-5"]); // sem o sort, a reserva ficaria atrás do "+N"
  });
  it("reserva a 25% de 6 meses: 1 bloco cheio em brand e 5 vazios em border; sem barra; o % continua à direita", async () => {
    await mostrar("varios"); const m = meta(1);
    expect(cheios(m)).toHaveLength(1); expect(vazios(m)).toHaveLength(5);
    expect(cor(cheios(m)[0]!)).toBe(claro.brand); expect(cor(vazios(m)[0]!)).toBe(claro.border);
    expect(barras(m)).toEqual([]); expect(m.getByText("25%")).toBeTruthy(); expect(m.getByTestId("icone-PiggyBank")).toBeTruthy(); expect(m.queryByTestId("icone-Target")).toBeNull();
  });
  it("floor, não round: 17999 de 18000 (99%) em 6 meses dá 5 blocos cheios (round daria 6)", async () => {
    const m = await mostrarMeta({ ...base(1), atual: "17999.00", falta: "1.00", percentual: 99 });
    expect(cheios(m)).toHaveLength(5); expect(vazios(m)).toHaveLength(1);
  });
  it("fronteira intermediária com fração abaixo do centavo (2999.995 de 18000, falta 15000.01): 0 cheios, não 1", async () => {
    expect(cheios(await mostrarMeta({ ...base(1), atual: "2999.995", falta: "15000.01", percentual: 16 }))).toHaveLength(0);
    expect(cheios(await mostrarMeta({ ...base(1), atual: "5999.995", falta: "12000.01", percentual: 33 }))).toHaveLength(1);
  });
  it("O1: atual com fração abaixo do centavo (17999.995, 99%, falta 0.01) não enche todos os blocos nem diz mais que o %: 5 cheios", async () => {
    const m = await mostrarMeta({ ...base(1), atual: "17999.995", falta: "0.01", percentual: 99 });
    expect(cheios(m)).toHaveLength(5); expect(vazios(m)).toHaveLength(1); expect(m.queryByTestId("icone-CheckCircle")).toBeNull();
    expect(rotulo(1)).toContain("5 de 6 meses");
  });
  // O servidor trunca o percentual (db/metas.py: int(atual*100//alvo)): 1/6 vira 16%, 2/6 33%, 4/6 66%, 5/6 83%. Contar blocos pelo % perdia um mês nesses casos.
  it.each([1, 2, 3, 4, 5])("custo 3000 x 6 meses (alvo 18000), %i meses guardados: blocos cheios e rótulo 'N de 6 meses' iguais a N, apesar do % truncado", async (k) => {
    const m = await mostrarMeta({ ...base(1), atual: `${k * 3000}.00`, falta: `${18000 - k * 3000}.00`, percentual: Math.floor((k * 100) / 6) });
    expect(cheios(m)).toHaveLength(k); expect(vazios(m)).toHaveLength(6 - k);
    expect(rotulo(1)).toContain(`${k} de 6 meses`);
  });
  it("blocos: 1 centavo abaixo de k meses não fecha o bloco; saldo negativo não passa de 0", async () => {
    expect(cheios(await mostrarMeta({ ...base(1), atual: "11999.99", falta: "6000.01" }))).toHaveLength(3);
    expect(cheios(await mostrarMeta({ ...base(1), atual: "-5.00", falta: "18005.00", percentual: 0 }))).toHaveLength(0);
  });
  it("reserva batida: todos os blocos em positive e o check no lugar do %", async () => {
    const m = await mostrarMeta({ ...base(1), percentual: 100, atual: "18000.00", falta: "0.00" });
    expect(cheios(m)).toHaveLength(6); expect(vazios(m)).toHaveLength(0); expect(cheios(m).every((b) => cor(b) === claro.positive)).toBe(true);
    expect(m.getByTestId("icone-CheckCircle")).toBeTruthy(); expect(m.queryByText("100%")).toBeNull();
  });
  it("reserva sem alvo: nenhum bloco cheio e o aviso no lugar do %", async () => {
    await mostrar("sem_custo"); const m = meta(1);
    expect(cheios(m)).toHaveLength(0); expect(vazios(m)).toHaveLength(6); expect(m.getByTestId("icone-WarningCircle")).toBeTruthy();
    expect(rotulo(1)).toContain("O valor da reserva precisa ser definido no painel"); expect(rotulo(1)).not.toMatch(/\d+ de \d+ meses/); // sem alvo, não diz "0 de 6 meses"
  });
  it("reserva sem saldo (atual nulo): nenhum bloco cheio e o rótulo não fala a contagem de meses", async () => {
    const m = await mostrarMeta({ ...base(1), atual: null, falta: null, percentual: null, por_mes: null });
    expect(cheios(m)).toHaveLength(0); expect(rotulo(1)).not.toMatch(/\d+ de \d+ meses/);
  });
  it("meta comum: ícone de alvo, 60% e barra em 60; sem aviso", async () => {
    await mostrar("varios"); const m = meta(3);
    expect(m.getByTestId("icone-Target")).toBeTruthy(); expect(m.getByText("37%")).toBeTruthy(); expect(barras(m)).toEqual([37]);
    expect(meta(2).getByText("60%")).toBeTruthy(); expect(barras(meta(2))).toEqual([60]);
  });
  it("meta batida: check, barra verde cheia e sem %; o rótulo diz 'meta batida'", async () => {
    await mostrarMeta(base(9)); const m = meta(9);
    expect(m.getByTestId("icone-CheckCircle")).toBeTruthy(); expect(barras(m)).toEqual([100]); expect(m.queryByText("100%")).toBeNull();
    expect(cor({ props: m.UNSAFE_queryAllByProps({ accessibilityRole: "progressbar" }).find((b) => typeof b.type === "string")!.props.children.props })).toBe(claro.positive); // o preenchimento da barra
    expect(rotulo(9)).toContain("meta batida");
  });
  it("sem saldo (atual nulo): sem barra, sem %, com o aviso", async () => {
    await mostrarMeta(base(10)); const m = meta(10);
    expect(barras(m)).toEqual([]); expect(m.getByTestId("icone-WarningCircle")).toBeTruthy(); expect(m.queryByText(/%/)).toBeNull();
    expect(rotulo(10)).toContain("no painel você edita a meta e escolhe outra");
  });
  it("conta pausada com saldo zero ('a conferir'): só o ícone de aviso na tela; a frase mora no rótulo", async () => {
    await mostrarMeta(base(11)); const m = meta(11);
    expect(m.getByTestId("icone-WarningCircle")).toBeTruthy(); expect(m.getByText("0%")).toBeTruthy(); expect(m.queryByText(/conferir|Reconecte/)).toBeNull();
    expect(rotulo(11)).toContain("A conexão com o banco está pausada. Reconecte pra eu voltar a ler.");
  });
  it("REGRA DO DONO: nenhuma frase (Falta, Precisa, ritmo, Nesse ritmo, medido em...) visível no card, com reserva, ritmo, parcial e batida", async () => {
    const c = await mostrarIds([1, 2, 3, 4, 9, 10, 11]); // 10 e 11 têm ⚠: o aviso é só o ícone, a frase fica no rótulo
    expect(c.queryAllByText(SEM_FRASES).length).toBe(0); expect(screen.queryAllByText(/Nesse ritmo/).length).toBe(0);
    expect(c.getAllByText(/%/).length).toBeGreaterThan(0); // controle: o card desenhou texto, só não as frases
    fireEvent.press(c.getByTestId("meta-mais")); // 10 e 11 ficam atrás do "+2"
    const nomes = [1, 2, 3, 4, 9, 10, 11].map((id) => base(id).nome as string);
    const textos = c.getAllByText(/./).map((e) => [e.props.children].flat().join("")); // o CONJUNTO do que se lê na tela, não só uma lista de proibidas
    expect(nomes.every((n) => textos.includes(n))).toBe(true); expect(textos.filter((t) => !nomes.includes(t) && !/^\d+%$/.test(t))).toEqual(["Metas", "Mostrar menos"]); // o título do card e o controle
  });
  it("VoiceOver: nome, %, valores falados e a frase de chegada no rótulo da pílula (Viagem)", async () => {
    await mostrar("varios"); const r = rotulo(2);
    expect(r).toContain("Viagem pro Japão, 60%"); expect(r).toContain(`${R(1200000)} de ${R(2000000)}`);
    expect(r).toContain("prazo junho de 2027"); expect(r).toContain("Nesse ritmo, chega em dezembro de 2027, depois do prazo");
    expect(rotulo(1)).toContain("Reserva de emergência, 25%, 1 de 6 meses"); expect(rotulo(1).toLowerCase().split("reserva de emergência")).toHaveLength(2); // o nome já diz "reserva de emergência": não repete
    await mostrarMeta({ ...base(1), nome: "Colchão" }); expect(rotulo(1)).toContain("Colchão, 25%, reserva de emergência, 1 de 6 meses");
    expect(rotulo(1)).toContain("Nesse ritmo, chega em abril de 2029");
  });
  it("VoiceOver: Notebook sem prazo e ritmo negativo ('a meta não anda'); prazo que passou ('o prazo era')", async () => {
    await mostrar("varios"); expect(rotulo(3)).toContain("Nesse ritmo, a meta não anda."); expect(rotulo(3)).not.toContain("prazo");
    await mostrarMeta(base(8)); expect(rotulo(8)).toContain("o prazo era agosto de 2026");
  });
  it.each([
    [4, "Ainda estou juntando histórico (4 de 7 dias). Volto a calcular seu ritmo a partir de 7 dias."],
    [5, "Ainda estou juntando histórico dos seus bancos. Em alguns dias eu calculo seu ritmo."],
    [7, "Pra saldo de conta eu ainda não meço ritmo."],
  ])("VoiceOver: meta %i sem número de ritmo lê o porquê, uma vez só", async (id, frase) => {
    await mostrarMeta(base(id)); expect(rotulo(id).split(frase)).toHaveLength(2);
  });
  it("sem botão dentro do card (até 5 metas) e o toque nele não abre folha", async () => {
    const c = await mostrarIds([1, 2, 3, 4, 9]);
    expect(c.queryAllByRole("button")).toHaveLength(0); expect(c.queryByTestId("meta-mais")).toBeNull();
    fireEvent.press(screen.getByTestId("bloco-metas")); expect(screen.queryByRole("header", { name: "Metas" })).toBeNull(); expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull();
    expect(screen.getAllByText("Viagem pro Japão")).toHaveLength(1);
  });
});
describe("muitas metas: +N", () => {
  it("reserva e 6 metas: 5 pílulas e '+2'; tocar mostra as 7 no próprio card, sem folha; 'Mostrar menos' volta a 5", async () => {
    const c = await mostrarIds([1, 2, 3, 4, 5, 6, 7]);
    expect(idsNaTela()).toHaveLength(5); expect(c.getByText("+2")).toBeTruthy(); expect(c.getAllByRole("button")).toHaveLength(1);
    expect(c.getByTestId("meta-mais").props.accessibilityLabel).toContain("+2"); // WCAG 2.5.3: o nome acessível contém o texto visível
    fireEvent.press(c.getByRole("button", { name: "+2, mostrar mais 2 metas" }));
    expect(idsNaTela()).toEqual(["meta-1", "meta-2", "meta-3", "meta-4", "meta-5", "meta-6", "meta-7"]); expect(c.getAllByTestId(/^meta-\d+$/)).toHaveLength(7); // as 7 DENTRO do bloco
    expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull(); expect(screen.queryByRole("header", { name: "Metas" })).toBeNull(); expect(c.queryByText("+2")).toBeNull(); expect(c.getByText("Mostrar menos")).toBeTruthy();
    fireEvent.press(c.getByRole("button", { name: "Mostrar menos metas" })); expect(idsNaTela()).toHaveLength(5);
  });
  it("O5: sem reserva, 'Montar reserva' ocupa a vaga dela: com 6 metas, 5 linhas (a de 'Montar reserva' + 4) e '+2'", async () => {
    const c = await mostrarIds([2, 3, 4, 5, 6, 7]);
    expect(c.getByText("Montar reserva")).toBeTruthy(); expect(idsNaTela()).toHaveLength(4); expect(c.getByText("+2")).toBeTruthy();
    fireEvent.press(c.getByTestId("meta-mais")); expect(idsNaTela()).toHaveLength(6);
  });
  it("de volta a 5 depois de 'Mostrar menos'", async () => {
    const c = await mostrarIds([1, 2, 3, 4, 5, 6, 7]);
    fireEvent.press(c.getByRole("button", { name: "+2, mostrar mais 2 metas" })); fireEvent.press(c.getByRole("button", { name: "Mostrar menos metas" }));
    expect(idsNaTela()).toHaveLength(5); expect(c.getByText("+2")).toBeTruthy();
  });
  it("com 5 no total (reserva incluída) não há '+N'; com 6, há '+1'", async () => {
    let c = await mostrarIds([1, 2, 3, 4, 5]); expect(c.queryByTestId("meta-mais")).toBeNull(); expect(idsNaTela()).toHaveLength(5);
    c = await mostrarIds([1, 2, 3, 4, 5, 6]); expect(c.getByText("+1")).toBeTruthy();
    expect(c.getByTestId("meta-mais").props.accessibilityLabel).toBe("+1, mostrar mais 1 meta"); // singular
  });
});
describe("avisos, ressalvas e fallbacks", () => {
  it("banco desatualizado com ritmo presente: só o ícone de aviso na tela; a frase, uma vez, no rótulo", async () => {
    const m = await mostrarMeta({ ...base(2), motivos: ["banco_desatualizado"] });
    expect(m.getByTestId("icone-WarningCircle")).toBeTruthy(); expect(m.queryByText(/não atualiza/)).toBeNull();
    expect(rotulo(2).split("Seu banco não atualiza há um tempo, então o número pode estar velho.")).toHaveLength(2);
  });
  it("sem motivos nem ressalva: nenhum ícone de aviso (controle do teste acima)", async () => {
    const m = await mostrarMeta(base(2)); expect(m.queryByTestId("icone-WarningCircle")).toBeNull();
  });
  it("motivos só do ritmo (historico_curto) não viram aviso à parte: a frase da conexão pausada fica sozinha no rótulo", async () => {
    await mostrarMeta({ ...base(11), atual: "500.00", ritmo: { ...base(11).ritmo, dias_de_historico: 3, motivos: ["conexao_pausada", "historico_curto"] } });
    expect(rotulo(11).split("A conexão com o banco está pausada. Reconecte pra eu voltar a ler.")).toHaveLength(2); expect(rotulo(11)).not.toContain("Ainda estou juntando");
  });
  it("meta batida com conexão pausada: check, aviso e a ressalva no rótulo", async () => {
    const m = await mostrarMeta({ ...base(9), motivos: ["conexao_pausada"], ritmo: { ...base(9).ritmo, valor: null, chega_em: null, motivos: ["conexao_pausada"] } });
    expect(m.getByTestId("icone-CheckCircle")).toBeTruthy(); expect(m.getByTestId("icone-WarningCircle")).toBeTruthy(); expect(rotulo(9)).toContain("A conexão com o banco está pausada.");
  });
  it("reserva com custo e cobranças do banco não lidas: aviso e 'O valor pode estar velho' no rótulo", async () => {
    const m = await mostrarMeta(base(1), { motivos: ["recorrencias_banco_nao_lidas"] });
    expect(m.getByTestId("icone-WarningCircle")).toBeTruthy(); expect(rotulo(1)).toContain("Não consegui ler as cobranças do banco agora. O valor pode estar velho.");
  });
  it("'a conferir' só com motivo do saldo: motivo do alvo (custo_desconhecido) com saldo zero não põe aviso", async () => {
    const m = await mostrarMeta({ ...base(11), motivos: ["custo_desconhecido"], ritmo: { ...base(11).ritmo, motivos: [] } });
    expect(m.queryByTestId("icone-WarningCircle")).toBeNull();
  });
  it("prazo sem 'precisa por mês' (por_mes nulo): o rótulo diz o prazo", async () => {
    await mostrarMeta({ ...base(3), data_alvo: "2027-06-30", por_mes: null }); expect(rotulo(3)).toContain("prazo junho de 2027");
  });
});
describe("outros casos da fixture", () => {
  it("vazio: o botão 'Criar meta' e a linha da reserva com 6 blocos vazios e 'Montar reserva'; nada manda ao painel", async () => {
    const c = await mostrar("vazio");
    expect(c.getByText("Criar meta")).toBeTruthy(); expect(c.getByText("Montar reserva")).toBeTruthy(); expect(c.queryByText(/painel/)).toBeNull();
    expect(vazios(c)).toHaveLength(6); expect(cheios(c)).toHaveLength(0); expect(c.getByTestId("icone-PiggyBank")).toBeTruthy(); expect(c.getAllByRole("button").map((b) => b.props.accessibilityLabel)).toEqual([expect.stringContaining("Montar reserva"), "Criar meta"]);
  });
  // O card pede AO CHAT (como se o usuário tivesse digitado): a mensagem sai pelo POST de sempre, sem navegar nem abrir folha.
  it("'Criar meta' envia 'Quero criar uma meta' no próprio chat; 'Montar reserva' envia o pedido da reserva", async () => {
    await mostrar("vazio"); const antes = posts().length;
    await apertar("Criar meta"); expect(posts()).toHaveLength(antes + 1); expect(JSON.parse(String(posts().at(-1)![1].body))).toMatchObject({ message: "Quero criar uma meta" });
    await waitFor(() => expect(screen.getAllByText("Resposta do Piggy").length).toBeGreaterThan(0)); // a conversa segue no mesmo chat: a pergunta vira bolha
    expect(screen.getByText("Quero criar uma meta")).toBeTruthy();
    await act(async () => { fireEvent.press(screen.getAllByRole("button", { name: /Montar reserva/ }).at(-1)!); await drenar(); }); expect(JSON.parse(String(posts().at(-1)![1].body))).toMatchObject({ message: "Quero montar minha reserva de emergência" });
  });
  it("cota cheia depois da resposta: os botões do card (no chat de verdade) ficam desativados, como a entrada", async () => {
    srv.uso = 99; const c = await mostrar("vazio"); // a resposta com o card grava 100 de 100
    expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy(); expect(c.getByRole("button", { name: "Criar meta" })).toBeDisabled(); expect(c.getByRole("button", { name: /Montar reserva/ })).toBeDisabled();
  });
  it("turno seguinte em voo: os botões do card do turno anterior ficam desativados e não enviam; ao terminar, voltam", async () => {
    await mostrar("vazio"); const g = segurar(); srv.chat = async () => { await g.promessa; return ok("Segunda resposta")(); };
    await enviar("outra pergunta"); const antes = posts().length; expect(antes).toBe(2);
    for (const nome of ["Criar meta", /Montar reserva/]) { const b = screen.getByRole("button", { name: nome }); expect(b).toBeDisabled(); await act(async () => { fireEvent.press(b); await drenar(); }); }
    expect(posts()).toHaveLength(antes);
    g.soltar(); await waitFor(() => expect(screen.getByText("Segunda resposta")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Criar meta" })).toBeEnabled(); // positivo: sem voo, o card volta a pedir
  });
  it("conversa não pronta (bloqueio 'falhou' ao reabrir depois de um erro): os botões do card ficam desativados e não enviam", async () => {
    await mostrar("vazio"); const card = srv.chat; srv.chat = (n) => n === 1 ? card(n) : resposta(500, { detail: "x" });
    await enviar("pergunta que falha"); await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
    srv.statusPendencia = 404; await apertar("Enviar mensagem"); expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy();
    const antes = posts().length;
    for (const nome of ["Criar meta", /Montar reserva/]) { const b = screen.getByRole("button", { name: nome }); expect(b).toBeDisabled(); await act(async () => { fireEvent.press(b); await drenar(); }); }
    expect(posts()).toHaveLength(antes);
  });
  it("valores ocultos ou entrada desligada: 'Criar meta' e 'Montar reserva' ficam desativados e não enviam", () => {
    const enviar = jest.fn(); jest.spyOn(provider, "usePainel").mockImplementation(() => ({ oculto: true }) as never);
    const recursos = { metas: metasV2Schema.parse({ ...V2.metas.vazio }) } as never;
    render(<TemaProvider esquema="light"><BlocoContexto.Provider value={{ recursos, pedir: { enviar, desativado: true } }}><MetasChat /></BlocoContexto.Provider></TemaProvider>);
    for (const nome of ["Criar meta", /Montar reserva/]) { const b = screen.getByRole("button", { name: nome, disabled: true }); fireEvent.press(b); expect(b).toBeDisabled(); }
    expect(enviar).not.toHaveBeenCalled();
  });
  it("positivo do mesmo contexto, ligado: o toque chama `pedir.enviar` com o texto", () => {
    const enviar = jest.fn(); jest.spyOn(provider, "usePainel").mockImplementation(() => ({ oculto: false }) as never);
    const recursos = { metas: metasV2Schema.parse({ ...V2.metas.vazio }) } as never;
    render(<TemaProvider esquema="light"><BlocoContexto.Provider value={{ recursos, pedir: { enviar, desativado: false } }}><MetasChat /></BlocoContexto.Provider></TemaProvider>);
    fireEvent.press(screen.getByRole("button", { name: "Criar meta" })); expect(enviar).toHaveBeenCalledWith("Quero criar uma meta");
  });
  it("sem_custo: já tem reserva (sem a linha 'Montar reserva'); a meta sem saldo tem aviso e o plano no rótulo", async () => {
    const c = await mostrar("sem_custo");
    expect(c.queryByText(/Montar reserva/)).toBeNull(); expect(meta(2).getByTestId("icone-WarningCircle")).toBeTruthy(); expect(barras(meta(2))).toEqual([]);
    expect(rotulo(2)).toContain("Seu plano atual não inclui investimentos."); expect(rotulo(1)).toContain(R(80000));
  });
  it("estresse: 24 blocos da reserva, nome longo sem lançar, e o alvo de R$ 24 bi sai por inteiro no rótulo", async () => {
    const c = await mostrar("estresse");
    expect(idsNaTela()).toEqual(["meta-3", "meta-1", "meta-2"]); expect(meta(3).queryAllByTestId(/^bloco-/)).toHaveLength(24); expect(cheios(meta(3))).toHaveLength(1); // 999999999,99 de 23999999999,76 = exatamente 1 de 24 meses (4% truncado: o % daria 0)
    expect(rotulo(3)).toContain(R(2399999999976)); expect(c.queryByText("—")).toBeNull();
  });
});
describe("cor de série (paridade com a web, #1065)", () => {
  it("metas 2 a 5 do `varios` (série 2,3,0,1): barra, ícone e círculo na cor da série; as 4 distintas e nenhuma é o brand", async () => {
    await mostrar("varios"); const ids = [2, 3, 4, 5], esperado = ids.map((id) => SERIE_META.light[serieDaMeta(id)]!);
    expect(esperado).toEqual([SERIE_META.light[2], SERIE_META.light[3], SERIE_META.light[0], SERIE_META.light[1]]);
    expect(ids.map(corIcone)).toEqual(esperado); expect(ids.map(circulo)).toEqual(esperado.map(wash)); expect(ids.map(corBarra)).toEqual(esperado);
    expect(new Set(esperado).size).toBe(4); expect(esperado).not.toContain(claro.brand);
  });
  it("reserva (id 1): ícone brand, círculo brandSoft e blocos brand, mesmo com a série do id 1 (controle positivo: não pintou a reserva)", async () => {
    await mostrar("varios"); expect(serieDaMeta(1)).toBe(1);
    expect(corIcone(1)).toBe(claro.brand); expect(circulo(1)).toBe(claro.brandSoft); expect(cor(cheios(meta(1))[0]!)).toBe(claro.brand);
  });
  it("batida (id 9): barra em positive, ícone e círculo seguem na série, ✓ presente", async () => {
    const c = await mostrar("varios"); fireEvent.press(c.getByTestId("meta-mais")); const s = SERIE_META.light[serieDaMeta(9)]!;
    expect(corBarra(9)).toBe(claro.positive); expect(corIcone(9)).toBe(s); expect(circulo(9)).toBe(wash(s)); expect(meta(9).getByTestId("icone-CheckCircle").props.tom).toBe("positive");
  });
  it("⚠ (meta 10, sem saldo) continua neutro (inkMuted), com o ícone na série e sem barra", async () => {
    const c = await mostrar("varios"); fireEvent.press(c.getByTestId("meta-mais")); const s = SERIE_META.light[serieDaMeta(10)]!;
    expect(meta(10).getByTestId("icone-WarningCircle").props.tom).toBe("inkMuted"); expect(corIcone(10)).toBe(s); expect(barras(meta(10))).toEqual([]);
  });
  it("a cor segue o id, não a posição: [2,3] e depois [3,2] dão a mesma cor por meta", async () => {
    await mostrarIds([2, 3]); const antes = { 2: corBarra(2), 3: corBarra(3) };
    await mostrarIds([3, 2]); expect(idsNaTela()).toEqual(["meta-3", "meta-2"]); expect({ 2: corBarra(2), 3: corBarra(3) }).toEqual(antes);
    expect(antes[2]).not.toBe(antes[3]);
  });
  it("tema escuro: os hex da web (id 2 = azul #60A5FA) e a reserva no brand do escuro; wash a 12%", () => {
    jest.spyOn(provider, "usePainel").mockImplementation(() => ({ oculto: false }) as never);
    const recursos = { metas: metasV2Schema.parse({ ...V2.metas.varios, metas: [1, 2].map(base) }) } as never;
    render(<TemaProvider esquema="dark"><BlocoContexto.Provider value={{ recursos }}><MetasChat /></BlocoContexto.Provider></TemaProvider>);
    expect(SERIE_META.dark[serieDaMeta(2)]).toBe("#60A5FA");
    expect(corIcone(2)).toBe("#60A5FA"); expect(corBarra(2)).toBe("#60A5FA"); expect(circulo(2)).toBe(wash("#60A5FA")); expect(circulo(1)).toBe(escuro.brandSoft); expect(corIcone(1)).toBe(escuro.brand);
  });
});
describe("valores ocultos", () => {
  it("no chat: ao ocultar, o card some e nenhum dígito de dinheiro fica (controle: antes, a barra 60 e o valor falado no rótulo)", async () => {
    await mostrar("varios");
    expect(barras(screen)).toContain(60); expect(rotulo(2)).toContain(R(1200000));
    fireEvent.press(screen.getByRole("button", { name: "Ocultar valores", hidden: true }));
    expect(barras(screen)).toEqual([]); expect(screen.queryAllByText(/R\$ \d|%/, { hidden: true })).toEqual([]); expect(screen.queryAllByLabelText(/ reais?\b| centavos?\b/, { hidden: true })).toEqual([]);
    expect(screen.queryByTestId("bloco-metas")).toBeNull();
  });
  it("o MetasChat direto, com valores ocultos: sem %, sem barra, zero bloco cheio e rótulo sem valor; o check de batida fica (controle: antes, tudo isso aparece)", () => {
    let oculto = false; jest.spyOn(provider, "usePainel").mockImplementation(() => ({ oculto }) as never);
    const recursos = { metas: metasV2Schema.parse({ ...V2.metas.varios, metas: [1, 3, 9].map(base) }) } as never;
    const ui = () => <TemaProvider esquema="light"><BlocoContexto.Provider value={{ recursos }}><MetasChat /></BlocoContexto.Provider></TemaProvider>;
    const v = render(ui());
    expect(screen.getByText("37%")).toBeTruthy(); expect(barras(screen)).toEqual([37, 100]); expect(screen.getAllByTestId("bloco-cheio").length).toBeGreaterThan(0);
    expect(rotulo(3)).toContain("Notebook novo, 37%"); expect(rotulo(3)).toContain(R(300000));
    oculto = true; v.rerender(ui());
    expect(screen.queryAllByText(/%/).length).toBe(0); expect(barras(screen)).toEqual([]); expect(screen.queryAllByTestId("bloco-cheio").length).toBe(0); // .length: um array de elementos no diff estoura a memória do jest
    expect(screen.getAllByTestId("bloco-vazio")).toHaveLength(6); // a reserva (1) segue desenhada em neutro
    expect(screen.getByTestId("icone-CheckCircle")).toBeTruthy(); // meta 9, batida
    for (const id of [1, 3, 9]) { const r = screen.getByTestId(`meta-${id}`).props.accessibilityLabel as string; expect(r).toContain("Valor oculto"); expect(r).not.toMatch(/reais|centavos|%|\d{3,}/); }
    expect(rotulo(3)).not.toContain("a meta não anda"); // a frase de ritmo/chegada deriva do dinheiro
  });
  it("com valores ocultos o ⚠ continua explicado: o rótulo da meta sem saldo (10) e o da conta pausada (11) leem o motivo, sem dinheiro, % nem 'reais'", () => {
    jest.spyOn(provider, "usePainel").mockImplementation(() => ({ oculto: true }) as never);
    const recursos = { metas: metasV2Schema.parse({ ...V2.metas.varios, metas: [10, 11].map(base) }) } as never;
    render(<TemaProvider esquema="light"><BlocoContexto.Provider value={{ recursos }}><MetasChat /></BlocoContexto.Provider></TemaProvider>);
    expect(rotulo(10)).toContain("no painel você edita a meta e escolhe outra"); expect(rotulo(11)).toContain("A conexão com o banco está pausada. Reconecte pra eu voltar a ler.");
    for (const id of [10, 11]) { expect(rotulo(id)).toContain("Valor oculto"); expect(rotulo(id)).not.toMatch(/reais|centavos|%|\d{3,}/); }
  });
});
