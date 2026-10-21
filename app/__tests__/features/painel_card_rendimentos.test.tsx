import { act } from "react";
import { Dimensions, StyleSheet } from "react-native";
import { fireEvent, render, screen } from "@testing-library/react-native";
import { rendimentoSchema } from "@/api/schemas/painel";
import { temMotivoBanco } from "@/features/painel/base";
import { CardRendimentos } from "@/features/painel/cardRendimentos";
import * as privacidade from "@/features/painel/privacidade";
import * as provider from "@/features/painel/provider";
import { TipografiaPainelProvider } from "@/features/painel/tipografia";
import { TemaProvider } from "@/ui/tema";
import { ALFA_FUNDO_TOM, claro, tingir } from "@/ui/tokens";
import fixture from "./painel.fixture.json";

// O card "Rendimentos": o servidor calcula (db/rendimentos.py), o app exibe. A fixture é o contrato novo; sem `rendimentos` é o servidor antigo.
const BASE = fixture["/api/app/rendimento"] as Record<string, any>;
const ativo = (nome: string, mes: string | null, total: string | null = null, extra: Record<string, unknown> = {}) => ({ nome, instituicao: "Nubank", logo_proprio: null, saldo: "1000.00", mes: { valor: mes, pct: mes ? "1.00" : null, motivo: mes ? null : "sem_base" }, total: { valor: total, pct: total ? "2.00" : null, motivo: total ? null : "sem_custo" }, ...extra });
const categoria = (tipo: string, rotulo: string, ativos: any[], extra: Record<string, unknown> = {}) => ({ tipo, rotulo, saldo: "1000.00", quantidade: ativos.length, mes: { valor: "10.00", pct: "1.00", sem_dado: 0 }, total: { valor: "20.00", pct: "2.00", sem_dado: 0 }, ativos, ...extra });
const com = (rendimentos: Record<string, unknown> | null | undefined) => rendimentoSchema.parse({ ...BASE, rendimentos });
const rend = (extra: Record<string, unknown>) => ({ ...BASE.rendimentos, ...extra });
const desenhar = (dado: ReturnType<typeof com>, oculto = false) => {
  jest.spyOn(provider, "usePainel").mockImplementation(() => ({ oculto }) as never);
  jest.spyOn(privacidade, "usePrivacidade").mockImplementation(() => ({ oculto }) as never); // o `Valor` lê a privacidade, não o `usePainel`
  return render(<TemaProvider esquema="light"><CardRendimentos d={dado} /></TemaProvider>);
};
const abrir = (rotulo: string) => fireEvent.press(screen.getByText(rotulo));
const cor = (t: string | RegExp) => StyleSheet.flatten(screen.getAllByText(t)[0]!.props.style).color;
afterEach(() => jest.restoreAllMocks());

describe("schema", () => {
  it("a fixture nova parseia, com o dinheiro em centavos e o pct como texto", () => {
    const r = com(BASE.rendimentos)!.rendimentos!;
    expect(r.saldo).toBe(1250000); expect(r.mes.valor).toBe(12000); expect(r.mes.pct).toBe("1.00"); expect(r.categorias[0]!.ativos[1]!.total.valor).toBe(25000);
    expect(com(BASE.rendimentos).itens).toHaveLength(1); // `itens` e `motivos` do topo seguem iguais
  });
  it("payload antigo (sem `rendimentos`) e payload fora do contrato dão null, sem derrubar `itens`", () => {
    expect(com(undefined).rendimentos).toBeNull(); expect(com(undefined).itens).toHaveLength(1);
    expect(com(rend({ saldo: "1E-7" })).rendimentos).toBeNull(); expect(com(rend({ categorias: "x" })).rendimentos).toBeNull();
  });
  it("ativo inválido sai da lista; os outros ficam", () => {
    const c = categoria("renda_fixa", "Renda fixa", [ativo("CDB", "5.00"), { ...ativo("Quebrado", "5.00"), mes: "x" }]);
    expect(com(rend({ categorias: [c] }))!.rendimentos!.categorias[0]!.ativos.map((a) => a.nome)).toEqual(["CDB"]);
  });
});

