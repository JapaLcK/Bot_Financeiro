// Cópia de `MOTIVO` (inclui os códigos das contas, que a previsão repassa), `DIRECAO` e `ESTADO` do /painel (`webapp/src/dashboard/parts/Selos.tsx`, `widgets/Previsao.tsx`): o app não importa o web (§0.7);
// `__tests__/features/previsao_motivos_paridade.test.ts` compara com o web e com os `_motivo(s, '…')` do Python. Código novo do servidor sai cru.
export const MOTIVO: Record<string, string> = {
  carteira_nao_confirmada: "a confirmar",
  especie_incompleta: "espécie a conferir",
  banco_desatualizado: "banco desatualizado",
  saldo_ausente: "saldo ausente",
  moeda_presumida: "moeda presumida",
  conta_fora_do_ultimo_sync: "fora da última atualização",
  outra_moeda: "outra moeda",
  conexao_pausada: "conexão pausada",
  acao_financeira_pendente: "pedido pendente na conversa",
  bancos_excluidos: "bancos fora do cálculo",
  calendario_fatura_presumido: "data da fatura presumida",
  calendario_recorrente_desconhecido: "data do fixo desconhecida",
  cartao_manual_cobertura_incompleta: "cartão manual incompleto",
  cobertura_bancaria_nao_comprovada: "banco pode não ter tudo",
  cobertura_fatura_nao_comprovada: "fatura pode estar incompleta",
  conciliacao_a_conferir: "conciliação a conferir",
  data_boleto_desconhecida: "boleto sem data",
  declaracao_bancaria_a_conferir: "movimento do banco a conferir",
  gastos_variaveis_nao_estimados: "gastos do dia a dia fora da conta",
  incorporacao_cartao_nao_comprovada: "pode já estar na fatura",
  instancia_recorrente_a_conferir: "conta do mês a conferir",
  moeda_fatura_desconhecida: "moeda da fatura desconhecida",
  pagamento_fatura_reflexo_desconhecido: "débito da fatura a conferir",
  parcelas_futuras_incompletas: "parcelas futuras incompletas",
  realizacao_boleto_a_conferir: "pagamento do boleto a conferir",
  realizacao_fatura_a_conferir: "pagamento da fatura a conferir",
  realizacao_passada_desconhecida: "pagamento anterior a conferir",
  receita_nao_garantida: "receita não garantida",
  recorrencia_banco_estimada: "estimado pelo histórico do banco",
  recorrencia_banco_atrasada: "cobrança do mês ainda não apareceu",
  recorrencia_banco_interrompida: "parou de aparecer no banco",
  recorrencia_banco_igual_a_fixo_manual: "já contado no seu fixo",
  recorrencia_banco_pode_repetir_manual: "pode repetir um fixo seu",
  recorrencia_banco_conta_fora_da_base: "conta fora do saldo de partida",
  recorrencias_banco_nao_lidas: "recorrências do banco desatualizadas",
  valor_boleto_desconhecido: "valor do boleto desconhecido",
  valor_boleto_estimado: "valor do boleto estimado",
  valor_fatura_a_conferir: "valor da fatura a conferir",
  valor_recorrente_desconhecido: "valor do fixo desconhecido",
  valor_recorrente_estimado: "valor do fixo estimado",
};
export const DIRECAO: Record<string, string> = {
  so_melhora: "o real pode ser pior",
  so_piora: "o real pode ser melhor",
  ambos: "pode variar para os dois lados",
};
/** Rótulo da estimativa do gasto do dia a dia; o /painel tem o mesmo literal em `widgets/Previsao.tsx` (`previsao_motivos_paridade.test.ts` compara). */
export const GASTOS_DIA_A_DIA = "Gastos do dia a dia";
export const ESTADO: Record<string, string> = { condicional: "Previsão condicional", a_conferir: "A conferir" };
/** Os códigos do patrimônio (`db/patrimonio.py`) que o Progresso ao milhão mostra e a previsão não tem (FORA de `MOTIVO`: o teste de paridade exige que ele tenha só os códigos do Python). Cópia de `Selos.tsx` do web, conferida em `previsao_motivos_paridade.test.ts`. */
export const MOTIVO_PATRIMONIO: Record<string, string> = {
  manual_e_banco: "manual e banco juntos",
  caixinha_espelhada_fora: "caixinha do banco fora da soma",
  conciliacao_pendente: "conciliação pendente",
  movimentos_pendentes: "movimentos pendentes",
};
// Só chave PRÓPRIA: "constructor"/"__proto__" não podem virar função/objeto.
export const ou = (mapa: Record<string, string>, k: string) => (Object.prototype.hasOwnProperty.call(mapa, k) ? mapa[k]! : k);
