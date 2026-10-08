// Keys remain in the external configuration, outside every distributable.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const python = join(homedir(), '.codex', 'runtimes', 'fmage-3.14', 'Scripts', 'python.exe');
const env = { ...process.env };
if (!env.FMAGE_PYTHON && existsSync(python)) env.FMAGE_PYTHON = python;
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(process.execPath, [join(root, 'mcp', 'server.mjs')], { cwd: root, env, stdio: 'inherit', windowsHide: true });
child.on('error', error => { console.error(error.message); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
