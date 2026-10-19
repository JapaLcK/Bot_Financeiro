import { ActivityIndicator, Image, View } from "react-native";
import Animated from "react-native-reanimated";
import { useEntrada } from "@/ui/motion";
import { SIMBOLO } from "@/ui/stickers";
import { useTema } from "@/ui/tema";
import { TextoPainel as Texto } from "./tipografia";
import { Acao } from "./base";
import { agenteDe, mencoes } from "./agentes";
import { AVATAR } from "./avatares";
import { useConversa, type Turno } from "./conversaEstado";
/** Quem fala: avatar (fora do VoiceOver), nome e, num agente, a etiqueta do estado. Sem `kind`, é o Piggy. */
export function Autor({ kind, etiqueta }: { kind?: string; etiqueta?: string }) {
  const nome = kind ? agenteDe(kind)?.nome ?? "Agente" : "Piggy";
  return <View accessible accessibilityLabel={etiqueta ? `${nome}, ${etiqueta.toLowerCase()}` : nome} style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
    <Image source={kind ? AVATAR[kind] ?? SIMBOLO : SIMBOLO} accessibilityIgnoresInvertColors style={{ width: 24, height: 24, borderRadius: 12 }} />
    <Texto variante="rotulo">{nome}</Texto>
    {etiqueta && <Texto variante="legenda" tom="inkMuted">{etiqueta}</Texto>}
  </View>;
}
/** A pergunta do usuário com cada menção válida em `brandInk`. */
export function ComMencoes({ texto }: { texto: string }) {
  const { cores } = useTema(); let i = 0; const partes: (string | React.JSX.Element)[] = [];
  for (const m of mencoes(texto)) { partes.push(texto.slice(i, m.ini), <Texto key={m.ini} style={{ color: cores.brandInk }}>{texto.slice(m.ini, m.fim)}</Texto>); i = m.fim; }
  return <Texto>{partes}{texto.slice(i)}</Texto>;
}
/** A resposta de um agente (turno com `agente`). Texto puro, sem Markdown: o `\n` do RN já quebra a linha. Sem copiar, sem 👍👎 (não há `messageId`). */
export function FalaAgente({ turno: t }: { turno: Turno }) {
  const c = useConversa(); const entrada = useEntrada(); const a = t.agente!, ag = c.agentes, nome = agenteDe(a.kind)?.nome ?? "Agente";
  const etiqueta = a.estado === "inativo" ? "NÃO ATIVO" : a.estado === "sem_energia" ? "SEM ENERGIA" : "AGENTE";
  return <Animated.View entering={entrada} testID="fala-agente" style={{ alignSelf: "flex-start", width: "100%", gap: 6 }}>
    <Autor kind={a.kind} etiqueta={etiqueta} />
    {a.estado === "enviando"
      ? <View style={{ alignSelf: "flex-start" }}><ActivityIndicator size="small" accessibilityLabel={`${nome} está respondendo`} /></View>
      : <Texto tom={a.estado === "erro" ? "warning" : "ink"}>{a.texto}</Texto>}
    {a.estado === "inativo" && <>
      <Acao rotulo={a.ativando ? "Ativando…" : "Ativar e responder"} desativado={a.ativando} onPress={() => void ag.ativarEResponder(t)} />
      {a.falhaAtivar && <Texto tom="warning" accessibilityRole="alert">Não foi possível ativar o agente. Tente novamente.</Texto>}
    </>}
    {a.estado === "erro" && a.repetir && <Acao rotulo="Tentar de novo" onPress={() => ag.tentarDeNovo(t)} />}
    {a.estado === "pronto" && a.redirects.map((r, i) => <Acao key={i} rotulo={`Perguntar ao ${agenteDe(r.kind)?.nome ?? r.name}`} desativado={c.enviando} onPress={() => ag.redirecionar(r)} />)}
  </Animated.View>;
}
