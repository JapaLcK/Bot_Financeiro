import { RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import { textoDaFalha } from "@/features/auth/entrar";
import { useSessao } from "@/features/auth/sessao";

/**
 * O tratamento de falha das telas de Segurança, o mesmo do Início
 * (`app/(app)/index.tsx`): sessão encerrada vai para o provider (que leva ao
 * login), resposta de outra conta é ignorada, e o resto vira texto para a
 * pessoa. Devolve `null` quando não há nada a mostrar.
 */
export function useFalha(): (e: unknown) => string | null {
  const sessao = useSessao();
  return (e) => {
    if (e instanceof SessaoExpirada) {
      sessao.expirou(e.detalhe);
      return null;
    }
    if (e instanceof RequisicaoSuperada) return null;
    return textoDaFalha(e);
  };
}
