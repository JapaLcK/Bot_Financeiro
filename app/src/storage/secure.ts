import * as SecureStore from "expo-secure-store";

/**
 * Guarda de credencial, atrás de uma interface de um arquivo.
 *
 * É um dos quatro pontos que a fase web vai trocar (secure storage, cache, IAP,
 * push). No navegador não existe keychain, e a troca certa lá é cookie
 * `HttpOnly` — que o app justamente não usa. Manter a troca num arquivo só é o
 * que impede essa decisão de se espalhar por 40 chamadas.
 */
const PAR = "pb.credenciais";

export type Credenciais = { access: string; refresh: string };

/**
 * O par vai numa CHAVE SÓ, e isso é sobre atomicidade, não sobre economia.
 *
 * Com duas chaves, uma escrita podia commitar e a outra não — processo morto no
 * meio da rotação, keychain recusando. Sobrava um access token NOVO ao lado de
 * um refresh já CONSUMIDO, e os dois presentes: a leitura aceitava a sessão
 * misturada, a renovação seguinte reapresentava o token gasto, e o servidor
 * trata reapresentação como roubo e revoga TUDO do usuário
 * (`core/refresh_tokens.py`, detecção de replay). O usuário seria deslogado de
 * todos os aparelhos por causa de uma escrita parcial.
 *
 * Um valor único não tem estado intermediário: ou o par novo está lá, ou o
 * antigo continua — e os dois são pares coerentes.
 */
export async function lerCredenciais(): Promise<Credenciais | null> {
  const bruto = await SecureStore.getItemAsync(PAR);
  if (!bruto) return null;
  try {
    const { access, refresh } = JSON.parse(bruto) as Partial<Credenciais>;
    return access && refresh ? { access, refresh } : null;
  } catch {
    // Valor corrompido é sessão inválida, não exceção para a tela tratar.
    return null;
  }
}

export async function guardarCredenciais(c: Credenciais): Promise<void> {
  await SecureStore.setItemAsync(PAR, JSON.stringify(c));
}

export async function limparCredenciais(): Promise<void> {
  await SecureStore.deleteItemAsync(PAR);
}
