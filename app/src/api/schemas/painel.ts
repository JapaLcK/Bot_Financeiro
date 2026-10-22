import { z } from "zod";
import { TITULOS } from "@/features/painel/catalogo";
import { decimalParaCentavos, numeroLegadoParaCentavos } from "@/features/painel/decimal";
const dinheiro = z.string().transform((v, ctx) => { try { return decimalParaCentavos(v); } catch { ctx.addIssue({ code: "custom", message: "Dinheiro inválido" }); return z.NEVER; } });
const legado = z.number().finite().transform((v, ctx) => { try { return numeroLegadoParaCentavos(v); } catch { ctx.addIssue({ code: "custom", message: "Dinheiro inválido" }); return z.NEVER; } });
const motivos = z.array(z.string());
const texto = z.string().nullable();
export const perfilPainelSchema = z.object({ perfil: z.enum(["padrao", "economizar", "investir", "controlar", "dividas", "autonomo"]).nullable() });
export const categoriasSchema = z.object({ categorias: z.array(z.object({ chave: z.string(), nome: z.string(), icone: z.string().catch("tag") })) }); // `icone`: o card `comparacao` desenha o ícone de categorias que podem não ter gasto no mês (o `/gastos` só devolve as com gasto)
export const planoSchema = z.object({ plan_tier: z.enum(["free", "essencial", "plus", "pro"]), agents_ui_enabled: z.boolean().optional() }); // `agents_ui_enabled`: o freio dos agentes; ausente (servidor antigo) = ligado
export const contasSchema = z.object({ total: dinheiro, motivos, fora_do_total: z.number().int(), carteira: z.object({ saldo: dinheiro, motivos }), contas: z.array(z.object({ id: z.number().int(), instituicao: texto, nome: texto, saldo: dinheiro.nullable(), moeda: z.string(), no_total: z.boolean(), conexao: z.string(), sincronizado_em: texto, motivos })) });
/**
 * `GET /api/app/bancos` (`api/v2/bancos.py`): o que cada banco vale, calculado no servidor (o app NÃO soma dinheiro). `null` ≠ zero. Tolerante POR ITEM: um banco que não passa
 * (p.ex. dinheiro em notação científica, "1E-7", que `decimalParaCentavos` recusa) sai da lista e a tela mostra "—" para ele; o topo que não passa vira `null`.
 */
const bancoSchema = z.object({ conexao_id: z.number().int(), saldo_conta: dinheiro.nullable(), investido: dinheiro.nullable(), total: dinheiro.nullable(), contas: z.number().int().nonnegative(), cartoes: z.number().int().nonnegative(), investimentos: z.number().int().nonnegative().nullable(), motivos });
export const bancosSchema = z.object({
  total: dinheiro.nullable().catch(null), motivos: motivos.catch([]),
  bancos: z.array(z.unknown()).transform((itens) => itens.flatMap((i) => { const b = bancoSchema.safeParse(i); return b.success ? [b.data] : []; })),
});
export type BancoLido = z.output<typeof bancoSchema>;
export const resumoSchema = z.object({ mes: z.string(), ate: z.string(), entrou: dinheiro, saiu: dinheiro, anterior: z.object({ mes: z.string(), entrou: dinheiro, saiu: dinheiro }).nullable(), motivos });
export const lancamentoSchema = z.object({ id: z.string(), data: z.string(), hora: texto, tipo: z.enum(["entrada", "saida"]), interno: z.boolean(), valor: dinheiro, moeda: z.string(), descricao: texto, mensagem: texto, categoria: texto, origem: z.enum(["carteira", "banco", "cartao", "registro_antigo"]), fundido: z.boolean(), instituicao: texto, conta_id: z.number().int().nullable(), cartao_id: z.number().int().nullable(), parcela: z.object({ n: z.number().int(), total: z.number().int() }).nullable(), fatura: texto, pode: z.array(z.string()), motivos });
export const lancamentosSchema = z.object({ mes: z.string(), itens: z.array(lancamentoSchema), proximo: texto, motivos });
const dia = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const partes = z.object({ carteira: dinheiro, bancos: dinheiro, investimentos_banco: dinheiro, caixinhas: dinheiro, investimentos_manuais: dinheiro });
export const patrimonioSchema = z.object({ total: dinheiro, partes, motivos, historico: z.array(z.object({ dia: z.string(), total: dinheiro, partes, motivos })), historico_desde: texto, trechos: z.array(z.object({ desde: dia, ate: dia })).catch([]), variacao: z.object({ valor: dinheiro, desde: dia, ate: dia }).nullable().catch(null) }); // `trechos`/`variacao` (PR A do patrimônio): servidor antigo ou campo fora do contrato = `[]`/`null`, nunca derruba o recurso
/**
 * `rendimentos` (acrescentado em `GET /api/app/rendimento`): o quanto os investimentos renderam, ESTIMADO pelo saldo; `null` ≠ zero. Ausente (servidor antigo) ou fora do contrato = `null`
 * (a tela diz que não deu para ler; `itens` e `motivos` seguem). `pct` é texto só para exibir. Tolerante POR ATIVO e POR CATEGORIA, como o `bancosSchema`: o item que não passa sai da lista.
 */
