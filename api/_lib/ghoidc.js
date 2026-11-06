// Verifies GitHub Actions OIDC tokens (RS256, keys from GitHub's JWKS) without a library.
import { createPublicKey, verify } from "node:crypto";

export const ISSUER = "https://token.actions.githubusercontent.com";
const JWKS_URL = `${ISSUER}/.well-known/jwks`;
let jwks = null;
let jwksAt = 0;

const b64 = (s) => Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");

async function keys(fetchImpl, force = false) {
  if (!force && jwks && Date.now() - jwksAt < 3600_000) return jwks;
  const r = await fetchImpl(JWKS_URL, { signal: AbortSignal.timeout(8000) });
  if (!r.ok) throw new Error(`jwks ${r.status}`);
  jwks = (await r.json()).keys || [];
  jwksAt = Date.now();
  return jwks;
}

export function _setJwks(k) {
  jwks = k;
  jwksAt = k ? Date.now() : 0;
}

/**
 * Returns the token's claims when it is a valid GitHub OIDC token for `audience` that matches
 * every expected claim (e.g. repository, ref); otherwise null.
 */
export async function verifyGithubOidc(token, { audience, expect = {}, fetchImpl = fetch, now = Date.now() } = {}) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) return null;
  let header, claims;
  try {
    header = JSON.parse(b64(parts[0]).toString("utf8"));
    claims = JSON.parse(b64(parts[1]).toString("utf8"));
  } catch {
    return null;
  }
  if (header.alg !== "RS256" || !header.kid) return null;
  let jwk = (await keys(fetchImpl)).find((k) => k.kid === header.kid);
  if (!jwk) jwk = (await keys(fetchImpl, true)).find((k) => k.kid === header.kid);
  if (!jwk) return null;
  const ok = verify("RSA-SHA256", Buffer.from(`${parts[0]}.${parts[1]}`), createPublicKey({ key: jwk, format: "jwk" }), b64(parts[2]));
  if (!ok) return null;
  const sec = now / 1000;
  if (claims.iss !== ISSUER) return null;
  const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!audience || !aud.includes(audience)) return null;
  if (!(claims.exp > sec) || (claims.nbf && claims.nbf > sec + 60)) return null;
  for (const [k, v] of Object.entries(expect)) if (claims[k] !== v) return null;
  return claims;
}
