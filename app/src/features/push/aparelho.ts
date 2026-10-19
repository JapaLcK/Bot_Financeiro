import * as Application from "expo-application";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

/**
 * O lado nativo do push: token APNs e desregistro. É FOLHA de propósito — nunca
 * importa `client` nem `storage/secure`. O `secure.ts` importa este arquivo (o
 * gancho de limpeza), e o `services/push.ts` importa os dois: se este importasse
 * o cofre de volta, seria um ciclo.
 */

/** Os valores que `/api/push/register` aceita. */
export type AmbienteApns = "sandbox" | "production";

/**
 * O ambiente do APNs vem do `aps-environment` do perfil de provisionamento,
 * NÃO do `APP_ENV` (que é o backend a que o app fala). Build de loja/TestFlight
 * e simulador não trazem o perfil embutido (`null`): aparelho de verdade assim
 * é produção; simulador é sandbox.
 */
export async function ambienteApns(): Promise<AmbienteApns> {
  const aps = await Application.getIosPushNotificationServiceEnvironmentAsync().catch(() => null);
  if (aps === "development") return "sandbox";
  if (aps === "production") return "production";
  return Device.isDevice ? "production" : "sandbox";
}

/**
 * Fila das operações nativas, no mesmo padrão da `naFila` do `secure.ts`:
 * `operacao` nos DOIS lados do `then` mantém a corrente viva depois de uma falha.
 * Serve para um registro pedido DEPOIS de um desregistro não poder passar na
 * frente dele.
 */
let fila: Promise<unknown> = Promise.resolve();

function naFila<T>(operacao: () => Promise<T>): Promise<T> {
  const resultado = fila.then(operacao, operacao);
  fila = resultado.catch(() => undefined);
  return resultado;
}

/**
 * O último token que o app mandou ao servidor, para o ouvinte de troca de token (`services/push.ts::registrarToken`)
 * não repetir o POST. Mora aqui, na folha, e não em `services/push.ts`, porque o desregistro (chamado pelo
 * `secure.ts`) precisa zerá-lo e a folha não pode importar quem a importa.
 */
let ultimoToken: string | null = null;
export const lembrarToken = (token: string | null) => void (ultimoToken = token);
export const tokenJaEnviado = (token: string) => ultimoToken === token;

/** Solta a espera do token em voo (`null`); só existe enquanto um `tokenSe` aguarda o nativo. */
let soltarEspera: (() => void) | null = null;
/** Conta os desregistros: um `tokenSe` que decidia (lendo o cofre) enquanto um aconteceu desiste. */
let desregistros = 0;

/**
 * Desregistra o aparelho no APNs. Enfileira de forma SÍNCRONA (por isso devolve
 * `void`): quem chama é a limpeza do cofre, e nenhum registro pedido depois dela
 * pode entrar na fila antes. Falha é engolida — a limpeza da sessão não depende
 * disto.
 *
 * Antes de enfileirar, CANCELA a espera do token em voo: sem isso o desregistro ficaria até `TEMPO_TOKEN_MS`
 * atrás de um token pendurado (offline), e fechar o app nesse intervalo o perderia. O desregistro roda depois da
 * CHAMADA nativa em voo, mas pode rodar antes da RESPOSTA dela (efeito no APNs: só no iPhone).
 */
export function desregistrarAparelho(): void {
  if (Platform.OS !== "ios") return;
  desregistros++;
  ultimoToken = null;
  soltarEspera?.();
  naFila(() => Notifications.unregisterForNotificationsAsync()).catch(() => undefined);
}

/**
 * O nativo só resolve o token em `didRegister`/`didFail`: offline ou no simulador ele pode nunca resolver, e
 * como a fila é uma só, o desregistro do Sair e todo registro seguinte ficariam presos atrás dele.
 */
const TEMPO_TOKEN_MS = 10_000;

/**
 * O token APNs, mas só se `haSessao()` disser que ainda há sessão — a pergunta é
 * feita DENTRO da fila, depois de qualquer desregistro pendente. Sem sessão no
 * cofre nunca se chama `getDevicePushTokenAsync`: ele registra o aparelho no
 * APNs de novo e desfaria o desregistro do Sair.
 */
export function tokenSe(haSessao: () => Promise<boolean>): Promise<string | null> {
  return naFila(async () => {
    const antes = desregistros;
    if (!(await haSessao()) || antes !== desregistros) return null;
    let cronometro: ReturnType<typeof setTimeout> | undefined;
    const limite = new Promise<null>((resolve) => {
      cronometro = setTimeout(resolve, TEMPO_TOKEN_MS, null);
      soltarEspera = () => resolve(null);
    });
    try {
      // Estourou o prazo: `null`, a fila segue e o token que chegar DEPOIS é ignorado (ninguém espera por ele, então nenhum POST sai).
      const token = await Promise.race([Notifications.getDevicePushTokenAsync(), limite]);
      return typeof token?.data === "string" && token.data ? token.data : null;
    } finally {
      soltarEspera = null;
      clearTimeout(cronometro);
    }
  });
}
