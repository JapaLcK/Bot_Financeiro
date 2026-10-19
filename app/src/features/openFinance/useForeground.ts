import { useEffect, useState } from "react";
import { AppState } from "react-native";

// O `inactive` (Central de Controle, notificação, ligação) é coberto pela tampa nativa e não desmonta nada;
// só o `background` (o iOS suspende o app e derruba a rede) pausa as leituras. A tela fica, e a volta sem trava (de qualquer duração; o servidor decide por 401) revalida em silêncio; só a trava biométrica fecha o portão.
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
