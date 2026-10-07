import { useFocusEffect } from "expo-router";
import * as SecureStore from "expo-secure-store";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { z } from "zod";
import { ActivityIndicator, View } from "react-native";
import { ContratoInvalido, ErroDeApi, RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import type { Perfil } from "@/api/schemas/auth";
import * as S from "@/api/schemas/painel";
import { useSessao } from "@/features/auth/sessao";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { carregarAcessoBancario } from "@/features/openFinance/acesso";
import { useForeground } from "@/features/openFinance/useForeground";
import { lerTentativaBancaria } from "@/storage/secure";
import { lerRecurso, salvarPerfil } from "@/services/painel";
import { Screen } from "@/ui/componentes/Screen";
import { Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { chaveLayout, mesAtual, sanitizarLayout, type Widget } from "./catalogo";
import Inicio from "../../../app/(app)/index";

export const schemas = { contas: S.contasSchema, resumo: S.resumoSchema, detalhes: S.detalhesMesSchema, patrimonio: S.patrimonioSchema, rendimento: S.rendimentoSchema, previsao: S.previsaoSchema, assinaturas: S.assinaturasSchema, metas: S.metasSchema, cartoes: S.cartoesSchema, parcelas: S.parcelasSchema, renda: S.rendaSchema, insights: S.insightsSchema, conversa: S.conversaSchema, plano: S.planoSchema };
export type Recurso = keyof typeof schemas;
export type Dados = { [K in Recurso]: z.output<typeof schemas[K]> };
export type Resultado<T> = { fase: "carregando" } | { fase: "pronto"; dado: T } | { fase: "erro" | "negado"; mensagem: string };
type Resultados = { [K in Recurso]?: Resultado<Dados[K]> };
type Painel = { usuario: Perfil | null; gate: "carregando" | "liberado" | "negado" | "erro"; erroGate: string; ativo: boolean; travado: boolean; voltarAoPortao: () => void; pendente: boolean; mes: string; mudarMes: (m: string) => void; oculto: boolean; alternarPrivacidade: () => void; perfil: S.PerfilPainel; escolhendo: boolean; salvandoPerfil: boolean; mudarPerfil: (p: S.PerfilPainel) => Promise<void>; layout: Widget[]; organizar: (ids: Widget[] | null) => void; dados: Resultados; aviso: string | null; atualizar: () => void; atualizando: boolean; dias?: number; mudarDias: (d: number) => void; versao: number; operacao: () => { controlador: AbortController; atual: () => boolean }; falhou: (e: unknown) => void };
const Contexto = createContext<Painel | null>(null);
export function useEstadoPainel() { const p = useContext(Contexto); if (!p) throw new Error("Painel indisponível"); return p; }
export function usePainel() { const p = useEstadoPainel(); if (!p.usuario || p.gate !== "liberado") throw new Error("Painel não autorizado"); return { ...p, usuario: p.usuario }; }
export function mensagemRecurso(e: unknown) {
  if (e instanceof ContratoInvalido) return "Recebemos dados incompatíveis. Tente atualizar.";
  if (e instanceof ErroDeApi && e.status === 403) return "Este recurso não está disponível no seu acesso atual.";
  if (e instanceof ErroDeApi && e.status === 429) return "Seu limite foi alcançado. Tente novamente mais tarde.";
  return "Não conseguimos carregar agora. Tente novamente.";
}
function rota(k: Recurso, uid: number, mes: string, dias?: number) {
  const base = `/api/app/`;
  return ({ contas: base + "contas", resumo: base + `resumo-do-mes?mes=${mes}`, detalhes: base + `mes-detalhes?mes=${mes}`, patrimonio: base + "patrimonio", rendimento: base + "rendimento", previsao: base + "previsao" + (dias ? `?dias=${dias}` : ""), assinaturas: base + "assinaturas", metas: `/goals/${uid}/status`, cartoes: `/cards/${uid}/summary`, parcelas: `/installments/${uid}/list`, renda: `/analytics/${uid}/evolution?months=6`, insights: `/insights/${uid}/current`, conversa: "/ai/messages", plano: base + "me" })[k];
}
export function PainelProvider({ children }: { children: ReactNode }) {
  const sessao = useSessao();
  const ativo = useForeground();
  const travado = useBloqueio().estado.fase === "travado";
  const [usuario, setUsuario] = useState<Perfil | null>(null);
  const [gate, setGate] = useState<"carregando" | "liberado" | "negado" | "erro">("carregando");
  const [erroGate, setErroGate] = useState("");
  const [pendente, setPendente] = useState(false);
  const [mes, setMes] = useState(mesAtual);
  const [oculto, setOculto] = useState(false);
  const [perfil, setPerfil] = useState<S.PerfilPainel>("padrao");
  const [escolhendo, setEscolhendo] = useState(false);
  const [salvandoPerfil, setSalvandoPerfil] = useState(false);
  const [layout, setLayout] = useState<Widget[]>(sanitizarLayout(null, "padrao"));
  const [dados, setDados] = useState<Resultados>({});
  const [aviso, setAviso] = useState<string | null>(null);
  const [dias, setDias] = useState<number>();
  const [versao, setVersao] = useState(0);
  const [atualizando, setAtualizando] = useState(false);
  const geracao = useRef(0);
  const geracaoAcesso = useRef(0);
  const acessoValidando = useRef(true);
  const dono = useRef<number | null>(null);
  const controladores = useRef(new Set<AbortController>());
  const leituras = useRef(new Map<Recurso, AbortController>());
  const ultimaCarga = useRef<{ uid: number; versao: number; mes: string; dias?: number; geracao: number } | null>(null);
  const emVoo = useRef(false);
  const layouts = useRef(new Map<S.PerfilPainel, Widget[]>());
  const filaStorage = useRef(Promise.resolve());
  const sessaoRef = useRef(sessao); sessaoRef.current = sessao;
  const cancelar = useCallback(() => { geracao.current++; for (const c of controladores.current) c.abort(); controladores.current.clear(); leituras.current.clear(); }, []);
  useEffect(() => cancelar, [cancelar]);
  const falhou = useCallback((e: unknown) => {
    if (e instanceof SessaoExpirada) sessaoRef.current.expirou(e.detalhe);
    else if (e instanceof ErroDeApi && e.status === 402) { cancelar(); setDados({}); setGate("negado"); }
  }, [cancelar]);
  useFocusEffect(useCallback(() => {
    if (!ativo || travado) { cancelar(); return; }
    let cancelado = false;
    const g = ++geracaoAcesso.current;
    acessoValidando.current = true;
    setGate("carregando");
    void carregarAcessoBancario().then(async (a) => {
      const tentativa = a.fase === "inicio" ? await lerTentativaBancaria(a.perfil.user_id) : null;
      if (cancelado || g !== geracaoAcesso.current) return;
      if (a.fase !== "inicio") { setDados({}); setUsuario(null); setGate("negado"); return; }
      if (dono.current !== a.perfil.user_id) {
        dono.current = a.perfil.user_id; layouts.current.clear(); setDados({}); setOculto(false); setPerfil("padrao"); setDias(undefined); setMes(mesAtual());
      } else {
        const atual = mesAtual(), inicio = a.perfil.history_earliest_date?.slice(0, 7);
        setMes((m) => m > atual ? atual : inicio && m < inicio ? inicio : m);
      }
      acessoValidando.current = false; setPendente(!!tentativa); setUsuario(a.perfil); setGate("liberado"); setVersao((v) => v + 1);
    }).catch((e: unknown) => {
      if (cancelado || g !== geracaoAcesso.current || e instanceof RequisicaoSuperada) return;
      falhou(e); setErroGate(mensagemRecurso(e)); setGate("erro");
    });
    return () => { cancelado = true; geracaoAcesso.current++; acessoValidando.current = true; cancelar(); };
  }, [ativo, travado, cancelar, falhou]));
  useEffect(() => {
    if (gate !== "liberado" || acessoValidando.current || !usuario || !ativo || travado) return;
    const uid = usuario.user_id, anterior = ultimaCarga.current;
    const completa = !anterior || anterior.uid !== uid || anterior.versao !== versao || anterior.geracao !== geracao.current;
    const recursos: Recurso[] = completa ? Object.keys(schemas) as Recurso[] : [
      ...(anterior.mes !== mes ? ["resumo", "detalhes"] as const : []),
      ...(anterior.dias !== dias ? ["previsao"] as const : []),
    ];
    if (completa) { cancelar(); setDados({}); }
    const g = geracao.current;
    ultimaCarga.current = { uid, versao, mes, dias, geracao: g };
    if (!recursos.length) return;
    setAtualizando(true);
    if (!completa) setDados((d) => ({ ...d, ...Object.fromEntries(recursos.map((k) => [k, { fase: "carregando" }])) }));
    const carregar = async <K extends Recurso>(k: K) => {
      const anterior = leituras.current.get(k);
      anterior?.abort(); if (anterior) controladores.current.delete(anterior);
      const c = new AbortController(); leituras.current.set(k, c); controladores.current.add(c);
      // Seletores superam somente a leitura deste recurso; refresh e acesso superam toda a geração.
      const atual = () => !c.signal.aborted && g === geracao.current && dono.current === uid && leituras.current.get(k) === c;
      try {
        const dado = await lerRecurso(rota(k, uid, mes, dias), schemas[k] as unknown as z.ZodType<Dados[K]>, c);
        if (atual()) setDados((d) => ({ ...d, [k]: { fase: "pronto", dado } }));
      } catch (e) {
        if (!atual() || e instanceof RequisicaoSuperada) return;
        falhou(e);
        if (!atual()) return;
        if (e instanceof ErroDeApi && e.status === 403 && typeof e.corpo === "object" && e.corpo && JSON.stringify(e.corpo).includes("open_finance_onboarding_required")) { cancelar(); setGate("negado"); setDados({}); return; }
        setDados((d) => ({ ...d, [k]: { fase: e instanceof ErroDeApi && e.status === 403 ? "negado" : "erro", mensagem: mensagemRecurso(e) } }));
      } finally {
        controladores.current.delete(c);
        if (leituras.current.get(k) === c) leituras.current.delete(k);
        if (g === geracao.current && dono.current === uid) setAtualizando(leituras.current.size > 0);
      }
    };
    void Promise.all(recursos.map(carregar));
    if (completa && !emVoo.current) {
      const c = new AbortController(); controladores.current.add(c);
      const atual = () => !c.signal.aborted && g === geracao.current && dono.current === uid;
      void lerRecurso("/api/app/perfil", S.perfilPainelSchema, c).then((p) => { if (atual() && !emVoo.current) { setPerfil(p.perfil ?? "padrao"); setEscolhendo(p.perfil === null); } }).catch((e: unknown) => { if (atual() && !(e instanceof RequisicaoSuperada)) { falhou(e); setAviso("Não conseguimos carregar seu perfil. Tente atualizar."); } }).finally(() => controladores.current.delete(c));
    }
  }, [gate, usuario, mes, dias, versao, ativo, travado, cancelar, falhou]);
  useEffect(() => {
    if (!usuario) return;
    const uid = usuario.user_id; let cancelado = false;
    setLayout(layouts.current.get(perfil) ?? sanitizarLayout(null, perfil));
    if (!layouts.current.has(perfil)) void SecureStore.getItemAsync(chaveLayout(uid, perfil)).then((raw) => {
      if (cancelado || dono.current !== uid || layouts.current.has(perfil)) return;
      let valor: unknown = null; try { valor = raw ? JSON.parse(raw) : null; } catch { /* restaura preset */ }
      const ids = sanitizarLayout(valor, perfil); layouts.current.set(perfil, ids); setLayout(ids);
    }).catch(() => { if (!cancelado) setAviso("Não conseguimos ler sua organização neste aparelho."); });
    return () => { cancelado = true; };
  }, [usuario, perfil]);
  const operacao = () => {
    const g = geracao.current, uid = dono.current;
    const controlador = new AbortController(); controladores.current.add(controlador);
    return { controlador, atual: () => !controlador.signal.aborted && g === geracao.current && uid === dono.current };
  };
  const mudarPerfil = async (p: S.PerfilPainel) => {
    if (emVoo.current || !usuario) return;
    emVoo.current = true; setSalvandoPerfil(true); setAviso(null);
    const anterior = perfil, uid = usuario.user_id;
    const op = operacao(); setPerfil(p); setEscolhendo(false);
    try { await salvarPerfil(p, op.controlador); }
    catch (e) { if (op.atual()) { setPerfil(anterior); setAviso("Não conseguimos salvar seu perfil. Tente de novo."); falhou(e); } }
    finally { controladores.current.delete(op.controlador); if (dono.current === uid) { setSalvandoPerfil(false); if (op.controlador.signal.aborted) { setPerfil(anterior); setVersao((v) => v + 1); } } emVoo.current = false; }
  };
  const organizar = (ids: Widget[] | null) => {
    if (!usuario) return;
    const valor = sanitizarLayout(ids, perfil), chave = chaveLayout(usuario.user_id, perfil);
    layouts.current.set(perfil, valor); setLayout(valor);
    const uid = usuario.user_id;
    filaStorage.current = filaStorage.current.catch(() => {}).then(() => SecureStore.setItemAsync(chave, JSON.stringify(valor))).catch(() => { if (dono.current === uid) setAviso("Organização mantida nesta visita. Não conseguimos salvar neste aparelho."); });
  };
  return <Contexto.Provider value={{ usuario, gate, erroGate, ativo, travado, voltarAoPortao: () => setGate("negado"), pendente, mes, mudarMes: setMes, oculto, alternarPrivacidade: () => setOculto((v) => !v), perfil, escolhendo, salvandoPerfil, mudarPerfil, layout, organizar, dados, aviso, atualizar: () => setVersao((v) => v + 1), atualizando, dias, mudarDias: setDias, versao, operacao, falhou }}>{children}</Contexto.Provider>;
}
/** Navigator permanece montado; nenhuma tela financeira monta antes do portão. */
export function PortaoPainel({ children }: { children: ReactNode }) {
 const p = useEstadoPainel(); const sessao = useSessao(); const [erroSaida, setErroSaida] = useState<string | null>(null);
 if (!p.ativo || p.travado) return <View />;
 if (p.gate === "negado") return <Inicio />;
 if (p.gate !== "liberado" || !p.usuario) return <Screen><View style={{ gap: 16, flex: 1, justifyContent: "center" }}><ActivityIndicator accessibilityLabel="Conferindo seu acesso" /><Texto>{p.gate === "erro" ? p.erroGate : "Conferindo seu acesso…"}</Texto>{erroSaida && <Texto tom="danger">{erroSaida}</Texto>}<Button rotulo="Conferir acesso" onPress={p.voltarAoPortao} /><Button rotulo="Sair" variante="secondary" onPress={() => void sessao.sair().then((ok) => { if (!ok) setErroSaida("Não conseguimos sair. Tente de novo."); })} /></View></Screen>;
 return <>{children}</>;
}
