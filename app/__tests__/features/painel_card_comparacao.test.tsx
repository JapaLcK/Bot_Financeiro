import { StyleSheet } from "react-native";
import { render } from "@testing-library/react-native";
import { fireEvent, screen } from "expo-router/testing-library";
import { lerEvento } from "@/api/schemas/painel";
import { BlocoContexto } from "@/features/painel/blocoContexto";
import { CardComparacao } from "@/features/painel/cardComparacao";
import { curvasDaComparacao, rotulosDaComparacao } from "@/features/painel/comparacaoSerie";
import * as provider from "@/features/painel/provider";
import { conteudoBolha } from "@/ui/componentes/CurvaAcumulada";
import { TemaProvider } from "@/ui/tema";
import { claro } from "@/ui/tokens";
import { prepararCaso } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import { reiniciar } from "./conversa_apoio";
import { comBlocos, F, instalarCards, perguntar } from "./conversa_cards_apoio";
import comparacaoJson from "../../../tests/fixtures/ai_chat_bloco_comparacao.json";
// O card `comparacao` (onda 2): a fixture é a do servidor (`tests/test_ai_chat_tool_card_onda2.py` a confere contra o bloco real).
jest.setTimeout(30_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); });
afterEach(() => { jest.restoreAllMocks(); });

const COMP = comparacaoJson;
const base = COMP.abas[0]!.recursos.comparacao as Record<string, any>;
const bloco = (extra: Record<string, unknown> = {}) => ({ ...COMP, abas: [{ mes: null, recursos: { comparacao: { ...base, ...extra } } }] });
const evento = (b: unknown) => lerEvento({ tipo: "bloco", bloco: b });
const recursos = (b: unknown) => (evento(b) as unknown as { bloco: { abas: { recursos: { comparacao: any } }[] } }).bloco.abas[0]!.recursos; // já no formato do app (centavos)
const cat = (categoria: string, delta: number) => ({ categoria, total_atual: Math.max(delta, 0), total_anterior: Math.max(-delta, 0), total_delta: delta, pct: null, estado: "variou" });
const CATALOGO = [{ chave: "alimentacao", nome: "Alimentação", emoji: "🍔", icone: "hamburger" }, { chave: "lazer", nome: "Lazer", emoji: "🎮", icone: "game-controller" }];
const direto = (b: unknown, oculto = false, catalogo: unknown[] = CATALOGO) => {
  jest.spyOn(provider, "usePainel").mockImplementation(() => ({ oculto, dados: { categorias: { fase: "pronto", dado: { categorias: catalogo } } } }) as never);
  return render(<TemaProvider esquema="light"><BlocoContexto.Provider value={{ recursos: recursos(b) as never }}><CardComparacao /></BlocoContexto.Provider></TemaProvider>);
};
const comp = (extra: Record<string, unknown> = {}) => recursos(bloco(extra)).comparacao;
const janela = (j: [string, string, number]) => ({ inicio: j[0], fim: j[1], dias: j[2] });
/** Dois períodos informados (a série da fixture não importa para o rótulo). */
const informados = (a: [string, string, number], b: [string, string, number]) => ({ modo: "periodos_informados", atual: janela(a), anterior: janela(b), dias_corridos: undefined, dias_no_mes: undefined });
const OUT26: [string, string, number] = ["2026-10-01", "2026-10-31", 31], OUT25: [string, string, number] = ["2025-10-01", "2025-10-31", 31], SET26: [string, string, number] = ["2026-09-01", "2026-09-30", 30];
const curva = () => screen.queryByTestId("curva-acumulada", { includeHiddenElements: true });
const rotuloDoCard = () => screen.root.findAll((n) => typeof n.type === "string" && typeof n.props.accessibilityLabel === "string" && n.props.accessibilityLabel.startsWith("Gastos de"))[0]!.props.accessibilityLabel as string;

