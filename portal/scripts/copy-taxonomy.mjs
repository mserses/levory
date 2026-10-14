// Backend taksonomisini (src/serviszeka/taxonomy.json) portalın tek kaynağına kopyalar.
// `npm run gen:taxonomy` ya da `npm run sync:api`; CI kopyanın güncel olduğunu denetler.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = fileURLToPath(new URL('../../src/serviszeka/taxonomy.json', import.meta.url));
const target = fileURLToPath(new URL('../src/lib/taxonomy.json', import.meta.url));
// Bayt bayt kopya: CI `cmp` ile kaynağa eşitliği denetler
const raw = readFileSync(source, 'utf8');
JSON.parse(raw); // geçerli JSON değilse dur
writeFileSync(target, raw);
console.log(`taksonomi kopyalandı: ${target}`);
