import { useEffect, useState } from "react";
import { frasesDe } from "../lib/agentes";

const TROCA_MS = 2000;
// O "enviando" de um agente: um anel que gira e uma frase que troca a cada 2 s. É só enfeite (aria-hidden); quem fala com o leitor
// de tela é o texto "Preparando a resposta…" da própria bolha (PiggyChat), que já é anunciado. Com movimento reduzido, a 1ª frase fica fixa.
export function Carregando({ kind }: { kind: string }) {
  const frases = frasesDe(kind);
  const [i, setI] = useState(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const t = setInterval(() => setI((n) => n + 1), TROCA_MS);
    return () => clearInterval(t);
  }, []);
  return (
    <p className="msg-carga" aria-hidden="true">
      <i className="msg-carga-anel" />
      <span key={i} className="msg-carga-frase">{frases[i % frases.length]}</span>
    </p>
  );
}
