// Use the selected source CLI's supported package-manager injection. Its normal
// argument parser, profile locking and compatibility checks remain in charge.
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [source, ...args] = process.argv.slice(2);
if (!source || args[0] !== 'plugin' || args[1] !== '--profile' || args[2] !== 'desktop') {
  throw new Error('Expected source root followed by plugin --profile desktop arguments.');
}
const entry = join(source, 'apps/cli/src/bin.ts');
const { runCli } = await import(pathToFileURL(entry).href);
process.argv = [process.execPath, entry, ...args];
await runCli({
  packageManager: {
    command: process.execPath,
    args: [join(source, 'node_modules/pnpm/bin/pnpm.cjs')],
  },
});
