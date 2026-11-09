import Constants from "expo-constants";
import { NativeModules } from "react-native";

/** Report only the native development launcher's observed manifest and JS URL. */
export function observeMetroSession() {
  if (!__DEV__ || Constants.expoConfig?.extra?.APP_ENV !== "staging") return;
  const scriptUrl: unknown = NativeModules.SourceCode?.getConstants?.().scriptURL
    ?? NativeModules.SourceCode?.scriptURL;
  const manifest = Constants.manifest2;
  if (typeof scriptUrl !== "string" || !manifest?.id) return;
  const origin = new URL(scriptUrl).origin;
  const runtime = Constants.expoConfig?.extra?.clientRuntime;
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout>;
  const report = async () => {
    try {
      const response = await fetch(`${origin}/_task/challenge`);
      const challenge: unknown = await response.json();
      if (!cancelled && response.ok && typeof challenge === "object" && challenge !== null
          && "nonce" in challenge && typeof challenge.nonce === "string") {
        const result = await fetch(`${origin}/_task/loaded`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nonce: challenge.nonce, manifest_id: manifest.id,
            script_url: scriptUrl, build_commit: runtime?.buildCommit, api_url: runtime?.apiUrl }),
        });
        if (result.ok) return;
      }
    } catch { /* Diagnostic transport must never block app startup. */ }
    if (!cancelled) timer = setTimeout(report, 1000);
  };
  void report();
  return () => { cancelled = true; clearTimeout(timer); };
}
