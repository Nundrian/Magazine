// Regenerate synthetic UI request fixtures after an intentional Function edit.
const fs = require('node:fs');
const path = require('node:path');
const c21 = require('./c21_prepare_requests.cjs');
const c23 = require('./c23_tool_requests.cjs');

async function main() {
  const output = path.join(__dirname, 'fixtures');
  fs.mkdirSync(output, { recursive: true });
  const fixtures = [
    ['c21-ui-requests.json', await c21.capture()],
    ['c23-tool-requests.json', await c23.capture()],
  ];
  for (const [name, value] of fixtures) {
    fs.writeFileSync(path.join(output, name), JSON.stringify(value, null, 2) + '\n');
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
