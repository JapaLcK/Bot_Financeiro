import DateTimePicker, { DateTimePickerAndroid } from "@react-native-community/datetimepicker";
import { router } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, ActivityIndicator, Alert, Platform, View } from "react-native";
import type { z } from "zod";
import { SessaoExpirada } from "@/api/client";
import * as S from "@/api/schemas/painel";
import { useSessao } from "@/features/auth/sessao";
import { apagarMeta, criarMeta, editarMeta, lerRecurso } from "@/services/painel";
import { AmountInput } from "@/ui/componentes/AmountInput";
import { Button } from "@/ui/componentes/Button";
import { Icone } from "@/ui/componentes/Icone";
import { Input } from "@/ui/componentes/Input";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";
import { Money } from "@/ui/componentes/Money";
import { SegmentedControl } from "@/ui/componentes/SegmentedControl";
import { Texto } from "@/ui/componentes/Texto";
import { sucesso } from "@/ui/haptics";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";
import { Acao } from "./base";
import { dataPtBr, hojeSP } from "./catalogo";
import { CONTA_A_ESCOLHER, INVESTIDO, MESES_MAX, MESES_MIN, NOME_MAX, NOME_RESERVA, chaveFonte, codigoDe, corpoCriar, corpoEditar, dataDoSeletor, falhaDaEscrita, inicialDe, isoDoSeletor, metasMudaram, validar, type Campos, type Falha, type Fonte } from "./metasEscrita";
import { Blocos } from "./metasChat";
import { MESES_PADRAO } from "./metasTextos";

type Conta = z.output<typeof S.contasSchema>["contas"][number];
type Carga = { fase: "carregando" } | { fase: "erro" } | { fase: "pronto"; metas: S.MetasV2; bancos: string[]; contas: Conta[]; semPlano: boolean };
const fecharFolha = () => router.back();

/**
 * Lê o que a folha precisa (metas, bancos e contas), sozinha: a rota fica fora do `PainelProvider`. Só a lista de metas é essencial — e `/contas` quando o `/investido` deu 403 `pro_required`
 * ("sem o plano": contas são a única fonte, sem elas o formulário não salva nada); fora isso, a falha de `/investido` ou de `/contas` só deixa a lista menor. ponytail: o formulário abre quando as três terminam (15 s de teto cada), sem
 * abrir antes e reajustar o padrão da fonte depois.
 */
function useCarga(tentativa: number): Carga {
  const [carga, setCarga] = useState<Carga>({ fase: "carregando" }); const sessao = useSessao();
  useEffect(() => {
    const pai = new AbortController(); setCarga({ fase: "carregando" });
    const vivo = <T,>(p: Promise<T>) => p.catch((e: unknown) => { if (e instanceof SessaoExpirada) sessao.expirou(e.detalhe); throw e; });
    void Promise.allSettled([vivo(lerRecurso("/api/app/metas", S.metasV2Schema, pai)), vivo(lerRecurso("/api/app/investido", S.investidoBancosSchema, pai)), vivo(lerRecurso("/api/app/contas", S.contasSchema, pai))]).then(([m, i, c]) => {
      if (pai.signal.aborted) return;
      const semPlano = i.status === "rejected" && codigoDe(i.reason) === "pro_required";
      if (m.status !== "fulfilled" || (semPlano && c.status !== "fulfilled")) return setCarga({ fase: "erro" });
      setCarga({ fase: "pronto", metas: m.value, bancos: i.status === "fulfilled" ? i.value.por_banco.map((b) => b.banco) : [], semPlano, contas: c.status === "fulfilled" ? c.value.contas.filter((k) => k.moeda === "BRL") : [] });
    });
    return () => pai.abort();
  }, [tentativa]); // `sessao.expirou` é estável
  return carga;
}

