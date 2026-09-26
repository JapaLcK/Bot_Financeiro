// /q#p=<perfil>&r=<respostas>[&e=<email>&c=<código>]: guarda o resultado do quiz num
// cookie e segue. Perfil e respostas são dado financeiro: chegam no FRAGMENTO (não vão
// ao servidor nem ao Referer) e saem só no cookie — nunca em query, DOM ou analytics. O
// servidor revalida o cookie (db/signup_quiz.py); tests/test_signup_quiz.py compara as listas.
// Com e/c (o código que o webhook do XQuiz mandou), a conta nasce aqui, sem senha, pelo
// /auth/verify-email — mas só depois do clique em Continuar, com o e-mail na tela: quem
// abre o link de outra pessoa vê um e-mail que não é o seu (login CSRF).
(function () {
  const PERFIS = ["economizar", "investir", "controlar", "dividas", "autonomo"];
  const RESPOSTAS = /^[a-d][a-d][a-d][a-d][a-e]$/;
  // O XQuiz pode grudar a UTM depois do #: `#p=x&r=y?utm…` ou `#p=x&r=y&utm…`.
  const frag = new URLSearchParams(location.hash.slice(1).replace(/\?/g, "&"));
  const query = new URLSearchParams(location.search);
  const p = frag.get("p");
  const r = frag.get("r");
  // O URLSearchParams lê `+` como espaço, e `+` é legítimo em e-mail.
  const email = (frag.get("e") || "").replace(/ /g, "+").trim();
  const code = (frag.get("c") || "").replace(/\D/g, "");
  // e/c saem ANTES do laço: senão e-mail e código iriam para a query (e para o log).
  ["p", "r", "e", "c"].forEach(function (k) { frag.delete(k); query.delete(k); });
  frag.forEach(function (v, k) { if (!query.has(k)) query.append(k, v); });
  const qs = query.toString();
  history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
  // Resultado inválido apaga o de uma visita anterior: senão o cadastro grava o perfil velho.
  const attrs = "; Path=/; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
  document.cookie = PERFIS.indexOf(p) !== -1
    ? "quiz_result=v1." + p + (RESPOSTAS.test(r || "") ? "." + r : "") + "; Max-Age=86400" + attrs
    : "quiz_result=; Max-Age=0" + attrs;
  if (email && code) confirmar(email, code, query);
  else location.replace("/cadastro" + (qs ? "?" + qs : ""));

  function confirmar(email, code, query) {
    const $ = function (id) { return document.getElementById(id); };
    const campo = $("codigo");
    const msg = $("msg");
    const alvo = document.createElement("strong");
    alvo.textContent = email + "?";
    $("titulo").textContent = "Criar sua conta com ";
    $("titulo").appendChild(alvo);
    document.title = "Criar sua conta | PigBank";
    $("sem-js").hidden = true;
    $("confirma").hidden = false;

    function post(url, corpo) {
      const m = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/);
      return fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": m ? decodeURIComponent(m[1]) : "" },
        body: JSON.stringify(corpo),
      });
    }

    function aviso(status) {
      msg.textContent = status === 429
        ? "Muitas tentativas. Aguarde alguns minutos e tente de novo."
        : "Não deu certo. Tente de novo.";
    }

    async function entrar(resp) {
      const d = await resp.json();
      const destino = new URL(d.dashboard_url || "/home", location.origin);
      query.forEach(function (v, k) { if (!destino.searchParams.has(k)) destino.searchParams.append(k, v); });
      location.replace(destino.toString());
    }

    function pedeCodigo() {
      $("campo-codigo").hidden = false;
      $("extras").hidden = false;
      campo.value = "";
      campo.focus();
    }

    $("confirma").addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const digitado = $("campo-codigo").hidden ? code : campo.value.replace(/\D/g, "");
      if (!digitado) { campo.focus(); return; }
      $("continuar").disabled = true;
      msg.textContent = "";
      try {
        const resp = await post("/auth/verify-email", { email: email, code: digitado });
        if (resp.ok) return entrar(resp);
        if (resp.status === 400) pedeCodigo();
        else aviso(resp.status);
      } catch (_) {
        aviso(0);
      }
      $("continuar").disabled = false;
    });

    $("reenviar").addEventListener("click", async function () {
      try {
        const resp = await post("/auth/quiz/resend", { email: email });
        if (resp.ok) msg.textContent = "Se o cadastro estiver pendente, um código novo foi para o seu e-mail.";
        else aviso(resp.status);
      } catch (_) {
        aviso(0);
      }
    });
  }
})();
