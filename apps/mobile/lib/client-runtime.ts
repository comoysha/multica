import Constants from "expo-constants";
import { z } from "zod";

const ClientRuntimeSchema = z.object({
  apiUrl: z.url().refine((value) => /^https?:\/\//.test(value)),
  buildCommit: z.string(),
});

export const clientRuntime = ClientRuntimeSchema.parse(
  Constants.expoConfig?.extra?.clientRuntime,
);

export const API_URL = clientRuntime.apiUrl;
