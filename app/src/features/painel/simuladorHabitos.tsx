import { useEffect, useRef, useState } from "react";
import { View } from "react-native";
import Slider from "@react-native-community/slider";
import type { z } from "zod";
import type * as S from "@/api/schemas/painel";
import { ErroDeApi } from "@/api/client";
import { simularHabitos } from "@/services/painel";
import { selecao } from "@/ui/haptics";
import { useTema } from "@/ui/tema";
import { CORES_CATEGORIA } from "@/ui/tokens";
import { Acao, Curva, RecursoDados, Valor, Vazio } from "./base";
import { useBloco } from "./blocoContexto";
import { usePainel } from "./provider";
import { codigoDe } from "./metasEscrita";
import { GENERICO, SEM_PLANO } from "./simuladorCompra";
import { Marca } from "./simuladorResultado";
import { usePrivacidade } from "./privacidade";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";

type Res = z.output<typeof S.simuladorHabitosSchema>;
const MUDOU = "Seus gastos do dia a dia mudaram. Ajuste de novo.";
// Só chave própria: uma categoria chamada "constructor" ou "toString" não pode ler o protótipo do objeto.
const de = (m: Record<string, number>, k: string) => (Object.prototype.hasOwnProperty.call(m, k) ? m[k]! : 0);
const passo = (v: number) => Math.min(100, Math.max(0, Math.round(v / 10) * 10)); // o slider nativo entrega qualquer número: o passo é 10 e o corpo só leva inteiro
const mudouDeCategoria = (e: unknown) => e instanceof ErroDeApi && e.status === 422 && ((e.corpo as { error?: { details?: { type?: unknown }[] } } | null | undefined)?.error?.details ?? []).some((d) => d?.type === "categoria_fora_da_estimativa");

/**
 * Corte de hábito: a base é a estimativa (mediana) da previsão, nunca o mês. O arraste só mexe no visual; ao soltar vai UM POST
 * (só cálculo) e a tela mostra o que o servidor devolveu: nenhuma conta de dinheiro aqui. Slider nativo (UISlider): ajustável no VoiceOver.
 */
export function SimuladorHabitos() {
  const p = usePainel(), bloco = useBloco(), { cores } = useTema(), { ampliado } = useTipografiaPainel(), { oculto } = usePrivacidade();
  // O estado mora FORA do `RecursoDados`: a releitura da previsão (422) o desmonta por um instante e a mensagem não pode ir junto.
  const [cortes, setCortes] = useState<Record<string, number>>({}), [res, setRes] = useState<Res | null>(null), [falha, setFalha] = useState<string | null>(null);
  const op = useRef<ReturnType<typeof p.operacao> | null>(null);
  const arraste = useRef<{ chave: string; v: number } | null>(null); // o passo do arraste em curso: o tique sai a cada passo novo, nunca 2x no mesmo (o render atrasa o estado)
  useEffect(() => () => op.current?.controlador.abort(), []);
  const limpar = () => { op.current?.controlador.abort(); op.current = null; arraste.current = null; setCortes({}); setRes(null); };
  const zerar = () => { limpar(); setFalha(null); };
  // Previsão relida com outra versão (pull-to-refresh, foco, 422): corte e resultado eram da estimativa antiga. Só a falha do 422 fica. No chat o bloco é congelado.
  const versao = useRef<string | null>(null);
  const r = p.dados.previsao, v = r?.fase === "pronto" ? r.dado.calculado_em : null;
  useEffect(() => { if (v === null || bloco) return; if (versao.current !== null && versao.current !== v) { limpar(); setFalha((f) => (f === MUDOU ? f : null)); } versao.current = v; }, [v, bloco]);
  async function soltou(categorias: { chave: string }[], chave: string, v: number) {
    const novo = { ...cortes, [chave]: v };
    const lista = categorias.filter((c) => de(novo, c.chave) > 0).map((c) => ({ categoria: c.chave, percentual: de(novo, c.chave) }));
    setCortes(novo); setFalha(null);
    op.current?.controlador.abort(); // o 2º toque cancela o 1º: a resposta velha nunca chega à tela
    if (!lista.length) { op.current = null; setRes(null); return; }
    const mine = (op.current = p.operacao()); setRes(null); // número velho nunca fica ao lado do corte novo enquanto o POST está no ar
    try { const r = await simularHabitos({ cortes: lista }, mine.controlador); if (mine.atual()) setRes(r); }
    catch (e) {
      if (!mine.atual()) return;
      setRes(null); p.falhou(e); // sessão, 402 e onboarding vão ao portão; erro nunca fica ao lado de número velho
      if (codigoDe(e) === "pro_required") setFalha(SEM_PLANO);
      else if (mudouDeCategoria(e)) { setFalha(MUDOU); if (!bloco) p.relerPrevisao(); }
      else setFalha(GENERICO);
    }
  }
  return <RecursoDados nome="previsao">{(prev) => {
    const categorias = prev.estimativa_variavel?.categorias;
    if (!categorias) return <Vazio texto="Ainda sem histórico para estimar." />;
    return <View style={{ gap: 8 }}>
      {categorias.map((c, i) => {
        const corte = de(cortes, c.chave), linha = res?.cortes.find((x) => x.chave === c.chave);
        return <View key={c.chave}>
          <View style={{ flexDirection: "row", flexWrap: ampliado ? "wrap" : "nowrap", justifyContent: "space-between", alignItems: "center", columnGap: 8 }}>
            <Texto variante="rotulo" numberOfLines={ampliado ? undefined : 1} style={{ flexShrink: 1 }}>{c.nome}</Texto>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 4, flexShrink: 0 }}>
              <Texto variante="rotulo" tom="inkMuted">{corte}%</Texto>
              {linha && corte > 0 && linha.percentual === corte ? <><Texto variante="rotulo" tom="inkMuted">·</Texto><Valor valor={linha.economia_mensal} tipo="entrada" pequeno /></> : null}
            </View>
          </View>
          <Slider accessibilityLabel={`Corte em ${c.nome}`} style={{ height: 40 }} minimumValue={0} maximumValue={100} step={10} value={corte}
            minimumTrackTintColor={CORES_CATEGORIA[i % CORES_CATEGORIA.length]!} maximumTrackTintColor={cores.surfaceRaised}
            onValueChange={(v) => { if (!Number.isFinite(v)) return; const n = passo(v), antes = arraste.current?.chave === c.chave ? arraste.current.v : corte; if (n !== antes) selecao(); arraste.current = { chave: c.chave, v: n }; setCortes((s) => ({ ...s, [c.chave]: n })); }}
            onSlidingComplete={(v) => { arraste.current = null; void soltou(categorias, c.chave, passo(v)); }} />
        </View>;
      })}
      {falha ? <Texto variante="rotulo" tom="warning" accessibilityRole="alert">{falha}</Texto> : null}
      {res && !falha ? <>
        <Texto variante="legenda" tom="inkMuted">Sobra por mês</Texto>
        <Valor valor={res.economia_mensal} tipo="entrada" destaque />
        {res.estado === "indisponivel" ? null : <>
          <Curva valores={res.com_corte.trajetoria.map((x) => x.saldo)} outra={res.atual.trajetoria.map((x) => x.saldo)} />
          {oculto ? null : <View style={{ flexDirection: "row", gap: 16 }}><Marca nome="Com corte" /><Marca nome="Sem corte" tracejada /></View>}
        </>}
      </> : null}
      {res || Object.values(cortes).some((v) => v > 0) ? <Acao rotulo="Zerar simulação" onPress={zerar} /> : null}
    </View>;
  }}</RecursoDados>;
}
