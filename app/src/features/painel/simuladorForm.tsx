import DateTimePicker, { DateTimePickerAndroid } from "@react-native-community/datetimepicker";
import { router } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Platform, View } from "react-native";
import type { z } from "zod";
import { SessaoExpirada } from "@/api/client";
import type * as S from "@/api/schemas/painel";
import { useSessao } from "@/features/auth/sessao";
import { simularCompra } from "@/services/painel";
import { AmountInput } from "@/ui/componentes/AmountInput";
import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";
import { useRolarSheetAoTopo } from "@/ui/componentes/Sheet";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";
import { Acao, Vazio } from "./base";
import { dataPtBr, hojeSP } from "./catalogo";
import { LinhaExpansivel } from "./expansao";
import { dataDoSeletor, isoDoSeletor } from "./metasEscrita";
import { CAMPOS_EXTRAS, VAZIO, corpoDe, falhaDe, type Cen, type Falha } from "./simuladorCompra";
import { SimuladorResultado } from "./simuladorResultado";
import { TextoPainel as Texto } from "./tipografia";

type Cenarios = Falha["cenarios"][number];
const fechar = () => router.back();
const erroEmExtras = (f: Cenarios | undefined) => !!f && CAMPOS_EXTRAS.some((k) => f[k]); // "Mais opções" abre sozinha quando um campo dela erra

/** Os campos de um cenário. `mudar` já ignora edição com o POST em voo (o corpo foi capturado: o que a tela mostrar agora não seria o calculado). */
function Cenario({ c, i, f, total, aberta, alternar, bloqueado, mudar, tirar }: { c: Cen; i: number; f: Cenarios; total: number; aberta: boolean; alternar: () => void; bloqueado: boolean; mudar: (p: Partial<Cen>) => void; tirar: () => void }) {
  const { esquema, cores } = useTema(), android = Platform.OS === "android", data = c.data ?? hojeSP();
  // Android: a API imperativa abre o diálogo uma vez, no toque (o componente reabriria a cada render).
  const abrirData = () => DateTimePickerAndroid.open({ value: dataDoSeletor(data), mode: "date", minimumDate: dataDoSeletor(hojeSP()), onChange: (e, d) => { if (e.type === "set" && d) mudar({ data: isoDoSeletor(d) }); } });
  return <View style={[{ gap: espaco.lg }, i > 0 && { borderTopWidth: 1, borderTopColor: cores.border, paddingTop: espaco.xl }]}>
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
      <Texto variante="rotulo" tom="inkMuted">Cenário {i + 1}</Texto>
      {total > 1 ? <Acao rotulo={`Tirar cenário ${i + 1}`} icone="Trash" somenteIcone desativado={bloqueado} onPress={tirar} /> : null}
    </View>
    <Input rotulo="Nome" value={c.nome} onChangeText={(nome) => mudar({ nome })} maxLength={60} placeholder="Ex.: Celular" erro={f.nome} desativado={bloqueado} />
    <AmountInput rotulo="Preço" centavos={c.preco} onChange={(preco) => mudar({ preco })} erro={f.preco} desativado={bloqueado} />
    <Input rotulo="Parcelas" value={c.parcelas} onChangeText={(parcelas) => mudar({ parcelas })} maxLength={3} keyboardType="number-pad" placeholder="À vista" erro={f.parcelas} desativado={bloqueado} />
    <GrupoDeLinhas>
      {android
        ? <ListRow titulo="Data da compra" trailing={<Texto tom="inkMuted">{dataPtBr(data)}</Texto>} chevron desativado={bloqueado} onPress={abrirData} />
        : <ListRow titulo="Data da compra" trailing={<DateTimePicker value={dataDoSeletor(data)} mode="date" display="compact" minimumDate={dataDoSeletor(hojeSP())} themeVariant={esquema} locale="pt-BR" accessibilityLabel="Data da compra" disabled={bloqueado} onChange={(_, d) => { if (d) mudar({ data: isoDoSeletor(d) }); }} />} />}
    </GrupoDeLinhas>
    {f.data_compra ? <Texto variante="legenda" tom="danger">{f.data_compra}</Texto> : null}
    <LinhaExpansivel linha={<Texto variante="rotulo">Mais opções</Texto>} aberta={aberta} alternar={alternar}>
      <View style={{ gap: espaco.lg, paddingTop: espaco.sm }}>
        <AmountInput rotulo="Entrada" centavos={c.entrada} onChange={(entrada) => mudar({ entrada })} erro={f.entrada} desativado={bloqueado} />
        <Input rotulo="Juros ao mês (%)" value={c.juros} onChangeText={(juros) => mudar({ juros })} keyboardType="decimal-pad" erro={f.juros_mensal_pct} desativado={bloqueado} />
        <AmountInput rotulo="Custos únicos" centavos={c.custos} onChange={(custos) => mudar({ custos })} erro={f.custos_unicos} desativado={bloqueado} />
        <AmountInput rotulo="Despesa mensal nova" centavos={c.despesa} onChange={(despesa) => mudar({ despesa })} erro={f.despesa_mensal_nova} desativado={bloqueado} />
      </View>
    </LinhaExpansivel>
    {f.cenario ? <Texto tom="danger">{f.cenario}</Texto> : null}
  </View>;
}

