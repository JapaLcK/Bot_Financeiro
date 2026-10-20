import { router, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { Animated, Image, LayoutAnimation, Pressable, ScrollView, StyleSheet, View } from "react-native";
import { RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import { useSessao } from "@/features/auth/sessao";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { useForeground } from "@/features/openFinance/useForeground";
import { lerAgentes } from "@/services/agentes";
import { perfil } from "@/services/auth";
import { Icone } from "@/ui/componentes/Icone";
import { Screen } from "@/ui/componentes/Screen";
import { usePressao, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { escuro, espaco, raio, texto } from "@/ui/tokens";
import { AGENTES, SOBRE, acessoDe, energiaDe, type Acesso, type Agentes } from "./agentes";
import { CENA } from "./avatares";
import { TextoPainel as Texto } from "./tipografia";

// A tela Agentes (aberta pela Ferramentas), par da /painel#/agentes: uma faixa aberta por vez, as outras com nome e tema.
// As faixas são fotos: ficam na paleta escura nos dois temas; o resto segue o tema do aparelho.
const FOTO = { fundo: escuro.bg, tinta: escuro.ink, suave: "rgba(244,244,246,0.8)", rosa: escuro.brandInk, chip: "rgba(244,244,246,0.12)" };
const ALTURA = { fechada: 68, aberta: 450 };
const SELO: Record<Acesso, { rotulo: string; cor: string }> = {
  ready: { rotulo: "No seu time", cor: escuro.positive }, activate: { rotulo: "Disponível", cor: escuro.brandInk },
  no_energy: { rotulo: "Sem energia", cor: escuro.warning }, upgrade: { rotulo: "Fora do plano", cor: escuro.inkFaint },
};
type Agente = (typeof AGENTES)[number] & (typeof SOBRE)[string] & { acesso: Acesso | null; custo?: number; aviso?: string };
let entradas = 0; // muda a cada toque: o chat põe o "@Nome pergunta" no campo mesmo se o mesmo agente for pedido duas vezes

/** Lê o dono da sessão (`/auth/me`) e o `/agents/{uid}` dele; o uid nunca vem de param nem de texto. */
function useElenco() {
  const { expirou } = useSessao();
  const [d, setD] = useState<Agentes | null>(null), [erro, setErro] = useState(false), [desligado, setDesligado] = useState(false);
  const ctl = useRef<AbortController | null>(null);
  const ler = useCallback(async () => {
    ctl.current?.abort(); const c = (ctl.current = new AbortController());
    try { const { user_id, agents_ui_enabled } = await perfil(); if (ctl.current !== c) return; setDesligado(agents_ui_enabled === false); if (agents_ui_enabled === false) return; const r = await lerAgentes(user_id, c); if (ctl.current !== c) return; setD(r); setErro(false); }
    catch (e) {
      if (ctl.current !== c) return;
      if (e instanceof SessaoExpirada) return expirou(e.detalhe);
      if (e instanceof RequisicaoSuperada) return;
      setD(null); setErro(true); // sem estado confiável: a tela mostra o catálogo sem selo nem botão, como o /painel
    }
  }, [expirou]);
  const ativo = useForeground(), travado = useBloqueio().estado.fase === "travado";
  useFocusEffect(useCallback(() => {
    if (ativo && !travado) void ler();
    return () => { ctl.current?.abort(); ctl.current = null; };
  }, [ler, ativo, travado]));
  const lista: Agente[] = AGENTES.map((a) => {
    const acesso = d ? acessoDe(d, a.kind) : null, e = d ? energiaDe(d, a.kind) : null;
    const aviso = acesso === "upgrade" ? `Conversar com o ${a.nome} faz parte do PigBank+.` : acesso === "no_energy" ? (e ? `O ${a.nome} usa ⚡${e.custo} de energia e você tem ${e.livre} livre.` : `Você está sem energia livre para ativar o ${a.nome}.`) : undefined;
    return { ...a, ...SOBRE[a.kind]!, acesso, custo: d?.catalog.find((c) => c.kind === a.kind)?.energy_cost, aviso };
  });
  // Plano sem energia (orçamento 0): sem barra, como no /painel.
  const energia = d?.energy_enabled && Number(d.energy_budget) > 0 ? { usada: Number(d.energy_used || 0), total: Number(d.energy_budget) } : null;
  return { lista, energia, erro, desligado };
}

export function AgentesTela() {
  const { lista, energia, erro, desligado } = useElenco();
  const reduzir = useReduzirMovimento(), indo = useRef(false);
  const [aberto, setAberto] = useState(0), [largura, setLargura] = useState(0);
  useFocusEffect(useCallback(() => { indo.current = false; }, []));
  const rolagem = useRef<ScrollView>(null), topoLista = useRef(0), tocou = useRef(false);
  const abrir = (i: number) => { if (!reduzir) LayoutAnimation.configureNext(LayoutAnimation.create(260, "easeInEaseOut", "opacity")); tocou.current = true; setAberto(i); };
  // dismissTo, não navigate: o painel já está embaixo na pilha (Painel > Ferramentas > Agentes); `navigate` empilhava um painel novo por cima.
  const conversar = (kind: string) => { if (indo.current) return; indo.current = true; router.dismissTo({ pathname: "/piggy", params: { agente: kind, entrada: ++entradas } }); };
  if (desligado) return <Screen sobCabecalho><Texto tom="inkMuted" style={{ paddingVertical: espaco.lg }}>Os agentes estão indisponíveis no momento.</Texto></Screen>; // o freio AGENTS_UI_ENABLED=0, como o /painel
  return <Screen sobCabecalho rolar={false}>
    <ScrollView ref={rolagem} contentContainerStyle={{ gap: espaco.lg, paddingVertical: espaco.lg }}>
      <Texto tom="inkMuted">Sete Piggys, cada um cuidando de uma parte do seu dinheiro.</Texto>
      {energia && <Energia {...energia} />}
      {erro && <Texto tom="warning" accessibilityRole="alert">Não deu para carregar o estado dos seus agentes agora. Tente de novo em instantes.</Texto>}
      <View style={{ gap: espaco.sm }} onLayout={(e) => { setLargura(e.nativeEvent.layout.width); topoLista.current = e.nativeEvent.layout.y; }}>
        {lista.map((a, i) => <Faixa key={a.kind} a={a} on={i === aberto} largura={largura} onAbrir={() => abrir(i)} onConversar={() => conversar(a.kind)}
          onLayoutAberta={(y) => { if (tocou.current) { tocou.current = false; rolagem.current?.scrollTo({ y: Math.max(0, topoLista.current + y - espaco.lg), animated: !reduzir }); } }} />)}
      </View>
    </ScrollView>
  </Screen>;
}

function Energia({ usada, total }: { usada: number; total: number }) {
  const { cores } = useTema(), cheios = total > 30 ? Math.round((usada / total) * 30) : usada; // acima de 30, proporção (como no /painel)
  return <View accessible accessibilityLabel={`Energia: ${usada} de ${total} em uso, ${Math.max(0, total - usada)} livres`} style={{ gap: espaco.md, padding: espaco.lg, borderRadius: raio.bloco, backgroundColor: cores.surface }}>
    <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm }}>
      <Icone nome="Lightning" tamanho={16} cor={cores.brand} />
      <Texto style={{ flex: 1 }}>Energia <Texto style={{ fontFamily: texto.secao.fontFamily }}>{usada}</Texto> de {total}</Texto>
      <Texto variante="legenda" tom="inkMuted">{Math.max(0, total - usada)} livres</Texto>
    </View>
    <View style={{ flexDirection: "row", gap: 3 }}>
      {Array.from({ length: Math.min(total, 30) }, (_, i) => <View key={i} style={{ flex: 1, height: 5, borderRadius: raio.barra, backgroundColor: i < cheios ? cores.brand : cores.border }} />)}
    </View>
  </View>;
}

const Chip = ({ children }: { children: React.ReactNode }) => <View style={{ flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 10, paddingVertical: 5, borderRadius: raio.pilula, backgroundColor: FOTO.chip }}>{children}</View>;
const NaFoto = (p: React.ComponentProps<typeof Texto>) => <Texto {...p} style={[{ color: FOTO.tinta }, p.style]} />;

function Faixa({ a, on, largura, onAbrir, onConversar, onLayoutAberta }: { a: Agente; on: boolean; largura: number; onAbrir: () => void; onConversar: () => void; onLayoutAberta: (y: number) => void }) {
  const h = on ? ALTURA.aberta : ALTURA.fechada, w = Math.max(largura, h * 2); // a cena é 2:1: cobre a faixa e recorta no `foco`
  const cena = largura > 0 && <Image source={CENA[a.kind]!} accessibilityIgnoresInvertColors style={{ position: "absolute", width: w, height: w / 2, left: -(w - largura) * a.foco, top: on ? 0 : -(w / 2 - h) / 2 }} />;
  const caixa = { minHeight: h, borderRadius: raio.bloco, overflow: "hidden" as const, backgroundColor: FOTO.fundo };
  const pressao = usePressao(), soltar = useRef(pressao.aoSoltar); soltar.current = pressao.aoSoltar;
  useEffect(() => { if (!on) soltar.current(); }, [on]); // o toque abre a faixa e desmonta o Pressable antes do onPressOut: a escala voltaria presa em 0,97
  if (!on) return <Pressable accessibilityRole="button" accessibilityState={{ expanded: false }} accessibilityLabel={`${a.nome}, ${a.tema}${a.acesso === "ready" ? ", no seu time" : a.acesso === "no_energy" ? ", sem energia" : ""}`} onPress={onAbrir} onPressIn={pressao.aoPressionar} onPressOut={pressao.aoSoltar}>
    <Animated.View style={[caixa, { justifyContent: "center" }, pressao.estilo]}>{/* o estilo mora na View, como no Button: o Pressable não cortava a cena */}
    {cena}
    <View style={[StyleSheet.absoluteFill, { experimental_backgroundImage: "linear-gradient(to right, rgba(9,9,11,0.9) 0%, rgba(9,9,11,0.55) 55%, rgba(9,9,11,0.2) 100%)" }]} />
    <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: espaco.lg, gap: espaco.sm }}>
      <View style={{ flex: 1 }}>
        <NaFoto numberOfLines={1} style={{ fontFamily: texto.secao.fontFamily, fontSize: 17, lineHeight: 22 }}>{a.nome}</NaFoto>
        <NaFoto variante="legenda" numberOfLines={1} style={{ color: FOTO.suave }}>{a.tema}</NaFoto>
      </View>
      {a.acesso === "ready" ? <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: SELO.ready.cor }} />
        : <>{a.acesso === "no_energy" && <NaFoto variante="legenda" style={{ color: FOTO.suave }}>Sem energia</NaFoto>}<Icone nome="CaretRight" tamanho={16} cor={FOTO.suave} /></>}
    </View>
    </Animated.View>
  </Pressable>;
  const selo = a.acesso && SELO[a.acesso];
  return <View style={[caixa, { justifyContent: "flex-end" }]} onLayout={(e) => onLayoutAberta(e.nativeEvent.layout.y)}>
    {cena}
    <View style={[StyleSheet.absoluteFill, { experimental_backgroundImage: "linear-gradient(to bottom, rgba(9,9,11,0) 25%, rgba(9,9,11,0.7) 55%, rgba(9,9,11,0.96) 100%)" }]} />
    <View style={{ padding: espaco.xl, gap: espaco.sm }}>
      <NaFoto variante="legenda" style={{ color: FOTO.rosa, fontFamily: texto.secao.fontFamily, letterSpacing: 1.6, textTransform: "uppercase" }}>{a.tema}</NaFoto>
      <NaFoto accessibilityRole="header" style={{ fontFamily: texto.display.fontFamily, fontSize: 34, lineHeight: 38, letterSpacing: -1 }}>{a.nome}</NaFoto>
      <NaFoto style={{ fontFamily: texto.secao.fontFamily, fontSize: 16, lineHeight: 22 }}>{a.frase}</NaFoto>
      <NaFoto style={{ color: FOTO.suave }}>{a.texto}</NaFoto>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, paddingTop: espaco.xs }}>
        {selo && <Chip><View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: selo.cor }} /><NaFoto variante="legenda">{selo.rotulo}</NaFoto></Chip>}
        <Chip><Icone nome="ClockCounterClockwise" tamanho={16} cor={FOTO.suave} /><NaFoto variante="legenda">{a.ritmo}</NaFoto></Chip>
        {a.custo != null && <View accessible accessibilityLabel={`Ocupa ${a.custo} de energia`}><Chip><Icone nome="Lightning" tamanho={16} cor={FOTO.rosa} /><NaFoto variante="legenda">{a.custo}</NaFoto></Chip></View>}
      </View>
      {a.aviso && <NaFoto variante="legenda" style={{ color: FOTO.suave }}>{a.aviso}</NaFoto>}
      {(a.acesso === "ready" || a.acesso === "activate") && <Cta rotulo={a.acesso === "ready" ? "Conversar" : "Ativar"} onPress={onConversar} />}
    </View>
  </View>;
}

function Cta({ rotulo, onPress }: { rotulo: string; onPress: () => void }) {
  const { cores } = useTema(), pressao = usePressao();
  return <Pressable accessibilityRole="button" accessibilityLabel={rotulo} onPress={onPress} onPressIn={pressao.aoPressionar} onPressOut={pressao.aoSoltar} hitSlop={4} style={{ alignSelf: "flex-start", marginTop: espaco.sm }}>
    <Animated.View style={[{ flexDirection: "row", alignItems: "center", gap: espaco.md, paddingLeft: espaco.xl, paddingRight: 6, paddingVertical: 6, borderRadius: raio.pilula, backgroundColor: cores.acao }, pressao.estilo]}>
      <Texto tom="onAcao" style={{ fontFamily: texto.secao.fontFamily, fontSize: 16 }}>{rotulo}</Texto>
      <View style={{ width: 34, height: 34, borderRadius: raio.pilula, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(15,23,42,0.12)" }}><Icone nome="ArrowRight" tamanho={16} tom="onAcao" /></View>
    </Animated.View>
  </Pressable>;
}
