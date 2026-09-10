/**
 * `npm audit` de um lock, com a diferença que o exit code do npm não conta:
 * distingue "achou vulnerabilidade" de "o audit NÃO RODOU".
 *
 * O npm sai 1 nos DOIS casos. O `npm audit ... || echo "::warning ..."` que
 * este script substituiu tratava os dois igual, então uma queda do registry
 * (indisponível, 403, resposta truncada) deixava o check verde anunciando
 * "vulnerabilidades encontradas" — com o lock nunca escaneado. Silêncio no
 * lugar do escaneamento é pior que a vulnerabilidade, porque ninguém procura.
 *
 * Quem discrimina é o PAYLOAD: `metadata.vulnerabilities.total` só existe num
 * relatório de verdade (`auditReportVersion`). Erro do npm sai como
 * `{"message":...,"error":{...}}`, sem `metadata`; saída truncada nem parseia.
 *
 * Sempre sai 0: a varredura é informativa por decisão do dono — ela sinaliza,
 * não trava o merge.
 *
 *     node scripts/npm_audit_report.mjs .        # lock da raiz
 *     node scripts/npm_audit_report.mjs mobile   # lock do app
 */
import { spawnSync } from "node:child_process";
import { basename, resolve } from "node:path";

const dir = process.argv[2] || ".";
const rotulo = dir === "." ? "raiz" : basename(resolve(dir));

// `--package-lock-only`: lê só o lock, não instala nada. Inclui
// devDependencies (padrão do comando) — no mobile/ a cadeia de build é quem
// assina o binário.
const npm = spawnSync("npm", ["audit", "--package-lock-only", "--json"], {
  cwd: dir,
  encoding: "utf8",
  maxBuffer: 32 * 1024 * 1024,
});

// `npm.status` é ignorado DE PROPÓSITO: vale 1 tanto com vulnerabilidade
// quanto com o audit fora do ar, então não carrega informação nenhuma.
let total = null;
try {
  total = JSON.parse(npm.stdout).metadata.vulnerabilities.total;
} catch {
  // payload de erro, saída truncada, ou npm ausente do PATH — todos "não rodou".
}

if (typeof total !== "number") {
  console.log(
    `::warning title=npm-audit NAO RODOU (${rotulo})::O audit falhou antes de` +
      ` produzir relatorio: o lock NAO foi escaneado (nenhuma conclusao sobre` +
      ` vulnerabilidades pode ser tirada deste run).`,
  );
  // O motivo, para não precisar reproduzir o run só para saber o que caiu.
  //
  // Isto é saída de TERCEIRO: quem escolhe o texto do stderr do npm é o
  // registry. `::error`/`::add-mask` e `##[error]` são COMANDOS do runner, e
  // quem os reconhece é o ActionCommandManager (`!TryParseV2(...) &&
  // !TryParse(...)`, ActionCommandManager.cs:70).
  //
  // Por que o token e não a posição: indentar NÃO protege. O V2 faz
  // `message.TrimStart()` ANTES de testar `StartsWith("::")`
  // (ActionCommand.cs, TryParseV2), então o espaço que se põe na frente ele
  // mesmo tira; e o V1 procura o prefixo `##[` com `IndexOf` — em QUALQUER
  // posição da linha, sem âncora nenhuma. O que sobra é quebrar o token: sem
  // `::` e sem `##[` colados, nenhum dos dois parsers casa, e o texto continua
  // legível no log.
  //
  // Alcançabilidade com o npm real nunca foi provada (ele prefixa o corpo do
  // registry com `npm warn audit `). Fronteira de confiança não se simplifica
  // por falta de exploit (CLAUDE.md §0.2).
  const motivo = (npm.stderr || npm.error?.message || "")
    .trim()
    .slice(0, 500)
    .replaceAll("::", ": :")
    .replaceAll("##[", "## [");
  if (motivo) console.log(`npm audit (${rotulo}) falhou: ${motivo}`);
} else if (total > 0) {
  console.log(
    `::warning title=npm-audit (${rotulo})::Vulnerabilidades conhecidas` +
      ` encontradas (informativo, nao bloqueia; o Dependabot abre os PRs de` +
      ` correcao).`,
  );
} else {
  console.log(`npm audit (${rotulo}): 0 vulnerabilidades conhecidas no lock.`);
}
