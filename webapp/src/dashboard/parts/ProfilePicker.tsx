import { useEffect, useRef } from "react";
import { PROFILES } from "../lib/profiles.js";
import { NATIVE, hide, isOpen, show } from "./dialog";

// Primeira visita ao Resumo: escolher o perfil que monta o painel. Esc, clique fora
// e "Pular" dão o painel padrão, e a escolha fica lembrada do mesmo jeito.
// Mora dentro de #pigbank-dashboard (sem portal): o Tailwind do painel tem escopo lá.
export function ProfilePicker({ onPick }: { onPick: (p: string) => void }) {
  const dlg = useRef<HTMLDialogElement>(null);
  const done = useRef(false);
  // Toda saída passa aqui uma vez. O Esc nativo sem gesto do usuário antes (Chrome) fecha
  // sem `cancel`: por isso o fechar escuta o `close`, não o `cancel`.
  const pick = (p: string) => { if (done.current) return; done.current = true; hide(dlg.current); onPick(p); };

  useEffect(() => {
    const d = dlg.current;
    show(d, "button");
    if (!NATIVE) d?.querySelector("button")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !NATIVE && isOpen(d)) pick("padrao"); };
    window.addEventListener("keydown", onKey);
    // Desmontado aberto (voltar do navegador): o nativo sai da camada de cima sozinho; o
    // fallback precisa soltar a trava de foco.
    return () => { window.removeEventListener("keydown", onKey); if (!NATIVE && isOpen(d)) hide(d); };
  }, []);

  return (<>
    <dialog ref={dlg} className={NATIVE ? "picker" : "picker picker-fb"} role={NATIVE ? undefined : "dialog"} aria-modal={NATIVE ? undefined : true}
      aria-labelledby="picker-title" aria-describedby="picker-lede"
      onClose={() => pick("padrao")}
      onClick={(e) => { if (e.target === dlg.current) pick("padrao"); }}>
      <h2 id="picker-title">O que você quer ver primeiro?</h2>
      <p id="picker-lede" className="picker-lede">A gente monta o Resumo pro seu momento. Dá pra trocar e mexer nos blocos quando quiser.</p>
      <ul className="picker-list">
        {PROFILES.map((p) => (
          <li key={p.id}>
            <button type="button" className="picker-card" onClick={() => pick(p.id)}>
              <i className={`ph ${p.icon}`} aria-hidden="true" />
              <span className="picker-name">{p.label}</span>
              <span className="picker-line">{p.line}</span>
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="btn btn-quiet picker-skip" onClick={() => pick("padrao")}>Pular, ver painel padrão</button>
    </dialog>
    {!NATIVE && <div className="picker-scrim" aria-hidden="true" onClick={() => pick("padrao")} />}
  </>);
}
