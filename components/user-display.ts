export interface PromDataUserLike {
  email?: string | null;
  user_metadata?: Record<string, unknown> | null;
  app_metadata?: Record<string, unknown> | null;
}

/**
 * Resuelve el nombre para mostrar del usuario siguiendo la cascada
 * estándar de PromData: full_name > name > preferred_username > email.
 */
export function resolveDisplayName(
  user: PromDataUserLike | null | undefined
): string {
  if (!user) return "Mi cuenta";

  const candidates = [
    user.user_metadata?.full_name,
    user.user_metadata?.name,
    user.user_metadata?.preferred_username,
    user.email?.split("@")[0],
  ];

  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }

  return "Mi cuenta";
}

/**
 * Devuelve hasta dos iniciales en mayúscula a partir de un nombre.
 */
export function resolveInitials(name: string): string {
  const source = name.trim() || "U";
  const parts = source.split(/\s+/).filter(Boolean).slice(0, 2);
  return parts.map((part) => part.charAt(0).toUpperCase()).join("") || "U";
}
