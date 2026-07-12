#!/usr/bin/env bun
// @ts-nocheck -- standalone Bun verifier dynamically imports a selected GBrain runtime.
/** Produce content-safe exact parity receipts for PGLite -> PostgreSQL migration. */
import { createHash } from 'crypto';
import { readFileSync, statSync } from 'fs';
import { pathToFileURL } from 'url';

const [runtime, sourcePath, targetUrlFile] = process.argv.slice(2);
if (!runtime || !sourcePath || !targetUrlFile) {
  throw new Error('usage: verify_postgres_parity.ts <runtime> <source-pglite> <target-url-file>');
}
const { PGLiteEngine } = await import(pathToFileURL(`${runtime}/src/core/pglite-engine.ts`).href);
const { PostgresEngine } = await import(pathToFileURL(`${runtime}/src/core/postgres-engine.ts`).href);
const source = new PGLiteEngine();
const target = new PostgresEngine();
if (statSync(targetUrlFile).mode & 0o077) throw new Error('target URL file must not be accessible to group or other users');
const targetUrl = readFileSync(targetUrlFile, 'utf8').trim();
if (!targetUrl.startsWith('postgresql://')) throw new Error('invalid target URL');
await source.connect({ engine: 'pglite', database_path: sourcePath });
await target.connect({ engine: 'postgres', database_url: targetUrl });

function normalized(value: unknown): unknown {
  if (value instanceof Date) return value.toISOString();
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$/.test(value)) {
    return new Date(value).toISOString();
  }
  if (typeof value === 'string' && /^[\[{]/.test(value.trim())) {
    try {
      return normalized(JSON.parse(value));
    } catch {
      // Ordinary text that begins with a bracket remains ordinary text.
    }
  }
  if (Array.isArray(value)) return value.map(normalized);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).sort().map(([k, v]) => [k, normalized(v)]));
  }
  if (typeof value === 'bigint') return value.toString();
  return value;
}
function digest(rows: unknown[]): string {
  const hash = createHash('sha256');
  for (const row of rows) hash.update(JSON.stringify(normalized(row)) + '\n');
  return hash.digest('hex');
}

async function fullColumnParity(table: 'pages' | 'content_chunks') {
  const columns = await source.executeRaw<{ column_name: string; data_type: string; is_generated: string }>(
    `SELECT column_name, data_type, is_generated
       FROM information_schema.columns
      WHERE table_schema='public' AND table_name=$1
      ORDER BY ordinal_position`,
    [table],
  );
  const excluded = new Set(table === 'pages' ? ['id', 'search_vector'] : ['id', 'page_id', 'search_vector']);
  const checked = columns.filter(
    (column) => column.is_generated === 'NEVER'
      && /^[a-z_][a-z0-9_]*$/.test(column.column_name)
      && !excluded.has(column.column_name),
  );
  const mismatches: Record<string, number> = {};
  for (const column of checked) {
    const prefix = table === 'pages' ? '' : 'c.';
    const value = column.data_type.includes('timestamp')
      ? `CASE WHEN ${prefix}${column.column_name} IS NULL THEN '<NULL>'
              ELSE round(extract(epoch from ${prefix}${column.column_name})*1000000)::bigint::text END`
      : `COALESCE(${prefix}${column.column_name}::text,'<NULL>')`;
    const sql = table === 'pages'
      ? `SELECT source_id, slug, md5(${value}) AS value_hash FROM pages`
      : `SELECT p.source_id, p.slug, c.chunk_index, md5(${value}) AS value_hash
           FROM content_chunks c JOIN pages p ON p.id=c.page_id`;
    const [sourceRows, targetRows] = await Promise.all([source.executeRaw(sql), target.executeRaw(sql)]);
    const key = (row: Record<string, unknown>) => table === 'pages'
      ? `${row.source_id}\0${row.slug}`
      : `${row.source_id}\0${row.slug}\0${row.chunk_index}`;
    const sourceMap = new Map(sourceRows.map((row) => [key(row), row.value_hash]));
    const targetMap = new Map(targetRows.map((row) => [key(row), row.value_hash]));
    const keys = new Set([...sourceMap.keys(), ...targetMap.keys()]);
    let mismatchCount = 0;
    for (const naturalKey of keys) {
      if (sourceMap.get(naturalKey) !== targetMap.get(naturalKey)) mismatchCount += 1;
    }
    if (mismatchCount) mismatches[column.column_name] = mismatchCount;
  }
  return {
    checked_columns: checked.map((column) => column.column_name),
    mismatch_columns: Object.keys(mismatches),
    mismatches,
    match: Object.keys(mismatches).length === 0,
  };
}