/** O corpo da folha de meta: nova (`id` ausente), reserva (`comecar`) ou edição (`id`). */
export function MetaFolha({ id, comecar }: { id?: number; comecar?: "reserva" }) {
  const [tentativa, setTentativa] = useState(0), carga = useCarga(tentativa), meta = carga.fase === "pronto" && id !== undefined ? carga.metas.metas.find((m) => m.id === id) : undefined;
  if (carga.fase === "carregando") return <View style={{ minHeight: 160, alignItems: "center", justifyContent: "center" }}><ActivityIndicator accessibilityLabel="Carregando" /></View>;
  if (carga.fase === "erro") return <View style={{ gap: espaco.lg }}><Texto tom="danger">Não deu para carregar.</Texto><Button rotulo="Tentar de novo" variante="secondary" onPress={() => setTentativa((t) => t + 1)} /></View>;
  if (id !== undefined && !meta) return <View style={{ gap: espaco.lg }}><Texto tom="danger">Essa meta não existe mais.</Texto><Button rotulo="Fechar" variante="secondary" onPress={fecharFolha} /></View>;
  return <Formulario meta={meta} comecar={comecar} custo={carga.metas.custo_mensal} temReserva={carga.metas.metas.some((m) => m.tipo === "reserva")} bancos={carga.bancos} contas={carga.contas} semPlano={carga.semPlano} />;
}

