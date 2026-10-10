/* Acompanhamento da coleta do Open Finance nos Ajustes (Onda 5, D6).

   Com alguma conexão em "Atualizando…" (`ui.state === "updating"`, o mesmo
   predicado do comecar.js e do app nativo), relê o snapshot com intervalos de
   5/10/20/40 s e depois de 60 s, contados do FIM de uma leitura até o próximo
   pedido: o `setTimeout` só é armado depois da resposta, então dois pedidos
   nunca se sobrepõem. Para quando nada mais está em andamento ou em 30 min,
   não pede nada com a aba oculta e relê na volta ao foco.

   Quem pinta é o `reler` (o loadData do settings.html, com propagate e sem
   caixinhas); quem decide se segue é o `observar`, chamado no fim de toda
   pintura da lista. A tabela estados × eventos mora em
   docs/open_finance_estados.md §2.4.

   Atualizar com `sync.still_updating > 0`: a Pluggy ainda coleta, mas o card
   pode estar "Atualizado" (item UPDATING com todos os produtos, contrato de
   tests/test_of_health.py). O `novoCiclo(resposta)` guarda, para cada item com
   `still_updating`, o `last_sync_at` da conexão na própria resposta; o ciclo
   segue até esse `last_sync_at` mudar no snapshot (o sync do fim da coleta),
   a conexão sumir ou cair em erro / ação necessária, ou o teto. Sem chave nova no corpo HTTP:
   `sync.items[].item_id` casa com `connections[].provider_item_id`.

   Volta ao foco (DP2 = A, DP3 = B): relê UMA vez sempre que alguma conexão não
   estiver "Atualizado". O ciclo só abre de novo se essa leitura trouxer
   "Atualizando…" numa conexão que não estava num ciclo esgotado: a mesma
   coleta que bateu o teto não renasce por troca de aba; só Atualizar ou
   conectar (`novoCiclo`) a acompanham de novo. O teto também esquece os itens
   do `still_updating`, pelo mesmo motivo.

   Só lê com a seção de Open Finance na tela (`visivel`): fora dela o tique
   pausa como com a aba oculta, e voltar para ela (`retomar`) segue a mesma
   regra da volta ao foco.

   Volta ao foco com um POST /refresh em voo: a leitura fica pendente e o fim
   do Atualizar MAIS RECENTE (`fimDoAtualizar`, no `finally` dele no
   refreshOpenFinance, ou no fim do prazo dele) a cumpre; um Atualizar velho
   preso não segura a trava (`ocupado`). A resposta OK do POST que chega DEPOIS da volta a
   substitui, porque a pintura dele é a leitura; uma volta durante o GET que o
   PTR faz depois do POST OK ainda lê 1 vez no fim. O teto deixa UMA leitura
   para a volta ao foco, mesmo com tudo "Atualizado" (DP2). O Atualizar mais
   recente abortado pelo prazo deixa UMA releitura (`releitura`), porque o
   servidor pode ter terminado depois do abort. Cada leitura tem prazo
   (`LEITURA_PRAZO_MS`): um GET pendurado não prende `emVoo`. */
