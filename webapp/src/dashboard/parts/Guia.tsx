import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AcaoGuia, Passo } from "../lib/api-v2.gen";
import { ICON } from "../lib/brand";
import { useConversation } from "../lib/conversation";
import { mesDe } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { DEMO, ErroApi, apiPost, guiaQuery, perfilQuery } from "../lib/v2";
import { route, type Path } from "../router";
import { ROTA, achar, posicionar, trazer, type Tipo } from "./guia-posicao";

// O guia do /painel (#728). O roteiro e o progresso vêm do servidor (GET/POST /api/v2/guia,
// `PASSOS` em api/v2/guia.py); aqui fica só se o guia está aberto. O passo avança quando a
// pessoa faz a ação de verdade, nunca por um "próximo". Balão não-modal: sem trava de foco.

// A aba de cada tela do roteiro (App.tsx põe `data-guia` no menu lateral e na barra de baixo).
export const navGuia = (p: Path) => {
  const tela = (Object.keys(ROTA) as Passo["tela"][]).find((t) => ROTA[t] === p);
  return tela && `nav.${tela}`;
};
// O item "Ajuda" e o Cmd-K abrem o guia por aqui.
export const abrirGuia = () => window.dispatchEvent(new Event("dash:guia"));

// Uma ação é a passagem de um retrato da tela para o seguinte; o retrato de quando o passo
// começou é o primeiro, então o que já estava escolhido antes não conta.
interface Retrato { mes: string; cat: string | null; perguntas: number; path: Path }
const ACOES: Record<string, (antes: Retrato, agora: Retrato) => boolean> = {
  "mes.trocado": (a, b) => b.mes !== a.mes,
  // Categoria escolhida no Resumo não conta: o passo é na página de Gastos.
  "categoria.aberta": (a, b) => b.path === "/gastos" && b.cat != null && b.cat !== a.cat,
  "piggy.perguntou": (a, b) => b.perguntas > a.perguntas,
};

const OF = "/settings?view=open-finance";
const MOTIVO: Record<NonNullable<Passo["motivo"]>, { texto: string; link?: string }> = {
  sem_dados: { texto: "Ainda não chegou gasto do seu banco neste mês nem no anterior. Conecta um banco e esse número aparece aqui.", link: "Conectar banco" },
  sincronizando: { texto: "Seu banco está sincronizando agora. Daqui a pouco esse número aparece aqui." },
  conexao_com_erro: { texto: "A conexão com o seu banco deu erro, e esse número não chega. Dá uma olhada nela.", link: "Ver conexão" },
};