describe("tolerância", () => {
  it("categoria inválida sai da lista; as outras ficam e o bloco não zera", () => {
    const ok = categoria("renda_fixa", "Renda fixa", [ativo("CDB", "5.00")]);
    const r = com(rend({ categorias: [ok, { ...categoria("acoes", "Ações", []), mes: "x" }] }))!.rendimentos!;
    expect(r.categorias.map((c) => c.tipo)).toEqual(["renda_fixa"]);
  });
  it("`banco_desatualizado` do bloco chega ao aviso único do topo (o card não repete estado de banco)", () => {
    expect(temMotivoBanco(com(rend({ motivos: ["banco_desatualizado"] })))).toBe(true); expect(temMotivoBanco(com(BASE.rendimentos))).toBe(false);
  });
});

describe("estados", () => {
  it("`rendimentos` null: diz que não deu para ler agora", () => {
    desenhar(com(undefined)); expect(screen.getByText("Não foi possível ler seus rendimentos agora.")).toBeTruthy(); expect(screen.queryByRole("tab", { name: "Rendimento do mês" })).toBeNull();
  });
  it("plano sem investimentos: o texto que já existe", () => {
    desenhar(com(rend({ quantidade: 0, categorias: [], motivos: ["plano_sem_investimentos"] }))); expect(screen.getByText("Seu plano atual não inclui investimentos.")).toBeTruthy();
  });
  it("sem posições: vazio, sem número", () => {
    desenhar(com(rend({ quantidade: 0, categorias: [], motivos: ["sem_posicoes"] }))); expect(screen.getByText("Não encontrei investimentos nos seus bancos conectados.")).toBeTruthy(); expect(screen.queryByText(/R\$/)).toBeNull();
  });
});

describe("seletor Mês | Total", () => {
  it("abre em Mês e o toque em Total troca o número, o pct e o rodapé", () => {
    desenhar(com(BASE.rendimentos));
    expect(screen.getAllByText(/\+R\$ 120,/)[0]).toBeTruthy(); expect(screen.getAllByText("+1,00%").length).toBeGreaterThan(0); expect(screen.getByText(/neste mês · saldo R\$ 12\.500,00/)).toBeTruthy(); expect(screen.getByText(/Estimado pela variação do saldo desde 30\/09/)).toBeTruthy();
    fireEvent.press(screen.getByRole("tab", { name: "Rendimento total desde a aplicação" }));
    expect(screen.getAllByText(/\+R\$ 350,/)[0]).toBeTruthy(); expect(screen.getAllByText("+3,00%").length).toBeGreaterThan(0); expect(screen.getByText(/desde a aplicação · saldo/)).toBeTruthy(); expect(screen.getByText(/Saldo menos o valor aplicado/)).toBeTruthy();
    expect(screen.getByText(/Open Finance sincronizado em 06\/10 /)).toBeTruthy();
  });
  it("D4: sem nenhum ativo com dado no Mês, abre em Total; com dado, abre em Mês", () => {
    desenhar(com(rend({ mes: { valor: null, pct: null, sem_dado: 3 } })));
    expect(screen.getByRole("tab", { name: "Rendimento total desde a aplicação" }).props.accessibilityState.selected).toBe(true); expect(screen.getByText(/desde a aplicação · saldo/)).toBeTruthy();
    screen.unmount(); desenhar(com(BASE.rendimentos));
    expect(screen.getByRole("tab", { name: "Rendimento do mês" }).props.accessibilityState.selected).toBe(true);
  });
  it("o usuário volta ao Mês vazio: explica quando a foto do mês anterior chega", () => {
    desenhar(com(rend({ mes: { valor: null, pct: null, sem_dado: 3 } }))); fireEvent.press(screen.getByRole("tab", { name: "Rendimento do mês" }));
    expect(screen.getAllByText("Não informado")[0]).toBeTruthy(); expect(screen.getByText(/O rendimento do mês aparece quando houver a foto do fechamento/)).toBeTruthy();
  });
});

