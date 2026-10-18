/**
 * Apoio dos testes do Perfil (`perfil_*.test.tsx`). Não termina em `.test.tsx`, então o Jest não o roda como suíte.
 */
import { Linking } from "react-native";
import { act, fireEvent, renderRouter, screen } from "expo-router/testing-library";

import { guardarCredenciais } from "@/storage/secure";

import { chamadas, resposta, rotear, S, type Rota } from "./auth_apoio";
import { drenar } from "./seguranca_sem_senha_apoio";

export const LINK = "https://api.whatsapp.com/send?phone=5511999990000&text=vincular%20ABC123";
export const CONTATO = {
  ok: true,
  user_id: 1,
  email: "sofia@x.com",
  display_name: "Sofia Lima",
  phone: "+5511987654321",
  created_at: "2026-03-04T15:00:00Z",
  whatsapp_verified_at: null,
  identities: [{ provider: "email", external_id: "sofia@x.com" }],
};
export const comWhatsApp = { identities: [{ provider: "whatsapp", external_id: "+5511900000000" }] };

export const abrirPerfil = (opcoes: { me?: object; contato?: object; link?: Rota } = {}, extra: Record<string, Rota> = {}, url = "/perfil") => {
  rotear({
    "/auth/me": () => resposta(200, { user_id: 1, email: "sofia@x.com", display_name: "Sofia", app_access: true, ...opcoes.me }),
    "/settings/1/security": () => resposta(200, { ...CONTATO, ...opcoes.contato }),
    "/auth/link-code": opcoes.link ?? (() => resposta(200, { whatsapp_link: LINK })),
    "/auth/forgot-password": () => resposta(200, { message: "x" }),
    ...extra,
  });
  return guardarCredenciais(S).then(() => renderRouter("./app", { initialUrl: url }));
};

export const tocar = (rotulo: string) =>
  act(async () => {
    fireEvent.press(screen.getByRole("button", { name: rotulo }));
    await drenar();
  });
export const postsDoLink = () => chamadas().filter((c) => c.caminho === "/auth/link-code");
export const abrirUrl = () => jest.mocked(Linking.openURL);
