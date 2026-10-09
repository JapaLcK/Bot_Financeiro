import { useLocalSearchParams } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { lancamentosSchema, type Lancamento } from "@/api/schemas/painel";
import { RequisicaoSuperada } from "@/api/client";
import { lerRecurso } from "@/services/painel";
import { Input } from "@/ui/componentes/Input";
import { ChipPainel as Chip, TextoPainel as Texto } from "./tipografia";
import { usePainel, mensagemRecurso } from "./provider";
import { dataPtBr, mesCurto, nomeCategoria } from "./catalogo";
import { Acao, Avisos, Filtros, Linha, Vazio } from "./base";
// "Registro antigo" só rotula o detalhe: o filtro de origem não o oferece.
const ORIGENS = [["banco", "Banco"], ["cartao", "Cartão"], ["carteira", "Carteira"], ["registro_antigo", "Registro antigo"]] as const;
export function Extrato() {
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
 const catalogo = p.dados.categorias?.fase === "pronto" ? p.dados.categorias.dado.categorias : undefined;
 const visiveis = params.dia ? itens.filter((i) => i.data === params.dia) : itens;
 return <View style={{ gap: 14 }}><Input rotulo="Buscar lançamento" value={q} onChangeText={setQ} maxLength={200} />{busca && <Texto testID="extrato-escopo-busca" variante="legenda" tom="inkMuted">Buscando em todo o histórico do seu plano</Texto>}<Filtros>{([["", "Todas as origens"], ...ORIGENS.filter(([id]) => id !== "registro_antigo")] as const).map(([id, label]) => <Chip key={id} rotulo={label} selecionado={origem === id} onPress={() => setOrigem(id)} />)}</Filtros><Filtros>{([["", "Tudo"], ["entrada", "Entradas"], ["saida", "Saídas"]] as const).map(([id, label]) => <Chip key={id} rotulo={label} selecionado={tipo === id} onPress={() => setTipo(id)} />)}</Filtros>{params.categoria !== undefined && <Texto variante="rotulo" tom="brand">{nomeCategoria(params.categoria, catalogo)}</Texto>}{params.dia && <Texto variante="rotulo" tom="brand">{dataPtBr(params.dia)}</Texto>}<Avisos motivos={motivos} />{erro && <><Texto tom="warning">{erro}</Texto><Acao rotulo="Tentar de novo" onPress={() => void carregar(proximo, lote.current)} /></>}{!carregando && !erro && !visiveis.length && <Vazio texto={proximo ? "Nenhum resultado nesta página." : "Nenhum lançamento encontrado."} />}{visiveis.length > 0 && <View style={{ gap: 4 }}>{visiveis.map((i) => <View key={i.id}><Linha titulo={p.oculto ? "Lançamento" : i.descricao || i.mensagem || "Lançamento"} valor={i.moeda === "BRL" ? i.valor : undefined} tipo={i.tipo} detalhe={`${dataPtBr(i.data)} · ${ORIGENS.find(([id]) => id === i.origem)?.[1] ?? i.origem}${i.interno ? " · transferência interna" : ""}${i.fatura ? ` · fatura ${mesCurto(i.fatura)}` : ""}${i.moeda === "BRL" ? "" : ` · valor em ${i.moeda} não convertido`}`} onPress={() => setSelecionado(selecionado?.id === i.id ? null : i)} />{selecionado?.id === i.id && <View style={{ paddingVertical: 12, gap: 6 }}><Texto variante="rotulo" tom="inkMuted">{p.oculto ? "Detalhes ocultos" : [i.categoria && nomeCategoria(i.categoria, catalogo), i.instituicao, i.hora, i.parcela ? `Parcela ${i.parcela.n}/${i.parcela.total}` : null].filter(Boolean).join(" · ") || "Sem detalhes adicionais"}</Texto><Avisos motivos={i.motivos} /></View>}</View>)}</View>}{carregando && <ActivityIndicator accessibilityLabel="Carregando lançamentos" />}{proximo && <Acao rotulo="Carregar mais lançamentos" desativado={carregando} onPress={() => void carregar(proximo, lote.current)} />}{(erro || (carregando && !proximo)) && <Texto variante="legenda" tom="inkMuted">{erro ? "Não foi possível concluir o carregamento." : "Buscando registros…"}</Texto>}</View>;
}
