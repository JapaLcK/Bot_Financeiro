import { ErroDeApi, RenovacaoIndisponivel, RequisicaoSuperada } from "@/api/client";
import { SEM_SENHA } from "@/features/openFinance/volta";
import { CONTA_A_ESCOLHER, INVESTIDO, alvoTexto, corpoCriar, corpoEditar, dataDoSeletor, falhaDaEscrita, inicialDe, isoDoSeletor, validar, type Campos } from "@/features/painel/metasEscrita";
import type { MetaV2 } from "@/features/painel/metasTextos";
// Funções puras da folha de meta. O fluxo da tela (servidor falso, trava, Alert) está em `painel_metas_escrita.test.tsx`.
const meta = (extra: Partial<MetaV2> = {}): MetaV2 => ({ id: 3, nome: "Notebook", tipo: "meta", meses: null, data_alvo: null, atual: 100, alvo: 800000, falta: 700000, percentual: 12, por_mes: null, prazo_passou: false, ritmo: { valor: null, dias_de_historico: null, parcial: false, motivos: [] }, chega_em: null, motivos: [], fonte: { tipo: "investido", rotulo: "Tudo", conta_id: null, banco: null }, ...extra });
const campos = (extra: Partial<Campos> = {}): Campos => ({ nome: "Viagem", tipo: "meta", centavos: 123450, meses: 6, data: null, fonte: INVESTIDO, ...extra });
const erro = (status: number, corpo?: unknown) => new ErroDeApi(status, "x", corpo);
const envelope = (code: string, details?: unknown) => ({ error: { code, message: "m", details } });

describe("corpo de criar", () => {
  it("centavos viram texto exato, sem float", () => {
    expect([0, 5, 99, 100, 123450, 9_999_999_999].map(alvoTexto)).toEqual(["0.00", "0.05", "0.99", "1.00", "1234.50", "99999999.99"]);
  });
  it("meta manda `alvo`, reserva manda `meses` (nunca os dois); `data_alvo` só se houver; o nome sai aparado", () => {
    expect(corpoCriar(campos({ nome: "  Viagem " }))).toEqual({ nome: "Viagem", tipo: "meta", fonte: { tipo: "investido" }, alvo: "1234.50" });
    expect(corpoCriar(campos({ tipo: "reserva", meses: 9, data: "2030-01-05" }))).toEqual({ nome: "Viagem", tipo: "reserva", fonte: { tipo: "investido" }, meses: 9, data_alvo: "2030-01-05" });
  });
  it("a fonte vai com o campo certo de cada tipo", () => {
    expect(corpoCriar(campos({ fonte: { tipo: "banco", banco: "XP", conta: null } })).fonte).toEqual({ tipo: "banco", banco: "XP" });
    expect(corpoCriar(campos({ fonte: { tipo: "conta", banco: null, conta: 7 } })).fonte).toEqual({ tipo: "conta", conta: 7 });
  });
});

describe("diff da edição", () => {
  const i = inicialDe(meta({ alvo: 800000 }));
  it("nada mudou (ou mudou e voltou): null — o 422 'Nada para editar' nunca sai do app", () => {
    expect(corpoEditar(3, i, i)).toBeNull();
    expect(corpoEditar(3, { ...i, nome: "Outro" }, i)).not.toBeNull(); expect(corpoEditar(3, { ...i, nome: "Notebook" }, i)).toBeNull();
  });
  it("manda SÓ o campo que mudou; meta nunca leva `meses`; reserva nunca leva `alvo`", () => {
    expect(corpoEditar(3, { ...i, centavos: 900050 }, i)).toEqual({ id: 3, alvo: "9000.50" });
    expect(corpoEditar(3, { ...i, nome: " N2 " }, i)).toEqual({ id: 3, nome: "N2" });
    const r = inicialDe(meta({ tipo: "reserva", meses: 6, alvo: 1800000 }));
    expect(corpoEditar(1, { ...r, meses: 7 }, r)).toEqual({ id: 1, meses: 7 });
    expect(corpoEditar(1, { ...r, meses: 7, nome: "R" }, r)).not.toHaveProperty("alvo");
  });
  it("meta sem `meses` (null) abre com o padrão e isso não vira diff", () => { expect(inicialDe(meta()).meses).toBe(6); expect(corpoEditar(3, inicialDe(meta()), inicialDe(meta()))).toBeNull(); });
  it("prazo: novo vai em `data_alvo`; tirar manda null; igual não manda", () => {
    expect(corpoEditar(3, { ...i, data: "2030-02-01" }, i)).toEqual({ id: 3, data_alvo: "2030-02-01" });
    const com = inicialDe(meta({ data_alvo: "2027-06-30" }));
    expect(corpoEditar(3, { ...com, data: null }, com)).toEqual({ id: 3, data_alvo: null }); expect(corpoEditar(3, com, com)).toBeNull();
  });
  it("fonte: só vai se mudou", () => {
    expect(corpoEditar(3, { ...i, fonte: { tipo: "banco", banco: "XP", conta: null } }, i)).toEqual({ id: 3, fonte: { tipo: "banco", banco: "XP" } });
    const sumido = inicialDe(meta({ fonte: { tipo: "banco", rotulo: "x", conta_id: null, banco: "Banco Sumiu" } }));
    expect(corpoEditar(3, { ...sumido, nome: "N" }, sumido)).toEqual({ id: 3, nome: "N" });
  });
  it("reserva → meta: `tipo: meta` + `alvo`, sem `meses`", () => {
    const r = inicialDe(meta({ tipo: "reserva", meses: 6, alvo: null }));
    expect(corpoEditar(1, { ...r, tipo: "meta", centavos: 1000000 }, r)).toEqual({ id: 1, tipo: "meta", alvo: "10000.00" });
  });
});

