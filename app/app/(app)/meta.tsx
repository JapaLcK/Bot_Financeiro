import { useLocalSearchParams } from "expo-router";

import { MetaFolha } from "@/features/painel/metaForm";
import { SheetConteudo } from "@/ui/componentes/Sheet";

/** Folha de meta: `?id=N` edita, `?tipo=reserva` monta a reserva, sem parâmetro cria uma meta. `id` presente e inválido (NaN) cai em "Essa meta não existe mais", nunca em "Nova meta". Rota interna (nenhum deep link externo chega aqui). */
export default function MetaSheet() {
  const { id, tipo } = useLocalSearchParams<{ id?: string; tipo?: string }>();
  return (
    <SheetConteudo>
      <MetaFolha id={id === undefined ? undefined : /^\d+$/.test(id) ? Number(id) : NaN} comecar={tipo === "reserva" ? "reserva" : undefined} />
    </SheetConteudo>
  );
}
