import type { QueryClient } from "@tanstack/react-query";
import { api, type ModelList, type Provider } from "./api";

export async function refreshModels(client: QueryClient, provider: Provider) {
  const queryKey = ["models", provider];
  await client.cancelQueries({ queryKey, exact: true });
  const response = await api.models(false, true, provider);
  await client.cancelQueries({ queryKey, exact: true });
  client.setQueryData<ModelList>(queryKey, (previous) =>
    response.note && previous?.models.length
      ? { ...previous, note: response.note }
      : response,
  );
}
