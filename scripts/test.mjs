import { readdir } from 'node:fs/promises';
import { spawn } from 'node:child_process';
async function files(dir) {const entries=await readdir(dir,{withFileTypes:true});return (await Promise.all(entries.map(e=>e.isDirectory()?files(dir+'/'+e.name):/\.test\.(mjs|cjs|js)$/.test(e.name)?[dir+'/'+e.name]:[]))).flat();}
const tests=(await files('tests')).sort();
if(!tests.length)throw new Error('No JavaScript tests discovered');
const child=spawn(process.execPath,['--test',...tests],{stdio:'inherit'});
child.on('exit',code=>process.exit(code || 0));child.on('error',e=>{console.error(e.message);process.exit(1);});
