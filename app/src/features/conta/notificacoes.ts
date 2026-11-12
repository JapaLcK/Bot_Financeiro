import { ContratoInvalido, ErroDeApi } from "@/api/client";
import type { Notificacoes } from "@/api/schemas/conta";
import type { CorpoNotificacoes } from "@/services/conta";

export const FALHA_SALVAR = "Não conseguimos salvar. Tente de novo.";
export const FALHA_CARREGAR = "Não conseguimos carregar suas notificações.";
export const SO_PLUS = "O resumo semanal é dos planos Plus e Pro.";
export const SO_PLUS_AVISO = "Os Avisos do Piggy por e-mail são dos planos Plus e Pro.";

// Rótulos curtos dos Avisos do Piggy por e-mail: cópias do web (`webapp/.../Notificacoes.tsx`, `frontend/settings.html`), vigiadas
// por `notificacoes_aviso_paridade.test.ts`. Os textos explicativos NÃO moram aqui: vêm do servidor.
export const TITULO_AVISO = "Avisos do Piggy por e-mail";
export const PLUS_E_PRO = "Plus e Pro";
export const LIGUE_INSIGHTS = "Ligue Insights financeiros";
export const DESLIGADOS_D5 = "Avisos do Piggy por e-mail desligados.";
export const DESLIGADOS = "Avisos desligados.";
export const DESLIGAR_AGORA = "Desligar agora";

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

/**
 * A linha dos Avisos (igual ao `/painel`): ligar pede e-mail, plano e Insights ligado; desligar nunca trava. A `dica` (hint do leitor
 * de tela) leva o selo, que no web fica dentro do `<label>`, e os textos do servidor.
 */
export function linhaDoAviso(d: Notificacoes) {
  const aderiu = d.insight_alert_email_enabled, noPlano = d.insight_alert_email_available;
  const selo = !noPlano ? (aderiu ? undefined : PLUS_E_PRO) : d.email_notifications_available && !d.insight_email_enabled ? LIGUE_INSIGHTS : undefined;
  return {
    travado: !aderiu && (!d.email_notifications_available || !noPlano || !d.insight_email_enabled),
    selo,
    dica: [selo && `${selo}.`, d.insight_alert_email_notice, d.insight_alert_email_paused_notice].filter(Boolean).join(" "),
  };
}

/** `mensagemDeErro` devolve o `error` cru ("pro_required"): a tela nunca mostra isso, por isso o corpo é olhado aqui. */
const detalhe = (e: ErroDeApi) => (e.corpo as { detail?: { error?: unknown; feature?: unknown; insight_alert_email_enabled?: unknown; insight_alert_email_lapsed_notice?: unknown } } | null)?.detail;
export const proRequired = (e: unknown) => e instanceof ErroDeApi && e.status === 403 && detalhe(e)?.error === "pro_required";

/** 402 do GET de conta cortada com adesão viva (D2): o texto da margem que o servidor mandou, ou `null` (402 de sempre). */
export function avisoSemPlano(e: unknown): string | null {
  const d = e instanceof ErroDeApi && e.status === 402 ? detalhe(e) : undefined;
  const texto = d?.insight_alert_email_lapsed_notice;
  return d?.error === "subscription_required" && d.insight_alert_email_enabled === true && typeof texto === "string" && texto.trim() ? texto : null;
}

/** Texto do Banner de uma escrita que falhou. 400 e 409 trazem o `detail` em português do servidor; o resto é genérico. */
export function textoDaEscrita(e: unknown): string {
  if (proRequired(e)) {
    const recurso = detalhe(e as ErroDeApi)?.feature;
    if (recurso === "weekly_report") return SO_PLUS;
    if (recurso === "insights") return SO_PLUS_AVISO;
  }
  if (e instanceof ErroDeApi && !(e instanceof ContratoInvalido) && (e.status === 400 || e.status === 409) && e.detalhe.trim()) return e.detalhe;
  return FALHA_SALVAR;
}
