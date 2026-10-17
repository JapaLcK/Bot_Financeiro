import { useState } from "react";
import { ScrollView, View } from "react-native";
import { BarrasMensais } from "@/ui/componentes/BarrasMensais";
import { rotuloCompacto } from "@/ui/componentes/CurvaAcumulada";
import { falado } from "@/ui/dinheiro";
import { CORES_CATEGORIA } from "@/ui/tokens";
import { TextoPainel as Texto } from "./tipografia";
import { mesAbrev, mesAtual, nomeCategoria, nomeMes } from "./catalogo";
import { Acao, Avisos, Linha, Vazio } from "./base";
import { useBloco } from "./blocoContexto";
import { somarCentavos } from "./decimal";
import { LinhaExpansivel } from "./expansao";
import { LinhaLancamento } from "./extrato";
import { LinhaCategoria, rotuloPercentual } from "./gastos";
import { EntrouSaiu } from "./numerosMes";
import { usePainel } from "./provider";
const MAIS = 5; // categorias visíveis antes do "+ N categorias"
const capital = (s: string) => s[0]!.toUpperCase() + s.slice(1);
const mes = (m: string) => nomeMes(m).split(" de ")[0]!;
/** "Agosto 2026", "Março a agosto 2026" (mesmo ano) ou "Nov 2025 a abr 2026" (anos diferentes). */
export function tituloPeriodo(inicio: string, fim: string) {
  if (inicio === fim) return `${capital(mes(inicio))} ${inicio.slice(0, 4)}`;
  if (inicio.slice(0, 4) === fim.slice(0, 4)) return `${capital(mes(inicio))} a ${mes(fim)} ${fim.slice(0, 4)}`;
  return `${capital(mesAbrev(inicio))} ${inicio.slice(0, 4)} a ${mesAbrev(fim)} ${fim.slice(0, 4)}`;
}
/**
 * Card `periodo` do chat (mais de um mês, só no chat): Entrou/Saiu/Saldo do servidor, as saídas por mês e as categorias. Tocar numa categoria expande
 * os maiores lançamentos DELA aqui mesmo (vêm no bloco: nenhuma busca). Dado = só `recursos.periodo` do bloco; sem ele, "Dado indisponível".
 */
export function CardPeriodo() {
  const d = useBloco()?.recursos.periodo; const p = usePainel();
  const [todas, setTodas] = useState(false); const [aberta, setAberta] = useState<string | null>(null); const [aberto, setAberto] = useState<string | null>(null);
  if (!d) return <Vazio texto="Dado indisponível neste card." />;
  const catalogo = p.dados.categorias?.fase === "pronto" ? p.dados.categorias.dado.categorias : undefined;
  const ordenadas = [...d.categorias].sort((a, b) => b.valor - a.valor), visiveis = todas ? ordenadas : ordenadas.slice(0, MAIS), total = somarCentavos(ordenadas.map((c) => c.valor));
  const corte = d.historico_desde?.slice(0, 7), atual = mesAtual();
  return <View style={{ gap: 12 }}>
    <EntrouSaiu entrou={d.entrou} saiu={d.saiu} />
    <Linha titulo="Saldo" valor={d.saldo} />
    {p.oculto ? <Texto tom="inkMuted">Gráfico oculto</Texto>
      : <View accessible accessibilityLabel={`Saídas por mês: ${d.meses.map((m) => `${nomeMes(m.mes)}: ${corte && m.mes < corte ? "antes do histórico do seu plano" : falado(m.saiu) ?? "valor indisponível"}`).join("; ")}`}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ minWidth: "100%" }}>
          <View style={{ flexGrow: 1 }}><BarrasMensais meses={d.meses.map((m) => ({ chave: m.mes, rotulo: mesAbrev(m.mes), valor: m.saiu, atual: m.mes === atual, apagado: !!corte && m.mes < corte }))} rotuloValor={rotuloCompacto} /></View>
        </ScrollView>
      </View>}
    {!ordenadas.length ? <Vazio texto="Nenhum gasto em reais neste período." /> : <View>
      {visiveis.map((c, i) => {
        const chave = c.categoria ?? "", linha = <LinhaCategoria titulo={nomeCategoria(c.categoria, catalogo)} detalhe={p.oculto ? undefined : rotuloPercentual(c.percentual, c.valor)} valor={c.valor} fracao={Math.max(0, c.valor) / Math.max(1, total)} cor={CORES_CATEGORIA[i % CORES_CATEGORIA.length]!} />;
        return c.maiores.length ? <LinhaExpansivel key={chave} linha={linha} aberta={aberta === chave} alternar={() => setAberta(aberta === chave ? null : chave)}>
          <Texto variante="legenda" tom="inkMuted">Maiores lançamentos</Texto>
          {c.maiores.map((l) => <LinhaLancamento key={l.id} item={l} comParcela aberto={aberto === l.id} alternar={() => setAberto(aberto === l.id ? null : l.id)} />)}
        </LinhaExpansivel> : <View key={chave}>{linha}</View>;
      })}
      {ordenadas.length > MAIS && <Acao rotulo={todas ? "Mostrar menos" : `+ ${ordenadas.length - MAIS} ${ordenadas.length - MAIS === 1 ? "categoria" : "categorias"}`} onPress={() => setTodas((t) => !t)} />}
    </View>}
    <Avisos motivos={d.motivos} />
  </View>;
}
