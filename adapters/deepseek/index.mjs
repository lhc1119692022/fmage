// Native desktop Cordis bundle; no shared filesystem skill registration.
import { readFileSync, writeFileSync, watchFile, unwatchFile, renameSync, unlinkSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { BUNDLED_SKILL_RANK } from '@deepseek-ai/dsh-skill';
import * as mcpClient from '@deepseek-ai/dsh-mcp-client';

export const name = 'fmage';
export const inject = ['skills', 'tools'];
const root = fileURLToPath(new URL('./', import.meta.url));

export async function apply(ctx) {
  ctx.skills.registerProvider(control => {
    const marker = join(root, '.fmage-build.json');
    const changed = () => control.invalidate();
    watchFile(marker, { interval: 1500, persistent: false }, changed);
    control.signal.addEventListener('abort', () => unwatchFile(marker, changed), { once: true });
    return {
      name: 'fmage-desktop',
      async list() {
        const catalog = JSON.parse(await readFile(join(root, 'catalog.json'), 'utf8'));
        return catalog.map(skill => ({
          name: skill.name, description: skill.description,
          invocation: { modelInvocable: skill.modelInvocable, userInvocable: true },
          provider: 'fmage-desktop', source: 'bundled', rank: BUNDLED_SKILL_RANK,
          resourceBase: { kind: 'directory', path: join(root, 'skills', skill.name) },
          locator: join(root, 'skills', skill.name, 'SKILL.md'),
        }));
      },
      async get(candidate, options) {
        const { rank, locator, ...summary } = candidate;
        const text = await readFile(locator, { encoding: 'utf8', signal: options.signal });
        return { ...summary, content: text.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '') };
      },
    };
  });
  await ctx.plugin(mcpClient, {
    transport: 'stdio', serverName: 'Fmage', command: 'node',
    args: [join(root, 'mcp', 'launch.mjs')], cwd: root,
    toolCallTimeoutMs: 1800000, failOnStartupError: true,
    reconnect: { enabled: false },
  });
  const { version, sha256 } = JSON.parse(readFileSync(join(root, '.fmage-build.json'), 'utf8'));
  const receipt = join(root, '.fmage-runtime.json');
  const loadedAt = new Date().toISOString();
  const report = () => {
    const toolCount = [...ctx.tools.view().visible.keys()].filter(n => n.startsWith('mcp__Fmage__')).length;
    writeFileSync(receipt + '.tmp', JSON.stringify({ version, sha256, pid: process.pid, loadedAt,
      heartbeatAt: new Date().toISOString(), skillCount: 5, toolCount, mcpReady: toolCount === 10 }));
    renameSync(receipt + '.tmp', receipt);
  };
  report();
  ctx.effect(() => {
    const timer = setInterval(() => { try { report(); } catch (error) { ctx.logger.warn(`Fmage runtime receipt: ${error.message}`); } }, 5000);
    timer.unref();
    return () => {
      clearInterval(timer);
      try { if (JSON.parse(readFileSync(receipt, 'utf8')).pid === process.pid) unlinkSync(receipt); }
      catch (error) { if (error.code !== 'ENOENT') ctx.logger.warn(`Fmage runtime receipt cleanup: ${error.message}`); }
    };
  });
  ctx.logger.info(`Fmage ${version}: five native skills and MCP tools loaded`);
}
