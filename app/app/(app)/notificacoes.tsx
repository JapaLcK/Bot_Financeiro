import DateTimePicker, { DateTimePickerAndroid } from "@react-native-community/datetimepicker";
import { router, useFocusEffect } from "expo-router";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Platform, Switch, View } from "react-native";

import { ErroDeApi, SessaoExpirada, conferirLinhagem } from "@/api/client";
import type { Notificacoes } from "@/api/schemas/conta";
import { EsqueciSenha } from "@/features/auth/EsqueciSenha";
import { semSenha, telefoneBR } from "@/features/conta/contato";
import { BlocoSemPlano, Desligou, NotaDaLinha, type EventoDesligou } from "@/features/conta/AvisoInsight";
import { DESLIGADOS, DESLIGADOS_D5, FALHA_CARREGAR, TITULO_AVISO, algumResumo, avisoSemPlano, corpoDaHora, hhmm, linhaDoAviso, semanalNaTela, semanalTravado, textoDaEscrita } from "@/features/conta/notificacoes";
import { usePermissaoPush } from "@/features/push/usePermissaoPush";
import { useFalha } from "@/features/seguranca/falha";
import { useVoltaDoFundo } from "@/features/seguranca/voltaDoFundo";
import { perfil } from "@/services/auth";
import { desligarAviso, lerNotificacoes, salvarNotificacoes, type CorpoNotificacoes } from "@/services/conta";
import { lerCredenciais } from "@/storage/secure";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

type Estado =
  | { fase: "carregando" }
  | { fase: "erro" }
  | { fase: "plano"; aviso: { uid: number; texto: string } | null } // `aviso`: cortada com adesão viva (D2), só desligar
  | { fase: "exclusao" }
  | { fase: "senha"; email: string | null }
  | { fase: "pronto"; uid: number; dados: Notificacoes; emailDaConta: string | null };

type CampoBool = "tip_email_enabled" | "insight_email_enabled" | "whatsapp_updates_enabled" | "daily_report_enabled" | "weekly_report_enabled" | "monthly_report_enabled" | "push_bank_reconnect_enabled" | "insight_alert_email_enabled";

const dataDaHora = (h: number, m: number) => {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d;
};

/** Cabeçalho de um grupo: ícone + o destino (e-mail ou telefone), sem rótulo visível; a palavra só existe para o leitor de tela. */
function Destino({ icone, rotulo, valor, children }: { icone: NomeIcone; rotulo: string; valor: string; children?: ReactNode }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: espaco.md }}>
      <View accessible accessibilityLabel={`${rotulo}, ${valor}`} style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm, flexShrink: 1 }}>
        <Icone nome={icone} tamanho={20} tom="inkMuted" />
        <Texto tom="inkMuted" numberOfLines={1} style={{ flexShrink: 1 }}>{valor}</Texto>
      </View>
      {children}
    </View>
  );
}

/**
 * Preferências de notificação: e-mail, WhatsApp e resumos. Uma escrita (PATCH) por controle, no máximo uma em voo;
 * a resposta do PATCH é a verdade (a tela só adianta o valor tocado até ela chegar). Falhou: relê, nunca reenvia.
 * PII: e-mail e telefone só vão para a tela, nunca para log ou analytics.
 */
