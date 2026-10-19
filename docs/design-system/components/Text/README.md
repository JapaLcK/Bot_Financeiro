Texto com um dos seis estilos de tipo e um tom de cor, de `app/src/ui/componentes/Texto.tsx`.

**Quem fornece:** `children`, `variant` (`display`, `title`, `section`, `body`, `label`, `caption`; padrão `body`), `tone` (nome de um token de cor; padrão `ink`), `numeric` para algarismos tabulares e `as` para a tag.

Tamanho e peso vêm **só** do estilo: não sobrescreva `font-size` nem `font-weight` por fora. Número que muda ou se compara leva `numeric`. Texto rosa usa `tone="brand-ink"`, nunca `brand`.
