import { z } from "zod";
import { ContratoInvalido, ErroDeApi, RenovacaoIndisponivel, RequisicaoSuperada } from "@/api/client";
import { SEM_SENHA } from "@/features/openFinance/volta";
import { ERRO_CAMPO, TEXTO_CODIGO, VAZIO, corpoDe, falhaDe, type Cen } from "@/features/painel/simuladorCompra";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// `corpoDe` e `falhaDe` em tabela (teste unitário). Só o grupo das parcelas tem controle negativo (regex): é o conserto que impede "à vista em silêncio".
const COMPLETO: Cen = { nome: " Celular ", preco: 123450, entrada: 20000, custos: 500, despesa: 1999, parcelas: "12", juros: "1,99", data: "2026-11-05" };
const um = (c: Partial<Cen>) => corpoDe(0, [{ ...VAZIO, nome: "A", preco: 100, ...c }]).cenarios[0]!;
const erro = (status: number, corpo?: unknown) => new ErroDeApi(status, "x", corpo);
const det = (type: string, loc: (string | number)[]) => erro(422, { error: { code: "validation_error", details: [{ type, loc, msg: "Value error, entrada_maior_que_preco should match" }] } });
const comCodigo = (status: number, code: string) => erro(status, { error: { code } });

describe("corpoDe", () => {
  it("cenário completo: dinheiro em texto exato, parcelas número, juros com ponto, nome sem espaço, data", () => {
    expect(corpoDe(5000, [COMPLETO])).toEqual({ reserva_minima: "50.00", cenarios: [{ nome: "Celular", preco: "1234.50", entrada: "200.00", custos_unicos: "5.00", despesa_mensal_nova: "19.99", juros_mensal_pct: "1.99", data_compra: "2026-11-05", parcelas: 12 }] });
  });
  it("opcionais zerados ou vazios saem das chaves; nenhum null no JSON; só nome e preço vão sempre", () => {
    const corpo = corpoDe(0, [{ ...VAZIO, nome: "A", preco: 100, juros: "  ", parcelas: " " }]);
    expect(Object.keys(corpo)).toEqual(["cenarios"]); expect(Object.keys(corpo.cenarios[0]!)).toEqual(["nome", "preco"]);
    expect(JSON.stringify(corpo)).not.toContain("null"); expect(corpo.cenarios[0]!.preco).toBe("1.00");
  });
  it("parcelas '012' vira 12; '0' vira 0 (o servidor recusa); centavos nunca viram float", () => {
    expect(um({ parcelas: "012" }).parcelas).toBe(12); expect(um({ parcelas: "0" }).parcelas).toBe(0); expect(um({ preco: 1 }).preco).toBe("0.01");
  });
  describe("guarda das parcelas: o que não é 1 a 3 dígitos vai como TEXTO (o servidor recusa), nunca como null", () => {
    it.each([["abc", "abc"], ["4 dígitos", "4200"], ["400 dígitos", "9".repeat(400)], ["1e3", "1e3"], ["-3", "-3"]])("%s", (_, p) => {
      const v = um({ parcelas: p }).parcelas; expect(v).toBe(p); expect(JSON.parse(JSON.stringify(um({ parcelas: p }))).parcelas).toBe(p);
    });
    it("positivo: 1 a 3 dígitos seguem como número", () => { expect(um({ parcelas: "420" }).parcelas).toBe(420); expect(um({ parcelas: "3" }).parcelas).toBe(3); });
  });
});

