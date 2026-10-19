import { ContratoInvalido, ErroDeApi } from "@/api/client";
import type { Notificacoes } from "@/api/schemas/conta";
import type { CorpoNotificacoes } from "@/services/conta";

export const FALHA_SALVAR = "Não conseguimos salvar. Tente de novo.";
export const SO_PLUS = "O resumo semanal é dos planos Plus e Pro.";

export const hhmm = (h: number, m: number) => `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;

/** Fora do plano o servidor devolve o semanal efetivo como desligado; o que a pessoa vê é o que ficou gravado. */
export const semanalNaTela = (d: Notificacoes) => (d.weekly_report_available ? d.weekly_report_enabled : d.weekly_report_stored_enabled);

/** Fora do plano e gravado desligado: ligar daria 403, então a linha nem oferece. Gravado ligado ainda pode ser desligado. */
export const semanalTravado = (d: Notificacoes) => !d.weekly_report_available && !d.weekly_report_stored_enabled;

/** O horário só vale se algum resumo realmente sai (o semanal fora do plano não sai, por isso o efetivo e não o gravado). */
export const algumResumo = (d: Notificacoes) => d.daily_report_enabled || d.weekly_report_enabled || d.monthly_report_enabled;

/**
 * Trocar a hora no servidor LIGA o diário (`set_daily_report_hour`); mandar `daily_report_enabled` junto,
 * aplicado depois, devolve o diário ao que o servidor tinha. `diarioAtual` é o da última RESPOSTA, não da prévia.
 */
export const corpoDaHora = (h: number, m: number, diarioAtual: boolean): CorpoNotificacoes => ({
  daily_report_hour: h,
  daily_report_minute: m,
  daily_report_enabled: diarioAtual,
});

/** `mensagemDeErro` devolve o `error` cru ("pro_required"): a tela nunca mostra isso, por isso o corpo é olhado aqui. */
const codigo = (e: ErroDeApi) => (e.corpo as { detail?: { error?: unknown } } | null)?.detail?.error;
export const proRequired = (e: unknown) => e instanceof ErroDeApi && e.status === 403 && codigo(e) === "pro_required";

/** Texto do Banner de uma escrita que falhou. 400 traz o `detail` em português do servidor; o resto é genérico. */
export function textoDaEscrita(e: unknown): string {
  if (proRequired(e)) return SO_PLUS;
  if (e instanceof ErroDeApi && !(e instanceof ContratoInvalido) && e.status === 400 && e.detalhe.trim()) return e.detalhe;
  return FALHA_SALVAR;
}
