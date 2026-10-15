// O que os três blocos de "Para onde vai" (widgets/Gastos*.tsx) mostram antes de ter número:
// carregando, erro e mês fora do histórico. `null` = há dado para desenhar.
import type { ReactNode } from "react";

type Consulta = { data?: { disponivel: boolean }; isPending: boolean; refetch: () => unknown };

export function estadoGastos(q: Consulta): ReactNode {
  if (!q.data) {
    return q.isPending ? <p role="status" className="faint">Carregando…</p> : (
      <div className="empty" role="alert">
        <p>Não deu para carregar.</p>
        <button type="button" className="btn retry btn-ghost" onClick={() => q.refetch()}>Tentar de novo</button>
      </div>
    );
  }
  // Mês todo antes do corte do plano: o servidor não manda total, e R$ 0 seria mentira.
  return q.data.disponivel ? null : <p className="faint">Este mês está fora do seu histórico.</p>;
}

// O total é o subtotal em reais: a saída em outra moeda não entra na soma.
export const AvisoMoeda = ({ n }: { n: number }) => n > 0
  ? <p className="w-lede">{n === 1 ? "1 saída em outra moeda ficou fora deste total" : `${n} saídas em outra moeda ficaram fora deste total`}</p>
  : null;
