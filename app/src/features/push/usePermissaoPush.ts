import * as Notifications from "expo-notifications";
import { useCallback, useState } from "react";
import { Linking } from "react-native";

import { pedirPermissaoPush } from "@/services/push";

export type Permissao = `${Notifications.PermissionStatus}`;

/**
 * A permissão de push do sistema, para a chave da tela de Notificações. `null` = ainda não lida.
 * `ler` roda no foco e na volta do fundo (a pessoa pode ter mudado nos Ajustes). Só iOS: a tela só
 * monta a linha lá.
 */
export function usePermissaoPush() {
  const [permissao, setPermissao] = useState<Permissao | null>(null);
  const ler = useCallback(() => {
    Notifications.getPermissionsAsync().then((p) => setPermissao(p.status), () => undefined);
  }, []);
  /**
   * Indeterminada pede; negada manda aos Ajustes (o iOS não pergunta duas vezes); concedida não faz nada aqui
   * (a chave escreve). Devolve `true` só quando a permissão foi concedida AGORA, no prompt.
   */
  const tocar = async (): Promise<boolean> => {
    if (permissao === "denied") void Linking.openSettings().catch(() => undefined);
    if (permissao !== "undetermined") return false;
    try {
      const status = await pedirPermissaoPush();
      setPermissao(status);
      return status === "granted";
    } catch {
      return false; // sem resposta do sistema a chave fica como estava
    }
  };
  return { permissao, ler, tocar };
}