export default function NotificacoesTela() {
  const { cores, esquema } = useTema();
  const falha = useFalha();
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });
  const [previa, setPrevia] = useState<Partial<Notificacoes>>({}); // o valor tocado, só na tela, até a resposta
  const [aviso, setAviso] = useState<string | null>(null);
  const [gravando, setGravando] = useState(false);
  const [desligou, setDesligou] = useState<EventoDesligou | null>(null); // "Avisos ... desligados.": um evento; visível até a próxima escrita ou a troca de conta
  const geracao = useRef(0); // só a ÚLTIMA carga escreve estado; toda escrita invalida a leitura que estava em voo
  const voando = useRef(false); // trava síncrona: dois toques no mesmo tick não passam
  const releitura = useRef(false); // leitura pedida durante a escrita (foco, volta do fundo, falha): roda quando ela termina
  const rascunho = useRef<Date | null>(null); // iOS: a hora que o usuário está girando; só vale ao fechar o seletor
  const agora = useRef(estado); // o estado ATUAL: callbacks que o nativo guarda (seletor do Android) nascem num render e confirmam em outro
  agora.current = estado;

  /** Leitura que muda a fase leva o banner de erro da escrita (ele é da fase em que ela falhou); "desligados" fica, calado. */
  const entrar = (novo: Estado) => {
    if (novo.fase !== agora.current.fase) setAviso(null);
    setEstado(novo);
  };

  /** Fases que valem para leitura e escrita. `true` = a tela já mudou de fase. */
  const fase = (e: unknown, email: string | null, uid: number | null) => {
    if (!(e instanceof ErroDeApi)) return false;
    if (e.status === 402) {
      const texto = avisoSemPlano(e);
      entrar({ fase: "plano", aviso: texto && uid !== null ? { uid, texto } : null });
    }
    else if (e.status === 403 && /exclus/i.test(e.detalhe)) entrar({ fase: "exclusao" });
    else if (semSenha(e)) entrar({ fase: "senha", email });
    else return false;
    return true;
  };

  const carregar = async () => {
    if (voando.current) return void (releitura.current = true);
    const minha = ++geracao.current;
    let emailDaConta: string | null = null, uid: number | null = null;
    try {
      const esperada = await lerCredenciais();
      const conta = await perfil();
      emailDaConta = conta.email?.trim() || null;
      uid = conta.user_id;
      // A conta trocada ENTRE as duas leituras faz a 2ª sair com o token novo e o uid antigo: o 403 de uid alheio vira RequisicaoSuperada.
      const dados = await lerNotificacoes(conta.user_id).catch(async (e: unknown) => {
        if (!(e instanceof SessaoExpirada)) await conferirLinhagem(esperada);
        throw e;
      });
      if (minha !== geracao.current) return;
      entrar({ fase: "pronto", uid: conta.user_id, dados, emailDaConta });
    } catch (e) {
      if (e instanceof SessaoExpirada) return void falha(e); // sessão expirada vale de qualquer geração
      if (minha !== geracao.current || fase(e, emailDaConta, uid)) return;
      if (falha(e) === null) { // RequisicaoSuperada: a conta trocou no meio; some o dado da anterior e relê (como na escrita)
        setDesligou(null);
        entrar({ fase: "carregando" });
        return void carregar();
      }
      // Recarga com dados na tela mantém a tela: trocar por erro desmontaria o que está sendo usado.
      if (agora.current.fase !== "pronto") setAviso(null);
      setEstado((atual) => (atual.fase === "pronto" ? atual : { fase: "erro" }));
    }
  };

  const push = usePermissaoPush();
  useFocusEffect(useCallback(() => { void carregar(); push.ler(); }, [])); // `[]` de propósito: `falha` muda a cada estado da sessão (ver perfil.tsx)
  useVoltaDoFundo(() => { void carregar(); push.ler(); });

  const escrever = async (corpo: CorpoNotificacoes, adiantar: Partial<Notificacoes>) => {
    const a = agora.current; // a última RESPOSTA do servidor, nunca a prévia
    const uid = a.fase === "pronto" ? a.uid : a.fase === "plano" ? a.aviso?.uid : undefined;
    if (voando.current || uid === undefined) return;
    const emailDaConta = a.fase === "pronto" ? a.emailDaConta : null;
    voando.current = true;
    geracao.current++;
    setGravando(true);
    setAviso(null);
    setDesligou(null);
    setPrevia(adiantar);
    try {
      if (corpo.insight_alert_email_enabled === false) {
        // D2: desligar a adesão vai SEMPRE com o corpo exato; a resposta pode ser a curta (cortada entre a leitura e o toque). Relê.
        const r = await desligarAviso(uid);
        releitura.current = true;
        if (a.fase === "pronto" && !("ok" in r)) setEstado({ fase: "pronto", uid, dados: r, emailDaConta }); // completa: notas e carência novas também
        else { // botão da fase `plano`, ou resposta curta (conta cortada ou sem senha): a tela de antes não vale; como o 402 da escrita, sai dela
          setEstado(a.fase === "plano" ? { fase: "plano", aviso: null } : { fase: "carregando" });
          setDesligou({ texto: DESLIGADOS, focar: true }); // o controle que levou até aqui sumiu
        }
      } else {
        const dados = await salvarNotificacoes(uid, corpo);
        setEstado({ fase: "pronto", uid, dados, emailDaConta });
        // D5: outra chave (o `false` da própria linha foi pelo ramo de cima) levou a adesão junto: desligar "Insights financeiros".
        if (a.fase === "pronto" && a.dados.insight_alert_email_enabled && !dados.insight_alert_email_enabled) setDesligou({ texto: DESLIGADOS_D5, focar: false });
      }
    } catch (e) {
      if (e instanceof SessaoExpirada) return void falha(e);
      if (e instanceof ErroDeApi && e.status === 402) { // o 402 da escrita não traz a adesão; só o GET diz se há o que desligar
        setEstado({ fase: "carregando" });
        releitura.current = true;
        return;
      }
      if (fase(e, emailDaConta, uid)) return;
      if (falha(e) === null) { // RequisicaoSuperada: a conta trocou no meio; some o dado da anterior e relê (o /auth/me reaprende o uid)
        setEstado({ fase: "carregando" });
        releitura.current = true;
        return;
      }
      releitura.current = true; // o que o servidor gravou (ou não) é a verdade; nada é reenviado
      setAviso(textoDaEscrita(e));
    } finally {
      voando.current = false;
      setPrevia({});
      setGravando(false);
      if (releitura.current) {
        releitura.current = false;
        void carregar();
      }
    }
  };

  const confirmarHora = (d: Date) => {
    if (agora.current.fase !== "pronto") return;
    const { dados } = agora.current; // a última RESPOSTA do servidor, nunca a prévia
    const h = d.getHours(), m = d.getMinutes();
    if (h === dados.daily_report_hour && m === dados.daily_report_minute) return;
    void escrever(corpoDaHora(h, m, dados.daily_report_enabled), { daily_report_hour: h, daily_report_minute: m });
  };

  const android = Platform.OS === "android";
  const v = estado.fase === "pronto" ? { ...estado.dados, ...previa } : null;

  /** Um Switch = um PATCH com SÓ o campo tocado, montado aqui com o valor do toque (nunca a partir do estado da tela). */
  const chave = (campo: CampoBool, rotulo: string, valor: boolean, opcoes: { travado?: boolean; selo?: string; dica?: string; adiantar?: (novo: boolean) => Partial<Notificacoes>; aoTrocar?: (novo: boolean) => void } = {}) => (
    <ListRow
      titulo={rotulo}
      desativado={opcoes.travado}
      trailing={
        <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm, flexShrink: 1 }}>
          {/* Selo que já vai no hint (Avisos): visível, mas fora da árvore de acessibilidade, para o leitor não ler duas vezes. */}
          {opcoes.selo ? <Texto variante="legenda" tom="inkMuted" style={{ flexShrink: 1 }} {...(opcoes.dica?.includes(opcoes.selo) && { accessibilityElementsHidden: true, importantForAccessibility: "no-hide-descendants" as const })}>{opcoes.selo}</Texto> : null}
          <Switch
            accessibilityLabel={rotulo}
            accessibilityHint={opcoes.dica || undefined}
            value={valor}
            disabled={opcoes.travado || gravando}
            onValueChange={(novo) => {
              if (opcoes.travado) return;
              if (opcoes.aoTrocar) return opcoes.aoTrocar(novo);
              void escrever({ [campo]: novo }, opcoes.adiantar?.(novo) ?? { [campo]: novo });
            }}
            trackColor={{ true: cores.acao }}
          />
        </View>
      }
    />
  );

  return (
    <Screen sobCabecalho>
      <View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
        {estado.fase === "carregando" && <ActivityIndicator color={cores.brand} accessibilityLabel="Carregando" />}
        {estado.fase === "plano" && <Texto>Escolha um plano para ver suas notificações.</Texto>}
        {estado.fase === "plano" && estado.aviso && <BlocoSemPlano texto={estado.aviso.texto} ocupado={gravando} aoDesligar={() => void escrever({ insight_alert_email_enabled: false }, {})} />}
        {estado.fase === "exclusao" && <Texto>Esta conta está agendada para exclusão.</Texto>}
        {estado.fase === "senha" && (estado.email ? <EsqueciSenha emailFixo={estado.email} /> : <Texto>Sua conta ainda não tem senha. Defina uma senha pelo site para ver suas notificações.</Texto>)}
        {estado.fase === "erro" && (
          <>
            <Texto tom="danger">{FALHA_CARREGAR}</Texto>
            <Button
              rotulo="Tentar de novo"
              onPress={() => {
                setEstado({ fase: "carregando" });
                void carregar();
              }}
            />
          </>
        )}
        {desligou && <Desligou evento={desligou} />}
        {aviso && <Banner tom="danger" mensagem={aviso} />}
        {v && (
          <>
            <Destino icone="Envelope" rotulo="E-mail" valor={v.email?.trim() || "—"} />
            <GrupoDeLinhas>
              {chave("tip_email_enabled", "Dicas de uso", v.tip_email_enabled, { travado: !v.email_notifications_available })}
              <>
                {chave("insight_email_enabled", "Insights financeiros", v.insight_email_enabled, { travado: !v.email_notifications_available, dica: v.insight_email_off_notice ?? undefined })}
                <NotaDaLinha textos={[v.insight_email_off_notice]} />
              </>
              {v.insight_alert_email_notice && (
                <>
                  {chave("insight_alert_email_enabled", TITULO_AVISO, v.insight_alert_email_enabled, linhaDoAviso(v))}
                  <NotaDaLinha textos={[v.insight_alert_email_notice, v.insight_alert_email_paused_notice]} />
                </>
              )}
            </GrupoDeLinhas>
            <Destino icone="WhatsappLogo" rotulo="WhatsApp" valor={telefoneBR(v.whatsapp_destination) || "—"}>
              {!v.whatsapp_updates_available && <Button rotulo="Conectar" variante="ghost" onPress={() => router.push("/perfil")} />}
            </Destino>
            <GrupoDeLinhas>
              {chave("whatsapp_updates_enabled", "Atualizações do Piggy", v.whatsapp_updates_enabled, { travado: !v.whatsapp_updates_available })}
              {chave("daily_report_enabled", "Resumo diário", v.daily_report_enabled)}
              {chave("weekly_report_enabled", "Resumo semanal", semanalNaTela(v), {
                travado: semanalTravado(v),
                selo: v.weekly_report_available ? undefined : v.weekly_report_stored_enabled ? "Pausado no seu plano" : "Plus e Pro",
                adiantar: (novo) => ({ weekly_report_enabled: novo, weekly_report_stored_enabled: novo }), // fora do plano a tela lê o gravado
              })}
              {chave("monthly_report_enabled", "Resumo mensal", v.monthly_report_enabled)}
              <ListRow
                titulo="Horário dos resumos"
                desativado={gravando || !algumResumo(v)}
                chevron={android}
                onPress={android ? () => { if (!gravando) DateTimePickerAndroid.open({ value: dataDaHora(v.daily_report_hour, v.daily_report_minute), mode: "time", is24Hour: true, onValueChange: (_, d) => confirmarHora(d) }); } : undefined}
                trailing={
                  android ? (
                    <Texto tom="inkMuted">{hhmm(v.daily_report_hour, v.daily_report_minute)}</Texto>
                  ) : (
                    <DateTimePicker
                      value={dataDaHora(v.daily_report_hour, v.daily_report_minute)}
                      mode="time"
                      display="compact"
                      themeVariant={esquema}
                      locale="pt-BR"
                      accessibilityLabel="Horário dos resumos"
                      disabled={gravando || !algumResumo(v)}
                      onValueChange={(_, d) => void (rascunho.current = d)}
                      onDismiss={() => {
                        const d = rascunho.current;
                        rascunho.current = null;
                        if (d) confirmarHora(d);
                      }}
                    />
                  )
                }
              />
            </GrupoDeLinhas>
            {/* iOS: o push é APNs. A chave liga a preferência só com a permissão do sistema concedida; negada manda aos Ajustes; indeterminada pede (docs/plano-push-app.md §2). */}
            {!android && (
              <>
                <Destino icone="Bell" rotulo="Notificações no celular" valor="No celular" />
                <GrupoDeLinhas>
                  {chave("push_bank_reconnect_enabled", "Banco precisa de atenção", push.permissao === "granted" && v.push_bank_reconnect_enabled, {
                    travado: push.permissao === null,
                    selo: push.permissao === "denied" ? "Nos Ajustes" : undefined,
                    aoTrocar: push.permissao === "granted"
                      ? (novo) => void escrever({ push_bank_reconnect_enabled: novo }, { push_bank_reconnect_enabled: novo })
                      // Tocou para LIGAR e concedeu: a preferência acompanha (se estava desligada no servidor, a chave não ficaria ligada).
                      : () => void push.tocar().then((concedeu) => { if (concedeu && !v.push_bank_reconnect_enabled) void escrever({ push_bank_reconnect_enabled: true }, { push_bank_reconnect_enabled: true }); }),
                  })}
                </GrupoDeLinhas>
              </>
            )}
          </>
        )}
      </View>
    </Screen>
  );
}
