import type { MetasV2 } from "@/api/schemas/painel";
import { nomeMes } from "./catalogo";
/**
 * A cópia da tela de metas do web (`webapp/src/dashboard/lib/metas-textos.ts`) para o card `metas` do chat, só leitura: sem os botões
 * (`acao`) e sem `antesDoCartao`. A ORDEM das chaves é a prioridade da frase quando há mais de um motivo. `metas_textos_paridade.test.ts`
 * compara chaves, ordem e textos com o web; as duas exceções (voz só-leitura: "no painel") são as de `core/services/metas_conversa.py`.
 */
export type MetaV2 = MetasV2["metas"][number];
type Ritmo = MetaV2["ritmo"];
export const DIAS_MINIMOS = 7; // db/metas_ritmo.py RITMO_DIAS_MINIMOS
export const MESES_PADRAO = 6; // db.metas.MESES_RESERVA
/** Motivo fora do mapa (código novo do servidor): nunca some, mas também não sai cru. */
export const FRASE_GENERICA = "Há informações a conferir nesta meta.";
/** Explicam o alvo da reserva, não o saldo da fonte: a frase do custo cobre. */
export const DO_ALVO = ["custo_desconhecido", "plano_sem_custo_mensal"];
/** Só o ritmo emite estes: são a explicação do ritmo (já no lugar do número), nunca um aviso à parte. db/metas_ritmo.py MOTIVOS_RITMO. */
export const SO_DO_RITMO = ["historico_curto", "historico_incompleto", "sem_historico_da_fonte"];
export const FRASE_RITMO: Record<string, string | ((dias: number | null) => string)> = {
  plano_sem_investimentos: "Seu plano atual não inclui investimentos.",
  sem_banco_conectado: "Conecte um banco pra eu acompanhar.",
  fonte_indisponivel: "Não acho mais essa fonte. Ela pode ter sido desconectada: no painel você edita a meta e escolhe outra.",
  conexao_pausada: "A conexão com o banco está pausada. Reconecte pra eu voltar a ler.",
  nenhum_investimento: "Não encontrei investimentos nos seus bancos conectados.",
  historico_incompleto: "Não consigo medir seu ritmo: um dos seus bancos não confirma a leitura dos investimentos. Reconectar o banco costuma resolver.",
  historico_curto: (dias) => dias === null
    ? "Ainda estou juntando histórico dos seus bancos. Em alguns dias eu calculo seu ritmo."
    : `Ainda estou juntando histórico (${dias} de ${DIAS_MINIMOS} dias). Volto a calcular seu ritmo a partir de ${DIAS_MINIMOS} dias.`,
  sem_historico_da_fonte: "Pra saldo de conta eu ainda não meço ritmo. Só o que está investido tem histórico.",
  saldo_ausente: "O banco não mandou o saldo de alguma posição.",
  outra_moeda: "Parte do dinheiro está em outra moeda e ficou de fora.",
  banco_desatualizado: "Seu banco não atualiza há um tempo, então o número pode estar velho.",
  moeda_presumida: "Considerei em reais uma posição sem moeda informada.",
  conta_fora_do_ultimo_sync: "Uma conta não veio na última atualização do banco.",
};
export const FRASE_CUSTO: Record<string, string> = {
  plano_sem_custo_mensal: "Seu plano não calcula as contas fixas. O valor da reserva precisa ser definido no painel.",
  sem_banco_conectado: "Conecte um banco pra eu calcular suas contas fixas.",
  sem_recorrencias: "Ainda não achei contas fixas nos seus bancos.",
  recorrencias_banco_nao_lidas: "Não consegui ler as cobranças do banco agora.",
  recorrencia_valor_invalido: "Uma cobrança veio com um valor estranho do banco. Não dá pra calcular agora.",
};
const frase = (c: string, dias: number | null) => { const f = FRASE_RITMO[c]!; return typeof f === "function" ? f(dias) : f; };
const primeiro = (mapa: object, motivos: readonly string[]) => Object.keys(mapa).find((k) => motivos.includes(k));
/** Por que o ritmo não tem número. Código fora do mapa: frase genérica; lista vazia: "Ainda não consigo medir seu ritmo.". */
export function fraseRitmo(r: Ritmo): string {
  const k = primeiro(FRASE_RITMO, r.motivos);
  return k ? frase(k, r.dias_de_historico) : r.motivos.length ? FRASE_GENERICA : "Ainda não consigo medir seu ritmo.";
}
/** Por que o custo mensal (e a reserva) não tem número; com valor, só o aviso de valor velho. */
export function fraseCusto(c: MetasV2["custo_mensal"]): string | null {
  const k = primeiro(FRASE_CUSTO, c.motivos);
  if (c.valor !== null) return k === "recorrencias_banco_nao_lidas" ? `${FRASE_CUSTO[k]} O valor pode estar velho.` : null;
  return k ? FRASE_CUSTO[k]! : c.motivos.length ? FRASE_GENERICA : "Não consegui calcular suas contas fixas.";
}
/** O fecho do ritmo: quando chega, e se é antes do prazo (comparação de "AAAA-MM" por texto). Dinheiro só é COMPARADO com zero, nunca calculado. */
export function fraseChegada(m: MetaV2): string | null {
  if (m.chega_em) return `Nesse ritmo, chega em ${nomeMes(m.chega_em)}${m.data_alvo ? (m.chega_em <= m.data_alvo.slice(0, 7) ? ", dentro do prazo" : ", depois do prazo") : ""}`;
  const v = m.ritmo.valor;
  return v !== null && v <= 0 && m.falta !== null && m.falta !== 0 ? "Nesse ritmo, a meta não anda." : null;
}
/** Motivos que só acompanham o número (os da meta e os do ritmo, menos os só do ritmo), um aviso por motivo, sem repetir a frase do ritmo que o card já mostra no lugar do número
 *  (sem saldo, sempre; com saldo e sem ritmo, só se a meta não está batida: batida não desenha o bloco do ritmo). Regra do `_avisos` do Python. */
export function avisos(m: MetaV2): string[] {
  const mostrada = m.atual === null || (m.ritmo.valor === null && m.falta !== 0) ? fraseRitmo(m.ritmo) : null;
  const codigos = new Set([...m.motivos, ...m.ritmo.motivos].filter((c) => !SO_DO_RITMO.includes(c)));
  const frases = Object.keys(FRASE_RITMO).filter((c) => codigos.has(c)).map((c) => frase(c, m.ritmo.dias_de_historico));
  const desconhecido = [...codigos].some((c) => !(c in FRASE_RITMO) && !DO_ALVO.includes(c));
  return [...new Set([...frases, ...(desconhecido ? [FRASE_GENERICA] : [])])].filter((f) => f !== mostrada);
}
