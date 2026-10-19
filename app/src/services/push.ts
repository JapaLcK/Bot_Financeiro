import { Platform } from "react-native";
import * as Notifications from "expo-notifications";
import { z } from "zod";

import { chamar, comLimite } from "@/api/client";
import { ambienteApns, tokenSe } from "@/features/push/aparelho";
import { lerCredenciais } from "@/storage/secure";

/**
 * O prompt de permissão do sistema (alerta e som, SEM badge). Concedida, registra o aparelho na hora
 * (sem sessão no cofre o registro não faz nada). Devolve o status, para a tela mostrar o que ficou.
 */
export async function pedirPermissaoPush(): Promise<`${Notifications.PermissionStatus}`> {
  const r = await Notifications.requestPermissionsAsync({ ios: { allowAlert: true, allowSound: true, allowBadge: false } });
  if (r.granted) void registrarAparelho();
  return r.status;
}

/**
 * Registra o token APNs deste aparelho na sessão logada. Só iOS, só com a
 * permissão concedida e só com sessão no cofre (o `tokenSe` confere dentro da
 * fila, depois de qualquer desregistro pendente).
 *
 * Engole qualquer erro, de propósito: rede, 5xx, 403 `password_required`,
 * `SessaoExpirada`, `RequisicaoSuperada`, token recusado no simulador. Push é
 * acessório, e o próximo gatilho (login, abertura, volta do fundo) tenta de novo.
 * O servidor faz upsert por token, então repetir é idempotente.
 */
export async function registrarAparelho(): Promise<void> {
  if (Platform.OS !== "ios") return;
  try {
    if ((await Notifications.getPermissionsAsync()).status !== "granted") return;
    const token = await tokenSe(async () => (await lerCredenciais()) !== null);
    if (!token) return;
    await chamar("/api/push/register", z.unknown(), {
      metodo: "POST",
      corpo: { token, platform: "ios", environment: await ambienteApns() },
      sinal: comLimite(),
    });
  } catch {
    // ver o comentário acima
  }
}
