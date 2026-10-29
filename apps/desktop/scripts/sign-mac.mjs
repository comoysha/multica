import { sign } from "app-builder-lib/out/codeSign/macCodeSign.js";

const SHA1_FINGERPRINT = /^[A-F0-9]{40}$/i;

export default function signMac(options) {
  if (
    options.identity &&
    options.identity !== "-" &&
    !SHA1_FINGERPRINT.test(options.identity)
  ) {
    throw new Error(
      "[package] electron-builder did not resolve a signing certificate fingerprint; refusing to sign by ambiguous identity name.",
    );
  }

  return sign(options);
}
