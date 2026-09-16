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

/**
 * O identificador de SESSÃO que vive dentro do access token.
 *
 * O refresh token muda a cada rotação; o `jti` não. É ele que o servidor usa
 * para saber que duas credenciais diferentes são a mesma sessão, e é o que o
 * logout precisa: entre capturar a credencial e apagar, uma renovação pode ter
 * acontecido, e comparar pelo refresh recusaria apagar a própria sessão.
 *
 * Decodifica só a carga do JWT, sem verificar assinatura — não é autenticação,
 * é leitura de um dado que o próprio app guardou. Token ilegível devolve null,
 * e aí quem chama cai no critério anterior.
 */
const ALFABETO =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/**
 * base64url → texto, sem `Buffer` e sem `atob`.
 *
 * O `Buffer` é do Node: no Hermes ele não existe, e usá-lo aqui lançava
 * `ReferenceError` que o `catch` engolia — todo token passaria a "não ter jti"
 * e o logout cairia calado no critério antigo. Pior: o `@types/node` fazia a
 * referência compilar, então nem o typecheck acusava, e o Jest roda no Node,
 * onde `Buffer` existe. Um conserto inerte no aparelho, verde em tudo aqui.
 *
 * O `atob` também não é garantido em toda versão do runtime. Doze linhas de
 * decodificação não dependem de nenhum dos dois.
 */
function deBase64Url(texto: string): string {
  const limpo = texto.replace(/-/g, "+").replace(/_/g, "/").replace(/=+$/, "");
  let bits = 0;
  let acumulado = 0;
  let saida = "";
  for (const caractere of limpo) {
    const valor = ALFABETO.indexOf(caractere);
    if (valor < 0) throw new Error("base64 inválido");
    acumulado = (acumulado << 6) | valor;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      saida += String.fromCharCode((acumulado >> bits) & 0xff);
    }
  }
  return saida;
}

export function jtiDe(access: string): string | null {
  try {
    const carga = access.split(".")[1];
    if (!carga) return null;
    const json = JSON.parse(deBase64Url(carga)) as { jti?: unknown };
    return typeof json.jti === "string" ? json.jti : null;
  } catch {
    return null;
  }
}

/**
 * Apaga se a sessão guardada for a MESMA que `access` identifica.
 *
 * "Mesma sessão" é o `jti`, não o refresh token: uma renovação entre a captura
 * e a limpeza troca o refresh e manteria o `jti`, e comparar pelo refresh
 * deixaria a sessão de pé depois de o usuário sair — com um token rotacionado
 * e válido no aparelho.
 *
 * Sem `jti` legível dos dois lados, cai na comparação por refresh, que é o
 * critério anterior e nunca apaga a sessão de outra conta.
 */
export function limparSessaoDe(access: string, refresh: string): Promise<boolean> {
  const alvo = jtiDe(access);
  return naFila(async () => {
    const atual = decodifica(await SecureStore.getItemAsync(PAR));
    if (!atual) return false;
    const mesma = alvo
      ? jtiDe(atual.access) === alvo
      : atual.refresh === refresh;
    if (!mesma) return false;
    await SecureStore.deleteItemAsync(PAR);
    return true;
  });
}

/**
 * Grava só se `permitido()` disser que sim — e a pergunta é feita DENTRO da fila.
 *
 * Existe porque conferir e gravar em dois passos deixa uma janela entre eles, e
 * o que cabe ali é uma entrada mais nova: a conta A confere, a conta B começa e
 * para numa etapa de código, e então a gravação da A acontece. O aparelho fica
 * logado como A enquanto a tela pede o código da B.
 *
 * O predicado é avaliado de forma síncrona na mesma operação da gravação, então
 * não há ponto de `await` no meio para outra tentativa se intrometer.
 */
export function guardarCredenciaisSe(
  permitido: () => boolean,
  c: Credenciais,
): Promise<boolean> {
  return naFila(async () => {
    if (!permitido()) return false;
    await SecureStore.setItemAsync(PAR, JSON.stringify(c));
    // E CONFERE DE NOVO. A gravação em si é assíncrona, então uma tentativa
    // mais nova pode ter começado enquanto ela acontecia — e ela pode nem
    // gravar nada (uma entrada que para numa etapa de código, por exemplo), o
    // que deixaria esta aqui vencendo por não ter concorrente na fila.
    //
    // Desfazer é o único jeito de fechar essa janela sem inventar um mutex
    // global: o contador vive fora do cofre, e nada aqui dentro impede alguém
    // de incrementá-lo. Como a escrita ainda não foi lida por ninguém, apagar
    // não perde informação.
    if (permitido()) return true;
    await SecureStore.deleteItemAsync(PAR).catch(() => undefined);
    return false;
  });
}
