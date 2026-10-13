import { useNavegacaoPainel } from "./navegacao";
import { View } from "react-native";
import { TextoPainel as Texto } from "./tipografia";
import { RecursoDados, Valor, Linha, Avisos, Acao, Vazio } from "./base";
import { dataCurta } from "./catalogo";
import { usePainel } from "./provider";
import { useTema } from "@/ui/tema";
import { BarraComposicao } from "@/ui/componentes/BarraComposicao";
import { segmentosDeContas } from "./composicaoContas";
export function Contas() { const { navegar, entrando } = useNavegacaoPainel(); const { oculto } = usePainel(); const { cores } = useTema(); return <RecursoDados nome="contas">{(d) => <View style={{ gap: 8 }}><Texto variante="legenda" tom="inkMuted">Disponível agora</Texto><Valor valor={d.total} destaque /><Avisos motivos={d.motivos} />{!oculto && <BarraComposicao segmentos={segmentosDeContas(d, cores)} />}<Linha titulo="Carteira manual" valor={d.carteira.saldo} />{d.contas.map((c) => <Linha key={c.id} titulo={c.instituicao || c.nome || "Conta bancária"} valor={c.moeda === "BRL" ? c.saldo : undefined} detalhe={[c.nome, c.moeda !== "BRL" ? `Saldo em ${c.moeda} fora do total` : null, c.sincronizado_em ? `Atualizado ${dataCurta(c.sincronizado_em)}` : "Sem atualização informada"].filter(Boolean).join(" · ")} onPress={() => navegar("/conexoes")} />)}{!d.contas.length && <Vazio texto="Nenhum banco conectado." />}{d.fora_do_total > 0 && <Texto variante="legenda" tom="warning">Há contas fora do total disponível.</Texto>}<Acao rotulo="Bancos conectados" desativado={entrando} onPress={() => navegar("/conexoes")} icone="Wallet" iconeFim="CaretRight" /></View>}</RecursoDados>; }