describe("validar", () => {
  it("nome vazio/longo, valor 0 e fonte por escolher barram a criação; o preenchido passa", () => {
    expect(Object.keys(validar(campos({ nome: "  ", centavos: 0, fonte: CONTA_A_ESCOLHER }), null)).sort()).toEqual(["alvo", "fonte", "nome"]);
    expect(validar(campos({ nome: "x".repeat(61) }), null)).toHaveProperty("nome"); expect(validar(campos({ nome: "x".repeat(60) }), null)).toEqual({});
    expect(validar(campos(), null)).toEqual({});
  });
  it("prazo no passado barra só se for NOVO; o já gravado (vencido) passa", () => {
    expect(validar(campos({ data: "2020-01-01" }), null)).toHaveProperty("data_alvo");
    const i = campos({ data: "2020-01-01" }); expect(validar({ ...i, nome: "Outro" }, i)).toEqual({});
  });
  it("na edição não revalida o que não mudou (banco sumido, alvo antigo)", () => {
    const i = campos({ fonte: { tipo: "banco", banco: null, conta: null } as never }); expect(validar({ ...i, nome: "Z" }, i)).toEqual({});
  });
});

describe("data do seletor", () => {
  it("ida e volta sem fuso: o dia civil não anda (meio-dia local)", () => {
    for (const d of ["2030-01-05", "2027-12-31", "2028-02-29", "2026-10-25"]) expect(isoDoSeletor(dataDoSeletor(d))).toBe(d);
    expect(isoDoSeletor(new Date(2030, 0, 5, 23, 59))).toBe("2030-01-05"); expect(isoDoSeletor(new Date(2030, 0, 5, 0, 1))).toBe("2030-01-05");
  });
});

describe("falhaDaEscrita", () => {
  it("rede, tempo esgotado, 5xx, resposta ilegível e conta trocada no ar: 'pode ter salvo' (apagar: 'apagado')", () => {
    for (const e of [new TypeError("Network request failed"), new DOMException("x", "AbortError"), erro(500), erro(502), erro(0), erro(200), new RequisicaoSuperada()]) expect(falhaDaEscrita(e)).toMatchObject({ geral: "Pode ter salvo. Confira a lista.", motivo: "perdida" });
    expect(falhaDaEscrita(erro(503), true).geral).toBe("Pode ter apagado. Confira a lista.");
  });
  it("renovação de sessão que falhou NÃO é 'pode ter salvo' (o pedido nem saiu)", () => { expect(falhaDaEscrita(new RenovacaoIndisponivel(503))).toMatchObject({ motivo: "outra" }); });
  it("409 reserva_ja_existe", () => expect(falhaDaEscrita(erro(409, envelope("reserva_ja_existe")))).toMatchObject({ motivo: "reserva_ja_existe", geral: "Você já tem uma reserva." }));
  it("o 409 da conta trocada (`RequisicaoSuperada`, status 409) não vira 'reserva já existe'", () => expect(falhaDaEscrita(new RequisicaoSuperada()).motivo).toBe("perdida"));
  it("404 meta_nao_encontrada", () => expect(falhaDaEscrita(erro(404, envelope("meta_nao_encontrada")))).toMatchObject({ motivo: "sumiu" }));
  it("403: pro_required, password_required (também no formato `detail`) e o 403 sem código", () => {
    expect(falhaDaEscrita(erro(403, envelope("pro_required"))).motivo).toBe("plano");
    expect(falhaDaEscrita(erro(403, envelope("password_required")))).toMatchObject({ motivo: "sem_senha", geral: SEM_SENHA });
    expect(falhaDaEscrita(erro(403, { detail: { error: "password_required" } })).motivo).toBe("sem_senha");
    expect(falhaDaEscrita(erro(403, { detail: "CSRF" })).motivo).toBe("outra");
  });
  it("422: o erro cai nos campos de `loc`, com texto do app (nunca o do servidor); sem `loc` conhecido, só o aviso geral", () => {
    const f = falhaDaEscrita(erro(422, envelope("validation_error", [{ loc: ["body", "nome"], msg: "in english" }, { loc: ["body", "fonte", "banco"], msg: "x" }, { loc: ["body", "body"], msg: "Nada para editar." }])));
    expect(f).toEqual({ geral: "Confira os campos.", campos: { nome: "1 a 60 letras", fonte: "Escolha a fonte" }, motivo: "campos" });
    expect(falhaDaEscrita(erro(422, envelope("validation_error", [{ loc: ["body", "body"] }]))).campos).toEqual({});
  });
  it("positivo: um 400 comum não é 'pode ter salvo'", () => expect(falhaDaEscrita(erro(400, envelope("valor_invalido")))).toMatchObject({ motivo: "outra" }));
});
