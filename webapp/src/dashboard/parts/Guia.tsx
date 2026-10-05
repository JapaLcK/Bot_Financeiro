import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AcaoGuia, Passo } from "../lib/api-v2.gen";
import { ICON } from "../lib/brand";
import { useConversation } from "../lib/conversation";
import { mesDe } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { DEMO, ErroApi, apiPost, guiaQuery, perfilQuery } from "../lib/v2";
import { go, type Path } from "../router";
import { ROTA, aba, achar, caixa, cobrir, corte, guia, posicionar, trazer, type Caixa, type Tipo } from "./guia-posicao";
import { avisarGuia } from "./Dica";
import { MOTIVO, OF, destino } from "./guia-falas";
import { useTecladoDoVeu } from "./guia-teclado";
import { TEMPO, partir, pular, quadro, tiltDe, type Voo } from "./guia-voo";

// O guia do /painel (#728). O roteiro e o progresso vêm do servidor (GET/POST /api/v2/guia,
// `PASSOS` em api/v2/guia.py); aqui fica só se o guia está aberto. O passo avança quando a
// pessoa faz a ação de verdade, nunca por um "próximo". O guia escurece o resto: só o que se
// toca agora (e o balão) recebe toque e Tab. Toda etapa espera um toque (dono, 2026-10-03):
// fora da tela do passo, a aba dele, com o anel ("Agora toca em Gastos."; a pessoa navega, o
// guia não); na tela, "bloco" (o Piggy apresenta o bloco, aceso e sem toque, e espera o
// "Entendi") e "alvo" (ele voa até o que se toca, que ganha o anel). Sem toque, só o voo até a
// próxima coisa a tocar e a comemoração. A tabela de quem o Piggy mira em cada etapa está no `tick`.

// A aba de cada tela do roteiro (App.tsx põe `data-guia` no menu lateral e na barra de baixo).
export const navGuia = (p: Path) => {
  const tela = (Object.keys(ROTA) as Passo["tela"][]).find((t) => ROTA[t] === p);
  return tela && `nav.${tela}`;
};
// O item "Ajuda" e o Cmd-K abrem o guia por aqui.
export const abrirGuia = () => window.dispatchEvent(new Event("dash:guia"));

// Uma ação é a passagem de um retrato da tela para o seguinte; o retrato de quando o passo
// começou é o primeiro, então o que já estava escolhido antes não conta. `alvo`: o `data-guia`
// do que se toca, em ordem de preferência (parts/guia-posicao.ts, `achar`).
interface Retrato { mes: string; cat: string | null; perguntas: number; path: Path }
const ACOES: Record<string, { feito: (antes: Retrato, agora: Retrato) => boolean; alvo: string[] }> = {
  // A seta Mês anterior (a Próximo mês, se não há anterior).
  "mes.trocado": { feito: (a, b) => b.mes !== a.mes, alvo: ["mes.trocar"] },
  // Categoria escolhida no Resumo não conta: o passo é na página de Gastos. Alvo: a 1ª linha não ativa.
  "categoria.aberta": { feito: (a, b) => b.path === "/gastos" && b.cat != null && b.cat !== a.cat, alvo: ["categorias.item"] },
  // Um chip de pergunta pronta; com a conversa já começada (sem chips), o campo da conversa.
  "piggy.perguntou": { feito: (a, b) => b.perguntas > a.perguntas, alvo: ["piggy.chip", "piggy.pergunta"] },
};

type Modo = "fechado" | "convite" | "ativo";
type Etapa = "bloco" | "alvo";
const TECLAS = ["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown", " "];

