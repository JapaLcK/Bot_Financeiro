import { router } from "expo-router";
/** O portão pode ter sido substituído pelo grupo de abas: não empilha outra Home. */
export function voltarAoInicio() {
  router.dismissAll();
  router.replace("/");
}
