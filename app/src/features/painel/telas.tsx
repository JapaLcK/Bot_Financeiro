import { useFocusEffect, type Href } from "expo-router";
import { useEffect, useState, type ReactNode } from "react";
import { TelaPainel } from "./cabecalho";
import { usePainel } from "./provider";
import { Acao, Bloco, Folha, Vazio, diasDoProvider, tituloWidget } from "./base";
import { type Widget } from "./catalogo";
import { WidgetConteudo, planoPro } from "./widgets";
import { Conversa } from "./conversa";
import { Extrato } from "./extrato";
import { NumerosMes } from "./numerosMes";
import { aoMetasMudarem } from "./metasEscrita";
import { useNavegacaoPainel } from "./navegacao";
export function TelaResumo() { const p = usePainel(); const [detalhe, setDetalhe] = useState<Widget | null>(null); return <TelaPainel titulo="Resumo" comReceitas>{!p.layout.length && <Vazio texto="Seu painel está vazio. Use Organizar para adicionar blocos." />}{p.layout.map((id) => <Bloco key={id} id={id} {...(id === "resumo" ? { ir: { destino: "Gastos", rota: "/gastos" as const } } : id === "hero" ? { detalhes: () => setDetalhe("hero") } : { abrir: () => setDetalhe(id) })}><WidgetConteudo id={id} /></Bloco>)}<Folha titulo={detalhe ? tituloWidget(detalhe, diasDoProvider(p.dados)) : "Detalhes"} aberta={!!detalhe} fechar={() => setDetalhe(null)}>{detalhe && <WidgetConteudo id={detalhe} />}</Folha></TelaPainel>; }
function CriarMeta() { const { navegar } = useNavegacaoPainel(); return <Acao rotulo="Criar meta" icone="Plus" somenteIcone onPress={() => navegar("/meta" as Href)} />; } // `navegar` entra uma vez só (o duplo toque não empilha duas folhas) e destrava ao voltar o foco
function SimularCompra() { const { navegar } = useNavegacaoPainel(); const p = usePainel(); return planoPro(p.dados) ? <Acao rotulo="Simular uma compra" icone="Plus" somenteIcone onPress={() => navegar("/simulador-compra" as Href)} /> : null; } // só Pro: quem não é Pro não vê o botão e nenhum POST sai
function TelaBlocos({ titulo, ids, topo }: { titulo: string; ids: Widget[]; topo?: ReactNode }) { return <TelaPainel titulo={titulo}>{topo}{ids.map((id) => <Bloco key={id} id={id} acao={id === "metas" ? <CriarMeta /> : id === "simulador" ? <SimularCompra /> : undefined}><WidgetConteudo id={id} /></Bloco>)}</TelaPainel>; }
export const TelaGastos = () => <TelaBlocos titulo="Gastos" topo={<NumerosMes />} ids={["categorias", "calendario", "simulador", "assinaturas"]} />;
export function TelaMetas() { const { relerMetas } = usePainel(); useFocusEffect(relerMetas); useEffect(() => aoMetasMudarem(() => relerMetas(true)), [relerMetas]); return <TelaBlocos titulo="Metas" ids={["metas", "patrimonio", "wealth", "rendimento"]} />; } // relê as metas a cada abertura: a meta pode ter sido criada no chat
export const TelaPiggy = Conversa;
export const TelaExtrato = () => <TelaPainel titulo="Extrato"><Extrato /></TelaPainel>;
