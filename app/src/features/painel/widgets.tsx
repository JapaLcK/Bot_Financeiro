import { Contas } from "./contasResumo";
import { ResumoMes } from "./resumoMes";
import { Categorias, Calendario, Simulador } from "./gastos";
import { Previsao, Compromissos } from "./futuro";
import { Caixinhas, Metas, Patrimonio, Distribuicao, Rendimento } from "./patrimonio";
import { Faturas, Parcelas, Renda, Assinaturas, Notou } from "./complementos";
import { CardPeriodo } from "./cardPeriodo";
import { CardGastos } from "./cardGastos";
import { CardFimDoMes, CardMaiores } from "./cardMaiores";
import { CardComparacao } from "./cardComparacao";
import { CardLancamentos } from "./cardLancamentos";
import { usePainel } from "./provider";
import { useBloco } from "./blocoContexto";
import { Vazio } from "./base";
import { SEM_PLANO } from "./simuladorCompra";
import type { CardChat } from "@/api/schemas/painel";
const VIEWS = { contas: Contas, hero: Previsao, resumo: ResumoMes, categorias: Categorias, calendario: Calendario, simulador: Simulador, compromissos: Compromissos, piggy: Notou, metas: Metas, caixinhas: Caixinhas, patrimonio: Patrimonio, fatura: Faturas, wealth: Distribuicao, renda: Renda, rendimento: Rendimento, parcelas: Parcelas, assinaturas: Assinaturas };
export const planoPro = (d: ReturnType<typeof usePainel>["dados"]) => d.plano?.fase === "pronto" && d.plano.dado.plan_tier === "pro";
/** No chat o servidor já aplicou o plano (`plan_gate_ok`): o gate do simulador vale só FORA de um bloco. */
export function WidgetConteudo({ id }: { id: CardChat }) { const p = usePainel(); const bloco = useBloco();
 if (id === "periodo") return <CardPeriodo />;
 if (id === "maiores_gastos") return <CardMaiores />;
 if (id === "fim_do_mes") return <CardFimDoMes />;
 if (id === "comparacao") return <CardComparacao />;
 if (id === "ultimos_lancamentos" || id === "sem_categoria") return <CardLancamentos id={id} />;
 if (id === "categorias" && bloco) return <CardGastos />; // card do chat; o painel segue com `Categorias`
 if (id === "simulador" && !bloco) { const plano = p.dados.plano; if (!plano || plano.fase === "carregando") return <Vazio texto="Conferindo disponibilidade do simulador…" />; if (!planoPro(p.dados)) return <Vazio texto={SEM_PLANO} />; }
 const Componente = VIEWS[id]; return <Componente />; }
