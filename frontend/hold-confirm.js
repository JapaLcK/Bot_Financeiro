/* Confirmar SEGURANDO o botão (ação irreversível). Script clássico, sem dependência.
 *
 *   const h = PBHoldConfirm.ligar(botao, { ms, confirmar, podeComecar?, aviso? });
 *   h.resetar();   // a página chama quando a ação falhou e o botão volta a valer
 *
 * `confirmar(origem)`: 'segurar' (segurou `ms`) ou 'clique' (clique SEM gesto
 * físico, event.detail === 0: leitor de tela, switch control). Clique de mouse/toque
 * rápido (detail >= 1) não faz nada. `podeComecar()` false = não arma.
 * `aviso` = elemento aria-live (FORA do botão, senão vira parte do nome acessível); só recebe 'Confirmado'/'Cancelado', nunca por frame.
 *
 * Estado em botao.dataset.holdState = idle | holding | done (CSS e testes leem).
 * A duração do anel sai de --hold-ms, a MESMA `ms` do timer. O timer é só
 * setTimeout (nunca transitionend): o teste mede com page.clock.
 *
 *   idle    --aperta--> holding (se não disabled e podeComecar() !== false)
 *   holding --solta---> idle ('Cancelado')
 *   holding --aborta--> idle (pointerleave/pointercancel/touchcancel/blur/blur da janela/Esc/aba oculta)
 *   holding --ms------> done ('Confirmado', confirmar('segurar'))
 *   holding --ms, botão não alcançável (alcancavel() falso)--> idle, em silêncio, sem confirmar
 *   done    --resetar-> idle        (done ignora tudo até lá)
 */
(function () {
  "use strict";

  function ehTeclaDeConfirmar(e) {
    return e.key === "Enter" || e.key === " ";
  }

  // A máquina de estados, sem DOM de evento: o que o `ligar` pluga nos eventos.
  function criarMaquina(botao, opts) {
    const ms = opts.ms;
    let estado = "idle";
    let timer = 0;

    function mudar(novo) {
      estado = novo;
      botao.dataset.holdState = novo;
    }
    function dizer(texto) {
      if (opts.aviso) opts.aviso.textContent = texto;
    }
    function limpar() {
      clearTimeout(timer);
      timer = 0;
    }
    // O centro do botão tem de receber o toque: elementFromPoint ignora visibility:hidden e
    // pointer-events:none e devolve a cobertura (overlay, modal de outro fluxo). Opacidade 0
    // em qualquer ancestral também conta. Fora da viewport = não alcançável.
    function alcancavel() {
      if (!botao.isConnected) return false;
      for (let n = botao; n; n = n.parentElement) {
        if (getComputedStyle(n).opacity === "0") return false;
      }
      const r = botao.getBoundingClientRect();
      const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return !!el && botao.contains(el);
    }
    function concluir() {
      timer = 0;
      // Escondido, coberto ou fora da tela no disparo (Cancelar, troca de seção, modal de
      // outro fluxo): não confirma, volta a idle em silêncio.
      if (!alcancavel()) return mudar("idle");
      mudar("done");
      dizer("Confirmado");
      opts.confirmar("segurar");
    }
    mudar("idle");
    return {
      ocioso: function () { return estado === "idle"; },
      apertar: function () {
        if (estado !== "idle" || botao.disabled) return;
        if (opts.podeComecar && opts.podeComecar() === false) return;
        mudar("holding");
        dizer("");
        timer = setTimeout(concluir, ms);
      },
      soltar: function () {
        if (estado !== "holding") return;
        limpar();
        mudar("idle");
        dizer("Cancelado");
      },
      abortar: function () {
        if (estado !== "holding") return;
        limpar();
        mudar("idle");
      },
      resetar: function () {
        limpar();
        mudar("idle");
        dizer("");
      },
    };
  }

  // Disparou com a tecla ainda apertada: o foco vai para o botão do modal que a página
  // abre e o auto-repeat fecharia o aviso antes de ser lido. Engole o repeat até o keyup.
  function engolirRepeat(botao, tecla) {
    function fim() {
      document.removeEventListener("keydown", repeat, true);
      document.removeEventListener("keyup", sobe, true);
      window.removeEventListener("blur", fim);
    }
    function repeat(e) {
      if (!botao.isConnected) return fim();
      if (e.key !== tecla || !e.repeat) return;
      e.preventDefault();
      e.stopImmediatePropagation();
    }
    function sobe(e) { if (e.key === tecla) fim(); }
    document.addEventListener("keydown", repeat, true);
    document.addEventListener("keyup", sobe, true);
    window.addEventListener("blur", fim);
  }

  function ligar(botao, opts) {
    if (!opts || !(opts.ms > 0) || typeof opts.confirmar !== "function") {
      throw new Error("PBHoldConfirm.ligar: ms (> 0) e confirmar são obrigatórios");
    }
    let tecla = null;   // tecla que armou o hold e segue apertada
    const m = criarMaquina(botao, Object.assign({}, opts, {
      confirmar: function (origem) {
        if (tecla) engolirRepeat(botao, tecla);
        opts.confirmar(origem);
      },
    }));
    const apertar = m.apertar;
    const soltar = m.soltar;
    const abortar = m.abortar;

    botao.style.setProperty("--hold-ms", opts.ms + "ms");

    botao.addEventListener("pointerdown", function (e) {
      if (e.button !== 0 || !e.isPrimary) return;
      // Toque captura o ponteiro no ALVO tocado (o rótulo ou o svg filho, não o botão)
      // e o pointerleave nunca vem: solta a captura para sair do botão com o dedo
      // abortar, como com o mouse.
      if (e.target.hasPointerCapture && e.target.hasPointerCapture(e.pointerId)) {
        e.target.releasePointerCapture(e.pointerId);
      }
      tecla = null;
      apertar();
    });
    botao.addEventListener("pointerup", function (e) {
      if (e.isPrimary) soltar();
    });
    ["pointerleave", "pointercancel", "touchcancel", "blur"].forEach(function (nome) {
      botao.addEventListener(nome, abortar);
    });
    botao.addEventListener("contextmenu", function (e) { e.preventDefault(); });

    // preventDefault em TODO Enter/Espaço (inclusive repeat): sem isso o Enter gera
    // click na descida e o Espaço na subida, e o "Enter rápido" viraria clique com
    // detail 0 (= o fallback assistivo).
    botao.addEventListener("keydown", function (e) {
      if (!ehTeclaDeConfirmar(e)) return;
      e.preventDefault();
      if (!e.repeat) {
        apertar();
        if (!m.ocioso()) tecla = e.key;
      }
    });
    botao.addEventListener("keyup", function (e) {
      if (!ehTeclaDeConfirmar(e)) return;
      e.preventDefault();
      if (e.key === tecla) tecla = null;
      soltar();
    });

    botao.addEventListener("click", function (e) {
      if (e.detail === 0 && m.ocioso() && !botao.disabled) opts.confirmar("clique");
    });

    // Esc no document: o Safari não foca o botão no clique, então keydown nele não chega.
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") abortar();
    });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) abortar();
    });
    window.addEventListener("blur", abortar);   // Cmd-Tab com o botão sem foco (Safari)

    return { resetar: m.resetar };
  }

  window.PBHoldConfirm = { ligar: ligar };
})();