const periodo = z.object({ valor: dinheiro.nullable(), pct: texto, sem_dado: z.number().int() });
const periodoAtivo = z.object({ valor: dinheiro.nullable(), pct: texto, motivo: texto });
const ativoRendimento = z.object({ nome: texto, instituicao: texto, logo_proprio: texto, saldo: dinheiro.nullable(), mes: periodoAtivo, total: periodoAtivo });
const categoriaRendimento = z.object({
  tipo: z.string(), rotulo: z.string(), saldo: dinheiro.nullable(), quantidade: z.number().int(), mes: periodo, total: periodo,
  ativos: z.array(z.unknown()).transform((itens) => itens.flatMap((i) => { const a = ativoRendimento.safeParse(i); return a.success ? [a.data] : []; })),
});
const rendimentos = z.object({ saldo: dinheiro.nullable(), quantidade: z.number().int(), mes: periodo, total: periodo, mes_desde: z.string(), sincronizado_em: texto, categorias: z.array(z.unknown()).transform((itens) => itens.flatMap((i) => { const c = categoriaRendimento.safeParse(i); return c.success ? [c.data] : []; })), motivos }).nullable().catch(null);
export const rendimentoSchema = z.object({ tipo: z.literal("contratado"), itens: z.array(z.object({ nome: texto, instituicao: texto, taxa: texto, tipo_taxa: texto, observado_em: texto, motivos })), motivos, rendimentos });
export const detalhesMesSchema = z.object({ mes: z.string(), ate: z.string(), entrou: dinheiro, saiu: dinheiro, categorias: z.array(z.object({ categoria: texto, valor: dinheiro, quantidade: z.number().int() })), origens: z.object({ cartao: dinheiro, banco: dinheiro, carteira: dinheiro, registro_antigo: dinheiro }).optional(), dias: z.array(z.object({ dia: z.string(), entrou: dinheiro, saiu: dinheiro })), guardado: z.object({ aportes: dinheiro, saques: dinheiro, liquido: dinheiro, cobertura: z.literal("movimentos_registrados") }), motivos, fora_do_total: z.number().int(), criterio_dias: z.literal("data_do_lancamento_ou_compra_na_fatura_do_mes") });
const motivoPrevisao = z.object({ codigo: z.string(), direcao_do_erro: z.enum(["so_melhora", "so_piora", "ambos"]) });
const ocorrencia = z.object({ chave: z.string(), ciclo: z.string(), data: texto, fonte: z.string(), tipo: z.string(), nome: z.string(), valor: dinheiro.nullable(), direcao: z.enum(["entrada", "saida"]), qualidade_valor: z.string(), qualidade_data: z.string(), realizacao: z.string(), incluida_no_calculo: z.boolean(), motivos: z.array(motivoPrevisao) });
/** Tolerância por item (`safeParse` no `transform`): item de forma nova sai, os válidos ficam. Campo ausente ou que não seja array dá `[]` (`.catch`, não `.default`). Nada disso derruba a Previsão nem o card do chat. */
const receitaDoBanco = z.object({ chave: z.string(), nome: z.string(), valor: dinheiro, dia: z.number().int(), status: z.enum(["pendente", "confirmada", "negada"]) });
/** `estimativa_variavel` e `pior_dia.despesa_variavel` (PR do gasto do dia a dia): só o que a tela lê. `.catch(null)`: ausente (servidor antigo) ou malformado não derruba a previsão nem o card do chat. */
const estimativaVariavel = z.object({ despesa_mensal: dinheiro, categorias: z.array(z.object({ chave: z.string(), nome: z.string(), despesa_mensal: dinheiro })) });
export const previsaoSchema = z.object({ hoje: z.string(), calculado_em: z.string(), valido_ate: texto, dias: z.number().int(), dias_permitidos: z.array(z.number().int()), capacidades: z.array(z.enum(["marcos", "trajetoria", "pior_dia", "compromissos"])), base: z.object({ saldo: dinheiro.nullable(), origem: z.string(), contas_bancarias: z.number().int(), bancos_excluidos: z.boolean(), motivos }), estado: z.enum(["condicional", "a_conferir", "indisponivel"]), motivos: z.array(motivoPrevisao), premissas: z.array(z.string()), cobertura: z.object({ fontes_incompletas: motivos, janela_conferencia_inicio: texto, inclui_estimativa_variavel: z.boolean() }), cabe_nas_premissas: z.boolean(), estimativa_variavel: estimativaVariavel.nullable().catch(null), marcos: z.array(z.object({ dias: z.number().int(), data: z.string(), saldo: dinheiro.nullable() })), periodo: z.object({ inicio: z.string(), fim: z.string() }).nullable(), ancora: z.object({ data: z.string(), saldo: dinheiro.nullable() }).nullable(), trajetoria: z.array(z.object({ data: z.string(), saldo: dinheiro.nullable(), compromissos: z.array(ocorrencia) })).nullable(), pior_dia: z.object({ data: z.string(), saldo: dinheiro, desde: texto, causas: z.array(ocorrencia), despesa_variavel: dinheiro.nullable().catch(null) }).nullable(), compromissos: z.array(z.object({ chave: z.string(), fonte: z.string(), nome: z.string(), primeira_data: texto, ultima_data: texto, ocorrencias: z.array(ocorrencia) })).nullable(), receitas_do_banco: z.array(z.unknown()).catch([]).transform((itens) => itens.flatMap((i) => { const r = receitaDoBanco.safeParse(i); return r.success ? [r.data] : []; })) });
/** `POST /api/app/simulador` (`api/v2/simulador.py`): só o que a tela lê (o zod descarta o resto). Dinheiro vira centavos aqui; `null` ≠ zero. */
const pontoSim = z.object({ data: z.string(), saldo: dinheiro.nullable() });
const resumoSim = { saldo_final: dinheiro.nullable(), pior_dia: z.object({ data: z.string(), saldo: dinheiro }).nullable(), dias_abaixo_da_reserva: z.number().int(), primeiro_dia_abaixo: texto, trajetoria: z.array(pontoSim) };
export const simuladorSchema = z.object({ hoje: z.string(), dias: z.number().int(), estado: z.enum(["condicional", "a_conferir", "indisponivel"]), motivos: z.array(motivoPrevisao), premissas: z.array(z.string()), base: z.object({ bancos_excluidos: z.boolean() }), reserva_minima: dinheiro, atual: z.object(resumoSim),
  cenarios: z.array(z.object({ nome: z.string(), resumo: z.object({ ...resumoSim, delta_vs_atual: dinheiro.nullable(), compra_fora_da_janela: z.boolean() }), contrato: z.object({ a_vista: z.boolean(), parcelas: z.number().int(), parcela: dinheiro, total_pago: dinheiro, juros_totais: dinheiro, parcelas_fora_do_horizonte: z.object({ quantidade: z.number().int(), valor: dinheiro }) }) })) });