describe("falhaDe", () => {
  const n = 2, campo = (f: ReturnType<typeof falhaDe>, i: number) => f.cenarios[i]!;
  it.each([
    ["entrada_maior_que_preco", "entrada"], ["entrada_parcial_sem_parcelas", "parcelas"], ["entrada_igual_ao_preco", "entrada"], ["juros_sem_parcelas", "parcelas"], ["data_no_passado", "data_compra"],
    ["data_fora_do_calendario", "data_compra"], ["financiado_pequeno_demais", "parcelas"], ["dinheiro_casas", "custos_unicos"],
  ] as const)("%s no campo %s do cenário 2: frase do app nesse campo, cenário 1 limpo", (codigo, k) => {
    const f = falhaDe(det(codigo, ["body", "cenarios", 1, k]), n);
    expect(campo(f, 1)[k]).toBe(TEXTO_CODIGO[codigo]); expect(campo(f, 0)).toEqual({}); expect(f.reserva).toBeUndefined(); expect(f.geral).toBe("Confira os campos.");
  });
  it("campo sem código conhecido cai na frase do campo; `valor_invalido` fora do campo cai no genérico do cenário", () => {
    expect(campo(falhaDe(det("valor_invalido", ["body", "cenarios", 0, "preco"]), n), 0).preco).toBe(ERRO_CAMPO.preco);
    expect(campo(falhaDe(det("valor_invalido", ["body", "cenarios", 0]), n), 0).cenario).toBe("Confira este cenário.");
    expect(campo(falhaDe(det("nomes_repetidos", ["body", "cenarios", 1]), n), 1).cenario).toBe(TEXTO_CODIGO.nomes_repetidos);
  });
  it("o `loc` da reserva vai para a reserva; sem índice, o código vai para o geral; índice fora da lista não quebra", () => {
    expect(falhaDe(det("dinheiro_casas", ["body", "reserva_minima"]), n).reserva).toBe(ERRO_CAMPO.reserva_minima);
    expect(falhaDe(det("nomes_repetidos", ["body", "cenarios"]), n).geral).toBe(TEXTO_CODIGO.nomes_repetidos);
    expect(falhaDe(det("dinheiro_casas", ["body", "cenarios", 7, "entrada"]), n).geral).toBe(TEXTO_CODIGO.dinheiro_casas);
    expect(falhaDe(det("x", ["body", "cenarios", 0, "constructor"]), n).cenarios[0]).toEqual({ cenario: "Confira este cenário." }); // chave herdada não é campo
  });
  it("422 sem `details` (ou de forma estranha) vai para o geral", () => {
    for (const corpo of [undefined, null, { error: {} }, { error: { details: "x" } }, { error: { details: [null, 3] } }]) { const f = falhaDe(erro(422, corpo), n); expect(f.geral).toBe("Confira os campos."); expect(f.cenarios).toEqual([{}, {}]); }
  });
  it("códigos de plano, senha, portão e onboarding", () => {
    expect(falhaDe(comCodigo(403, "pro_required"), n)).toMatchObject({ motivo: "plano", geral: "Simulador indisponível no seu plano." });
    expect(falhaDe(comCodigo(403, "password_required"), n)).toMatchObject({ geral: SEM_SENHA }); expect(falhaDe(erro(403, { detail: { error: "password_required" } }), n).geral).toBe(SEM_SENHA);
    expect(falhaDe(erro(402), n).motivo).toBe("portao");
    expect(falhaDe(comCodigo(403, "open_finance_onboarding_required"), n).geral).toBe("Conecte seu banco para simular.");
  });
  it("RequisicaoSuperada é 'superada' (e não o genérico do 409); 429 tem a sua frase", () => {
    expect(falhaDe(new RequisicaoSuperada(), n).motivo).toBe("superada"); expect(falhaDe(erro(409), n).motivo).toBeUndefined();
    expect(falhaDe(erro(429), n).geral).toBe("Muitas simulações. Espere um pouco.");
  });
  it("500, rede, ContratoInvalido, renovação e status estranho: o genérico, sem campo e sem motivo", () => {
    const GEN = "Não deu para calcular. Tente de novo.";
    for (const e of [erro(500), erro(503), new TypeError("Network request failed"), new ContratoInvalido("/api/app/simulador", new z.ZodError([])), new RenovacaoIndisponivel(0), erro(0), erro(418), erro(404), "texto", null]) {
      const f = falhaDe(e, n); expect(f.geral).toBe(GEN); expect(f.motivo).toBeUndefined(); expect(f.cenarios).toEqual([{}, {}]);
    }
  });
  it("nenhum texto devolvido (todos os códigos da fixture, em todos os lugares) deixa vazar código ou `msg` do servidor", () => {
    const codigos = (v2 as unknown as { simulador: { codigos_422: string[] } }).simulador.codigos_422, RUIM = /[a-z]+_[a-z_]+|Value error|should match/;
    const textos: string[] = [];
    for (const c of codigos) for (const loc of [["body", "cenarios", 0, "entrada"], ["body", "cenarios", 0], ["body", "reserva_minima"], ["body"]]) {
      const f = falhaDe(det(c, loc), 1); textos.push(f.geral, ...(f.reserva ? [f.reserva] : []), ...Object.values(f.cenarios[0]!));
    }
    for (const e of [erro(500), erro(429), erro(402), comCodigo(403, "pro_required"), comCodigo(403, "open_finance_onboarding_required")]) textos.push(falhaDe(e, 1).geral);
    expect(textos.length).toBeGreaterThan(30); for (const t of textos) expect(t).not.toMatch(RUIM); expect(Object.values(ERRO_CAMPO).concat(Object.values(TEXTO_CODIGO)).filter((t) => RUIM.test(t))).toEqual([]);
  });
});