type Modo = "fechado" | "convite" | "ativo";
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
  const [fechouDialog, reavaliar] = useState(0);
  const ofereceu = useRef(false);
  const focar = useRef(false);
  const rolou = useRef("");
  const mexeu = useRef(false); // a pessoa rolou por conta própria desde a rolagem inicial do passo
  const piggy = useRef<HTMLImageElement>(null);
  const balao = useRef<HTMLElement>(null);

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

  const iniciar = (rever: boolean) => {
    setRev(rever); setVistos([]); setFesta(null); setModo("ativo");
    focar.current = true;
  };
  const fechar = (dispensa: boolean) => {
    if (dispensa) m.mutate({ acao: "dispensar" });
    if (balao.current?.contains(document.activeElement)) document.getElementById("page-title")?.focus({ preventScroll: true });
    setModo("fechado"); setFesta(null);
  };
  // O botão some com o passo: o foco vai para o título do que vem (o próximo ou a tela final).
  const pular = (id: string) => { setVistos((x) => [...x, id]); focar.current = true; };

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
    const rodando = modo === "ativo" && atual && atual.disponivel && !festa && !salvando && !falhou && !s.editing;
    const a = antes.current;
    antes.current = { id: rodando ? atual.id : "", r: agora };
    if (rodando && a.id === atual.id && ACOES[atual.acao]?.(a.r, agora)) m.mutate({ acao: "feito", passo: atual.id });
  });

  // Comemoração: o passo seguinte entra depois dela; a última fica até fechar.
  useEffect(() => {
    if (!festa || acabou) return;
    const t = setTimeout(() => setFesta(null), 1600);
    return () => clearTimeout(t);
  }, [festa, acabou]);
  // Acabaram os passos sem comemoração (Seguir no último; refetch que trouxe o resto feito por
  // outra aba ou antes do 200): a mesma tela final, nunca fechar calado.
  useEffect(() => {
    if (modo !== "ativo" || !g || atual || festa) return;
    const id = vistos[vistos.length - 1] ?? passos[passos.length - 1]?.id;
    if (id) setFesta(id); else setModo("fechado");
  });

  // Posição: segue a âncora a cada quadro (rolagem, grade arrastável, troca de página).
  const tick = () => {
    if (!piggy.current || !balao.current) return;
    const { el, tipo: t } = exibido && modo === "ativo" ? achar(exibido, path) : { el: null, tipo: "espera" as Tipo };
    setTipo(t);
    // Rola uma vez por passo, e de novo se a página mudou de altura (um bloco acima chegou
    // depois e empurrou a âncora), mas só enquanto a pessoa não rolou por conta própria: o
    // refetch do SSE muda a altura e não pode puxá-la de volta.
    const chave = `${exibido?.id}:${document.documentElement.scrollHeight}`;
    const primeira = !rolou.current.startsWith(`${exibido?.id}:`);
    const rolar = !!el && t === "ancora" && rolou.current !== chave && (primeira || !mexeu.current);
    if (rolar) { if (primeira) mexeu.current = false; rolou.current = chave; trazer(el); }
    posicionar(el, piggy.current, balao.current, rolar);
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
  const status = !aberto || s.editing ? ""
    : modo === "convite" ? "O Piggy quer te mostrar o painel."
    : festa ? (fim ? "Guia concluído." : acabou ? "Por agora é isso. O passo que ficou pra depois volta na Ajuda." : "Passo feito.")
    : `Guia, passo ${n} de ${passos.length}: ${atual!.fala.titulo}`;
  let corpo = null;
  if (!aberto || s.editing) {
    // fechado, ou pausado enquanto organiza o painel: só a região de anúncio fica montada
  } else if (modo === "convite") {
    corpo = <>
      <h2 id="guia-titulo" tabIndex={-1}>Oi! Te mostro o painel?</h2>
      <p>São {passos.length} passos rapidinhos: você faz, eu mostro onde fica cada coisa.</p>
      <div className="guia-acoes">
        <button type="button" className="btn btn-primary" onClick={() => iniciar(false)}>Bora</button>
        <button type="button" className="btn btn-quiet" onClick={() => fechar(true)}>Agora não</button>
      </div>
    </>;
  } else if (festa) {
    corpo = <>
      <h2 id="guia-titulo" tabIndex={-1}>{fim ? "Fechou! O painel é seu." : acabou ? "Por agora é isso" : "Isso aí!"}</h2>
      <p>{fim ? "Quando quiser rever, o guia mora em Ajuda." : acabou ? "O passo que ficou pra depois volta quando você abrir a Ajuda." : "Passo feito. Bora pro próximo."}</p>
      {acabou && <div className="guia-acoes"><button type="button" className="btn btn-primary" onClick={() => fechar(false)}>Fechar</button></div>}
    </>;
  } else {
    const p = atual!;
    const mot = !p.disponivel && p.motivo ? MOTIVO[p.motivo] : null;
    corpo = <>
      <p className="guia-passo">Passo {n} de {passos.length}</p>
      <h2 id="guia-titulo" tabIndex={-1}>{p.fala.titulo}{p.dado === "exemplo" && <> <span className="selo">exemplo</span></>}</h2>
      <p>{mot ? mot.texto : p.fala.texto}</p>
      {mot?.link && <p><a href={OF}>{mot.link}</a></p>}
      {!mot && tipo === "ausente" && <p>Esse bloco não está no seu painel agora. Dá pra pôr de volta em Organizar.</p>}
      {!mot && tipo === "nav" && <p className="guia-dica">Primeiro, abre {route(ROTA[p.tela]).short}.</p>}
      {salvando && <p className="guia-dica">Salvando…</p>}
      {falhou && <p role="alert">Não consegui salvar seu progresso. <button type="button" className="btn btn-ghost" onClick={() => qc.isMutating({ mutationKey: ["guia"] }) || m.mutate(v!)}>Tentar de novo</button></p>}
      <div className="guia-acoes">
        {(mot || tipo === "ausente") && <button type="button" className="btn btn-ghost" onClick={() => pular(p.id)}>Seguir</button>}
        <button type="button" className="btn btn-quiet" onClick={() => fechar(true)}>Pular guia</button>
      </div>
    </>;
  }

  return <>
    <p className="sr-only" role="status">{status}</p>
    {corpo && <img ref={piggy} key={exibido?.id ?? "convite"} src={ICON} alt="" width={44} height={44} className="guia-piggy" data-festa={festa ? "" : undefined} />}
    {corpo && <section ref={balao} className="guia-balao" aria-labelledby="guia-titulo">{corpo}</section>}
  </>;
}
