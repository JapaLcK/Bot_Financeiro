import { chamar, comLimite } from "@/api/client";
import { contatoSchema, linkCodeSchema } from "@/api/schemas/conta";

/** Contato da conta (Perfil). Só leitura. */
export const lerContato = (uid: number) => chamar(`/settings/${uid}/security`, contatoSchema, { sinal: comLimite() });

/** Gera o código de vínculo e devolve o link do WhatsApp com ele. */
export const gerarLinkWhatsApp = () => chamar("/auth/link-code", linkCodeSchema, { metodo: "POST", corpo: {}, sinal: comLimite() });
