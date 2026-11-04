// Static build only. Database migrations are an explicit, separate operation.
import { build } from 'esbuild';
import { mkdir, readFile, writeFile, cp, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const output = root+'dist/';
await rm(output,{recursive:true,force:true});
await cp(root+'public',output,{recursive:true});
await mkdir(output+'vendor',{recursive:true});
await build({ entryPoints:[root+'scripts/office-vendor.js'], outfile:output+'vendor/office.js', bundle:true, minify:true, platform:'browser', format:'iife', target:['es2022'], legalComments:'eof' });
const html=await readFile(root+'public/index.html','utf8');
const assetPattern=/(src|href)="(\/[^"?#]+\.(?:js|css))(?:\?[^"#]*)?"/g;
const hashes=new Map();
for(const [, ,asset] of html.matchAll(assetPattern)) {
  const bytes=await readFile(output+asset.slice(1));
  hashes.set(asset,createHash('sha256').update(bytes).digest('hex').slice(0,16));
}
await writeFile(output+'index.html',html.replace(assetPattern,(_,attribute,asset)=>`${attribute}="${asset}?v=${hashes.get(asset)}"`));
console.log(`Static assets and Office export bundle ready in dist; ${hashes.size} assets fingerprinted. No database changes.`);
