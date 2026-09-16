import * as SecureStore from "expo-secure-store";

/**
 * Guarda de credencial, atrás de uma interface de um arquivo.
 *
 * É um dos quatro pontos que a fase web vai trocar (secure storage, cache, IAP,
 * push). No navegador não existe keychain, e a troca certa lá é cookie
 * `HttpOnly` — que o app justamente não usa. Manter a troca num arquivo só é o
 * que impede essa decisão de se espalhar por 40 chamadas.
 */
const ACCESS = "pb.access";
const REFRESH = "pb.refresh";

export type Credenciais = { access: string; refresh: string };

export async function lerCredenciais(): Promise<Credenciais | null> {
  const [access, refresh] = await Promise.all([
    SecureStore.getItemAsync(ACCESS),
    SecureStore.getItemAsync(REFRESH),
  ]);
  // Os dois ou nenhum: meia sessão guardada é sessão que falha no primeiro 401
  // e não sabe se renova ou desloga.
  return access && refresh ? { access, refresh } : null;
}

export async function guardarCredenciais(c: Credenciais): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(ACCESS, c.access),
    SecureStore.setItemAsync(REFRESH, c.refresh),
  ]);
}

export async function limparCredenciais(): Promise<void> {
  await Promise.all([
    SecureStore.deleteItemAsync(ACCESS),
    SecureStore.deleteItemAsync(REFRESH),
  ]);
}
