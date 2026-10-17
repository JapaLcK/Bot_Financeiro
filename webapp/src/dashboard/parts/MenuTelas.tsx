import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { DEMO, guiaQuery, sair } from "../lib/v2";
import { CONTA, RAIL, href, route, useRoute } from "../router";
import { abrirDica, dicaDe } from "./Dica";
import { abrirGuia } from "./Guia";
import { NATIVE, hide, isOpen, show } from "./dialog";

// O "Menu" da barra de baixo (celular): uma folha com todas as telas e a Ajuda. Abre pelo evento
// `dash:menu` (App.tsx). Na Ajuda, as mesmas ações da Ajuda do trilho (parts/Ajuda.tsx), sem o
// popover: dentro de um <dialog> modal o menu dela ficaria preso na camada de cima.
export function MenuTelas() {
  const dlg = useRef<HTMLDialogElement>(null);
  const path = useRoute();
  const dica = !!dicaDe(useQuery(guiaQuery).data, path);
  // Só fecha o que está aberto: o `hide` do fallback mexe na trava de foco, que é uma só (dialog.ts).
  const fechar = () => { if (isOpen(dlg.current)) hide(dlg.current); };
  const ajuda = (abrir: () => void) => () => { fechar(); abrir(); };

  useEffect(() => {
    const abrir = () => {
      // O Fechar cobre a barra inteira, que cresce com o texto ampliado: herda a altura real dela (getBoundingClientRect, não arredondada).
      const barra = document.querySelector(".tabbar");
      if (barra) dlg.current?.style.setProperty("--tabbar-h", barra.getBoundingClientRect().height + "px");
      show(dlg.current, "a, button"); dlg.current?.querySelector<HTMLElement>('[aria-current="page"]')?.focus();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !NATIVE && isOpen(dlg.current)) hide(dlg.current); };
    window.addEventListener("dash:menu", abrir);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("dash:menu", abrir); window.removeEventListener("keydown", onKey); };
  }, []);
  useEffect(fechar, [path]); // a rota mudou por fora (voltar do navegador, Cmd-K)

  return (<>
    <dialog className={NATIVE ? "menu-telas" : "menu-telas menu-telas-fb"} ref={dlg} role={NATIVE ? undefined : "dialog"} aria-modal={NATIVE ? undefined : true} aria-label="Menu" onClick={(e) => { if (e.target === dlg.current) fechar(); }}>
      <div className="menu-telas-body">
        <h2>Menu</h2>
        {!DEMO && <div role="group" aria-labelledby="menu-ajuda">
          <p id="menu-ajuda" className="menu-telas-sec">Ajuda</p>
          <ul>
            <li><button type="button" onClick={ajuda(abrirGuia)}><i className="ph ph-question" aria-hidden="true" /><span>Guia do painel</span></button></li>
            {dica && <li><button type="button" onClick={ajuda(abrirDica)}><i className="ph ph-info" aria-hidden="true" /><span>Como funciona esta tela</span></button></li>}
          </ul>
        </div>}
        <ul>
          {RAIL.map(route).map((r) => (
            <li key={r.path}>
              <a href={href(r.path)} aria-current={path === r.path ? "page" : undefined} onClick={fechar}>
                <i className={`ph ${r.icon}`} aria-hidden="true" /><span>{r.label}</span>
              </a>
            </li>
          ))}
        </ul>
        {!DEMO && <div role="group" className="menu-telas-conta" aria-labelledby="menu-conta">
          <p id="menu-conta" className="menu-telas-sec">Conta</p>
          <ul>
            {CONTA.map(route).map((r) => (
              <li key={r.path}>
                <a href={href(r.path)} aria-current={path === r.path ? "page" : undefined} onClick={fechar}>
                  <i className={`ph ${r.icon}`} aria-hidden="true" /><span>{r.label}</span>
                </a>
              </li>
            ))}
            <li><button type="button" onClick={sair}><i className="ph ph-sign-out" aria-hidden="true" /><span>Sair</span></button></li>
          </ul>
        </div>}
        {/* Fecha na faixa de baixo, onde o dedo estava ao abrir: o duplo toque no "Menu" da barra vira abrir+fechar. */}
        <button type="button" className="menu-telas-fechar" onClick={fechar}>Fechar</button>
      </div>
    </dialog>
    {!NATIVE && <div className="cmdk-scrim" aria-hidden="true" onClick={fechar} />}
  </>);
}
