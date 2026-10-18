import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { NATIVE, useDialogo } from "../parts/dialog";
import { ErroApi } from "../lib/v2";

// O cofre dos códigos de backup do MFA: o servidor os devolve UMA vez e (no regerar) já apagou os antigos, então eles
// moram AQUI, na memória do módulo (padrão do router.ts), e não em estado de componente nem no cache do React Query.
// Fechar o modal do MFA, trocar de página, o aviso do SSE, um refetch ou o portão (parts/Entrada.tsx) cair NÃO os apagam:
// o <Cofre/> é irmão do <Entrada> (main.tsx). Só o "Concluído" os solta. Nunca em storage, URL nem console.
type Guardado = { codigos: string[]; regerado: boolean; aberto: boolean };
let guardado: Guardado | null = null;
const ouvintes = new Set<() => void>();
const muda = (g: Guardado | null) => { guardado = g; ouvintes.forEach((f) => f()); };

// Chamado pelas escritas (api.ts) com a resposta crua. Resposta que não é uma lista de textos = o servidor comitou e
// os códigos não chegaram: quem chamou trata como falha ambígua (releitura do status).
export function guardarCodigos(bruto: unknown, regerado: boolean) {
  // Todos os itens têm de ser texto: mostrar MENOS códigos que os comitados, em silêncio, é pior que dizer que não vieram.
  if (!Array.isArray(bruto) || !bruto.length || !bruto.every((c) => typeof c === "string" && c !== "")) throw new ErroApi(200, "sem_codigos");
  muda({ codigos: bruto as string[], regerado, aberto: false });
}
// Separado de `guardar`: o modal do MFA fecha ANTES (o fallback sem <dialog> aceita uma trava por vez).
export const abrirCofre = () => { if (guardado && !guardado.aberto) muda({ ...guardado, aberto: true }); };

const assinar = (f: () => void) => { ouvintes.add(f); return () => { ouvintes.delete(f); }; };

function Modal({ g }: { g: Guardado }) {
  const saindo = useRef(false); // só o "Concluído" destrava: Esc, fundo e o 3º Esc do Chromium (onClose) reabrem
  const { dlg, fecharDialog, fundo, props } = useDialogo(() => muda(null), () => !saindo.current);
  const concluir = () => { saindo.current = true; fecharDialog(); };
  const [aviso, setAviso] = useState(""); // para leitor de tela; o texto dos códigos nunca vai ao console
  const copiar = () => {
    Promise.resolve().then(() => navigator.clipboard.writeText(g.codigos.join("\n"))).then(() => setAviso("Códigos copiados"), () => setAviso("Não foi possível copiar"));
  };
  return <>
    <dialog {...props} className={`lanc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby="cofre-t">
      <div className="cofre">
        <h2 id="cofre-t">{g.regerado ? "Novos códigos de backup" : "Códigos de backup"}</h2>
        {!g.regerado && <p>Guarde esses códigos em local seguro. Cada um pode ser usado uma vez se você perder o app autenticador.</p>}
        <p className="warn"><i className="ph ph-warning" aria-hidden="true" /> {g.regerado ? "Os anteriores foram invalidados. Salve estes agora." : "Você não verá esses códigos novamente. Salve agora."}</p>
        <ul className="mfa-codigos">{g.codigos.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
        <p role="status" className="faint">{aviso}</p>
        <footer className="cofre-acoes">
          <button className="btn btn-ghost" type="button" onClick={copiar}>Copiar todos</button>
          <button className="btn btn-primary" type="button" onClick={concluir}>Concluído</button>
        </footer>
      </div>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}

export function Cofre() {
  const g = useSyncExternalStore(assinar, () => guardado);
  const tem = !!g;
  useEffect(() => {
    if (!tem) return;
    // Recarregar/fechar a aba com os códigos ainda não concluídos pergunta antes.
    const f = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", f);
    return () => window.removeEventListener("beforeunload", f);
  }, [tem]);
  return g?.aberto ? <Modal g={g} /> : null;
}
