// O aviso de CVE tem de saber a diferença entre "achou" e "nem rodou".
//
// `npm audit` sai 1 nos dois casos, então o `npm audit || echo "::warning ..."`
// que existia aqui deixava o check verde anunciando "vulnerabilidades
// encontradas" quando o registry caía — com o lock NUNCA escaneado.
//
// Sem rede e sem registry: um `npm` FALSO entra na frente do PATH, imprime o
// payload de cada cenário e REGISTRA o que recebeu (`argv` e `pwd`). O registro
// não é enfeite: sem ele, tirar `--json`, `--package-lock-only` ou o `cwd` do
// spawn deixava o grupo 5/5 verde — e sem o `cwd` o mobile passa a reportar
// "0 vulnerabilidades" com o lock dele nunca lido, que é falso limpo, pior que
// o falso alarme que este conserto existe para matar.
//
// Rodar:  npm run test:frontend
//         (ou só este: node --test tests/frontend/npm_audit_report.test.mjs)
import nodeTest from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const RAIZ = fileURLToPath(new URL("../..", import.meta.url));
const SCRIPT = join(RAIZ, "scripts/npm_audit_report.mjs");

const TITULO_VULN = (rotulo) => `title=npm-audit (${rotulo})::`;
const TITULO_NAO_RODOU = "title=npm-audit NAO RODOU (raiz)::";

/**
 * Roda o script com um `npm` falso que imprime `payload`, sai com `codigo`, e
 * anota em disco o `argv` e o `pwd` com que foi chamado.
 */
function comNpmFalso(payload, codigo, { dir = ".", stderr = "" } = {}) {
  const bin = mkdtempSync(join(tmpdir(), "npm-falso-"));
  try {
    const registro = join(bin, "recebido.txt");
    const falso = join(bin, "npm");
    writeFileSync(
      falso,
      [
        "#!/bin/sh",
        `{ echo "argv: $@"; echo "pwd: $(pwd)"; } > '${registro}'`,
        `cat >&2 <<'FIM_ERR'\n${stderr}\nFIM_ERR`,
        `cat <<'FIM_OUT'\n${payload}\nFIM_OUT`,
        `exit ${codigo}`,
      ].join("\n"),
    );
    chmodSync(falso, 0o755);
    const r = spawnSync(process.execPath, [SCRIPT, dir], {
      cwd: RAIZ,
      encoding: "utf8",
      env: { ...process.env, PATH: `${bin}:${process.env.PATH}` },
    });
    return { ...r, recebido: readFileSync(registro, "utf8") };
  } finally {
    // Sem isto, quatro tmpdirs vazados por rodada da suíte.
    rmSync(bin, { recursive: true, force: true });
  }
}

const RELATORIO = (total) =>
  JSON.stringify({
    auditReportVersion: 2,
    metadata: { vulnerabilities: { moderate: 3, high: 1, total }, dependencies: { total: 184 } },
  });

nodeTest("achou vulnerabilidade: avisa, e não é o aviso de 'não rodou'", () => {
  const r = comNpmFalso(RELATORIO(4), 1);
  assert.equal(r.status, 0, "o step é informativo: o script nunca pode reprovar o job");
  assert.ok(r.stdout.includes(TITULO_VULN("raiz")), r.stdout);
  assert.ok(!r.stdout.includes(TITULO_NAO_RODOU), r.stdout);
});

nodeTest("audit não rodou (erro do registry): avisa que o lock NÃO foi escaneado", () => {
  const r = comNpmFalso(
    JSON.stringify({ message: "connect ECONNREFUSED 127.0.0.1:1", error: { code: "ECONNREFUSED" } }),
    1,
  );
  assert.equal(r.status, 0);
  assert.ok(r.stdout.includes(TITULO_NAO_RODOU), r.stdout);
  assert.ok(/lock NAO foi escaneado/.test(r.stdout), r.stdout);
  assert.ok(!r.stdout.includes(TITULO_VULN("raiz")), r.stdout);
});

nodeTest("payload truncado no meio também é 'não rodou', não 'zero vulnerabilidades'", () => {
  const r = comNpmFalso('{"auditReportVersion":2,"metad', 1);
  assert.equal(r.status, 0);
  assert.ok(r.stdout.includes(TITULO_NAO_RODOU), r.stdout);
  assert.ok(!r.stdout.includes(TITULO_VULN("raiz")), r.stdout);
});