(function () {
  "use strict";

  const CADENCIA = [5000, 10000, 20000, 40000];
  const DEPOIS = 60000;
  const TETO = 30 * 60 * 1000;
  // Estados em que o item do `still_updating` segue na espera; os outros de
  // `_LABELS` (core/services/pluggy_health.py) a encerram, e estado novo também
  // (default: parar de ler). tests/test_of_coleta_contrato.py confere a lista.
  const SEGUE_ESPERANDO = ["updated", "updating", "partial", "no_accounts"];
  const PARA = [401, 403, 404];   // sessão morta ou rota que não vai voltar: não insiste

  let reler = null;
  let ocupado = () => false;
  let visivel = () => true;
  let ativo = false, inicio = 0, passo = 0, espera = 0, timer = null, emVoo = false;
  let pendente = false;     // volta ao foco barrada pelo POST /refresh: o fim dele lê
  let devida = false;       // UMA leitura para a volta ao foco: a do teto (DP2) ou a releitura oculta
  let foraDoOk = false;     // a última lista tem conexão fora de "updated" (DP3)
  let andamento = [];       // chaves em "updating" na última lista
  // Chaves que estavam em "updating" quando o teto bateu. Saem quando a conexão
  // deixa de estar em "updating" (a coleta acabou; a próxima é outra coleta).
  // ponytail: "mesma coleta" = "em updating em toda leitura vista"; uma volta
  // final→updating sem ninguém olhar conta como a mesma, até o próximo Atualizar.
  const esgotados = new Set();
  // provider_item_id → last_sync_at visto na resposta do Atualizar que disse
  // `still_updating` para ele.
  const aguardando = new Map();

  const chave = (c) => String(c.id != null ? c.id : c.provider_item_id);
  const estado = (c) => c && c.ui && c.ui.state;
  const naTela = () => !document.hidden && visivel();

  function agendar(ms) {
    espera = ms;
    timer = setTimeout(tique, ms);
  }
  const proximo = () => (passo < CADENCIA.length ? CADENCIA[passo++] : DEPOIS);

  function desligar() {
    ativo = false;
    clearTimeout(timer);
    timer = null;
  }

  // 402 e sessão morta: dormente até a próxima pintura com coleta.
  function parar() {
    desligar();
    foraDoOk = false;
    devida = false;
    aguardando.clear();
  }

  // Prazo da leitura: o GET do snapshot é só banco
  // (`get_open_finance_snapshot`, db/open_finance.py, sem Pluggy), então 15 s
  // já é servidor degradado. Vencido, o fetch é abortado (a resposta tardia não
  // pinta) e a corrida solta `emVoo` mesmo se o que pendurar for o caixinhas
  // pendente, que não leva o signal. Limite aceito (dono, 2026-10-09): snapshot
  // acima de 15 s nunca pinta pelo acompanhamento; o botão Atualizar (pinta
  // pelo corpo do POST, prazo de 60 s) e recarregar a página continuam pintando.
  // `AbortController` + `setTimeout`, não `AbortSignal.timeout` (iOS 14, pb-nav.js).
  const LEITURA_PRAZO_MS = 15000;

  async function ler() {
    emVoo = true;
    const corte = new AbortController();
    let prazo = null;
    try {
      await Promise.race([reler(corte.signal), new Promise((_, falhou) => {
        prazo = setTimeout(() => { corte.abort(); falhou(new Error("prazo da leitura")); }, LEITURA_PRAZO_MS);
      })]);
    } catch (err) {
      // Rede, 5xx, 429, prazo: a tela já foi preservada pelo propagate; segue.
      if (err && PARA.includes(err.status)) parar();
    } finally {
      clearTimeout(prazo);
      emVoo = false;
    }
    if (ativo && !timer) agendar(proximo());
  }

  function tique() {
    timer = null;
    if (Date.now() - inicio >= TETO) {
      andamento.forEach((k) => esgotados.add(k));
      aguardando.clear();
      desligar();
      // ponytail: um ciclo reaberto pelo `observar` sem `novoCiclo` (ex.:
      // disconnectAll) que termina normal deixa `devida` true: 1 GET a mais na
      // próxima volta ao foco, com tudo "Atualizado" (§2.4). Se pesar, zerar no `ligar`.
      devida = true;
      return;
    }
    if (!naTela()) return;   // ativo e sem timer: a volta ao foco / à seção lê
    if (ocupado()) { agendar(espera); return; }   // o POST /refresh em voo repinta
    ler();
  }

  function retomar() {
    if (!reler || !naTela() || emVoo) return;
    if (ocupado()) { pendente = true; return; }   // o fim do POST cumpre (fimDoAtualizar)
    if (ativo) {
      clearTimeout(timer);
      tique();
      if (ativo || emVoo) return;   // leu, ou o POST em voo reagendou
    }
    // Fora de ciclo, ou depois do teto (DP2): UMA leitura; o `observar` dela
    // decide se abre ciclo.
    if (foraDoOk || devida) { devida = false; ler(); }
  }

  function aoVoltar(ev) {
    if (ev.type === "pageshow" && !ev.persisted) return;
    retomar();
  }

  function observar(lista) {
    const l = Array.isArray(lista) ? lista : [];
    foraDoOk = l.some((c) => estado(c) !== "updated");
    andamento = l.filter((c) => estado(c) === "updating").map(chave);
    esgotados.forEach((k) => { if (!andamento.includes(k)) esgotados.delete(k); });
    aguardando.forEach((visto, item) => {
      const c = l.find((x) => String(x.provider_item_id) === item);
      // Sincronizou de novo, sumiu, ou caiu em erro / ação necessária (a falha
      // não avança o last_sync_at). "Dados parciais" e "Sem dados" ficam: são a foto
      // antiga de uma coleta que ainda roda (decisão do dono, Tester r2).
      if (!c || c.last_sync_at !== visto || !SEGUE_ESPERANDO.includes(estado(c))) aguardando.delete(item);
    });
    if (!andamento.length && !aguardando.size) { desligar(); return; }
    if (!aguardando.size && andamento.every((k) => esgotados.has(k))) return;
    ligar();
  }

  function ligar() {
    if (ativo || !reler) return;
    ativo = true; inicio = Date.now(); passo = 0;
    if (!emVoo) agendar(proximo());   // em voo, quem agenda é o fim da leitura
  }

  // A resposta do POST /refresh só LIGA: no PTR a pintura de tela é um GET
  // depois, que pode falhar e deixar `aguardando` sem ciclo. Nunca desliga nem
  // mexe em `foraDoOk`: isso é do `observar`, com a lista que a tela pintou.
  // Cada Atualizar substitui o `aguardando` do anterior: o servidor mediu de novo.
  function ligarPelaResposta(resposta) {
    aguardando.clear();
    const sync = resposta.sync || {};
    const conns = Array.isArray(resposta.connections) ? resposta.connections : [];
    if (Number(sync.still_updating) > 0) {
      for (const i of Array.isArray(sync.items) ? sync.items : []) {
        if (!i.still_updating) continue;
        const c = conns.find((x) => String(x.provider_item_id) === String(i.item_id));
        if (c && SEGUE_ESPERANDO.includes(estado(c))) aguardando.set(String(i.item_id), c.last_sync_at);
      }
    }
    const vivos = conns.filter((c) => estado(c) === "updating").map(chave);
    andamento = [...new Set([...andamento, ...vivos])];
    if (vivos.length || aguardando.size) ligar();
  }

  // `resposta`: o corpo do POST /refresh (o onConnected chama sem).
  function novoCiclo(resposta) {
    pendente = false;     // a pintura do POST é a leitura; a cadência conta dele
    devida = false;
    esgotados.clear();
    if (resposta) ligarPelaResposta(resposta);
    if (!ativo) return;   // o `observar` da pintura que vem em seguida arma
    inicio = Date.now(); passo = 0;
    clearTimeout(timer);
    timer = null;
    if (!emVoo) agendar(proximo());
  }

  // Fim do Atualizar mais recente (o `finally` dele ou o prazo), qualquer
  // desfecho. O sucesso já limpou a pendência (novoCiclo); o 402 já parou
  // (parar), e o `retomar` não acha o que ler.
  function fimDoAtualizar() { if (pendente) { pendente = false; retomar(); } }

  // UMA leitura depois do Atualizar mais recente abortado pelo prazo (agendada
  // pelo settings.html, que a cancela se outro Atualizar começar ou se o
  // snapshot responder 402, junto do `parar`). Com o ciclo
  // ligado, o tique já lê; fora da tela, fica devida para a volta.
  function releitura() {
    if (ativo) return;
    devida = true;
    retomar();
  }

  window.PBColetaOF = {
    configurar(opts) {
      reler = opts.reler;
      if (opts.ocupado) ocupado = opts.ocupado;
      if (opts.visivel) visivel = opts.visivel;
      document.addEventListener("visibilitychange", aoVoltar);
      window.addEventListener("pageshow", aoVoltar);
    },
    observar,
    novoCiclo,
    retomar,
    parar,
    fimDoAtualizar,
    releitura,
  };
})();
