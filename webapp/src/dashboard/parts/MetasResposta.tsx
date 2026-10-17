import { useContext, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErroApi, metasQuery } from "../lib/v2";
import { href } from "../router";
import { SEM_SENHA, emOrdem } from "../widgets/Metas";
import { Anuncio, ERRO } from "./InvestidoResposta";
import { MetaPilula, PilulaSemReserva } from "./MetaPilula";

// O assunto `metas` do chat (GET /api/v2/metas, o mesmo dado da página Metas). Duas peças porque o cartão não cabe no <p class="msg-text"> nem no role=status:
// a frase vai no texto, os cartões vão no `card` do Answer (lib/topics.tsx). As duas leem
// a mesma consulta (uma requisição só). Nenhum R$ na tela: o dinheiro só entra no aria-label da pílula (MetaPilula.tsx).
// Pílulas à mostra (a da reserva, ou a linha "Monte no painel", incluída): o resto abre no "+N". Igual ao app.
const VISIVEIS = 5;

// Só conteúdo de linha (vai dentro do <p>). A cópia do role=status (Anuncio) só espelha: não consulta.
export function MetasResposta() {
  const q = useQuery({ ...metasQuery, enabled: metasQuery.enabled && !useContext(Anuncio) });
  if (q.isPending) return <>Calculando…</>;
  if (q.isError) {
    const e = q.error;
    return <>{!(e instanceof ErroApi) ? ERRO : e.code === "password_required" ? SEM_SENHA : e.status === 401 ? "Sua sessão terminou. Recarregue a página." : ERRO}</>;
  }
  const n = q.data.metas.length;
  return <>{n ? `Você tem ${n} ${n === 1 ? "meta" : "metas"}.` : "Você ainda não tem metas. Crie a primeira no painel."}</>;
}

const Abrir = ({ texto }: { texto: string }) => (
  <a className="link msg-open" href={href("/metas")}>{texto}<i className="ph ph-arrow-right" aria-hidden="true" /></a>
);

// As pílulas do app, só leitura. Carregando e erro não mostram nada (a frase já diz).
export function MetasCartoes() {
  const q = useQuery(metasQuery);
  const [aberto, setAberto] = useState(false);
  if (q.isPending || q.isError) return null;
  const { metas, custo_mensal } = q.data;
  const semReserva = !metas.some((m) => m.tipo === "reserva");
  const visiveis = VISIVEIS - Number(semReserva), resto = metas.length - visiveis; // a vaga da reserva é a linha "Monte no painel"
  return (
    <section className="metas-chat">
      <h2 className="sr-only">Suas metas</h2>
      <ul>
        {semReserva && <li><PilulaSemReserva /></li>}
        {(aberto ? emOrdem(metas) : emOrdem(metas).slice(0, visiveis)).map((m) => <li key={m.id}><MetaPilula m={m} custo={custo_mensal} /></li>)}
      </ul>
      {resto > 0 && (
        <button type="button" className="btn btn-ghost pilula-mais" aria-expanded={aberto} onClick={() => setAberto(!aberto)}
          aria-label={aberto ? "Mostrar menos metas" : `+${resto}, mostrar mais ${resto} ${resto === 1 ? "meta" : "metas"}`}>
          {aberto ? "Mostrar menos" : `+${resto}`}
        </button>
      )}
      <Abrir texto={!metas.length ? "Criar no painel" : resto > 0 ? `Ver todas as ${metas.length} metas no painel` : "Abrir no painel"} />
    </section>
  );
}
