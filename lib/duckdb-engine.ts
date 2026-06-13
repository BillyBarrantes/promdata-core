/**
 * DuckDB-WASM Engine v1.0 — Motor Analítico Client-Side
 * 
 * Singleton que gestiona una instancia DuckDB-WASM en el navegador.
 * Carga datos desde Arrow IPC (base64) de la Fase 3 y ejecuta
 * queries SQL locales para cross-filtering en <50ms.
 * 
 * Principios:
 * - Singleton: Una sola instancia DuckDB compartida por toda la app
 * - Lazy Init: Se inicializa solo cuando hay datos Arrow para cargar
 * - Schema-Agnostic: Acepta cualquier tabla Arrow sin conocer las columnas
 * - Fail-Safe: Si DuckDB falla, el drill-down al backend sigue funcionando
 */

import * as duckdb from '@duckdb/duckdb-wasm';
import { recordLocalPerf, startLocalPerf } from '@/lib/local-performance';

// ---------------------------------------------------------------------------
// TIPOS
// ---------------------------------------------------------------------------

interface DuckDBState {
  db: duckdb.AsyncDuckDB | null;
  conn: duckdb.AsyncDuckDBConnection | null;
  initialized: boolean;
  tables: Set<string>;
  tableFingerprints: Map<string, string>;
  filterCache: Map<string, Record<string, unknown>[]>;
  arrowBinaryCache: Map<string, Uint8Array>;
  tableSchemaCache: Map<string, DuckDBColumnSchema[]>;
  resolvedConditionCache: Map<string, string | null>;
}

type DuckDBColumnSchema = {
  column_name: string;
  data_type: string;
};

type SyntheticBucketFilter = {
  type: 'others_excluding_visible';
  label?: string;
  dimension: string;
  excluded: string[];
};

// ---------------------------------------------------------------------------
// SINGLETON STATE
// ---------------------------------------------------------------------------

const state: DuckDBState = {
  db: null,
  conn: null,
  initialized: false,
  tables: new Set(),
  tableFingerprints: new Map(),
  filterCache: new Map(),
  arrowBinaryCache: new Map(),
  tableSchemaCache: new Map(),
  resolvedConditionCache: new Map(),
};

// Flag para evitar inicializaciones concurrentes
let initPromise: Promise<void> | null = null;

const FILTER_CACHE_LIMIT = 50;
const ARROW_BINARY_CACHE_LIMIT = 12;
const RESOLVED_CONDITION_CACHE_LIMIT = 160;

export interface ArrowPreloadEntry {
  tableName: string;
  base64Data: string | null | undefined;
  priority?: number;
}

function cloneArrowBinary(bytes: Uint8Array): Uint8Array {
  return bytes.slice();
}

function buildArrowFingerprint(base64Data: string): string {
  const prefix = base64Data.slice(0, 96);
  const suffix = base64Data.slice(-96);
  return `${base64Data.length}:${prefix}:${suffix}`;
}

function buildFilterCacheKey(
  tableName: string,
  filters: Record<string, string | null>
): string {
  const normalizedEntries = Object.entries(filters)
    .filter(([_, value]) => value !== null)
    .sort(([left], [right]) => left.localeCompare(right));

  return `${tableName}::${JSON.stringify(normalizedEntries)}`;
}

function buildResolvedConditionCacheKey(
  tableName: string,
  dimension: string,
  value: string
): string {
  return `${tableName}::${dimension}::${value}`;
}

function clearTableCaches(tableName: string): void {
  for (const key of Array.from(state.filterCache.keys())) {
    if (key.startsWith(`${tableName}::`)) {
      state.filterCache.delete(key);
    }
  }

  for (const key of Array.from(state.resolvedConditionCache.keys())) {
    if (key.startsWith(`${tableName}::`)) {
      state.resolvedConditionCache.delete(key);
    }
  }

  state.tableSchemaCache.delete(tableName);
}

function rememberFilterResult(
  cacheKey: string,
  rows: Record<string, unknown>[]
): void {
  if (state.filterCache.has(cacheKey)) {
    state.filterCache.delete(cacheKey);
  }

  state.filterCache.set(cacheKey, rows);

  if (state.filterCache.size > FILTER_CACHE_LIMIT) {
    const oldestKey = state.filterCache.keys().next().value;
    if (oldestKey) {
      state.filterCache.delete(oldestKey);
    }
  }
}

function rememberResolvedCondition(
  cacheKey: string,
  condition: string | null
): void {
  if (state.resolvedConditionCache.has(cacheKey)) {
    state.resolvedConditionCache.delete(cacheKey);
  }

  state.resolvedConditionCache.set(cacheKey, condition);

  if (state.resolvedConditionCache.size > RESOLVED_CONDITION_CACHE_LIMIT) {
    const oldestKey = state.resolvedConditionCache.keys().next().value;
    if (oldestKey) {
      state.resolvedConditionCache.delete(oldestKey);
    }
  }
}

function rememberArrowBinary(
  fingerprint: string,
  bytes: Uint8Array
): Uint8Array {
  if (state.arrowBinaryCache.has(fingerprint)) {
    const cached = state.arrowBinaryCache.get(fingerprint)!;
    state.arrowBinaryCache.delete(fingerprint);
    state.arrowBinaryCache.set(fingerprint, cached);
    return cloneArrowBinary(cached);
  }

  const cachedBytes = cloneArrowBinary(bytes);
  state.arrowBinaryCache.set(fingerprint, cachedBytes);

  if (state.arrowBinaryCache.size > ARROW_BINARY_CACHE_LIMIT) {
    const oldestKey = state.arrowBinaryCache.keys().next().value;
    if (oldestKey) {
      state.arrowBinaryCache.delete(oldestKey);
    }
  }

  return cloneArrowBinary(cachedBytes);
}

function decodeArrowBase64(base64Data: string): Uint8Array {
  const fingerprint = buildArrowFingerprint(base64Data);
  const cached = state.arrowBinaryCache.get(fingerprint);
  if (cached) {
    state.arrowBinaryCache.delete(fingerprint);
    state.arrowBinaryCache.set(fingerprint, cached);
    return cloneArrowBinary(cached);
  }

  const binaryString = atob(base64Data);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  return rememberArrowBinary(fingerprint, bytes);
}

// ---------------------------------------------------------------------------
// INICIALIZACIÓN (Lazy, una sola vez)
// ---------------------------------------------------------------------------

