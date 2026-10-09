import { router, useFocusEffect, type Href } from "expo-router";
import { useCallback, useRef, useState } from "react";
import { View } from "react-native";
import { RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import { useSessao } from "@/features/auth/sessao";
import { perfil } from "@/services/auth";
import { conexoes } from "@/services/openFinance";
import { lerTentativaBancaria } from "@/storage/secure";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

/** Diagnóstico mantido até validar as telas definitivas em banco real/iPhone. */
export default function TestePluggy() {
  const sessao = useSessao();
  const [linhas, setLinhas] = useState<string[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [rodada, setRodada] = useState(0);
  const entradaEmVoo = useRef(false);
  const [entrando, setEntrando] = useState(false);
  useFocusEffect(useCallback(() => {
    entradaEmVoo.current = false;
    setEntrando(false);
  }, []));
  const navegar = (destino: Href) => {
    if (entradaEmVoo.current) return;
    entradaEmVoo.current = true; setEntrando(true);
    try { router.push(destino); }
    catch { entradaEmVoo.current = false; setEntrando(false); setErro("Não conseguimos abrir a tela. Tente de novo."); }
  };
  useFocusEffect(useCallback(() => {
    let ativo = true;
    const controlador = new AbortController();
    void (async () => {
      const p = await perfil();
      if (p.app_access !== true) throw new Error("A conta não tem acesso ao Open Finance agora.");
      const s = await conexoes(p.user_id, controlador);
      const t = await lerTentativaBancaria(p.user_id);
      if (ativo) { setErro(null); setLinhas([
        `Conferido às ${new Date().toLocaleTimeString("pt-BR")}`,
        `Servidor: ${s.connections.length} conexão(ões)`,
        ...s.connections.map((c) => `${c.institution_name ?? "Banco"}: ${c.ui.label}. ${c.ui.detail ?? ""} Sincronização: ${c.last_sync_at ?? "pendente"}`),
        t ? `Tentativa em andamento: ${t.modo}, retorno ${t.item_id ? "capturado" : "ainda não recebido"}.` : "Sem tentativa em andamento.",
      ]); }
    })().catch((e: unknown) => {
      if (!ativo || e instanceof RequisicaoSuperada) return;
      if (e instanceof SessaoExpirada) sessao.expirou(e.detalhe);
      else setErro("Não conseguimos conferir agora. Tente de novo.");
    });
    return () => { ativo = false; controlador.abort(); };
  }, [rodada]));
  return <Screen sobCabecalho><View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
    <Texto variante="secao">Teste Open Finance</Texto>
    {erro && <Banner tom="danger" mensagem={erro} />}
    {linhas.map((l, i) => <Texto key={i} variante="legenda">{l}</Texto>)}
    <Button rotulo="Conferir no servidor" onPress={() => setRodada((v) => v + 1)} />
    <Button rotulo="Conectar para testar" variante="secondary" desativado={entrando} onPress={() => navegar("/conectar-banco")} />
    <Button rotulo="Retomar retorno do banco" variante="secondary" desativado={entrando} onPress={() => navegar("/open-finance-volta")} />
    <Button rotulo="Ver bancos conectados" variante="ghost" desativado={entrando} onPress={() => navegar("/conexoes")} />
  </View></Screen>;
}
