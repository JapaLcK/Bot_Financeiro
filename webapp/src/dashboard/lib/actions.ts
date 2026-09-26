import { createContext, useContext } from "react";
import { go, type Path } from "../router";
import { get, set, setFilter } from "./store.js";
import type { DashState } from "./types";

// O que os blocos escrevem e para onde navegam. Sem Provider (as páginas do painel) é a
// store global; na conversa do Piggy cada resposta fornece o seu (parts/LiveAnswer.tsx).
export interface Actions {
  get: () => DashState;
  set: (patch: Partial<DashState>) => void;
  setFilter: (patch: Partial<DashState["filter"]>) => void;
  go: (p: Path) => void;
}
export const ActionsContext = createContext<Actions>({ get, set, setFilter, go });
export const useActions = () => useContext(ActionsContext);