/** O corpo da folha: formulário → "Calcular" → resultado no lugar dele ("Editar" volta ao rascunho). Fechar descarta o rascunho. O POST só calcula: falha deixa o botão livre para tentar de novo. */
export function SimuladorCompraFolha() {
  const sessao = useSessao(), aoTopo = useRolarSheetAoTopo();
  const [cens, setCens] = useState<Cen[]>([VAZIO]), [reserva, setReserva] = useState(0), [abertas, setAbertas] = useState<boolean[]>([false]);
  const [falha, setFalha] = useState<Falha | null>(null), [pendente, setPendente] = useState(false), [res, setRes] = useState<z.output<typeof S.simuladorSchema> | null>(null);
  const vivo = useRef(true), voando = useRef(false), controle = useRef<AbortController | null>(null); // `voando` síncrona: dois toques no mesmo tick não passam pelo `pendente`
  useEffect(() => { vivo.current = true; return () => { vivo.current = false; controle.current?.abort(); }; }, []); // folha dispensada com o POST em voo: aborta (é só leitura) e não mexe em estado
  useEffect(() => { if (falha?.geral) AccessibilityInfo.announceForAccessibility(falha.geral); }, [falha]);
  const livre = (fn: () => void) => { if (voando.current) return; fn(); setFalha(null); };
  const mudar = (i: number, p: Partial<Cen>) => livre(() => setCens((x) => x.map((c, j) => (j === i ? { ...c, ...p } : c))));

  async function calcular() {
    if (voando.current) return;
    voando.current = true; setPendente(true); setFalha(null);
    const n = cens.length, pai = (controle.current = new AbortController());
    try { const r = await simularCompra(corpoDe(reserva, cens), pai); if (vivo.current) { setRes(r); aoTopo(); } } catch (e) { // `vivo` aqui e no catch: defesa em profundidade (setState em desmontado não avisa; sem teste barato do `setRes`)
      if (!vivo.current) return;
      if (e instanceof SessaoExpirada) return sessao.expirou(e.detalhe);
      const f = falhaDe(e, n);
      if (f.motivo === "superada" || f.motivo === "portao") return fechar(); // a releitura da aba cai no portão (402); a folha era de outra conta (409)
      setAbertas((a) => a.map((v, i) => v || erroEmExtras(f.cenarios[i])));
      setFalha(f); if (f.motivo === "plano") aoTopo(); // o convite troca o formulário inteiro, como o resultado; o 422 fica perto do campo
    } finally { voando.current = false; if (vivo.current) setPendente(false); }
  }

  return <View style={{ gap: espaco.xl }}>
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
      <Texto variante="secao" accessibilityRole="header" style={{ flex: 1 }}>Simular compra</Texto>
      <Acao rotulo="Fechar" icone="X" somenteIcone onPress={fechar} />
    </View>
    {falha?.motivo === "plano" ? <Vazio texto={falha.geral} /> : res ? <>
      <SimuladorResultado d={res} />
      <Button rotulo="Editar" variante="secondary" onPress={() => { setRes(null); setFalha(null); aoTopo(); }} />
    </> : <>
      {cens.map((c, i) => <Cenario key={i} c={c} i={i} f={falha?.cenarios[i] ?? {}} total={cens.length} aberta={abertas[i]!} alternar={() => setAbertas((a) => a.map((v, j) => (j === i ? !v : v)))} bloqueado={pendente} mudar={(p) => mudar(i, p)} tirar={() => livre(() => { setCens((x) => x.filter((_, j) => j !== i)); setAbertas((a) => a.filter((_, j) => j !== i)); })} />)}
      {cens.length < 3 ? <Acao rotulo="Comparar com outro cenário" texto="Outro cenário" icone="Plus" desativado={pendente} onPress={() => livre(() => { setCens((x) => [...x, VAZIO]); setAbertas((a) => [...a, false]); })} /> : null}
      <AmountInput rotulo="Reserva mínima" centavos={reserva} onChange={(v) => livre(() => setReserva(v))} erro={falha?.reserva} desativado={pendente} />
      {falha && !falha.cenarios.some((c) => Object.keys(c).length) && !falha.reserva ? <Texto tom="danger" accessibilityRole="alert">{falha.geral}</Texto> : null}
      <Button rotulo="Calcular" carregando={pendente} onPress={() => void calcular()} />
    </>}
  </View>;
}