const checks = {
  pages: `SELECT source_id, slug, type, title, content_hash, deleted_at IS NOT NULL AS deleted
            FROM pages ORDER BY source_id, slug`,
  chunks: `SELECT p.source_id, p.slug, c.chunk_index, md5(c.chunk_text) AS text_hash,
                  c.chunk_source, c.model, c.token_count,
                  CASE WHEN c.embedding IS NULL THEN NULL ELSE md5(c.embedding::text) END AS embedding_hash,
                  CASE WHEN c.embedding IS NULL THEN NULL ELSE vector_dims(c.embedding) END AS embedding_dims
             FROM content_chunks c JOIN pages p ON p.id=c.page_id
            ORDER BY p.source_id, p.slug, c.chunk_index`,
  tags: `SELECT p.source_id, p.slug, t.tag
           FROM tags t JOIN pages p ON p.id=t.page_id
          ORDER BY p.source_id, p.slug, t.tag`,
  links: `SELECT pf.source_id AS from_source, pf.slug AS from_slug,
                 pt.source_id AS to_source, pt.slug AS to_slug,
                 l.link_type, l.context, l.link_source, l.origin_field
            FROM links l JOIN pages pf ON pf.id=l.from_page_id JOIN pages pt ON pt.id=l.to_page_id
           ORDER BY 1,2,3,4,5,6,7,8`,
  page_versions: `SELECT p.source_id, p.slug, v.compiled_truth, v.frontmatter::text AS frontmatter, v.snapshot_at
                     FROM page_versions v JOIN pages p ON p.id=v.page_id
                    ORDER BY p.source_id, p.slug, v.snapshot_at, v.id`,
  sources: `SELECT id, name, local_path, last_commit, last_sync_at, config::text AS config, archived,
                   archived_at, archive_expires_at, contextual_retrieval_mode,
                   trust_frontmatter_overrides, newest_content_at, created_at, chunker_version
              FROM sources ORDER BY id`,
};

try {
  const result: Record<string, unknown> = { status: 'ok', checks: {} };
  for (const [name, sql] of Object.entries(checks)) {
    const sourceRows = await source.executeRaw(sql);
    const targetRows = await target.executeRaw(sql);
    const sourceDigest = digest(sourceRows);
    const targetDigest = digest(targetRows);
    (result.checks as Record<string, unknown>)[name] = {
      source_count: sourceRows.length,
      target_count: targetRows.length,
      source_digest: sourceDigest,
      target_digest: targetDigest,
      match: sourceRows.length === targetRows.length && sourceDigest === targetDigest,
    };
    if (sourceRows.length !== targetRows.length || sourceDigest !== targetDigest) result.status = 'blocked';
  }
  const fullColumnParityResult = {
    pages: await fullColumnParity('pages'),
    content_chunks: await fullColumnParity('content_chunks'),
  };
  result.full_column_parity = fullColumnParityResult;
  if (!fullColumnParityResult.pages.match || !fullColumnParityResult.content_chunks.match) {
    result.status = 'blocked';
  }
  const sourceIdentity = await source.executeRaw('SELECT brain_id FROM brain_identity WHERE singleton=TRUE');
  const targetIdentity = await target.executeRaw('SELECT brain_id FROM brain_identity WHERE singleton=TRUE');
  result.brain_identity_match = digest(sourceIdentity) === digest(targetIdentity);
  if (!result.brain_identity_match) result.status = 'blocked';
  const dimensions = await target.executeRaw(
    `SELECT count(*)::int AS embedded,
            count(*) FILTER (WHERE vector_dims(embedding) <> 1280)::int AS wrong_width
       FROM content_chunks WHERE embedding IS NOT NULL`,
  );
  result.target_embeddings = dimensions[0];
  if (Number(dimensions[0]?.wrong_width ?? 0) !== 0) result.status = 'blocked';
  console.log(JSON.stringify(result, null, 2));
  if (result.status !== 'ok') process.exitCode = 1;
} finally {
  await target.disconnect();
  await source.disconnect();
}
