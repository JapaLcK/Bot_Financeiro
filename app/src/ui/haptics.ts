import * as Haptics from "expo-haptics";

/**
 * Fire-and-forget: quem toca um chip não pode esperar o retorno do motor de
 * haptics para continuar, e simulador/web rejeitam a chamada — engolir aqui é
 * o que evita todo chamador precisar de try/catch para um retorno tátil.
 */
function disparar(promessa: Promise<unknown>): void {
  promessa.catch(() => {});
}

export function selecao(): void {
  disparar(Haptics.selectionAsync());
}

export function sucesso(): void {
  disparar(Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success));
}

export function aviso(): void {
  disparar(Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning));
}
