import { Pressable, View } from "react-native";

import { corDoBanco } from "@/features/painel/composicaoContas";
import { Avatar } from "@/ui/componentes/Avatar";
import { BarraComposicao } from "@/ui/componentes/BarraComposicao";
import { Button } from "@/ui/componentes/Button";
import { Card } from "@/ui/componentes/Card";
import { ConnectionStatus } from "@/ui/componentes/ConnectionStatus";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { Money } from "@/ui/componentes/Money";
import { Texto } from "@/ui/componentes/Texto";
import { faladoComSinal, VALOR_INDISPONIVEL_FALADO, VALOR_OCULTO_FALADO } from "@/ui/dinheiro";
import { useTema } from "@/ui/tema";
import { claro, espaco, texto } from "@/ui/tokens";

import { logoSeguro, type Item } from "./juntar";
import { tempoRelativo } from "./tempoRelativo";

interface Props {
  item: Item;
  /** `false`: `/api/app/bancos` não veio; o cartão mostra só nome, estado e hora (como antes), sem valor nem contagens. */
  numeros: boolean;
  oculto: boolean;
  aberto: boolean;
  alternar: () => void;
  /** Posição na lista: escolhe a tinta quando o banco não tem cor própria. */
  indice: number;
  podeReconectar: boolean;
  /** Outra tela já está abrindo (entrada reservada): nenhuma ação nova. */
  entrando: boolean;
  /** Id da conexão cuja remoção está em voo, ou null. Reconectar fica travado com qualquer uma; Desconectar, só nas OUTRAS. */
  removendoId: number | null;
  aoAcompanhar: () => void;
  aoReconectar: () => void;
  aoDesconectar: () => void;
}

/** O valor de uma parte: `null` é "—" (o `Money` já o escreve assim para o que não é inteiro seguro), nunca R$ 0,00. */
function Valor({ centavos, oculto, variante = "corpo" }: { centavos: number | null; oculto: boolean; variante?: "corpo" | "rotulo" }) {
  return <Money centavos={centavos ?? Number.NaN} oculto={oculto} variante={variante} />;
}

function Contagem({ icone, n }: { icone: NomeIcone; n: number | null }) {
  return n ? (
    <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.xs }}>
      <Icone nome={icone} tamanho={16} tom="inkMuted" />
      <Texto variante="legenda" tom="inkMuted" numerico>{n}</Texto>
    </View>
  ) : null;
}

function Parte({ icone, rotulo, centavos, oculto }: { icone: NomeIcone; rotulo: string; centavos: number | null; oculto: boolean }) {
  const { cores } = useTema();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.md, minHeight: 44 }}>
      <View style={{ width: 30, height: 30, borderRadius: 9, alignItems: "center", justifyContent: "center", backgroundColor: cores.surfaceControl }}>
        <Icone nome={icone} tamanho={16} tom="inkMuted" />
      </View>
      <Texto style={{ flex: 1 }}>{rotulo}</Texto>
      <Valor centavos={centavos} oculto={oculto} />
    </View>
  );
}

function falado(item: Item, numeros: boolean, oculto: boolean, aConferir: boolean): string {
  if (!numeros) return "";
  if (oculto) return `, ${VALOR_OCULTO_FALADO.toLowerCase()}`;
  const total = item.banco?.total;
  return `, ${total === null || total === undefined ? VALOR_INDISPONIVEL_FALADO : faladoComSinal(total)}${aConferir ? ", a conferir" : ""}`; // o aviso que o ícone mostra
}

/**
 * O que importa fica à mostra (logo, nome, estado, total, há quanto tempo); o resto abre aqui mesmo, sem folha. O nome acessível do cabeçalho é
 * "banco, estado, total, a conferir (quando o ícone de aviso aparece)". O rodapé (hora e seta) é um segundo alvo de toque para o MESMO gesto, sem papel de botão: o leitor de tela lê a hora e usa o cabeçalho para abrir. O botão que a situação pede (`Reconectar` quando o banco
 * pede ação, `Acompanhar sincronização` enquanto atualiza) fica FORA do que abre: é a razão de o usuário olhar o cartão.
 */
