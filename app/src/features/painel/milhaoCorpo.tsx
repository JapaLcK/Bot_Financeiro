import { useMemo, useState } from "react";
import { View } from "react-native";
import Slider from "@react-native-community/slider";
import type { z } from "zod";
import type * as S from "@/api/schemas/painel";
import { partes } from "@/ui/dinheiro";
import { Money } from "@/ui/componentes/Money";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { Selo } from "@/ui/componentes/Selo";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";
import { Acao, Curva } from "./base";
import { mesAtual, nomeMes } from "./catalogo";
import { duracao, projetar } from "./milhao";
import { MOTIVO, MOTIVO_PATRIMONIO, ou } from "./previsaoMotivos";
import { usePrivacidade } from "./privacidade";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";

const APORTE = { min: 0, max: 10000, passo: 50, padrao: 0 }; // ponytail: teto declarado de R$ 10 mil por mês (igual ao web)
const TAXA = { min: 0, max: 15, passo: 0.5, padrao: 10 };
const ROTULO = { ...MOTIVO, ...MOTIVO_PATRIMONIO };
const virgula = (n: number) => String(n).replace(".", ",");

/** O corpo do Progresso ao milhão: o patrimônio vem do servidor, a conta de juros é de `milhao.ts` e os dois controles não gravam nada. */
export function MilhaoCorpo({ d }: { d: z.output<typeof S.progressoMilhaoSchema> }) {
  const { oculto } = usePrivacidade(), { cores } = useTema(), { fontScale } = useTipografiaPainel();
  const [aporte, setAporte] = useState(APORTE.padrao), [taxa, setTaxa] = useState(TAXA.padrao);
  const inicio = mesAtual(); // fora do memo: a tela aberta na virada do mês recalcula no próximo render (ex.: puxar para atualizar)
  const p = useMemo(() => projetar({ patrimonio: d.patrimonio / 100, aporte, taxaAnual: taxa, inicio }), [d.patrimonio, aporte, taxa, inicio]);
  const passou = p.meta > 1e6, alvo = passou ? `R$ ${new Intl.NumberFormat("pt-BR").format(p.meta / 1e6)} milhões` : "R$ 1 milhão", padrao = aporte === APORTE.padrao && taxa === TAXA.padrao;
  const trilha = { minimumTrackTintColor: cores.brand, maximumTrackTintColor: cores.surfaceRaised, style: { height: 40 } };
  return <View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
    <View style={{ gap: 4 }}>
      {passou ? <Texto variante="rotulo">Você já passou do milhão</Texto> : null}
      <Texto variante="legenda" tom="inkMuted">Faltam</Texto>
      <Money key={fontScale} centavos={Math.ceil(p.faltaCentavos / 100) * 100} variante="display" oculto={oculto} />
    </View>
    {oculto ? null : <ProgressBar valor={p.pct / 100} />}
    <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", columnGap: 8 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 4, flexShrink: 1 }}>
        <Texto variante="legenda" tom="inkMuted">Hoje</Texto>
        <Money key={fontScale} centavos={d.patrimonio} variante="rotulo" oculto={oculto} />
        {oculto ? null : <Texto variante="legenda" tom="inkMuted">· {p.pct}%</Texto>}
      </View>
      <Texto variante="legenda" tom="inkMuted">{alvo}</Texto>
    </View>
    {d.motivos.length ? <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>{d.motivos.map((m) => <Selo key={m} texto={ou(ROTULO, m)} />)}</View> : null}
    <View style={{ gap: 4 }}>
      {p.chegada ? <>
        <Texto variante="legenda" tom="inkMuted">{passou ? `Você chega aos ${alvo} em` : "Você chega em"}</Texto>
        <Texto variante="secao">{nomeMes(p.chegada)}</Texto>
        <Texto variante="legenda" tom="inkMuted">Daqui a {duracao(p.anos, p.mesesResto)}</Texto>
      </> : <>
        <Texto variante="secao">Sem previsão</Texto>
        <Texto variante="legenda" tom="inkMuted">Arraste o Guardar por mês para ver quando você chega.</Texto>
      </>}
    </View>
    <Curva valores={p.pontos} outra={p.pontos.map(() => p.meta)} />
    <View>
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
        <Texto variante="rotulo">Guardar por mês</Texto>
        <Money key={fontScale} centavos={aporte * 100} variante="rotulo" />
      </View>
      {/* Arredondar ao passo ANTES do estado: o slider nativo entrega qualquer número. */}
      <Slider accessibilityLabel="Guardar por mês" accessibilityValue={{ text: `R$ ${partes(aporte * 100)!.inteiro} por mês` }} {...trilha} minimumValue={APORTE.min} maximumValue={APORTE.max} step={APORTE.passo} value={aporte} onValueChange={(v) => setAporte(Math.round(v / APORTE.passo) * APORTE.passo)} />
    </View>
    <View>
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
        <Texto variante="rotulo">Rendimento ao ano</Texto>
        <Texto variante="rotulo">{virgula(taxa)}%</Texto>
      </View>
      <Slider accessibilityLabel="Rendimento ao ano" accessibilityValue={{ text: `${virgula(taxa)}% ao ano` }} {...trilha} minimumValue={TAXA.min} maximumValue={TAXA.max} step={TAXA.passo} value={taxa} onValueChange={(v) => setTaxa(Math.round(v / TAXA.passo) * TAXA.passo)} />
    </View>
    <Acao rotulo="Voltar ao padrão" desativado={padrao} onPress={() => { setAporte(APORTE.padrao); setTaxa(TAXA.padrao); }} />
    <Texto variante="legenda" tom="inkMuted">Conta de juros compostos com taxa fixa: não é promessa de rendimento. Patrimônio = Carteira, contas, caixinhas e investimentos.</Texto>
  </View>;
}
