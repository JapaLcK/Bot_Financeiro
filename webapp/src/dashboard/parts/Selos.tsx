import type { Contas, ResumoDoMes } from "../lib/api-v2.gen";

export type Motivo = Contas["motivos"][number] | ResumoDoMes["motivos"][number];

// Por que um número da /api/v2 não é exato, em português. Código novo do servidor sai cru
// (melhor que esconder a ressalva).
const MOTIVO: Record<Motivo, string> = {
  carteira_nao_confirmada: "a confirmar",
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
};

// O bloco (ou a tela) ainda é de exemplo. O espaço antes é para o leitor de tela.
export const Demonstracao = () => <> <span className="selo">demonstração</span></>;

export function Selos({ motivos }: { motivos: Motivo[] }) {
  if (!motivos.length) return null;
  // o espaço entre os selos é para o leitor de tela (e a cópia) não colar as palavras
  return <p className="selos">{motivos.flatMap((m, i) => [i ? " " : null, <span key={m} className="selo">{MOTIVO[m] ?? m}</span>])}</p>;
}
