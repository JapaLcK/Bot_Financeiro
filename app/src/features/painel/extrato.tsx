import { useLocalSearchParams } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { lancamentosSchema, type Lancamento } from "@/api/schemas/painel";
import { RequisicaoSuperada } from "@/api/client";
import { lerRecurso } from "@/services/painel";
import { Input } from "@/ui/componentes/Input";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { usePainel, mensagemRecurso } from "./provider";
import { dataPtBr } from "./catalogo";
import { Acao, Avisos, Linha, Vazio, Valor } from "./base";
export function Extrato() {
 const { ampliado } = useTipografiaPainel();
 const p = usePainel(); const params = useLocalSearchParams<{ categoria?: string; dia?: string }>();
 const [q, setQ] = useState(""); const [busca, setBusca] = useState(""); const [origem, setOrigem] = useState(""); const [tipo, setTipo] = useState("");
 const [itens, setItens] = useState<Lancamento[]>([]); const [proximo, setProximo] = useState<string | null>(null); const [motivos, setMotivos] = useState<string[]>([]); const [erro, setErro] = useState<string | null>(null); const [carregando, setCarregando] = useState(true); const [selecionado, setSelecionado] = useState<Lancamento | null>(null);
 const lote = useRef(0); const emVoo = useRef(false); const controlador = useRef<AbortController | null>(null);
 useEffect(() => { const timer = setTimeout(() => setBusca(q.trim()), 300); return () => clearTimeout(timer); }, [q]);
 const carregar = async (cursor: string | null, g: number) => {
   if (emVoo.current && cursor) return; emVoo.current = true; setCarregando(true); setErro(null);
   const op = p.operacao(); controlador.current = op.controlador;
   const consulta = new URLSearchParams({ mes: p.mes, limite: "50" });
   if (busca) consulta.set("q", busca); if (origem) consulta.set("origem", origem); if (tipo) consulta.set("tipo", tipo); if (params.categoria !== undefined) consulta.set("categoria", params.categoria); if (cursor) consulta.set("cursor", cursor);
   try { const d = await lerRecurso(`/api/app/lancamentos?${consulta}`, lancamentosSchema, op.controlador); if (op.atual() && lote.current === g) { setItens((s) => cursor ? [...s, ...d.itens.filter((n) => !s.some((i) => i.id === n.id))] : d.itens); setProximo(d.proximo); setMotivos(d.motivos); } }
   catch (e) { if (op.atual() && lote.current === g && !(e instanceof RequisicaoSuperada)) { p.falhou(e); setErro(mensagemRecurso(e)); } }
   finally { if (lote.current === g) { setCarregando(false); emVoo.current = false; } }
 };
 useEffect(() => { controlador.current?.abort(); const g = ++lote.current; setItens([]); setSelecionado(null); setProximo(null); emVoo.current = false; void carregar(null, g); return () => { lote.current++; controlador.current?.abort(); }; }, [p.usuario.user_id, p.mes, p.versao, busca, origem, tipo, params.categoria, params.dia]);
 const visiveis = params.dia ? itens.filter((i) => i.data === params.dia) : itens;
 return <View style={{ gap: 14 }}><Input rotulo="Buscar lançamento" value={q} onChangeText={setQ} maxLength={200} /><View style={{ flexDirection: ampliado ? "column" : "row", alignItems: ampliado ? "flex-start" : undefined, flexWrap: "wrap", gap: 12 }}>{([["", "Todas as origens"], ["banco", "Banco"], ["cartao", "Cartão"], ["carteira", "Carteira"]] as const).map(([id, label]) => <Acao key={id} rotulo={`${label}${origem === id ? " · selecionado" : ""}`} onPress={() => setOrigem(id)} />)}</View><View style={{ flexDirection: ampliado ? "column" : "row", alignItems: ampliado ? "flex-start" : undefined, flexWrap: "wrap", gap: 16 }}>{([["", "Tudo"], ["entrada", "Entradas"], ["saida", "Saídas"]] as const).map(([id, label]) => <Acao key={id} rotulo={`${label}${tipo === id ? " · selecionado" : ""}`} onPress={() => setTipo(id)} />)}</View>{(params.categoria !== undefined || params.dia) && <Texto variante="rotulo" tom="brand">{params.categoria !== undefined ? params.categoria || "Sem categoria" : dataPtBr(params.dia ?? null)}</Texto>}<Avisos motivos={motivos} />{erro && <><Texto tom="warning">{erro}</Texto><Acao rotulo="Tentar de novo" onPress={() => void carregar(proximo, lote.current)} /></>}{!carregando && !erro && !visiveis.length && <Vazio texto={proximo ? "Nenhum resultado nesta página. Carregue mais lançamentos para conferir o período." : "Nenhum lançamento encontrado."} />}{visiveis.map((i) => <View key={i.id}><Linha titulo={p.oculto ? "Lançamento" : i.descricao || i.mensagem || "Lançamento"} detalhe={`${dataPtBr(i.data)} · ${i.origem}${i.interno ? " · transferência interna" : ""}${i.fatura ? ` · fatura ${i.fatura}` : ""}`} onPress={() => setSelecionado(selecionado?.id === i.id ? null : i)} />{i.moeda === "BRL" ? <Valor valor={i.valor} tipo={i.tipo} /> : <Texto variante="rotulo" tom="inkMuted">Valor em {i.moeda} não convertido</Texto>}{selecionado?.id === i.id && <View style={{ paddingVertical: 12, gap: 6 }}><Texto variante="rotulo" tom="inkMuted">{p.oculto ? "Detalhes ocultos" : [i.categoria, i.instituicao, i.hora, i.parcela ? `Parcela ${i.parcela.n}/${i.parcela.total}` : null].filter(Boolean).join(" · ") || "Sem detalhes adicionais"}</Texto><Avisos motivos={i.motivos} /></View>}</View>)}{carregando && <ActivityIndicator accessibilityLabel="Carregando lançamentos" />}{proximo && <Acao rotulo="Carregar mais lançamentos" desativado={carregando} onPress={() => void carregar(proximo, lote.current)} />}<Texto variante="legenda" tom="inkMuted">{proximo ? "Existem mais páginas deste período." : carregando ? "Buscando registros…" : "Todos os resultados deste filtro foram carregados."}</Texto></View>;
}
