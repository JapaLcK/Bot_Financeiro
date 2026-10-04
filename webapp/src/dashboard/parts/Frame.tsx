import { createContext, useContext, type ReactNode } from "react";
import { DEMO } from "../lib/v2";
import { Demonstracao } from "./Selos";
import { href, route, type Path } from "../router";

// No Resumo cada bloco aponta para a sua página; nas páginas o contexto fica vazio.
export const FrameLink = createContext<Path | null>(null);
// Prefixo dos ids: a conversa do Piggy pode mostrar o mesmo bloco em várias respostas.
export const FrameScope = createContext("");

// Moldura comum dos widgets: título, ação à direita e corpo. Bloco que não é `real` (o dado
// ainda é inventado) leva o selo "demonstração"; no protótipo, todos levam.
export function Frame({ id, title, aside, children, className = "", real = false }: {
  id: string;
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  real?: boolean;
}) {
  const to = useContext(FrameLink);
  const key = `${useContext(FrameScope)}w-${id}`;
  return (
    <article className={`w ${className}`} id={key} aria-labelledby={`${key}-h`}>
      <header className="w-head">
        <h2 id={`${key}-h`} className="w-title">
          <span>{title}</span>
          {(!real || DEMO) && <Demonstracao />}
        </h2>
        {aside && <div className="w-aside">{aside}</div>}
        {to && (
          <a className="w-open" href={href(to)} aria-label={`Abrir ${route(to).label}`}>
            <i className="ph ph-arrow-right" aria-hidden="true" />
          </a>
        )}
        <svg className="w-grip" width="10" height="16" viewBox="0 0 10 16" aria-hidden="true">
          {[3, 8, 13].map((y) => [2.5, 7.5].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.4" />))}
        </svg>
      </header>
      <div className="w-body">{children}</div>
    </article>
  );
}