type Opcao = { titulo: string; sub: string | undefined; fonte: Fonte };
function Formulario({ meta, comecar, custo, temReserva, bancos, contas, semPlano: semPlanoInicial }: { meta?: S.MetasV2["metas"][number]; comecar?: "reserva"; custo: S.MetasV2["custo_mensal"]; temReserva: boolean; bancos: string[]; contas: Conta[]; semPlano: boolean }) {
  const { esquema } = useTema(), sessao = useSessao(), novo = !meta;
  const inicial = useRef<Campos>(meta ? inicialDe(meta) : { nome: comecar ? NOME_RESERVA : "", tipo: comecar ?? "meta", centavos: 0, meses: MESES_PADRAO, data: null, fonte: semPlanoInicial ? CONTA_A_ESCOLHER : INVESTIDO }).current;
  const [c, setC] = useState<Campos>(inicial), [semPlano, setSemPlano] = useState(semPlanoInicial), [reservaExiste, setReservaExiste] = useState(temReserva);
  const [falha, setFalha] = useState<Falha | null>(null), [pendente, setPendente] = useState(false), [fonteAberta, setFonteAberta] = useState(false);
  const vivo = useRef(true); useEffect(() => { vivo.current = true; return () => { vivo.current = false; }; }, []); // a folha foi dispensada (deslizar) com o POST em voo? Aí ela não fecha a folha de OUTRA tela
  const voando = useRef(false); // síncrona: dois toques no mesmo tick não passam pelo `pendente` (estado só vale no render seguinte)
  useEffect(() => { if (falha?.geral) AccessibilityInfo.announceForAccessibility(falha.geral); }, [falha]);
  const perdida = falha?.motivo === "perdida", bloqueado = pendente || perdida; // resultado desconhecido: o formulário trava e só sobra "Fechar" (outro Salvar duplicaria a meta)
  const mudar = (p: Partial<Campos>) => { if (perdida || voando.current) return; setC((x) => ({ ...x, ...p })); setFalha(null); }; // `voando`: o corpo do POST já foi capturado, mudar a tela agora mostraria o que não vai ser gravado
  const reserva = c.tipo === "reserva", mudou = novo || corpoEditar(meta.id, c, inicial) !== null;

  /** Uma escrita por vez. Sucesso mantém a trava até a folha sair (um 2º toque na animação de fechar não grava de novo); falha a solta, sem reenviar sozinha. */
  async function enviar(escrever: (pai: AbortController) => Promise<unknown>, apagando = false) {
    if (voando.current) return;
    voando.current = true; setPendente(true); setFalha(null);
    try { await escrever(new AbortController()); sucesso(); if (vivo.current) fecharFolha(); else metasMudaram(); return; } catch (e) {
      voando.current = false; setPendente(false);
      if (e instanceof SessaoExpirada) return sessao.expirou(e.detalhe);
      if (!vivo.current) return metasMudaram(); // dispensada: nada a mostrar nem a fechar; o resultado pode ter mudado a lista
      const f = falhaDaEscrita(e, apagando);
      if (f.motivo === "portao") return fecharFolha(); // a releitura da aba, ao focar, cai no portão (402)
      if (f.motivo === "sumiu") return apagando ? fecharFolha() : Alert.alert(f.geral, undefined, [{ text: "OK", onPress: fecharFolha }]); // apagar o que já se foi é o resultado pedido
      if (f.motivo === "reserva_ja_existe") { setReservaExiste(true); setC((x) => ({ ...x, tipo: "meta" })); } // o rascunho fica, como Meta
      if (f.motivo === "plano") { setSemPlano(true); setC((x) => (x.fonte.tipo === "conta" ? x : { ...x, fonte: CONTA_A_ESCOLHER })); }
      setFalha(f);
    }
  }
  function salvar() {
    const campos = validar(c, novo ? null : inicial);
    if (Object.keys(campos).length) return setFalha({ geral: "Confira os campos.", campos, motivo: "campos" });
    const corpo = novo ? corpoCriar(c) : corpoEditar(meta.id, c, inicial);
    if (corpo) void enviar((pai) => (novo ? criarMeta(corpo, pai) : editarMeta(corpo, pai)));
  }
  const apagar = () => { if (!voando.current && meta) Alert.alert("Apagar meta?", undefined, [{ text: "Cancelar", style: "cancel" }, { text: "Apagar", style: "destructive", onPress: () => void enviar((pai) => apagarMeta(meta.id, pai), true) }]); };

  const opcoes: Opcao[] = [
    ...(semPlano ? [] : [{ titulo: "Tudo investido", sub: undefined, fonte: INVESTIDO }, ...bancos.map((b): Opcao => ({ titulo: b, sub: "Investido", fonte: { tipo: "banco", banco: b, conta: null } }))]),
    ...contas.map((k): Opcao => ({ titulo: [k.instituicao, k.nome].filter(Boolean).join(" · ") || "Conta", sub: "Saldo", fonte: { tipo: "conta", banco: null, conta: k.id } })),
  ];
  const escolhida = opcoes.find((o) => chaveFonte(o.fonte) === chaveFonte(c.fonte));
  const haEscolha = c.fonte.tipo === "investido" || c.fonte.banco !== null || c.fonte.conta !== null;
  if (!escolhida && haEscolha) opcoes.unshift({ titulo: c.fonte.tipo === "investido" ? "Tudo investido" : c.fonte.tipo === "banco" ? c.fonte.banco! : (meta?.fonte.rotulo ?? "Conta"), sub: "Não disponível", fonte: c.fonte }); // a escolha gravada que sumiu da lista não some em silêncio
  const rotuloFonte = (escolhida ?? opcoes.find((o) => chaveFonte(o.fonte) === chaveFonte(c.fonte)))?.titulo ?? "Escolher";
  const trocarTipo = (v: string) => { const tipo = v === "Reserva" ? "reserva" : "meta"; mudar({ tipo, nome: tipo === "reserva" && !c.nome ? NOME_RESERVA : tipo === "meta" && c.nome === NOME_RESERVA ? "" : c.nome }); };
  // Android: o `<DateTimePicker>` é um componente que abre o diálogo num efeito com `onChange` nas dependências (reabriria a cada render); a API imperativa abre uma vez, no toque.
  const android = Platform.OS === "android";
  const abrirPrazo = () => DateTimePickerAndroid.open({ value: dataDoSeletor(c.data ?? hojeSP()), mode: "date", minimumDate: dataDoSeletor(hojeSP()), onChange: (e, d) => { if (e.type === "set" && d) mudar({ data: isoDoSeletor(d) }); } });
  const titulo = !novo ? "Editar meta" : reserva ? "Montar reserva" : "Nova meta", meses = c.meses;

  return <View style={{ gap: espaco.xl }}>
    <Texto variante="secao" accessibilityRole="header">{titulo}</Texto>
    {novo && !comecar && !reservaExiste ? <SegmentedControl opcoes={["Meta", "Reserva"]} valor={reserva ? "Reserva" : "Meta"} onChange={trocarTipo} /> : null}
    <Input rotulo="Nome" value={c.nome} onChangeText={(nome) => mudar({ nome })} maxLength={NOME_MAX} placeholder="Ex.: Viagem" erro={falha?.campos.nome} desativado={bloqueado} />
    {reserva ? <View style={{ gap: espaco.md }}>
      <Texto variante="rotulo" accessibilityLiveRegion="polite">{meses} {meses === 1 ? "mês" : "meses"}</Texto>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <Acao rotulo="Menos um mês" icone="Minus" somenteIcone desativado={bloqueado || meses <= MESES_MIN} onPress={() => mudar({ meses: meses - 1 })} />
        <View style={{ flex: 1, alignItems: "center" }}><Blocos n={meses} cheios={0} tom="brand" /></View>
        <Acao rotulo="Mais um mês" icone="Plus" somenteIcone desativado={bloqueado || meses >= MESES_MAX} onPress={() => mudar({ meses: meses + 1 })} />
      </View>
      {falha?.campos.meses ? <Texto variante="legenda" tom="danger">{falha.campos.meses}</Texto> : null}
      {custo.valor === null
        ? <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm }}><Icone nome="WarningCircle" tamanho={20} tom="inkMuted" /><Acao rotulo="Definir o valor eu mesmo" desativado={bloqueado} onPress={() => mudar({ tipo: "meta" })} /></View>
        : <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}><Texto tom="inkMuted">Custo mensal</Texto><Money centavos={custo.valor} /></View>}
    </View> : <AmountInput rotulo="Valor" centavos={c.centavos} onChange={(centavos) => mudar({ centavos })} erro={falha?.campos.alvo} desativado={bloqueado} />}
    <GrupoDeLinhas>
      {c.data && !android
        ? <ListRow titulo="Prazo" trailing={<DateTimePicker value={dataDoSeletor(c.data)} mode="date" display="compact" minimumDate={dataDoSeletor(hojeSP())} themeVariant={esquema} locale="pt-BR" accessibilityLabel="Prazo" disabled={bloqueado} onChange={(_, d) => { if (d) mudar({ data: isoDoSeletor(d) }); }} />} />
        : <ListRow titulo="Prazo" trailing={<Texto tom="inkMuted">{c.data ? dataPtBr(c.data) : "Sem prazo"}</Texto>} chevron desativado={bloqueado} onPress={android ? abrirPrazo : () => mudar({ data: hojeSP() })} />}
      {c.data ? <ListRow titulo="Tirar prazo" desativado={bloqueado} onPress={() => mudar({ data: null })} /> : null}
      <ListRow titulo="Fonte" trailing={<Texto tom="inkMuted">{rotuloFonte}</Texto>} chevron desativado={bloqueado} onPress={() => setFonteAberta((v) => !v)} />
      {fonteAberta ? opcoes.map((o) => <ListRow key={chaveFonte(o.fonte)} titulo={o.titulo} subtitulo={o.sub} desativado={bloqueado} trailing={chaveFonte(o.fonte) === chaveFonte(c.fonte) ? <Icone nome="CheckCircle" tamanho={20} tom="brand" /> : undefined} onPress={() => { mudar({ fonte: o.fonte }); setFonteAberta(false); }} />) : null}
    </GrupoDeLinhas>
    {falha?.campos.data_alvo ? <Texto variante="legenda" tom="danger">{falha.campos.data_alvo}</Texto> : null}
    {falha?.campos.fonte ? <Texto variante="legenda" tom="danger">{falha.campos.fonte}</Texto> : null}
    {falha && !Object.keys(falha.campos).length ? <Texto tom="danger" accessibilityRole="alert">{falha.geral}</Texto> : null}
    {perdida ? <Button rotulo="Fechar" variante="secondary" onPress={fecharFolha} /> : <Button rotulo="Salvar" desativado={!mudou} carregando={pendente} onPress={salvar} />}
    {!novo ? <View style={{ marginTop: espaco.md }}><Button rotulo="Apagar" variante="danger" desativado={bloqueado} onPress={apagar} /></View> : null}
  </View>;
}
