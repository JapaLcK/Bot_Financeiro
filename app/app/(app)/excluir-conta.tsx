import { Stack } from "expo-router";

import { ExcluirConta } from "@/features/conta/ExcluirConta";
import { usePrenderSheet } from "@/features/seguranca/prenderSheet";
import { SheetConteudo } from "@/ui/componentes/Sheet";
import { TemaProvider } from "@/ui/tema";

export default function ExcluirContaSheet() {
  // Preso desde o envio do DELETE: no sucesso a sessão cai e o `Stack.Protected` tira a rota; na falha, `desprender`.
  const { preso, prender, desprender } = usePrenderSheet();
  return (
    <TemaProvider acesso>
      <SheetConteudo rolar>
        <Stack.Screen options={{ gestureEnabled: !preso }} />
        <ExcluirConta prender={prender} desprender={desprender} />
      </SheetConteudo>
    </TemaProvider>
  );
}
