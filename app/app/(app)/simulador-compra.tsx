import { SimuladorCompraFolha } from "@/features/painel/simuladorForm";
import { TipografiaPainelProvider } from "@/features/painel/tipografia";
import { SheetConteudo } from "@/ui/componentes/Sheet";

/** Folha do simulador de compra (só Pro; aberta pelo `+` do bloco Simulador na aba Gastos). Fica fora da `(painel)`: o `TipografiaPainelProvider` é próprio. Rota interna (nenhum deep link externo chega aqui). */
export default function SimuladorCompraSheet() {
  return (
    <TipografiaPainelProvider>
      <SheetConteudo rolar>
        <SimuladorCompraFolha />
      </SheetConteudo>
    </TipografiaPainelProvider>
  );
}
