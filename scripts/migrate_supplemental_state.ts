#!/usr/bin/env bun
// @ts-nocheck -- standalone Bun migration helper dynamically imports a selected GBrain runtime.
/** Copy state that `gbrain migrate --to supabase` does not preserve.
 *
 * The canonical page/chunk/tag/link transfer must run first. This helper then
 * preserves logical brain identity, source registry, page-version history,
 * authentication continuity, and local audit ledgers without printing secret
 * values. Derived query cache rows are intentionally not copied.
 */
import { readFileSync, statSync } from 'fs';
import { pathToFileURL } from 'url';

const [runtime, sourcePath, targetUrlFile, acknowledgement] = process.argv.slice(2);
if (!runtime || !sourcePath || !targetUrlFile || acknowledgement !== '--offline-target') {
  throw new Error(
    'usage: migrate_supplemental_state.ts <runtime> <source-pglite> <target-url-file> --offline-target',
  );
}
const pgliteModule = await import(pathToFileURL(`${runtime}/src/core/pglite-engine.ts`).href);
const postgresModule = await import(pathToFileURL(`${runtime}/src/core/postgres-engine.ts`).href);
const source = new pgliteModule.PGLiteEngine();
const target = new postgresModule.PostgresEngine();
if (statSync(targetUrlFile).mode & 0o077) throw new Error('target URL file must not be accessible to group or other users');
const targetUrl = readFileSync(targetUrlFile, 'utf8').trim();
if (!targetUrl.startsWith('postgresql://')) throw new Error('invalid target URL');
await source.connect({ engine: 'pglite', database_path: sourcePath });
await target.connect({ engine: 'postgres', database_url: targetUrl });

type Column = { column_name: string; data_type: string; udt_name: string };
type Row = Record<string, unknown>;

function encoded(value: unknown, column: Column, index: number): { sql: string; value: unknown } {
  if (column.data_type === 'jsonb' || column.data_type === 'json') {
    let jsonValue = value ?? null;
    if (typeof jsonValue === 'string') {
      for (let depth = 0; depth < 3 && typeof jsonValue === 'string'; depth++) {
        try {
          jsonValue = JSON.parse(jsonValue);
        } catch {
          // A legitimate JSON string scalar remains a string.
          break;
        }
      }
    }
    return { sql: `$${index}::jsonb`, value: jsonValue };
  }
  if (column.data_type === 'ARRAY') {
    const escape = (item: unknown) => `"${String(item).replaceAll('\\', '\\\\').replaceAll('"', '\\"')}"`;
    let literal: string;
    if (Array.isArray(value)) {
      literal = `{${value.map(escape).join(',')}}`;
    } else if (typeof value === 'string' && value.startsWith('{') && value.endsWith('}')) {
      literal = value;
    } else if (typeof value === 'string' && value.startsWith('[')) {
      const parsed = JSON.parse(value);
      literal = `{${(Array.isArray(parsed) ? parsed : [parsed]).map(escape).join(',')}}`;
    } else {
      literal = value == null ? '{}' : `{${escape(value)}}`;
    }
    return { sql: `$${index}::text[]`, value: literal };
  }
  if (column.data_type === 'bigint' || column.data_type === 'numeric') {
    return { sql: `$${index}`, value: value == null ? null : String(value) };
  }
  return { sql: `$${index}`, value };
}

async function columnsFor(table: string): Promise<Column[]> {
  return await target.executeRaw<Column>(
    `SELECT column_name, data_type, udt_name
       FROM information_schema.columns
      WHERE table_schema='public' AND table_name=$1
      ORDER BY ordinal_position`,
    [table],
  );
}

async function copyTable(table: string, conflictColumns: string[] = []): Promise<number> {
  if (!/^[a-z_][a-z0-9_]*$/.test(table)) throw new Error(`unsafe table ${table}`);
  const columns = await columnsFor(table);
  const names = columns.map((column) => column.column_name);
  const rows = await source.executeRaw<Row>(`SELECT * FROM "${table}"`);
  const chunkSize = 100;
  for (let offset = 0; offset < rows.length; offset += chunkSize) {
    const batch = rows.slice(offset, offset + chunkSize);
    const params: unknown[] = [];
    const tuples = batch.map((row) => {
      const expressions = columns.map((column) => {
        const item = encoded(row[column.column_name], column, params.length + 1);
        params.push(item.value);
        return item.sql;
      });
      return `(${expressions.join(',')})`;
    });
    const quoted = names.map((name) => `"${name}"`).join(',');
    const update = conflictColumns.length
      ? `ON CONFLICT (${conflictColumns.map((name) => `"${name}"`).join(',')}) DO UPDATE SET ${names
          .filter((name) => !conflictColumns.includes(name))
          .map((name) => `"${name}"=EXCLUDED."${name}"`)
          .join(',')}`
      : 'ON CONFLICT DO NOTHING';
    await target.executeRaw(`INSERT INTO "${table}" (${quoted}) VALUES ${tuples.join(',')} ${update}`, params);
  }
  return rows.length;
}