// Relatório com forma de relatório e SEM o número: afrouxar a guarda para
// `total === null` faz isto virar "0 vulnerabilidades" — falso limpo.
nodeTest("relatório sem o total é 'não rodou', nunca 'lock limpo'", () => {
  for (const payload of [
    '{"auditReportVersion":2,"metadata":{"vulnerabilities":{}}}',
    '{"auditReportVersion":2,"metadata":{"vulnerabilities":{"total":"4"}}}',
  ]) {
    const r = comNpmFalso(payload, 1);
    assert.equal(r.status, 0);
    assert.ok(r.stdout.includes(TITULO_NAO_RODOU), `${payload}\n${r.stdout}`);
    assert.ok(!/0 vulnerabilidades/.test(r.stdout), `${payload}\n${r.stdout}`);
  }
});

// O `npm.status` é ignorado DE PROPÓSITO, e essa decisão só existia em
// comentário: sem este caso, voltar a olhar o exit code (`&& npm.status !== 0`
// na guarda) deixava o grupo inteiro verde e transformava erro do registry em
// "0 vulnerabilidades" — o falso limpo que este conserto existe para matar.
nodeTest("exit 0 com payload ruim ainda é 'não rodou' — o exit code não decide", () => {
  for (const payload of ['{"message":"boom"}', '{"auditReportVersion":2,"metad']) {
    const r = comNpmFalso(payload, 0);
    assert.equal(r.status, 0);
    assert.ok(r.stdout.includes(TITULO_NAO_RODOU), `${payload}\n${r.stdout}`);
    assert.ok(!/0 vulnerabilidades/.test(r.stdout), `${payload}\n${r.stdout}`);
  }
});

// Controle positivo: sem ele o grupo passaria num script que grita em tudo.
nodeTest("lock limpo: nenhum ::warning", () => {
  const r = comNpmFalso(RELATORIO(0), 0);
  assert.equal(r.status, 0);
  assert.ok(!r.stdout.includes("::warning"), r.stdout);
  assert.match(r.stdout, /0 vulnerabilidades/);
});

// O que o npm RECEBE. Sem estas asserções, tirar `--json`, tirar
// `--package-lock-only` ou tirar o `cwd` não acende uma linha vermelha.
nodeTest("o npm é chamado com --json e --package-lock-only", () => {
  const { recebido } = comNpmFalso(RELATORIO(0), 0);
  const argv = recebido.split("\n")[0];
  assert.match(argv, /(^|\s)audit(\s|$)/, argv);
  assert.match(argv, /--json/, argv);
  assert.match(argv, /--package-lock-only/, argv);
  // `--omit=dev` zera o relatório do mobile: o comentário do script promete
  // devDependencies ("a cadeia de build é quem assina o binário") e sem esta
  // linha nada mede a promessa. Medido em 2026-09-09 com
  // `npm audit --package-lock-only [--omit=dev] --json` em `mobile/`: sem a
  // flag o `metadata.vulnerabilities.total` era 4, com ela 0. Não confie no 4:
  // o advisory database é vivo e o número já oscilou entre execuções deste
  // mesmo ciclo — remeça com o comando acima. O que não oscila é o ZERO.
  assert.doesNotMatch(argv, /--omit|--production/, argv);
});

// O diretório do argumento é onde o npm RODA — é o que decide qual lock é lido.
// Provado com os dois diretórios que o CI usa: sem o `cwd`, o step do mobile
// leria o lock da raiz e reportaria sobre o lock errado.
nodeTest("o argumento vira o diretório do npm — e o rótulo do aviso", () => {
  const naRaiz = comNpmFalso(RELATORIO(0), 0, { dir: "." });
  assert.equal(naRaiz.recebido.split("\n")[1], `pwd: ${RAIZ.replace(/\/$/, "")}`, naRaiz.recebido);

  const noMobile = comNpmFalso(RELATORIO(4), 1, { dir: "mobile" });
  assert.equal(
    noMobile.recebido.split("\n")[1],
    `pwd: ${join(RAIZ, "mobile")}`,
    noMobile.recebido,
  );
  assert.ok(noMobile.stdout.includes(TITULO_VULN("mobile")), noMobile.stdout);
  assert.ok(!noMobile.stdout.includes(TITULO_VULN("raiz")), noMobile.stdout);
});

