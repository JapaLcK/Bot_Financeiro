// Assinaturas: o card do Resumo e a página /assinaturas, sobre a /api/v2/assinaturas.
// O 403 `pro_required` do servidor (Essencial) vira o convite: o gate é do backend.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Assinatura, Assinaturas, MarcaIn } from "../lib/api-v2.gen";
import { dayMonth, isoDay, money, money0, monthYear } from "../lib/format.js";
import { ErroApi, apiPost, assinaturasQuery } from "../lib/v2";
import { Frame } from "../parts/Frame";

// Estados comuns ao card e à página, com texto fixo: a `message` do servidor não vai à tela.
// `ignorouTudo`: a lista esvaziou por um Ignorar nesta tela. A API não diz se o vazio veio
// de marcas, então recarregar (ou sair da página) volta ao texto genérico.
function useAssinaturas(ignorouTudo = false): { data?: Assinaturas; estado: ReactNode } {
  const q = useQuery(assinaturasQuery);
  if (q.isPending) return { estado: <p role="status" className="faint">Carregando…</p> };
  if (q.isError && q.error instanceof ErroApi && q.error.code === "pro_required") {
    return {
      estado: (
        <div className="empty">
          <i className="ph ph-lock" aria-hidden="true" />
          <p>Assinaturas é do Plus e do Pro.</p>
          <a className="btn btn-ghost" href="/precos">Ver planos</a>
        </div>
      ),
    };
  }
  if (q.isError) {
    return {
      estado: (
        <div className="empty" role="alert">
          <p>Não deu para carregar as assinaturas.</p>
          <button type="button" className="btn btn-ghost" onClick={() => q.refetch()}>Tentar de novo</button>
        </div>
      ),
    };
  }
  if (!q.data.servicos.length && !q.data.outras.length) {
    if (ignorouTudo) {
      return {
        estado: (
          <div className="empty">
            <i className="ph ph-eye-slash" aria-hidden="true" />
            <p>Você ignorou todas as cobranças detectadas.</p>
          </div>
        ),
      };
    }
    return {
      estado: (
        <div className="empty">
          <i className="ph ph-bank" aria-hidden="true" />
          <p>Nenhuma assinatura por aqui ainda. Elas aparecem sozinhas a partir das contas e cartões conectados pelo Open Finance.</p>
          <a className="btn btn-ghost" href="/settings?view=open-finance">Conectar banco</a>
        </div>
      ),
    };
  }
  return { data: q.data, estado: null };
}

export function Subscriptions() {
  const { data, estado } = useAssinaturas();
  const ativas = data?.servicos.filter((a) => a.status === "ativa").slice(0, 3) ?? [];
  const n = data && !data.servicos.length ? data.outras.length : 0;
  return (
    <Frame id="assinaturas" title="Assinaturas">
      {estado ?? (
        <>
          <p className="w-lede"><span className="num">{money(Number(data!.total_mensal))}</span> por mês · <span className="num">{money0(Number(data!.total_anual))}</span> por ano</p>
          {ativas.length > 0 && (
            <div className="bills">
              {ativas.map((a, i) => (
                <div key={`${a.chave}#${i}`} className="bill sub-bill">
                  <span className="bill-name">{a.nome}<span className="bill-status faint">todo dia {a.dia}</span></span>
                  <span className="bill-amt num">{money(Number(a.valor))}</span>
                </div>
              ))}
            </div>
          )}
          {n > 0 && <p className="w-lede">{n === 1 ? "1 cobrança recorrente para revisar" : `${n} cobranças recorrentes para revisar`}</p>}
        </>
      )}
    </Frame>
  );
}

type Acao = { corpo: MarcaIn; feito: string; desfazer?: MarcaIn; desfazendo?: true };
// [uma cobrança, o grupo]: a marca é por comerciante (`chave`) e vale para todas as linhas dele.
const FEITO = { assinatura: ["marcada como assinatura", "marcadas como assinatura"], ignorar: ["ignorada", "ignoradas"] };

