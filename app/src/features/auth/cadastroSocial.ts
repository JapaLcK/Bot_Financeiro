import { ErroDeApi } from "@/api/client";
import { EntradaSuperada, completarCadastroSocial, type ProvedorSocial } from "@/services/auth";
import { FalhaNoCofre } from "@/storage/secure";

import { textoSocial, type EstadoEntrar } from "./entrar";

/** A mensagem da fase X quando a conta já nasceu no servidor: a saída é entrar pelo MESMO provedor. */
export const ERRO_COFRE_SOCIAL: Record<ProvedorSocial, string> = {
  google: "Sua conta foi criada, mas não conseguimos abrir a sessão neste aparelho. Entre com o Google de novo.",
  apple: "Sua conta foi criada, mas não conseguimos abrir a sessão neste aparelho. Entre com a Apple de novo.",
};

/**
 * K → resultado, para Google e Apple. O 400 do `complete-signup` só distingue o
 * cadastro vencido pelo texto (`consume_pending_google_signup`,
 * `db/google_auth.py`, que começa com "Cadastro expirado" nos dois provedores):
 * esse volta ao formulário, porque o token morreu; os de nome/telefone ficam em C.
 *
 * ponytail: casa pelo começo do `detail`, sem `code` no corpo. Se o texto do
 * servidor mudar, o vencido cai em C com o aviso certo e a pessoa volta pelo
 * Voltar. Ganha um `code` no backend quando isso incomodar.
 */
export async function criarContaSocial(
  c: { provedor: ProvedorSocial; token: string; email: string; nome: string },
  telefone: string,
  autenticar: () => void,
): Promise<EstadoEntrar> {
  const voltaAoC = (aviso?: string): EstadoEntrar => ({ fase: "cadastro-social", ...c, aviso });
  try {
    await completarCadastroSocial(c.provedor, c.token, c.nome.trim(), telefone.replace(/\D+/g, ""));
    autenticar();
    return { fase: "criando-social", provedor: c.provedor, token: c.token, email: c.email };
  } catch (e) {
    if (e instanceof EntradaSuperada) return voltaAoC();
    if (e instanceof FalhaNoCofre) return { fase: "erro-cofre", mensagem: ERRO_COFRE_SOCIAL[c.provedor] };
    if (e instanceof ErroDeApi && e.status === 400 && e.detalhe.startsWith("Cadastro expirado")) {
      return { fase: "formulario", aviso: e.detalhe };
    }
    return voltaAoC(textoSocial(e));
  }
}
