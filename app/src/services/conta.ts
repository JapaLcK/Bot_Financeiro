import { z } from "zod";

import { chamar, comLimite } from "@/api/client";
import { avisoDesligadoSchema, codigoEnviadoSchema, contatoSchema, credencialLeituraSchema, exclusaoSchema, exportEnviadoSchema, freioLeituraSchema, linkCodeSchema, notificacoesSchema, type Notificacoes } from "@/api/schemas/conta";

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
    | "insight_alert_email_enabled"
  >
>;

export const lerNotificacoes = (uid: number) => chamar(`/settings/${uid}/notifications`, notificacoesSchema, { sinal: comLimite() });

/** PATCH devolve o estado inteiro (a verdade do servidor). Nunca é reenviado sozinho. */
export const salvarNotificacoes = (uid: number, corpo: CorpoNotificacoes) =>
  chamar(`/settings/${uid}/notifications`, notificacoesSchema, { metodo: "PATCH", corpo, sinal: comLimite() });

/**
 * Desliga os Avisos do Piggy por e-mail com o ÚNICO corpo que o servidor isenta do plano e da credencial (D2):
 * outro campo junto o tira da exceção. A resposta pode ser a curta (conta cortada); quem chama relê.
 */
export const desligarAviso = (uid: number) =>
  chamar(`/settings/${uid}/notifications`, avisoDesligadoSchema, { metodo: "PATCH", corpo: { insight_alert_email_enabled: false }, sinal: comLimite() });

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

/** O freio da seção "Acesso de visualização" (Segurança) e se a sessão é de leitor. Falha de qualquer tipo = sem a seção: quem chama trata como sem nada. */
export const lerFreioLeitura = () => chamar("/api/app/me", freioLeituraSchema, { sinal: comLimite() });

export const lerCredencialLeitura = () => chamar("/api/app/conta/leitura", credencialLeituraSchema, { sinal: comLimite() });

/** Manda ao e-mail do dono o código que autoriza o PUT/DELETE abaixo (finalidade própria; 3/h por conta somando a da exclusão). */
export const pedirCodigoLeitura = () => chamar("/api/app/conta/leitura/codigo", z.unknown(), { metodo: "POST", corpo: {}, sinal: comLimite() });

export const definirLeitura = (usuario: string, senha: string, codigo: string) =>
  chamar("/api/app/conta/leitura", credencialLeituraSchema, { metodo: "PUT", corpo: { usuario, senha, codigo }, sinal: comLimite() });

export const desativarLeitura = (codigo: string) =>
  chamar("/api/app/conta/leitura", credencialLeituraSchema, { metodo: "DELETE", corpo: { codigo }, sinal: comLimite() });
