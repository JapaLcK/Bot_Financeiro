import { useState } from "react";
import { View } from "react-native";
import { TelaPainel } from "./cabecalho";
import { usePainel } from "./provider";
import { Bloco, Folha, Vazio } from "./base";
import { TITULOS, type Widget } from "./catalogo";
import { WidgetConteudo } from "./widgets";
import { Conversa } from "./conversa";
import { Extrato } from "./extrato";
export function TelaResumo() { const p = usePainel(); const [detalhe, setDetalhe] = useState<Widget | null>(null); return <TelaPainel titulo="Resumo">{!p.layout.length && <Vazio texto="Seu painel está vazio. Use Organizar para adicionar blocos." />}{p.layout.map((id) => <Bloco key={id} id={id} abrir={() => setDetalhe(id)}><WidgetConteudo id={id} /></Bloco>)}<Folha titulo={detalhe ? TITULOS[detalhe] : "Detalhes"} aberta={!!detalhe} fechar={() => setDetalhe(null)}>{detalhe && <WidgetConteudo id={detalhe} />}</Folha></TelaPainel>; }
function TelaBlocos({ titulo, ids }: { titulo: string; ids: Widget[] }) { return <TelaPainel titulo={titulo}>{ids.map((id) => <Bloco key={id} id={id}><WidgetConteudo id={id} /></Bloco>)}</TelaPainel>; }
export const TelaGastos = () => <TelaBlocos titulo="Gastos" ids={["categorias", "calendario", "simulador", "assinaturas"]} />;
export const TelaMetas = () => <TelaBlocos titulo="Metas" ids={["metas", "patrimonio", "wealth", "rendimento"]} />;
export const TelaPiggy = () => <TelaPainel titulo="Piggy"><Bloco id="piggy"><WidgetConteudo id="piggy" /></Bloco><View style={{ gap: 16 }}><Conversa /></View></TelaPainel>;
export const TelaExtrato = () => <TelaPainel titulo="Extrato"><Extrato /></TelaPainel>;
