#!/usr/bin/env bun
// @ts-nocheck -- standalone repair helper dynamically imports a selected GBrain runtime.
/** Restore metadata omitted by GBrain's behavioral PGLite -> Postgres migrator.
 *
 * Rows whose content hash changed after cutover are deliberately skipped. Each
 * UPDATE rechecks the source content hash so a concurrent collector cannot have
 * newer metadata overwritten by the migration snapshot.
 */
import { readFileSync, statSync } from 'fs';
import { pathToFileURL } from 'url';

const [runtime, sourcePath, targetUrlFile, acknowledgement] = process.argv.slice(2);
if (!runtime || !sourcePath || !targetUrlFile || acknowledgement !== '--owner-quiesced') {
  throw new Error(
    'usage: repair_postgres_page_metadata.ts <runtime> <source-pglite> <target-url-file> --owner-quiesced',
  );
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

const pageColumns = [
  ['created_at', 'timestamptz'], ['updated_at', 'timestamptz'], ['effective_date', 'timestamptz'],
  ['effective_date_source', 'text'], ['import_filename', 'text'], ['last_retrieved_at', 'timestamptz'],
  ['contextual_retrieval_mode', 'text'], ['corpus_generation', 'text'], ['generation', 'int8'],
  ['chunker_version', 'int2'], ['source_path', 'text'], ['ingested_via', 'text'],
  ['ingested_at', 'timestamptz'], ['source_uri', 'text'], ['source_kind', 'text'],
  ['embedding_signature', 'text'],
];

function tuples(rows, keyColumns, valueColumns) {
  const params = [];
  const sql = rows.map((row) => {
    const values = [...keyColumns, ...valueColumns].map(([name, cast]) => {
      params.push(row[name]);
      return `$${params.length}::${cast}`;
    });
    return `(${values.join(',')})`;
  });
  return { params, sql: sql.join(',') };
}

let generationTriggersDisabled = false;
try {
  // Preserve source generations exactly. GBrain's normal row/statement
  // generation triggers intentionally rewrite them on UPDATE, so maintenance
  // runs disable only those two triggers and restore them in finally.
  await target.executeRaw('ALTER TABLE pages DISABLE TRIGGER bump_page_generation_trg');
  await target.executeRaw('ALTER TABLE pages DISABLE TRIGGER bump_page_generation_clock_trg');
  generationTriggersDisabled = true;
  const sourcePages = await source.executeRaw(
    `SELECT source_id, slug, content_hash, ${pageColumns.map(([name]) => name).join(', ')} FROM pages ORDER BY source_id, slug`,
  );
  const targetPages = await target.executeRaw('SELECT source_id, slug, content_hash FROM pages');
  const targetHashes = new Map(targetPages.map((row) => [`${row.source_id}\0${row.slug}`, row.content_hash]));
  const unchangedPages = sourcePages.filter(
    (row) => targetHashes.get(`${row.source_id}\0${row.slug}`) === row.content_hash,
  );
  for (let offset = 0; offset < unchangedPages.length; offset += 100) {
    const batch = unchangedPages.slice(offset, offset + 100);
    const keyColumns = [['source_id', 'text'], ['slug', 'text'], ['content_hash', 'text']];
    const values = tuples(batch, keyColumns, pageColumns);
    const aliases = [...keyColumns, ...pageColumns].map(([name]) => name).join(',');
    const assignments = pageColumns.map(([name]) => `${name}=v.${name}`).join(',');
    await target.executeRaw(
      `UPDATE pages p SET ${assignments} FROM (VALUES ${values.sql}) AS v(${aliases})
        WHERE p.source_id=v.source_id AND p.slug=v.slug AND p.content_hash=v.content_hash`,
      values.params,
    );
  }

  const sourceChunks = await source.executeRaw(
    `SELECT p.source_id, p.slug, p.content_hash, c.chunk_index, c.embedded_at, c.created_at
       FROM content_chunks c JOIN pages p ON p.id=c.page_id
      ORDER BY p.source_id, p.slug, c.chunk_index`,
  );
  const unchangedKeys = new Set(unchangedPages.map((row) => `${row.source_id}\0${row.slug}`));
  const chunks = sourceChunks.filter((row) => unchangedKeys.has(`${row.source_id}\0${row.slug}`));
  const chunkKeys = [['source_id', 'text'], ['slug', 'text'], ['content_hash', 'text'], ['chunk_index', 'int4']];
  const chunkValues = [['embedded_at', 'timestamptz'], ['created_at', 'timestamptz']];
  for (let offset = 0; offset < chunks.length; offset += 100) {
    const batch = chunks.slice(offset, offset + 100);
    const values = tuples(batch, chunkKeys, chunkValues);
    const aliases = [...chunkKeys, ...chunkValues].map(([name]) => name).join(',');
    await target.executeRaw(
      `UPDATE content_chunks c SET embedded_at=v.embedded_at, created_at=v.created_at
         FROM pages p, (VALUES ${values.sql}) AS v(${aliases})
        WHERE c.page_id=p.id AND p.source_id=v.source_id AND p.slug=v.slug
          AND p.content_hash=v.content_hash AND c.chunk_index=v.chunk_index`,
      values.params,
    );
  }
  console.log(JSON.stringify({
    status: 'ok',
    source_pages: sourcePages.length,
    repaired_pages: unchangedPages.length,
    skipped_changed_pages: sourcePages.length - unchangedPages.length,
    repaired_chunks: chunks.length,
  }, null, 2));
} finally {
  const cleanupErrors = [];
  if (generationTriggersDisabled) {
    for (const sql of [
      'ALTER TABLE pages ENABLE TRIGGER bump_page_generation_trg',
      'ALTER TABLE pages ENABLE TRIGGER bump_page_generation_clock_trg',
    ]) {
      try {
        await target.executeRaw(sql);
      } catch (error) {
        cleanupErrors.push(error);
      }
    }
  }
  for (const engine of [target, source]) {
    try {
      await engine.disconnect();
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'metadata repair cleanup failed');
}
