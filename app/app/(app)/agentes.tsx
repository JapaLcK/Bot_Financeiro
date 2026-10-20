import { AgentesTela } from "@/features/painel/agentesTela";
import { TipografiaPainelProvider } from "@/features/painel/tipografia";

/** Agentes (aberta pela tela Ferramentas): o estado de cada agente vem do `/agents/{uid}` do dono da sessão. Fora da `(painel)`: o `TipografiaPainelProvider` é próprio. */
export default function AgentesRota() {
  return <TipografiaPainelProvider><AgentesTela /></TipografiaPainelProvider>;
}
