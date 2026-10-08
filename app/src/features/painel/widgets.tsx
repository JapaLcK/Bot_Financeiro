import { Contas, ResumoMes } from "./contasResumo";
import { Categorias, Calendario, Simulador } from "./gastos";
import { Previsao, Compromissos } from "./futuro";
import { Metas, Patrimonio, Distribuicao, Rendimento } from "./patrimonio";
import { Faturas, Parcelas, Renda, Assinaturas, Notou } from "./complementos";
import { usePainel } from "./provider";
import { Vazio } from "./base";
import type { Widget } from "./catalogo";
const VIEWS = { contas: Contas, hero: Previsao, resumo: ResumoMes, categorias: Categorias, calendario: Calendario, simulador: Simulador, compromissos: Compromissos, piggy: Notou, metas: Metas, patrimonio: Patrimonio, fatura: Faturas, wealth: Distribuicao, renda: Renda, rendimento: Rendimento, parcelas: Parcelas, assinaturas: Assinaturas };
export function WidgetConteudo({ id }: { id: Widget }) { const p = usePainel();
 if (id === "simulador") { const plano = p.dados.plano; if (!plano || plano.fase === "carregando") return <Vazio texto="Conferindo disponibilidade do simulador…" />; if (plano.fase !== "pronto" || plano.dado.plan_tier !== "pro") return <Vazio texto="Simulador indisponível no seu plano." />; }
 const Componente = VIEWS[id]; return <Componente />; }
