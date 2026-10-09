import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { Input } from "@/ui/componentes/Input";
import { ButtonPainel as Button } from "./tipografia";
import { TextoPainel as Texto } from "./tipografia";
import { enviarMensagem } from "@/services/painel";
import { usePainel, mensagemRecurso } from "./provider";
import { Acao, RecursoDados, Vazio } from "./base";

type Mensagem = { role: "user" | "assistant"; content: string };
type Uso = { used: number; limit: number };
type Historico = { messages: Mensagem[]; usage: Uso };
type Eco = { base: Historico; messages: Mensagem[] };
// Um GET bem-sucedido substitui o histórico inteiro: sua janela pode retirar
// turnos antigos. O eco do POST pertence somente ao snapshot que o precedeu.
// Nunca reconciliamos por texto, contagem ou timestamps.
export function Conversa() {
  const p = usePainel();
  const [texto, setTexto] = useState("");
  const [eco, setEco] = useState<Eco | null>(null);
  const [memoria, setMemoria] = useState<{ uid: number; dado: Historico } | null>(null);
  const [uso, setUso] = useState<Uso | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const voo = useRef<symbol | null>(null);
  const dono = useRef(p.usuario.user_id); dono.current = p.usuario.user_id;
  const recurso = p.dados.conversa;

  useEffect(() => {
    setEco(null); setMemoria(null); setUso(null); setTexto(""); setErro(null); setEnviando(false); voo.current = null;
  }, [p.usuario.user_id]);
  useEffect(() => {
    if (recurso?.fase !== "pronto") return;
    setMemoria({ uid: p.usuario.user_id, dado: recurso.dado });
    setEco(null);
    setUso(recurso.dado.usage);
  }, [recurso, p.usuario.user_id]);

  const enviar = async () => {
    const message = texto.trim();
    const limite = uso ?? (recurso?.fase === "pronto" ? recurso.dado.usage : null);
    if (recurso?.fase !== "pronto" || !message || message.length > 2000 || voo.current || !limite || limite.used >= limite.limit) return;
    const token = Symbol(); const uid = p.usuario.user_id;
    voo.current = token; setEnviando(true); setErro(null);
    const op = p.operacao();
    try {
      const r = await enviarMensagem(message, op.controlador);
      if (op.atual()) {
        const pergunta: Mensagem = { role: "user", content: message };
        const resposta: Mensagem = { role: "assistant", content: r.reply };
        const base = recurso.dado;
        setEco((anterior) => ({ base, messages: [...(anterior?.base === base ? anterior.messages : []), pergunta, resposta] }));
        setUso(r.usage); setTexto("");
      }
    } catch (e) {
      if (op.atual()) { p.falhou(e); setErro(mensagemRecurso(e)); }
    } finally {
      if (voo.current === token && dono.current === uid) { setEnviando(false); voo.current = null; }
    }
  };

  const d = recurso?.fase === "pronto" ? recurso.dado : memoria?.uid === p.usuario.user_id ? memoria.dado : null;
  if (!d) return <RecursoDados nome="conversa">{() => null}</RecursoDados>;
  const mensagens = [...d.messages, ...(eco?.base === d ? eco.messages : [])];
    const atual = uso ?? d.usage;
    return <View style={{ gap: 16 }}>
      <Texto variante="rotulo" tom="inkMuted">Pergunte sobre seus gastos, metas e compromissos. Ações financeiras pedem sua confirmação na conversa.</Texto>
      {!mensagens.length && <Vazio texto="Comece uma conversa com o Piggy." />}
      {mensagens.map((m, i) => <View key={i} style={{ gap: 6 }}><Texto variante="legenda" tom="brandInk">{m.role === "user" ? "Você" : "Piggy"}</Texto><Texto>{p.oculto ? "Mensagem oculta enquanto os valores estão privados." : m.content}</Texto></View>)}
      {!p.oculto && <Texto variante="legenda" tom="inkMuted">{atual.used} de {atual.limit} mensagens utilizadas</Texto>}
      {erro && <Texto tom="warning">{erro}</Texto>}
      <Input rotulo="Mensagem para o Piggy" value={p.oculto ? "" : texto} onChangeText={setTexto} multiline maxLength={2000} desativado={enviando || p.oculto} />
      <Button rotulo="Enviar mensagem" carregando={enviando} desativado={recurso?.fase !== "pronto" || !texto.trim() || p.oculto || atual.used >= atual.limit} onPress={() => void enviar()} />
      {(!recurso || recurso.fase === "carregando") && <ActivityIndicator accessibilityLabel="Atualizando conversa" />}
      {recurso && recurso.fase !== "pronto" && recurso.fase !== "carregando" && <View style={{ gap: 8 }}><Texto tom="warning">{recurso.mensagem}</Texto>{recurso.fase === "erro" && <Acao rotulo="Tentar novamente" onPress={p.atualizar} />}</View>}
    </View>;
}
