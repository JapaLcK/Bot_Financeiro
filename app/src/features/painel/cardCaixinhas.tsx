import { View } from "react-native";
import type { z } from "zod";
import type { caixinhasV2Schema } from "@/api/schemas/painel";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { reais } from "@/ui/componentes/CurvaAcumulada";
import { Linha, Valor, Vazio } from "./base";
import { TextoPainel as Texto } from "./tipografia";

type Caixinhas = z.output<typeof caixinhasV2Schema>;
const VISIVEIS = 4;

/** O total guardado e as 4 caixinhas de maior saldo (a ordem é a do servidor), com a barra de quem tem meta. Sem a meta batida, nunca "100%": arredonda para baixo (99,96% é 99%). Lê `/api/app/caixinhas` (v2), não o legado do chat. Porta de `CardCaixinhas.tsx`. */
export function CardCaixinhas({ d }: { d: Caixinhas }) {
  if (!d.caixinhas.length) return <Vazio texto="Nenhuma caixinha ainda." />;
  const resto = d.caixinhas.length - VISIVEIS;
  return <View style={{ gap: 12 }}>
    <Valor valor={d.total} destaque />
    {d.caixinhas.slice(0, VISIVEIS).map((c) => {
      const p = c.meta !== null && c.progresso_pct !== null ? Math.max(0, Math.min(100, Number(c.progresso_pct))) : null;
      const n = p === null ? null : c.atingida ? Math.round(p) : Math.min(99, Math.floor(p));
      return <Linha key={c.id} titulo={c.nome} valor={c.saldo} detalhe={n === null ? undefined : `${c.atingida ? "Meta batida" : `${n}%`} · meta ${reais(c.meta!)}`}>
        {n !== null && <ProgressBar valor={n / 100} tom={c.atingida ? "positive" : "brand"} />}
      </Linha>;
    })}
    {resto > 0 && <Texto variante="legenda" tom="inkMuted">{`e mais ${resto} ${resto === 1 ? "caixinha" : "caixinhas"}`}</Texto>}
  </View>;
}
