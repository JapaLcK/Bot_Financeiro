import { useFocusEffect } from "expo-router";
import * as SecureStore from "expo-secure-store";
import { createContext, Fragment, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { z } from "zod";
import { ActivityIndicator, View } from "react-native";
import { ContratoInvalido, ErroDeApi, RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import type { Perfil } from "@/api/schemas/auth";
import * as S from "@/api/schemas/painel";
import { useSessao } from "@/features/auth/sessao";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { temTampa } from "@/features/bloqueio/tampa";
import { carregarAcessoBancario, conferirSessao } from "@/features/openFinance/acesso";
import { useForeground } from "@/features/openFinance/useForeground";
import { lerCredenciais, lerTentativaBancaria, type Credenciais } from "@/storage/secure";
import { lerRecurso, salvarPerfil } from "@/services/painel";
import { usePrivacidade } from "./privacidade";
import { Screen } from "@/ui/componentes/Screen";
import { Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { chaveLayout, mesAtual, mesesAnteriores, sanitizarLayout, type Widget } from "./catalogo";
import Inicio from "../../../app/(app)/index";

export const schemas = S.schemasRecurso;
export type Recurso = keyof typeof schemas;
const MENSAIS: Recurso[] = ["resumo", "detalhes", "detalhesAnterior"];
export type Dados = { [K in Recurso]: z.output<typeof schemas[K]> };
export type Resultado<T> = { fase: "carregando" } | { fase: "pronto"; dado: T } | { fase: "erro" | "negado"; mensagem: string };
type Resultados = { [K in Recurso]?: Resultado<Dados[K]> };
type Painel = { usuario: Perfil | null; gate: "carregando" | "liberado" | "negado" | "erro"; erroGate: string; ativo: boolean; travado: boolean; voltarAoPortao: () => void; pendente: boolean; mes: string; mudarMes: (m: string) => void; oculto: boolean; alternarPrivacidade: () => void; perfil: S.PerfilPainel; escolhendo: boolean; salvandoPerfil: boolean; mudarPerfil: (p: S.PerfilPainel) => Promise<void>; layout: Widget[]; organizar: (ids: Widget[] | null) => void; dados: Resultados; aviso: string | null; atualizar: () => void; atualizando: boolean; dias?: number; mudarDias: (d: number) => void; relerPrevisao: () => void; relerMetas: (forcar?: boolean) => void; versao: number; operacao: () => { controlador: AbortController; atual: () => boolean }; falhou: (e: unknown) => void };
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
  return ({ contas: base + "contas", resumo: base + `resumo-do-mes?mes=${mes}`, detalhes: base + `mes-detalhes?mes=${mes}`, detalhesAnterior: base + `mes-detalhes?mes=${mesesAnteriores(mes, 2)[1]}`, patrimonio: base + "patrimonio", rendimento: base + "rendimento", previsao: base + "previsao" + (dias ? `?dias=${dias}` : ""), assinaturas: base + "assinaturas", metas: base + "metas", cartoes: `/cards/${uid}/summary`, parcelas: `/installments/${uid}/list`, renda: `/analytics/${uid}/evolution?months=6`, insights: `/insights/${uid}/current`, plano: base + "me", categorias: base + "categorias" })[k];
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
  const { oculto, alternar: alternarPrivacidade, mostrar: mostrarValores } = usePrivacidade();
  const [perfil, setPerfil] = useState<S.PerfilPainel>("padrao");
  const [escolhendo, setEscolhendo] = useState(false);
  const [salvandoPerfil, setSalvandoPerfil] = useState(false);
  const [layout, setLayout] = useState<Widget[]>(sanitizarLayout(null, "padrao"));
  const [dados, setDados] = useState<Resultados>({});
  const [aviso, setAviso] = useState<string | null>(null);
  const [dias, setDias] = useState<number>();
  const [versao, setVersao] = useState(0);
  const [releitura, setReleitura] = useState(0);
  const [releituraMetas, setReleituraMetas] = useState(0);
  const [atualizando, setAtualizando] = useState(false);
  const geracao = useRef(0);
  const geracaoAcesso = useRef(0);
  const acessoValidando = useRef(true);
  const dono = useRef<number | null>(null);
  const controladores = useRef(new Set<AbortController>());
  const leituras = useRef(new Map<Recurso, AbortController>());
  const ultimaCarga = useRef<{ uid: number; versao: number; mes: string; dias?: number; releitura: number; releituraMetas: number; geracao: number } | null>(null);
  const emVoo = useRef(false);
  const leituraPerfil = useRef<AbortController | null>(null);
  const layouts = useRef(new Map<S.PerfilPainel, Widget[]>());
  const filaStorage = useRef(Promise.resolve());
  const sessaoRef = useRef(sessao); sessaoRef.current = sessao;
  const versaoRef = useRef(versao); versaoRef.current = versao;
  const gateRef = useRef(gate); gateRef.current = gate;
  const silenciosa = useRef(false); // refoco da tela já liberada, mesmo dono: a próxima carga completa vai por cima do dado bom
  const credencialValidada = useRef<Credenciais | null>(null); // de quem é a última validação bem-sucedida: o refoco que falha por rede/5xx só mantém a tela se a SESSÃO ainda for a mesma (`jti`; o access pode ter sido renovado, e isso deve passar)
  const pediuAtualizar = useRef(false); // pull-to-refresh durante a revalidação: a carga que vem depois acende o RefreshControl
  const quietas = useRef(new WeakSet<AbortController>()); // leitura silenciosa não acende o RefreshControl
  // Estável: a tela o passa ao `useFocusEffect`. Se as metas já estão a caminho (carga completa por vir ou em voo, como no foco do mount), a resposta é fresca: não relê de novo. `forcar` (uma gravação terminou depois disso) supera a leitura em voo, que nasceu antes dela.
  const relerMetas = useCallback((forcar?: boolean) => { const a = ultimaCarga.current; if (!a || a.uid !== dono.current || a.versao !== versaoRef.current || a.geracao !== geracao.current || (!forcar && leituras.current.has("metas"))) return; setReleituraMetas((v) => v + 1); }, []);
  const cancelar = useCallback(() => { geracao.current++; for (const c of controladores.current) c.abort(); controladores.current.clear(); leituras.current.clear(); }, []);
  useEffect(() => cancelar, [cancelar]);
  const falhou = useCallback((e: unknown) => {
    if (e instanceof SessaoExpirada) sessaoRef.current.expirou(e.detalhe);
    else if (e instanceof ErroDeApi && (e.status === 402 || (e.status === 403 && typeof e.corpo === "object" && e.corpo && JSON.stringify(e.corpo).includes("open_finance_onboarding_required")))) { cancelar(); setDados({}); setGate("negado"); } // 402 e 403 do onboarding: o portão, para toda chamada que passa por aqui (leituras e escritas: recursos, Extrato, chat, perfil, assinaturas, leitura sob demanda)
  }, [cancelar]);
  useEffect(() => { if (travado) setGate("carregando"); }, [travado]); // só a trava biométrica volta ao portão; o segundo plano sem trava (`!ativo`, de qualquer duração) mantém a tela e revalida em silêncio no focus effect (o `refoco` abaixo). A tampa nativa cobre o app switcher
  useFocusEffect(useCallback(() => {
    if (!ativo || travado) { cancelar(); return; }
    let cancelado = false;
    const g = ++geracaoAcesso.current;
    const refoco = gateRef.current === "liberado";
    acessoValidando.current = true; pediuAtualizar.current = false;
    const credencial = lerCredenciais().catch(() => null);
    if (!refoco) setGate("carregando");
    void carregarAcessoBancario().then(async (a) => {
      const tentativa = a.fase === "inicio" ? await lerTentativaBancaria(a.perfil.user_id) : null;
      const cred = await credencial;
      if (cancelado || g !== geracaoAcesso.current) return;
      if (a.fase !== "inicio") { setDados({}); setUsuario(null); setGate("negado"); return; }
      const mesmo = dono.current === a.perfil.user_id;
      if (!mesmo) {
        dono.current = a.perfil.user_id; layouts.current.clear(); setDados({}); mostrarValores(); setPerfil("padrao"); setDias(undefined); setMes(mesAtual());
      } else {
        const atual = mesAtual(), inicio = a.perfil.history_earliest_date?.slice(0, 7);
        setMes((m) => m > atual ? atual : inicio && m < inicio ? inicio : m);
      }
      credencialValidada.current = cred;
      silenciosa.current = refoco && mesmo && !pediuAtualizar.current;
      acessoValidando.current = false; setPendente(!!tentativa); setUsuario(a.perfil); setGate("liberado"); setVersao((v) => v + 1);
    }).catch((e: unknown) => {
      if (cancelado || g !== geracaoAcesso.current) return;
      if (e instanceof RequisicaoSuperada) { if (refoco) setGate("carregando"); return; }
      falhou(e);
      if (refoco) { // rede/5xx na revalidação não derruba a tela liberada, desde que o token seja o da última validação (o 402 e o 403 de onboarding já foram ao portão em `falhou`; um 403 de recurso vai a `negado` na carga de dados)
        conferirSessao(credencialValidada.current).then(() => { if (cancelado || g !== geracaoAcesso.current) return; silenciosa.current = !pediuAtualizar.current; acessoValidando.current = false; setVersao((v) => v + 1); },
          () => { if (cancelado || g !== geracaoAcesso.current) return; setErroGate(mensagemRecurso(e)); setGate((x) => x === "negado" ? x : "erro"); }); // outra conta no cofre e a identidade não confirmada: portão, sem liberar a carga
        return;
      }
      setErroGate(mensagemRecurso(e)); setGate("erro");
    });
    return () => { cancelado = true; geracaoAcesso.current++; acessoValidando.current = true; cancelar(); };
  }, [ativo, travado, cancelar, falhou]));
  useEffect(() => {
    if (gate !== "liberado" || acessoValidando.current || !usuario || !ativo || travado) return;
    const uid = usuario.user_id, anterior = ultimaCarga.current;
    const completa = !anterior || anterior.uid !== uid || anterior.versao !== versao || anterior.geracao !== geracao.current;
    const recursos: Recurso[] = completa ? Object.keys(schemas) as Recurso[] : [
      ...(anterior.mes !== mes ? MENSAIS : []),
      ...(anterior.dias !== dias || anterior.releitura !== releitura ? ["previsao"] as const : []), // UMA condição: dois spreads pediriam a previsão duas vezes
      ...(anterior.releituraMetas !== releituraMetas ? ["metas"] as const : []), // a aba Metas relê ao abrir: a meta pode ter sido criada no chat
    ];
    // Não cancela aqui (#849): o efeito do pai roda depois do dos filhos e mataria a página recém-pedida. Quem pede a recarga cancela antes (atualizar, cleanup do portão); o setVersao do finally de mudarPerfil pode vir sem cancelar (limite de 15s do PUT), e por isso as leituras se superam por recurso e a de perfil pela mais nova.
    const quieta = completa && silenciosa.current; if (completa) silenciosa.current = false;
    if (completa && !quieta) setDados({});
    const g = geracao.current;
    ultimaCarga.current = { uid, versao, mes, dias, releitura, releituraMetas, geracao: g };
    if (!recursos.length) return;
    const soMetas = !completa && recursos.every((k) => k === "metas"); // releitura de foco: silenciosa (sem RefreshControl, e o dado bom fica na tela)
    const chaveMudou = (k: Recurso) => !!anterior && ((anterior.mes !== mes && MENSAIS.includes(k)) || (anterior.dias !== dias && k === "previsao")); // mês/dias novos: o dado velho é de outra chave
    const quieto = (k: Recurso) => soMetas || (quieta && !chaveMudou(k));
    const visivelEmVoo = () => [...leituras.current.values()].some((x) => !quietas.current.has(x));
    setAtualizando(recursos.some((k) => !quieto(k)) || visivelEmVoo()); // escreve o false também: o blur abortou a carga visível e o `finally` dela não limpa (geração velha)
    if (!completa || quieta) setDados((d) => ({ ...d, ...Object.fromEntries(recursos.map((k) => [k, quieto(k) && d[k]?.fase === "pronto" ? d[k] : { fase: "carregando" }])) }));
    const carregar = async <K extends Recurso>(k: K) => {
      const anterior = leituras.current.get(k);
      anterior?.abort(); if (anterior) controladores.current.delete(anterior);
      const c = new AbortController(); leituras.current.set(k, c); controladores.current.add(c); if (quieto(k)) quietas.current.add(c);
      // Seletores superam somente a leitura deste recurso; refresh e acesso superam toda a geração.
      const atual = () => !c.signal.aborted && g === geracao.current && dono.current === uid && leituras.current.get(k) === c;
      try {
        // Só economia de requisição: o servidor responderia 200 (com `inicio_do_historico`) para o mês anterior ao corte, e o `anterior` do resumo já vem null nesse caso; aqui nem pede e cai em "negado" (o card segue sem a comparação).
        const inicio = usuario.history_earliest_date?.slice(0, 7);
        if (k === "detalhesAnterior" && inicio && mesesAnteriores(mes, 2)[1]! < inicio) { if (atual()) setDados((d) => ({ ...d, [k]: { fase: "negado", mensagem: "Mês anterior fora do seu histórico." } })); return; }
        const dado = await lerRecurso(rota(k, uid, mes, dias), schemas[k] as unknown as z.ZodType<Dados[K]>, c);
        if (atual()) setDados((d) => ({ ...d, [k]: { fase: "pronto", dado } }));
      } catch (e) {
        if (!atual() || e instanceof RequisicaoSuperada) return;
        falhou(e);
        if (!atual()) return;
        const negado = e instanceof ErroDeApi && e.status === 403;
        setDados((d) => quieto(k) && (soMetas || !negado) && d[k]?.fase === "pronto" ? d : { ...d, [k]: { fase: negado ? "negado" : "erro", mensagem: mensagemRecurso(e) } }); // releitura de foco que falha: o dado velho fica (menos num 403 de recurso: o acesso foi revogado, o dado não pode ficar)
      } finally {
        controladores.current.delete(c);
        if (leituras.current.get(k) === c) leituras.current.delete(k);
        if (g === geracao.current && dono.current === uid) setAtualizando(visivelEmVoo());
      }
    };
    void Promise.all(recursos.map(carregar));
    if (completa && !emVoo.current) {
      leituraPerfil.current?.abort(); // a leitura de perfil de uma carga completa supera a anterior (como `leituras`, fora dele: não conta em `atualizando`)
      const c = new AbortController(); leituraPerfil.current = c; controladores.current.add(c);
      const atual = () => !c.signal.aborted && g === geracao.current && dono.current === uid && leituraPerfil.current === c;
      void lerRecurso("/api/app/perfil", S.perfilPainelSchema, c).then((p) => { if (atual() && !emVoo.current) { setPerfil(p.perfil ?? "padrao"); setEscolhendo(p.perfil === null); } }).catch((e: unknown) => { if (atual() && !(e instanceof RequisicaoSuperada)) { falhou(e); setAviso("Não conseguimos carregar seu perfil. Tente atualizar."); } }).finally(() => { controladores.current.delete(c); if (leituraPerfil.current === c) leituraPerfil.current = null; });
    }
  }, [gate, usuario, mes, dias, releitura, releituraMetas, versao, ativo, travado, cancelar, falhou]);
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
  // Chave nova com a revalidação do refoco em voo: o efeito de carga espera o acesso, então o dado velho sairia sob o rótulo novo. Marca "carregando"; a carga que vem depois lê o novo.
  const trocouChave = (recursos: Recurso[]) => { if (acessoValidando.current) setDados((d) => ({ ...d, ...Object.fromEntries(recursos.map((k) => [k, { fase: "carregando" }])) })); };
  // atualizar cancela no pedido, não no efeito: o efeito do pai roda depois do dos filhos e mataria a página recém-pedida (#849).
  return <Contexto.Provider value={{ usuario, gate, erroGate, ativo, travado, voltarAoPortao: () => setGate("negado"), pendente, mes, mudarMes: (m) => { setMes(m); if (m !== mes) trocouChave(MENSAIS); }, oculto, alternarPrivacidade, perfil, escolhendo, salvandoPerfil, mudarPerfil, layout, organizar, dados, aviso, atualizar: () => { cancelar(); pediuAtualizar.current = true; setVersao((v) => v + 1); }, atualizando, dias, mudarDias: (d) => { setDias(d); if (d !== dias) trocouChave(["previsao"]); }, relerPrevisao: () => setReleitura((v) => v + 1), relerMetas, versao, operacao, falhou }}>{children}</Contexto.Provider>;
}
/** Navigator permanece montado; nenhuma tela financeira monta antes do portão. */
export function PortaoPainel({ children }: { children: ReactNode }) {
 const p = useEstadoPainel(); const sessao = useSessao(); const [erroSaida, setErroSaida] = useState<string | null>(null);
 if (p.travado || (!p.ativo && !temTampa)) return <View />; // sem tampa nativa (Android, Expo Go) o fundo desmonta a árvore
 if (p.gate === "negado") return <Inicio />;
 if (p.gate !== "liberado" || !p.usuario) return <Screen><View style={{ gap: 16, flex: 1, justifyContent: "center" }}><ActivityIndicator accessibilityLabel="Conferindo seu acesso" /><Texto>{p.gate === "erro" ? p.erroGate : "Conferindo seu acesso…"}</Texto>{erroSaida && <Texto tom="danger">{erroSaida}</Texto>}<Button rotulo="Conferir acesso" onPress={p.voltarAoPortao} /><Button rotulo="Sair" variante="secondary" onPress={() => void sessao.sair().then((ok) => { if (!ok) setErroSaida("Não conseguimos sair. Tente de novo."); })} /></View></Screen>;
 return <Fragment key={p.usuario.user_id}>{children}</Fragment>;
}
