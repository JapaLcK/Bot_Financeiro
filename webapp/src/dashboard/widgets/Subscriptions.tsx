// Assinaturas: o card do Resumo e a página /assinaturas, sobre a /api/v2/assinaturas.
// O 403 `pro_required` do servidor (Essencial) vira o convite: o gate é do backend.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Assinatura, Assinaturas, MarcaIn } from "../lib/api-v2.gen";
import { dayMonth, isoDay, money, money0, monthYear } from "../lib/format.js";
import { ErroApi, apiPost, assinaturasQuery } from "../lib/v2";
import { Frame } from "../parts/Frame";

// Estados comuns ao card e à página, com texto fixo: a `message` do servidor não vai à tela.
// Tudo ignorado devolve também o `data`: a página ainda mostra a seção das ignoradas.
function useAssinaturas(): { data?: Assinaturas; estado: ReactNode } {
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
  // Servidor anterior ao #751 (deploy fora de ordem) não manda `ignoradas`.
  const data = q.data.ignoradas ? q.data : { ...q.data, ignoradas: [] };
  if (!data.servicos.length && !data.outras.length) {
    if (data.ignoradas.length) {
      return {
        data,
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
  return { data, estado: null };
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
  const { data, estado } = useAssinaturas();
  const qc = useQueryClient();
  const [aviso, setAviso] = useState<{ texto: string; desfazer?: MarcaIn } | null>(null);
  const avisoRef = useRef<HTMLParagraphElement>(null);
  const desfazerRef = useRef<HTMLButtonElement>(null);
  // O Desfazer de um Voltar a mostrar devolve o item às ignoradas: a seção (que pode ter
  // sumido e voltar fechada) abre uma vez, na próxima montagem; o usuário fecha depois.
  const reabrir = useRef(false);
  const feito = (a: Acao) => setAviso({ texto: a.feito, desfazer: a.desfazer });
  // Sem atualização otimista: a lista na tela é sempre a última resposta do servidor.
  const m = useMutation({
    mutationKey: ["assinaturas", "marca"],
    mutationFn: (a: Acao) => apiPost("/assinaturas/marca", a.corpo),
    onMutate: () => qc.cancelQueries({ queryKey: assinaturasQuery.queryKey }),
    onSuccess: (d, a) => {
      reabrir.current = !!a.desfazendo && a.corpo.status === "ignorar";
      qc.setQueryData(assinaturasQuery.queryKey, d);
      feito(a);
    },
    // O GET de recarga também planta de novo o cookie de CSRF, se ele venceu.
    // O Desfazer (o que falhou, ou o da ação anterior) continua disponível: é o caminho
    // mais curto de volta, além da seção das ignoradas.
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
      const pegou = status === "ignorar" ? d?.ignoradas?.some((x) => x.chave === chave) : status === "assinatura" ? d?.servicos.some((x) => x.chave === chave && x.marcada) : item && !item.marcada;
      if (d && pegou) feito(a);
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
  // O Desfazer volta ao estado anterior: o de quem sai da seção das ignoradas é ignorar de novo.
  const marcar = (a: Assinatura, status: MarcaIn["status"]) => {
    if (ocupado() || !data) return;
    const ignorada = data.ignoradas.some((x) => x.chave === a.chave);
    const k = [...data.servicos, ...data.outras, ...data.ignoradas].filter((x) => x.chave === a.chave).length - 1;
    const quem = k ? `${a.nome} e mais ${k} ${k === 1 ? "cobrança" : "cobranças"} do mesmo comerciante` : a.nome;
    m.mutate({
      corpo: { chave: a.chave, status },
      // "nenhuma" não diz para onde foi: o item pode seguir em serviços pela categoria.
      feito: ignorada ? `${quem} ${k ? "voltaram" : "voltou"} para a lista.`
        : status === "nenhuma" ? `Marca removida de ${quem}.` : `${quem} ${FEITO[status][k ? 1 : 0]}.`,
      desfazer: { chave: a.chave, status: ignorada ? "ignorar" : a.marcada ? "assinatura" : "nenhuma" },
    });
  };
  const botao = (a: Assinatura, status: MarcaIn["status"], rotulo: string, cls = "btn-quiet") => (
    <button type="button" className={`btn ${cls}`} disabled={pendente} onClick={() => marcar(a, status)}
      aria-label={status === "ignorar" ? `${rotulo} ${a.nome}` : `${rotulo}: ${a.nome}`}>{rotulo}</button>
  );
  // Por último e com `key` fixo: a seção está nos dois retornos abaixo, em posições diferentes,
  // e o `key` a mantém montada (e o `<details>` aberto) quando a página passa de um ao outro.
  // Em `ignoradas`, `marcada` é a marca guardada, que o Voltar a mostrar restaura.
  const n = data?.ignoradas.length ?? 0;
  const ignoradas = n > 0 && (
    <div key="ignoradas" className="panel span-12">
      <Frame id="assinaturas-ignoradas" title="Ignoradas">
        <details className="sub-ignoradas" ref={(d) => { if (d && reabrir.current) { d.open = true; reabrir.current = false; } }}>
          <summary><span className="sub-mostrar">Mostrar {n} {n === 1 ? "cobrança" : "cobranças"}</span><span className="sub-esconder">Esconder</span></summary>
          <ul className="subs">
            {data!.ignoradas.map((a, i) => (
              <Linha key={`${a.chave}#${i}`} a={a}>
                {botao(a, a.marcada ? "assinatura" : "nenhuma", "Voltar a mostrar", "btn-ghost")}
              </Linha>
            ))}
          </ul>
        </details>
      </Frame>
    </div>
  );

  if (estado) return <><div className="panel span-12"><Frame id="assinaturas" title="Assinaturas">{avisoBloco}{estado}</Frame></div>{ignoradas}</>;
  const { servicos, outras, total_mensal, total_anual } = data!;

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
      {ignoradas}
    </>
  );
}
