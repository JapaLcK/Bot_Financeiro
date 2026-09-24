import { DesativarMfa } from "@/features/seguranca/DesativarMfa";
import { usePrenderSheet } from "@/features/seguranca/prenderSheet";
import { SheetConteudo } from "@/ui/componentes/Sheet";

export default function MfaDesativarSheet() {
  // Nada a prender aqui; o `soltar` é só pelo `router.back()` num efeito: se a
  // sheet já fechou (arrasto, voltar) com o disable em voo, a resposta chega a
  // um componente desmontado e não volta de novo a partir da Segurança.
  const { soltar } = usePrenderSheet();
  return (
    <SheetConteudo rolar>
      <DesativarMfa aoConcluir={soltar} />
    </SheetConteudo>
  );
}
