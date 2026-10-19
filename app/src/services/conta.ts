import { chamar, comLimite } from "@/api/client";
import { contatoSchema, linkCodeSchema, notificacoesSchema, type Notificacoes } from "@/api/schemas/conta";

/** Contato da conta (Perfil). Só leitura. */
export const lerContato = (uid: number) => chamar(`/settings/${uid}/security`, contatoSchema, { sinal: comLimite() });

/** Gera o código de vínculo e devolve o link do WhatsApp com ele. */
export const gerarLinkWhatsApp = () => chamar("/auth/link-code", linkCodeSchema, { metodo: "POST", corpo: {}, sinal: comLimite() });

/** Só o que o app escreve: o servidor grava o que vier e ignora o resto (`engagement_email_enabled` fica fora de propósito). */
export type CorpoNotificacoes = Partial<
  Pick<
    Notificacoes,
    | "tip_email_enabled"
    | "insight_email_enabled"
    | "whatsapp_updates_enabled"
    | "daily_report_enabled"
    | "daily_report_hour"
    | "daily_report_minute"
    | "weekly_report_enabled"
    | "monthly_report_enabled"
  >
>;

export const lerNotificacoes = (uid: number) => chamar(`/settings/${uid}/notifications`, notificacoesSchema, { sinal: comLimite() });

/** PATCH devolve o estado inteiro (a verdade do servidor). Nunca é reenviado sozinho. */
export const salvarNotificacoes = (uid: number, corpo: CorpoNotificacoes) =>
  chamar(`/settings/${uid}/notifications`, notificacoesSchema, { metodo: "PATCH", corpo, sinal: comLimite() });
