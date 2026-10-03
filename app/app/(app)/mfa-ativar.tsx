import { router, Stack } from "expo-router";

import { AtivarMfa } from "@/features/seguranca/AtivarMfa";
import { usePrenderSheet } from "@/features/seguranca/prenderSheet";
import { SheetConteudo } from "@/ui/componentes/Sheet";

export default function MfaAtivarSheet() {
  const { preso, prender, desprender, soltar } = usePrenderSheet();
  return (
    <SheetConteudo rolar>
      <Stack.Screen options={{ gestureEnabled: !preso }} />
      <AtivarMfa aoEnviar={prender} aoFalhar={desprender} aoConcluir={soltar} aoGerarNovos={() => router.replace("/mfa-novos-codigos")} />
    </SheetConteudo>
  );
}
