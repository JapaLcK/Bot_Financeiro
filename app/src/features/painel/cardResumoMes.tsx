import { View } from "react-native";
import type { z } from "zod";
import type { resumoSchema } from "@/api/schemas/painel";
import { reais } from "@/ui/componentes/CurvaAcumulada";
import { Avisos, Linha } from "./base";
import { nomeMes } from "./catalogo";
import { Delta } from "./cardComparacao";
import { EntrouSaiu } from "./numerosMes";
import { TextoPainel as Texto } from "./tipografia";

type Resumo = z.output<typeof resumoSchema>;
const minus = (mes: string) => nomeMes(mes).split(" de ")[0]!;
const cap = (s: string) => s.replace(/^./, (c) => c.toUpperCase());
const nome = (mes: string) => cap(minus(mes));
/** O título do card, também o dos estados Carregando/erro: "Setembro × outubro de 2026" (anos diferentes: "Dezembro de 2025 × janeiro de 2026"). */
export const tituloMeses = ([a, b]: [string, string]) => a.slice(0, 4) === b.slice(0, 4) ? `${nome(a)} × ${minus(b)} de ${b.slice(0, 4)}` : `${cap(nomeMes(a))} × ${nomeMes(b)}`;

/** Entrou, Saiu e Sobrou do mês (`/api/app/resumo-do-mes`, a regra do `get_period_summary` de meses inteiros). `motivos`: os avisos que o AGENTE deu (o card do servidor), não os da rota. */
export function CardResumoMes({ d, motivos }: { d: Resumo; motivos: string[] }) {
  return <View style={{ gap: 12 }}>
    <EntrouSaiu entrou={d.entrou} saiu={d.saiu} />
    <Linha titulo="Sobrou" valor={d.entrou - d.saiu} />
    <Avisos motivos={motivos} />
  </View>;
}

/** Entrou, Saiu e Sobrou dos dois meses e a diferença (mais novo − mais antigo): o resumo-do-mes de cada um, a mesma conta do `compare_periods` por fatura. Subir Entrou/Sobrou é verde; subir Saiu é atenção. */
export function CardComparacaoMeses({ d, meses, motivos }: { d: [Resumo, Resumo]; meses: [string, string]; motivos: string[] }) {
  const [ant, nov] = d, linhas: [string, number, number, boolean][] = [["Entrou", ant.entrou, nov.entrou, true], ["Saiu", ant.saiu, nov.saiu, false], ["Sobrou", ant.entrou - ant.saiu, nov.entrou - nov.saiu, true]];
  return <View style={{ gap: 8 }}>
    {linhas.map(([rot, x, y, sobe]) => <View key={rot} style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 44 }}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Texto variante="rotulo">{rot}</Texto>
        <Texto variante="legenda" tom="inkMuted">{`${nome(meses[0])} ${reais(x)} · ${nome(meses[1])} ${reais(y)}`}</Texto>
      </View>
      <Delta centavos={y - x} oculto={false} sobeBem={sobe} />
    </View>)}
    <Avisos motivos={motivos} />
  </View>;
}
