/**
 * O carimbo do artefato da ilha React: prova que `frontend/precos-app.*` é o
 * build de `webapp/` de hoje, SEM rebuildar para conferir.
 *
 * O gate que isto substitui rodava `npm --prefix webapp ci && npm run build` no
 * CI e reprovava se o `git status` sujasse os dois arquivos. Ele media a coisa
 * certa pelo meio errado: o Vite 8 buda com **rolldown** e **lightningcss**, os
 * dois binários NATIVOS (27 pacotes com `os`/`cpu` no lockfile de webapp/). O
 * artefato commitado sai de `darwin-arm64` e o CI roda `linux-x64-gnu` — e
 * nenhum dos dois projetos promete saída byte a byte igual entre arquiteturas.
 * Como o step não tinha `if: paths:`, um único byte de diferença deixava
 * VERMELHO todo PR do repositório, inclusive os que não tocam `webapp/`.
 *
 * O objetivo real é um só: pegar quem editou `webapp/` e esqueceu de buildar.
 * Para isso não é preciso buildar duas vezes, basta comparar HASHES do que está
 * commitado:
 *
 *   fontes    = hash de todo arquivo de `webapp/` (menos node_modules e este
 *               carimbo). Inclui `vite.config.js`, `package.json` e o lockfile:
 *               os três mudam a saída sem tocar em `src/`.
 *   artefatos = hash de cada `frontend/precos-app.*` commitado.
 *
 * Quem ESCREVE o carimbo é o `postbuild` do webapp (roda depois do `vite
 * build`), então atualizá-lo sem buildar não é um caminho que exista: o dev que
 * edita o `.jsx` e não builda deixa `fontes` divergente. Quem edita o bundle
 * minificado à mão deixa o hash do artefato divergente — a metade que o gate
 * anterior pegava, e que este mantém.
 *
 * Zero dependência de plataforma: nenhum byte de build é comparado com nenhum
 * outro byte de build. Só `node:crypto` e `node:fs`.
 *
 *     node scripts/precos_bundle_stamp.mjs --write   # o postbuild do webapp/
 *     node scripts/precos_bundle_stamp.mjs --check   # CI e teste
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const RAIZ = resolve(fileURLToPath(new URL("..", import.meta.url)));
const CARIMBO = join(RAIZ, "webapp", "build-stamp.txt");

/**
 * Os DOIS artefatos que a convenção admite, por nome. A lista é literal de
 * propósito: `assetFileNames: "precos-app.[ext]"` nomeia TODO asset assim, então
 * uma fonte ou imagem que o build passe a emitir amanhã sairia em
 * `frontend/precos-app.woff2` — e asset em `frontend/` sem rota escrita à mão em
 * `static_pages.py` é 404 no navegador (§5). Nome fora da lista REPROVA, no
 * `--write` (na hora, para quem buildou) e no `--check`.
 */
const ARTEFATOS = ["frontend/precos-app.js", "frontend/precos-app.css"];

const sha = (buf) => createHash("sha256").update(buf).digest("hex");

/** Todo arquivo de `webapp/`, ordenado, menos node_modules e o próprio carimbo. */
function fontes(dir = join(RAIZ, "webapp")) {
  const achados = [];
  for (const nome of readdirSync(dir).sort()) {
    if (nome === "node_modules") continue;
    const caminho = join(dir, nome);
    if (statSync(caminho).isDirectory()) achados.push(...fontes(caminho));
    else if (caminho !== CARIMBO) achados.push(caminho);
  }
  return achados;
}

function carimboEsperado() {
  const listagem = fontes()
    .map((f) => `${relative(RAIZ, f).split("\\").join("/")} ${sha(readFileSync(f))}\n`)
    .join("");
  const emitidos = readdirSync(join(RAIZ, "frontend"))
    .filter((n) => n.startsWith("precos-app."))
    .map((n) => `frontend/${n}`)
    .sort();
  const extras = emitidos.filter((n) => !ARTEFATOS.includes(n));
  if (extras.length) {
    throw new Error(`asset fora da convenção em frontend/: ${extras.join(", ")}.`
      + " O build emitiu um artefato novo; ele precisa de rota própria em"
      + " frontend/routes/static_pages.py e de entrar na lista ARTEFATOS deste"
      + " script — sem rota, 404 no navegador com o CI verde (§5).");
  }
  return [
    "# Carimbo do build da ilha React da /precos. NÃO edite à mão.",
    "# Escrito pelo `postbuild` de webapp/ (roda junto com `npm --prefix webapp run build`)",
    "# e conferido por `node scripts/precos_bundle_stamp.mjs --check`.",
    `fontes ${sha(listagem)}`,
    ...ARTEFATOS.map((a) => `${a} ${sha(readFileSync(join(RAIZ, a)))}`),
    "",
  ].join("\n");
}

export function conferir() {
  const esperado = carimboEsperado();
  let atual = "";
  try {
    atual = readFileSync(CARIMBO, "utf8");
  } catch {
    return "webapp/build-stamp.txt não existe. Rode `npm --prefix webapp ci &&"
      + " npm --prefix webapp run build` e commite o resultado.";
  }
  if (atual === esperado) return null;
  const linha = (txt, chave) => (txt.split("\n").find((l) => l.startsWith(chave + " ")) || "").trim();
  const divergentes = ["fontes", ...ARTEFATOS]
    .filter((c) => linha(atual, c) !== linha(esperado, c));
  return `webapp/build-stamp.txt não corresponde ao que está commitado`
    + ` (divergem: ${divergentes.join(", ") || "o cabeçalho"}).`
    + (divergentes.includes("fontes")
      ? " `fontes` divergente = webapp/ mudou e o build NÃO foi rodado: rode"
        + " `npm --prefix webapp ci && npm --prefix webapp run build` e commite"
        + " frontend/precos-app.* junto com o carimbo."
      : " O artefato commitado foi alterado por fora do build.");
}

// Só age quando é ELE o programa; o teste importa o `conferir` e não quer CLI.
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv[2] === "--write") {
    writeFileSync(CARIMBO, carimboEsperado());
  } else if (process.argv[2] === "--check") {
    // `try` porque o `carimboEsperado` ATIRA no asset fora da convenção, e um
    // stack trace de node não é diagnóstico no log do CI.
    let erro;
    try {
      erro = conferir();
    } catch (e) {
      erro = e.message;
    }
    if (erro) {
      console.error(`::error file=webapp/build-stamp.txt,title=Artefato da ilha React desatualizado::${erro}`);
      process.exit(1);
    }
    console.log("carimbo do frontend/precos-app.* em dia com webapp/");
  } else {
    console.error("uso: node scripts/precos_bundle_stamp.mjs --write|--check");
    process.exit(2);
  }
}