/** `POST /api/app/simulador/habitos` (corte de hábito): só o que a tela lê. A curva e a linha por categoria são enfeite: dinheiro inválido no total vira `ContratoInvalido`. */
export const simuladorHabitosSchema = z.object({ estado: z.enum(["condicional", "a_conferir", "indisponivel"]).catch("indisponivel"), economia_mensal: dinheiro, cortes: z.array(z.object({ chave: z.string(), nome: z.string(), percentual: z.number().int(), economia_mensal: dinheiro, valor_pedido: dinheiro.nullable().catch(null) })).catch([]), atual: z.object({ trajetoria: z.array(pontoSim).catch([]) }), com_corte: z.object({ trajetoria: z.array(pontoSim).catch([]) }) });
const assinatura = z.object({ chave: z.string(), nome: z.string(), categoria: texto, valor: dinheiro, valor_anterior: dinheiro.nullable(), reajuste_em: texto, dia: z.number().int(), proxima: z.string(), ultima: z.string(), desde: z.string(), meses: z.number().int(), meio: z.object({ tipo: z.enum(["cartao", "conta"]), nome: z.string(), final: texto }), status: z.enum(["ativa", "possivelmente_cancelada"]), marcada: z.boolean() });
export const assinaturasSchema = z.object({ servicos: z.array(assinatura), outras: z.array(assinatura), ignoradas: z.array(assinatura), total_mensal: dinheiro, total_anual: dinheiro });
export const metasSchema = z.object({ ok: z.literal(true), goals: z.array(z.object({ id: z.number().int(), name: z.string(), balance: legado, target_amount: legado.nullable(), target_date: texto, source: texto, of_plan_active: z.boolean(), is_goal: z.boolean(), pct_complete: z.number().finite().nullable(), monthly_pace_current: legado.nullable(), monthly_pace_needed: legado.nullable(), projected_months: z.number().finite().nullable(), indicator: z.string() })) });
export const cartoesSchema = z.object({ ok: z.literal(true), cards: z.array(z.object({ id: z.number().int(), name: z.string(), last4: texto, credit_limit: legado.nullable(), due_day: z.number().int(), closing_day: z.number().int(), credit_used: legado, open_bill: z.object({ total: legado, paid_amount: legado, due_amount: legado, period_end: texto }) })) });
export const parcelasSchema = z.object({ ok: z.literal(true), installments: z.array(z.object({ group_id: z.string(), name: z.string(), card_name: z.string(), installments_total: z.number().int(), parcelas: z.array(z.object({ tx_id: z.number().int(), installment_no: z.number().int(), valor: legado, is_paid: z.boolean(), is_next: z.boolean(), due_date: z.string(), period_end: z.string() })) })) });
export const rendaSchema = z.object({ ok: z.literal(true), evolution: z.array(z.object({ month: z.string(), income: legado, expense: legado, net: legado })), months: z.number().int() });
export const insightsSchema = z.object({ ok: z.literal(true), insights: z.array(z.object({ key: z.string(), title: z.string(), message: z.string(), severity: z.string(), action_label: texto, action_view: texto })) });
const usage = z.object({ used: z.number().int(), limit: z.number().int() });
export const conversaSchema = z.object({ messages: z.array(z.object({ role: z.enum(["user", "assistant"]), content: z.string(), created_at: texto })), usage });
const mesAAAAMM = z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/); // `tituloPeriodo` lança em "" (nomeMes): mês malformado invalida o bloco
/** Os cards que a PRÓPRIA tool do servidor emite (`cards.DA_TOOL` em `core/services/ai_chat/cards.py`, sem `show_card`): só do chat, fora do `TITULOS`. O schema valida o contrato do servidor (inclusive chaves opcionais que o card não desenha); fixtures: `tests/fixtures/ai_chat_bloco_{maiores_gastos,fim_do_mes,comparacao,ultimos_lancamentos,sem_categoria}.json`. Esta é a fonte da lista `cards` que o app manda no POST (`CARDS_DA_TOOL`): `tests/test_ai_chat_cards_ids_app.py` a compara com o Python. */
const maioresGastosSchema = z.object({
  start_date: z.string(), end_date: z.string(),
  expenses: z.array(z.object({ valor: legado, categoria: texto, descricao: texto, data: z.string(), instituicao: texto, parcelamento: texto })),
  motivos: motivos.optional(), historico_desde: texto.optional(), limitado_a_12_meses: z.boolean().optional(), pedido: z.object({ start_date: z.string(), end_date: z.string() }).optional(), aviso_limite: z.string().optional(), fim_ajustado: z.boolean().optional(), aviso_fim: z.string().optional(),
});
const fimDoMesSchema = z.object({ mes: mesAAAAMM, dias_corridos: z.number().int(), dias_no_mes: z.number().int(), receita: legado, despesa: legado, despesa_projetada: legado, saldo_projetado: legado, vai_fechar_negativo: z.boolean(), motivos });
/** `ultimos_lancamentos` e `sem_categoria`: a mesma linha do Extrato (`lancamentoSchema`), até 10 itens (o servidor corta; o app não recusa lista maior). */
const listaLancamentosSchema = z.object({ itens: z.array(lancamentoSchema), motivos });
/** `comparacao` (`compare_periods`, `core/services/comparacao_periodos.py`): dinheiro em float → centavos; `pct` null sem base. Fixture: `tests/fixtures/ai_chat_bloco_comparacao.json`. Tolerante onde o servidor varia: `estado` é texto livre, `serie` de forma inesperada vira `null` (o card sai sem gráfico) e o que o card não desenha (`base`, `receita`, `resultado`) não derruba o bloco. */
const linhaComparacao = z.object({ total_atual: legado, total_anterior: legado, total_delta: legado, pct: z.number().finite().nullable(), estado: z.string() });
const janelaComparacao = z.object({ inicio: z.string(), fim: z.string(), dias: z.number().int().positive() });
const comparacaoSchema = z.object({
  modo: z.string(), atual: janelaComparacao, anterior: janelaComparacao,
  mesmo_tamanho: z.boolean().optional(), anterior_encurtado: z.boolean().optional(), base: z.unknown().optional(),
  despesa: linhaComparacao.nullable(), receita: linhaComparacao.nullable().optional().catch(undefined), resultado: linhaComparacao.nullable().optional().catch(undefined),
  categorias: z.array(linhaComparacao.extend({ categoria: z.string() })), categorias_omitidas: z.number().int().optional(),
  motivo: texto.optional(), motivos, dias_corridos: z.number().int().optional(), dias_no_mes: z.number().int().optional(),
  serie: z.object({ atual: z.array(legado), anterior: z.array(legado) }).nullable().optional().catch(null),
});
const schemasDaTool = { maiores_gastos: maioresGastosSchema, fim_do_mes: fimDoMesSchema, comparacao: comparacaoSchema, ultimos_lancamentos: listaLancamentosSchema, sem_categoria: listaLancamentosSchema };
export const CARDS_DA_TOOL = Object.keys(schemasDaTool) as (keyof typeof schemasDaTool)[];
/** `periodo` e `caixinhas` existem só no chat (card sem widget): ficam FORA do `TITULOS` (ids e títulos de widget). O catálogo de "Adicionar blocos" e `lerLayout` é `BLOCOS_HOME` (`catalogo.ts`), que também deixa o `metas` de fora. */
export type CardChat = keyof typeof TITULOS | "periodo" | "caixinhas" | (typeof CARDS_DA_TOOL)[number];
const cardId = z.enum(["periodo", "caixinhas", ...Object.keys(TITULOS), ...CARDS_DA_TOOL] as [CardChat, ...CardChat[]]);
/** O recurso `periodo` do card (`api/nativo/mes_detalhes.py`), uma aba com `mes: null`. Fixture: `tests/fixtures/ai_chat_bloco_periodo.json`. */
export const periodoSchema = z.object({ inicio: mesAAAAMM, fim: mesAAAAMM, ate: z.string(), entrou: dinheiro, saiu: dinheiro, saldo: dinheiro, meses: z.array(z.object({ mes: z.string(), entrou: dinheiro, saiu: dinheiro })), categorias: z.array(z.object({ categoria: texto, valor: dinheiro, quantidade: z.number().int(), percentual: z.number().int(), maiores: z.array(lancamentoSchema) })), motivos, historico_desde: texto, fora_do_total: z.number().int() });
/** O recurso `metas`: a aba Metas (provider, `/api/app/metas`) e o card do chat (bloco): a forma de `GET /api/v2/metas` (`api/v2/metas.py`). Fixture: `tests/frontend/api_v2_respostas.json["metas"]`. O app só mostra: nada de `falta`/`percentual` derivado aqui. */
export const metasV2Schema = z.object({
  metas: z.array(z.object({ id: z.number().int(), nome: z.string(), tipo: z.enum(["meta", "reserva"]), meses: z.number().int().nullable(), data_alvo: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])-\d{2}$/).nullable(), atual: dinheiro.nullable(), alvo: dinheiro.nullable(), falta: dinheiro.nullable(), percentual: z.number().int().min(0).max(100).nullable(), por_mes: dinheiro.nullable(), prazo_passou: z.boolean(), ritmo: z.object({ valor: dinheiro.nullable(), dias_de_historico: z.number().int().nullable(), parcial: z.boolean(), motivos }), chega_em: mesAAAAMM.nullable(), motivos, fonte: z.object({ tipo: z.string(), rotulo: texto, conta_id: z.number().int().nullish(), banco: z.string().nullish() }) })),
  custo_mensal: z.object({ valor: dinheiro.nullable(), recorrencias: z.number().int(), motivos }),
});
export type MetasV2 = z.output<typeof metasV2Schema>;
/** `/api/app/investido`: só os bancos (a fonte `banco` de uma meta). O resto do recurso o app não lê. */
export const investidoBancosSchema = z.object({ por_banco: z.array(z.object({ banco: z.string() })) });
/**
 * Rotas que o card abaixo da resposta de um agente lê (`features/painel/cardAgente.tsx`; `/api/app/investido`, `contas-a-pagar`, `orcamentos-categoria`, `caixinhas`, mesma forma de `/api/v2`): SÓ o que o card desenha.
 * Corpo fora disto = erro de contrato (o card diz que não deu para carregar; nunca meio número). Fixtures: `tests/frontend/api_v2_respostas.json`.
 */
