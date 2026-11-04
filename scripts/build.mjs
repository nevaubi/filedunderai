// Static build only. Database migrations are an explicit, separate operation.
import { build } from 'esbuild';
import { mkdir, readFile, access } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
await mkdir(root + 'public/vendor', { recursive: true });
await build({ entryPoints:[root+'scripts/office-vendor.js'], outfile:root+'public/vendor/office.js', bundle:true, minify:true, platform:'browser', format:'iife', target:['es2022'], legalComments:'eof' });
const html=await readFile(root+'public/index.html','utf8');
for(const [,asset] of html.matchAll(/(?:src|href)="(\/(?:[^"?#]+\.(?:js|css)))"/g)) await access(root+'public'+asset);
console.log('Static assets and Office export bundle ready. No database changes.');
