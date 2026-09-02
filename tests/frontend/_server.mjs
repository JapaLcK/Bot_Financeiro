/**
 * O `http.server` que serve `frontend/` para os testes de navegador.
 *
 * Existe um só porque a porta FIXA já comeu o sinal de teste três vezes, sempre
 * com o código certo e um vermelho convincente em área não relacionada: um
 * servidor órfão de OUTRO worktree segurando a porta (15–22 casos vermelhos com
 * ERR_CONNECTION_REFUSED), pytest ocupando-a (4), e de novo (11, todos em
 * `hiw_rail` com "porta 8901 ocupada?"). Cada ocorrência mandou alguém caçar um
 * bug que não existia.
 *
 * A porta agora é EFÊMERA: o `0` faz o kernel escolher uma livre, e o próprio
 * `http.server` anuncia qual na saída. Não há porta para colidir — nem entre os
 * arquivos desta pasta (o `node --test` roda todos em PARALELO), nem com outro
 * worktree, nem com o servidor de preview do `.claude/launch.json`, que usa a
 * 8899 e matava o `settings_security_fanout`.
 *
 * Antes, cada arquivo tinha sua cópia deste helper, sua porta fixa e um
 * comentário listando as portas dos outros — uma lista mantida à mão, em oito
 * lugares, que já tinha divergido: `handlers_inline` e `reset_cache_multiaba`
 * apontavam ambos para a 8911.
 */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");

// No Ubuntu do CI o interpretador é `python3`; no Windows esse nome resolve pro
// stub da Microsoft Store e o servidor morre na hora, com todos os casos
// vermelhos por "porta ocupada" — que é o sintoma errado da causa certa.
const PY = process.env.PB_PYTHON || (process.platform === "win32" ? "python" : "python3");

/**
 * Sobe o servidor e resolve com `{ proc, origin }` — `origin` já com a porta que
 * o kernel deu. Feche com `proc.kill()` no `after`.
 */
export function startServer() {
  return new Promise((resolve, reject) => {
    // `-u` não é enfeite: com stdout num pipe o Python bufferiza por BLOCO, e a
    // linha da porta ficaria presa no buffer até o processo morrer — o helper
    // esperaria para sempre por uma porta que já está servindo.
    const proc = spawn(PY, ["-u", "-m", "http.server", "0", "--bind", "127.0.0.1",
                            "--directory", FRONTEND],
                       { stdio: ["ignore", "pipe", "pipe"] });

    let pronto = false, saida = "";
    const timer = setTimeout(() => falhar("não anunciou a porta em 30s"), 30_000);

    function falhar(motivo) {
      if (pronto) return;
      pronto = true;
      clearTimeout(timer);
      proc.kill();
      reject(new Error(`http.server ${motivo}\n${saida}`));
    }

    // Os dois streams ficam sendo CONSUMIDOS depois do resolve de propósito: o
    // http.server loga toda requisição no stderr, e pipe que ninguém drena
    // enche (~64KB) e trava o servidor no meio da suíte. Só o acúmulo em
    // memória para; a leitura, não.
    const ler = (b) => { if (!pronto) saida += b; };
    proc.stderr.on("data", ler);
    proc.stdout.on("data", (b) => {
      ler(b);
      const porta = /port (\d+)/.exec(saida)?.[1];
      if (pronto || !porta) return;
      pronto = true;
      clearTimeout(timer);
      // A linha já sai depois do bind+listen, então a porta aceita conexão aqui
      // (elas esperam no backlog até o serve_forever). Não precisa de sonda.
      resolve({ proc, origin: `http://127.0.0.1:${porta}` });
    });

    proc.on("error", (e) => falhar(`não executou — "${PY}" ${e.message}`));
    proc.on("exit", (code) => falhar(`morreu com código ${code}`));
  });
}
