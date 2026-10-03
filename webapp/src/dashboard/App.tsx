import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { monthTitle } from "./lib/format.js";
import { escolherMes, mesDe } from "./lib/store.js";
import type { DashState } from "./lib/types";
import { useDash } from "./useDash";
import { AskBar } from "./parts/AskBar";
import { Command } from "./parts/Command";
import { Guia, abrirGuia, navGuia } from "./parts/Guia";
import { Tip } from "./parts/Tip";
import { PAGES } from "./pages";
import { NO_MONTH, RAIL, TABBAR, href, route, useRoute, type Path } from "./router";
import { ICON } from "./lib/brand";
import { DEMO, MESES, contasQuery } from "./lib/v2";

// Quantos bancos têm conta não pausada na /api/v2/contas. Zero esconde a linha: a rota só
// traz conta BANK, e quem conectou só cartão ou investimento chega com `contas: []`.
// ponytail: contagem pelas contas; a exata é uma contagem de conexões vinda do servidor.
function RailSync() {
  const { data } = useQuery(contasQuery);
  const vivas = data?.contas.filter((c) => !c.motivos.includes("conexao_pausada")) ?? [];
  const n = new Set(vivas.map((c) => c.instituicao ?? `#${c.id}`)).size;
  if (!n) return null;
  return (
    <p className="rail-sync">
      <span className="dot-live" aria-hidden="true" />
      {`${n} ${n === 1 ? "banco" : "bancos"} via Open Finance`}
    </p>
  );
}

function Topbar({ s, path }: { s: DashState; path: Path }) {
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const on = () => setStuck(window.scrollY > 4);
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  const mes = mesDe(s);
  const i = MESES.indexOf(mes);
  return (
    <header className="topbar" data-stuck={stuck}>
      {!NO_MONTH.includes(path) && <div className="month-switch" role="group" aria-label="Mês exibido" data-guia="mes.seletor">
        <button className="icon-btn" type="button" aria-label="Mês anterior" disabled={i === 0} data-guia={i > 0 ? "mes.trocar" : undefined} onClick={() => escolherMes(MESES[i - 1])}><i className="ph ph-arrow-left" aria-hidden="true" /></button>
        <p className="month-title" aria-live="polite">{monthTitle(mes).replace(/ (\d{4})$/, "")}<span className="month-year"> {mes.slice(0, 4)}</span></p>
        <button className="icon-btn" type="button" aria-label="Próximo mês" disabled={i === MESES.length - 1} data-guia={i === 0 ? "mes.trocar" : undefined} onClick={() => escolherMes(MESES[i + 1])}><i className="ph ph-arrow-right" aria-hidden="true" /></button>
      </div>}
      <span className="topbar-spacer" />
      <button className="cmd-trigger" type="button" aria-label="Buscar ou ir para" aria-keyshortcuts="Meta+K Control+K /" onClick={() => window.dispatchEvent(new Event("dash:command"))}>
        <i className="ph ph-magnifying-glass" aria-hidden="true" />
        <span>Buscar ou ir para…</span>
        <kbd>⌘K</kbd>
      </button>
      <a className="btn btn-primary" href={href("/ferramentas")} aria-current={path === "/ferramentas" ? "page" : undefined}><i className="ph ph-wrench" aria-hidden="true" />Ferramentas</a>
    </header>
  );
}

export function App() {
  const s = useDash() as DashState;
  const path = useRoute();
  const Page = PAGES[path];
  const first = useRef(true);

  // A cada troca de página: título da aba e foco no h1 (leitor de tela anuncia a página nova).
  useEffect(() => {
    document.title = `PigBank · ${route(path).label}`;
    if (first.current) { first.current = false; return; }
    document.getElementById("page-title")?.focus({ preventScroll: true });
  }, [path]);

  return (
    <>
      <a className="skip" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("page-title")?.focus(); }}>Pular para o conteúdo</a>
      {/* Logo no começo do DOM: o balão do guia fica no começo da ordem de leitura (o Tab, com o véu, é dele). */}
      <Guia s={s} path={path} />
      <div className="shell">
        <nav className="rail" aria-label="Páginas do painel">
          <a className="brand" href={href("/")}>
            <img src={ICON} alt="" width={28} height={28} />
            <span>PigBank</span>
          </a>
          <ul className="rail-list">
            {RAIL.map(route).map((r) => (
              <li key={r.path}>
                <a href={href(r.path)} aria-current={path === r.path ? "page" : undefined} title={r.label} data-guia={navGuia(r.path)}>
                  <i className={`ph ${r.icon}`} aria-hidden="true" /><span className="rail-label">{r.label}</span>
                </a>
              </li>
            ))}
            {!DEMO && <li><button type="button" aria-label="Ajuda" title="Ajuda" onClick={abrirGuia}><i className="ph ph-question" aria-hidden="true" /><span className="rail-label">Ajuda</span></button></li>}
          </ul>
          <div className="rail-foot">
            <RailSync />
          </div>
        </nav>
        <div className="main-col">
          <Topbar s={s} path={path} />
          <main id="main" className="page" data-page={path}>
            <Page s={s} />
            {DEMO && <p className="foot">Protótipo com dados sintéticos. Nenhum valor aqui pertence a um usuário real.</p>}
          </main>
        </div>
      </div>
      <nav className={DEMO ? "tabbar" : "tabbar tabbar-ajuda"} aria-label="Páginas do painel">
        {TABBAR.map((p) => {
          const r = route(p);
          return (
            <a key={p} href={href(p)} aria-current={path === p ? "page" : undefined} data-tab={p === "/piggy" ? "piggy" : undefined} data-guia={navGuia(p)}>
              {p === "/piggy"
                ? <img src={ICON} alt="" width={26} height={26} />
                : <i className={`ph ${r.icon}`} aria-hidden="true" />}
              <span>{r.short}</span>
            </a>
          );
        })}
        {!DEMO && <button type="button" aria-label="Ajuda" onClick={abrirGuia}><i className="ph ph-question" aria-hidden="true" /><span>Ajuda</span></button>}
      </nav>
      <AskBar path={path} />
      <Tip />
      <Command />
    </>
  );
}
