import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { contaQuery } from "../lib/v2";
import { route, type Path } from "../router";

// Página-ponte da Conta: mostra o que se lê barato (nome e e-mail) e leva à tela antiga, que segue
// valendo até o conteúdo real chegar aqui. O nome do componente leva o prefixo `Conta`: o `/perfil`
// do v2 é a persona do Resumo, outra coisa.
export function ContaPonte({ path, destino, acao, children }: { path: Path; destino: string; acao: string; children?: ReactNode }) {
  const { data } = useQuery(contaQuery);
  const texto = (v: unknown) => (typeof v === "string" ? v.trim() : "");
  const nome = texto(data?.display_name), email = texto(data?.email);
  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>{route(path).title}</h1>
      </header>
      <div className="page-grid">
        {children}
        <section className="panel span-12 conta-ponte" aria-label="Sua conta">
          {nome && <b>{nome}</b>}
          {email && <span>{email}</span>}
          <a className="btn btn-ghost" href={destino}>{acao}<i className="ph ph-arrow-right" aria-hidden="true" /></a>
        </section>
      </div>
    </>
  );
}
