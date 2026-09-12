/**
 * Nenhum `style="…${…}…"` escreve BRANCO literal — a categoria da 6ª rodada,
 * medida ESTATICAMENTE.
 *
 * Mora fora do `modal_claro_veu.test.mjs` porque é outra espécie de medição: lê o
 * TEXTO do arquivo e não precisa de navegador nenhum, enquanto aquele arquivo paga
 * um Chromium por caso. E porque não prova comportamento — prova que o CONSTRUTO
 * não existe. Anda AO LADO do ΔE, nunca no lugar dele (`CLAUDE.md` §3 é explícito
 * sobre teste que lê texto de arquivo).
 *
 * Existe porque as duas vias que já rodavam eram cegas a ele: `rg` por linha nem
 * casa (o `style="` cai numa linha e o `${` em outra) e o `[^"]*` de um `rg`
 * multilinha para na primeira aspa DENTRO do ternário, então o `#fff` de
 * `border:2px solid ${sel ? "#fff" : "transparent"}` nunca aparecia no match.
 * `estilosInline()` trata `${ … }` como nível aninhado.
 *
 * Rodar:  npm run test:frontend
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { estilosInline } from "./_modal_veu.mjs";

test("nenhum style inline interpolado escreve branco literal", async () => {
  const BRANCO = /#fff\b|#ffffff\b|\bwhite\b|255\s*,\s*255\s*,\s*255/i;
  const achados = [];
  for (const arq of ["dashboard.js", "dashboard.html", "admin-dashboard.html", "home.html"])
    for (const s of estilosInline(arq))
      if (s.val.includes("${") && BRANCO.test(s.val))
        achados.push(`${arq}:${s.linha}  ${s.val.replace(/\s+/g, " ").slice(0, 120)}`);
  // Um tolerado, e a tolerância é de ESCOPO, não de mérito. O `color` do aviso de
  // e-mail vai para `#agentes-shelf` (`dashboard.html:663`), que não tem ancestral
  // `.modal`. A evidência é do `dashboard.html` e só dele, porque é lá que o shelf
  // mora — o primeiro `class="modal` do arquivo vem DEPOIS dele:
  //     grep -n 'id="agentes-shelf"\|class="modal' frontend/dashboard.html | head -2
  // (A frase anterior citava ":1623" como a linha do primeiro modal. 1623 é o TOTAL
  // DE LINHAS do arquivo, usado como número de linha: a conclusão estava certa e a
  // evidência era inventada, que é o mecanismo do `dashboard.css:69` que este PR
  // existe para consertar. A versão seguinte citou `dashboard.js:2019` e apodreceu
  // no mesmo commit, porque este PR adiciona linhas ao `dashboard.js`. Daí o
  // comando no lugar do número, e só o arquivo que importa.)
  // A frase que vinha aqui dizia "fora de modal a superfície segue escura no
  // claro, então o branco translúcido é legítimo", e ela é FALSA. Medido no tema
  // claro: `.ag-card` é rgba(255,255,255,.85) sobre o body rgb(246,244,241), o que
  // compõe rgb(254,253,253); o `rgba(255,255,255,.4)` deste inline dá 1,01:1 e o
  // `rgba(255,255,255,.6)` do botão irmão idem. É defeito de contraste REAL, só
  // que PRÉ-EXISTENTE (`origin/main:dashboard.js:11050`, mesma tinta) e fora do
  // escopo deste PR, que é o modal. Fica como pendência nominal — não como
  // "legítimo". Escrever "é legítimo" sem medir é o modo de falha que este PR
  // corrigiu em `dashboard.css:69`; não reintroduza aqui.
  // ÂNCORA EM ARQUIVO, não no texto do valor. `/agentes|emailOn/` casava contra
  // `arquivo:linha␣␣valor`, então qualquer branco inline NOVO cujo valor contivesse
  // a palavra "agentes" entrava tolerado sem revisão. O par arquivo+conteúdo do
  // aviso de e-mail é o que identifica este caso; a linha não entra porque anda a
  // cada edição do `dashboard.js` e viraria falso vermelho.
  const TOLERADO = (a) => a.startsWith("dashboard.js:") && /\bemailOn\b/.test(a);
  // SUBCONJUNTO, não contagem exata. `assert.equal(fora.length, 1)` PUNIA o
  // conserto: trocar o `rgba(255,255,255,.4)` por um token deixava o caso
  // vermelho, que é o §2 de novo (contagem como asserção). O invariante é
  // "nenhum branco inline novo", e consertar o tolerado tem de ficar VERDE.
  // Entrada nova fora de modal também reprova, de propósito: é lombada, e quem
  // adicionar justifica aqui com a medição em vez de alargar o regex.
  assert.deepEqual(achados.filter((a) => !TOLERADO(a)), [],
    `style inline interpolado escrevendo branco literal:\n  ` +
    achados.filter((a) => !TOLERADO(a)).join("\n  "));
});