describe("valores", () => {
  it("ganho em verde com +; perda em ink com −, nunca vermelho, rosa ou atenção", () => {
    const perda = categoria("renda_fixa", "Renda fixa", [ativo("CDB", "-5.00")], { mes: { valor: "-5.00", pct: "-0.50", sem_dado: 0 } });
    desenhar(com(rend({ mes: { valor: "-5.00", pct: "-0.50", sem_dado: 0 }, categorias: [perda] })));
    expect(screen.getAllByText(/^−R\$ 5,/).length).toBeGreaterThan(0); expect(screen.getAllByText("−0,50%").length).toBeGreaterThan(0);
    for (const t of screen.getAllByText(/^−R\$ 5,/)) expect([claro.ink]).toContain(StyleSheet.flatten(t.props.style).color);
    expect(cor("−0,50%")).toBe(claro.ink); expect(cor("−0,50%")).not.toBe(claro.danger); expect(cor("−0,50%")).not.toBe(claro.warning);
    screen.unmount(); desenhar(com(BASE.rendimentos)); expect(cor(/^\+R\$ 120,/)).toBe(claro.positive);
  });
  it("valor null é 'Não informado' e o resto da categoria segue; nunca R$ 0,00", () => {
    desenhar(com(BASE.rendimentos)); expect(screen.getByText("Ações")).toBeTruthy(); expect(screen.getAllByText("Não informado").length).toBeGreaterThan(0); expect(screen.queryByText(/R\$ 0,00/)).toBeNull();
  });
  it("linha parcial: 'N de M ativos sem rendimento informado' só com sem_dado > 0", () => {
    desenhar(com(BASE.rendimentos)); expect(screen.getByText("1 de 3 ativos sem rendimento informado")).toBeTruthy();
    screen.unmount(); desenhar(com(rend({ mes: { valor: "120.00", pct: "1.00", sem_dado: 0 } }))); expect(screen.queryByText(/ativos sem rendimento informado/)).toBeNull();
  });
  it("valores ocultos: nenhum dígito de dinheiro, sem pct e sem barra", () => {
    desenhar(com(BASE.rendimentos), true); abrir("Renda fixa");
    expect(screen.queryByText(/\d,\d\d/)).toBeNull(); expect(screen.queryByText(/%/)).toBeNull(); expect(screen.queryByTestId("barra-categorias")).toBeNull(); expect(screen.getAllByText("R$ ••••").length).toBeGreaterThan(0);
  });
});