const pctTexto = z.string().regex(/^-?\d+(\.\d+)?$/);
export const investidoSchema = z.object({ total: dinheiro.nullable(), por_tipo: z.array(z.object({ tipo: z.string(), rotulo: z.string(), valor: dinheiro.nullable(), motivos })), motivos });
export const contasAPagarSchema = z.object({ hoje: z.string(), pendentes: z.number().int(), vencidas: z.number().int(), total_estimado: dinheiro, tem_variavel: z.boolean(), contas: z.array(z.object({ id: z.number().int(), nome: texto, dias: z.number().int().nullable(), valor: dinheiro.nullable(), variavel: z.boolean(), vencida: z.boolean() })) });
export const orcamentosCategoriaSchema = z.object({ mes: z.string(), total: z.object({ gasto: dinheiro, teto: dinheiro }), orcamentos: z.array(z.object({ categoria: z.string(), icone: z.string(), status: z.string(), gasto: dinheiro, teto: dinheiro, restante: dinheiro, usado_pct: pctTexto })) });
export const caixinhasV2Schema = z.object({ total: dinheiro, caixinhas: z.array(z.object({ id: z.number().int(), nome: z.string(), saldo: dinheiro, meta: dinheiro.nullable(), progresso_pct: pctTexto.nullable(), atingida: z.boolean().nullable() })) });
/** A resposta das três escritas de `/api/app/metas*`: o id. */
export const metaGravadaSchema = z.object({ id: z.number().int() });
/** `GET /api/app/progresso-milhao` (`api/v2/progresso_milhao.py`): só o patrimônio de hoje; a conta de juros é do app (`milhao.ts`). Fora de `schemasRecurso`: a tela lê sozinha. */
export const progressoMilhaoSchema = z.object({ patrimonio: dinheiro, motivos });
/** Fonte única dos recursos do painel: o provider lê cada rota com o schema daqui e o card do chat valida o recurso que o servidor embutiu com o mesmo. */
export const schemasRecurso = { contas: contasSchema, resumo: resumoSchema, detalhes: detalhesMesSchema, detalhesAnterior: detalhesMesSchema, patrimonio: patrimonioSchema, rendimento: rendimentoSchema, previsao: previsaoSchema, assinaturas: assinaturasSchema, metas: metasV2Schema, cartoes: cartoesSchema, parcelas: parcelasSchema, renda: rendaSchema, insights: insightsSchema, plano: planoSchema, categorias: categoriasSchema };
/** Dinheiro em que o app também DECIDE (média, % do dia, fatia da barra): centavos para exibir e o texto decimal bruto para decidir, como o web (`Number(raw)`); arredondar antes muda o resultado (`gastosDia.ts`). */
const dinheiroBruto = z.string().transform((v, ctx) => { try { return { centavos: decimalParaCentavos(v), bruto: v }; } catch { ctx.addIssue({ code: "custom", message: "Dinheiro inválido" }); return z.NEVER; } });
/** `GET /api/app/gastos?mes=` (`api/v2/gastos.py`), SÓ o que a folha do dia lê (campo a mais é ignorado; a cor da categoria vem de `CORES_CATEGORIA` por posição). Campos lidos versionados em `tests/fixtures/app_gastos_campos_lidos.json` e fixture em `tests/fixtures/app_gastos.json`, ambas conferidas contra o Pydantic por `tests/test_api_v2_gastos.py`. */
export const gastosSchema = z.object({
  hoje: z.string(), disponivel: z.boolean(), historico_desde: texto,
  dias: z.array(z.object({ dia: z.string(), total: dinheiroBruto, quantidade: z.number().int(), maiores: z.array(z.object({ descricao: z.string(), valor: dinheiro, categoria: z.string() })), categorias: z.array(z.object({ chave: z.string(), valor: dinheiroBruto })) })),
  categorias: z.array(z.object({ chave: z.string(), nome: z.string(), emoji: z.string(), icone: z.string().catch("tag") })),
  motivos,
});
export type Gastos = z.output<typeof gastosSchema>;
/** `simulador_habitos` (só do chat, quando a pessoa pediu um corte) é o corpo do POST do corte. `caixinhas` (só do chat) traz o formato legado `{ok, goals}`; o `metas` (aba e chat) é o v2 (`metasV2Schema`, desenhado por `MetasChat`). */
const schemasBloco = { ...schemasRecurso, periodo: periodoSchema, caixinhas: metasSchema, simulador_habitos: simuladorHabitosSchema, ...schemasDaTool };
export type RecursosBloco = { [K in keyof typeof schemasBloco]?: z.output<(typeof schemasBloco)[K]> };
/** Chave desconhecida é descartada; recurso que não passa no schema do app invalida o bloco (quem lê descarta SÓ o evento). Dinheiro chega em centavos. */
const recursosBloco = z.record(z.string(), z.unknown()).transform((bruto, ctx): RecursosBloco => {
  const saida: Record<string, unknown> = {};
  for (const [chave, valor] of Object.entries(bruto)) {
    if (!Object.hasOwn(schemasBloco, chave)) continue;
    const r = schemasBloco[chave as keyof typeof schemasBloco].safeParse(valor);
    if (!r.success && chave === "contas" && "detalhes" in bruto) continue; // `contas` ao lado de `detalhes` é só a sincronização do card de gastos: inválida, o card sai sem ela
    if (!r.success) { ctx.addIssue({ code: "custom", message: `Recurso inválido: ${chave}` }); return z.NEVER; }
    saida[chave] = r.data;
  }
  return saida as RecursosBloco;
});
/** Eventos do `/ai/chat/stream`, um schema por `tipo`. O `bloco` valida o envelope e cada recurso embutido. */
const eventosChat = {
  passo: z.object({ texto: z.string(), previa: cardId.nullable().catch(null) }),
  bloco: z.object({ bloco: z.object({ card: cardId, abas: z.array(z.object({ mes: mesAAAAMM.nullable(), recursos: recursosBloco })).min(1).max(3) }) }),
  delta: z.object({ texto: z.string() }),
  reinicio: z.object({}),
  fim: z.object({ reply: z.string(), usage, message_id: z.number().int().positive().nullable(), chips: z.array(z.string().min(1).max(40)).max(4).catch([]) }),
  erro: z.object({ mensagem: z.string().optional() }),
};
type Eventos = typeof eventosChat;
export type EventoChat = { [K in keyof Eventos]: { tipo: K } & z.infer<Eventos[K]> }[keyof Eventos];
export type BlocoChat = z.infer<Eventos["bloco"]>["bloco"];
/** `null` para tipo desconhecido ou forma inválida: quem chama descarta SÓ aquele evento e segue. */
export function lerEvento(bruto: unknown): EventoChat | null {
  const tipo = (bruto as { tipo?: unknown } | null)?.tipo;
  if (typeof tipo !== "string" || !Object.hasOwn(eventosChat, tipo)) return null;
  const r = eventosChat[tipo as keyof Eventos].safeParse(bruto);
  return r.success ? ({ ...r.data, tipo } as EventoChat) : null;
}
export const feedbackSchema = z.object({ ok: z.literal(true), nota: z.union([z.literal(1), z.literal(-1)]).nullable() });
export const cancelamentoSchema = z.object({ cancelada: z.string().nullable() });
export type PerfilPainel = NonNullable<z.infer<typeof perfilPainelSchema>["perfil"]>;
export type Lancamento = z.infer<typeof lancamentoSchema>;