export function Guia({ s, path }: { s: DashState; path: Path }) {
  const qc = useQueryClient();
  const g = useQuery(guiaQuery).data;
  const perfil = useQuery(perfilQuery).data?.perfil;
  const perguntas = useConversation().filter((m) => m.role === "user").length;
  const [modo, setModo] = useState<Modo>("fechado");
  const [rev, setRev] = useState(false); // aberto pela Ajuda com tudo feito: revisão, um passo por vez
  const [vistos, setVistos] = useState<string[]>([]); // feitos ou pulados nesta abertura
  const [festa, setFesta] = useState<string | null>(null);
  const [tipo, setTipo] = useState<Tipo>("espera");
  const [paraAba, setParaAba] = useState(""); // "em Gastos": a aba do passo, se é noutra tela (o tick lê)
  const [fechouDialog, reavaliar] = useState(0);
  const [fase, setFase] = useState<{ id: string; etapa: Etapa }>({ id: "", etapa: "bloco" }); // presa ao passo atual
  const ofereceu = useRef(false);
  const miraAnt = useRef<HTMLElement | null>(null); // onde o Piggy estava mirando: mudou, ele voa
  const voo = useRef<Voo | null>(null);
  const ultimo = useRef<Caixa | null>(null); // o último recorte aceso pintado: o próximo parte dele
  const focar = useRef(false);
  const rolou = useRef("");
  const mexeu = useRef(false); // a pessoa rolou por conta própria desde a rolagem inicial do passo
  const piggy = useRef<HTMLImageElement>(null);
  const balao = useRef<HTMLElement>(null);
  const veus = useRef<HTMLDivElement>(null);
  const sombra = useRef<SVGPathElement>(null);
  const anel = useRef<HTMLDivElement>(null);
  const furo = useRef<HTMLElement | null>(null); // o alvo que o véu deixa tocar agora
  const prende = useRef(false); // o foco que cai fora do balão e do alvo volta ao balão

  const m = useMutation({
    mutationKey: ["guia"],
    mutationFn: (c: AcaoGuia) => apiPost("/guia", c),
    // A comemoração só depois do 200: o progresso está salvo.
    onSuccess: (novo, c) => {
      qc.setQueryData(guiaQuery.queryKey, novo);
      if (c.acao === "feito" && c.passo) { setVistos((v) => [...v, c.passo!]); setFesta(c.passo); }
    },
    // 409: o passo deixou de valer (o banco sumiu do mês). Não é "tentar de novo": relê e
    // mostra a orientação do motivo.
    onError: (e, c) => {
      if (c.acao === "feito" && e instanceof ErroApi && e.code === "passo_indisponivel") qc.invalidateQueries({ queryKey: guiaQuery.queryKey });
    },
  });

  const passos = g?.passos ?? [];
  const atual = passos.find((p) => !vistos.includes(p.id) && (rev || !p.feito));
  const exibido = festa ? passos.find((p) => p.id === festa) : atual;
  // Acabaram os passos desta abertura; concluído só quando o servidor diz (um pulado não foi feito).
  const acabou = !!festa && !atual;
  const fim = acabou && g?.estado === "concluido";
  const n = atual ? passos.indexOf(atual) + 1 : 0;
  const v = m.variables;
  const salvando = m.isPending && v?.acao === "feito";
  const indisponivel = m.error instanceof ErroApi && m.error.code === "passo_indisponivel";
  const falhou = m.isError && v?.acao === "feito" && v.passo === atual?.id && !indisponivel;
  // A etapa do passo: a gravada, ou "bloco" (passo novo). `fora`: a tela não é a do passo (passo
  // novo noutra tela, ou a pessoa saiu pelo voltar do navegador): a etapa é a aba; tocada, a
  // etapa gravada volta.
  const etapa: Etapa | null = modo !== "ativo" || festa || !atual ? null : fase.id === atual.id ? fase.etapa : "bloco";
  const fora = !!etapa && path !== ROTA[atual!.tela];

  // O que guarda movimento ou posição de um Piggy e de um véu montados: o Piggy que monta de
  // novo (reabrir, sair do Organizar) nasce parado, sem a mola nem o recorte do anterior.
  // Zera ao desmontar o balão (fechar, Organizar: o efeito depois do `corpo`) e ao iniciar.
  const zerar = () => { voo.current = null; miraAnt.current = null; ultimo.current = null; furo.current = null; };
  const iniciar = (rever: boolean) => {
    setRev(rever); setVistos([]); setFesta(null); setModo("ativo"); setFase({ id: "", etapa: "bloco" }); zerar(); rolou.current = "";
    focar.current = true;
  };
  const fechar = (dispensa: boolean) => {
    if (dispensa) m.mutate({ acao: "dispensar" });
    prende.current = false; // antes do foco ir para a página, senão a guarda o devolve ao balão
    if (balao.current?.contains(document.activeElement)) document.getElementById("page-title")?.focus({ preventScroll: true });
    setModo("fechado"); setFesta(null);
  };
  // O botão some com o passo: o foco vai para o título do que vem (o próximo ou a tela final).
  const seguir = (id: string) => { setVistos((x) => [...x, id]); focar.current = true; };

  // Convite: só para quem nunca viu (`oferecer`), no Resumo, depois do perfil escolhido e sem
  // nenhum dialog aberto (perfil, Cmd-K). Uma vez por carga de página. Com dialog aberto,
  // espera ele fechar: o fallback do Safari 14 (parts/dialog.ts, hide) só tira o `open`, sem
  // evento `close`, então quem avisa é o atributo.
  useEffect(() => {
    if (ofereceu.current || modo !== "fechado" || DEMO || path !== "/" || g?.estado !== "oferecer" || perfil == null) return;
    if (document.querySelector("dialog[open]")) {
      const o = new MutationObserver(() => { if (!document.querySelector("dialog[open]")) reavaliar((x) => x + 1); });
      o.observe(document.body, { attributes: true, attributeFilter: ["open"], subtree: true });
      return () => o.disconnect();
    }
    ofereceu.current = true;
    setModo("convite");
    m.mutate({ acao: "visto" });
  }, [modo, path, g?.estado, perfil, fechouDialog]);

  // Ajuda e Cmd-K: reabre no servidor e depois abre no primeiro passo não feito (ou em revisão).
  // Quem abriu pela Ajuda não recebe o convite depois (nem o `visto` por cima do reabrir).
  useEffect(() => {
    const on = () => {
      ofereceu.current = true;
      m.mutate({ acao: "reabrir" }, {
        onSettled: (novo) => {
          const ps = (novo ?? qc.getQueryData<typeof novo>(guiaQuery.queryKey))?.passos;
          if (ps) iniciar(ps.every((p) => p.feito));
        },
      });
    };
    window.addEventListener("dash:guia", on);
    return () => window.removeEventListener("dash:guia", on);
  });

  // Esc fecha (e dispensa), menos quando é o Esc de um dialog aberto por cima. Na captura:
  // no Safari 14 (parts/dialog.ts) o Cmd-K e o perfil tiram o `open` no keydown deles, antes.
  useEffect(() => {
    if (modo === "fechado") return;
    const on = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector("dialog[open]")) fechar(!acabou); };
    window.addEventListener("keydown", on, true);
    return () => window.removeEventListener("keydown", on, true);
  });

  // Ação real: compara o retrato desta renderização com o anterior, só com o passo rodando.
  const agora: Retrato = { mes: mesDe(s), cat: s.filter.category, perguntas, path };
  const antes = useRef<{ id: string; r: Retrato }>({ id: "", r: agora });
  useEffect(() => {
    const rodando = etapa === "alvo" && atual && atual.disponivel && !salvando && !falhou && !s.editing;
    const a = antes.current;
    antes.current = { id: rodando ? atual.id : "", r: agora };
    if (rodando && a.id === atual.id && ACOES[atual.acao]?.feito(a.r, agora)) m.mutate({ acao: "feito", passo: atual.id });
  });

  // No desktop a aba do Piggy é a barra de conversa (D9), que não é link: tocar nela (ou Enter
  // no campo) só focaria o campo. Aí o toque da pessoa leva à conversa; nunca sem ele. A aba
  // se acha na hora do toque: a janela que cruza 760 px troca a barra pela aba de baixo.
  const tela = fora ? atual!.tela : null;
  useEffect(() => {
    if (!tela) return;
    const ir = (e: Event) => {
      const a = aba(atual!);
      if (!a || a.matches("a[href]") || !a.contains(e.target as Node)) return;
      if (!(e instanceof KeyboardEvent) || e.key === "Enter") go(ROTA[tela]);
    };
    document.addEventListener("click", ir);
    document.addEventListener("keydown", ir);
    return () => { document.removeEventListener("click", ir); document.removeEventListener("keydown", ir); };
  }, [tela]);

  // Comemoração: o passo seguinte entra depois dela; a última fica até fechar.
  useEffect(() => {
    if (!festa || acabou) return;
    const t = setTimeout(() => setFesta(null), TEMPO.festa);
    return () => clearTimeout(t);
  }, [festa, acabou]);
  useEffect(() => { if (festa && piggy.current) pular(piggy.current); }, [festa]);
  // Acabaram os passos sem comemoração (Seguir no último; refetch que trouxe o resto feito por
  // outra aba ou antes do 200): a mesma tela final, nunca fechar calado.
  useEffect(() => {
    if (modo !== "ativo" || !g || atual || festa) return;
    const id = vistos[vistos.length - 1] ?? passos[passos.length - 1]?.id;
    if (id) setFesta(id); else setModo("fechado");
  });

  // Posição: segue a mira a cada quadro (rolagem, grade arrastável, troca de página). A tabela
  // do plano (#728): quem o Piggy mira; o que fica aceso sem toque (claro), o que se toca
  // (toque) e onde vai o anel (marca), por etapa:
  //   convite, ausente, espera: canto (na espera o Piggy fica onde está), tudo escuro;
  //   festa: fica onde estava, tudo escuro;
  //   fora da tela do passo: a aba, tocável e com anel;
  //   motivo: a âncora, tudo escuro;
  //   bloco: a âncora, acesa sem toque e sem anel;
  //   alvo: o alvo, tocável e com anel.
  // Mira nova: o Piggy, o balão e o recorte aceso vão até ela na mesma mola; no voo, sem anel.
  const tick = () => {
    const pg = piggy.current, b = balao.current;
    if (!pg || !b) return;
    const p = modo === "ativo" ? exibido : undefined;
    const { el: alvo, tipo: t } = p ? achar(p, path, ACOES[p.acao]?.alvo ?? []) : { el: null, tipo: "espera" as Tipo };
    setTipo(t);
    const prox = modo === "ativo" && atual && path !== ROTA[atual.tela] ? aba(atual) : null;
    setParaAba(prox ? destino(prox) : "");
    let mira: HTMLElement | null = null, toque: HTMLElement | null = null, claro: HTMLElement | null = null, marca: HTMLElement | null = null;
    if (p && festa) mira = miraAnt.current?.isConnected ? miraAnt.current : alvo;
    else if (p && fora) mira = toque = marca = aba(p);
    else if (p && t === "alvo") {
      if (!p.disponivel) mira = guia(p.ancora);
      else if (etapa === "bloco") mira = claro = guia(p.ancora);
      else mira = toque = marca = alvo;
    }
    const parado = !!p && !mira && t === "espera" && !!pg.style.left;
    // Rola uma vez por etapa, e de novo se a página mudou de altura (um bloco acima chegou
    // depois e empurrou a âncora), mas só enquanto a pessoa não rolou por conta própria: o
    // refetch do SSE muda a altura e não pode puxá-la de volta.
    const pre = `${p?.id}:${etapa}:`, chave = `${pre}${document.documentElement.scrollHeight}`;
    const primeira = !rolou.current.startsWith(pre);
    // `trazer` rola agora; a mira da barra de cima (a seta do mês) não rola, e aí nada conta como rolado.
    const rolar = !!mira && !festa && !!etapa && !fora && rolou.current !== chave && (primeira || !mexeu.current) && trazer(mira);
    if (rolar) { if (primeira) mexeu.current = false; rolou.current = chave; }
    const de = pg.getBoundingClientRect(), deB = b.getBoundingClientRect(), tiltA = tiltDe(pg), tinha = !!pg.style.left;
    posicionar(mira, parado ? null : pg, b, rolar, p && t === "alvo" && !festa && !fora ? [alvo, guia(p.ancora)] : []);
    const aceso = toque ?? claro, para = aceso && caixa(aceso);
    if (!parado && mira !== miraAnt.current) {
      if (tinha) voo.current = partir(pg, de, tiltA, b, deB, ultimo.current, para);
      miraAnt.current = mira;
    }
    const [pinta, voa] = quadro(voo.current, pg, b, para);
    if (!voa) voo.current = null;
    if (pinta) ultimo.current = pinta;
    const tocavel = toque && pinta && para && corte(pinta, para); // com toque, o aceso é ele
    furo.current = tocavel ? toque : null;
    if (veus.current && sombra.current && anel.current) {
      cobrir(pinta, tocavel, voa ? null : marca, [...veus.current.children] as HTMLElement[], sombra.current, anel.current);
    }
  };
  useLayoutEffect(tick);
  // Rolagem da pessoa: só eventos de entrada (o `scroll` também vem do scrollBy do guia).
  useEffect(() => {
    mexeu.current = false;
    if (modo === "fechado") return;
    const on = (e: Event) => {
      if (e instanceof KeyboardEvent) {
        const alvo = e.target as HTMLElement;
        if (!TECLAS.includes(e.key) || alvo.closest?.("input, textarea, select") || alvo.isContentEditable) return;
      }
      mexeu.current = true;
    };
    const tipos = ["wheel", "touchmove", "keydown"];
    tipos.forEach((t) => window.addEventListener(t, on, { passive: true }));
    return () => tipos.forEach((t) => window.removeEventListener(t, on));
  }, [modo]);
  useEffect(() => {
    if (modo === "fechado") return;
    let raf = 0;
    const loop = () => { tick(); raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  });
  useEffect(() => {
    if (focar.current && modo === "ativo" && exibido) { focar.current = false; document.getElementById("guia-titulo")?.focus({ preventScroll: true }); }
  });

  const aberto = modo === "convite" || (modo === "ativo" && !!exibido);
  useEffect(() => avisarGuia(aberto));
  const leva = fora ? `Agora toca ${paraAba}.` : null;
  const mot = atual && !atual.disponivel && atual.motivo ? MOTIVO[atual.motivo] : null;
  const entendi = (id: string) => { setFase({ id, etapa: "alvo" }); focar.current = true; };
  const status = !aberto || s.editing ? ""
    : modo === "convite" ? "O Piggy quer te mostrar o painel."
    : festa ? (fim ? "Guia concluído." : acabou ? "Por agora é isso. O passo que ficou pra depois volta na Ajuda." : "Passo feito.")
    // Cada etapa muda o texto (senão a região não fala): o que o balão diz, sem adiantar o passo.
    : leva ?? (mot || tipo === "ausente" ? `Guia, passo ${n} de ${passos.length}: ${atual!.fala.titulo}`
      : etapa === "bloco" ? `Passo ${n} de ${passos.length}: ${atual!.fala.titulo}. ${atual!.fala.apresenta}` : atual!.fala.texto);
  let corpo = null;
  if (!aberto || s.editing) {
    // fechado, ou pausado enquanto organiza o painel: só a região de anúncio fica montada
  } else if (modo === "convite") {
    corpo = <>
      <h2 key="titulo" id="guia-titulo" tabIndex={-1}>Oi! Te mostro o painel?</h2>
      <p id="guia-texto">São {passos.length} passos rapidinhos: você faz, eu mostro onde fica cada coisa.</p>
      <div className="guia-acoes">
        <button type="button" className="btn btn-primary" onClick={() => iniciar(false)}>Bora</button>
        <button type="button" className="btn btn-quiet" onClick={() => fechar(true)}>Agora não</button>
      </div>
    </>;
  } else if (festa) {
    corpo = <>
      <h2 key="titulo" id="guia-titulo" tabIndex={-1}>{fim ? "Fechou! O painel é seu." : acabou ? "Por agora é isso" : "Isso aí!"}</h2>
      <p id="guia-texto">{fim ? "Quando quiser rever, o guia mora em Ajuda." : acabou ? "O passo que ficou pra depois volta quando você abrir a Ajuda." : "Passo feito. Bora pro próximo."}</p>
      {acabou && <div className="guia-acoes"><button type="button" className="btn btn-primary" onClick={() => fechar(false)}>Fechar</button></div>}
    </>;
  } else {
    const p = atual!;
    corpo = <>
      <p className="guia-passo"><span className="sr-only">Passo {n} de {passos.length}</span>{passos.map((x, i) => <i key={x.id} aria-hidden="true" data-atual={i === n - 1 || undefined} />)}</p>
      {/* O mesmo <h2> (key) em toda etapa e na festa: o foco que está nele não cai no body. */}
      <h2 key="titulo" id="guia-titulo" tabIndex={-1}>{leva ?? <>{p.fala.titulo}{p.dado === "exemplo" && <> <span className="selo">exemplo</span></>}</>}</h2>
      {!leva && <>
        <p id="guia-texto">{mot ? mot.texto : etapa === "bloco" ? p.fala.apresenta : p.fala.texto}</p>
        {mot?.link && <p><a href={OF}>{mot.link}</a></p>}
        {!mot && tipo === "ausente" && <p>Esse bloco não está no seu painel agora. Toca em Seguir; depois dá pra pôr ele de volta em Organizar.</p>}
      </>}
      {salvando && <p className="guia-dica">Salvando…</p>}
      {falhou && <p role="alert">Não consegui salvar seu progresso. <button type="button" className="btn btn-ghost" onClick={() => qc.isMutating({ mutationKey: ["guia"] }) || m.mutate(v!)}>Tentar de novo</button></p>}
      <div className="guia-acoes">
        {!leva && !mot && etapa === "bloco" && tipo !== "ausente" && <button type="button" className="btn btn-primary" onClick={() => entendi(p.id)}>Entendi</button>}
        {!leva && (mot || tipo === "ausente") && <button type="button" className="btn btn-ghost" onClick={() => seguir(p.id)}>Seguir</button>}
        <button type="button" className="btn btn-quiet" onClick={() => fechar(true)}>Pular guia</button>
      </div>
    </>;
  }

  useTecladoDoVeu(!!corpo, balao, furo, prende);
  useLayoutEffect(() => { if (!corpo) zerar(); });

  return <>
    <p className="sr-only guia-status" role="status">{status}</p>
    {corpo && <>
      <svg className="guia-sombra" aria-hidden="true"><path ref={sombra} fillRule="evenodd" /></svg>
      <div ref={veus} aria-hidden="true">{[0, 1, 2, 3].map((i) => <div key={i} className="guia-veu" />)}</div>
      <div ref={anel} className="guia-anel" hidden />
    </>}
    {corpo && <img ref={piggy} src={ICON} alt="" width={44} height={44} className="guia-piggy" />}
    {corpo && <section ref={balao} className="guia-balao" role="dialog" aria-labelledby="guia-titulo" aria-describedby="guia-texto">{corpo}</section>}
  </>;
}