describe("categorias e ativos", () => {
  it("expandir mostra os ativos do período escolhido, do que mais rendeu ao menos; nulos no fim", () => {
    const c = categoria("renda_fixa", "Renda fixa", [ativo("Zero", null, "9.00"), ativo("Neg", "-3.00", "9.00"), ativo("Pouco", "1.00", "9.00"), ativo("Muito", "7.00", "9.00")]);
    desenhar(com(rend({ categorias: [c] }))); expect(screen.queryByText("Muito")).toBeNull(); abrir("Renda fixa");
    const nomes = () => screen.getAllByText(/^(Zero|Neg|Pouco|Muito)$/).map((n) => n.props.children);
    expect(nomes()).toEqual(["Muito", "Pouco", "Neg", "Zero"]);
    fireEvent.press(screen.getByRole("tab", { name: "Rendimento total desde a aplicação" })); expect(nomes()).toEqual(["Zero", "Neg", "Pouco", "Muito"]); // empate no Total: a ordem do servidor
  });
  it("5 ativos e 'Ver mais N'; 'Ver menos' recolhe", () => {
    const c = categoria("acoes", "Ações", Array.from({ length: 8 }, (_, i) => ativo(`Ativo${i}`, `${8 - i}.00`)));
    desenhar(com(rend({ categorias: [c] }))); abrir("Ações");
    expect(screen.getAllByText(/^Ativo\d$/)).toHaveLength(5); fireEvent.press(screen.getByRole("button", { name: "Ver mais 3" })); expect(screen.getAllByText(/^Ativo\d$/)).toHaveLength(8);
    fireEvent.press(screen.getByRole("button", { name: "Ver menos" })); expect(screen.getAllByText(/^Ativo\d$/)).toHaveLength(5);
  });
  it("uma categoria aberta por vez", () => {
    desenhar(com(BASE.rendimentos)); abrir("Renda fixa"); expect(screen.getByText("CDB")).toBeTruthy(); abrir("Ações"); expect(screen.queryByText("CDB")).toBeNull(); expect(screen.getByText("PETR4")).toBeTruthy();
  });
  it("a barra some quando nenhuma categoria rendeu no período (tudo ≤ 0)", () => {
    desenhar(com(BASE.rendimentos)); expect(screen.getByTestId("barra-categorias")).toBeTruthy();
    screen.unmount(); desenhar(com(rend({ categorias: [categoria("renda_fixa", "Renda fixa", [ativo("CDB", "-5.00")], { mes: { valor: "-5.00", pct: "-0.50", sem_dado: 0 } })] }))); expect(screen.queryByTestId("barra-categorias")).toBeNull();
  });
});

describe("bordas de exibição", () => {
  it("fonte ao vivo: o valor remonta (remede) sem trocar o dado", async () => {
    const janela = Dimensions.get("window");
    try {
      const fonte = (fontScale: number) => act(async () => { Dimensions.set({ window: { ...janela, width: 402, height: 874, fontScale } }); }); // o padrão do jest já é 2
      await fonte(1); jest.spyOn(provider, "usePainel").mockImplementation(() => ({ oculto: false }) as never);
      render(<TemaProvider esquema="light"><TipografiaPainelProvider><CardRendimentos d={com(BASE.rendimentos)} /></TipografiaPainelProvider></TemaProvider>);
      const antes = screen.getAllByLabelText("mais 120 reais")[0]; // o do herói
      await fonte(2);
      expect(screen.getAllByLabelText("mais 120 reais")[0]).not.toBe(antes);
    } finally { await act(async () => { Dimensions.set({ window: janela }); }); }
  });
  it("oculto: o accessibilityLabel do valor é 'Valor oculto', sem dígito nem ganho/perda", () => {
    desenhar(com(BASE.rendimentos), true); abrir("Renda fixa");
    const labels: string[] = [];
    screen.root.findAll((n: any) => { const l = n.props?.accessibilityLabel; if (typeof l === "string") labels.push(l); return false; });
    expect(labels).toContain("Valor oculto"); expect(labels.filter((l) => /\d|real|reais|centavo|ganho|perda/i.test(l))).toEqual([]);
  });
  it("sincronizado_em inválido: some a frase de sincronização, sem 'Invalid Date'", () => {
    desenhar(com(rend({ sincronizado_em: "lixo" }))); expect(screen.queryByText(/Invalid Date|sincronizado/)).toBeNull(); expect(screen.getByText(/Estimado pela variação/)).toBeTruthy();
  });
  it("pct: zero exato sem sinal; não numérico some; o valor segue", () => {
    desenhar(com(rend({ mes: { valor: "0.00", pct: "0.00", sem_dado: 0 } }))); expect(screen.getByText("0,00%")).toBeTruthy(); expect(screen.queryByText("+0,00%")).toBeNull();
    screen.unmount(); desenhar(com(rend({ mes: { valor: "5.00", pct: "abc", sem_dado: 0 } }))); expect(screen.queryByText(/abc/)).toBeNull(); expect(screen.queryByTestId("chip-pct")).toBeNull(); expect(screen.getAllByText(/^\+R\$ 5,/)[0]).toBeTruthy();
  });
  it("categoria sem ativos legíveis: não diz '3 ativos' e expande com 'Não informado'", () => {
    desenhar(com(rend({ categorias: [categoria("renda_fixa", "Renda fixa", [{ ...ativo("Q", "1.00"), mes: "x" }], { quantidade: 3 })] })));
    expect(screen.queryByText(/^3 ativos/)).toBeNull(); expect(screen.getByText(/^0 ativos/)).toBeTruthy(); expect(screen.queryByText("Não informado")).toBeNull(); abrir("Renda fixa"); expect(screen.getByText("Não informado")).toBeTruthy();
  });
});

