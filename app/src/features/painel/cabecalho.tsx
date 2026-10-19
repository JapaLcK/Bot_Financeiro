import { useState, type ReactNode } from "react";
import { AccessibilityInfo, Image, Pressable, View } from "react-native";
import { useSessao } from "@/features/auth/sessao";
import { Screen } from "@/ui/componentes/Screen";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { Icone } from "@/ui/componentes/Icone";
import { ButtonPainel as Button, ChipPainel as Chip, ListRowPainel } from "./tipografia";
import { GrupoDeLinhas } from "@/ui/componentes/ListRow";
import { Banner } from "@/ui/componentes/Banner";
import { ToastProvider } from "@/ui/componentes/Toast";
import { SIMBOLO } from "@/ui/stickers";
import { useTema } from "@/ui/tema";
import { raio } from "@/ui/tokens";
import { fecharAvisoBanco, useAvisoBancoFechado } from "./avisoBanco";
import { CorpoAvisoBanco } from "./corpoAvisoBanco";
import { cancelarAtualizarBanco } from "./usarAtualizarBanco";
import { PilhaDeAvisos, type AvisoDaPilha } from "./pilhaAvisos";
import { ProvedorRespondidas, ReceitasDoBanco } from "./receitas";
import { usePainel } from "./provider";
import { ContextoNavegacaoPainel, useNavegarPainel } from "./navegacao";
import { Acao, Folha, MOTIVOS_BANCO, RecursoDados, temMotivoBanco } from "./base";
import { BLOCOS_HOME, PERFIS, TITULOS, nomeMes, mesesDoHistorico, dataPtBr, mesAtual, type Widget } from "./catalogo";
export const nomeDe = (u: { display_name?: string | null; email?: string | null }) => u.display_name?.trim() || u.email?.split("@")[0] || "Você";
export function BotaoPrivacidade({ tamanho }: { tamanho?: 20 | 24 }) {
 const p = usePainel();
 return <Pressable testID="painel-privacidade" accessibilityRole="button" accessibilityLabel={p.oculto ? "Mostrar valores" : "Ocultar valores"} onPress={p.alternarPrivacidade} style={{ width: 44, height: 44, flexShrink: 0, alignItems: "center", justifyContent: "center" }}><Icone nome={p.oculto ? "EyeSlash" : "Eye"} tamanho={tamanho} /></Pressable>;
}
/** `comReceitas`: só o Resumo. As receitas pendentes entram na pilha; as respondidas somem do topo e vão para o card "Saldo previsto" (contexto `ProvedorRespondidas`, que só existe aqui). */
export function TelaPainel({ titulo, children, comReceitas = false }: { titulo: string; children: ReactNode; comReceitas?: boolean }) {
 const { ampliado } = useTipografiaPainel();
 const p = usePainel(); const sessao = useSessao(); const { cores } = useTema();
 const [folha, setFolha] = useState<"perfil" | "mes" | "organizar" | "conta" | null>(null);
 const [erroSaida, setErroSaida] = useState<string | null>(null); const [saindo, setSaindo] = useState(false);
 const [descartando, setDescartando] = useState(false);
 const descartar = async () => { setDescartando(true); const ok = await p.descartarPendente(); setDescartando(false); if (ok) AccessibilityInfo.announceForAccessibility("Conexão pendente descartada"); };
 const [quantidadeMeses, setQuantidadeMeses] = useState(12);
 const { navegar, entrando, aviso: avisoNavegacao, emVoo: entradaEmVoo } = useNavegarPainel(() => setFolha(null));
 const nome = nomeDe(p.usuario);
 const label = PERFIS.find((x) => x.id === p.perfil)!.label;
 // O sinal vem de QUALQUER recurso carregado (contas pode ter falhado ou não ter a conexão); os nomes, de quem os traz.
 const { contas, rendimento } = p.dados, comBanco = (x: { motivos: string[] }) => x.motivos.some((m) => MOTIVOS_BANCO.has(m));
 const avisoFechado = useAvisoBancoFechado();
 const bancoVelho = temMotivoBanco(Object.values(p.dados).map((r) => r?.fase === "pronto" ? r.dado : null));
 const bancos = [...new Set([...(contas?.fase === "pronto" ? contas.dado.contas.filter(comBanco).map((c) => c.instituicao || c.nome || "seu banco") : []), ...(rendimento?.fase === "pronto" ? rendimento.dado.itens.filter(comBanco).flatMap((i) => i.instituicao ? [i.instituicao] : []) : [])])];
 const mover = (id: Widget, dir: number) => { const ids = [...p.layout], i = ids.indexOf(id), j = i + dir; if (j < 0 || j >= ids.length) return; const atual = ids[i]!, outro = ids[j]!; ids[i] = outro; ids[j] = atual; p.organizar(ids); };
 // Ordem da pilha: conexão pendente (tem prazo), banco (os números estão errados agora), receitas (decisão). `p.aviso` e `avisoNavegacao` são feedback de uma ação: ficam FORA, como Banner.
 const itens: AvisoDaPilha[] = [
  ...(p.pendente ? [{ id: "conexao", tom: "alerta" as const, icone: "ArrowsClockwise" as const, titulo: "Conexão com o banco pendente", corpo: <><Texto variante="legenda" tom="inkMuted">Desistiu? Descarte e conecte de novo quando quiser.</Texto><View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}><Acao rotulo="Retomar conexão" desativado={entrando || descartando} onPress={() => navegar("/open-finance-volta")} /><Acao rotulo="Descartar" desativado={entrando || descartando} onPress={() => void descartar()} /></View></> }] : []),
  ...(bancoVelho && !avisoFechado ? [{ id: "banco", tom: "alerta" as const, icone: "Bank" as const, titulo: "Dados do banco desatualizados", aoFechar: () => { cancelarAtualizarBanco(); fecharAvisoBanco(); }, corpo: <CorpoAvisoBanco bancos={bancos} /> }] : []),
 ];
 const feedback = <>{p.aviso && <Banner tom="warning" mensagem={p.aviso} />}{avisoNavegacao && <Banner tom="warning" mensagem={avisoNavegacao} />}</>;
 const sair = async () => { setSaindo(true); setErroSaida(null); if (!(await sessao.sair())) { setSaindo(false); setErroSaida("Não conseguimos sair. Tente de novo."); } };
 const tela = (topo: ReactNode) => <Screen onAtualizar={p.atualizar} atualizando={p.atualizando}><View style={{ gap: 12, paddingTop: 12, paddingBottom: 24 }}>
 <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}><Pressable accessibilityRole="button" accessibilityLabel="PigBank, abrir Resumo" onPress={() => navegar("/resumo", "navigate")} style={{ flex: 1, minWidth: 0, minHeight: 44, justifyContent: "center" }}><View style={{ flexDirection: "row", gap: 5, alignItems: "center" }}><Image source={SIMBOLO} style={{ width: 28, height: 28 }} accessibilityIgnoresInvertColors /><Texto variante="secao" style={{ letterSpacing: -1, fontSize: 26, flexShrink: 1 }}>PigBank</Texto></View></Pressable><BotaoPrivacidade /><Pressable testID="painel-conta" accessibilityRole="button" accessibilityLabel="Abrir minha conta" onPress={() => { if (!entradaEmVoo.current) setFolha("conta"); }} style={{ width: 44, height: 44, flexShrink: 0, borderRadius: raio.pilula, backgroundColor: cores.surfaceRaised, alignItems: "center", justifyContent: "center" }}><Texto variante="rotulo">{nome.slice(0, 1).toUpperCase()}</Texto></Pressable></View>
 {topo}
 <View style={{ gap: 8 }}>{titulo === "Resumo" && <Texto variante="rotulo" tom="inkMuted">Bom dia, {nome}</Texto>}<View style={{ flexDirection: ampliado ? "column" : "row", alignItems: ampliado ? "flex-start" : "center", gap: 12 }}><Texto variante="titulo" accessibilityRole="header" style={{ flex: ampliado ? undefined : 1, flexShrink: 1, minWidth: 0 }}>{titulo === "Resumo" ? `Resumo de ${nomeMes(p.mes).split(" de ")[0]}` : titulo}</Texto><Acao rotulo={nomeMes(p.mes).slice(0, 3)} iconeFim="CaretDown" onPress={() => setFolha("mes")} /></View></View>
 {titulo === "Resumo" && <View style={{ flexDirection: "row", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}><Acao rotulo={label} iconeFim="CaretDown" onPress={() => setFolha("perfil")} desativado={p.salvandoPerfil} /><Acao rotulo="Organizar" icone="SlidersHorizontal" onPress={() => setFolha("organizar")} /></View>}
 {titulo === "Resumo" && <Pressable accessibilityRole="button" accessibilityLabel="Conversar com o Piggy" onPress={() => navegar("/piggy", "navigate")} style={{ padding: 12, borderRadius: raio.bloco, backgroundColor: cores.brandSoft, flexDirection: "row", alignItems: "center", gap: 12 }}><Image source={SIMBOLO} style={{ width: 44, height: 44 }} accessibilityIgnoresInvertColors /><Texto variante="rotulo" style={{ flex: 1 }}>Seu dinheiro conta uma história.</Texto><Icone nome="CaretRight" tamanho={20} /></Pressable>}{children}</View></Screen>;
 return <ContextoNavegacaoPainel.Provider value={{ navegar, entrando }}><><ToastProvider>{comReceitas ? <ReceitasDoBanco>{(r) => <ProvedorRespondidas value={r.respondidas}>{tela(<>{feedback}{r.erro}<PilhaDeAvisos itens={[...itens, ...r.itens]} /></>)}</ProvedorRespondidas>}</ReceitasDoBanco> : tela(<>{feedback}<PilhaDeAvisos itens={itens} /></>)}</ToastProvider>
 <Folha titulo="Seu perfil" aberta={folha === "perfil" || p.escolhendo} fechar={() => { if (p.escolhendo) void p.mudarPerfil("padrao"); setFolha(null); }}><Texto tom="inkMuted">O que você quer acompanhar primeiro?</Texto><View style={{ flexDirection: ampliado ? "column" : "row", alignItems: ampliado ? "flex-start" : undefined, flexWrap: "wrap", gap: 8 }}>{PERFIS.map((perfil) => <Chip key={perfil.id} rotulo={perfil.label} selecionado={p.perfil === perfil.id} desativado={p.salvandoPerfil} onPress={() => { void p.mudarPerfil(perfil.id); setFolha(null); }} />)}</View></Folha>
 <Folha titulo="Escolher mês" aberta={folha === "mes"} fechar={() => setFolha(null)}><View style={{ flexDirection: ampliado ? "column" : "row", alignItems: ampliado ? "flex-start" : undefined, flexWrap: "wrap", gap: 8 }}>{mesesDoHistorico(mesAtual(), p.usuario.history_earliest_date, quantidadeMeses).map((mes) => <Chip key={mes} rotulo={nomeMes(mes)} selecionado={p.mes === mes} onPress={() => { p.mudarMes(mes); setFolha(null); }} />)}</View>{p.usuario.history_earliest_date && <Texto testID="painel-historico-escopo" variante="legenda" tom="inkMuted">Histórico desde {dataPtBr(p.usuario.history_earliest_date)}</Texto>}{p.usuario.history_earliest_date == null && <View testID="painel-historico-expandir"><Button rotulo="Mostrar meses anteriores" variante="secondary" onPress={() => setQuantidadeMeses((q) => q + 12)} /></View>}</Folha>
 <Folha titulo="Organizar painel" aberta={folha === "organizar"} fechar={() => setFolha(null)}><Texto tom="inkMuted">Perfil: {label}</Texto>{!p.layout.length && <Texto>Seu painel está vazio.</Texto>}{p.layout.map((id, i) => <View key={id} style={{ gap: 4 }}><Texto variante="rotulo">{TITULOS[id]}</Texto><View style={{ flexDirection: "row", justifyContent: "space-between", flexWrap: "wrap" }}><Acao rotulo={`Subir ${TITULOS[id]}`} icone="ArrowUp" somenteIcone desativado={i === 0} onPress={() => mover(id, -1)} /><Acao rotulo={`Descer ${TITULOS[id]}`} icone="ArrowDown" somenteIcone desativado={i === p.layout.length - 1} onPress={() => mover(id, 1)} /><Acao rotulo={`Esconder ${TITULOS[id]}`} icone="Minus" somenteIcone onPress={() => p.organizar(p.layout.filter((v) => v !== id))} /></View></View>)}<Texto variante="secao">Adicionar blocos</Texto>{BLOCOS_HOME.filter((id) => !p.layout.includes(id)).map((id) => <Acao key={id} rotulo={`Adicionar ${TITULOS[id]}`} icone="Plus" onPress={() => p.organizar([...p.layout, id])} />)}<Button rotulo="Restaurar padrão deste perfil" variante="secondary" onPress={() => p.organizar(null)} /><Button rotulo="Pronto" onPress={() => setFolha(null)} /></Folha>
 <Folha titulo="Minha conta" aberta={folha === "conta"} fechar={() => setFolha(null)}><Texto variante="secao">{nome}</Texto><RecursoDados nome="plano">{(d) => <Texto tom="inkMuted">Plano {d.plan_tier === "free" ? "Grátis" : d.plan_tier === "essencial" ? "Essencial" : d.plan_tier === "plus" ? "Plus" : "Pro"}</Texto>}</RecursoDados><GrupoDeLinhas>{([ ["Bancos conectados", "/conexoes"], ["Configurações", "/configuracoes"], ["Segurança", "/seguranca"] ] as const).map(([rotulo, destino]) => <ListRowPainel key={destino} titulo={rotulo} chevron desativado={entrando} onPress={() => navegar(destino)} />)}</GrupoDeLinhas>{erroSaida && <Banner tom="danger" mensagem={erroSaida} />}<GrupoDeLinhas><ListRowPainel titulo="Sair" tom="danger" carregando={saindo} onPress={() => void sair()} /></GrupoDeLinhas></Folha></></ContextoNavegacaoPainel.Provider>;
}
