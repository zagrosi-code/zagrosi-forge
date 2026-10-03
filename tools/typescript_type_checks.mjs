// Compile a trusted consumer outside the editable trial, without emitting files.
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export function checkPublicTypes(workspace) {
  const compiler = fileURLToPath(new URL('./node_modules/typescript/bin/tsc', import.meta.url));
  const packageFile = new URL('./node_modules/typescript/package.json', import.meta.url);
  const expected = JSON.parse(readFileSync(new URL('./package.json', import.meta.url))).devDependencies.typescript;
  if (!existsSync(compiler) || !existsSync(packageFile) || JSON.parse(readFileSync(packageFile)).version !== expected) {
    throw new Error(`TypeScript public contract requires compiler ${expected}; run npm ci --prefix tools in the Forge checkout.`);
  }
  const imports = {
    __CANDIDATE__: path.resolve(workspace),
    __BASELINE__: fileURLToPath(new URL('../examples/evals/coding/typescript-access', import.meta.url)),
  };
  let consumer = readFileSync(new URL('./typescript_public_contract.ts', import.meta.url), 'utf8');
  for (const [marker, directory] of Object.entries(imports)) {
    consumer = consumer.replaceAll(marker, JSON.stringify(directory.split(path.sep).join('/')).slice(1, -1));
  }
  const temporary = mkdtempSync(path.join(tmpdir(), 'forge-types-'));
  try {
    const source = path.join(temporary, 'consumer.mts');
    writeFileSync(source, consumer);
    const result = spawnSync(process.execPath, [compiler, '--noEmit', '--strict', '--target', 'es2022',
      '--module', 'nodenext', '--allowImportingTsExtensions', '--pretty', 'false', source],
      { cwd: temporary, encoding: 'utf8' }); // The trial executor bounds the whole process tree.
    if (result.error || result.status !== 0) {
      throw new Error(`TypeScript public contract failed:\n${result.stdout || result.stderr || result.error || result.status}`);
    }
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
}
