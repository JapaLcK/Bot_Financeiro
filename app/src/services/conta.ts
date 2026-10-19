import { chamar, comLimite } from "@/api/client";
import { codigoEnviadoSchema, contatoSchema, exclusaoSchema, exportEnviadoSchema, linkCodeSchema, notificacoesSchema, type Notificacoes } from "@/api/schemas/conta";

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
    | "push_bank_reconnect_enabled"
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

/** Manda o código de exclusão ao e-mail da conta (3/h por conta, 5/h por IP). Corpo vazio: o destino é sempre o da sessão. */
export const pedirCodigoReauth = () => chamar("/auth/account/reauth-code", codigoEnviadoSchema, { metodo: "POST", corpo: {}, sinal: comLimite() });

/** Pede o link do ZIP por e-mail. `senha` só no modo senha; o 401 daqui pode ser da senha, não da sessão. */
export const exportarDados = (senha?: string) =>
  chamar("/auth/account/export", exportEnviadoSchema, {
    metodo: "POST",
    corpo: senha === undefined ? {} : { password: senha },
    credencialSecundaria: true,
    sinal: comLimite(),
  });

/**
 * Apaga a conta NA HORA. A confirmação já foi o Alert nativo; `confirmacao` é a constante que o servidor exige.
 * 60 s: o DELETE cancela a assinatura na Stripe e apaga na Pluggy na mesma requisição.
 */
export const excluirConta = (dados: { password?: string; code: string; totp?: string }) =>
  chamar("/auth/account", exclusaoSchema, {
    metodo: "DELETE",
    corpo: { ...dados, confirmacao: "EXCLUIR" },
    credencialSecundaria: true,
    sinal: comLimite(60_000),
  });
