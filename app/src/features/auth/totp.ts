/**
 * A regra do campo de código de 6 dígitos, uma só para o login (`CodigoMfa.tsx`),
 * o cadastro (`CodigoEmail.tsx`) e a configuração do MFA (`features/seguranca/`).
 */
export const TAMANHO_TOTP = 6;

/**
 * Mantém só dígitos ASCII (0-9) — descarta espaço, hífen, letra e qualquer
 * separador, inclusive dígito arábico-índico ("١٢٣٤٥٦"): o servidor até aceita
 * a FORMA (Python `isdigit()` conta esses como dígito), mas a comparação do
 * TOTP é contra uma string só de ASCII e nunca bate. Corta em 6 mesmo colando
 * mais, para não mandar o 7º dígito de um autofill ao servidor.
 */
export const filtrarTotp = (v: string) => v.replace(/\D+/g, "").slice(0, TAMANHO_TOTP);

/**
 * Auto-envia só quando a ENTRADA EM SI já eram 6 dígitos puros — não o valor
 * FILTRADO. Um colado com lixo ("123-456", "Código: 123456", "G-123456",
 * "123 456") pode virar 6 dígitos DEPOIS do filtro por coincidência, e
 * auto-enviar isso gastaria uma tentativa com um código que a pessoa nunca
 * digitou por completo. O autofill de SMS entrega uma string só de dígitos —
 * é esse caso que continua disparando sozinho.
 */
export const completouTotp = (cru: string) => /^[0-9]+$/.test(cru) && filtrarTotp(cru).length === TAMANHO_TOTP;
