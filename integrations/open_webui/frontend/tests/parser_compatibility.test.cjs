const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
// Run from the patched Open WebUI source after npm ci, before bundling.
const root = process.cwd();
const XLSX = require(path.join(root, 'node_modules/xlsx'));
test('all installed parser versions and provenance meet the remediation pins', () => {
  const lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json')));
  const views = Object.entries(lock.packages).filter(([name]) => name.endsWith('/prosemirror-view'));
  assert.equal(views.length, 1);
  assert.equal(views[0][1].version, '1.42.3');
  assert.equal(XLSX.version, '0.20.3');
  assert.equal(lock.packages['node_modules/xlsx'].resolved, 'https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz');
  assert.match(lock.packages['node_modules/xlsx'].integrity, /^sha512-/);
});
for (const bookType of ['xlsx', 'xls', 'ods']) {
  test(`${bookType} preserves workbook values through the production array parser`, () => {
    const rows = [['Magazine', 'Number', 'Boolean'], ['Maltese Ċ Ω 漢字', 42, true], ['<script>text only</script>', -1.25, false]];
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), 'Acceptance');
    const bytes = XLSX.write(wb, { type: 'array', bookType });
    const parsed = XLSX.read(new Uint8Array(bytes), { type: 'array' });
    assert.deepEqual(parsed.SheetNames, ['Acceptance']);
    assert.deepEqual(XLSX.utils.sheet_to_json(parsed.Sheets.Acceptance, { header: 1 }), rows);
    assert.match(XLSX.utils.sheet_to_csv(parsed.Sheets.Acceptance, { FS: '\t' }), /42/);
    assert.equal(Object.prototype.polluted, undefined);
  });
}
test('ESM browser module exports the APIs used by native previews', async () => {
  const { pathToFileURL } = require('node:url');
  const esm = await import(pathToFileURL(path.join(root, 'node_modules/xlsx/xlsx.mjs')).href);
  assert.equal(typeof esm.read, 'function');
  assert.equal(typeof esm.utils.sheet_to_json, 'function');
  assert.equal(typeof esm.utils.sheet_to_csv, 'function');
});