describe("schema (lerEvento)", () => {
  it("aceita a fixture do servidor e converte o dinheiro para centavos (inclusive a série)", () => {
    const c = recursos(COMP).comparacao;
    expect(c.despesa.total_delta).toBe(-124950); expect(c.categorias[0].total_anterior).toBe(150000);
    expect(c.serie.atual.at(-1)).toBe(210050); expect(c.serie.anterior).toHaveLength(30);
  });
  it.each([
    ["serie null", { serie: null }, null], ["serie ausente", { serie: undefined }, null], ["serie de forma inesperada", { serie: { atual: "x", anterior: [1] } }, null],
    ["serie com ponto não numérico", { serie: { atual: [1, "a"], anterior: [1] } }, null],
  ])("tolerante: %s vira card sem gráfico, o bloco vale", (_n, extra, esperado) => { expect(recursos(bloco(extra)).comparacao.serie ?? null).toBe(esperado); });
  it("aceita série mais curta que `dias`, pct null, sem categorias, sem dias_corridos, receita ausente e despesa null", () => {
    for (const extra of [{ serie: { atual: [1, 2], anterior: [1] } }, { despesa: { ...base.despesa, pct: null, estado: "nova" } }, { categorias: [] }, { dias_corridos: undefined, dias_no_mes: undefined }, { receita: undefined, resultado: undefined }, { despesa: null, motivo: "sem_base" }]) expect(evento(bloco(extra))).not.toBeNull();
  });
  it.each([
    ["dinheiro em texto", { despesa: { ...base.despesa, total_delta: "-1249.50" } }], ["sem modo", { modo: undefined }], ["janela sem dias", { atual: { inicio: "2026-10-01", fim: "2026-10-31" } }],
    ["categorias que não é lista", { categorias: {} }], ["categoria sem nome", { categorias: [{ ...base.categorias[0], categoria: undefined }] }],
  ])("recusa a forma errada: %s", (_n, extra) => { expect(evento(bloco(extra))).toBeNull(); });
});

describe("curvasDaComparacao", () => {
  const de = comp;
  it("mês aberto: atual para em hoje (17 pontos, marca o dia), anterior inteiro; o eixo X é o maior dos dois (31)", () => {
    const c = curvasDaComparacao(de())!; expect([c.pontos.length, c.anterior.length, c.diasNoMes, c.indiceHoje]).toEqual([17, 30, 31, 16]); expect(c.pontos.at(-1)).toBe(210050);
  });
  it("o lado que contém hoje é o ANTERIOR (período informado, sem `dias_corridos`): a atual vem inteira, sem marca de hoje", () => {
    const s = { atual: Array.from({ length: 31 }, (_, i) => i * 100), anterior: Array.from({ length: 10 }, (_, i) => i * 100) };
    const c = curvasDaComparacao(de({ modo: "periodos_informados", dias_corridos: undefined, dias_no_mes: undefined, serie: { atual: s.atual.map((v) => v / 100), anterior: s.anterior.map((v) => v / 100) } }))!; expect([c.pontos.length, c.anterior.length, c.indiceHoje]).toEqual([31, 10, null]);
  });
  it("período mais curto que o outro: o eixo é o maior (fevereiro de 28 contra janeiro de 31)", () => {
    expect(curvasDaComparacao(de({ atual: { inicio: "2026-02-01", fim: "2026-02-28", dias: 28 }, anterior: { inicio: "2026-01-01", fim: "2026-01-31", dias: 31 }, serie: { atual: Array(28).fill(1), anterior: Array(31).fill(1) } }))!.diasNoMes).toBe(31);
  });
  const serie = (n: number, m = 30) => ({ serie: { atual: Array(n).fill(10), anterior: Array(m).fill(10) } }), semHoje = { dias_corridos: undefined, dias_no_mes: undefined };
  // A tabela inteira de `indiceHoje`: só `mes_aberto` com `dias_corridos` válido (0 < n <= pontos) sabe qual é hoje; o resto é `null`, nunca "o fim da série".
  it.each([
    ["mes_aberto, a série para em hoje", { dias_corridos: 12, ...serie(12) }, 11],
    ["mes_aberto, lançamento futuro estica a série além de hoje", { dias_corridos: 12, ...serie(25) }, 11],
    ["mes_aberto, lançamento futuro no último dia (a série cobre o mês todo)", { dias_corridos: 12, ...serie(31) }, 11],
    ["mes_aberto, `dias_corridos` ausente", { ...semHoje, ...serie(12) }, null],
    ["mes_aberto, `dias_corridos` 0", { dias_corridos: 0, ...serie(12) }, null],
    ["mes_aberto, `dias_corridos` maior que a série", { dias_corridos: 20, ...serie(12) }, null],
    ["mes_fechado, série cheia", { modo: "mes_fechado", ...semHoje, ...serie(31) }, null],
    ["mes_fechado, série curta", { modo: "mes_fechado", ...semHoje, ...serie(12) }, null],
    ["periodos_informados, mês corrente: hoje dia 12 e lançamento no dia 15 (15 pontos, sem `dias_corridos`)", { ...informados(OUT26, SET26), ...serie(15) }, null],
  ])("indiceHoje: %s", (_n, extra, esperado) => { expect(curvasDaComparacao(de(extra))!.indiceHoje).toBe(esperado); });
  it.each([["null", { serie: null }], ["vazia", { serie: { atual: [], anterior: [1] } }], ["ponto negativo", { serie: { atual: [-1, 2], anterior: [1] } }]])("%s: sem gráfico (nunca linha inventada)", (_n, extra) => { expect(curvasDaComparacao(de(extra))).toBeNull(); });
});