async function copyPageVersions(): Promise<number> {
  const rows = await source.executeRaw<Row>(
    `SELECT p.source_id, p.slug, v.compiled_truth, v.frontmatter, v.snapshot_at
       FROM page_versions v JOIN pages p ON p.id=v.page_id
      ORDER BY v.id`,
  );
  const targetPages = await target.executeRaw<{ id: number; source_id: string; slug: string }>(
    'SELECT id, source_id, slug FROM pages',
  );
  const pageIds = new Map(targetPages.map((page) => [`${page.source_id}::${page.slug}`, page.id]));
  const columns: Column[] = [
    { column_name: 'page_id', data_type: 'integer', udt_name: 'int4' },
    { column_name: 'compiled_truth', data_type: 'text', udt_name: 'text' },
    { column_name: 'frontmatter', data_type: 'jsonb', udt_name: 'jsonb' },
    { column_name: 'snapshot_at', data_type: 'timestamp with time zone', udt_name: 'timestamptz' },
  ];
  const normalized = rows.map((row) => {
    const pageId = pageIds.get(`${row.source_id}::${row.slug}`);
    if (pageId == null) throw new Error(`missing target page for version ${row.source_id}::${row.slug}`);
    return { page_id: pageId, compiled_truth: row.compiled_truth, frontmatter: row.frontmatter, snapshot_at: row.snapshot_at };
  });
  // The built-in engine migration does not copy page versions. Replacing the
  // target set makes this supplemental pass resumable after a later-table
  // failure without duplicating history.
  await target.executeRaw('DELETE FROM page_versions');
  for (let offset = 0; offset < normalized.length; offset += 100) {
    const params: unknown[] = [];
    const tuples = normalized.slice(offset, offset + 100).map((row) => {
      const expressions = columns.map((column) => {
        const item = encoded(row[column.column_name as keyof typeof row], column, params.length + 1);
        params.push(item.value);
        return item.sql;
      });
      return `(${expressions.join(',')})`;
    });
    await target.executeRaw(
      `INSERT INTO page_versions (page_id,compiled_truth,frontmatter,snapshot_at) VALUES ${tuples.join(',')}`,
      params,
    );
  }
  return normalized.length;
}

try {
  const counts: Record<string, number> = {};
  counts.sources = await copyTable('sources', ['id']);

  const identities = await source.executeRaw<{ brain_id: string; created_at: unknown }>(
    'SELECT brain_id, created_at FROM brain_identity WHERE singleton=TRUE',
  );
  if (identities.length !== 1) throw new Error('source brain identity singleton is missing');
  await target.executeRaw(
    'UPDATE brain_identity SET brain_id=$1, created_at=$2 WHERE singleton=TRUE',
    [identities[0].brain_id, identities[0].created_at],
  );
  counts.brain_identity = 1;

  counts.config = await copyTable('config', ['key']);
  counts.page_versions = await copyPageVersions();
  counts.oauth_clients = await copyTable('oauth_clients', ['client_id']);
  counts.oauth_tokens = await copyTable('oauth_tokens', ['token_hash']);
  counts.access_tokens = await copyTable('access_tokens', ['id']);
  counts.ingest_log = await copyTable('ingest_log', ['id']);
  counts.mcp_request_log = await copyTable('mcp_request_log', ['id']);
  counts.search_telemetry = await copyTable('search_telemetry', ['date', 'mode', 'intent']);
  counts.page_generation_clock = await copyTable('page_generation_clock', ['id']);

  for (const table of ['page_versions', 'ingest_log', 'mcp_request_log']) {
    await target.executeRaw(
      `SELECT setval(pg_get_serial_sequence('${table}','id'), GREATEST(COALESCE((SELECT max(id) FROM "${table}"),1),1), true)`,
    );
  }
  console.log(JSON.stringify({ status: 'ok', copied: counts, intentionally_omitted: { query_cache: 'derived' } }, null, 2));
} finally {
  await target.disconnect();
  await source.disconnect();
}
