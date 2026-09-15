---
description: Audita, cria e refina interfaces do PigBank (atalho para a skill pigbank-frontend)
---

Tarefa recebida do usuário: $ARGUMENTS

Invoque `Skill(skill="pigbank-frontend")` passando a tarefa acima como argumento,
e siga o que a skill mandar — inclusive a seção "Verificação", que exige abrir a
página no navegador em desktop **e** mobile e relatar como você conferiu.

Se a tarefa for exclusivamente de backend, a própria skill manda não usá-la:
nesse caso registre no relato que ela não se aplicava, em vez de omitir (§7).

---

Este arquivo é só o atalho de barra, e de propósito não repete nada do conteúdo.
A identidade visual, as regras de implementação e a lista de verificação moram em
`.claude/skills/pigbank-frontend/SKILL.md` — **é lá que se edita**. Duas cópias da
mesma regra em lugares diferentes é o que o `CLAUDE.md` §0.7 proíbe, e foi assim
que o §3 e o §6 do próprio `CLAUDE.md` divergiram das skills uma vez.

Por que existir: skills em `.claude/skills/` não viram comando de barra sozinhas —
quem as invoca é o Claude. Sem este arquivo, `/pigbank-frontend` responde
"Unknown command", que foi o que aconteceu em 2026-09-15.
