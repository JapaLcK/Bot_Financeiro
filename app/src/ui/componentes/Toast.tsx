import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AccessibilityInfo, Animated, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { duracoes, facilitador, useReduzirMovimento } from "@/ui/motion";
import { espaco, type Paleta } from "@/ui/tokens";

import { Card } from "./Card";
import { Icone, type NomeIcone } from "./Icone";
import { Texto } from "./Texto";

type Tom = "sucesso" | "info" | "aviso" | "erro";

interface Opcoes {
  mensagem: string;
  tom?: Tom;
}

interface ContextoToast {
  mostrar: (opcoes: Opcoes) => void;
}

const Contexto = createContext<ContextoToast | null>(null);

const VISUAL: Record<Tom, { tom: keyof Paleta; icone: NomeIcone }> = {
  sucesso: { tom: "positive", icone: "CheckCircle" },
  info: { tom: "ink", icone: "Info" },
  aviso: { tom: "warning", icone: "WarningCircle" },
  erro: { tom: "danger", icone: "WarningOctagon" },
};

const DURACAO_VISIVEL = 3000;

export function useToast(): ContextoToast {
  const contexto = useContext(Contexto);
  if (!contexto) throw new Error("useToast() precisa de um <ToastProvider> por cima na árvore.");
  return contexto;
}

