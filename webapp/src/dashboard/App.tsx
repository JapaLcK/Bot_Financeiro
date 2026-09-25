import { useEffect, useRef, useState } from "react";
import { MONTHS } from "./lib/api";
import { monthTitle } from "./lib/format.js";
import { set } from "./lib/store.js";
import type { DashState } from "./lib/types";
import { useDash } from "./useDash";
import { Command } from "./parts/Command";
import { Tip } from "./parts/Tip";
import { PAGES } from "./pages";
import { NO_MONTH, RAIL, TABBAR, href, route, useRoute, type Path } from "./router";

function Topbar({ s, path }: { s: DashState; path: Path }) {
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const on = () => setStuck(window.scrollY > 4);
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  const i = MONTHS.indexOf(s.month);
  return (
    <header className="topbar" data-stuck={stuck}>
      {!NO_MONTH.includes(path) && <div className="month-switch" role="group" aria-label="Mês exibido">
        <button className="icon-btn" type="button" aria-label="Mês anterior" disabled={i === 0} onClick={() => set({ month: MONTHS[i - 1] })}><i className="ph ph-arrow-left" aria-hidden="true" /></button>
        <p className="month-title" aria-live="polite">{monthTitle(s.month)}</p>
        <button className="icon-btn" type="button" aria-label="Próximo mês" disabled={i === MONTHS.length - 1} onClick={() => set({ month: MONTHS[i + 1] })}><i className="ph ph-arrow-right" aria-hidden="true" /></button>
      </div>}
      <span className="tag-demo">Demonstração</span>
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
      <div className="shell">
        <nav className="rail" aria-label="Páginas do painel">
          <a className="brand" href={href("/")}>
            <img src="../frontend/brand/icon.png" alt="" width={28} height={28} />
            <span>PigBank</span>
          </a>
          <ul className="rail-list">
            {RAIL.map(route).map((r) => (
              <li key={r.path}>
                <a href={href(r.path)} aria-current={path === r.path ? "page" : undefined} title={r.label}>
                  <i className={`ph ${r.icon}`} aria-hidden="true" /><span className="rail-label">{r.label}</span>
                </a>
              </li>
            ))}
          </ul>
          <div className="rail-foot">
            <p className="rail-sync"><span className="dot-live" aria-hidden="true" />2 bancos via Open Finance</p>
            <p className="faint">Dados de demonstração</p>
          </div>
        </nav>
        <div className="main-col">
          <Topbar s={s} path={path} />
          <main id="main" className="page" data-page={path}>
            <Page s={s} />
            <p className="foot">Protótipo com dados sintéticos. Nenhum valor aqui pertence a um usuário real.</p>
          </main>
        </div>
      </div>
      <nav className="tabbar" aria-label="Páginas do painel">
        {TABBAR.map((p) => {
          const r = route(p);
          return (
            <a key={p} href={href(p)} aria-current={path === p ? "page" : undefined} data-tab={p === "/piggy" ? "piggy" : undefined}>
              {p === "/piggy"
                ? <img src="../frontend/brand/avatar.webp" alt="" width={30} height={30} />
                : <i className={`ph ${r.icon}`} aria-hidden="true" />}
              <span>{r.short}</span>
            </a>
          );
        })}
      </nav>
      <Tip />
      <Command />
    </>
  );
}
