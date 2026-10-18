import { Pressable, View } from "react-native";

import { percentualDe } from "@/features/painel/decimal";
import { BarraComposicao } from "@/ui/componentes/BarraComposicao";
import { Card } from "@/ui/componentes/Card";
import { Icone } from "@/ui/componentes/Icone";
import { Money } from "@/ui/componentes/Money";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

import type { Panorama } from "./juntar";

export type Parte = { nome: string; valor: number; cor: string };
export type Vagas = { max: number | null; emUso: number };
/** Até aqui desenho um quadradinho por vaga; acima, só o "2/9" (uma fileira de 9 não cabe na linha). */
const MAX_QUADRADOS = 6;

interface Props {
  /** `false`: `/api/app/bancos` não veio (403, 404, 5xx, rede, schema): a tela fica "como antes", sem número nenhum. */
  numeros: boolean;
  /** O `total` do SERVIDOR, tal como veio. `null` = não dá para saber ("—", nunca R$ 0,00). O app não soma. */
  total: number | null;
  /** Os bancos listados que têm valor (> 0), para a barra e a legenda. */
  partes: Parte[];
  panorama: Panorama;
  vagas: Vagas;
  oculto: boolean;
  aoConferir: () => void;
}

/**
 * A barra só mostra a PROPORÇÃO entre as partes que vieram (a legenda diz quanto cada uma pesa DENTRO delas). O valor grande é o `total` do servidor:
 * pode incluir banco que a lista não mostra, e por isso nenhuma conta do app o reproduz. Com 1 parte só ou com "ocultar valores" não há composição.
 */
export function ResumoBancos({ numeros, total, partes, panorama, vagas, oculto, aoConferir }: Props) {
  const { cores } = useTema();
  const barra = numeros && !oculto && partes.length >= 2;
  const soma = partes.reduce((s, p) => s + p.valor, 0); // só para % da legenda; nenhum valor em R$ sai desta soma
  return (
    <Card>
      <View style={{ gap: espaco.sm }}>
        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
          <Texto variante="rotulo" tom="inkMuted">Nos seus bancos</Texto>
          <Pressable accessibilityRole="button" accessibilityLabel="Conferir de novo" onPress={aoConferir} style={{ width: 44, height: 44, marginVertical: -espaco.sm, marginRight: -espaco.md, alignItems: "center", justifyContent: "center" }}>
            <Icone nome="ArrowsClockwise" tamanho={20} tom="inkMuted" />
          </Pressable>
        </View>
        {numeros ? <Money centavos={total ?? Number.NaN} oculto={oculto} variante="display" /> : null}
        {barra ? (
          <View style={{ gap: espaco.sm }}>
            <BarraComposicao segmentos={partes.map((p) => ({ valor: p.valor, cor: p.cor }))} />
            <View style={{ flexDirection: "row", flexWrap: "wrap", columnGap: espaco.lg, rowGap: espaco.xs }}>
              {partes.map((p, i) => (
                <View key={i} style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm }}>
                  <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: p.cor }} />
                  <Texto variante="legenda" tom="inkMuted">{p.nome} <Texto variante="legenda" tom="inkFaint">{percentualDe(p.valor, soma)}%</Texto></Texto>
                </View>
              ))}
            </View>
          </View>
        ) : null}
        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", columnGap: espaco.md, rowGap: espaco.sm, marginTop: espaco.sm, paddingTop: espaco.md, borderTopWidth: 1, borderTopColor: cores.border }}>
          {panorama.acoes > 0 ? (
            <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.xs }}>
              <Icone nome="WarningCircle" tamanho={16} tom="warning" />
              <Texto variante="rotulo" tom="warning">{panorama.acoes === 1 ? "1 pede ação" : `${panorama.acoes} pedem ação`}</Texto>
            </View>
          ) : panorama.tudoEmDia ? (
            <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.xs }}>
              <Icone nome="CheckCircle" tamanho={16} tom="positive" />
              <Texto variante="rotulo" tom="positive">Tudo em dia</Texto>
            </View>
          ) : <View />}
          {vagas.max ? <Vaga vagas={vagas} /> : null}
        </View>
      </View>
    </Card>
  );
}

function Vaga({ vagas }: { vagas: Vagas }) {
  const { cores } = useTema();
  const quadrados = vagas.max !== null && vagas.max <= MAX_QUADRADOS ? vagas.max : 0;
  return (
    <View accessible accessibilityLabel={`${vagas.emUso} de ${vagas.max} bancos`} style={{ flexDirection: "row", alignItems: "center", gap: espaco.xs }}>
      {Array.from({ length: quadrados }, (_, i) => {
        const ocupada = i < vagas.emUso;
        return (
          <View key={i} style={{ width: 28, height: 28, borderRadius: raio.controle, alignItems: "center", justifyContent: "center", backgroundColor: ocupada ? cores.brandSoft : cores.surfaceControl }}>
            <Icone nome="Bank" tamanho={16} tom={ocupada ? "brandInk" : "inkFaint"} />
          </View>
        );
      })}
      <Texto variante="rotulo" tom="inkMuted" numerico>{vagas.emUso}/{vagas.max}</Texto>
    </View>
  );
}