describe("Money, escopo do mockup", () => {
  it("o valor fala 'mais/menos' como o resto do app e o grande encolhe em vez de virar reticências", () => {
    const perda = categoria("renda_fixa", "Renda fixa", [ativo("CDB", "-5.00")], { mes: { valor: "-5.00", pct: "-0.50", sem_dado: 0 } });
    desenhar(com(rend({ mes: { valor: "-5.00", pct: "-0.50", sem_dado: 0 }, categorias: [perda] })));
    expect(screen.getAllByLabelText(/^menos 5 reais/).length).toBeGreaterThan(0);
    screen.unmount(); desenhar(com(BASE.rendimentos));
    expect(screen.getAllByLabelText(/^mais 120 reais/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^\+R\$ 120,/)[0]!.props.adjustsFontSizeToFit).toBe(true);
  });
  it("linha da categoria: 'N ativos · saldo' e o pct do período embaixo do valor", () => {
    desenhar(com(rend({ categorias: [categoria("renda_fixa", "Renda fixa", [ativo("CDB", "5.00")], { saldo: "1234.50" })] })));
    expect(screen.getByText("1 ativo · R$ 1.234,50")).toBeTruthy(); expect(screen.getAllByText("+1,00%").length).toBe(2); // chip do topo + categoria
    fireEvent.press(screen.getByRole("tab", { name: "Rendimento total desde a aplicação" })); expect(screen.getAllByText("+2,00%").length).toBe(1); // só a categoria (o topo é +3,00%)
  });
  it("linha do ativo: 'Instituição · saldo'; sem saldo, só a instituição", () => {
    desenhar(com(rend({ categorias: [categoria("renda_fixa", "Renda fixa", [ativo("CDB", "5.00", null, { saldo: "800.00" }), ativo("LCI", "4.00", null, { saldo: null })])] }))); abrir("Renda fixa");
    expect(screen.getByText("Nubank · R$ 800,00")).toBeTruthy(); expect(screen.getByText("Nubank")).toBeTruthy();
  });
  it("tile da categoria com fundo tingido pela cor da série", () => {
    desenhar(com(BASE.rendimentos)); const bg = StyleSheet.flatten(screen.getByTestId("tile-renda_fixa").props.style).backgroundColor;
    expect(bg).toBe(tingir("#0E7490", 0.12)); expect(screen.queryByTestId("tile-renda_fixa")!.children.length).toBe(1);
  });
  it("pílula do %: fundo verde claro no ganho, neutro na perda", () => {
    desenhar(com(BASE.rendimentos)); expect(StyleSheet.flatten(screen.getByTestId("chip-pct").props.style).backgroundColor).toBe(tingir(claro.positive, ALFA_FUNDO_TOM.light));
    screen.unmount(); desenhar(com(rend({ mes: { valor: "-5.00", pct: "-0.50", sem_dado: 0 } })));
    expect(StyleSheet.flatten(screen.getByTestId("chip-pct").props.style).backgroundColor).toBe(claro.surfaceControl);
  });
  it("oculto: saldo e % da categoria e do ativo mascarados, sem número", () => {
    desenhar(com(BASE.rendimentos), true); abrir("Renda fixa");
    expect(screen.queryByText(/\d,\d\d/)).toBeNull(); expect(screen.getAllByText(/ativos? · R\$ ••••/).length).toBeGreaterThan(0); expect(screen.getAllByText(/ · R\$ ••••/).length).toBeGreaterThan(1);
  });
});
