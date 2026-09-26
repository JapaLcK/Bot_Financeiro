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
  if (PERFIS.indexOf(p) !== -1) {
    document.cookie = "quiz_result=v1." + p + (RESPOSTAS.test(r || "") ? "." + r : "") +
      "; Max-Age=86400; Path=/; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
  }
  const qs = query.toString();
  location.replace("/cadastro" + (qs ? "?" + qs : ""));
})();
