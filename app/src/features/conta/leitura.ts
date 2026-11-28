import { ErroDeApi } from "@/api/client";

/** O `error.code` do envelope v2 (`{error:{code}}`) das rotas de `/api/app/conta/leitura`; o texto do servidor não vai à tela. */
const codigoDe = (e: ErroDeApi) => (e.corpo as { error?: { code?: unknown } } | null | undefined)?.error?.code;

const TEXTOS: Record<string, string> = {
  usuario_em_uso: "Esse usuário já está em uso. Escolha outro.",
  codigo_invalido: "Código incorreto. Confira o e-mail e tente de novo.",
  codigo_expirado: "O código expirou. Peça um novo.",
  acesso_leitura_indisponivel: "O acesso de visualização ainda não está disponível para a sua conta.",
};

/** A recusa que já gastou o código do e-mail (errado, vencido, ou 409 de corrida depois de conferido): o campo limpa. As demais (422, 403, 429, rede) o mantêm. */
export const gastouOCodigo = (e: unknown) => e instanceof ErroDeApi && (e.status === 409 || ["codigo_invalido", "codigo_expirado"].includes(String(codigoDe(e))));

/** O texto de uma recusa do acesso de visualização; `null` = não é recusa conhecida (quem chama cai no `useFalha`). */
export function textoDaLeitura(e: unknown): string | null {
  if (!(e instanceof ErroDeApi)) return null;
  const c = codigoDe(e);
  if (typeof c === "string" && TEXTOS[c]) return TEXTOS[c];
  if (e.status === 422) return "Usuário de 4 a 40 caracteres (minúsculas, números, ponto, hífen ou _; sem @) e senha de 8 a 72 caracteres.";
  if (e.status === 429) return "Muitas tentativas. Tente de novo mais tarde.";
  return null;
}
