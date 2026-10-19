import type { ImageSourcePropType } from "react-native";
/** Retrato de cada agente (96×96, o mesmo recorte do /painel); `tests` conferem que todo kind do catálogo tem o seu. */
export const AVATAR: Record<string, ImageSourcePropType> = {
  xerife: require("../../../assets/agentes/xerife.webp"),
  reporter: require("../../../assets/agentes/reporter.webp"),
  carteiro: require("../../../assets/agentes/carteiro.webp"),
  detetive: require("../../../assets/agentes/detetive.webp"),
  cofre: require("../../../assets/agentes/cofre.webp"),
  barao: require("../../../assets/agentes/barao.webp"),
  faria_limer: require("../../../assets/agentes/faria_limer.webp"),
};
