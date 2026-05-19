/**
 * Arrow Transport Parser v1.0
 * 
 * Decodifica payloads Arrow IPC (base64) enviados por el backend
 * y los convierte a arrays de objetos JSON que nuestros componentes
 * (SmartTable, AnalysisReport, ECharts) ya entienden.
 * 
 * Pipeline: base64 string → Uint8Array → Arrow Table → Record<string, unknown>[]
 */

import { tableFromIPC } from 'apache-arrow';

const ARROW_PARSE_CACHE_LIMIT = 8;
const arrowParseCache = new Map<string, Record<string, unknown>[]>();

function buildArrowParseFingerprint(base64String: string): string {
  return `${base64String.length}:${base64String.slice(0, 80)}:${base64String.slice(-80)}`;
}

function rememberParsedArrow(
  fingerprint: string,
  rows: Record<string, unknown>[]
): Record<string, unknown>[] {
  if (arrowParseCache.has(fingerprint)) {
    arrowParseCache.delete(fingerprint);
  }

  arrowParseCache.set(fingerprint, rows);

  if (arrowParseCache.size > ARROW_PARSE_CACHE_LIMIT) {
    const oldestKey = arrowParseCache.keys().next().value;
    if (oldestKey) {
      arrowParseCache.delete(oldestKey);
    }
  }

  return rows;
}

/**
 * Convierte un string base64 de Arrow IPC Stream a un array de objetos JSON.
 * 
 * @param base64String - String base64 del Arrow IPC stream (generado por arrow_utils.py)
 * @returns Array de objetos (cada objeto = una fila), compatible con SmartTable y AnalysisReport
 * @throws Error si la decodificación o el parsing fallan
 */
export function parseArrowBase64(base64String: string): Record<string, unknown>[] {
  const fingerprint = buildArrowParseFingerprint(base64String);
  const cached = arrowParseCache.get(fingerprint);
  if (cached) {
    arrowParseCache.delete(fingerprint);
    arrowParseCache.set(fingerprint, cached);
    return cached;
  }

  // 1. Base64 → bytes
  const binaryString = atob(base64String);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  // 2. Bytes → Arrow Table
  const table = tableFromIPC(bytes);

  // 3. Arrow Table → array of plain objects
  const rows: Record<string, unknown>[] = [];
  for (let i = 0; i < table.numRows; i++) {
    const row = table.get(i);
    if (row) {
      // Arrow devuelve un StructRow proxy — lo convertimos a plain object
      rows.push(Object.fromEntries(
        Object.entries(row.toJSON ? row.toJSON() : row)
      ));
    }
  }

  return rememberParsedArrow(fingerprint, rows);
}

/**
 * Intenta decodificar `arrow_data` de un item del backend.
 * Si falla, retorna `fallbackData` sin romper la UI.
 * 
 * Uso en chat-interface.tsx:
 *   item.data = tryParseArrow(item.arrow_data, item.data);
 * 
 * @param arrowBase64 - String base64 de Arrow IPC (puede ser undefined)
 * @param fallbackData - Data JSON original como fallback
 * @returns Array de objetos decodificados, o el fallback si Arrow falla
 */
export function tryParseArrow(
  arrowBase64: string | undefined,
  fallbackData: Record<string, unknown>[] | undefined
): Record<string, unknown>[] {
  if (!arrowBase64) {
    return fallbackData ?? [];
  }

  try {
    const parsed = parseArrowBase64(arrowBase64);
    console.log(`🏹 [ARROW] Decodificados ${parsed.length} registros desde Arrow IPC`);
    return parsed;
  } catch (error) {
    console.error('⚠️ [ARROW] Error decodificando Arrow IPC, usando fallback JSON:', error);
    return fallbackData ?? [];
  }
}