async function initDuckDB(): Promise<void> {
  if (state.initialized) return;

  // Evitar race conditions
  if (initPromise) return initPromise;

  initPromise = (async () => {
    try {
      console.log('🦆 [DuckDB] Inicializando motor WASM...');

      // Seleccionar el bundle más adecuado para el navegador
      const JSDELIVR_BUNDLES = duckdb.getJsDelivrBundles();
      const bundle = await duckdb.selectBundle(JSDELIVR_BUNDLES);

      if (!bundle.mainWorker) {
        throw new Error('No se pudo resolver el worker de DuckDB');
      }

      // 🛡️ [CORS FIX] Fetch worker script como blob para evitar
      // "SecurityError: Failed to construct 'Worker': Script at CDN
      //  cannot be accessed from origin 'http://localhost:3000'"
      // Patrón estándar: fetch → Blob → createObjectURL (same-origin)
      const workerScript = await fetch(bundle.mainWorker);
      const workerBlob = new Blob([await workerScript.text()], { type: 'text/javascript' });
      const workerUrl = URL.createObjectURL(workerBlob);
      const worker = new Worker(workerUrl);

      // 🔇 VoidLogger: suprime TODOS los logs internos de DuckDB (Binder Errors, etc.)
      // Nuestros propios console.log en query()/crossFilter() proveen visibilidad completa.
      const logger = new duckdb.VoidLogger();
      const db = new duckdb.AsyncDuckDB(logger, worker);

      await db.instantiate(bundle.mainModule, bundle.pthreadWorker);

      const conn = await db.connect();

      state.db = db;
      state.conn = conn;
      state.initialized = true;

      console.log('🦆 [DuckDB] Motor WASM listo ✅');
    } catch (error) {
      console.error('⚠️ [DuckDB] Error inicializando:', error);
      state.initialized = false;
      throw error;
    } finally {
      initPromise = null;
    }
  })();

  return initPromise;
}

export async function warmup(): Promise<void> {
  await initDuckDB();
}

// ---------------------------------------------------------------------------
// CARGA DE DATOS: Arrow base64 → DuckDB Table
// ---------------------------------------------------------------------------

/**
 * Mutex por tabla para serializar cargas concurrentes.
 * React dev mode (StrictMode) double-fires useEffect, y chat-interface.tsx
 * llama loadArrowData para múltiples tablas — sin mutex, dos calls para
 * la misma tabla colisionan entre DROP e INSERT.
 */
const loadLocks = new Map<string, Promise<void>>();

/**
 * Carga datos Arrow IPC (base64) en una tabla DuckDB local.
 * Serializado por tabla via mutex para evitar race conditions.
 * 
 * @param base64Data - String base64 del Arrow IPC stream (de arrow_utils.py)
 * @param tableName - Nombre de la tabla destino (default: 'analysis_data')
 */
export async function loadArrowData(
  base64Data: string,
  tableName: string = 'analysis_data'
): Promise<void> {
  // 🔒 Mutex per-table: encadenar promesas para serializar acceso
  const previousLock = loadLocks.get(tableName) ?? Promise.resolve();
  
  // Crear UNA sola promesa de ejecución (no duplicar la llamada)
  let resolve: () => void;
  let reject: (e: unknown) => void;
  const currentLock = new Promise<void>((res, rej) => { resolve = res; reject = rej; });
  loadLocks.set(tableName, currentLock.catch(() => {})); // lock nunca rechaza

  // Esperar turno previo, luego ejecutar
  await previousLock.catch(() => {}); // ignorar errores de la carga previa
  try {
    await _loadArrowDataUnsafe(base64Data, tableName);
    resolve!();
  } catch (e) {
    reject!(e);
    throw e;
  }
}

export async function preloadArrowTables(
  entries: ArrowPreloadEntry[],
  eagerCount: number = 1
): Promise<void> {
  const normalizedEntries = entries
    .filter((entry): entry is ArrowPreloadEntry & { base64Data: string } => Boolean(entry?.tableName && entry?.base64Data))
    .sort((left, right) => (left.priority ?? 99) - (right.priority ?? 99));

  if (normalizedEntries.length === 0) return;

  await initDuckDB();

  const eagerEntries = normalizedEntries.slice(0, Math.max(0, eagerCount));
  const backgroundEntries = normalizedEntries.slice(Math.max(0, eagerCount));

  for (const entry of eagerEntries) {
    await loadArrowData(entry.base64Data, entry.tableName);
  }

  backgroundEntries.forEach((entry) => {
    void loadArrowData(entry.base64Data, entry.tableName).catch((error) => {
      console.warn(`⚠️ [DuckDB] Preload diferido falló para '${entry.tableName}':`, error);
    });
  });
}

