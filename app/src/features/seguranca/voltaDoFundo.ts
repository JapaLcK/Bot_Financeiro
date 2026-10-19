import { useEffect, useRef } from "react";
import { AppState } from "react-native";

/**
 * O link do e-mail e o do WhatsApp abrem outro app: voltar é AppState, não foco de navegação, e o
 * `useFocusEffect` da tela não dispara. Chama `aoVoltar` na volta REAL (`active` depois de
 * `background`): no iOS a volta passa por `inactive` com a rede ainda acordando, e `inactive`
 * sozinho (Central de Controle) não é saída do app. A tela segue como está até a resposta chegar.
 */
export function useVoltaDoFundo(aoVoltar: () => void) {
  const ultimo = useRef(aoVoltar);
  ultimo.current = aoVoltar;
  useEffect(() => {
    let foiParaFundo = false;
    const escuta = AppState.addEventListener("change", (v) => {
      if (v === "background") foiParaFundo = true;
      else if (v === "active" && foiParaFundo) {
        foiParaFundo = false;
        ultimo.current();
      }
    });
    return () => escuta.remove();
  }, []);
}
