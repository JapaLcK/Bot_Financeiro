import { ErroDeApi } from "@/api/client";
import type { Perfil } from "@/api/schemas/auth";
import { GENERICO } from "@/features/auth/entrar";

/** Como a conta se reautentica (`MODO_SQL` de `db/reauth_codes.py`): sem credencial não há o que digitar nem a quem mandar código. */
export type ModoConta = "sem_credencial" | "codigo" | "senha";

export const modoDaConta = (p: Perfil): ModoConta => (p.precisa_criar_senha ? "sem_credencial" : p.has_password === false ? "codigo" : "senha");

const codigoDe = (e: ErroDeApi) => (e.corpo as { detail?: { code?: unknown } } | null | undefined)?.detail?.code;

/** Que campo o erro aponta: o que a tela esvazia e onde devolve o foco. */
export function campoDoErro(e: unknown): "senha" | "codigo" | "totp" | null {
  if (!(e instanceof ErroDeApi)) return null;
  const code = codigoDe(e);
  if (code === "codigo_obrigatorio" || code === "codigo_invalido" || code === "codigo_expirado") return "codigo";
  if (code === "mfa_obrigatorio" || code === "mfa_invalido") return "totp";
  // Sem `code`: "Senha incorreta." (401) e "Informe sua senha…" (400) são strings.
  if (code === undefined && (e.status === 401 || (e.status === 400 && /senha/i.test(e.detalhe)))) return "senha";
  return null;
}

export const NAO_CONFIRMOU = "Não deu para confirmar. Se a conta foi excluída, chega um e-mail.";

/**
 * O texto do Banner de um erro do DELETE. `texto` é o que o `useFalha` já devolveu.
 * `confirmacao_invalida` e 422 são defeito do app: genérico, nunca o "Digite EXCLUIR" do servidor.
 * Rede e tempo limite: o servidor pode ter apagado sem a resposta chegar.
 */
export function textoDaExclusao(e: unknown, texto: string): string {
  if (!(e instanceof ErroDeApi)) return NAO_CONFIRMOU;
  return e.status === 422 || codigoDe(e) === "confirmacao_invalida" ? GENERICO : texto;
}
