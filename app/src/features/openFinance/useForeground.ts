import { useEffect, useState } from "react";
import { AppState } from "react-native";

// O `inactive` (Central de Controle, notificação, ligação) é coberto pela tampa nativa e não desmonta nada;
// só o `background` (o iOS suspende o app e derruba a rede) pausa as leituras e fecha o portão (#897).
const emPrimeiroPlano = (s: string) => s === "active" || s === "inactive";

/** Suspensão não cancela o consentimento; somente pausa as leituras da tela. */
export function useForeground() {
  const [ativo, setAtivo] = useState(emPrimeiroPlano(AppState.currentState));
  useEffect(() => {
    const escuta = AppState.addEventListener("change", (s) => setAtivo(emPrimeiroPlano(s)));
    return () => escuta.remove();
  }, []);
  return ativo;
}
