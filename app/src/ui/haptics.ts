import { useEffect, useRef } from "react";
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

/**
 * Dispara `aviso()` só na TRANSIÇÃO falso→verdadeiro — nunca na montagem
 * (campo que já nasce com erro não vibra sozinho), nunca de um erro para
 * outro (trocar a mensagem não é um novo erro) e nunca a cada render (um
 * componente controlado rerenderiza a cada tecla). `AmountInput` usa este
 * hook; `Input` do C1 reusa o mesmo.
 */
export function useAvisoAoErrar(comErro: boolean): void {
  const anterior = useRef(comErro);
  useEffect(() => {
    if (comErro && !anterior.current) aviso();
    anterior.current = comErro;
  }, [comErro]);
}
