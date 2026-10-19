import { ExportarDados } from "@/features/conta/ExportarDados";
import { SheetConteudo } from "@/ui/componentes/Sheet";
import { TemaProvider } from "@/ui/tema";

export default function ExportarDadosSheet() {
  return (
    <TemaProvider acesso>
      <SheetConteudo rolar>
        <ExportarDados />
      </SheetConteudo>
    </TemaProvider>
  );
}