describe("rotulosDaComparacao (a legenda usa o rótulo como `key`: nunca repete)", () => {
  const r = (a: [string, string, number], b: [string, string, number]) => rotulosDaComparacao(comp(informados(a, b)));
  it("mês corrente × o mesmo mês do ano passado, em períodos informados: nomes de mês com o ano, legenda sem key duplicada", () => {
    const erro = jest.spyOn(console, "error").mockImplementation(() => undefined);
    expect(r(OUT26, OUT25)).toEqual({ atual: "out/2026", anterior: "out/2025" });
    direto(bloco(informados(OUT26, OUT25)));
    expect(screen.getByText("out/2026", { includeHiddenElements: true })).toBeTruthy(); expect(screen.getByText("out/2025", { includeHiddenElements: true })).toBeTruthy(); expect(screen.getAllByTestId("curva-legenda", { includeHiddenElements: true })).toHaveLength(2);
    expect(erro).not.toHaveBeenCalled();
  });
  it("meses inteiros de anos iguais viram nome de mês (set × out, fevereiro bissexto × janeiro)", () => {
    expect(r(["2026-10-01", "2026-10-31", 31], SET26)).toEqual({ atual: "out", anterior: "set" });
    expect(r(SET26, ["2026-10-01", "2026-10-31", 31])).toEqual({ atual: "set", anterior: "out" });
    expect(r(["2024-02-01", "2024-02-29", 29], ["2024-01-01", "2024-01-31", 31])).toEqual({ atual: "fev", anterior: "jan" });
  });
  it("período livre sai 'dd/mm a dd/mm'; mês quase inteiro (até o dia 30 de outubro) é livre", () => {
    expect(r(["2026-10-01", "2026-10-15", 15], ["2026-09-10", "2026-09-24", 15])).toEqual({ atual: "01/10 a 15/10", anterior: "10/09 a 24/09" });
    expect(r(["2026-10-01", "2026-10-30", 30], SET26).atual).toBe("01/10 a 30/10");
  });
  it("períodos livres com o mesmo dd/mm em anos diferentes ganham o ano", () => {
    expect(r(["2026-10-01", "2026-10-15", 15], ["2025-10-01", "2025-10-15", 15])).toEqual({ atual: "01/10/2026 a 15/10/2026", anterior: "01/10/2025 a 15/10/2025" });
  });
  it.each([
    ["meses de anos diferentes", OUT26, OUT25], ["meses seguidos", OUT26, SET26], ["livres iguais em dd/mm", ["2026-10-01", "2026-10-15", 15], ["2025-10-01", "2025-10-15", 15]],
    ["janelas idênticas (o servidor não emite)", OUT26, OUT26], ["mês inteiro × livre", OUT26, ["2026-09-01", "2026-09-15", 15]],
  ] as const)("invariante atual !== anterior: %s", (_n, a, b) => { const { atual, anterior } = r(a as never, b as never); expect(atual).not.toBe(anterior); });
  it("nos outros modos o nome é o do mês (aberto/fechado)", () => { expect(rotulosDaComparacao(comp())).toEqual({ atual: "out", anterior: "set" }); });
});