function Linha({ a, children }: { a: Assinatura; children: ReactNode }) {
  const ativa = a.status === "ativa";
  return (
    <li className="sub">
      <b className="sub-name">{a.nome}</b>
      <span className="sub-amt num">{money(Number(a.valor))}</span>
      <p className="sub-meta">
        todo dia {a.dia} · {ativa
          ? `próxima ${dayMonth(isoDay(a.proxima))}`
          : <span className="faint">parece cancelada · última em {dayMonth(isoDay(a.ultima))}</span>}
      </p>
      <p className="sub-meta">
        <i className={`ph ${a.meio.tipo === "cartao" ? "ph-credit-card" : "ph-bank"}`} aria-hidden="true" /> {a.meio.nome}{a.meio.final && ` ••${a.meio.final}`}
        {" · "}desde {monthYear(isoDay(a.desde))}
      </p>
      {a.valor_anterior != null && a.reajuste_em && (
        <p className="sub-meta">{Number(a.valor) > Number(a.valor_anterior) ? "subiu de" : "baixou de"} <span className="num">{money(Number(a.valor_anterior))}</span> em {dayMonth(isoDay(a.reajuste_em))}</p>
      )}
      <div className="sub-acoes">{children}</div>
    </li>
  );
}

export function SubscriptionList() {
  const [ignorouTudo, setIgnorouTudo] = useState(false);
  const { data, estado } = useAssinaturas(ignorouTudo);
  const qc = useQueryClient();
  const [aviso, setAviso] = useState<{ texto: string; desfazer?: MarcaIn } | null>(null);
  const avisoRef = useRef<HTMLParagraphElement>(null);
  const desfazerRef = useRef<HTMLButtonElement>(null);
  const feito = (d: Assinaturas, a: Acao) => {
    setAviso({ texto: a.feito, desfazer: a.desfazer });
    setIgnorouTudo(a.corpo.status === "ignorar" && !d.servicos.length && !d.outras.length);
  };
  // Sem atualização otimista: a lista na tela é sempre a última resposta do servidor.
  const m = useMutation({
    mutationKey: ["assinaturas", "marca"],
    mutationFn: (a: Acao) => apiPost("/assinaturas/marca", a.corpo),
    onMutate: () => qc.cancelQueries({ queryKey: assinaturasQuery.queryKey }),
    onSuccess: (d, a) => {
      qc.setQueryData(assinaturasQuery.queryKey, d);
      feito(d, a);
    },
    // O GET de recarga também planta de novo o cookie de CSRF, se ele venceu.
    // O Desfazer (o que falhou, ou o da ação anterior) continua disponível: o item ignorado
    // não volta por outro caminho.
    onError: async (_, a) => {
      if (a.desfazendo) {
        setAviso({ texto: "Não deu para desfazer. Tente de novo.", desfazer: a.corpo });
        qc.invalidateQueries({ queryKey: assinaturasQuery.queryKey });
        return;
      }
      // A resposta pode ter se perdido com a marca já gravada: a recarga diz se ela pegou.
      // Recarga que falha deixa a lista de antes do POST, onde a ação não aparece aplicada.
      await qc.invalidateQueries({ queryKey: assinaturasQuery.queryKey });
      const d = qc.getQueryData<Assinaturas>(assinaturasQuery.queryKey);
      const { chave, status } = a.corpo;
      const item = d && [...d.servicos, ...d.outras].find((x) => x.chave === chave);
      const pegou = status === "ignorar" ? !item : status === "assinatura" ? d?.servicos.some((x) => x.chave === chave && x.marcada) : item && !item.marcada;
      if (d && pegou) feito(d, a);
      else setAviso((p) => ({ texto: "Não deu para salvar. Tente de novo.", desfazer: p?.desfazer }));
    },
  });
  // `m.isPending` só muda no re-render (o TanStack notifica num setTimeout): dois cliques no
  // mesmo tique mandariam dois POST. O cache de mutações já sabe na hora.
  const ocupado = () => qc.isMutating({ mutationKey: ["assinaturas", "marca"] }) > 0;
  // E o `disabled` lê a mesma fonte: o POST que segue no ar depois de sair e voltar à página
  // não é deste `m`, e o `m.isPending` deixaria os botões vivos e mudos.
  const pendente = useIsMutating({ mutationKey: ["assinaturas", "marca"] }) > 0;
  useEffect(() => { if (aviso) (desfazerRef.current ?? avisoRef.current)?.focus(); }, [aviso]);

  // Fica também no estado vazio: ignorar a última assinatura não pode levar o Desfazer junto.
  const avisoBloco = (
    <div className="sub-aviso" aria-live="polite">
      {aviso && <p ref={avisoRef} tabIndex={-1}>{aviso.texto}</p>}
      {aviso?.desfazer && (
        <button ref={desfazerRef} type="button" className="btn btn-ghost" disabled={pendente}
          onClick={() => { if (!ocupado()) m.mutate({ corpo: aviso.desfazer!, feito: "Desfeito.", desfazendo: true }); }}>Desfazer</button>
      )}
    </div>
  );
  if (estado) return <div className="panel span-12"><Frame id="assinaturas" title="Assinaturas">{avisoBloco}{estado}</Frame></div>;
  const { servicos, outras, total_mensal, total_anual } = data!;
  // O item ignorado some da API: só dá para desfazer agora, voltando ao estado anterior.
  const marcar = (a: Assinatura, status: MarcaIn["status"]) => {
    if (ocupado()) return;
    const k = [...servicos, ...outras].filter((x) => x.chave === a.chave).length - 1;
    const quem = k ? `${a.nome} e mais ${k} ${k === 1 ? "cobrança" : "cobranças"} do mesmo comerciante` : a.nome;
    m.mutate({
      corpo: { chave: a.chave, status },
      // "nenhuma" não diz para onde foi: o item pode seguir em serviços pela categoria.
      feito: status === "nenhuma" ? `Marca removida de ${quem}.` : `${quem} ${FEITO[status][k ? 1 : 0]}.`,
      desfazer: { chave: a.chave, status: a.marcada ? "assinatura" : "nenhuma" },
    });
  };
  const botao = (a: Assinatura, status: MarcaIn["status"], rotulo: string, cls = "btn-quiet") => (
    <button type="button" className={`btn ${cls}`} disabled={pendente} onClick={() => marcar(a, status)}
      aria-label={status === "ignorar" ? `${rotulo} ${a.nome}` : `${rotulo}: ${a.nome}`}>{rotulo}</button>
  );

  return (
    <>
      <div className="panel span-12">
        <Frame id="assinaturas-servicos" title="Serviços">
          <dl className="detail-facts">
            <div><dt>Por mês</dt><dd className="num">{money(Number(total_mensal))}</dd></div>
            <div><dt>Por ano</dt><dd className="num">{money0(Number(total_anual))}</dd></div>
          </dl>
          <p className="w-lede">Só os serviços ativos entram no total.</p>
          {avisoBloco}
          {servicos.length > 0 && (
            <ul className="subs">
              {servicos.map((a, i) => (
                <Linha key={`${a.chave}#${i}`} a={a}>
                  {botao(a, "ignorar", "Ignorar")}
                  {a.marcada && botao(a, "nenhuma", "Não é assinatura")}
                </Linha>
              ))}
            </ul>
          )}
        </Frame>
      </div>
      {outras.length > 0 && (
        <div className="panel span-12">
          <Frame id="assinaturas-outras" title="Outras cobranças recorrentes">
            <ul className="subs">
              {outras.map((a, i) => (
                <Linha key={`${a.chave}#${i}`} a={a}>
                  {botao(a, "assinatura", "É assinatura", "btn-ghost")}
                  {botao(a, "ignorar", "Ignorar")}
                </Linha>
              ))}
            </ul>
          </Frame>
        </div>
      )}
    </>
  );
}
