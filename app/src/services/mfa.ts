import { z } from "zod";

import { chamar, comLimite } from "@/api/client";
import { codigosBackupSchema, mfaSetupSchema, mfaStatusSchema } from "@/api/schemas/auth";

/**
 * As rotas de MFA de `frontend/finance_bot_websocket_custom.py`. As que pedem
 * a senha de novo usam `credencialSecundaria`: o 401 delas pode ser da senha,
 * não da sessão (ver `client.ts`).
 */

export const statusMfa = () => chamar("/auth/mfa/status", mfaStatusSchema, { sinal: comLimite() });

export const iniciarMfa = (senha: string) =>
  chamar("/auth/mfa/setup", mfaSetupSchema, {
    metodo: "POST",
    corpo: { password: senha },
    credencialSecundaria: true,
    sinal: comLimite(),
  });

export const ativarMfa = (codigo: string) =>
  chamar("/auth/mfa/enable", codigosBackupSchema, {
    metodo: "POST",
    corpo: { code: codigo },
    sinal: comLimite(),
  });

/** Só TOTP: o servidor não aceita código de backup aqui. */
export const novosCodigos = (senha: string, codigo: string) =>
  chamar("/auth/mfa/regenerate-backup-codes", codigosBackupSchema, {
    metodo: "POST",
    corpo: { password: senha, code: codigo },
    credencialSecundaria: true,
    sinal: comLimite(),
  });

/** TOTP ou código de backup: o servidor tenta os dois. */
export const desativarMfa = (senha: string, codigo: string) =>
  chamar("/auth/mfa/disable", z.object({ ok: z.literal(true) }), {
    metodo: "POST",
    corpo: { password: senha, code: codigo },
    credencialSecundaria: true,
    sinal: comLimite(),
  });
