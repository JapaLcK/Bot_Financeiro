import { obj } from "../../lib/ia-cards";
import { lista } from "./legado";

// O card `piggy` ("Piggy notou", `{ insights: { ok, insights[] } }`): o que o Piggy destacou, do cache de 6 horas. Título e frase são texto do modelo (sempre como texto, nunca como HTML).
// Só leitura: o `action_view` do destaque não leva a lugar nenhum no chat (o app também o ignora). A chave do React é o índice, porque a `key` do modelo pode repetir.
type Destaque = { title: string; message: string };
const destaqueOk = (i: unknown) => obj(i) && typeof i.title === "string" && typeof i.message === "string";
export const contratoPiggy = (r: unknown) => { const d = (r as { insights?: unknown } | null)?.insights; return obj(d) && d.ok === true && lista(d.insights, destaqueOk); };

export function CardPiggy({ d }: { d: { insights: Destaque[] } }) {
  if (!d.insights.length) return <p className="ci-vazio">O Piggy ainda não encontrou um destaque para você.</p>;
  return (
    <ul className="ci-lista">
      {d.insights.map((i, n) => <li key={n} className="ci-conta ci-grupo"><b>{i.title}</b><p className="ci-leg">{i.message}</p></li>)}
    </ul>
  );
}
