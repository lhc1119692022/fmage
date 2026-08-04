#!/usr/bin/env node
/** Refresh Pi MCP Adapter's cached Fmage metadata from the live local server. */

import { readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";


function parseArgs() {
  const args = process.argv.slice(2);
  const index = args.indexOf("--agent-dir");
  return {
    agentDir: resolve(index >= 0 ? args[index + 1] : join(homedir(), ".pi", "agent")),
  };
}


function stringEnvironment(extra = {}) {
  return Object.fromEntries(
    Object.entries({ ...process.env, ...extra }).filter(([, value]) => typeof value === "string"),
  );
}


function serializeTools(tools = []) {
  return tools.filter((tool) => tool?.name).map((tool) => ({
    name: tool.name,
    description: tool.description,
    inputSchema: tool.inputSchema,
    ...(tool._meta?.ui?.resourceUri ? { uiResourceUri: tool._meta.ui.resourceUri } : {}),
  }));
}


async function optionalList(call, key) {
  try {
    const result = await call();
    return Array.isArray(result?.[key]) ? result[key] : [];
  } catch {
    return [];
  }
}


async function main() {
  const { agentDir } = parseArgs();
  const configPath = join(agentDir, "mcp.json");
  const cachePath = join(agentDir, "mcp-cache.json");
  const config = JSON.parse(await readFile(configPath, "utf8"));
  const definition = config?.mcpServers?.Fmage;
  if (!definition?.command) {
    throw new Error("Pi mcp.json does not define the local stdio Fmage server");
  }

  const moduleRoot = join(agentDir, "npm", "node_modules");
  const [{ Client }, { StdioClientTransport }] = await Promise.all([
    import(pathToFileURL(join(moduleRoot, "@modelcontextprotocol", "sdk", "dist", "esm", "client", "index.js"))),
    import(pathToFileURL(join(moduleRoot, "@modelcontextprotocol", "sdk", "dist", "esm", "client", "stdio.js"))),
  ]);

  const client = new Client({ name: "fmage-pi-compat-sync", version: "1.0.0" });
  const transport = new StdioClientTransport({
    command: definition.command,
    args: definition.args ?? [],
    cwd: definition.cwd ? resolve(dirname(configPath), definition.cwd) : dirname(configPath),
    env: stringEnvironment(definition.env),
    stderr: "pipe",
  });

  try {
    await client.connect(transport);
    const [toolsResult, resources, prompts] = await Promise.all([
      client.listTools(),
      optionalList(() => client.listResources(), "resources"),
      optionalList(() => client.listPrompts(), "prompts"),
    ]);
    const cache = JSON.parse(await readFile(cachePath, "utf8"));
    const existing = cache?.servers?.Fmage;
    if (!existing?.configHash) {
      throw new Error("Pi mcp-cache.json has no existing Fmage configHash to preserve");
    }
    cache.version = 1;
    cache.servers.Fmage = {
      configHash: existing.configHash,
      tools: serializeTools(toolsResult.tools),
      resources,
      prompts,
      instructions: client.getInstructions?.(),
      cachedAt: Date.now(),
    };
    const temporaryPath = `${cachePath}.${process.pid}.tmp`;
    await writeFile(temporaryPath, `${JSON.stringify(cache, null, 2)}\n`, "utf8");
    await rename(temporaryPath, cachePath);
    console.log(`Refreshed Pi Fmage MCP cache with ${toolsResult.tools.length} live tools.`);
  } finally {
    await client.close();
  }
}


main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
