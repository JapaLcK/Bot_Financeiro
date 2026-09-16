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
 * Toda operação de sessão entra nesta fila, e sai uma de cada vez.
 *
 * O JavaScript do React Native roda numa thread só, mas isso não dá
 * atomicidade: cada `await` é um ponto onde outra operação assume. Ler o cofre,
 * decidir e gravar eram três passos com dois desses pontos no meio — e uma
 * entrada ou uma saída de conta que caísse ali gravava por cima de uma decisão
 * tomada sobre um estado que já não existia.
 *
 * A fila fecha isso: enquanto uma operação não termina, nenhuma outra começa,
 * então "ler, comparar e gravar" vira indivisível.
 */
let fila: Promise<unknown> = Promise.resolve();

function naFila<T>(operacao: () => Promise<T>): Promise<T> {
  // `operacao` nos DOIS lados do `then` é o que mantém a corrente viva: se a
  // operação anterior falhou, a seguinte roda mesmo assim. Uma falha de
  // keychain não pode travar a sessão inteira do app.
  const resultado = fila.then(operacao, operacao);
  // E o `catch` garante a MESMA coisa por outro caminho, além de evitar que uma
  // falha sem operação seguinte fique como rejeição não tratada (no React
  // Native isso vira aviso no console). A redundância é deliberada e está
  // medida: tirar qualquer UMA das duas linhas mantém o grupo verde; só tirando
  // as DUAS o teste de falha fica vermelho. Quem mexer aqui precisa saber que a
  // garantia não mora numa linha só.
  fila = resultado.catch(() => undefined);
  return resultado;
}

function decodifica(bruto: string | null): Credenciais | null {
  if (!bruto) return null;
  try {
    const { access, refresh } = JSON.parse(bruto) as Partial<Credenciais>;
    return access && refresh ? { access, refresh } : null;
  } catch {
    // Valor corrompido é sessão inválida, não exceção para a tela tratar.
    return null;
  }
}

/**
 * O par vai numa CHAVE SÓ, e isso é sobre atomicidade, não sobre economia.
 *
 * Com duas chaves, uma escrita podia commitar e a outra não — processo morto no
 * meio da rotação, keychain recusando. Sobrava um access token NOVO ao lado de
 * um refresh já CONSUMIDO, e os dois presentes: a leitura aceitava a sessão
 * misturada, a renovação seguinte reapresentava o token gasto, e o servidor
 * trata reapresentação como roubo e revoga TUDO do usuário. O usuário seria
 * deslogado de todos os aparelhos por causa de uma escrita parcial.
 */
export function lerCredenciais(): Promise<Credenciais | null> {
  return naFila(async () => decodifica(await SecureStore.getItemAsync(PAR)));
}

export function guardarCredenciais(c: Credenciais): Promise<void> {
  return naFila(() => SecureStore.setItemAsync(PAR, JSON.stringify(c)));
}

export function limparCredenciais(): Promise<void> {
  return naFila(() => SecureStore.deleteItemAsync(PAR));
}

/**
 * Compara-e-troca: só grava se a sessão guardada ainda for `esperado`.
 *
 * É o que a renovação precisa. Conferir o dono e depois gravar em duas chamadas
 * deixa uma janela entre elas, e é nela que cabe a entrada de outra conta — o
 * resultado seria a sessão antiga restaurada por cima da nova, seguida da
 * repetição da requisição da antiga. Num POST de dinheiro, escrita na conta
 * errada.
 *
 * Aqui a comparação e a gravação acontecem dentro da MESMA operação da fila,
 * então não há janela. Devolve `false` quando a sessão mudou — e aí quem chamou
 * sabe que não deve repetir nada.
 */
export function trocarSe(
  esperado: string,
  novo: Credenciais,
): Promise<boolean> {
  return naFila(async () => {
    const atual = decodifica(await SecureStore.getItemAsync(PAR));
    if (atual?.refresh !== esperado) return false;
    await SecureStore.setItemAsync(PAR, JSON.stringify(novo));
    return true;
  });
}

/** Apaga só se a sessão guardada ainda for `esperado`. O par do `trocarSe`. */
export function limparSe(esperado: string): Promise<boolean> {
  return naFila(async () => {
    const atual = decodifica(await SecureStore.getItemAsync(PAR));
    if (atual?.refresh !== esperado) return false;
    await SecureStore.deleteItemAsync(PAR);
    return true;
  });
}