describe("conteudoBolha com rótulos", () => {
  it("a bolha nomeia o período anterior pelo rótulo, não 'mês passado'", () => {
    const pontos = [10000, 30000];
    expect(conteudoBolha(1, pontos, [20000, 50000], "set").anterior).toBe("set R$ 500,00"); expect(conteudoBolha(1, pontos, [20000, 50000]).anterior).toBe("mês passado R$ 500,00");
  });
});

describe("CardComparacao", () => {
  it("a fixture: delta de despesa com sinal e o chip de % cinza no mês aberto, 'dia 17 de 31', as duas linhas e as 3 categorias", () => {
    direto(COMP);
    expect(screen.getByText("−R$ 1.249,50")).toBeTruthy(); expect(screen.getByText("-37,3%")).toBeTruthy(); expect(screen.getByText("dia 17 de 31")).toBeTruthy();
    expect(StyleSheet.flatten(screen.getByTestId("chip-pct").props.style).backgroundColor).toBe(claro.surfaceControl);
    expect(curva()).toBeTruthy(); fireEvent(screen.getByTestId("curva-plot", { includeHiddenElements: true }), "layout", { nativeEvent: { layout: { width: 300, height: 130 } } }); // o SVG só nasce com a largura medida
    expect(screen.getByTestId("curva-linha", { includeHiddenElements: true })).toBeTruthy(); expect(screen.getByTestId("curva-anterior", { includeHiddenElements: true })).toBeTruthy();
    expect(screen.getAllByTestId("curva-legenda", { includeHiddenElements: true })).toHaveLength(2); expect(screen.getByText("out", { includeHiddenElements: true })).toBeTruthy(); expect(screen.getByText("set", { includeHiddenElements: true })).toBeTruthy();
    expect(screen.getByText("−R$ 800,00")).toBeTruthy(); expect(screen.getByText("−R$ 200,00")).toBeTruthy(); expect(screen.getByText("+R$ 45,00")).toBeTruthy();
  });
  it("periodos_informados no mês corrente com lançamento futuro (15 pontos, sem `dias_corridos`): o anel de hoje não é desenhado, a linha sim; no mês aberto (controle) o anel existe", () => {
    const abrir = () => fireEvent(screen.getByTestId("curva-plot", { includeHiddenElements: true }), "layout", { nativeEvent: { layout: { width: 300, height: 130 } } });
    const v = direto(bloco({ ...informados(OUT26, SET26), serie: { atual: Array(15).fill(10), anterior: Array(30).fill(10) } })); abrir();
    expect(screen.getByTestId("curva-linha", { includeHiddenElements: true })).toBeTruthy(); expect(screen.queryByTestId("ponto-hoje", { includeHiddenElements: true })).toBeNull();
    v.unmount(); direto(COMP); abrir(); expect(screen.getByTestId("ponto-hoje", { includeHiddenElements: true })).toBeTruthy();
  });
  it("diminuiu é verde, aumentou é atenção (nunca vermelho)", () => {
    const cor = (t: string) => StyleSheet.flatten(screen.getByText(t).props.style).color;
    const v = direto(COMP); expect(cor("−R$ 1.249,50")).toBe(claro.positive); expect(cor("+R$ 45,00")).toBe(claro.warning);
    v.unmount(); direto(bloco({ despesa: { ...base.despesa, total_delta: 300, pct: 9 } })); expect(cor("+R$ 300,00")).toBe(claro.warning);
  });
  it("período terminado: o chip é rosa claro; pct null: sem chip", () => {
    const v = direto(bloco({ modo: "mes_fechado", dias_corridos: undefined, dias_no_mes: undefined }));
    expect(StyleSheet.flatten(screen.getByTestId("chip-pct").props.style).backgroundColor).toBe(claro.brandSoft); expect(screen.queryByText(/dia \d+ de/)).toBeNull();
    v.unmount(); direto(bloco({ despesa: { ...base.despesa, total_delta: 4500, pct: null, estado: "nova" } }));
    expect(screen.queryByTestId("chip-pct")).toBeNull(); expect(screen.getByText("+R$ 45,00", { exact: true })).toBeTruthy();
  });
  it("no máximo 3 categorias (a ordem é a do servidor) e o ícone vem do catálogo pelo nome, sem acento/caixa; sem par, `tag`", () => {
    direto(bloco({ categorias: [cat("alimentacao", -10), cat("LAZER", -20), cat("Streaming", 30), cat("Quarta", 40), cat("Quinta", 50)] }));
    expect(screen.queryByText("Quarta")).toBeNull(); expect(screen.queryByText("Quinta")).toBeNull(); expect(screen.getByText("Streaming")).toBeTruthy();
    expect(["hamburger", "game-controller", "tag"].map((n) => screen.queryAllByTestId(`icone-categoria-${n}`, { includeHiddenElements: true }).length)).toEqual([1, 1, 1]);
  });
  it("o ícone vem do catálogo INTEIRO: a categoria que zerou no período (Lazer, `sem_gasto`) e a nova (Streaming) acham o seu; sem par no catálogo, `tag`", () => {
    const completo = [...CATALOGO, { chave: "streaming", nome: "Streaming", emoji: "📺", icone: "television" }], icones = (n: string) => screen.queryAllByTestId(`icone-categoria-${n}`, { includeHiddenElements: true }).length;
    const v = direto(COMP, false, completo); expect(["hamburger", "game-controller", "television", "tag"].map(icones)).toEqual([1, 1, 1, 0]);
    v.unmount(); direto(COMP, false, CATALOGO.filter((c) => c.chave !== "lazer")); expect(["hamburger", "game-controller", "tag"].map(icones)).toEqual([1, 0, 2]); // controle: sem Lazer no catálogo, só ela cai em `tag` (e a Streaming, também fora)
  });
  it("total_delta 0 com pct não nulo: sem sinal, tom neutro, sem seta, 'sem diferença' no rótulo falado", () => {
    direto(bloco({ despesa: { ...base.despesa, total_atual: 100, total_anterior: 100, total_delta: 0, pct: 0, estado: "igual" } }));
    expect(screen.getByText("R$ 0,00")).toBeTruthy(); expect(StyleSheet.flatten(screen.getByText("R$ 0,00").props.style).color).toBe(claro.ink);
    expect(screen.getByText("0,0%")).toBeTruthy(); expect(screen.getByTestId("chip-pct").children).toHaveLength(1); // só o texto, sem seta
    expect(rotuloDoCard()).toContain("contra set: sem diferença, 0,0 por cento");
  });
  it("pct negativo é falado sem o sinal ('a menos, 37,3 por cento', nunca '-37,3')", () => {
    direto(COMP); const r = rotuloDoCard();
    expect(r).toContain("a menos, 37,3 por cento"); expect(r).not.toContain("-37,3");
  });
  it("os `motivos` do bloco aparecem como ressalva; sem motivos, nada", () => {
    const v = direto(COMP); expect(screen.queryByLabelText("Ver ressalvas")).toBeNull();
    v.unmount(); direto(bloco({ motivos: ["cobertura_parcial"] })); expect(screen.getByLabelText("Ver ressalvas")).toBeTruthy();
  });
  it("`motivo` singular do bloco (categorias_indisponiveis, com categorias vazias) vira ressalva; sem motivo nenhum, nada; repetido em `motivos`, um aviso só", () => {
    const aviso = "Não deu para ler as categorias agora.", abrir = () => fireEvent.press(screen.getByLabelText("Ver ressalvas"));
    const v = direto(bloco({ motivo: "categorias_indisponiveis", categorias: [], motivos: [] })); abrir(); expect(screen.getAllByText(aviso)).toHaveLength(1);
    v.unmount(); direto(bloco({ motivo: null, motivos: [] })); expect(screen.queryByLabelText("Ver ressalvas")).toBeNull();
    v.unmount(); direto(bloco({ motivo: "categorias_indisponiveis", motivos: ["categorias_indisponiveis"] })); abrir(); expect(screen.getAllByText(aviso)).toHaveLength(1);
  });
  it("serie null: o card sai sem gráfico, com valor, chip e categorias; sem categorias: só o topo", () => {
    const v = direto(bloco({ serie: null })); expect(curva()).toBeNull(); expect(screen.getByText("−R$ 1.249,50")).toBeTruthy(); expect(screen.getByText("-37,3%")).toBeTruthy(); expect(screen.getByText("Alimentação")).toBeTruthy();
    v.unmount(); direto(bloco({ categorias: [] })); expect(screen.queryByText("Alimentação")).toBeNull(); expect(screen.getByText("−R$ 1.249,50")).toBeTruthy();
  });
  it("despesa null (o servidor não deveria emitir): texto de indisponível, sem quebrar", () => { direto(bloco({ despesa: null })); expect(screen.getByText("Dado indisponível neste card.")).toBeTruthy(); });
  it("UM elemento falado com os números por extenso, o dia e as categorias", () => {
    direto(COMP); const r = rotuloDoCard();
    expect(r).toContain("1249 reais e 50 centavos a menos"); expect(r).toContain("37,3 por cento"); expect(r).toContain("dia 17 de 31"); expect(r).toContain("Alimentação, 800 reais a menos"); expect(r).toContain("Streaming, 45 reais a mais");
    expect(r).toContain("Lazer, 200 reais a menos");
  });
  it("valores ocultos: sem R$, sem %, sem gráfico e sem número no rótulo; visível mostra (controle)", () => {
    const v = direto(COMP, true);
    expect(screen.queryAllByText(/R\$ \d|%/)).toEqual([]); expect(screen.queryByTestId("chip-pct")).toBeNull(); expect(curva()).toBeNull();
    expect(rotuloDoCard()).not.toMatch(/reais?\b|centavos?\b|por cento/); expect(screen.getAllByText("R$ ••••").length).toBeGreaterThan(0);
    v.unmount(); direto(COMP, false); expect(screen.getAllByText(/R\$ \d/).length).toBeGreaterThan(0);
  });
});

describe("no chat", () => {
  it("o título nomeia os dois períodos e o card desenha (o ícone cai em `tag` quando o catálogo não chega)", async () => {
    comBlocos(bloco() as never); await perguntar();
    expect(screen.getByText("Gastos · out × set")).toBeTruthy(); expect(screen.getByText("−R$ 1.249,50")).toBeTruthy(); expect(curva()).toBeTruthy();
  });
  it("o ícone chega do `/api/app/categorias` pelo provider (o schema do catálogo guarda o `icone`)", async () => {
    const alimentacao = F["/api/app/categorias"].categorias.find((c: { nome: string }) => c.nome === "Alimentação"); alimentacao.icone = "hamburger";
    try {
      instalarCards(true); comBlocos(bloco() as never); await perguntar();
      expect(await screen.findByTestId("icone-categoria-hamburger", { includeHiddenElements: true })).toBeTruthy();
    } finally { delete alimentacao.icone; }
  });
});
