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
/** Cena de cada agente (1200×600, a mesma de frontend/brand/agents/{kind}_hero.webp): a faixa da tela Agentes. */
export const CENA: Record<string, ImageSourcePropType> = {
  xerife: require("../../../assets/agentes/cenas/xerife.webp"),
  reporter: require("../../../assets/agentes/cenas/reporter.webp"),
  carteiro: require("../../../assets/agentes/cenas/carteiro.webp"),
  detetive: require("../../../assets/agentes/cenas/detetive.webp"),
  cofre: require("../../../assets/agentes/cenas/cofre.webp"),
  barao: require("../../../assets/agentes/cenas/barao.webp"),
  faria_limer: require("../../../assets/agentes/cenas/faria_limer.webp"),
};
