import { View } from "react-native";

import { Folha } from "@/features/painel/base";
import { corDoBanco } from "@/features/painel/composicaoContas";
import { Avatar } from "@/ui/componentes/Avatar";
import { Banner } from "@/ui/componentes/Banner";
import { BarraComposicao } from "@/ui/componentes/BarraComposicao";
import { Button } from "@/ui/componentes/Button";
import { ConnectionStatus } from "@/ui/componentes/ConnectionStatus";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";
import { Money } from "@/ui/componentes/Money";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { claro, espaco } from "@/ui/tokens";

import { logoDoBanco, type Item } from "./juntar";
import { tempoRelativo } from "./tempoRelativo";

/** Mesmo texto de webapp/src/dashboard/conta/BancoDetalhe.tsx; `tests/frontend/bancos_paridade.test.mjs` compara. */
export const FAIXA_BC = "Conexão regulamentada pelo Banco Central, oficial pelo Open Finance.";

interface Props {
  item: Item;
  indice: number;
  /** `false`: `/api/app/bancos` não veio; o detalhe não mostra valor nem contagens. */
  numeros: boolean;
  oculto: boolean;
  agora?: number;
  podeReconectar: boolean;
  /** Outra tela já está abrindo (entrada reservada): nenhuma ação nova. */
  entrando: boolean;
  /** Id da conexão cuja remoção está em voo, ou null. Reconectar fica travado com qualquer uma; Desconectar, só nas OUTRAS. */
  removendoId: number | null;
  /** A falha da última ação: a folha cobre a tela, então o erro tem de aparecer aqui dentro. */
  falha: string | null;
  fechar: () => void;
  aoAcompanhar: () => void;
  aoReconectar: () => void;
  aoDesconectar: () => void;
}

/** Um elemento só, com o rótulo do web ("2 contas"): sem ele o VoiceOver lê o número solto. Ícone e número ficam cobertos pelo rótulo do pai. */
const Contagem = ({ icone, n, um, varios }: { icone: NomeIcone; n: number | null; um: string; varios: string }) => n ? (
  <View accessible accessibilityLabel={`${n} ${n === 1 ? um : varios}`} style={{ flexDirection: "row", alignItems: "center", gap: espaco.xs }}>
    <Icone nome={icone} tamanho={16} tom="inkMuted" />
    <Texto variante="legenda" tom="inkMuted" numerico>{n}</Texto>
  </View>
) : null;

/** O detalhe de um banco, numa folha: total (o do SERVIDOR, o app não soma), composição, contagens, última atualização, faixa do Banco Central e as ações. `null` vira "—" (o `Money` escreve), nunca R$ 0,00. */
export function BancoDetalhe({ item, indice, numeros, oculto, agora, podeReconectar, entrando, removendoId, falha, fechar, aoAcompanhar, aoReconectar, aoDesconectar }: Props) {
  const { cores } = useTema();
  const { conexao: c, banco: b } = item;
  const nome = c.institution_name ?? "Seu banco";
  const nomeAcessivel = c.institution_name ?? "banco"; // os botões leem "Desconectar banco", não "Desconectar Seu banco"
  const estado = c.ui.state;
  const removendo = removendoId === c.id;
  const reconectavel = !!c.provider_item_id && podeReconectar && !["removed", "item_missing", "paused"].includes(estado);
  const emDestaque = reconectavel && estado === "needs_user_action";
  const acompanhavel = estado === "updating" && !!c.provider_item_id;
  const aConferir = !!b && b.motivos.some((m) => m !== "plano_sem_investimentos");
  const split = !oculto && b && b.saldo_conta !== null && b.investido !== null && b.saldo_conta > 0 && b.investido > 0;
  const valor = (centavos: number | null | undefined) => <Money centavos={centavos ?? Number.NaN} oculto={oculto} variante="rotulo" />;
  return (
    <Folha titulo={nome} aberta fechar={fechar}>
      <View style={{ gap: espaco.lg }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.md }}>
          <Avatar nome={nome} imagem={logoDoBanco(c)} tamanho={64} decorativo fundo={claro.bg} letra={corDoBanco(nome, indice, claro)} />
          <View style={{ flex: 1 }}><ConnectionStatus estado={estado} label={c.ui.label} detalhe={estado !== "updated" ? c.ui.detail ?? undefined : undefined} /></View>
        </View>
        {numeros ? (
          <View style={{ gap: espaco.md }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm }}>
              <Money centavos={b?.total ?? Number.NaN} oculto={oculto} variante="display" />
              {aConferir && !oculto ? <View accessible accessibilityLabel="a conferir"><Icone nome="WarningCircle" tamanho={20} tom="warning" /></View> : null}
            </View>
            {split ? <BarraComposicao segmentos={[{ valor: b.saldo_conta!, cor: cores.ink }, { valor: b.investido!, cor: cores.brand }]} /> : null}
          </View>
        ) : null}
        <GrupoDeLinhas>
          <ListRow titulo="Última atualização" trailing={<Texto tom="inkMuted">{c.last_sync_at ? tempoRelativo(c.last_sync_at, agora) : "Ainda não sincronizou"}</Texto>} />
          {numeros ? <ListRow titulo="Em conta" trailing={valor(b?.saldo_conta)} /> : null}
          {numeros ? <ListRow titulo="Investido" trailing={valor(b?.investido)} /> : null}
        </GrupoDeLinhas>
        {numeros && b ? (
          <View style={{ flexDirection: "row", gap: espaco.md }}>
            <Contagem icone="Bank" n={b.contas} um="conta" varios="contas" />
            <Contagem icone="CreditCard" n={b.cartoes} um="cartão" varios="cartões" />
            <Contagem icone="TrendUp" n={b.investimentos} um="investimento" varios="investimentos" />
          </View>
        ) : null}
        <Banner tom="info" mensagem={FAIXA_BC} />
        {falha ? <Banner tom="danger" mensagem={falha} /> : null}
        {emDestaque ? <Button rotulo={`Reconectar ${nomeAcessivel}`} desativado={entrando || removendoId !== null} onPress={aoReconectar} /> : null}
        {acompanhavel ? <Button rotulo="Acompanhar sincronização" variante="secondary" desativado={entrando} onPress={aoAcompanhar} /> : null}
        <View style={{ flexDirection: "row", gap: espaco.md }}>
          {reconectavel && !emDestaque ? <View style={{ flex: 1 }}><Button rotulo={`Reconectar ${nomeAcessivel}`} variante="secondary" icone="ArrowsClockwise" desativado={entrando || removendoId !== null} onPress={aoReconectar} /></View> : null}
          <View style={{ flex: 1 }}><Button rotulo={`Desconectar ${nomeAcessivel}`} variante="ghost" icone="Trash" carregando={removendo} desativado={entrando || (removendoId !== null && !removendo)} onPress={aoDesconectar} /></View>
        </View>
      </View>
    </Folha>
  );
}