export function BancoCard({ item, numeros, oculto, aberto, alternar, indice, podeReconectar, entrando, removendoId, aoAcompanhar, aoReconectar, aoDesconectar }: Props) {
  const { cores } = useTema();
  const { conexao: c, banco: b } = item;
  const removendo = removendoId === c.id;
  const nome = c.institution_name ?? "Seu banco";
  const nomeAcessivel = c.institution_name ?? "banco"; // os botões leem "Desconectar banco", não "Desconectar Seu banco"
  const estado = c.ui.state;
  const reconectavel = !!c.provider_item_id && podeReconectar && !["removed", "item_missing", "paused"].includes(estado);
  const emDestaque = reconectavel && estado === "needs_user_action";
  const acompanhavel = estado === "updating" && !!c.provider_item_id;
  const aConferir = !!b && b.motivos.some((m) => m !== "plano_sem_investimentos");
  const conta = b && (b.saldo_conta !== null || b.contas > 0), invest = b && (b.investido !== null || !!b.investimentos);
  const split = !oculto && b && b.saldo_conta !== null && b.investido !== null && b.saldo_conta > 0 && b.investido > 0;
  return (
    <Card>
      <View style={{ gap: espaco.md }}>
        <Pressable accessibilityRole="button" accessibilityState={{ expanded: aberto }} accessibilityLabel={`${nome}, ${c.ui.label}${falado(item, numeros, oculto, aConferir)}`} onPress={alternar} style={{ flexDirection: "row", alignItems: "center", gap: espaco.md, minHeight: 44 }}>
          <Avatar nome={nome} imagem={logoSeguro(c.logo_url)} tamanho={44} decorativo fundo={claro.bg} letra={corDoBanco(nome, indice, claro)} />
          <View style={{ flex: 1, gap: espaco.xs }}>
            <Texto numberOfLines={1} style={{ fontSize: 16, lineHeight: 22, fontFamily: texto.secao.fontFamily }}>{nome}</Texto>
            {numeros && b ? (
              <View style={{ flexDirection: "row", gap: espaco.md }}>
                <Contagem icone="Bank" n={b.contas} />
                <Contagem icone="CreditCard" n={b.cartoes} />
                <Contagem icone="TrendUp" n={b.investimentos} />
              </View>
            ) : null}
          </View>
          <View style={{ alignItems: "flex-end", gap: espaco.xs, flexShrink: 0 }}>
            {numeros ? (
              <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.xs }}>
                {aConferir && !oculto ? <Icone nome="WarningCircle" tamanho={16} tom="warning" /> : null}
                <Valor centavos={b?.total ?? null} oculto={oculto} />
              </View>
            ) : null}
            <ConnectionStatus estado={estado} label={c.ui.label} />
          </View>
        </Pressable>
        {c.ui.detail && estado !== "updated" ? (
          <View style={{ flexDirection: "row", gap: espaco.sm, alignItems: "flex-start" }}>
            <Icone nome="Info" tamanho={16} tom="inkMuted" />
            <Texto variante="legenda" tom="inkMuted" style={{ flex: 1 }}>{c.ui.detail}</Texto>
          </View>
        ) : null}
        {emDestaque ? <Button rotulo={`Reconectar ${nomeAcessivel}`} desativado={entrando || removendoId !== null} onPress={aoReconectar} /> : null}
        {acompanhavel ? <Button rotulo="Acompanhar sincronização" variante="secondary" desativado={entrando} onPress={aoAcompanhar} /> : null}
        <Pressable onPress={alternar} accessible={false} style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", minHeight: 44, borderTopWidth: 1, borderTopColor: cores.border }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.xs }}>
            <Icone nome="ClockCounterClockwise" tamanho={16} tom="inkFaint" />
            <Texto variante="legenda" tom="inkFaint">{c.last_sync_at ? tempoRelativo(c.last_sync_at) : "Ainda não sincronizou"}</Texto>
          </View>
          <View style={{ transform: [{ rotate: aberto ? "180deg" : "0deg" }] }}><Icone nome="CaretDown" tamanho={16} tom="inkFaint" /></View>
        </Pressable>
        {aberto ? (
          <View style={{ gap: espaco.xs }}>
            {numeros && b && (conta || invest) ? (
              <>
                {split ? <BarraComposicao segmentos={[{ valor: b.saldo_conta!, cor: cores.ink }, { valor: b.investido!, cor: cores.brand }]} /> : null}
                {conta ? <Parte icone="Bank" rotulo="Conta" centavos={b.saldo_conta} oculto={oculto} /> : null}
                {invest ? <Parte icone="TrendUp" rotulo="Investimentos" centavos={b.investido} oculto={oculto} /> : null}
              </>
            ) : null}
            <View style={{ flexDirection: "row", gap: espaco.md, marginTop: espaco.sm }}>
              {reconectavel && !emDestaque ? <View style={{ flex: 1 }}><Button rotulo={`Reconectar ${nomeAcessivel}`} variante="secondary" icone="ArrowsClockwise" desativado={entrando || removendoId !== null} onPress={aoReconectar} /></View> : null}
              <View style={{ flex: 1 }}><Button rotulo={`Desconectar ${nomeAcessivel}`} variante="ghost" icone="Trash" carregando={removendo} desativado={entrando || (removendoId !== null && !removendo)} onPress={aoDesconectar} /></View>
            </View>
          </View>
        ) : null}
      </View>
    </Card>
  );
}
