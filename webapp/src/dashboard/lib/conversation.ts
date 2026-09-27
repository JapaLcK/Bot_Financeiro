import { useSyncExternalStore, type ReactNode } from "react";
import { go } from "../router";
import { DEMO, answer, type Answer, type TopicId } from "./topics";

// A conversa com o Piggy: vive enquanto a aba está aberta (troca de página não apaga;
// recarregar começa de novo). Guardar histórico é fase posterior, com backend.
export interface Msg extends Partial<Omit<Answer, "text">> { id: number; role: "user" | "piggy"; text: ReactNode }

let msgs: Msg[] = [];
let seq = 0;
const subs = new Set<() => void>();

export const useConversation = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => msgs);

/** Envia a pergunta e abre a conversa. Sem `topic`, o Piggy responde com os atalhos. */
export function ask(q: { text: string; topic?: TopicId | null; cat?: string | null; key?: string }) {
  const a = q.topic ? answer(q.topic, q.cat ?? null, q.key) : DEMO;
  msgs = [...msgs, { id: ++seq, role: "user", text: q.text }, { id: ++seq, role: "piggy", ...a }];
  subs.forEach((f) => f());
  go("/piggy");
}
