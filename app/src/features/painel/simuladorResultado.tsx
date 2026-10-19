import { useState } from "react";
import { View } from "react-native";
import Svg, { Line } from "react-native-svg";
import type { z } from "zod";
import type * as S from "@/api/schemas/painel";
import { Card } from "@/ui/componentes/Card";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";
import { Curva, Linha, Valor, Vazio } from "./base";
import { diaMes } from "./catalogo";
import { LinhaExpansivel } from "./expansao";
import { DIRECAO, ESTADO, MOTIVO, ou } from "./previsaoMotivos";
import { usePrivacidade } from "./privacidade";
import { TextoPainel as Texto } from "./tipografia";

type Sim = z.output<typeof S.simuladorSchema>;
/** Duas curvas só se desenham juntas quando têm os mesmos dias, sem buraco: senão o traço compararia datas diferentes (ou um saldo que não existe). */
const desenhavel = (a: Sim["atual"]["trajetoria"], b: Sim["atual"]["trajetoria"]) => a.length > 0 && a.length === b.length && a.every((p, i) => p.data === b[i]!.data && p.saldo !== null && b[i]!.saldo !== null);

function Marca({ tracejada, nome }: { tracejada?: boolean; nome: string }) {
  const { cores } = useTema();
  return <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Svg width={20} height={4} accessible={false}><Line x1={0} y1={2} x2={20} y2={2} stroke={tracejada ? cores.inkMuted : cores.brand} strokeWidth={tracejada ? 2 : 3} strokeDasharray={tracejada ? "6 4" : undefined} /></Svg><Texto variante="legenda" tom="inkMuted">{nome}</Texto></View>;
}

function Cartao({ d, c }: { d: Sim; c: Sim["cenarios"][number] }) {
  const { oculto } = usePrivacidade(), r = c.resumo, k = c.contrato, fora = k.parcelas_fora_do_horizonte, ultimo = r.trajetoria.at(-1), n = r.dias_abaixo_da_reserva;
  const curva = d.estado !== "indisponivel" && desenhavel(d.atual.trajetoria, r.trajetoria);
  return <Card><View style={{ gap: 8 }}>
    <Texto variante="rotulo">{c.nome}</Texto>
    <Texto variante="legenda" tom="inkMuted">{d.dias} dias{ultimo ? ` · ${diaMes(ultimo.data, d.hoje)}` : ""}</Texto>
    <Valor valor={r.saldo_final} destaque />
    <Linha titulo="Diferença sem a compra" valor={r.delta_vs_atual} />
    {d.estado === "indisponivel" ? null : curva ? <>
      <Curva valores={r.trajetoria.map((p) => p.saldo)} outra={d.atual.trajetoria.map((p) => p.saldo)} />
      {oculto ? null : <View style={{ flexDirection: "row", gap: 16 }}><Marca nome="Com" /><Marca nome="Sem" tracejada /></View>}
    </> : <Vazio texto="Trajetória indisponível." />}
    <Linha titulo={r.pior_dia ? `Menor saldo · ${diaMes(r.pior_dia.data, d.hoje)}` : "Menor saldo"} valor={r.pior_dia ? r.pior_dia.saldo : null} />
    {d.reserva_minima > 0 && d.estado !== "indisponivel" && r.saldo_final !== null ? <Linha titulo={`${n} ${n === 1 ? "dia" : "dias"} abaixo da reserva`} detalhe={r.primeiro_dia_abaixo ? `desde ${diaMes(r.primeiro_dia_abaixo, d.hoje)}` : undefined} /> : null}
    {k.a_vista ? null : <Linha titulo={`${k.parcelas}× parcela`} valor={k.parcela} />}
    <Linha titulo="Total pago" valor={k.total_pago} />
    {k.juros_totais > 0 ? <Linha titulo="Juros" valor={k.juros_totais} /> : null}
    {fora.quantidade > 0 ? <Linha titulo={`${fora.quantidade} ${fora.quantidade === 1 ? "parcela" : "parcelas"} depois de ${d.dias} dias`} valor={fora.valor} /> : null}
    {r.compra_fora_da_janela ? <Texto variante="legenda" tom="inkMuted">Compra depois de {d.dias} dias: o saldo não muda</Texto> : null}
  </View></Card>;
}

/** O resultado de `POST /simulador`, um cartão por cenário, na ordem da resposta. Nenhuma conta aqui: tudo vem do servidor (`null` ≠ zero; comparar com 0 só decide o que mostrar). */
export function SimuladorResultado({ d }: { d: Sim }) {
  const [premissas, setPremissas] = useState(false);
  return <View style={{ gap: espaco.lg }}>
    <View style={{ gap: 4 }}>
      <Texto variante="legenda" tom="inkMuted">A partir de hoje, {diaMes(d.hoje, d.hoje)}</Texto>
      {d.base.bancos_excluidos ? <Texto variante="legenda" tom="warning">Bancos conectados fora do cálculo</Texto> : null}
      {d.estado === "indisponivel" ? <Texto variante="rotulo" tom="warning">Previsão indisponível</Texto> : null}
    </View>
    {d.cenarios.map((c, i) => <Cartao key={i} d={d} c={c} />)}
    <LinhaExpansivel linha={<Texto variante="rotulo">Premissas</Texto>} aberta={premissas} alternar={() => setPremissas((a) => !a)}>
      <View style={{ gap: 8 }}>
        {d.estado !== "indisponivel" ? <Texto variante="rotulo" tom="inkMuted">{ou(ESTADO, d.estado)}</Texto> : null}
        {d.motivos.map((m) => <Linha key={m.codigo + m.direcao_do_erro} titulo={ou(MOTIVO, m.codigo)} detalhe={ou(DIRECAO, m.direcao_do_erro)} />)}
        {d.premissas.map((t) => <Texto key={t} variante="legenda" tom="inkMuted">{t}</Texto>)}
      </View>
    </LinhaExpansivel>
  </View>;
}
