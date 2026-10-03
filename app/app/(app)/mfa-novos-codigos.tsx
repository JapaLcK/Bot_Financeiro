import { Stack } from "expo-router";

import { NovosCodigos } from "@/features/seguranca/NovosCodigos";
import { usePrenderSheet } from "@/features/seguranca/prenderSheet";
import { SheetConteudo } from "@/ui/componentes/Sheet";

export default function MfaNovosCodigosSheet() {
  const { preso, prender, desprender, soltar } = usePrenderSheet();
  return (
    <SheetConteudo rolar>
      <Stack.Screen options={{ gestureEnabled: !preso }} />
      <NovosCodigos aoEnviar={prender} aoFalhar={desprender} aoConcluir={soltar} />
    </SheetConteudo>
  );
}
