import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { lancamentosSchema, type Lancamento } from "@/api/schemas/painel";
import { RequisicaoSuperada } from "@/api/client";
import { lerRecurso } from "@/services/painel";
import { Input } from "@/ui/componentes/Input";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { usePainel, mensagemRecurso } from "./provider";
import { dataPtBr, mesCurto, nomeCategoria } from "./catalogo";
import { Acao, Avisos, Linha, Vazio } from "./base";
import { alvoTexto } from "./metasEscrita";
import { FichasAtivas, FolhaFiltros, ORIGENS, SEM_FILTROS, fichasAtivas, type FiltrosExtrato } from "./extratoFiltros";
/**
 * Paginação de `/api/app/lancamentos` (Extrato e linhas que expandem no chat). `consulta` é a query SEM cursor; a lista reinicia quando ela muda, ou na carga completa do provider.
 * Guardas: `lote` (resposta de consulta velha), `emVoo` (uma página por vez), abort no cleanup, dedupe por id. Só leitura.
 */
export function useLancamentos(consulta: string) {
 const p = usePainel();
 const [itens, setItens] = useState<Lancamento[]>([]); const [proximo, setProximo] = useState<string | null>(null); const [motivos, setMotivos] = useState<string[]>([]); const [erro, setErro] = useState<string | null>(null); const [carregando, setCarregando] = useState(true);
 const lote = useRef(0); const emVoo = useRef(false); const controlador = useRef<AbortController | null>(null);
 const carregar = async (cursor: string | null, g: number) => {
   if (emVoo.current && cursor) return; emVoo.current = true; setCarregando(true); setErro(null);
   const op = p.operacao(); controlador.current = op.controlador;
   const query = new URLSearchParams(consulta); if (cursor) query.set("cursor", cursor);
   try { const d = await lerRecurso(`/api/app/lancamentos?${query}`, lancamentosSchema, op.controlador); if (op.atual() && lote.current === g) { setItens((s) => cursor ? [...s, ...d.itens.filter((n) => !s.some((i) => i.id === n.id))] : d.itens); setProximo(d.proximo); setMotivos(d.motivos); } }
   catch (e) { if (op.atual() && lote.current === g && !(e instanceof RequisicaoSuperada)) { p.falhou(e); setErro(mensagemRecurso(e)); } }
   finally { if (lote.current === g) { setCarregando(false); emVoo.current = false; } }
 };
 useEffect(() => { controlador.current?.abort(); const g = ++lote.current; setItens([]); setProximo(null); emVoo.current = false; void carregar(null, g); return () => { lote.current++; controlador.current?.abort(); }; }, [p.usuario.user_id, p.versao, consulta]);
 return { itens, proximo, motivos, carregando, erro, carregarMais: () => void carregar(proximo, lote.current) }; // com `proximo` null (1ª página falhou) é recarregar do início: serve ao "Tentar de novo" também
}
/** Uma linha de lançamento; tocar abre/fecha o detalhe (categoria, instituição, hora, parcela). `comParcela` escreve "parcela n/N" já na linha recolhida. */
export function LinhaLancamento({ item: i, aberto, alternar, comParcela = false }: { item: Lancamento; aberto: boolean; alternar: () => void; comParcela?: boolean }) {
 const p = usePainel(); const catalogo = p.dados.categorias?.fase === "pronto" ? p.dados.categorias.dado.categorias : undefined;
 return <View><Linha titulo={p.oculto ? "Lançamento" : i.descricao || i.mensagem || "Lançamento"} valor={i.moeda === "BRL" ? i.valor : undefined} tipo={i.tipo} detalhe={`${dataPtBr(i.data)} · ${ORIGENS.find(([id]) => id === i.origem)?.[1] ?? i.origem}${i.interno ? " · transferência interna" : ""}${i.fatura ? ` · fatura ${mesCurto(i.fatura)}` : ""}${i.moeda === "BRL" ? "" : ` · valor em ${i.moeda} não convertido`}${comParcela && i.parcela ? ` · parcela ${i.parcela.n}/${i.parcela.total}` : ""}`} onPress={alternar} />{aberto && <View style={{ paddingVertical: 12, gap: 6 }}><Texto variante="rotulo" tom="inkMuted">{p.oculto ? "Detalhes ocultos" : [i.categoria && nomeCategoria(i.categoria, catalogo), i.instituicao, i.hora, i.parcela ? `Parcela ${i.parcela.n}/${i.parcela.total}` : null].filter(Boolean).join(" · ") || "Sem detalhes adicionais"}</Texto><Avisos motivos={i.motivos} /></View>}</View>;
}
export function Extrato() {
 const p = usePainel(); const params = useLocalSearchParams<{ categoria?: string; dia?: string; entrada?: string }>();
 const [q, setQ] = useState(""); const [busca, setBusca] = useState(""); const [origem, setOrigem] = useState(""); const [tipo, setTipo] = useState(""); const [valor, setValor] = useState({ min: 0, max: 0 }); const [folha, setFolha] = useState(false); const { ampliado } = useTipografiaPainel(); const [selecionado, setSelecionado] = useState<Lancamento | null>(null);
 useEffect(() => { const timer = setTimeout(() => setBusca(q.trim()), 300); return () => clearTimeout(timer); }, [q]);
 useEffect(() => { setQ(""); setBusca(""); setOrigem(""); setTipo(""); setValor({ min: 0, max: 0 }); }, [params.entrada]); // cada `irAoExtrato` traz uma `entrada` nova (mesmo com o mesmo `dia`): filtro de uma visita anterior não combina com o dia/categoria pedido
 const consulta = new URLSearchParams({ mes: p.mes, limite: "50" });
 if (busca) consulta.set("q", busca); if (origem) consulta.set("origem", origem); if (tipo) consulta.set("tipo", tipo); if (params.categoria !== undefined) consulta.set("categoria", params.categoria); if (params.dia) consulta.set("dia", params.dia); if (valor.min) consulta.set("valor_min", alvoTexto(valor.min)); if (valor.max) consulta.set("valor_max", alvoTexto(valor.max)); // `dia` vai ao servidor (com o `mes`): o filtro no aparelho só via a 1ª página
 const { itens, proximo, motivos, carregando, erro, carregarMais } = useLancamentos(consulta.toString());
 useEffect(() => { setSelecionado(null); }, [p.usuario.user_id, p.versao, consulta.toString()]); // a lista reinicia nestas mesmas chaves
 const catalogo = p.dados.categorias?.fase === "pronto" ? p.dados.categorias.dado.categorias : undefined;
 const atual: FiltrosExtrato = { tipo, origem, dia: params.dia ?? "", ...valor }; // o `dia` mora no route param: vale junto com o calendário e o "Ver extrato do dia"
 const aplicar = (n: FiltrosExtrato, semCategoria = false) => { setTipo(n.tipo); setOrigem(n.origem); setValor({ min: n.min, max: n.max }); setFolha(false); const novos: Record<string, string | undefined> = {}; if (n.dia !== atual.dia) novos.dia = n.dia || undefined; if (semCategoria && params.categoria !== undefined) novos.categoria = undefined; if (Object.keys(novos).length) router.setParams(novos); };
 const fichas = fichasAtivas(atual, aplicar, params.categoria !== undefined ? { nome: nomeCategoria(params.categoria, catalogo), tirar: () => router.setParams({ categoria: undefined }) } : undefined, p.oculto);
 return <View style={{ gap: 14 }}><View style={{ flexDirection: ampliado ? "column" : "row", alignItems: ampliado ? "stretch" : "flex-end", gap: 8 }}><View style={{ flex: ampliado ? undefined : 1 }}><Input rotulo="Buscar lançamento" value={q} onChangeText={setQ} maxLength={200} /></View><Acao rotulo="Filtros" texto={fichas.length ? `Filtros · ${fichas.length}` : "Filtros"} icone="SlidersHorizontal" ativa={fichas.length > 0} onPress={() => setFolha(true)} /></View>{busca && <Texto testID="extrato-escopo-busca" variante="legenda" tom="inkMuted">Buscando em todo o histórico do seu plano</Texto>}<FichasAtivas fichas={fichas} /><FolhaFiltros aberta={folha} fechar={() => setFolha(false)} atual={atual} mes={p.mes} aplicar={aplicar} limpar={() => aplicar(SEM_FILTROS, true)} /><Avisos motivos={motivos} />{erro && <><Texto tom="warning">{erro}</Texto><Acao rotulo="Tentar de novo" onPress={carregarMais} /></>}{!carregando && !erro && !itens.length && <Vazio texto={proximo ? "Nenhum resultado nesta página." : "Nenhum lançamento encontrado."} />}{itens.length > 0 && <View style={{ gap: 4 }}>{itens.map((i) => <LinhaLancamento key={i.id} item={i} aberto={selecionado?.id === i.id} alternar={() => setSelecionado(selecionado?.id === i.id ? null : i)} />)}</View>}{carregando && <ActivityIndicator accessibilityLabel="Carregando lançamentos" />}{proximo && <Acao rotulo="Carregar mais lançamentos" desativado={carregando} onPress={carregarMais} />}{(erro || (carregando && !proximo)) && <Texto variante="legenda" tom="inkMuted">{erro ? "Não foi possível concluir o carregamento." : "Buscando registros…"}</Texto>}</View>;
}
