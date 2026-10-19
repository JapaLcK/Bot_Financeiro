import { chamar, comLimite } from "@/api/client";
import { codigoEnviadoSchema, contatoSchema, exclusaoSchema, exportEnviadoSchema, linkCodeSchema } from "@/api/schemas/conta";

/** Contato da conta (Perfil). Só leitura. */
export const lerContato = (uid: number) => chamar(`/settings/${uid}/security`, contatoSchema, { sinal: comLimite() });

/** Gera o código de vínculo e devolve o link do WhatsApp com ele. */
export const gerarLinkWhatsApp = () => chamar("/auth/link-code", linkCodeSchema, { metodo: "POST", corpo: {}, sinal: comLimite() });

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
