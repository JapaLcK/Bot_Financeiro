import { useState } from "react";
import { useNavegacaoPainel } from "./navegacao";
import { View } from "react-native";
import { TextoPainel as Texto } from "./tipografia";
import { RecursoDados, Valor, Linha, Avisos, Acao, Vazio } from "./base";
import { dataCurta, mesAtual } from "./catalogo";
import { useBloco } from "./blocoContexto";
import { LancamentosDe, LinhaExpansivel } from "./expansao";
import { usePainel } from "./provider";
import { useTema } from "@/ui/tema";
import { BarraComposicao } from "@/ui/componentes/BarraComposicao";
import { segmentosDeContas } from "./composicaoContas";
// No chat (dentro de um bloco) a conta não navega: expande os lançamentos DELA no mês atual, 10 por vez; "Bancos conectados" some.
export function Contas() { const { navegar, entrando } = useNavegacaoPainel(); const { oculto } = usePainel(); const { cores } = useTema(); const bloco = useBloco(); const [aberta, setAberta] = useState<number | null>(null); return <RecursoDados nome="contas">{(d) => <View style={{ gap: 8 }}><Texto variante="legenda" tom="inkMuted">Disponível agora</Texto><Valor valor={d.total} destaque /><Avisos motivos={d.motivos} />{!oculto && <BarraComposicao segmentos={segmentosDeContas(d, cores)} />}<Linha titulo="Carteira manual" valor={d.carteira.saldo} />{d.contas.map((c) => { const linha = <Linha titulo={c.instituicao || c.nome || "Conta bancária"} valor={c.moeda === "BRL" ? c.saldo : undefined} detalhe={[c.nome, c.moeda !== "BRL" ? `Saldo em ${c.moeda} fora do total` : null, c.sincronizado_em ? `Atualizado ${dataCurta(c.sincronizado_em)}` : "Sem atualização informada"].filter(Boolean).join(" · ")} onPress={bloco ? undefined : () => navegar("/conexoes")} />; return bloco ? <LinhaExpansivel key={c.id} linha={linha} aberta={aberta === c.id} alternar={() => setAberta(aberta === c.id ? null : c.id)}><><Texto variante="legenda" tom="inkMuted">Lançamentos deste mês</Texto><LancamentosDe consulta={new URLSearchParams({ conta: String(c.id), mes: mesAtual(), limite: "10" }).toString()} /></></LinhaExpansivel> : <View key={c.id}>{linha}</View>; })}{!d.contas.length && <Vazio texto="Nenhum banco conectado." />}{d.fora_do_total > 0 && <Texto variante="legenda" tom="warning">Há contas fora do total disponível.</Texto>}{!bloco && <Acao rotulo="Bancos conectados" desativado={entrando} onPress={() => navegar("/conexoes")} icone="Wallet" iconeFim="CaretRight" />}</View>}</RecursoDados>; }
