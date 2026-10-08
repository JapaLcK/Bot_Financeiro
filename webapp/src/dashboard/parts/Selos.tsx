import type { Contas, Investido, Lancamento, MotivoPrevisao, ResumoDoMes } from "../lib/api-v2.gen";

export type Motivo = Contas["motivos"][number] | ResumoDoMes["motivos"][number] | Lancamento["motivos"][number] | Investido["motivos"][number];

// Por que um número da /api/v2 não é exato, em português. Código novo do servidor sai cru
// (melhor que esconder a ressalva). Os da previsão saem de core/services/cashflow_snapshot.py
// (`_motivo(s, '…')`); tests/frontend/dashboard_v2_previsao.test.mjs confere a paridade.
const MOTIVO: Record<string, string> = {
  carteira_nao_confirmada: "a confirmar",
  transacao_pendente: "transação pendente",
  conciliacao_pendente: "conciliação pendente",
  movimentos_pendentes: "movimentos pendentes",
  especie_incompleta: "espécie a conferir",
  banco_desatualizado: "banco desatualizado",
  saldo_ausente: "saldo ausente",
  moeda_presumida: "moeda presumida",
  conta_fora_do_ultimo_sync: "fora da última atualização",
  outra_moeda: "outra moeda",
  conexao_pausada: "conexão pausada",
  inicio_do_historico: "início do histórico",
  sem_banco_conectado: "sem banco conectado",
  // previsão
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
  valor_boleto_desconhecido: "valor do boleto desconhecido",
  valor_boleto_estimado: "valor do boleto estimado",
  valor_fatura_a_conferir: "valor da fatura a conferir",
  valor_recorrente_desconhecido: "valor do fixo desconhecido",
  valor_recorrente_estimado: "valor do fixo estimado",
};
export const rotulo = (codigo: string) => MOTIVO[codigo] ?? codigo;

// Para que lado o número real pode ficar, por motivo da previsão. `so_melhora`: a falta ou o
// erro do dado só deixa a projeção melhor do que a realidade, então o real pode ser PIOR
// (docs/plano-piggy-assistente-contextual.md); `so_piora` é o contrário.
export const DIRECAO: Record<MotivoPrevisao["direcao_do_erro"], string> = {
  so_melhora: "o real pode ser pior",
  so_piora: "o real pode ser melhor",
  ambos: "pode variar para os dois lados",
};

// O bloco (ou a tela) ainda é de exemplo. O espaço antes é para o leitor de tela.
export const Demonstracao = () => <> <span className="selo">demonstração</span></>;

export function Selos({ motivos }: { motivos: readonly string[] }) {
  if (!motivos.length) return null;
  // o espaço entre os selos é para o leitor de tela (e a cópia) não colar as palavras
  return <p className="selos">{motivos.flatMap((m, i) => [i ? " " : null, <span key={m} className="selo">{rotulo(m)}</span>])}</p>;
}

// Motivos da previsão com a direção do erro, em lista (texto longo demais para selo).
export function MotivosPrevisao({ motivos }: { motivos: MotivoPrevisao[] }) {
  if (!motivos.length) return null;
  return (
    <ul className="prev-motivos">
      {motivos.map((m) => <li key={m.codigo + m.direcao_do_erro}><b>{rotulo(m.codigo)}</b> <span className="faint">· {DIRECAO[m.direcao_do_erro] ?? m.direcao_do_erro}</span></li>)}
    </ul>
  );
}
