import { useCallback, useRef, useState } from "react";
import { ActivityIndicator, Image, KeyboardAvoidingView, Platform, Pressable, ScrollView, TextInput, View } from "react-native";
import { useFocusEffect } from "expo-router";
import { Screen } from "@/ui/componentes/Screen";
import { Icone } from "@/ui/componentes/Icone";
import { SIMBOLO } from "@/ui/stickers";
import { useTema } from "@/ui/tema";
import { texto as escalas } from "@/ui/tokens";
import { TextoPainel as Texto } from "./tipografia";
import { usePainel } from "./provider";
import { BotaoPrivacidade } from "./cabecalho";
import { ContextoNavegacaoPainel, useNavegarPainel } from "./navegacao";
import { useConversa } from "./conversaEstado";
import { EstadoVazio, TurnoChat } from "./conversaFalas";
import { Acao, Folha, tituloWidget } from "./base";
import type { Widget } from "./catalogo";
import { WidgetConteudo } from "./widgets";
/** A aba Piggy é só o chat. Sair da aba limpa a tela (conversaEstado.fechar); o histórico do servidor não é mostrado. */
export function Conversa() {
  const p = usePainel(); const c = useConversa(); const { cores } = useTema();
  const nav = useNavegarPainel();
  const pai = useRef<View>(null); const [topoJanela, setTopoJanela] = useState(0);
  const [detalhe, setDetalhe] = useState<Widget | null>(null); const rolagem = useRef<ScrollView>(null); const rolado = useRef(0);
  useFocusEffect(useCallback(() => { c.abrir(); return () => { setDetalhe(null); c.fechar(); }; }, [c.abrir, c.fechar]));
  const livre = c.bloqueio === "livre" && !p.oculto, cheia = !!c.uso && c.uso.used >= c.uso.limit;
  const podeEnviar = livre && !c.enviando && !cheia && !!c.rascunho.trim();
  return <ContextoNavegacaoPainel.Provider value={{ navegar: nav.navegar, entrando: nav.entrando }}><Screen rolar={false}><View ref={pai} onLayout={() => pai.current?.measureInWindow((_x, y) => setTopoJanela(y))} style={{ flex: 1 }}>
    <View style={{ paddingTop: 12, gap: 4 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}><Texto variante="titulo" accessibilityRole="header" style={{ flex: 1, minWidth: 0 }}>Converse com o Piggy</Texto><BotaoPrivacidade /></View>
      <Texto variante="rotulo" tom="inkMuted">Pergunte sobre gastos, fatura, metas e investimentos.</Texto>
      {nav.aviso && <Texto tom="warning">{nav.aviso}</Texto>}
    </View>
    <KeyboardAvoidingView keyboardVerticalOffset={topoJanela} behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <ScrollView ref={rolagem} style={{ flex: 1 }} contentContainerStyle={{ gap: 16, paddingVertical: 16, flexGrow: 1 }} keyboardShouldPersistTaps="handled">
        {c.bloqueio === "abrindo" && <ActivityIndicator accessibilityLabel="Preparando a conversa" />}
        {c.bloqueio === "falhou" && <View style={{ gap: 8 }}><Texto tom="warning">Não conseguimos preparar a conversa agora.</Texto><Acao rotulo="Tentar novamente" onPress={c.tentarNovamente} /></View>}
        {!c.turnos.length ? <EstadoVazio desativado={!livre || cheia || c.enviando} escolher={(s) => void c.enviar(s.ask, s)} />
          : c.turnos.map((t, i) => <TurnoChat key={t.id} turno={t} abrir={setDetalhe} onLayout={i === c.turnos.length - 1 ? (y) => { if (rolado.current !== t.id) { rolado.current = t.id; rolagem.current?.scrollTo({ y, animated: true }); } } : undefined} />)}
      </ScrollView>
      <View style={{ flexDirection: "row", alignItems: "flex-end", gap: 8, borderWidth: 1, borderColor: cores.inkMuted, backgroundColor: cores.surface, borderRadius: 26, paddingLeft: 12, paddingRight: 4, paddingVertical: 4 }}>
        <Image source={SIMBOLO} style={{ width: 24, height: 24, marginBottom: 10 }} accessibilityIgnoresInvertColors />
        <TextInput accessibilityLabel="Mensagem para o Piggy" placeholder="Converse com o Piggy…" placeholderTextColor={cores.inkMuted} value={p.oculto ? "" : c.rascunho} onChangeText={c.setRascunho} editable={!p.oculto} multiline maxLength={2000} maxFontSizeMultiplier={1.3} style={[escalas.corpo, { flex: 1, color: cores.ink, minHeight: 44, maxHeight: 120, paddingVertical: 10 }]} />
        <Pressable accessibilityRole="button" accessibilityLabel="Enviar mensagem" accessibilityState={{ disabled: !podeEnviar }} disabled={!podeEnviar} onPress={() => void c.enviar(c.rascunho)} style={{ width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: podeEnviar ? cores.acao : cores.inkFaint }}><Icone nome="ArrowUp" tom="onAcao" /></Pressable>
      </View>
      {!p.oculto && c.uso && <Texto variante="legenda" tom="inkMuted" style={{ paddingTop: 6 }}>{c.uso.used} de {c.uso.limit} mensagens utilizadas</Texto>}
    </KeyboardAvoidingView>
    <Folha titulo={detalhe ? tituloWidget(detalhe, p.dados) : "Detalhes"} aberta={!!detalhe} fechar={() => setDetalhe(null)}>{detalhe && <WidgetConteudo id={detalhe} />}</Folha>
  </View></Screen></ContextoNavegacaoPainel.Provider>;
}
