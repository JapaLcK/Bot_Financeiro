/**
 * O carimbo do artefato da ilha React (`webapp/build-stamp.txt`) mede alguma
 * coisa — e o que ele mede é o que o CI usa para dizer "o bundle commitado é o
 * build de hoje".
 *
 * Este arquivo é o controle negativo dele. Sem um teste, o gate era pura
 * afirmação: `node scripts/precos_bundle_stamp.mjs --check` saindo 0 num
 * repositório em dia não distingue "confere de verdade" de "sempre diz sim" —
 * o gate anterior (rebuildar no CI e comparar bytes) morreu por causa dessa
 * classe de problema, só do outro lado: ele podia dizer NÃO por diferença de
 * arquitetura, sem ninguém ter errado nada.
 *
 * As sondas CRIAM e APAGAM arquivo próprio em vez de editar fonte de verdade:
 * mutação em `webapp/src/**` que o runner não desfaça (Ctrl-C, SIGKILL) deixa a
 * árvore suja com cara de trabalho commitável.
 *
 * Rodar:  npm run test:frontend
 *         (ou só este: node --test tests/frontend/precos_bundle_stamp.test.mjs)
 */
import nodeTest from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { conferir } from "../../scripts/precos_bundle_stamp.mjs";

const RAIZ = fileURLToPath(new URL("../..", import.meta.url));

/**
 * Escreve `corpo` em `relativo`, roda `f`, e DESFAZ — repondo o conteúdo
 * anterior se o arquivo já existia, apagando se não existia. O `finally` não é
 * opcional, e a primeira versão deste helper apagava o `precos-app.css` de
 * verdade: `rmSync` cego serve para o arquivo que a sonda criou, não para o que
 * ela sobrescreveu.
 */
function comArquivo(relativo, corpo, f) {
  const caminho = join(RAIZ, relativo);
  const antes = existsSync(caminho) ? readFileSync(caminho) : null;
  writeFileSync(caminho, corpo);
  try {
    return f();
  } finally {
    if (antes === null) rmSync(caminho, { force: true });
    else writeFileSync(caminho, antes);
  }
}

// CONTROLE POSITIVO: na árvore commitada o carimbo bate. Sem este caso, uma
// versão do script que reprovasse SEMPRE passaria nos dois negativos abaixo.
nodeTest("o carimbo commitado bate com webapp/ e com frontend/precos-app.*", () => {
  assert.equal(conferir(), null,
    "o carimbo está desatualizado nesta árvore: rode `npm --prefix webapp ci &&"
    + " npm --prefix webapp run build` e commite o resultado");
});

// CONTROLE NEGATIVO 1 — fonte que mudou sem build. É O bug que o gate existe
// para pegar: editei o `.jsx`, esqueci de buildar, produção continua servindo o
// bundle anterior. A sonda é um arquivo NOVO dentro de `webapp/`, que é o mesmo
// universo hasheado que o `src/` — sem precisar tocar em nenhuma fonte real.
nodeTest("fonte nova em webapp/ sem rebuild deixa o gate VERMELHO", () => {
  const erro = comArquivo("webapp/src/sonda-do-carimbo.js", "// sonda\n", conferir);
  assert.ok(erro, "o gate aprovou webapp/ modificado sem build — não mede nada");
  assert.match(erro, /fontes/, erro);
  assert.equal(conferir(), null, "a sonda não foi desfeita");
});

// CONTROLE NEGATIVO 2 — o artefato mexido por fora do build. Metade que o gate
// antigo (comparação de bytes) pegava, e que esta versão tinha de manter.
nodeTest("artefato editado à mão deixa o gate VERMELHO", () => {
  const erro = comArquivo("frontend/precos-app.css", "/* sonda */\n", conferir);
  assert.ok(erro, "o gate aprovou um artefato que não saiu do build");
  assert.match(erro, /precos-app\.css/, erro);
  assert.equal(conferir(), null, "a sonda não foi desfeita");
});

// CONTROLE NEGATIVO 3 — achado 12: `assetFileNames: "precos-app.[ext]"` nomeia
// TODO asset assim, e asset em `frontend/` sem rota escrita à mão em
// `static_pages.py` é 404 no navegador com o CI verde (§5). Hoje o build só
// emite `.js` e `.css`; uma fonte ou imagem amanhã sai em `precos-app.woff2`.
nodeTest("asset fora da dupla js/css reprova em vez de passar calado", () => {
  const erro = comArquivo("frontend/precos-app.woff2", "x", () => {
    try {
      return conferir();
    } catch (e) {
      return e.message;
    }
  });
  assert.match(erro ?? "", /fora da convenção.*precos-app\.woff2/s, String(erro));
  assert.equal(conferir(), null, "a sonda não foi desfeita");
});
