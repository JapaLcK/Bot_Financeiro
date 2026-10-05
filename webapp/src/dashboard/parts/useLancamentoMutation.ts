import { useState } from "react";
import { useIsMutating, useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import type { Edicao, Lancamento, Lancamentos, NovoLancamento } from "../lib/api-v2.gen";
import { apiPost, ErroApi, lancamentosQuery } from "../lib/v2";

export type Escrita = { acao: "criar"; corpo: NovoLancamento } | { acao: "editar"; corpo: Edicao } | { acao: "apagar"; corpo: { id: string } };
const CHAVE = ["lancamentos", "escrita"];
type Conferencia = { escrita: Escrita; item?: Lancamento; meses: string[]; vistos: string[]; alvo?: string; fase: "pendente" | "incerta" | "carregando" | "pronta" };
// ponytail: contexto desta aba; idempotência entre abas/reloads exige contrato do servidor.
let conferencia: Conferencia | undefined;

export function useLancamentoMutation() {
  const qc = useQueryClient();
  const [aviso, setAviso] = useState("");
  const [, render] = useState(0);
  const [rascunho, setRascunho] = useState<NovoLancamento>();
  const recarregar = async (primeiraPagina = true) => {
    await qc.cancelQueries({ queryKey: ["lancamentos"] });
    if (primeiraPagina) qc.setQueriesData<InfiniteData<Lancamentos, string | null>>({ queryKey: ["lancamentos"] }, (d) => d && ({ ...d, pages: d.pages.slice(0, 1), pageParams: d.pageParams.slice(0, 1) }));
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["lancamentos"] }, { throwOnError: true }),
      ...["categorias", "contas", "resumo-do-mes"].map((k) => qc.invalidateQueries({ queryKey: [k] })),
    ]);
  };
  const m = useMutation({
    mutationKey: CHAVE, retry: false, networkMode: "always",
    mutationFn: (e: Escrita) => e.acao === "criar" ? apiPost("/lancamentos/carteira", e.corpo)
      : e.acao === "editar" ? apiPost("/lancamentos/editar", e.corpo) : apiPost("/lancamentos/apagar", e.corpo),
    onMutate: () => qc.cancelQueries({ queryKey: ["lancamentos"] }),
    onSuccess: (_, e) => { conferencia = undefined; setRascunho(undefined); setAviso(e.acao === "apagar" ? "Lançamento apagado." : "Lançamento salvo."); },
    onError: (e) => {
      if (!(e instanceof ErroApi) || e.status === 0 || e.status >= 500 || (e.status >= 200 && e.status < 300)) {
        if (conferencia) conferencia.fase = "incerta";
        setAviso("A resposta se perdeu. A gravação pode ter sido concluída. Atualize e confira a lista antes de tentar novamente.");
      } else conferencia = undefined;
    },
    onSettled: async (_, erro) => { try { await recarregar(!erro); } catch { setAviso((a) => `${a} Não foi possível atualizar a lista.`); } },
  });
  const pendente = useIsMutating({ mutationKey: CHAVE }) > 0;
  const salvar = async (e: Escrita, item?: Lancamento, mes?: string, historico = false) => {
    if (conferencia || qc.isMutating({ mutationKey: CHAVE })) return false;
    const origem = item?.fatura ?? (historico ? item?.data.slice(0, 7) : mes);
    const meses = e.acao === "criar" ? (e.corpo.data ? [e.corpo.data.slice(0, 7)] : [])
      : [...new Set([origem, e.acao === "editar" && e.corpo.data && !item?.fatura ? e.corpo.data.slice(0, 7) : undefined].filter((v): v is string => !!v))];
    conferencia = { escrita: e, item, meses, vistos: [], fase: "pendente" };
    await m.mutateAsync(e);
    return true;
  };
  const atualizar = async (mes?: string): Promise<string | boolean> => {
    if (qc.isMutating({ mutationKey: CHAVE }) || conferencia?.fase === "carregando") return false;
    const c = conferencia;
    try {
      if (!c) { await recarregar(); setAviso("Lista atualizada. Confira os lançamentos antes de salvar novamente."); return true; }
      c.fase = "carregando"; render((v) => v + 1);
      const fresco = async (alvo?: string) => {
        const op = lancamentosQuery(alvo ? { mes: alvo } : {});
        await qc.cancelQueries({ queryKey: op.queryKey, exact: true });
        qc.setQueryData<InfiniteData<Lancamentos, string | null>>(op.queryKey, (d) => d && ({ ...d, pages: d.pages.slice(0, 1), pageParams: d.pageParams.slice(0, 1) }));
        await qc.invalidateQueries({ queryKey: op.queryKey, exact: true, refetchType: "none" });
        return qc.fetchInfiniteQuery(op);
      };
      if (!c.meses.length) c.meses = [(await fresco()).pages[0].mes];
      const alvo = mes && c.meses.includes(mes) ? mes : c.meses[0];
      await fresco(alvo);
      c.alvo = alvo; c.vistos = [...new Set([...c.vistos, alvo])]; c.fase = "pronta";
      setAviso("Lista atualizada. Confira as páginas necessárias antes de autorizar outra gravação.");
      return alvo;
    } catch {
      if (c) c.fase = "incerta";
      setAviso("Não foi possível atualizar a lista. Confira antes de salvar novamente."); return false;
    } finally { render((v) => v + 1); }
  };
  const confirmar = (visaoConferivel: boolean) => {
    if (!visaoConferivel || !conferencia || conferencia.fase !== "pronta" || conferencia.meses.some((mes) => !conferencia!.vistos.includes(mes))) return;
    if (conferencia.escrita.acao === "criar") setRascunho(conferencia.escrita.corpo);
    conferencia = undefined; setAviso("Conferência concluída. Uma nova gravação depende de sua ação."); render((v) => v + 1);
  };
  return { salvar, atualizar, confirmar, pendente, bloqueado: !!conferencia && conferencia.fase !== "pendente", conferencia,
    rascunho: conferencia?.escrita.acao === "criar" ? conferencia.escrita.corpo : rascunho,
    descartarRascunho: () => setRascunho(undefined), aviso };
}