/**
 * Fila de UM: o segundo `mostrar()` SUBSTITUI o primeiro (troca o texto sem
 * tocar a animação de entrada de novo, já que o toast já está na tela) e
 * reinicia os 3s — nunca empilha dois toasts.
 *
 * Posicionamento: `ToastVisual` é `position: absolute` fixo na área segura
 * inferior da TELA. Se `<Screen>` (que rola) ficar por DENTRO do provedor, o
 * `absolute` resolve contra o contêiner de conteúdo do `ScrollView` — que
 * dimensiona pelo CONTEÚDO, não pela viewport, e recorta o que sai da sua
 * própria área — em vez da tela inteira. Monte `<ToastProvider>` por FORA de
 * `<Screen>` (como em `app/_ds/formulario.tsx`); dentro de uma caixa de
 * altura fixa e não rolável (como o catálogo em `src/ui/ds/estado.tsx`) é a
 * exceção deliberada, não o padrão.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [conteudo, setConteudo] = useState<{ mensagem: string; tom: Tom } | null>(null);
  const posicao = useRef(new Animated.Value(0)).current;
  const cronometro = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Animação de SAÍDA em andamento (ou `null`). Guarda quem chamou
  // `setConteudo(null)` no callback é a saída CORRENTE, não uma anterior já
  // cancelada — sem isso, `mostrar()` chegando nos 150ms da saída via um
  // `mostrar()` novo tinha o conteúdo NOVO apagado pelo callback da saída
  // VELHA (o `.start(callback)` incondicional de antes).
  const saida = useRef<Animated.CompositeAnimation | null>(null);
  // Marcado dentro do updater de `mostrar()` (decide ali, com `atual`
  // fresco), lido pelo `useEffect` abaixo que de fato dispara a entrada — ver
  // o comentário do efeito para o porquê de não chamar `.start()` no updater.
  const precisaAnimarEntrada = useRef(false);
  const reduzir = useReduzirMovimento();

  useEffect(() => () => {
    if (cronometro.current) clearTimeout(cronometro.current);
  }, []);

  const agendarSaida = useCallback(() => {
    if (cronometro.current) clearTimeout(cronometro.current);
    cronometro.current = setTimeout(() => {
      // Saída (`duracoes.feedback`, 150ms) mais rápida que a entrada
      // (`duracoes.transicao`, 250ms) — emil-design-eng.
      const animacao = Animated.timing(posicao, { toValue: 0, duration: duracoes.feedback, easing: facilitador, useNativeDriver: true });
      saida.current = animacao;
      animacao.start(() => {
        // Só limpa se ESTA ainda é a saída corrente — `mostrar()` pode ter
        // cancelado e trocado por outra enquanto a animação corria.
        if (saida.current === animacao) {
          saida.current = null;
          setConteudo(null);
        }
      });
    }, DURACAO_VISIVEL);
  }, [posicao]);

  const mostrar = useCallback(
    ({ mensagem, tom = "info" }: Opcoes) => {
      if (!mensagem.trim()) return;
      AccessibilityInfo.announceForAccessibility(mensagem);
      setConteudo((atual) => {
        const saidaEmAndamento = saida.current;
        const haviaSaidaEmAndamento = saidaEmAndamento !== null;
        if (saidaEmAndamento) {
          // Zera ANTES de `.stop()`, não depois: no RN real `.stop()` invoca o
          // callback de saída de forma SÍNCRONA (`{finished: false}` —
          // `Animation.stop()` chama `__notifyAnimationEnd()`, que chama o
          // `onEnd` na hora — ver `TimingAnimation.js`/`Animation.js` no
          // pacote). Se `saida.current` ainda apontasse para esta mesma
          // animação quando o callback rodar, a guarda dele (`agendarSaida`,
          // abaixo) veria `saida.current === animacao` e chamaria
          // `setConteudo(null)` de forma reentrante — DENTRO deste updater —
          // apagando o conteúdo que estamos prestes a definir.
          saida.current = null;
          saidaEmAndamento.stop();
        }
        // Reanima a entrada sempre que a posição não está parada em 1: ou é a
        // primeira exibição (`atual` nulo, posição em 0), ou é uma saída
        // interrompida no meio (posição entre 0 e 1) — nunca quando o toast
        // já está visível e parado. "há conteúdo" (`atual`) não é o mesmo
        // teste que "está visível": um toast pode ter conteúdo e já estar a
        // meio caminho de sumir. Só MARCA aqui — quem chama `.start()` é o
        // `useEffect` abaixo, depois que `ToastVisual` já montou.
        precisaAnimarEntrada.current = !atual || haviaSaidaEmAndamento;
        if (!atual) posicao.setValue(0);
        return { mensagem, tom };
      });
      agendarSaida();
    },
    [agendarSaida, posicao],
  );

  // Dispara a animação de ENTRADA só depois que `ToastVisual` já está montado
  // e conectado ao driver nativo — nunca de dentro do updater de `mostrar()`.
  // Antes disso: `Animated.View` ainda não existe na árvore quando o updater
  // roda (o updater executa DURANTE o render que cria `conteudo`, antes do
  // commit), então `.start({ useNativeDriver: true })` fazia
  // `AnimatedValue.__makeNative()` e mandava o `startAnimatingNode` para o
  // nativo (node_modules/react-native/Libraries/Animated/animations/
  // Animation.js:125-178) sem nenhuma view ligada ao grafo ainda — a conexão
  // real só acontece quando `ToastVisual` monta, via `AnimatedProps.__attach`
  // (.../nodes/AnimatedProps.js:212-219, chamado no `useInsertionEffect` de
  // `useAnimatedProps`) e depois `setNativeView`/`#connectAnimatedView`
  // (.../nodes/AnimatedProps.js:263-272, no ref callback da fase de layout).
  // Se essa conexão perde a corrida contra a animação de 250ms (que só roda
  // UMA vez), a view nunca recebe frame nenhum: o primeiro commit já pintou
  // opacity com o valor JS ainda em 0 (`AnimatedValue._value`, só sincronizado
  // de volta no fim da animação nativa, e nada força um novo commit depois
  // disso) — opacidade travada em 0 para sempre, sem erro nenhum. Efeito
  // depois do commit garante a ordem: monta e conecta primeiro, anima depois.
  useEffect(() => {
    if (!conteudo || !precisaAnimarEntrada.current) return;
    precisaAnimarEntrada.current = false;
    Animated.timing(posicao, { toValue: 1, duration: duracoes.transicao, easing: facilitador, useNativeDriver: true }).start();
  }, [conteudo, posicao]);

  const valor = useMemo(() => ({ mostrar }), [mostrar]);

  return (
    <Contexto.Provider value={valor}>
      {children}
      {conteudo ? <ToastVisual mensagem={conteudo.mensagem} tom={conteudo.tom} posicao={posicao} reduzir={reduzir} /> : null}
    </Contexto.Provider>
  );
}

function ToastVisual(props: { mensagem: string; tom: Tom; posicao: Animated.Value; reduzir: boolean }) {
  const insets = useSafeAreaInsets();
  const visual = VISUAL[props.tom];
  // Mesma direção na entrada e na saída (emil-design-eng): sobe pelo mesmo
  // eixo nos dois sentidos, nunca lados diferentes. Reduzir movimento tira o
  // deslocamento e mantém só a opacidade (a mesma regra do `usePressao`).
  const estiloMovimento = props.reduzir
    ? { opacity: props.posicao }
    : {
        opacity: props.posicao,
        transform: [{ translateY: props.posicao.interpolate({ inputRange: [0, 1], outputRange: [espaco.xxl, 0] }) }],
      };

  return (
    <Animated.View
      testID="toast"
      pointerEvents="none"
      style={[
        { position: "absolute", left: espaco.lg, right: espaco.lg, bottom: insets.bottom + espaco.lg },
        estiloMovimento,
      ]}
    >
      <Card elevacao="raised">
        <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.md }}>
          <Icone nome={visual.icone} tom={visual.tom} />
          <Texto variante="corpo" tom="ink" style={{ flex: 1 }}>
            {props.mensagem}
          </Texto>
        </View>
      </Card>
    </Animated.View>
  );
}
