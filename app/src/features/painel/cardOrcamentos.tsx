import { View } from "react-native";
import type { z } from "zod";
import type { orcamentosCategoriaSchema } from "@/api/schemas/painel";
import { IconeCategoria } from "@/ui/componentes/IconeCategoria";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { reais } from "@/ui/componentes/CurvaAcumulada";
import { Linha, Valor, Vazio } from "./base";
import { TextoPainel as Texto } from "./tipografia";

type Orcamentos = z.output<typeof orcamentosCategoriaSchema>;
const VISIVEIS = 5;

/** O gasto total do mês contra o teto e as 5 categorias mais perto do teto (`usado_pct` é do servidor). A barra passa de 100% travada; o texto diz quanto estourou. Porta de `CardOrcamentos.tsx`. */
export function CardOrcamentos({ d }: { d: Orcamentos }) {
  const linhas = [...d.orcamentos].sort((a, b) => Number(b.usado_pct) - Number(a.usado_pct));
  if (!linhas.length) return <Vazio texto="Nenhum orçamento por categoria ainda." />;
  return <View style={{ gap: 12 }}>
    <View style={{ gap: 4 }}><Valor valor={d.total.gasto} destaque /><Texto variante="legenda" tom="inkMuted">{`de ${reais(d.total.teto)}`}</Texto></View>
    {linhas.slice(0, VISIVEIS).map((o) => <Linha key={o.categoria} titulo={o.categoria} valor={o.gasto} inicio={<IconeCategoria icone={o.icone} />}
      detalhe={o.restante < 0 ? `passou ${reais(-o.restante)} do teto de ${reais(o.teto)}` : `${Math.round(Number(o.usado_pct))}% do teto de ${reais(o.teto)}`}>
      <ProgressBar valor={Number(o.usado_pct) / 100} tom={o.status === "verde" ? "brand" : "warning"} />
    </Linha>)}
    {linhas.length > VISIVEIS && <Texto variante="legenda" tom="inkMuted">{`e mais ${linhas.length - VISIVEIS} orçamentos`}</Texto>}
  </View>;
}