// O stderr do npm carrega texto que o REGISTRY escolheu, e o runner trata duas
// formas como COMANDO. O oráculo aqui é o do runner, não "começa com `::`":
//
//   V2 (ActionCommand.cs, TryParseV2): `message.TrimStart()` e SÓ ENTÃO
//       `StartsWith("::")` — indentar não protege, o runner tira o espaço.
//   V1 (mesmo arquivo): `message.IndexOf("##[")` — em qualquer posição da linha.
//
// Por isso a asserção é sobre a linha TRIMADA (é o que o runner enxerga) e
// cobre as duas formas.
//
// O trim aqui NÃO é `trimStart()`: o `Char.IsWhiteSpace` do .NET inclui
// U+0085 (NEL) e o `\s` do JS não (`/\s/.test("\u0085")` é `false`), então
// `trimStart()` diria "seguro" para uma linha que o runner executaria. O
// `[\s\u0085]` cobre a classe do .NET com folga (o JS ainda tem U+FEFF a
// mais, o que só reprova de sobra, nunca de menos).
const NOSSO_AVISO = "::warning title=npm-audit ";
const trimComoRunner = (l) => l.replace(/^[\s\u0085]+/, "");
function comandosVazados(stdout) {
  return stdout
    .split("\n")
    .map(trimComoRunner)
    .filter((l) => !l.startsWith(NOSSO_AVISO))
    .filter((l) => l.startsWith("::") || l.includes("##["));
}

nodeTest("stderr do npm não vira comando do Actions", () => {
  const PAYLOADS = [
    ["::error title=A::x", "title=A"],
    ["  ::error title=B::x", "title=B"],
    ["\r::error title=C::x", "title=C"],
    ["\t::add-mask::segredo", "segredo"],
    ["##[error]D", "error]D"],
    ["npm error 403 blah ##[error]E no meio", "error]E"],
    // DUAS injeções na mesma saída: com `.replace` no lugar de `.replaceAll`
    // só a primeira é desarmada e a segunda vaza (`::endgroup::` sozinho já
    // fecha o grupo do log; `::stop-commands::` desliga o parser inteiro).
    ["::stop-commands::abc\n::endgroup::\n##[error]F\n##[warning]G", "endgroup"],
    // U+0085 (NEL) é espaço para o `TrimStart()` do .NET e NÃO é para o
    // `trimStart()` do JS: com o oráculo ingênuo esta linha passava por segura.
    ["\u0085::error title=NEL::x", "title=NEL"],
  ];
  for (const [injecao, pedaco] of PAYLOADS) {
    // A linha comum na frente: sem ela o `.trim()` do script comeria o \r/\t
    // da injeção e o caso 3/4 mediria outra coisa.
    const r = comNpmFalso('{"message":"boom"}', 1, {
      stderr: `npm error 403 Forbidden\n${injecao}`,
    });
    assert.equal(r.status, 0);
    assert.deepEqual(comandosVazados(r.stdout), [], `${JSON.stringify(injecao)}\n${r.stdout}`);
    assert.ok(r.stdout.includes(pedaco), `${JSON.stringify(injecao)}: o texto sumiu do log em vez de ser desarmado`);
  }
});

