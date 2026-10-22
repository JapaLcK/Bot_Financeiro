import { useEffect, useState, type ReactNode } from "react";
import { ActivityIndicator, View } from "react-native";
import { z } from "zod";
import { ErroDeApi, RequisicaoSuperada } from "@/api/client";
import type { CardAgente as Card } from "@/api/schemas/agentes";
import * as S from "@/api/schemas/painel";
import { lerRecurso } from "@/services/painel";
import { Acao, AvisoDeBancoNoCard, Bloco } from "./base";
import { BlocoContexto } from "./blocoContexto";
import { nomeMes } from "./catalogo";
import { CardCaixinhas } from "./cardCaixinhas";
import { CardContasPagar } from "./cardContasPagar";
import { CardInvestido } from "./cardInvestido";
import { CardOrcamentos } from "./cardOrcamentos";
import { CardComparacaoMeses, CardResumoMes, tituloMeses } from "./cardResumoMes";
import { Assinaturas } from "./complementos";
import { usePainel } from "./provider";
import { TextoPainel as Texto } from "./tipografia";

const meSchema = z.object({ user_id: z.number().int() });
type Resumo = z.output<typeof S.resumoSchema>;
/** `outroPeriodo`: a rota respondeu um mês ou dia que não é o do texto (virou entre a consulta e o card): nenhum card, nem erro. */
type Leitura = { titulo: string; ler: (c: AbortController) => Promise<unknown>; corpo: (d: unknown) => ReactNode; outroPeriodo?: (d: unknown) => boolean };
function L<T>(titulo: string, ler: (c: AbortController) => Promise<T>, corpo: (d: T) => ReactNode, outroPeriodo?: (d: T) => boolean): Leitura {
  return { titulo, ler, corpo: corpo as (d: unknown) => ReactNode, outroPeriodo: outroPeriodo as ((d: unknown) => boolean) | undefined };
}
const resumoDe = async (mes: string, c: AbortController): Promise<Resumo> => {
  const d = await lerRecurso(`/api/app/resumo-do-mes?mes=${mes}`, S.resumoSchema, c);
  if (d.mes !== mes) throw new ErroDeApi(200, "Mês diferente do pedido"); // contrato quebrado: nunca desenha o número de outro mês
  return d;
};

/** Para cada tipo: o título, a leitura (a rota `/api/app` do tipo, a mesma regra do que o agente leu), o corpo e, quando o card carrega período, o que é "outro período". Porta de `leituraDe` em `webapp/.../CardAgente.tsx`. */
function leituraDe(c: Card): Leitura {
  switch (c.tipo) {
    case "assinaturas": // só leitura: o `BlocoContexto` desliga as ações do widget
      return L("Assinaturas", (k) => lerRecurso("/api/app/assinaturas", S.assinaturasSchema, k), (d) => <BlocoContexto.Provider value={{ recursos: { assinaturas: d } }}><Assinaturas /></BlocoContexto.Provider>);
    case "resumo_mes":
      return L(`Resumo de ${nomeMes(c.mes)}`, (k) => resumoDe(c.mes, k), (d) => <CardResumoMes d={d} motivos={c.motivos} />);
    case "comparacao_meses": // duas leituras e uma conferência de conta depois das duas: falha de uma = o card inteiro falha, nunca um lado só
      return L(tituloMeses(c.meses), async (k) => Promise.all(c.meses.map((m) => resumoDe(m, k))) as Promise<[Resumo, Resumo]>, (d) => <CardComparacaoMeses d={d} meses={c.meses} motivos={c.motivos} />);
    case "orcamentos":
      return L(`Orçamentos de ${nomeMes(c.mes)}`, (k) => lerRecurso("/api/app/orcamentos-categoria", S.orcamentosCategoriaSchema, k), (d) => <CardOrcamentos d={d} />, (d) => d.mes !== c.mes);
    case "contas":
      return L("Contas a pagar", (k) => lerRecurso("/api/app/contas-a-pagar", S.contasAPagarSchema, k), (d) => <CardContasPagar d={d} />, (d) => d.hoje !== c.hoje);
    case "caixinhas":
      return L("Caixinhas", (k) => lerRecurso("/api/app/caixinhas", S.caixinhasV2Schema, k), (d) => <CardCaixinhas d={d} />);
    default:
      return L(c.tipo === "renda_fixa" ? "Renda fixa" : "Investimentos", (k) => lerRecurso("/api/app/investido", S.investidoSchema, k), (d) => <CardInvestido d={d} foco={c.tipo === "renda_fixa"} />);
  }
}

type Estado = { fase: "carregando" } | { fase: "nada" } | { fase: "erro" } | { fase: "pronto"; d: unknown; chave: string }; // `chave`: de quem e de qual card é o número; a releitura só o mantém se continuar a mesma
/**
 * O card com os números do tema, abaixo da resposta do agente. O servidor só diz QUAL; a rota `/api/app` lê o usuário da SESSÃO, não o `uid` do POST. Por isso a resposta só vale se
 * (1) a chamada não foi superada (troca de conta no cofre: `RequisicaoSuperada`), (2) o provider ainda é o mesmo (`op.atual()`) e (3) o `/auth/me` DEPOIS das leituras ainda é o dono do provider:
 * fecha a janela em que o cofre já é da conta B e o provider ainda é da A. Qualquer uma das três = nenhum número na tela.
 */
export function CardAgente({ card }: { card: Card }) {
  const p = usePainel(), l = leituraDe(card);
  const [e, setE] = useState<Estado>({ fase: "carregando" }), [tentativa, setTentativa] = useState(0);
  useEffect(() => {
    // o uid é defesa extra: a troca de conta já desmonta e reseta o card
    const op = p.operacao(), chave = `${p.usuario.user_id}|${JSON.stringify(card)}`;
    const mantem = (x: Estado) => x.fase === "pronto" && x.chave === chave; // releitura (volta do segundo plano, "atualizar"): o número bom fica, como no provider
    setE((x) => mantem(x) ? x : { fase: "carregando" });
    void (async () => {
      try {
        const d = await l.ler(op.controlador), eu = await lerRecurso("/auth/me", meSchema, op.controlador);
        if (!op.atual()) return;
        setE(eu.user_id !== p.usuario.user_id || l.outroPeriodo?.(d) ? { fase: "nada" } : { fase: "pronto", d, chave });
      } catch (x) {
        if (!op.atual() || x instanceof RequisicaoSuperada) return; // troca de conta ou saída da aba: o card não é mais desta tela
        p.falhou(x);
        const semAcesso = x instanceof ErroDeApi && (x.status === 401 || x.status === 403); // sessão ou plano: o texto já disse o que importa, e o número não pode ficar
        setE((y) => semAcesso ? { fase: "nada" } : mantem(y) ? y : { fase: "erro" });
      }
    })();
    return () => op.controlador.abort();
  }, [p.usuario.user_id, p.versao, card, tentativa]); // `l` é derivado de `card`
  if (e.fase === "nada") return null;
  return <AvisoDeBancoNoCard.Provider value><Bloco id={card.tipo} titulo={l.titulo}>
    {e.fase === "carregando" ? <ActivityIndicator accessibilityLabel="Carregando dados" />
      : e.fase === "erro" ? <View style={{ gap: 8 }}><Texto variante="rotulo" tom="inkMuted">Não deu para carregar os dados agora.</Texto><Acao rotulo="Tentar de novo" onPress={() => setTentativa((t) => t + 1)} /></View>
      : l.corpo(e.d)}
  </Bloco></AvisoDeBancoNoCard.Provider>;
}