async function getTableSchema(tableName: string): Promise<DuckDBColumnSchema[]> {
  const cachedSchema = state.tableSchemaCache.get(tableName);
  if (cachedSchema) {
    return cachedSchema;
  }

  const escapedTableName = tableName.replace(/'/g, "''");
  const columns = await query(
    `SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '${escapedTableName}'`,
    true
  );

  const normalizedColumns = columns.map((column) => ({
    column_name: String(column.column_name),
    data_type: String(column.data_type),
  }));

  state.tableSchemaCache.set(tableName, normalizedColumns);
  return normalizedColumns;
}

/**
 * Implementación interna — NO llamar directamente, usar loadArrowData().
 */
async function _loadArrowDataUnsafe(
  base64Data: string,
  tableName: string
): Promise<void> {
  try {
    await initDuckDB();

    if (!state.db) {
      throw new Error('DuckDB no inicializado');
    }

    const fingerprint = buildArrowFingerprint(base64Data);
    if (state.tables.has(tableName) && state.tableFingerprints.get(tableName) === fingerprint) {
      return;
    }

    // Decodificación reutilizable con cache LRU en memoria
    const bytes = decodeArrowBase64(base64Data);

    if (!state.conn) {
      throw new Error('DuckDB sin conexión activa');
    }

    const conn = state.conn;

    // Limpiar tabla previa (evitar fugas de memoria entre análisis)
    state.tables.delete(tableName);
    state.tableFingerprints.delete(tableName);
    await conn.query(`DROP TABLE IF EXISTS "${tableName}"`);
    clearTableCaches(tableName);

    // 🚀 Inserción nativa Arrow IPC — API oficial de DuckDB-WASM
    // Usamos la conexión singleton para que la tabla quede visible
    // para el resto del ciclo reactivo y no muera al cerrar una conexión temporal.
    await conn.insertArrowFromIPCStream(bytes, { name: tableName });

    state.tables.add(tableName);
    state.tableFingerprints.set(tableName, fingerprint);

    // Verificar carga
    const result = await conn.query(`SELECT COUNT(*) as total FROM "${tableName}"`);
    const count = result.toArray()[0]?.total ?? 0;

    console.log(`🦆 [DuckDB] Tabla '${tableName}' cargada vía IPC Stream: ${count} filas ✅`);
  } catch (error) {
    console.error(`⚠️ [DuckDB] Error cargando tabla '${tableName}':`, error);
    throw error;
  }
}

// ---------------------------------------------------------------------------
// QUERIES: SQL local → Records
// ---------------------------------------------------------------------------

/**
 * Ejecuta una query SQL local y retorna los resultados como array de objetos.
 * Diseñado para cross-filtering en <50ms.
 * 
 * @param sql - Query SQL (SELECT ... FROM analysis_data WHERE ...)
 * @param silent - Si true, no imprime errores en consola (para queries de sondeo)
 * @returns Array de objetos (cada objeto = una fila)
 */
export async function query(sql: string, silent: boolean = false): Promise<Record<string, unknown>[]> {
  if (!state.conn || !state.initialized) {
    throw new Error('DuckDB no inicializado');
  }

  const finishPerf = startLocalPerf('duckdb_query', {
    silent,
    sqlLength: sql.length,
  });

  try {
    const start = performance.now();
    const result = await state.conn.query(sql);
    const elapsed = (performance.now() - start).toFixed(1);

    const rows = result.toArray().map((row: any) => {
      // DuckDB retorna StructRow → convertir a plain object
      if (row && typeof row === 'object') {
        const obj: Record<string, unknown> = {};
        for (const key of Object.keys(row)) {
          obj[key] = row[key];
        }
        return obj;
      }
      return row;
    });

    if (!silent) {
      console.log(`🦆 [DuckDB] Query ejecutada en ${elapsed}ms → ${rows.length} filas`);
    }
    finishPerf({ rows: rows.length });
    return rows;
  } catch (error) {
    finishPerf({
      rows: 0,
      failed: true,
      error: error instanceof Error ? error.message : String(error),
    });
    if (!silent) {
      console.error('⚠️ [DuckDB] Error ejecutando query:', error);
    }
    throw error;
  }
}

// ---------------------------------------------------------------------------
// CROSS-FILTER: Filtrado instantáneo con resolución de 4 niveles
// ---------------------------------------------------------------------------

// --- Contexto implícito eliminado por Fix Fase 4 ---
// El backend de IBIS pre-pinta el contexto temporal antes de enviar el Arrow IPC.
// DuckDB Wasm ya no fuerza condiciones retroactivas que generan "amnesia".

/**
 * Helper: identifica si un tipo de dato es textual (VARCHAR, TEXT, CHAR).
 */
function isTextType(dataType: string): boolean {
  const t = dataType.toUpperCase();
  // STRUCT, MAP, and LIST types may contain VARCHAR internally but are NOT
  // text-filterable columns.  Exclude them to prevent type-cast errors like
  // "VARCHAR 'Pesado (30T)' can't be cast to STRUCT(...)".
  if (t.includes('STRUCT') || t.includes('MAP(') || t.includes('LIST(')) {
    return false;
  }
  return t.includes('VARCHAR') || t.includes('TEXT') || t.includes('CHAR');
}

function isTemporalType(dataType: string): boolean {
  const t = dataType.toUpperCase();
  return t.includes('DATE') || t.includes('TIMESTAMP') || t.startsWith('TIME') || t.includes(' TIME');
}

type TemporalValueCandidate = {
  year: number;
  month?: number;
  day?: number;
  kind: 'iso_date' | 'year_month' | 'month_year_label' | 'year_only';
};

const MONTH_TOKEN_TO_NUMBER: Record<string, number> = {
  jan: 1,
  january: 1,
  ene: 1,
  enero: 1,
  feb: 2,
  february: 2,
  febrero: 2,
  mar: 3,
  march: 3,
  marzo: 3,
  apr: 4,
  april: 4,
  abr: 4,
  abril: 4,
  may: 5,
  mayo: 5,
  jun: 6,
  june: 6,
  junio: 6,
  jul: 7,
  july: 7,
  julio: 7,
  aug: 8,
  august: 8,
  ago: 8,
  agosto: 8,
  sep: 9,
  sept: 9,
  september: 9,
  septiembre: 9,
  oct: 10,
  october: 10,
  octubre: 10,
  nov: 11,
  november: 11,
  noviembre: 11,
  dec: 12,
  december: 12,
  dic: 12,
  diciembre: 12,
};

function normalizeMonthToken(token: string): string {
  return token
    .replace(/\0/g, '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z]/g, '');
}

function buildTemporalValueCandidates(rawValue: string): TemporalValueCandidate[] {
  const normalized = rawValue
    .replace(/\0/g, '')
    .normalize('NFC')
    .replace(/\s+/g, ' ')
    .trim();
  if (!normalized) return [];

  const candidates: TemporalValueCandidate[] = [];
  const seen = new Set<string>();
  const pushCandidate = (candidate: TemporalValueCandidate | null): void => {
    if (!candidate) return;
    const { year, month, day } = candidate;
    if (year < 1900 || year > 2100) return;
    if (month !== undefined && (month < 1 || month > 12)) return;
    if (day !== undefined && (day < 1 || day > 31)) return;
    const key = `${year}-${month ?? 0}-${day ?? 0}`;
    if (seen.has(key)) return;
    seen.add(key);
    candidates.push(candidate);
  };

  let match = normalized.match(/^(\d{4})[-/](\d{2})[-/](\d{2})$/);
  if (match) {
    pushCandidate({
      year: Number(match[1]),
      month: Number(match[2]),
      day: Number(match[3]),
      kind: 'iso_date',
    });
  }

  match = normalized.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$/);
  if (match) {
    pushCandidate({
      year: Number(match[3]),
      month: Number(match[2]),
      day: Number(match[1]),
      kind: 'iso_date',
    });
  }

  match = normalized.match(/^(\d{4})[-/](\d{2})$/);
  if (match) {
    pushCandidate({
      year: Number(match[1]),
      month: Number(match[2]),
      kind: 'year_month',
    });
  }

  match = normalized.match(/^([a-záéíóúñü]{3,12})[-/_.\s]+(\d{4})$/i);
  if (match) {
    const monthToken = normalizeMonthToken(match[1]);
    const monthNumber = MONTH_TOKEN_TO_NUMBER[monthToken];
    if (monthNumber) {
      pushCandidate({
        year: Number(match[2]),
        month: monthNumber,
        kind: 'month_year_label',
      });
    }
  }

  match = normalized.match(/^(\d{4})[-/_.\s]+([a-záéíóúñü]{3,12})$/i);
  if (match) {
    const monthToken = normalizeMonthToken(match[2]);
    const monthNumber = MONTH_TOKEN_TO_NUMBER[monthToken];
    if (monthNumber) {
      pushCandidate({
        year: Number(match[1]),
        month: monthNumber,
        kind: 'month_year_label',
      });
    }
  }

  match = normalized.match(/^(\d{4})$/);
  if (match) {
    pushCandidate({
      year: Number(match[1]),
      kind: 'year_only',
    });
  }

  return candidates;
}

function temporalPredicateForColumn(columnName: string, candidate: TemporalValueCandidate): string {
  if (candidate.day !== undefined && candidate.month !== undefined) {
    const month = String(candidate.month).padStart(2, '0');
    const day = String(candidate.day).padStart(2, '0');
    return `CAST(CAST("${columnName}" AS TIMESTAMP) AS DATE) = DATE '${candidate.year}-${month}-${day}'`;
  }
  if (candidate.month !== undefined) {
    return `EXTRACT(YEAR FROM CAST("${columnName}" AS TIMESTAMP)) = ${candidate.year} AND EXTRACT(MONTH FROM CAST("${columnName}" AS TIMESTAMP)) = ${candidate.month}`;
  }
  return `EXTRACT(YEAR FROM CAST("${columnName}" AS TIMESTAMP)) = ${candidate.year}`;
}

async function tryResolveTemporalCondition(
  tableName: string,
  columnName: string,
  candidates: TemporalValueCandidate[]
): Promise<string | null> {
  for (const candidate of candidates) {
    const predicate = temporalPredicateForColumn(columnName, candidate);
    try {
      const match = await query(
        `SELECT COUNT(*) as cnt FROM "${tableName}" WHERE ${predicate}`,
        true
      );
      if (match.length > 0 && Number(match[0].cnt) > 0) {
        return predicate;
      }
    } catch {
      continue;
    }
  }
  return null;
}

/**
 * Normaliza texto para matching tolerante:
 * - minúsculas
 * - sin diacríticos (cuando sea representable)
 * - sólo alfanumérico (sin espacios/puntuación)
 *
 * Esto permite empatar variantes como:
 *   "Piña" / "PiÃ±a" / "Pina"
 */
function canonicalizeFilterToken(value: string): string {
  const repaired = value
    .replace(/Ã±/gi, 'ñ')
    .replace(/Ã¡/gi, 'á')
    .replace(/Ã©/gi, 'é')
    .replace(/Ãí/gi, 'í')
    .replace(/Ã³/gi, 'ó')
    .replace(/Ãº/gi, 'ú')
    .replace(/Ã¼/gi, 'ü')
    .replace(/Â/g, '');

  return repaired
    .replace(/\0/g, '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '');
}

function buildCanonicalSqlExpr(colName: string): string {
  return `regexp_replace(
    lower(
      replace(
        replace(
          replace(
            replace(
              replace(
                replace(
                  replace(
                    replace(
                      replace(CAST("${colName}" AS VARCHAR), 'Ã±', 'n'),
                    'Ã‘', 'n'),
                  'Ã¡', 'a'),
                'Ã©', 'e'),
              'Ãí', 'i'),
            'Ã³', 'o'),
          'Ãº', 'u'),
        'Ã¼', 'u'),
      'Â', '')
    ),
    '[^a-z0-9]+',
    '',
    'g'
  )`;
}

function isOthersBucketLabel(value: string | null | undefined): boolean {
  if (!value) return false;
  const normalized = canonicalizeFilterToken(String(value));
  return normalized === 'otros' || normalized === 'other' || normalized === 'others';
}

function parseSyntheticBucketFilter(
  filters: Record<string, string | null>
): SyntheticBucketFilter | null {
  const rawValue = filters.__synthetic_bucket__;
  if (!rawValue) return null;

  try {
    const parsed = JSON.parse(rawValue);
    if (
      !parsed
      || parsed.type !== 'others_excluding_visible'
      || typeof parsed.dimension !== 'string'
      || !Array.isArray(parsed.excluded)
    ) {
      return null;
    }

    const excluded = parsed.excluded
      .map((entry: unknown) => String(entry ?? '').trim())
      .filter((entry: string) => Boolean(entry) && !isOthersBucketLabel(entry));

    if (excluded.length === 0) {
      return null;
    }

    return {
      type: 'others_excluding_visible',
      label: typeof parsed.label === 'string' ? parsed.label : undefined,
      dimension: parsed.dimension,
      excluded,
    };
  } catch {
    return null;
  }
}

function buildSyntheticBucketCondition(
  columns: DuckDBColumnSchema[],
  filter: SyntheticBucketFilter
): string | null {
  const targetColumn = columns.find((column) => String(column.column_name) === filter.dimension);
  if (!targetColumn) {
    return null;
  }

  const canonicalExpr = buildCanonicalSqlExpr(filter.dimension);
  const escapedExcluded = filter.excluded
    .map((entry) => canonicalizeFilterToken(entry))
    .filter(Boolean)
    .map((entry) => `'${entry.replace(/'/g, "''")}'`);

  if (escapedExcluded.length === 0) {
    return null;
  }

  return `${canonicalExpr} NOT IN (${escapedExcluded.join(', ')}) AND ${canonicalExpr} <> ''`;
}

type GlobalFilterCandidate = {
  value: string;
  kind: 'full' | 'prefix_stripped' | 'separator_tail' | 'numeric_tail';
  allowCastFallback: boolean;
};

function looksLikeMetricColumnName(columnName: string): boolean {
  return /(cantidad|qty|venta|total|monto|importe|saldo|stock|units|unidades|precio|valor|cost|costo|amount|revenue|income|expense|balance|hours|rate|score|percent|margin|discount)/i.test(columnName);
}

/**
 * Domain-agnostic: detecta columnas que probablemente son códigos
 * o identificadores estructurales usando patrones genéricos de naming,
 * no términos de dominio específico.
 */
function looksLikeStructuralCodeColumnName(columnName: string): boolean {
  return /(tipo|type|codigo|code|cod|id|sku|key|ref|num|nombre|name|label|desc|category|categoria|grupo|group|clase|class|segmento|segment|status|estado|region|zona|area)/i.test(columnName);
}

/**
 * Domain-agnostic: permite fallback numérico cuando el valor parece
 * un código estructural ("Tipo 130", "Cat: 42") sin hardcodear dominios.
 */
function shouldAllowNumericTailFallback(rawValue: string): boolean {
  const normalized = rawValue
    .replace(/\0/g, '')
    .normalize('NFC')
    .replace(/\s+/g, ' ')
    .trim();

  if (!normalized) return false;
  // Valor puramente numérico → siempre permitir
  if (/^\d+(?:[.,]\d+)?$/.test(normalized)) return true;
  // Patrón genérico "Label: 123" o "Key = 456" → permitir
  if (/^[^:=]+[:=]\s*\d+(?:[.,]\d+)?$/i.test(normalized)) return true;
  // Patrón genérico "Prefix NNN" (ej: "Tipo 130", "Cat 42", "Zona 5")
  if (/^[a-záéíóúñü]+(?:\s+[a-záéíóúñü]+)*\s+\d+(?:[.,]\d+)?$/i.test(normalized)) return true;
  return false;
}

function buildGlobalFilterValueCandidates(value: string): GlobalFilterCandidate[] {
  const normalized = value
    .replace(/\0/g, '')
    .normalize('NFC')
    .replace(/\s+/g, ' ')
    .trim();

  const candidates: GlobalFilterCandidate[] = [];
  const seen = new Set<string>();
  const pushCandidate = (
    candidate: string | null | undefined,
    kind: GlobalFilterCandidate['kind'],
    allowCastFallback: boolean,
  ) => {
    if (!candidate) return;
    const cleaned = candidate
      .replace(/\0/g, '')
      .normalize('NFC')
      .replace(/\s+/g, ' ')
      .trim();
    if (!cleaned || seen.has(cleaned)) return;
    seen.add(cleaned);
    candidates.push({ value: cleaned, kind, allowCastFallback });
  };

  pushCandidate(normalized, 'full', false);

  // Domain-agnostic: strip genérico de prefijos tipo "Label VALUE"
  // Captura patrones como "Tipo X", "Cat 42", "Region Norte", etc.
  const prefixedMatch = normalized.match(/^([a-záéíóúñü]+(?:\s+(?:de|del)\s+[a-záéíóúñü]+)?)\s+(.+)$/i);
  if (prefixedMatch?.[2] && prefixedMatch[1].length <= 30) {
    pushCandidate(prefixedMatch[2], 'prefix_stripped', true);
  }

  const separatorMatch = normalized.match(/^[^:=]+[:=]\s*(.+)$/);
  if (separatorMatch?.[1]) {
    pushCandidate(separatorMatch[1], 'separator_tail', true);
  }

  const numericTail = normalized.match(/(\d+(?:[.,]\d+)?)$/);
  if (numericTail?.[1] && shouldAllowNumericTailFallback(normalized)) {
    pushCandidate(numericTail[1].replace(',', '.'), 'numeric_tail', true);
  }

  return candidates;
}

/**
 * Domain-agnostic: puntúa columnas para filtro global usando heurísticas
 * universales basadas en el tipo de nombre (categórico vs métrico)
 * sin priorizar ningún dominio específico.
 *
 * Principio: las columnas que lucen categóricas/descriptivas (name, type,
 * category, status, code) reciben prioridad sobre las métricas.
 */
function scoreColumnForGlobalFilter(columnName: string, filterValue: string): number {
  const normalizedColumn = columnName.toLowerCase();
  const normalizedFilter = filterValue.toLowerCase();

  let score = 0;

  // --- Anti-score: las columnas métricas NUNCA deben ser el target de un filtro global ---
  if (looksLikeMetricColumnName(normalizedColumn)) {
    return -100;
  }

  // --- Bonus por patrones genéricos de columnas categóricas ---
  // Tier 1: Columnas de tipo/categoría (alta probabilidad de ser filtro)
  if (/(tipo|type|category|categoria|clase|class|grupo|group|segmento|segment)/i.test(normalizedColumn)) score += 40;
  // Tier 2: Columnas de nombre/label (descriptivas)
  if (/(name|nombre|label|titulo|title|desc)/i.test(normalizedColumn)) score += 25;
  // Tier 3: Columnas de código/ID (identificadores estructurales)
  if (/(codigo|code|cod|id|sku|ref|key|num)/i.test(normalizedColumn)) score += 15;
  // Tier 4: Columnas de estado/región (contextuales)
  if (/(status|estado|region|zona|area|pais|country|ciudad|city)/i.test(normalizedColumn)) score += 12;

  // --- Bonus por coincidencia textual directa entre filtro y columna ---
  const filterTokens = normalizedFilter.replace(/[^a-záéíóúñü0-9\s]/gi, '').split(/\s+/).filter(t => t.length > 2);
  const colTokens = normalizedColumn.replace(/_/g, ' ').split(/\s+/);
  const tokenOverlap = filterTokens.filter(ft => colTokens.some(ct => ct.includes(ft) || ft.includes(ct))).length;
  if (tokenOverlap > 0) score += 30 * tokenOverlap;

  return score;
}

/**
 * Ejecuta un cross-filter con resolución inteligente de 4 niveles.
 * Diseñado para ser resiliente a las "decoraciones semánticas" que el LLM
 * inyecta en las etiquetas de los gráficos (ej: "Tipo Almacen 130" vs "130").
 *
 * Niveles de resolución:
 *   L1: Columna directa (dimension es un nombre de columna real)
 *   L2: Coincidencia exacta del valor en columnas texto
 *   L3: Coincidencia exacta con CAST en todas las columnas
 *   L4: 🧠 Fuzzy reverse-contains (SOLO en columnas texto — evita colisiones de dominio)
 *
 * Contexto temporal:
 *   Si la tabla tiene `is_latest_snapshot`, se inyecta automáticamente
 *   `AND is_latest_snapshot = true` para preservar el contexto del gráfico.
 *
 * ⚠️ NUNCA ejecuta una query con un nombre de columna sin validar.
 */
export async function crossFilter(
  filters: Record<string, string | null>,
  tableName: string = 'analysis_data',
  crossFilterContext?: any
): Promise<Record<string, unknown>[]> {
  if (!state.tables.has(tableName)) {
    throw new Error(`Tabla '${tableName}' no cargada en DuckDB`);
  }

  const finishPerf = startLocalPerf('duckdb_cross_filter', {
    tableName,
    filterCount: Object.values(filters).filter((value) => value !== null).length,
  }, tableName);

  const syntheticBucketFilter = parseSyntheticBucketFilter(filters);

  // Si no hay filtros válidos, retornamos todo
  const validFilters = Object.entries(filters).filter(([key, value]) => value !== null && !key.startsWith('__'));
  if (validFilters.length === 0) {
    const rows = await query(`SELECT * FROM "${tableName}"`);
    finishPerf({
      cacheHit: false,
      rows: rows.length,
      emptyFilterSet: true,
    });
    return rows;
  }

  console.log(`🕵️ [CROSS-FILTER] input`, {
    tableName,
    filters: validFilters.map(([dimension, value]) => ({ dimension, value })),
    syntheticBucket: syntheticBucketFilter,
  });

  const cacheKey = buildFilterCacheKey(tableName, filters);
  const cachedResult = state.filterCache.get(cacheKey);
  if (cachedResult) {
    console.log(`🦆 [CROSS-FILTER] Cache hit local: ${tableName} ✅`);
    finishPerf({
      cacheHit: true,
      rows: cachedResult.length,
    });
    return cachedResult;
  }

  // --- Obtener schema de la tabla (una sola vez por versión cargada) ---
  const columns = await getTableSchema(tableName);

  const conditions: string[] = [];

  // FASE 4: Predicados estructurados del crossFilterContext
  if (crossFilterContext) {
    const predicates = [
      ...(Array.isArray(crossFilterContext.base_predicates) ? crossFilterContext.base_predicates : []),
      ...(Array.isArray(crossFilterContext.runtime_predicates) ? crossFilterContext.runtime_predicates : [])
    ];
    for (const pred of predicates) {
      if (pred.column && pred.operator && pred.value !== undefined) {
        const hasColumn = columns.some((c: any) => String(c.column_name) === pred.column);
        if (hasColumn) {
          let op = String(pred.operator).toUpperCase();
          if (op === '==') op = '=';
          let condition = '';
          
          if (op === 'IN' && Array.isArray(pred.value)) {
            const listStr = pred.value.map((v: any) => `'${String(v).replace(/'/g, "''")}'`).join(', ');
            condition = `"${pred.column}" IN (${listStr})`;
          } else if (op === 'BETWEEN' && Array.isArray(pred.value) && pred.value.length === 2) {
            const val1 = String(pred.value[0]).replace(/'/g, "''");
            const val2 = String(pred.value[1]).replace(/'/g, "''");
            condition = `"${pred.column}" BETWEEN '${val1}' AND '${val2}'`;
          } else if (typeof pred.value === 'boolean') {
            // Booleanos: emitir como literal SQL (true/false), no como string
            condition = `"${pred.column}" ${op} ${pred.value}`;
          } else {
            const escapedValue = String(pred.value).replace(/'/g, "''");
            condition = `"${pred.column}" ${op} '${escapedValue}'`;
          }
          console.log(`🧠 [CROSS-FILTER] context_predicate_added:`, condition);
          conditions.push(condition);
        } else {
          console.warn(`⚠️ [CROSS-FILTER] Predicado estructural omitido, columna '${pred.column}' no existe en '${tableName}'`);
        }
      }
    }
  }

  if (syntheticBucketFilter) {
    const syntheticCondition = buildSyntheticBucketCondition(columns, syntheticBucketFilter);
    if (syntheticCondition) {
      console.log(`🧠 [CROSS-FILTER] synthetic_bucket_condition`, {
        tableName,
        dimension: syntheticBucketFilter.dimension,
        excluded: syntheticBucketFilter.excluded,
        condition: syntheticCondition,
      });
      conditions.push(syntheticCondition);
    } else {
      console.warn(`⚠️ [CROSS-FILTER] Synthetic bucket sin dimensión resoluble en "${tableName}"`);
    }
  }

  for (const [dimension, value] of validFilters) {
    if (value === null) continue;
    if (
      syntheticBucketFilter
      && (dimension === 'global_cross_filter' || dimension === 'global_chart_filter')
      && isOthersBucketLabel(value)
    ) {
      console.log(`🧠 [CROSS-FILTER] synthetic_bucket_skip_literal`, {
        tableName,
        dimension,
        value,
      });
      continue;
    }

    const escapedValue = value.replace(/'/g, "''");
    
    const condition = await resolveFilterCondition(tableName, columns, dimension, value, escapedValue);
    if (condition) {
      console.log(`🕵️ [CROSS-FILTER] resolved_condition`, {
        tableName,
        dimension,
        value,
        condition,
      });
      conditions.push(condition);
    } else {
      console.warn(`⚠️ [CROSS-FILTER] Degradación elegante: Valor "${value}" (dimensión: ${dimension}) omitido porque no se encontró coincidencia estructural en la tabla "${tableName}"`);
      continue; // No abortar; mantener los filtros que SÍ funcionaron
    }
  }

  if (conditions.length === 0) {
    console.warn(`⚠️ [CROSS-FILTER] Intersección vacía: ningún filtro resolvió una condición estructural en "${tableName}"`);
    rememberFilterResult(cacheKey, []);
    finishPerf({
      cacheHit: false,
      rows: 0,
      unresolvedFilters: true,
    });
    return [];
  }

  const whereClause = conditions.join(' AND ');
  const sqlQuery = `SELECT * FROM "${tableName}" WHERE ${whereClause}`;
  console.log(`2. SQL Generado: ${sqlQuery}`);
  
  const results = await query(sqlQuery);
  rememberFilterResult(cacheKey, results);
  console.log(`3. Filas retornadas: ${results.length}`);
  finishPerf({
    cacheHit: false,
    rows: results.length,
  });
  return results;
}

/**
 * Función auxiliar para resolver la mejor coincidencia de columna y su condición
 */
async function resolveFilterCondition(
  tableName: string,
  columns: DuckDBColumnSchema[],
  dimension: string,
  value: string,
  escapedValue: string
): Promise<string | null> {
  const conditionCacheKey = buildResolvedConditionCacheKey(tableName, dimension, value);
  if (state.resolvedConditionCache.has(conditionCacheKey)) {
    return state.resolvedConditionCache.get(conditionCacheKey) ?? null;
  }

  const columnNames = new Set(columns.map(c => String(c.column_name)));
  const canonicalValue = canonicalizeFilterToken(value);
  const escapedCanonicalValue = canonicalValue.replace(/'/g, "''");
  const temporalCandidates = buildTemporalValueCandidates(value);
  const isDateLikeValue = temporalCandidates.some((candidate) => candidate.day !== undefined);
  const isSyntheticGlobalFilter = dimension === 'global_cross_filter' || dimension === 'global_chart_filter';


  // --- Construir contexto implícito REMOVIDO ---
  // El contexto ahora se delega completamente a la tabla específica entregada por el backend

  // ─── FAST PATH: FILTRO GLOBAL SINTÉTICO (prioriza columna correcta) ───
  if (isSyntheticGlobalFilter) {
    const valueCandidates = buildGlobalFilterValueCandidates(value);
    const prioritizedColumns = [...columns].sort((left, right) => {
      const leftName = String(left.column_name);
      const rightName = String(right.column_name);
      const leftScore = scoreColumnForGlobalFilter(leftName, valueCandidates[0]?.value || value);
      const rightScore = scoreColumnForGlobalFilter(rightName, valueCandidates[0]?.value || value);
      return rightScore - leftScore;
    });

    console.log(`🕵️ [CROSS-FILTER] synthetic_candidates`, {
      tableName,
      dimension,
      rawValue: value,
      valueCandidates: valueCandidates.map((candidate) => ({
        value: candidate.value,
        kind: candidate.kind,
        allowCastFallback: candidate.allowCastFallback,
      })),
      topColumns: prioritizedColumns.slice(0, 8).map((entry) => ({
        column: String(entry.column_name),
        type: String(entry.data_type),
      })),
    });

    for (const candidate of valueCandidates) {
      const candidateValue = candidate.value;
      const escapedCandidate = candidateValue.replace(/'/g, "''");
      const canonicalCandidate = canonicalizeFilterToken(candidateValue);
      const escapedCanonicalCandidate = canonicalCandidate.replace(/'/g, "''");

      for (const col of prioritizedColumns) {
        const colName = String(col.column_name);
        const textCol = isTextType(String(col.data_type));
        const temporalCol = isTemporalType(String(col.data_type));

        if (temporalCol && temporalCandidates.length > 0) {
          const temporalCondition = await tryResolveTemporalCondition(tableName, colName, temporalCandidates);
          if (temporalCondition) {
            console.log(`🦆 [CROSS-FILTER] FAST global temporal: "${colName}" = "${candidateValue}" ✅`);
            rememberResolvedCondition(conditionCacheKey, temporalCondition);
            return temporalCondition;
          }
        }

        if (textCol) {
          const exactMatch = await query(
            `SELECT COUNT(*) as cnt FROM "${tableName}" WHERE "${colName}" = '${escapedCandidate}'`,
            true
          );
          if (exactMatch.length > 0 && Number(exactMatch[0].cnt) > 0) {
            console.log(`🦆 [CROSS-FILTER] FAST global exact: "${colName}" = "${candidateValue}" ✅`);
            const resolvedCondition = `"${colName}" = '${escapedCandidate}'`;
            rememberResolvedCondition(conditionCacheKey, resolvedCondition);
            return resolvedCondition;
          }

          const ciMatch = await query(
            `SELECT COUNT(*) as cnt FROM "${tableName}" WHERE LOWER(TRIM("${colName}")) = LOWER(TRIM('${escapedCandidate}'))`,
            true
          );
          if (ciMatch.length > 0 && Number(ciMatch[0].cnt) > 0) {
            console.log(`🦆 [CROSS-FILTER] FAST global CI: "${colName}" = "${candidateValue}" ✅`);
            const resolvedCondition = `LOWER(TRIM("${colName}")) = LOWER(TRIM('${escapedCandidate}'))`;
            rememberResolvedCondition(conditionCacheKey, resolvedCondition);
            return resolvedCondition;
          }

          if (escapedCanonicalCandidate.length > 0) {
            const canonicalExpr = buildCanonicalSqlExpr(colName);
            const canonicalMatch = await query(
              `SELECT COUNT(*) as cnt
               FROM "${tableName}"
               WHERE ${canonicalExpr} = '${escapedCanonicalCandidate}'`,
              true
            );
            if (canonicalMatch.length > 0 && Number(canonicalMatch[0].cnt) > 0) {
              console.log(`🦆 [CROSS-FILTER] FAST global canónico: "${colName}" = "${candidateValue}" ✅`);
              const resolvedCondition = `${canonicalExpr} = '${escapedCanonicalCandidate}'`;
              rememberResolvedCondition(conditionCacheKey, resolvedCondition);
              return resolvedCondition;
            }
          }
        }

        if (!candidate.allowCastFallback) {
          continue;
        }

        const isStructuralColumn =
          looksLikeStructuralCodeColumnName(colName) &&
          !looksLikeMetricColumnName(colName);

        if (!isStructuralColumn) {
          continue;
        }

        const castMatch = await query(
          `SELECT COUNT(*) as cnt FROM "${tableName}" WHERE CAST("${colName}" AS VARCHAR) = '${escapedCandidate}'`,
          true
        );
        if (castMatch.length > 0 && Number(castMatch[0].cnt) > 0) {
          console.log(`🦆 [CROSS-FILTER] FAST global cast: "${colName}" = "${candidateValue}" ✅`);
          const resolvedCondition = `CAST("${colName}" AS VARCHAR) = '${escapedCandidate}'`;
          rememberResolvedCondition(conditionCacheKey, resolvedCondition);
          return resolvedCondition;
        }
      }
    }
  }

  // ─── L1: COLUMNA DIRECTA ─────────────────────────────────────────────
  if (columnNames.has(dimension)) {
    const result = await query(
      `SELECT COUNT(*) as cnt FROM "${tableName}" WHERE "${dimension}" = '${escapedValue}'`, true
    );
    if (result.length > 0 && Number(result[0].cnt) > 0) {
      console.log(`🦆 [CROSS-FILTER] L1 Columna directa: "${dimension}" ✅`);
      const resolvedCondition = `"${dimension}" = '${escapedValue}'`;
      rememberResolvedCondition(conditionCacheKey, resolvedCondition);
      return resolvedCondition;
    }
  }

  // ─── L2: COINCIDENCIA EXACTA EN COLUMNAS TEXTO ───────────────────────
  console.log(`🦆 [CROSS-FILTER] L2 Buscando valor exacto: "${value}"`);
  for (const col of columns) {
    const colName = String(col.column_name);
    if (!isTextType(String(col.data_type))) continue;

    const match = await query(
      `SELECT COUNT(*) as cnt FROM "${tableName}" WHERE "${colName}" = '${escapedValue}'`, true
    );
    if (match.length > 0 && Number(match[0].cnt) > 0) {
      console.log(`🦆 [CROSS-FILTER] L2 Columna texto: "${colName}" ✅`);
      const resolvedCondition = `"${colName}" = '${escapedValue}'`;
      rememberResolvedCondition(conditionCacheKey, resolvedCondition);
      return resolvedCondition;
    }

    // L2b: Igualdad case-insensitive + trim
    const ciMatch = await query(
      `SELECT COUNT(*) as cnt FROM "${tableName}" WHERE LOWER(TRIM("${colName}")) = LOWER(TRIM('${escapedValue}'))`, true
    );
    if (ciMatch.length > 0 && Number(ciMatch[0].cnt) > 0) {
      console.log(`🦆 [CROSS-FILTER] L2b Columna texto (case-insensitive): "${colName}" ✅`);
      const resolvedCondition = `LOWER(TRIM("${colName}")) = LOWER(TRIM('${escapedValue}'))`;
      rememberResolvedCondition(conditionCacheKey, resolvedCondition);
      return resolvedCondition;
    }

    // L2c: Igualdad canónica (tolera mojibake/acentos/puntuación/espacios)
    if (escapedCanonicalValue.length > 0) {
      const canonicalExpr = buildCanonicalSqlExpr(colName);
      const canonicalMatch = await query(
        `SELECT COUNT(*) as cnt
         FROM "${tableName}"
         WHERE ${canonicalExpr} = '${escapedCanonicalValue}'`,
        true
      );
      if (canonicalMatch.length > 0 && Number(canonicalMatch[0].cnt) > 0) {
        console.log(`🦆 [CROSS-FILTER] L2c Columna texto (canónica): "${colName}" ✅`);
        const resolvedCondition = `${canonicalExpr} = '${escapedCanonicalValue}'`;
        rememberResolvedCondition(conditionCacheKey, resolvedCondition);
        return resolvedCondition;
      }
    }
  }

  // ─── L3: COINCIDENCIA EXACTA CON CAST (numéricas como string) ────────
  for (const col of columns) {
    const colName = String(col.column_name);
    if (isTextType(String(col.data_type))) continue; // ya probadas en L2
    const temporalCol = isTemporalType(String(col.data_type));

    if (temporalCol && temporalCandidates.length > 0) {
      const temporalCondition = await tryResolveTemporalCondition(tableName, colName, temporalCandidates);
      if (temporalCondition) {
        console.log(`🦆 [CROSS-FILTER] L3 temporal match: "${colName}" ✅`);
        rememberResolvedCondition(conditionCacheKey, temporalCondition);
        return temporalCondition;
      }
    }

    const match = await query(
      `SELECT COUNT(*) as cnt FROM "${tableName}" WHERE CAST("${colName}" AS VARCHAR) = '${escapedValue}'`, true
    );
    if (match.length > 0 && Number(match[0].cnt) > 0) {
      console.log(`🦆 [CROSS-FILTER] L3 Columna numérica (cast): "${colName}" ✅`);
      const resolvedCondition = `CAST("${colName}" AS VARCHAR) = '${escapedValue}'`;
      rememberResolvedCondition(conditionCacheKey, resolvedCondition);
      return resolvedCondition;
    }

    // L3b: Filtro por prefijo para fechas (ej: "2021-07-31" vs "2021-07-31 00:00:00")
    if (isDateLikeValue) {
      const prefixMatch = await query(
        `SELECT COUNT(*) as cnt FROM "${tableName}" WHERE CAST("${colName}" AS VARCHAR) LIKE '${escapedValue}%'`,
        true
      );
      if (prefixMatch.length > 0 && Number(prefixMatch[0].cnt) > 0) {
        console.log(`🦆 [CROSS-FILTER] L3b Columna fecha/timestamp (prefix cast): "${colName}" ✅`);
        const resolvedCondition = `CAST("${colName}" AS VARCHAR) LIKE '${escapedValue}%'`;
        rememberResolvedCondition(conditionCacheKey, resolvedCondition);
        return resolvedCondition;
      }
    }
  }

  // ─── L4: 🧠 FUZZY REVERSE-CONTAINS (SOLO COLUMNAS TEXTO) ────────────
  console.log(`🦆 [CROSS-FILTER] L4 Fuzzy reverse-contains (solo texto) para: "${value}"`);

  let bestMatch: { colName: string; dbValue: string; matchLen: number } | null = null;

  for (const col of columns) {
    const colName = String(col.column_name);
    if (!isTextType(String(col.data_type))) continue;

    try {
      const fuzzyMatches = await query(
        `SELECT DISTINCT "${colName}" as val
         FROM "${tableName}"
         WHERE '${escapedValue}' LIKE '%' || "${colName}"
           AND LENGTH("${colName}") > 0
         ORDER BY LENGTH("${colName}") DESC
         LIMIT 1`, true
      );

      if (fuzzyMatches.length > 0) {
        const dbValue = String(fuzzyMatches[0].val);
        if (!bestMatch || dbValue.length > bestMatch.matchLen) {
          bestMatch = { colName, dbValue, matchLen: dbValue.length };
        }
      }
    } catch {
      continue;
    }
  }

  if (bestMatch) {
    const escapedDbValue = bestMatch.dbValue.replace(/'/g, "''");
    console.log(
      `🦆 [CROSS-FILTER] L4 Fuzzy: "${value}" → col "${bestMatch.colName}", DB val "${bestMatch.dbValue}" ✅`
    );
    const resolvedCondition = `"${bestMatch.colName}" = '${escapedDbValue}'`;
    rememberResolvedCondition(conditionCacheKey, resolvedCondition);
    return resolvedCondition;
  }

  rememberResolvedCondition(conditionCacheKey, null);
  return null;
}

// ---------------------------------------------------------------------------
// UTILIDADES
// ---------------------------------------------------------------------------

/** Indica si DuckDB está inicializado y tiene datos */
export function isReady(): boolean {
  return state.initialized && state.tables.size > 0;
}

/** Lista las tablas cargadas en DuckDB */
export function getTableNames(): string[] {
  return Array.from(state.tables);
}

/** Limpia todas las tablas inactiva para gestión de memoria WASM */
export async function cleanupInactiveTables(activeTableNames: string[]): Promise<void> {
  if (!state.conn) return;
  const activeSet = new Set(activeTableNames);
  for (const table of state.tables) {
    if (!activeSet.has(table)) {
      console.log(`🦆 [DuckDB] Limpiando tabla inactiva: ${table}`);
      try {
        await state.conn.query(`DROP TABLE IF EXISTS "${table}"`);
        
        // 🛡️ [Micro-Optimización Enterprise] Liberación del VFS (File System de DuckDB)
        // DuckDB-WASM a veces retiene internamente archivos `.arrow` físicos o en-buffer.
        // Forzamos explícitamente su eliminación para un perfil de memoria agresivamente óptimo.
        if (state.db) {
          try { await state.db.dropFile(table); } catch (e) { /* ignore */ }
          try { await state.db.dropFile(`${table}.arrow`); } catch (e) { /* ignore */ }
        }

        state.tables.delete(table);
        state.tableFingerprints.delete(table);
        clearTableCaches(table);
        // Desbloquear el mutex si existe (por seguridad)
        loadLocks.delete(table);
      } catch (e) {
        console.error(`⚠️ [DuckDB] Error limpiando tabla ${table}`, e);
      }
    }
  }
}

/** Limpia todas las tablas y resetea el estado */
export async function reset(): Promise<void> {
  if (state.conn) {
    for (const table of state.tables) {
      try {
        await state.conn.query(`DROP TABLE IF EXISTS "${table}"`);
        
        // 🛡️ Liberación VFS
        if (state.db) {
          try { await state.db.dropFile(table); } catch (e) { /* ignore */ }
          try { await state.db.dropFile(`${table}.arrow`); } catch (e) { /* ignore */ }
        }
        clearTableCaches(table);
      } catch { /* ignore */ }
    }
  }
  state.tables.clear();
  state.tableFingerprints.clear();
  state.filterCache.clear();
  state.arrowBinaryCache.clear();
  state.tableSchemaCache.clear();
  state.resolvedConditionCache.clear();
  loadLocks.clear();
  console.log('🦆 [DuckDB] Reset completo');
}