// ── Fiação ────────────────────────────────────────────────────────────────
// Cinto, não a prova: os testes acima medem o script; este garante que é ELE
// que o CI chama. Parse de verdade, não `includes`: com busca por substring, os
// dois steps podiam ser APAGADOS e o mesmo texto deixado num comentário do YAML
// que o teste continuava verde — e o CI parava de auditar npm.
//
// ponytail: js-yaml é declarada em devDependencies porque só o eslint a trazia
// (eslint > @eslint/eslintrc > js-yaml) e o bump do eslint 10 (PR #344) faz o
// `@eslint/eslintrc` sumir inteiro do lock — medido em 2026-09-10. Não usei
// `yaml.safe_load` do Python: PyYAML NÃO é premissa paga aqui (ausente do
// requirements.txt, zero imports no repo, `Required-by:` vazio) — o teste
// ficaria vermelho no CI.
let YAML;
let semYaml = false;
try {
  const mod = await import("js-yaml");
  // A 4.x exporta `default`, a 5.x só nomeados. Sem o `?? mod` o `.default` vinha
  // `undefined` com a 5.x, o catch nada via (o import SUCEDE) e o teste pulava em
  // silêncio no CI — medido em 2026-09-10 com js-yaml 5.4.1.
  YAML = mod.default ?? mod;
  // O import suceder não prova a forma: sem esta checagem, uma 6.x que renomeie
  // `load` cairia num TypeError longe daqui em vez de reprovar no CI (ou pular local).
  if (typeof YAML.load !== "function") {
    throw new Error(`js-yaml sem \`.load\` (exporta: ${Object.keys(mod).join(", ")})`);
  }
} catch (erro) {
  if (process.env.CI) throw erro;
  semYaml = `js-yaml indisponível (${erro.message}): rode \`npm ci\` na raiz (no CI isto REPROVA, não pula)`;
}

/**
 * Os locks são LEVANTADOS DO DISCO, não escritos aqui.
 *
 * A versão anterior comparava com a lista literal `[". ", "mobile"]`, e por isso
 * o terceiro lock do repositório (`webapp/`, a ilha React da /precos) entrou sem
 * uma linha vermelha: 45 pacotes novos na cadeia que produz um arquivo servido
 * na página que vende, fora do `npm audit` e fora do Dependabot. Lista literal
 * mede o passado; `readdirSync` mede o repositório de hoje — o quarto lock
 * reprova até ser auditado.
 */
const LOCKS = ["."].concat(readdirSync(RAIZ, { withFileTypes: true })
  .filter((d) => d.isDirectory() && d.name !== "node_modules" && !d.name.startsWith(".")
    && existsSync(join(RAIZ, d.name, "package-lock.json")))
  .map((d) => d.name)
  .sort());

nodeTest("o workflow chama o script em TODO lock do repo, sem `|| echo`", { skip: semYaml }, () => {
  const doc = YAML.load(readFileSync(join(RAIZ, ".github/workflows/tests.yml"), "utf8"));
  assert.ok(doc.jobs.audit, "o job `audit` sumiu (ou foi renomeado) no workflow");
  // Step/job com `if:` existe e não roda — o audit sairia do ar sem sumir do YAML.
  assert.equal(doc.jobs.audit.if, undefined, "o job `audit` ficou condicional");
  const steps = doc.jobs.audit.steps;

  // Só os steps que chamam ESTE script. O `pip-audit` fica de fora de propósito
  // (mantém o `|| echo`, fora do escopo), e um `npm audit fix || echo ...` que
  // alguém adicione amanhã também não é problema deste teste.
  const meus = steps.filter((s) => (s.run || "").includes("npm_audit_report.mjs"));
  assert.deepEqual(
    meus.map((s) => s.run.trim()).sort(),
    LOCKS.map((d) => `node scripts/npm_audit_report.mjs ${d}`).sort(),
    `locks no disco: ${LOCKS.join(", ")}`,
  );
  for (const s of meus) {
    assert.ok(!s.run.includes("|| echo"), `${s.name}: o \`|| echo\` voltou`);
    // O diretório é argumento; `working-directory` fixaria o step num lock só.
    assert.equal(s["working-directory"], undefined, s.name);
    assert.equal(s.if, undefined, `${s.name}: step condicional não audita nada`);
  }
});

/**
 * O par do teste acima: escanear é achar a CVE, o Dependabot é quem a conserta.
 * Um lock auditado e sem Dependabot fica avisando para sempre sem PR nenhum —
 * foi o estado do `webapp/` no dia em que ele entrou.
 */
nodeTest("todo lock do repo tem um ecossistema npm no Dependabot", { skip: semYaml }, () => {
  const doc = YAML.load(readFileSync(join(RAIZ, ".github/dependabot.yml"), "utf8"));
  const npm = doc.updates.filter((u) => u["package-ecosystem"] === "npm")
    .map((u) => u.directory.replace(/^\/(.*)$/, "$1") || ".");
  assert.deepEqual(npm.sort(), [...LOCKS].sort(), `locks no disco: ${LOCKS.join(", ")}`);
});
