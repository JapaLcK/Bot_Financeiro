import { useState } from "react";
import { useIsMutating, useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import type { Edicao, Lancamentos, NovoLancamento } from "../lib/api-v2.gen";
import { apiPost, ErroApi } from "../lib/v2";

export type Escrita = { acao: "criar"; corpo: NovoLancamento } | { acao: "editar"; corpo: Edicao } | { acao: "apagar"; corpo: { id: string } };
const CHAVE = ["lancamentos", "escrita"];
// Sobrevive à saída da tela: resposta perdida exige conferir antes de outra escrita.
let conferir = false;

export function useLancamentoMutation() {
  const qc = useQueryClient();
  const [aviso, setAviso] = useState("");
  const [bloqueado, setBloqueado] = useState(conferir);
  const recarregar = async (primeiraPagina = true) => {
    await qc.cancelQueries({ queryKey: ["lancamentos"] });
    if (primeiraPagina) qc.setQueriesData<InfiniteData<Lancamentos, string | null>>({ queryKey: ["lancamentos"] }, (d) => d && ({ ...d, pages: d.pages.slice(0, 1), pageParams: d.pageParams.slice(0, 1) }));
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["lancamentos"] }, { throwOnError: true }),
      ...["categorias", "contas", "resumo-do-mes"].map((k) => qc.invalidateQueries({ queryKey: [k] })),
    ]);
  };
  const m = useMutation({
    mutationKey: CHAVE,
    retry: false,
    networkMode: "always",
    mutationFn: (e: Escrita) => e.acao === "criar" ? apiPost("/lancamentos/carteira", e.corpo)
      : e.acao === "editar" ? apiPost("/lancamentos/editar", e.corpo) : apiPost("/lancamentos/apagar", e.corpo),
    onMutate: () => qc.cancelQueries({ queryKey: ["lancamentos"] }),
    onSuccess: (_, e) => { setAviso(e.acao === "apagar" ? "Lançamento apagado." : "Lançamento salvo."); },
    onError: (e) => {
      if (!(e instanceof ErroApi) || e.status === 0 || e.status >= 500 || (e.status >= 200 && e.status < 300)) {
        conferir = true;
        setBloqueado(true);
        setAviso("A resposta se perdeu. A gravação pode ter sido concluída. Atualize e confira a lista antes de tentar novamente.");
      }
    },
    onSettled: async (_, erro) => { try { await recarregar(!erro); } catch { setAviso((a) => `${a} Não foi possível atualizar a lista.`); } },
  });
  const pendente = useIsMutating({ mutationKey: CHAVE }) > 0;
  const salvar = async (e: Escrita) => {
    if (conferir || qc.isMutating({ mutationKey: CHAVE })) return false;
    await m.mutateAsync(e);
    return true;
  };
  const atualizar = async () => {
    if (qc.isMutating({ mutationKey: CHAVE })) return false;
    try {
      await recarregar();
      conferir = false;
      setBloqueado(false);
      setAviso("Lista atualizada. Confira os lançamentos antes de salvar novamente.");
      return true;
    } catch { setAviso("Não foi possível atualizar a lista. Confira antes de salvar novamente."); return false; }
  };
  return { salvar, atualizar, pendente, bloqueado: bloqueado || conferir, aviso };
}
