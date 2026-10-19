import { useEffect, useLayoutEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ErroApi, NOME_DO_PLANO, usePlan } from "../lib/v2";
import { route } from "../router";
import { abrirPortal, cobrancaQuery, comPrazoLeitura, desfazerTroca, falhaCobranca, type Falha } from "./api";

const SELO = { monthly: "Mensal", annual: "Anual" };
const ERRO_PORTAL = "Não foi possível abrir o gerenciamento da assinatura agora. Tente novamente em instantes.";
const ERRO_DESFAZER = "Não foi possível desfazer a troca agora. Tente novamente em instantes.";

// No MÓDULO, não no componente: sair da rota e voltar remonta a página, e uma trava por instância liberaria um 2º POST com o 1º em voo.
// A trava e a falha ficam juntas: a instância nova nasce com o botão travado e mostra a falha de um POST que a velha começou.
// Uma só para as duas escritas (portal e desfazer). A trava é lida na hora (`escrita.voando`): dois cliques no mesmo tick não passam pelo estado.
// `voando`: "escrita" = um POST em voo (só o erro ou o fim dele solta); "navegando" = o portal respondeu e a página está indo ao Stripe.
const escrita = { voando: null as "escrita" | "navegando" | null, falha: null as Falha | null };
const ouvintes = new Set<() => void>();
const muda = (p: Partial<typeof escrita>) => { Object.assign(escrita, p); ouvintes.forEach((o) => o()); };
const assina = (f: () => void) => { ouvintes.add(f); return () => { ouvintes.delete(f); }; };

// Só a trava "navegando" solta aqui, nunca a de um POST em voo (um desfazer não pode ganhar um 2º POST). Dois jeitos de soltar:
// voltar do Stripe pelo bfcache (`pageshow`), e o prazo de segurança: navegação interrompida (Parar, `window.stop()`) não dispara nem erro nem
// pageshow, e deixaria o botão travado até recarregar. Um 2º POST depois do prazo só cria outra sessão do portal, inofensivo.
// No módulo, não no componente: com a Cobrança desmontada a trava continua presa, e a volta do bfcache tem de soltá-la mesmo assim.
const PRAZO_NAVEGACAO = 10000;
let seguranca: ReturnType<typeof setTimeout>;
const solta = () => { clearTimeout(seguranca); if (escrita.voando === "navegando") muda({ voando: null }); };
window.addEventListener("pageshow", (e) => { if (e.persisted) solta(); });

