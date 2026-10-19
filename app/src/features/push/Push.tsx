import * as Notifications from "expo-notifications";
import { useRootNavigationState, useRouter, type Href } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { Platform } from "react-native";

import { useSessao } from "@/features/auth/sessao";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { useVoltaDoFundo } from "@/features/seguranca/voltaDoFundo";
import { pedirPermissaoPush, registrarAparelho } from "@/services/push";

/**
 * Com o app aberto: aviso no topo e som, sem badge (decisão do dono). Sem isto o
 * iOS descarta a notificação em primeiro plano.
 */
if (Platform.OS === "ios") {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}

/**
 * Tabela própria do toque, e um `Map` (nunca `ROTAS[rota]` num objeto literal:
 * `"__proto__"` e `"constructor"` viram achado). NÃO é o `+native-intent`, que só
 * filtra URL do Linking. Rota nova do push entra aqui.
 */
const ROTAS = new Map<string, Href>([["conexoes", "/conexoes"]]);

/**
 * A rota que o toque pede, ou `null`. O servidor põe `rota` na RAIZ do payload e
 * o iOS não a copia para `content.data` (ali vem vazio em push remoto): o
 * userInfo inteiro fica em `trigger.payload`.
 */
export function rotaDoToque(resposta: Notifications.NotificationResponse): Href | null {
  if (resposta.actionIdentifier !== Notifications.DEFAULT_ACTION_IDENTIFIER) return null;
  const gatilho = resposta.notification.request.trigger;
  if (!gatilho || !("type" in gatilho) || gatilho.type !== "push") return null;
  const rota = gatilho.payload?.rota;
  return typeof rota === "string" ? (ROTAS.get(rota) ?? null) : null;
}

/**
 * Sem UI. Pede a permissão uma vez, registra o token a cada login e abertura, e
 * leva o toque na notificação até a tela. Só iOS (o `_layout` só monta no iOS).
 * O desregistro mora no cofre (`storage/secure.ts`), não aqui.
 */
export function Push() {
  const { estado } = useSessao();
  const trava = useBloqueio().estado.fase;
  const router = useRouter();
  const navegacaoPronta = Boolean(useRootNavigationState()?.key);
  const fase = estado.fase;
  const autenticado = fase === "autenticado";
  // Com a trava em `lendo`/`travado` não há prompt: colidiria com o Face ID.
  const livre = (trava === "livre" || trava === "inerte") && (fase === "anonimo" || autenticado);

  const pediu = useRef(false);
  useEffect(() => {
    if (!livre || pediu.current) return;
    pediu.current = true;
    void (async () => {
      try {
        if ((await Notifications.getPermissionsAsync()).status !== "undetermined") return;
        await pedirPermissaoPush();
      } catch {
        // Sem permissão o push não chega; o app segue igual.
      }
    })();
  }, [livre]);

  // Registra com a sessão E a trava livres: com `lendo`/`travado` nenhum pedido sai antes do
  // desbloqueio (a abertura com sessão salva não faz rede até liberar). Ao liberar, a borda
  // de subida dispara o registro.
  const registravel = autenticado && livre;
  useEffect(() => {
    if (registravel) void registrarAparelho();
  }, [registravel]);
  useVoltaDoFundo(() => {
    if (registravel) void registrarAparelho();
  });

  // O hook segura a última resposta: cada uma vale uma vez só.
  const resposta = Notifications.useLastNotificationResponse();
  const ultima = useRef<string | null>(null);
  const [rota, setRota] = useState<Href | null>(null);
  useEffect(() => {
    if (!resposta) return;
    // Identificador + data: todo push do servidor leva `apns-collapse-id`, e no iOS o identificador de um
    // push remoto com collapse id é o próprio collapse id, igual em todos. Só o identificador descartaria o 2º toque.
    const id = `${resposta.notification.request.identifier}:${resposta.notification.date}`;
    if (ultima.current === id) return;
    ultima.current = id;
    Notifications.clearLastNotificationResponse();
    setRota(rotaDoToque(resposta));
  }, [resposta]);

  // Segura a rota até haver tela: sessão verificando, trava e navegação esperam;
  // sem sessão, descarta.
  useEffect(() => {
    if (!rota) return;
    if (fase === "anonimo") setRota(null);
    else if (autenticado && livre && navegacaoPronta) {
      setRota(null);
      router.navigate(rota);
    }
  }, [rota, fase, autenticado, livre, navegacaoPronta, router]);

  return null;
}
