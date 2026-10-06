import { useEffect, useState } from "react";
import { AppState } from "react-native";

/** Suspensão não cancela o consentimento; somente pausa as leituras da tela. */
export function useForeground() {
  const [ativo, setAtivo] = useState(AppState.currentState === "active");
  useEffect(() => {
    const escuta = AppState.addEventListener("change", (s) => setAtivo(s === "active"));
    return () => escuta.remove();
  }, []);
  return ativo;
}
