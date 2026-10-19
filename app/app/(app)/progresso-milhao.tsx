import { ProgressoMilhao } from "@/features/painel/progressoMilhao";
import { TipografiaPainelProvider } from "@/features/painel/tipografia";

/** Progresso ao milhão (aberta pela tela Ferramentas, todos os planos: o servidor decide o convite). Fora da `(painel)`: o `TipografiaPainelProvider` é próprio. */
export default function ProgressoMilhaoTela() {
  return <TipografiaPainelProvider><ProgressoMilhao /></TipografiaPainelProvider>;
}
