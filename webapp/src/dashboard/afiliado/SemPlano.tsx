import { useEffect } from "react";
import { ICON } from "../lib/brand";
import { sair } from "../lib/v2";
import { AreaAfiliado } from "./Afiliado";

// O GET /afiliado: a mesma tela, sem o resto do painel, para quem é afiliado e está sem plano. Só fala com /api/affiliate e /auth (a /api/v2 daria 402).
// "Ver planos" é link para a /precos de propósito: o auth-refresh.js esconde esses links no app iOS (diretriz 3.1.1).
export function PaginaSemPlano() {
  useEffect(() => { document.title = "PigBank · Afiliado"; }, []);
  return <div className="afil-so">
    <header className="afil-so-topo">
      <span className="afil-so-marca"><img src={ICON} alt="" width={28} height={28} />PigBank</span>
      <a className="btn btn-ghost" href="/precos?escolha=1">Ver planos</a>
      <button type="button" className="btn btn-ghost" onClick={sair}>Sair</button>
    </header>
    <main id="main" className="page" data-page="/afiliado"><AreaAfiliado /></main>
  </div>;
}
