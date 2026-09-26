// /q#p=<perfil>&r=<respostas>: guarda o resultado do quiz num cookie e segue pro cadastro.
// Perfil e respostas são dado financeiro: chegam no FRAGMENTO (não vão ao servidor nem
// ao Referer) e saem só no cookie — nunca em query, DOM ou analytics. O servidor
// revalida o cookie (db/signup_quiz.py); tests/test_signup_quiz.py compara as listas.
(function () {
  const PERFIS = ["economizar", "investir", "controlar", "dividas", "autonomo"];
  const RESPOSTAS = /^[a-d][a-d][a-d][a-d][a-e]$/;
  // O XQuiz pode grudar a UTM depois do #: `#p=x&r=y?utm…` ou `#p=x&r=y&utm…`.
  const frag = new URLSearchParams(location.hash.slice(1).replace(/\?/g, "&"));
  const query = new URLSearchParams(location.search);
  query.delete("p");
  query.delete("r");
  const p = frag.get("p");
  const r = frag.get("r");
  frag.delete("p");
  frag.delete("r");
  frag.forEach(function (v, k) { if (!query.has(k)) query.append(k, v); });
  // Resultado inválido apaga o de uma visita anterior: senão o cadastro grava o perfil velho.
  const attrs = "; Path=/; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
  document.cookie = PERFIS.indexOf(p) !== -1
    ? "quiz_result=v1." + p + (RESPOSTAS.test(r || "") ? "." + r : "") + "; Max-Age=86400" + attrs
    : "quiz_result=; Max-Age=0" + attrs;
  const qs = query.toString();
  location.replace("/cadastro" + (qs ? "?" + qs : ""));
})();