// Cobrança da Conta (/conta/cobranca): o plano, até quando vale o período atual e as ações que o servidor já tem (portal do Stripe,
// desfazer a troca agendada, ver planos). A troca de plano segue na /precos. Dinheiro não aparece: a rota não traz preço.
export function ContaCobranca() {
  const qc = useQueryClient();
  const chave = cobrancaQuery.queryKey;
  const q = useQuery(cobrancaQuery);
  const tier = usePlan();
  const ocupado = useSyncExternalStore(assina, () => escrita.voando !== null); // aria-disabled
  const falha = useSyncExternalStore(assina, () => escrita.falha);
  const [aviso, setAviso] = useState("");

  useLayoutEffect(() => { muda({ falha: null }); }, []); // falha de uma visita anterior não volta (a de um POST em voo ainda não existe: nasce quando ele falha)

  // Voltar do Stripe pelo histórico pode trazer a página do bfcache, com o plano de antes de o cliente mexer lá: relê.
  // (A trava do portal solta no `pageshow` do módulo, acima, com a página montada ou não.)
  useEffect(() => {
    const volta = (e: PageTransitionEvent) => { if (e.persisted) q.refetch(); };
    window.addEventListener("pageshow", volta);
    return () => window.removeEventListener("pageshow", volta);
  }, [q.refetch]);

  const erro = q.error instanceof ErroApi ? q.error : null;
  const sessao = erro?.status === 401; // só escolhe o texto do erro de carga (o dado em cache já some em `d`)
  const d = q.isError ? undefined : q.data; // erro de carga nunca mostra o dado velho

  // Uma escrita por vez. Falha: mostra o texto e relê (a escrita pode ter ido); o botão só volta depois, mas a releitura tem prazo.
  // `ok` devolver true = a página está indo embora (portal): a trava NÃO solta no sucesso, senão um 2º clique, com a navegação ainda em curso,
  // faria um 2º POST. Só o erro, a volta do bfcache ou o prazo de segurança (acima) a soltam.
  const executar = async (f: () => Promise<unknown>, reserva: string, ok: () => boolean | void, jaFeito?: (e: unknown) => boolean) => {
    if (escrita.voando) return;
    muda({ voando: "escrita", falha: null });
    setAviso("");
    let fica = false;
    try { await f(); fica = ok() === true; }
    catch (e) {
      if (!jaFeito?.(e)) muda({ falha: falhaCobranca(e, reserva) });
      await comPrazoLeitura(qc.invalidateQueries({ queryKey: chave }));
    } finally { if (!fica) muda({ voando: null }); }
  };
  const gerenciar = () => executar(async () => location.assign(await abrirPortal()), ERRO_PORTAL, () => {
    muda({ voando: "navegando" });
    seguranca = setTimeout(solta, PRAZO_NAVEGACAO);
    return true;
  });
  const desfazer = () => executar(desfazerTroca, ERRO_DESFAZER, () => {
    qc.setQueryData(chave, (c: typeof d) => c && { ...c, troca: null });
    setAviso("Troca desfeita. Seu plano continua como está.");
    qc.invalidateQueries({ queryKey: chave });
  }, (e) => e instanceof ErroApi && (e.status === 400 || e.status === 409)); // 400 no_change / 409 no_subscription: a troca já não existe

  const linha = (icone: string, rotulo: string, valor: ReactNode, acao?: ReactNode) => (
    <div>
      <dt><i className={`ph ${icone}`} aria-hidden="true" /><span className="sr-only">{rotulo}</span></dt>
      <dd>{valor}</dd>
      {acao && <dd>{acao}</dd>}
    </div>
  );
  const planos = <a className="btn btn-ghost" href="/precos">Ver planos</a>;

  let corpo: ReactNode;
  if (q.isPending) corpo = <p role="status" className="faint">Carregando…</p>;
  else if (!d) corpo = (
    <div className="empty" role="alert">
      <p>{sessao ? "Sua sessão terminou." : "Não foi possível carregar a cobrança."}</p>
      {sessao ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
        : <button type="button" className="btn btn-ghost retry" onClick={() => { muda({ falha: null }); q.refetch(); }}>Tentar de novo</button>}
    </div>
  );
  else {
    // Na carência o `plan_tier` do /me é free e a assinatura segue viva: o nome sai da assinatura, o tier só quando ela não diz.
    const atual = (d.ativa && d.plano) || tier;
    const nome = NOME_DO_PLANO[atual];
    // Troca para o MESMO plano é troca de intervalo ("Plus Mensal" → "Plus Anual"): sem o selo a linha pareceria um não-evento.
    const destino = d.troca && NOME_DO_PLANO[d.troca.plano] + (d.troca.plano === atual && d.troca.intervalo ? ` ${SELO[d.troca.intervalo]}` : "");
    const selos = d.vitalicio ? ["Vitalício"] : [d.intervalo && SELO[d.intervalo], d.pix && "Pix"].filter((x): x is string => !!x);
    const plano = nome || selos.length ? <>{nome}{selos.map((x) => <span key={x}> <span className="selo">{x}</span></span>)}</> : (!d.ativa && "Sem plano");
    const stripe = d.ativa && !d.vitalicio && !d.pix;
    corpo = <>
      <dl className="detail-facts col">
        {plano && linha("ph-credit-card", "Plano", plano)}
        {d.ate && !d.vitalicio && linha("ph-calendar-blank", "Data", `${d.pix ? "Válido até" : "Período atual até"} ${d.ate}`)}
        {d.troca && linha("ph-arrows-clockwise", "Troca agendada", `Muda para ${destino} em ${d.troca.em}`,
          <button type="button" className="btn btn-ghost" aria-disabled={ocupado || undefined} onClick={desfazer}
            aria-label={`Desfazer a troca para ${destino}`}>Desfazer</button>)}
      </dl>
      {!d.vitalicio && <div className="cobranca-acoes">
        {stripe && <button type="button" className="btn btn-primary" aria-disabled={ocupado || undefined} onClick={gerenciar}>Gerenciar assinatura<i className="ph ph-arrow-square-out" aria-hidden="true" /></button>}
        {planos}
      </div>}
      {falha && <div role="alert" className="sess-falha"><span className="warn">{falha.texto}</span>
        {falha.acao === "recarregar" && <button type="button" className="btn btn-ghost" onClick={() => location.reload()}>Recarregar</button>}</div>}
    </>;
  }

  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>{route("/conta/cobranca").title}</h1>
      </header>
      <div className="page-grid">
        <section className="panel span-12 conta-perfil conta-cobranca" aria-label="Sua assinatura">
          {corpo}
          <p role="status" className="sr-only">{aviso}</p>
        </section>
      </div>
    </>
  );
}
