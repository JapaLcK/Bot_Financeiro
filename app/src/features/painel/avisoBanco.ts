import { useSyncExternalStore } from "react";

// Estado de MÓDULO, sem persistência: o painel remonta ao trocar de aba (useState reabriria o aviso),
// e o dono quer que ele só volte quando o processo do app for morto e reaberto.
let fechado = false;
const ouvintes = new Set<() => void>();

export function fecharAvisoBanco() {
  fechado = true;
  ouvintes.forEach((o) => o());
}

/** Só para teste: o estado de módulo vaza entre os testes do jest. */
export function __resetAvisoBanco() {
  fechado = false;
}

export function useAvisoBancoFechado() {
  return useSyncExternalStore(
    (o) => { ouvintes.add(o); return () => { ouvintes.delete(o); }; },
    () => fechado,
  );
}
