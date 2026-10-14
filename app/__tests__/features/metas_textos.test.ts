import { avisos, fraseChegada, fraseCusto, fraseRitmo, type MetaV2 } from "@/features/painel/metasTextos";
// As frases do card `metas` do chat, em tabela: a prioridade dos motivos, o fecho do ritmo e o que nunca some.
const ritmo = (motivos: string[] = [], extra: Partial<MetaV2["ritmo"]> = {}): MetaV2["ritmo"] => ({ valor: null, dias_de_historico: null, parcial: false, motivos, ...extra });
const meta = (extra: Partial<MetaV2> = {}): MetaV2 => ({ id: 1, nome: "M", tipo: "meta", meses: null, data_alvo: null, atual: 100, alvo: 1000, falta: 900, percentual: 10, por_mes: null, prazo_passou: false, ritmo: ritmo(), chega_em: null, motivos: [], fonte: { tipo: "investido", rotulo: null }, ...extra });
const custo = (valor: number | null, motivos: string[] = []) => ({ valor, recorrencias: 0, motivos });

it.each([
  [{ chega_em: "2027-06" as string | null, data_alvo: "2027-06-30" }, "Nesse ritmo, chega em junho de 2027, dentro do prazo"],
  [{ chega_em: "2027-07", data_alvo: "2027-06-30" }, "Nesse ritmo, chega em julho de 2027, depois do prazo"],
  [{ chega_em: "2027-07", data_alvo: null }, "Nesse ritmo, chega em julho de 2027"],
  [{ ritmo: ritmo([], { valor: -25000 }) }, "Nesse ritmo, a meta não anda."],
  [{ ritmo: ritmo([], { valor: 0 }) }, "Nesse ritmo, a meta não anda."],
  [{ ritmo: ritmo([], { valor: 0 }), falta: 0 }, null],
  [{ ritmo: ritmo([], { valor: 0 }), falta: null }, null],
  [{ ritmo: ritmo([], { valor: 100 }) }, null],
  [{ ritmo: ritmo(["historico_curto"]) }, null],
])("fraseChegada %#", (extra, esperado) => expect(fraseChegada(meta(extra))).toBe(esperado));

it.each([
  [ritmo(["historico_curto"], { dias_de_historico: 4 }), "Ainda estou juntando histórico (4 de 7 dias). Volto a calcular seu ritmo a partir de 7 dias."],
  [ritmo(["historico_curto"]), "Ainda estou juntando histórico dos seus bancos. Em alguns dias eu calculo seu ritmo."],
  [ritmo(["banco_desatualizado", "conexao_pausada"]), "A conexão com o banco está pausada. Reconecte pra eu voltar a ler."], // a ordem do mapa manda, não a do servidor
  [ritmo(["codigo_novo"]), "Há informações a conferir nesta meta."], // fora do mapa: nunca cru, nunca some
  [ritmo([]), "Ainda não consigo medir seu ritmo."],
])("fraseRitmo %#", (r, esperado) => expect(fraseRitmo(r)).toBe(esperado));

it.each([
  [custo(3000), null],
  [custo(3000, ["recorrencias_banco_nao_lidas"]), "Não consegui ler as cobranças do banco agora. O valor pode estar velho."],
  [custo(null, ["plano_sem_custo_mensal"]), "Seu plano não calcula as contas fixas. O valor da reserva precisa ser definido no painel."],
  [custo(null, ["codigo_novo"]), "Há informações a conferir nesta meta."],
  [custo(null), "Não consegui calcular suas contas fixas."],
  [custo(0), null], // R$ 0,00 é valor: não é "sem número"
])("fraseCusto %#", (c, esperado) => expect(fraseCusto(c)).toBe(esperado));

it("avisos: um por motivo, sem repetir a frase do ritmo que o card já mostra; do alvo não são aviso; código novo vira a genérica uma vez", () => {
  expect(avisos(meta({ atual: null, motivos: ["fonte_indisponivel"], ritmo: ritmo(["fonte_indisponivel"]) }))).toEqual([]); // é a frase do lugar do número
  expect(avisos(meta({ motivos: ["banco_desatualizado"], ritmo: ritmo([], { valor: 100 }) }))).toEqual(["Seu banco não atualiza há um tempo, então o número pode estar velho."]);
  expect(avisos(meta({ motivos: ["plano_sem_custo_mensal", "custo_desconhecido"] }))).toEqual([]);
  expect(avisos(meta({ motivos: ["a", "b"], ritmo: ritmo([], { valor: 1 }) }))).toEqual(["Há informações a conferir nesta meta."]);
  expect(avisos(meta({ motivos: ["moeda_presumida", "outra_moeda"], ritmo: ritmo([], { valor: 1 }) }))).toEqual(["Parte do dinheiro está em outra moeda e ficou de fora.", "Considerei em reais uma posição sem moeda informada."]); // ordem do mapa
});
