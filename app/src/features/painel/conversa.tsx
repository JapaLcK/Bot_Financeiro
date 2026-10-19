import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Image, KeyboardAvoidingView, Platform, Pressable, ScrollView, TextInput, View } from "react-native";
import { useFocusEffect } from "expo-router";
import Animated, { FadeIn, FadeOut } from "react-native-reanimated";
import { Screen } from "@/ui/componentes/Screen";
import { Icone } from "@/ui/componentes/Icone";
import { ToastProvider } from "@/ui/componentes/Toast";
import { SIMBOLO } from "@/ui/stickers";
import { useEntrada, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { espaco, raio, texto as escalas } from "@/ui/tokens";
import { TextoPainel as Texto } from "./tipografia";
import { usePainel } from "./provider";
import { BotaoPrivacidade } from "./cabecalho";
import { ContextoNavegacaoPainel, useNavegarPainel } from "./navegacao";
import { cotaCheia, useConversa } from "./conversaEstado";
import { EstadoVazio, TurnoChat } from "./conversaFalas";
import { Acao } from "./base";
import { LinhaSugestoes } from "./linhaSugestoes";
import { ListaMencoes } from "./listaMencoes";
import { TEM_ARROBA, itensMencao, tokenEm } from "./agentes";
import { sugestoes } from "./perguntas";
import { TEXTO_MAX, useComentario } from "./motivoFeedback";
/** A aba Piggy é só o chat. Sair da aba limpa a tela (conversaEstado.fechar); o histórico do servidor não é mostrado. */
export function Conversa() {
  const nav = useNavegarPainel();
  return <ContextoNavegacaoPainel.Provider value={{ navegar: nav.navegar, entrando: nav.entrando }}><ToastProvider><ConversaTela aviso={nav.aviso} /></ToastProvider></ContextoNavegacaoPainel.Provider>; // o toast do modo comentário (`useComentario`) precisa estar DENTRO do ToastProvider
}
function ConversaTela({ aviso }: { aviso: string | null }) {
  const p = usePainel(); const c = useConversa(); const { cores } = useTema();
  const com = useComentario(), campo = useRef<TextInput>(null);
  useEffect(() => { if (com.ativo) campo.current?.focus(); }, [com.ativo]); // "Outro" não abre campo novo: leva o cursor para a caixa de sempre
  const pai = useRef<View>(null); const [topoJanela, setTopoJanela] = useState(0);
  const rolagem = useRef<ScrollView>(null); const rolado = useRef(0);
  const reduzir = useReduzirMovimento(), entrada = useEntrada();
  const pos = useRef({ y: 0, h: 0, c: 0 }); const [longe, setLonge] = useState(false); // "voltar ao fim" quando falta mais que `huge` até o fim; o stream não puxa a tela
  const medir = (v: Partial<typeof pos.current>) => { Object.assign(pos.current, v); const { y, h, c } = pos.current; setLonge(c - (y + h) > espaco.huge); };
  useFocusEffect(useCallback(() => { c.abrir(); return c.fechar; }, [c.abrir, c.fechar]));
  const livre = c.bloqueio === "livre" && !p.oculto, cheia = cotaCheia(c.uso);
  const podeEnviar = livre && !c.enviando && !cheia && !!c.rascunho.trim();
  const chips = !c.enviando && livre && !cheia ? c.turnos[c.turnos.length - 1]?.chips ?? [] : []; // do ÚLTIMO turno; o `fim` com a aba fechada já os zera
  const vazio = !c.turnos.length; // chat novo: as 5 sugestões fixas (desligadas, não escondidas, se o envio está bloqueado); depois da resposta, os chips do servidor
  const sugs = vazio ? sugestoes(p.perfil).map((s) => ({ rotulo: s.ask, aoEscolher: () => void c.enviar(s.ask, s) })) : chips.map((x) => ({ rotulo: x, aoEscolher: () => void c.enviar(x) }));
  const [cursor, setCursor] = useState(0); // onChangeText põe no fim; o onSelectionChange corrige (a ordem dos dois muda por plataforma)
  const quer = !com.ativo && !p.oculto && TEM_ARROBA.test(c.rascunho), { ler, catalogo } = c.agentes; // sem `@`, nenhuma requisição a /agents
  useEffect(() => { if (quer) void ler(); }, [quer, ler]);
  const token = quer ? tokenEm(c.rascunho, cursor) : null, itens = token && catalogo ? itensMencao(catalogo, token.q) : [];
  const escolher = (nome: string) => { const antes = c.rascunho.slice(0, token!.ini); c.setRascunho(`${antes}@${nome} ${c.rascunho.slice(cursor)}`); setCursor(antes.length + nome.length + 2); };
  const enviarCampo = () => { const t = c.rascunho; if (!TEM_ARROBA.test(t)) return void c.enviar(t, undefined, true); void c.agentes.rotear(t).then((r) => { if (r === "piggy") void c.enviar(t, undefined, true); }); };
  const ativo = com.ativo ? com.pode && !p.oculto : c.enviando || podeEnviar; // enviando = o botão vira "Parar" (permitido com valores ocultos: não revela nada)
  return <Screen rolar={false} sobAbas><View ref={pai} onLayout={() => pai.current?.measureInWindow((_x, y) => setTopoJanela(y))} style={{ flex: 1 }}>
    <View style={{ flexDirection: "row", justifyContent: "flex-end", backgroundColor: cores.bg }}><BotaoPrivacidade tamanho={20} /></View>
    {aviso && <Texto tom="warning">{aviso}</Texto>}
    <KeyboardAvoidingView keyboardVerticalOffset={topoJanela} behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <View style={{ flex: 1 }}>
      <ScrollView ref={rolagem} style={{ flex: 1 }} scrollEventThrottle={100} onScroll={(e) => { const n = e.nativeEvent; medir({ y: n.contentOffset.y, h: n.layoutMeasurement.height, c: n.contentSize.height }); }} onLayout={(e) => medir({ h: e.nativeEvent.layout.height })} onContentSizeChange={(_w, h) => medir({ c: h })} contentContainerStyle={{ gap: 16, paddingVertical: 16, flexGrow: 1 }} keyboardShouldPersistTaps="handled">
        {c.bloqueio === "abrindo" && <ActivityIndicator accessibilityLabel="Preparando a conversa" />}
        {c.bloqueio === "falhou" && <View style={{ gap: 8 }}><Texto tom="warning">Não conseguimos preparar a conversa agora.</Texto><Acao rotulo="Tentar novamente" onPress={c.tentarNovamente} /></View>}
        {vazio ? <EstadoVazio />
          : c.turnos.map((t, i) => <TurnoChat key={t.id} turno={t} ultimo={i === c.turnos.length - 1} onLayout={i === c.turnos.length - 1 ? (y) => { if (rolado.current !== t.id) { rolado.current = t.id; rolagem.current?.scrollTo({ y, animated: !reduzir }); } } : undefined} />)}
      </ScrollView>
      {longe && <Animated.View entering={FadeIn} exiting={FadeOut} style={{ position: "absolute", bottom: 8, alignSelf: "center" }}><Pressable accessibilityRole="button" accessibilityLabel="Voltar ao fim da conversa" onPress={() => rolagem.current?.scrollToEnd({ animated: !reduzir })} style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center" }}><View style={{ width: 36, height: 36, borderRadius: raio.pilula, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: cores.border, backgroundColor: cores.surfaceRaised }}><Icone nome="ArrowDown" tom="inkMuted" /></View></Pressable></Animated.View>}
      </View>
      {itens.length > 0 ? <ListaMencoes itens={itens} onEscolher={escolher} /> : sugs.length > 0 && <Animated.View testID={vazio ? "sugestoes-piggy" : "chips-piggy"} entering={entrada}><LinhaSugestoes itens={sugs} desativado={vazio && (!livre || cheia || c.enviando)} /></Animated.View>}
      <View style={{ flexDirection: "row", alignItems: "flex-end", gap: 8, borderWidth: 1, borderColor: cores.border, backgroundColor: cores.surfaceControl, borderRadius: raio.pilula, paddingLeft: 12, paddingRight: 4, paddingVertical: 4 }}>
        <Image source={SIMBOLO} style={{ width: 24, height: 24, marginBottom: 10 }} accessibilityIgnoresInvertColors />
        <TextInput ref={campo} accessibilityLabel={com.ativo ? "Comentário sobre a resposta" : "Mensagem para o Piggy"} placeholder={com.ativo ? "Conte o que houve (opcional)" : "Converse com o Piggy…"} placeholderTextColor={cores.inkFaint} selectionColor={cores.brand} value={p.oculto ? "" : com.ativo ? com.texto : c.rascunho} onChangeText={com.ativo ? com.mudar : (t) => { c.setRascunho(t); setCursor(t.length); }} onSelectionChange={(e) => setCursor(e.nativeEvent.selection.end)} editable={!p.oculto} multiline maxLength={com.ativo ? TEXTO_MAX * 2 : 2000} maxFontSizeMultiplier={1.3} style={[escalas.corpo, { flex: 1, color: cores.ink, minHeight: 44, maxHeight: 120, paddingVertical: 10 }]} />
        <Pressable accessibilityRole="button" accessibilityLabel={com.ativo ? "Enviar comentário" : c.enviando ? "Parar resposta" : "Enviar mensagem"} accessibilityState={{ disabled: !ativo }} disabled={!ativo} onPress={com.ativo ? () => void com.enviar() : c.enviando ? c.parar : enviarCampo} style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center" }}><View style={{ width: 36, height: 36, borderRadius: raio.pilula, alignItems: "center", justifyContent: "center", backgroundColor: ativo ? cores.acao : cores.surfaceRaised }}><Icone nome={c.enviando ? "Stop" : "ArrowUp"} tom={ativo ? "onAcao" : "inkFaint"} /></View></Pressable>
      </View>
      {!p.oculto && c.uso && c.uso.used * 5 >= c.uso.limit * 4 && <Texto variante="legenda" tom="inkMuted" style={{ paddingTop: 6 }}>{c.uso.used} de {c.uso.limit} mensagens utilizadas</Texto>}
      <View style={{ height: espaco.sm }} />{/* respiro: sob a tab bar a borda da caixa não encosta; vive dentro do KAV, que sobrescreve o próprio paddingBottom */}
    </KeyboardAvoidingView>
  </View></Screen>;
}
