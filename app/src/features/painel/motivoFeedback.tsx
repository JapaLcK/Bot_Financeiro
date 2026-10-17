import { Keyboard, View } from "react-native";
import { useToast } from "@/ui/componentes/Toast";
import { useConversa, type Turno } from "./conversaEstado";
import { ChipPainel, TextoPainel as Texto } from "./tipografia";
/** Espelho de `db/ai_feedback.py::MOTIVOS` e de `TEXTO_MAX` em `frontend/routes/ai_chat.py` (§0.7: `tests/test_motivo_feedback_app.py` compara). */
export const MOTIVOS = { numero_errado: "Número errado", nao_entendeu: "Não entendeu", faltou_card: "Faltou card", outro: "Outro" } as const;
export type Motivo = keyof typeof MOTIVOS;
export const TEXTO_MAX = 500;
const FALHA = { mensagem: "Não conseguimos registrar sua avaliação.", tom: "erro" } as const;
/** Sob o 👎 da última resposta: pílulas com o motivo, inline no chat. Tocar na marcada desmarca (grava só `{nota:-1}`); "Outro" põe a caixa principal do chat em modo comentário (`useComentario`). */
export function MotivoFeedback({ turno: t }: { turno: Turno }) {
  const c = useConversa(); const toast = useToast();
  const escolher = async (m: Motivo) => {
    const tira = t.motivo === m, antes = c.comentario;
    c.comentar(!tira && m === "outro" ? { turno: t.id, texto: "" } : null);
    if ((await c.gravarAvaliacao(t, { nota: -1, motivo: tira ? undefined : m })) === false) { c.comentar(antes); toast.mostrar(FALHA); } // a reversão da nota devolve o modo e o texto de antes
  };
  return <View style={{ gap: 4 }}>
    <Texto variante="legenda" tom="inkMuted">O que houve?</Texto>
    <View style={{ flexDirection: "row", flexWrap: "wrap", columnGap: 8 }}>
      {(Object.keys(MOTIVOS) as Motivo[]).map((m) => <ChipPainel key={m} rotulo={MOTIVOS[m]} selecionado={t.motivo === m} desativado={c.avaliando} onPress={() => void escolher(m)} />)}
    </View>
  </View>;
}
/** A caixa principal do chat em modo comentário: `ativo` só com o último turno em 👎 + "outro" e o toque no "Outro" (cada entrada reescreve o `comentario`, então ele nunca é de outro turno). O ↑ manda `enviar` (feedback), não a IA; falha mantém o texto. Chamar dentro do ToastProvider. */
export function useComentario() {
  const c = useConversa(); const toast = useToast();
  const ult = c.turnos[c.turnos.length - 1], k = c.comentario;
  const ativo = !!ult && !!k && ult.feedback === -1 && ult.motivo === "outro";
  const texto = ativo ? k.texto : "";
  const enviar = async () => {
    if (!ativo) return;
    const ok = await c.gravarAvaliacao(ult, { nota: -1, motivo: "outro", texto: texto.trim() });
    if (ok === false) return toast.mostrar(FALHA);
    if (!ok) return; // descartada (toque duplo): o POST em voo decide
    c.comentar(null); Keyboard.dismiss(); /* o teclado cobriria o toast */ toast.mostrar({ mensagem: "Obrigado pelo retorno", tom: "sucesso" });
  };
  return { ativo, texto, pode: ativo && !!texto.trim() && !c.avaliando, mudar: (x: string) => ult && c.comentar({ turno: ult.id, texto: x }), enviar };
}
