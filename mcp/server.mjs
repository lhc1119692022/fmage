import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { access, copyFile, mkdir, mkdtemp, readFile, readdir, realpath, rename, rm, rmdir, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { basename, dirname, extname, isAbsolute, join, relative, resolve } from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const SERVER_NAME = "Fmage";
const SERVER_VERSION = "0.1.0";
const TOOL_GENERATE = "generate_image";
const TOOL_EDIT = "edit_image";
const TOOL_GENERATE_BATCH = "generate_image_batch";
const TOOL_EDIT_BATCH = "edit_image_batch";
const PROMPT_PROFILE_DALLE3 = "dall-e3";
const DALLE3_DEFAULT_PROMPT_POLICY = Object.freeze({
  max_chars: 4000,
  target_chars: 3900,
  overflow_strategy: "language_aware_compact",
  preferred_compact_language: "zh-CN",
});
const TOOL_PREPARE_PROMPT_DALLE3 = "prepare_prompt_dalle3";
const TOOL_TRACE_PLAN = "trace_image_job_plan";
const TOOL_STATUS = "get_provider_status";
const TOOL_TASK_STATUS = "get_image_task_status";
const TOOL_REGRESS = "regress_image";
const BASE_INITIALIZE_INSTRUCTIONS =
  "Use Fmage image tools. Complete the unrestricted prompt with the active model before provider adaptation. " +
  "For edits, identify reference-image roles and preserve required text, layout, and other locked details.";
const MAX_EMBEDDED_IMAGE_BYTES = 20 * 1024 * 1024;
const MAX_CHILD_OUTPUT_BYTES = 2 * 1024 * 1024;
const DEFAULT_HELPER_TIMEOUT_SECONDS = 360;
const HELPER_TIMEOUT_GRACE_SECONDS = 60;
const BATCH_FOREGROUND_WAIT_SECONDS = 240;
const PENDING_TOTAL_TIMEOUT_SECONDS = 500;
const PENDING_POLL_FAST_WINDOW_SECONDS = 120;
const PENDING_POLL_FAST_INTERVAL_SECONDS = 20;
const PENDING_POLL_SLOW_INTERVAL_SECONDS = 45;
const DALLE3_PROMPT_STAGE_TTL_MS = 10 * 60 * 1000;
const PLUGIN_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG_PATH =
  process.env.FMAGE_CONFIG ||
  join(process.env.CODEX_HOME || join(homedir(), ".codex"), "fmage", "providers.json");
const TRANSPORT_OPENAI_IMAGES = "openai-images";
const TRANSPORT_PROFILE_808 = "808";
const TRANSPORT_EZAI_BANANA_IMAGES = "ezai-banana-images";
const OPENAI_IMAGES_808_DEFAULT_TIMEOUT_SECONDS = 600;
const OPENAI_IMAGES_808_DEFAULT_RESPONSE_FORMAT = "url";
const OPENAI_IMAGES_808_SUPPORTED_MODELS = new Set(["gpt-image-2", "gpt-image-2-token"]);
const EZAI_BANANA_DEFAULT_RESPONSE_FORMAT = "url";
const EZAI_BANANA_SUPPORTED_RESPONSE_FORMATS = new Set(["url", "b64_json"]);

const TRANSPORTS = {
  [TRANSPORT_OPENAI_IMAGES]: join(PLUGIN_ROOT, "scripts", "openai_images_transport.py"),
  [TRANSPORT_EZAI_BANANA_IMAGES]: join(
    PLUGIN_ROOT,
    "scripts",
    "ezai_banana_transport.py",
  ),
  "zenmux-vertex": join(PLUGIN_ROOT, "scripts", "zenmux_vertex_transport.py"),
};
const TRANSPORT_PROFILE_SCRIPTS = {
  [TRANSPORT_PROFILE_808]: join(PLUGIN_ROOT, "scripts", "openai_images_808_transport.py"),
};
const REGRESSION_SCRIPT = join(
  PLUGIN_ROOT,
  "skills",
  "fmage-image-regression",
  "scripts",
  "degrade_image.py",
);

const JsonRpcError = {
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
};

const dalle3PromptStages = new Map();

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function sendResult(id, result) {
  send({ jsonrpc: "2.0", id, result });
}

function sendError(id, code, message) {
  send({ jsonrpc: "2.0", id, error: { code, message } });
}

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function unicodeCharacterCount(value) {
  return Array.from(value).length;
}

function promptLanguageProfile(value) {
  const hanCharacters = value.match(/\p{Script=Han}/gu)?.length ?? 0;
  const latinCharacters = value.match(/\p{Script=Latin}/gu)?.length ?? 0;
  let dominantLanguage = "mixed_or_other";
  if (latinCharacters > hanCharacters) dominantLanguage = "english_dominant";
  if (hanCharacters > latinCharacters) dominantLanguage = "chinese_dominant";
  return {
    dominant_language: dominantLanguage,
    han_characters: hanCharacters,
    latin_characters: latinCharacters,
  };
}

function newDalle3PromptStageId() {
  return randomBytes(32).toString("hex");
}

function cleanupDalle3PromptStages(now = Date.now()) {
  for (const [id, stage] of dalle3PromptStages.entries()) {
    if (!stage || stage.expiresAtMs <= now) dalle3PromptStages.delete(id);
  }
}

function getDalle3PromptStage(id, expectedKind = null) {
  cleanupDalle3PromptStages();
  const supplied = nonEmptyString(id);
  const stage = supplied ? dalle3PromptStages.get(supplied) : null;
  if (!stage || (expectedKind && stage.kind !== expectedKind)) {
    throw new Error(
      `The ${expectedKind === "pending" ? "prompt_session_id" : "prompt_check_id"} is invalid or expired. ` +
        `No provider request has been sent; prepare the prompt again.`,
    );
  }
  return { id: supplied, stage };
}

function stagePendingDalle3Prompt(
  sourcePrompt,
  sourcePromptChars,
  sourceLanguage,
  policy,
  provider,
  previous = null,
) {
  cleanupDalle3PromptStages();
  const now = Date.now();
  const id = previous?.id ?? newDalle3PromptStageId();
  const sourcePreparedAt = previous?.stage?.sourcePreparedAt ?? isoNow();
  const preparationCallCount = (previous?.stage?.preparationCallCount ?? 0) + 1;
  dalle3PromptStages.set(id, {
    kind: "pending",
    sourcePrompt,
    sourcePromptChars,
    sourceLanguage,
    promptPolicy: policy,
    providerName: provider.name,
    transport: provider.transport,
    transportProfile: provider.transportProfile ?? null,
    promptProfile: provider.promptProfile ?? PROMPT_PROFILE_DALLE3,
    sourcePreparedAt,
    preparationCallCount,
    expiresAtMs: now + DALLE3_PROMPT_STAGE_TTL_MS,
  });
  return { id, sourcePreparedAt, preparationCallCount };
}

function stageReadyDalle3Prompt(promptResolution, provider, previous = null) {
  cleanupDalle3PromptStages();
  const now = Date.now();
  const id = newDalle3PromptStageId();
  const sourcePreparedAt = previous?.stage?.sourcePreparedAt ?? isoNow();
  const preparationCallCount = (previous?.stage?.preparationCallCount ?? 0) + 1;
  const transportReadyAt = isoNow();
  const stagedResolution = {
    ...promptResolution,
    providerName: provider.name,
    transport: provider.transport,
    transportProfile: provider.transportProfile ?? null,
    promptProfile: provider.promptProfile ?? PROMPT_PROFILE_DALLE3,
    promptPreparation: {
      mode: "staged_transport",
      source_prepared_at: sourcePreparedAt,
      transport_ready_at: transportReadyAt,
      preparation_call_count: preparationCallCount,
      ttl_seconds: DALLE3_PROMPT_STAGE_TTL_MS / 1000,
    },
  };
  dalle3PromptStages.set(id, {
    kind: "ready",
    promptResolution: stagedResolution,
    expiresAtMs: now + DALLE3_PROMPT_STAGE_TTL_MS,
  });
  if (previous?.id) dalle3PromptStages.delete(previous.id);
  return { id, promptResolution: stagedResolution };
}

function takeReadyDalle3Prompt(promptCheckId) {
  const { id, stage } = getDalle3PromptStage(promptCheckId, "ready");
  dalle3PromptStages.delete(id);
  return stage.promptResolution;
}

function normalizePromptPolicy(rawPolicy, context = "prompt_policy") {
  if (rawPolicy === undefined || rawPolicy === null) return null;
  if (typeof rawPolicy !== "object" || Array.isArray(rawPolicy)) {
    throw new Error(`${context} must be an object.`);
  }

  const maxChars = positiveInteger(rawPolicy.max_chars, 0);
  if (!maxChars) throw new Error(`${context}.max_chars must be a positive integer.`);

  const targetChars = positiveInteger(rawPolicy.target_chars, maxChars);
  if (targetChars > maxChars) {
    throw new Error(`${context}.target_chars must not exceed max_chars.`);
  }

  const overflowStrategy = nonEmptyString(rawPolicy.overflow_strategy) || "semantic_compact";
  if (!["language_aware_compact", "semantic_compact"].includes(overflowStrategy)) {
    throw new Error(
      `${context}.overflow_strategy must be "language_aware_compact" or "semantic_compact".`,
    );
  }

  const preferredCompactLanguage = nonEmptyString(rawPolicy.preferred_compact_language);
  return {
    max_chars: maxChars,
    target_chars: targetChars,
    overflow_strategy: overflowStrategy,
    preferred_compact_language: preferredCompactLanguage ?? undefined,
    count: "unicode_code_points",
  };
}

function normalizeTransportProfile(value) {
  const configured = nonEmptyString(value);
  if (!configured) return null;
  if (configured === TRANSPORT_PROFILE_808) return TRANSPORT_PROFILE_808;
  throw new Error(`Unsupported transport_profile "${value}". Use "${TRANSPORT_PROFILE_808}".`);
}

function normalizePromptProfile(value) {
  const configured = nonEmptyString(value);
  if (!configured) return null;
  if (configured === PROMPT_PROFILE_DALLE3) return PROMPT_PROFILE_DALLE3;
  throw new Error(`Unsupported prompt_profile "${value}". Use "${PROMPT_PROFILE_DALLE3}".`);
}

function isDalle3PromptPolicyProvider(provider) {
  return Boolean(provider?.dalle3PromptProfile && provider?.promptPolicy);
}

function resolveProviderPrompt(args, provider) {
  if (!isDalle3PromptPolicyProvider(provider)) {
    throw new Error(
      `Provider prompt preparation is only available when prompt_profile is "${PROMPT_PROFILE_DALLE3}".`,
    );
  }
  if (args._prompt_resolution) return args._prompt_resolution;
  if (args._dalle3_prompt_check_required && nonEmptyString(args.prompt_check_id)) {
    return takeReadyDalle3Prompt(args.prompt_check_id);
  }
  const sourcePrompt = nonEmptyString(args.prompt);
  if (!sourcePrompt) {
    throw new Error(
      `Provide either the complete unrestricted prompt for the initial prompt-profile call ` +
        `or a valid prompt_check_id returned by ${TOOL_PREPARE_PROMPT_DALLE3}.`,
    );
  }

  const sourcePromptChars = unicodeCharacterCount(sourcePrompt);
  const policy = provider.promptPolicy;
  if (!policy || sourcePromptChars <= policy.max_chars) {
    return {
      sourcePrompt,
      submittedPrompt: sourcePrompt,
      sourcePromptChars,
      submittedPromptChars: sourcePromptChars,
      promptPolicy: policy,
      promptPolicyApplied: false,
      promptPreparation: {
        mode: "direct_source_within_limit",
        preparation_call_count: 0,
      },
    };
  }

  const providerPrompt = nonEmptyString(args.provider_prompt);
  if (!providerPrompt) {
    const languageGuidance =
      policy.overflow_strategy === "language_aware_compact"
        ? " For English-dominant prompts, prefer a meaning-preserving Chinese translation; for Chinese-dominant prompts, compact the Chinese wording without summarizing."
        : " Compact semantically without summarizing.";
    throw new Error(
      `The complete prompt has ${sourcePromptChars} Unicode characters, exceeding the selected provider limit of ` +
        `${policy.max_chars}. Keep prompt unchanged and provide provider_prompt derived only after the complete prompt is formed; ` +
        `aim for about ${policy.target_chars} characters.${languageGuidance}`,
    );
  }

  const submittedPromptChars = unicodeCharacterCount(providerPrompt);
  if (submittedPromptChars > policy.max_chars) {
    throw new Error(
      `provider_prompt has ${submittedPromptChars} Unicode characters, exceeding the selected provider limit of ` +
        `${policy.max_chars}. Keep prompt unchanged and compact provider_prompt to about ${policy.target_chars} characters.`,
    );
  }

  const sourceLanguage = promptLanguageProfile(sourcePrompt);
  const providerPromptLanguage = promptLanguageProfile(providerPrompt);
  if (
    policy.overflow_strategy === "language_aware_compact" &&
    sourceLanguage.dominant_language === "english_dominant" &&
    providerPromptLanguage.dominant_language !== "chinese_dominant"
  ) {
    throw new Error(
      "provider_prompt must be Chinese-dominant when the over-limit source prompt is " +
        "English-dominant. Translate meaning-for-meaning without dropping detail, then compact only " +
        `if needed to stay within ${policy.max_chars} characters.`,
    );
  }

  return {
    sourcePrompt,
    submittedPrompt: providerPrompt,
    sourcePromptChars,
    submittedPromptChars,
    promptPolicy: policy,
    promptPolicyApplied: true,
  };
}

function prepareDalle3ProviderPrompt(args, providerContext = null) {
  const suppliedSessionId = nonEmptyString(args.prompt_session_id);
  const pending = suppliedSessionId
    ? getDalle3PromptStage(suppliedSessionId, "pending")
    : null;
  const context = providerContext ?? configuredDalle3PolicyContext(pending?.stage.providerName);
  if (!context) {
    throw new Error(`No active provider uses prompt_profile "${PROMPT_PROFILE_DALLE3}".`);
  }
  if (pending && nonEmptyString(args.prompt)) {
    throw new Error(
      "Do not resend prompt when continuing with prompt_session_id; send only provider_prompt.",
    );
  }
  const sourcePrompt = pending?.stage.sourcePrompt ?? nonEmptyString(args.prompt);
  if (!sourcePrompt) {
    throw new Error(
      "Provide prompt for a new preparation or prompt_session_id to continue an over-limit preparation.",
    );
  }

  const policy = context.policy ?? context.promptPolicy;
  if (
    pending &&
    JSON.stringify(pending.stage.promptPolicy) !== JSON.stringify(policy)
  ) {
    dalle3PromptStages.delete(pending.id);
    throw new Error(
      `The ${context.providerName} prompt policy changed while this preparation was pending. ` +
        "No provider request has been sent; start again with the complete prompt.",
    );
  }
  const sourcePromptChars = unicodeCharacterCount(sourcePrompt);
  const sourceLanguage = promptLanguageProfile(sourcePrompt);
  const providerPrompt = nonEmptyString(args.provider_prompt);
  const providerPromptChars = providerPrompt ? unicodeCharacterCount(providerPrompt) : null;
  const providerPromptLanguage = providerPrompt ? promptLanguageProfile(providerPrompt) : null;
  let promptResolution = null;
  let status;
  let nextAction;

  if (sourcePromptChars <= policy.max_chars) {
    status = "ready_source_within_limit";
    promptResolution = {
      sourcePrompt,
      submittedPrompt: sourcePrompt,
      sourcePromptChars,
      submittedPromptChars: sourcePromptChars,
      promptPolicy: policy,
      promptPolicyApplied: false,
    };
    nextAction =
      `Call the matching ${context.providerName} image tool once with only this prompt_check_id plus ` +
      "the image and output parameters; do not resend prompt or provider_prompt.";
  } else if (!providerPrompt) {
    if (
      policy.overflow_strategy === "language_aware_compact" &&
      sourceLanguage.dominant_language === "english_dominant"
    ) {
      status = "english_translation_required";
      nextAction =
        `Keep the staged source unchanged. First translate the complete English-dominant Stage 1 prompt ` +
        `meaning-for-meaning into Chinese without compacting or dropping detail. Then call ` +
        `${TOOL_PREPARE_PROMPT_DALLE3} with this prompt_session_id and that translation as ` +
        `provider_prompt; do not resend prompt. Only compact the ` +
        "Chinese translation if a later preparation result says it is still over the limit.";
    } else {
      status = "chinese_compaction_required";
      nextAction =
        `Keep the staged source unchanged. Compact the Chinese-dominant Stage 1 wording toward ` +
        `${policy.target_chars} characters without truncating, summarizing, or changing meaning, then call ` +
        `${TOOL_PREPARE_PROMPT_DALLE3} with this prompt_session_id and the result as provider_prompt; ` +
        "do not resend prompt.";
    }
  } else if (providerPromptChars > policy.max_chars) {
    const excessCharacters = providerPromptChars - policy.max_chars;
    const translationStillRequired =
      policy.overflow_strategy === "language_aware_compact" &&
      sourceLanguage.dominant_language === "english_dominant" &&
      providerPromptLanguage.dominant_language !== "chinese_dominant";
    const translatedChineseStillTooLong =
      policy.overflow_strategy === "language_aware_compact" &&
      sourceLanguage.dominant_language === "english_dominant" &&
      providerPromptLanguage.dominant_language === "chinese_dominant";
    if (translationStillRequired) {
      status = "english_translation_required";
      nextAction =
        `Keep the staged source unchanged. provider_prompt is still English-dominant and exceeds the limit by ` +
        `${excessCharacters} characters. Translate it meaning-for-meaning into Chinese before any ` +
        `compaction, preserving every detail, then call ${TOOL_PREPARE_PROMPT_DALLE3} again with this ` +
        `prompt_session_id and the revised provider_prompt only. Only compact ` +
        "the Chinese translation if it remains over the limit.";
    } else if (translatedChineseStillTooLong) {
      status = "translated_chinese_compaction_required";
      nextAction =
        `Keep prompt unchanged. The Chinese translation still exceeds the limit by ${excessCharacters} ` +
        `characters; now compact that translated Chinese toward ${policy.target_chars} without truncating, ` +
        `summarizing, or changing meaning, then call ${TOOL_PREPARE_PROMPT_DALLE3} again with this ` +
        "prompt_session_id and the revised provider_prompt only.";
    } else {
      status = "chinese_compaction_required";
      nextAction =
        `Keep prompt unchanged. The Chinese-dominant provider_prompt exceeds the limit by ` +
        `${excessCharacters} characters; compact it toward ${policy.target_chars} without truncating, ` +
        `summarizing, or changing meaning, then call ${TOOL_PREPARE_PROMPT_DALLE3} again with this ` +
        "prompt_session_id and the revised provider_prompt only.";
    }
  } else if (
    policy.overflow_strategy === "language_aware_compact" &&
    sourceLanguage.dominant_language === "english_dominant" &&
    providerPromptLanguage.dominant_language !== "chinese_dominant"
  ) {
    status = "english_translation_required";
    nextAction =
      `Keep prompt unchanged. The English-dominant provider_prompt is within the character limit, ` +
      "but the required first transformation is still a meaning-for-meaning Chinese translation, not " +
      `English compaction. Translate it without dropping detail, then call ${TOOL_PREPARE_PROMPT_DALLE3} ` +
      "again with this prompt_session_id and the revised provider_prompt only. Only compact the Chinese " +
      "translation if it is over the limit.";
  } else {
    status = "ready_transport_valid";
    promptResolution = {
      sourcePrompt,
      submittedPrompt: providerPrompt,
      sourcePromptChars,
      submittedPromptChars: providerPromptChars,
      promptPolicy: policy,
      promptPolicyApplied: true,
    };
    nextAction =
      `Call the matching ${context.providerName} image tool once with only this prompt_check_id plus ` +
      "the image and output parameters; do not resend prompt or provider_prompt.";
  }

  const staged = promptResolution
    ? stageReadyDalle3Prompt(promptResolution, context, pending)
    : stagePendingDalle3Prompt(
        sourcePrompt,
        sourcePromptChars,
        sourceLanguage,
        policy,
        context,
        pending,
      );

  return {
    status,
    ready: Boolean(promptResolution),
    provider_request_sent: false,
    provider: context.providerName,
    source_prompt_chars: sourcePromptChars,
    source_language: sourceLanguage,
    provider_prompt_chars: providerPromptChars,
    provider_prompt_language: providerPromptLanguage ?? undefined,
    max_chars: policy.max_chars,
    target_chars: policy.target_chars,
    prompt_session_id: promptResolution ? undefined : staged.id,
    prompt_check_id: promptResolution ? staged.id : undefined,
    preparation_call_count: promptResolution
      ? staged.promptResolution.promptPreparation.preparation_call_count
      : staged.preparationCallCount,
    stage_expires_in_seconds: DALLE3_PROMPT_STAGE_TTL_MS / 1000,
    next_action: nextAction,
  };
}

function stripJsonBom(value) {
  return value.charCodeAt(0) === 0xfeff ? value.slice(1) : value;
}

function configBaseDir() {
  return dirname(CONFIG_PATH);
}

function readConfigForPaths() {
  try {
    return JSON.parse(stripJsonBom(readFileSync(CONFIG_PATH, "utf8")));
  } catch {
    return null;
  }
}

function resolveConfigDirectory(value, fallback) {
  const directory = nonEmptyString(value);
  return directory ? resolve(configBaseDir(), directory) : fallback;
}

function configuredGlobalCacheRoot() {
  const config = readConfigForPaths();
  return resolveConfigDirectory(config?.cache_dir, join(configBaseDir(), "cache"));
}

function providerSetting(provider, key) {
  return nonEmptyString(provider?.raw?.[key]) || nonEmptyString(provider?.config?.[key]);
}

function finalOutputRoot(args, provider) {
  return resolveConfigDirectory(
    nonEmptyString(args.output_dir) || providerSetting(provider, "output_dir"),
    join(configBaseDir(), "outputs", provider.name),
  );
}

function transportOutputRoot(args, provider) {
  return resolveConfigDirectory(
    nonEmptyString(args._transport_output_dir) || providerSetting(provider, "cache_dir"),
    join(configuredGlobalCacheRoot(), provider.name),
  );
}

function normalizeDisplayPath(value) {
  const path = nonEmptyString(value);
  return path ? path.replaceAll("\\", "/") : null;
}

function imageMimeType(value) {
  const path = nonEmptyString(value)?.toLowerCase();
  if (!path) return null;
  if (path.endsWith(".png")) return "image/png";
  if (path.endsWith(".jpg") || path.endsWith(".jpeg")) return "image/jpeg";
  if (path.endsWith(".webp")) return "image/webp";
  return null;
}

function appendOption(argv, flag, value) {
  if (value === undefined || value === null || value === "") return;
  argv.push(flag, String(value));
}

function appendFlag(argv, flag, enabled) {
  if (enabled) argv.push(flag);
}

function timestampForPath(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-` +
    `${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  );
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function compactFailureMessage(error) {
  const message = errorMessage(error).replace(/\s+/g, " ").trim();
  const http = message.match(/\bHTTP\s+(\d{3})\b/i);
  if (http) return `HTTP ${http[1]}`;
  if (/timed out/i.test(message)) return "timeout";
  if (/Network error/i.test(message)) return "network_error";
  return message.length > 160 ? `${message.slice(0, 157)}...` : message;
}

function jsonTrace(value) {
  if (value === undefined) return null;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function numberedBlock(values) {
  return values.map((value, index) => `[${index + 1}]\n${value}`).join("\n\n");
}

function normalizedStringArray(value) {
  return Array.isArray(value)
    ? value.map((item) => nonEmptyString(item)).filter(Boolean)
    : [];
}

function planTraceText(trace) {
  const lines = ["Fmage preflight trace."];
  const targetTool = nonEmptyString(trace.image_tool);
  if (targetTool) lines.push(`Target image tool: ${targetTool}`);

  const plan = jsonTrace(trace.image_job_plan);
  if (plan) lines.push(`ImageJobPlan:\n${plan}`);

  const argsPreview = jsonTrace(trace.call_arguments_preview);
  if (argsPreview) lines.push(`Image tool arguments preview:\n${argsPreview}`);

  const prompts = normalizedStringArray(trace.revised_prompts);
  if (prompts.length) {
    lines.push(`Revised prompts to submit:\n${numberedBlock(prompts)}`);
  }

  const references = normalizedStringArray(trace.reference_images);
  if (references.length) {
    lines.push(`Reference images:\n${references.join("\n")}`);
  }

  const notes = normalizedStringArray(trace.notes);
  if (notes.length) {
    lines.push(`Notes:\n${notes.join("\n")}`);
  }

  return lines.join("\n\n");
}

function isoNow() {
  return new Date().toISOString();
}

function taskStoreRoot() {
  return join(configuredGlobalCacheRoot(), "tasks");
}

function createTaskId(command) {
  return `task_${timestampForPath()}_${command}_${process.pid}_${Math.random().toString(36).slice(2, 8)}`;
}

function validTaskId(value) {
  return /^[A-Za-z0-9_.-]+$/.test(value);
}

function taskStatePath(taskId) {
  if (!validTaskId(taskId)) {
    throw new Error("Invalid task_id.");
  }
  return join(taskStoreRoot(), taskId, "state.json");
}

async function writeTaskState(task) {
  const statePath = taskStatePath(task.task_id);
  await mkdir(dirname(statePath), { recursive: true });
  await writeFile(statePath, JSON.stringify(task, null, 2), "utf8");
}

async function readTaskState(taskId) {
  return readTaskStateWithOptions(taskId, {});
}

async function readTaskStateWithOptions(taskId, options = {}) {
  const normalizedTaskId = nonEmptyString(taskId);
  if (!normalizedTaskId) throw new Error("task_id is required.");
  const statePath = taskStatePath(normalizedTaskId);
  const task = JSON.parse(stripJsonBom(await readFile(statePath, "utf8")));
  return normalizeTaskForResponse({ ...task, state_path: statePath }, options);
}

function normalizeTaskForResponse(task, options = {}) {
  const images = Array.isArray(task.images) ? task.images : [];
  const displayImages = images.map(normalizeDisplayPath).filter(Boolean);
  const manifests = Array.isArray(task.manifests) ? task.manifests : [];
  const displayManifests = manifests.map(normalizeDisplayPath).filter(Boolean);
  const displayManifest = normalizeDisplayPath(task.manifest);
  const displayStatePath = normalizeDisplayPath(task.state_path ?? taskStatePath(task.task_id));

  if (!options.verbose) {
    const includeProviderMetadata = Boolean(options.includeProviderMetadata);
    const compact = {
      task_id: task.task_id,
      status: task.status,
      requested_count: task.requested_count,
      completed_count: task.completed_count,
      pending_count: task.pending_count,
      failed_count: task.failed_count,
      requested_size: task.requested_size,
      images,
      display_images: displayImages,
      image_metadata: compactImageMetadata(task.image_metadata),
      warnings: shouldExposeWarnings(task) ? normalizedStringArray(task.warnings) : undefined,
      manifests: manifests.length ? manifests : undefined,
      display_manifests: displayManifests.length ? displayManifests : undefined,
      manifest: task.manifest,
      display_manifest: displayManifest,
      error: task.error,
      prompt_provenance: includeProviderMetadata ? task.prompt_provenance : undefined,
      prompt_provenances: includeProviderMetadata ? task.prompt_provenances : undefined,
      provider_response_metadata: includeProviderMetadata
        ? task.provider_response_metadata
        : undefined,
      provider_response_metadata_items: includeProviderMetadata
        ? task.provider_response_metadata_items
        : undefined,
    };
    Object.keys(compact).forEach((key) => {
      if (compact[key] === undefined) delete compact[key];
    });
    return compact;
  }

  const normalized = {
    ...task,
    display_images: displayImages,
    display_manifests: displayManifests,
    display_manifest: displayManifest,
    display_state_path: displayStatePath,
  };
  delete normalized.task_dir;
  return normalized;
}

function compactImageMetadata(metadata) {
  if (!Array.isArray(metadata)) return undefined;
  const compact = metadata
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const next = {};
      if (typeof item.width === "number") next.width = item.width;
      if (typeof item.height === "number") next.height = item.height;
      if (typeof item.aspect_ratio === "number") next.aspect_ratio = item.aspect_ratio;
      return Object.keys(next).length ? next : null;
    })
    .filter(Boolean);
  return compact.length ? compact : undefined;
}

function shouldExposeWarnings(result, { verbose = false } = {}) {
  const warnings = normalizedStringArray(result?.warnings);
  if (!warnings.length) return false;
  if (verbose) return true;

  const images = Array.isArray(result?.images) ? result.images : [];
  const failedCount = Number(result?.failed_count ?? 0);
  const pendingCount = Number(result?.pending_count ?? 0);
  const status = nonEmptyString(result?.status);
  return (
    images.length === 0 ||
    failedCount > 0 ||
    pendingCount > 0 ||
    ["failed", "partial", "pending", "pending_expired"].includes(status)
  );
}

function isPendingResult(result) {
  return Boolean(result && typeof result === "object" && result.pending);
}

function batchStatusFromCounts(completedCount, pendingCount, failedCount) {
  if (failedCount === 0 && pendingCount === 0) return "completed";
  if (completedCount > 0) return "partial";
  if (pendingCount > 0) return failedCount > 0 ? "partial" : "pending";
  return "failed";
}

function pythonCommand() {
  return nonEmptyString(process.env.FMAGE_PYTHON) || nonEmptyString(process.env.PYTHON) || "python";
}

async function readProviderConfig() {
  let config;
  try {
    config = JSON.parse(stripJsonBom(await readFile(CONFIG_PATH, "utf8")));
  } catch (error) {
    if (error && typeof error === "object" && error.code === "ENOENT") {
      try {
        await mkdir(dirname(CONFIG_PATH), { recursive: true });
        const starterConfig = await readFile(
          join(PLUGIN_ROOT, "config", "providers.example.json"),
          "utf8",
        );
        await writeFile(CONFIG_PATH, starterConfig, { encoding: "utf8", flag: "wx" });
      } catch (setupError) {
        if (!setupError || typeof setupError !== "object" || setupError.code !== "EEXIST") {
          throw new Error(
            `Fmage could not create its starter configuration at ${normalizeDisplayPath(CONFIG_PATH)}. ` +
              `Details: ${setupError instanceof Error ? setupError.message : String(setupError)}`,
          );
        }
      }
      throw new Error(
        `Fmage needs one-time setup. A starter configuration was created at ` +
          `${normalizeDisplayPath(CONFIG_PATH)}. Open that file, paste each API key into the ` +
          `"api_key" field of its corresponding provider, set "active_providers" to include the providers you want to use, ` +
          `save the file, and retry. Do not paste API keys into chat.`,
      );
    }
    throw new Error(
      `Fmage provider configuration could not be read at ${CONFIG_PATH}. ` +
        `Create it from ${join(PLUGIN_ROOT, "config", "providers.example.json")}. ` +
        `Details: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  if (!config || typeof config !== "object" || !config.providers || typeof config.providers !== "object") {
    throw new Error(`Invalid Fmage configuration at ${CONFIG_PATH}: missing providers object.`);
  }
  return config;
}

function configuredActiveProviders(config) {
  if (Array.isArray(config?.active_providers)) {
    return config.active_providers.map(nonEmptyString).filter(Boolean);
  }
  if (config && Object.prototype.hasOwnProperty.call(config, "active_providers")) {
    throw new Error(`Invalid Fmage configuration at ${CONFIG_PATH}: active_providers must be an array.`);
  }
  if (Array.isArray(config?.active_provider)) {
    return config.active_provider.map(nonEmptyString).filter(Boolean);
  }
  return [nonEmptyString(config?.active_provider)].filter(Boolean);
}

function normalizeProviderLookup(value) {
  return nonEmptyString(value)?.toLowerCase().replace(/[\s_.]+/g, "-").replace(/-+/g, "-") ?? null;
}

function activeProviderMatchesRequest(providerName, requestedProvider) {
  const name = normalizeProviderLookup(providerName);
  const requested = normalizeProviderLookup(requestedProvider);
  if (!name || !requested) return false;
  return (
    name === requested ||
    name.startsWith(`${requested}-`) ||
    name.endsWith(`-${requested}`) ||
    name.includes(`-${requested}-`)
  );
}

function resolveProviderName(config, requestedProvider) {
  const requested = nonEmptyString(requestedProvider);
  const activeProviders = configuredActiveProviders(config);
  if (!requested) {
    const defaultProvider = activeProviders[0];
    if (!defaultProvider) {
      throw new Error(`No active_providers are configured in ${CONFIG_PATH}.`);
    }
    return defaultProvider;
  }

  if (Object.prototype.hasOwnProperty.call(config.providers, requested)) {
    if (activeProviders.includes(requested)) {
      return requested;
    }
    throw new Error(
      `Provider "${requested}" is configured but is not listed in active_providers at ${CONFIG_PATH}. ` +
        `Add it to active_providers and retry.`,
    );
  }

  const matches = Array.from(
    new Set(activeProviders.filter((providerName) => activeProviderMatchesRequest(providerName, requested))),
  );
  if (matches.length === 1) {
    return matches[0];
  }
  if (matches.length > 1) {
    throw new Error(
      `Provider shorthand "${requested}" is ambiguous among active_providers: ` +
        `${matches.join(", ")}. Use the full provider name.`,
    );
  }

  throw new Error(
    `Provider "${requested}" does not exist in ${CONFIG_PATH} and did not match any active_providers entry.`,
  );
}

async function resolveProvider(requestedProvider, requireKey = true) {
  const config = await readProviderConfig();
  const providerName = resolveProviderName(config, requestedProvider);

  const raw = config.providers[providerName];
  if (!raw || typeof raw !== "object") {
    throw new Error(`Provider "${providerName}" does not exist in ${CONFIG_PATH}.`);
  }

  for (const legacyField of ["compatibility", "compatibility_profile"]) {
    if (Object.prototype.hasOwnProperty.call(raw, legacyField)) {
      throw new Error(
        `Provider "${providerName}" uses unsupported legacy field "${legacyField}". ` +
          `Use prompt_profile: "${PROMPT_PROFILE_DALLE3}" instead.`,
      );
    }
  }
  const configuredTransport = nonEmptyString(raw.transport);
  const transport = configuredTransport;
  const transportProfile = normalizeTransportProfile(raw.transport_profile);
  const baseUrl = nonEmptyString(raw.base_url);
  const model = nonEmptyString(raw.model);
  const apiKeyEnv = nonEmptyString(raw.api_key_env);
  const apiKey = nonEmptyString(raw.api_key) || (apiKeyEnv ? nonEmptyString(process.env[apiKeyEnv]) : null);
  const promptProfile = normalizePromptProfile(raw.prompt_profile);
  const dalle3PromptProfile = promptProfile === PROMPT_PROFILE_DALLE3;
  const promptPolicy = dalle3PromptProfile
    ? normalizePromptPolicy(
        raw.prompt_policy == null ? DALLE3_DEFAULT_PROMPT_POLICY : raw.prompt_policy,
        `providers.${providerName}.prompt_policy`,
      )
    : null;

  if (!transport || !TRANSPORTS[transport]) {
    throw new Error(
      `Provider "${providerName}" has unsupported transport "${configuredTransport ?? ""}". ` +
        `Use one of: ${Object.keys(TRANSPORTS).join(", ")}.`,
    );
  }
  if (transportProfile && transport !== TRANSPORT_OPENAI_IMAGES) {
    throw new Error(
      `Provider "${providerName}" transport_profile "${transportProfile}" requires transport ` +
        `"${TRANSPORT_OPENAI_IMAGES}".`,
    );
  }
  if (promptProfile === PROMPT_PROFILE_DALLE3 && transport !== TRANSPORT_OPENAI_IMAGES) {
    throw new Error(
      `Provider "${providerName}" prompt_profile "${PROMPT_PROFILE_DALLE3}" currently requires transport ` +
        `"${TRANSPORT_OPENAI_IMAGES}".`,
    );
  }
  if (!baseUrl || !model) {
    throw new Error(`Provider "${providerName}" must define base_url and model in ${CONFIG_PATH}.`);
  }
  let openaiImages808 = null;
  if (transportProfile === TRANSPORT_PROFILE_808) {
    if (!OPENAI_IMAGES_808_SUPPORTED_MODELS.has(model)) {
      throw new Error(
        `Provider "${providerName}" model "${model}" is not supported by transport_profile ` +
          `"${TRANSPORT_PROFILE_808}". ` +
          `Use gpt-image-2 or gpt-image-2-token.`,
      );
    }
    const responseFormat = nonEmptyString(raw.response_format) || OPENAI_IMAGES_808_DEFAULT_RESPONSE_FORMAT;
    if (!["url", "b64_json"].includes(responseFormat)) {
      throw new Error(
        `Provider "${providerName}" has unsupported response_format "${responseFormat}". ` +
          `Use "url" or "b64_json".`,
      );
    }
    const configuredTimeout = raw.timeout;
    if (configuredTimeout !== undefined && positiveInteger(configuredTimeout, 0) === 0) {
      throw new Error(`Provider "${providerName}" timeout must be a positive integer.`);
    }
    openaiImages808 = {
      responseFormat,
      timeoutSeconds: positiveInteger(configuredTimeout, OPENAI_IMAGES_808_DEFAULT_TIMEOUT_SECONDS),
    };
  }
  let ezaiBanana = null;
  if (transport === TRANSPORT_EZAI_BANANA_IMAGES) {
    const responseFormat = nonEmptyString(raw.response_format) || EZAI_BANANA_DEFAULT_RESPONSE_FORMAT;
    if (!EZAI_BANANA_SUPPORTED_RESPONSE_FORMATS.has(responseFormat)) {
      throw new Error(
        `Provider "${providerName}" has unsupported response_format "${responseFormat}". ` +
          `Use ${Array.from(EZAI_BANANA_SUPPORTED_RESPONSE_FORMATS).map((item) => `"${item}"`).join(" or ")}.`,
      );
    }
    ezaiBanana = {
      responseFormat,
      editInputModes: ["json_image_urls", "multipart_local_files"],
    };
  }
  if (requireKey && !apiKey) {
    throw new Error(
      `Provider "${providerName}" has no API key. Open ${normalizeDisplayPath(CONFIG_PATH)}, ` +
        `paste the key into providers.${providerName}.api_key, save the file, and retry` +
        (apiKeyEnv ? `; alternatively define environment variable ${apiKeyEnv}` : "") +
        `. Do not paste API keys into chat.`,
    );
  }

  return {
    name: providerName,
    transport,
    transportProfile,
    promptProfile,
    baseUrl,
    model,
    apiKey,
    apiKeyConfigured: Boolean(apiKey),
    apiKeySource: nonEmptyString(raw.api_key) ? "external_config" : apiKey ? `environment:${apiKeyEnv}` : "missing",
    dalle3PromptProfile,
    promptPolicy,
    openaiImages808,
    ezaiBanana,
    config,
    raw,
  };
}

function helperTimeoutMilliseconds(value) {
  const requestedTimeout = positiveInteger(value, 0);
  const timeoutSeconds = requestedTimeout
    ? requestedTimeout + HELPER_TIMEOUT_GRACE_SECONDS
    : DEFAULT_HELPER_TIMEOUT_SECONDS;
  return timeoutSeconds * 1000;
}

function appendBoundedOutput(current, chunk, streamName, child) {
  if (Buffer.byteLength(current, "utf8") + Buffer.byteLength(chunk, "utf8") > MAX_CHILD_OUTPUT_BYTES) {
    const error = new Error(`Fmage helper ${streamName} exceeded ${MAX_CHILD_OUTPUT_BYTES} bytes.`);
    child.kill();
    throw error;
  }
  return current + chunk;
}

function runProcess(command, argv, extraEnv = {}, options = {}) {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(command, argv, {
      cwd: PLUGIN_ROOT,
      env: { ...process.env, ...extraEnv },
      windowsHide: true,
      shell: false,
    });

    let stdout = "";
    let stderr = "";
    let settled = false;
    let pendingError = null;
    const timeoutMs = helperTimeoutMilliseconds(options.timeoutSeconds);
    const timeout = setTimeout(() => {
      pendingError = new Error(`Fmage helper timed out after ${Math.round(timeoutMs / 1000)} seconds.`);
      child.kill();
    }, timeoutMs);
    const rejectOnce = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      rejectPromise(error);
    };
    const resolveOnce = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      resolvePromise(value);
    };
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      try {
        stdout = appendBoundedOutput(stdout, chunk, "stdout", child);
      } catch (error) {
        pendingError = error;
      }
    });
    child.stderr.on("data", (chunk) => {
      try {
        stderr = appendBoundedOutput(stderr, chunk, "stderr", child);
      } catch (error) {
        pendingError = error;
      }
    });
    child.on("error", rejectOnce);
    child.on("close", (code) => {
      if (pendingError) {
        rejectOnce(pendingError);
        return;
      }
      if (code !== 0) {
        rejectOnce(new Error(stderr.trim() || stdout.trim() || `Fmage helper exited with code ${code}.`));
        return;
      }
      try {
        resolveOnce(JSON.parse(stdout));
      } catch {
        rejectOnce(new Error(`Fmage helper returned invalid JSON: ${stdout.slice(0, 1000)}`));
      }
    });
  });
}

async function runImageRegression(args) {
  const image = nonEmptyString(args.image);
  if (!image) throw new Error("regress_image requires a local image path.");
  const result = await runProcess(
    pythonCommand(),
    [REGRESSION_SCRIPT, "--input", image],
    { PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
    { timeoutSeconds: 120 },
  );
  const output = normalizeDisplayPath(result.output);
  return {
    ...result,
    output,
    display_images: output ? [output] : [],
  };
}

function regressionResultText(result) {
  const output = nonEmptyString(result.output);
  const original = Array.isArray(result.original_size) ? result.original_size : [];
  const finalSize = Array.isArray(result.output_size) ? result.output_size : [];
  const lines = ["处理完成。"];
  if (output) lines.push(`- 输出：[${output.split("/").pop()}](${output})`);
  if (finalSize.length === 2) {
    const originalText = original.length === 2 ? `（原图 ${original[0]} × ${original[1]}）` : "";
    lines.push(`- 尺寸：${finalSize[0]} × ${finalSize[1]}${originalText}`);
  }
  return lines.join("\n\n");
}

function commonArguments(args, promptFile, provider) {
  const outputDir = transportOutputRoot(args, provider);
  const argv = ["--prompt-file", promptFile];

  appendOption(argv, "--base-url", provider.baseUrl);
  appendOption(argv, "--model", provider.model);
  appendOption(argv, "--api-key-env", "FMAGE_ACTIVE_API_KEY");
  appendOption(argv, "--size", args.size);
  appendOption(argv, "--aspect", args.aspect);
  appendOption(argv, "--resolution", args.resolution);
  const quality = args.quality ?? defaultQualityForProvider(provider);
  appendOption(argv, "--quality", quality);
  appendOption(argv, "--output-dir", outputDir);
  appendOption(argv, "--timeout", args.timeout);

  if (provider.transport === "openai-images") {
    appendOption(argv, "--moderation", args.moderation ?? "low");
    appendOption(argv, "--background", args.background ?? "auto");
    appendOption(argv, "--output-format", args.output_format ?? "png");
    appendOption(argv, "--output-compression", args.output_compression);
  } else if (provider.transport === TRANSPORT_EZAI_BANANA_IMAGES) {
    appendOption(argv, "--output-format", args.output_format ?? "png");
    appendOption(argv, "--thinking-level", args.thinking_level);
    appendOption(
      argv,
      "--response-format",
      nonEmptyString(args.response_format) ||
        provider.ezaiBanana?.responseFormat ||
        EZAI_BANANA_DEFAULT_RESPONSE_FORMAT,
    );
  } else if (provider.transport === "zenmux-vertex") {
    appendOption(argv, "--output-format", args.output_format ?? "png");
    appendOption(argv, "--output-compression", args.output_compression);
  }

  appendOption(argv, "--pending-total-timeout", args._pending_total_timeout);
  appendOption(argv, "--pending-fast-window", args._pending_fast_window);
  appendOption(argv, "--pending-fast-interval", args._pending_fast_interval);
  appendOption(argv, "--pending-slow-interval", args._pending_slow_interval);

  appendFlag(argv, "--dry-run", args.dry_run);
  return argv;
}

function openaiImages808RequestTimeoutSeconds(args, provider) {
  return positiveInteger(
    args.timeout,
    provider.openaiImages808?.timeoutSeconds ?? OPENAI_IMAGES_808_DEFAULT_TIMEOUT_SECONDS,
  );
}

function openaiImages808PendingTimeoutSeconds(args, provider) {
  return Math.max(
    openaiImages808RequestTimeoutSeconds(args, provider),
    positiveInteger(args._pending_total_timeout, 0),
  );
}

function providerTransportScript(provider) {
  if (provider.transportProfile) {
    const script = TRANSPORT_PROFILE_SCRIPTS[provider.transportProfile];
    if (!script) {
      throw new Error(
        `Provider "${provider.name}" has unsupported transport_profile "${provider.transportProfile}".`,
      );
    }
    return script;
  }
  return TRANSPORTS[provider.transport];
}

function openaiImages808Arguments(args, promptFile, provider) {
  const outputDir = transportOutputRoot(args, provider);
  const argv = ["--prompt-file", promptFile];

  appendOption(argv, "--base-url", provider.baseUrl);
  appendOption(argv, "--model", provider.model);
  appendOption(argv, "--api-key-env", "FMAGE_ACTIVE_API_KEY");
  appendOption(argv, "--size", args.size);
  appendOption(argv, "--aspect", args.aspect);
  appendOption(argv, "--resolution", args.resolution);
  appendOption(argv, "--quality", args.quality ?? defaultQualityForProvider(provider));
  appendOption(argv, "--moderation", args.moderation ?? "low");
  appendOption(argv, "--background", args.background ?? "auto");
  appendOption(argv, "--output-format", args.output_format ?? "png");
  appendOption(argv, "--output-compression", args.output_compression);
  appendOption(
    argv,
    "--response-format",
    nonEmptyString(args.response_format) ||
      provider.openaiImages808?.responseFormat ||
      OPENAI_IMAGES_808_DEFAULT_RESPONSE_FORMAT,
  );
  appendOption(argv, "--output-dir", outputDir);
  appendOption(argv, "--timeout", openaiImages808RequestTimeoutSeconds(args, provider));
  appendOption(argv, "--pending-total-timeout", openaiImages808PendingTimeoutSeconds(args, provider));
  appendFlag(argv, "--dry-run", args.dry_run);
  return argv;
}

function defaultQualityForProvider(provider) {
  const model = nonEmptyString(provider?.model)?.toLowerCase() ?? "";
  return model.startsWith("gpt-image-2") ? "high" : "medium";
}

function enforceQualityPolicy(args = {}) {
  const requestedQuality = nonEmptyString(args.quality);
  if (!requestedQuality || requestedQuality === "medium" || args.quality_user_requested === true) {
    return args;
  }
  return {
    ...args,
    quality: "medium",
    _quality_policy_warning:
      `Ignored quality="${requestedQuality}" because quality_user_requested was not true; using medium.`,
  };
}

function textPartsFromRequestContent(content) {
  if (typeof content === "string") {
    return [nonEmptyString(content)].filter(Boolean);
  }
  if (Array.isArray(content)) {
    return content.flatMap(textPartsFromRequestContent);
  }
  if (!content || typeof content !== "object") {
    return [];
  }

  const directText = nonEmptyString(content.text) || nonEmptyString(content.input_text);
  const nestedText = content.content ? textPartsFromRequestContent(content.content) : [];
  return [...(directText ? [directText] : []), ...nestedText];
}

function promptFromMessages(messages) {
  if (!Array.isArray(messages)) return null;
  const prompt = messages
    .flatMap((message) => textPartsFromRequestContent(message?.content))
    .map(nonEmptyString)
    .filter(Boolean)
    .join("\n\n");
  return nonEmptyString(prompt);
}

function promptFromInstances(instances) {
  if (!Array.isArray(instances)) return null;
  const prompt = instances
    .map((instance) => nonEmptyString(instance?.prompt))
    .filter(Boolean)
    .join("\n\n");
  return nonEmptyString(prompt);
}

function requestPrompt(request) {
  return (
    nonEmptyString(request?.prompt) ||
    promptFromMessages(request?.messages) ||
    promptFromInstances(request?.instances)
  );
}

function sanitizeRequest(request) {
  if (!request || typeof request !== "object") return undefined;
  return {
    model: request.model,
    prompt: requestPrompt(request),
    size: request.size,
    aspect: request.aspect,
    aspect_ratio: request.aspect_ratio,
    resolution: request.resolution,
    image_size: request.image_size,
    thinking_level: request.thinking_level,
    n: request.n,
    image_urls: request.image_urls,
    quality: request.quality,
    moderation: request.moderation,
    background: request.background,
    output_format: request.output_format,
    output_mime_type: request.output_mime_type,
    response_format: request.response_format,
    parameters: request.parameters,
    image_config: request.image_config,
  };
}

function sanitizeRequestCompact(request) {
  if (!request || typeof request !== "object") return undefined;
  const compact = { ...sanitizeRequest(request) };
  delete compact.prompt;
  if (Array.isArray(request.jobs)) {
    compact.jobs = request.jobs.map((job) => {
      const compactJob = { ...sanitizeRequest(job) };
      delete compactJob.prompt;
      return compactJob;
    });
  }
  return compact;
}

function normalizePromptProvenance(value = {}, fallbacks = {}) {
  const submitted =
    nonEmptyString(value?.submitted) ||
    nonEmptyString(fallbacks.submitted);
  const source =
    nonEmptyString(value?.source) ||
    nonEmptyString(fallbacks.source);
  const providerCandidates = [
    nonEmptyString(value?.provider_revised),
    ...normalizedStringArray(value?.provider_revised_prompts),
    nonEmptyString(fallbacks.providerRevised),
    ...normalizedStringArray(fallbacks.providerRevisedPrompts),
  ].filter(Boolean);
  const uniqueProviderPrompts = [...new Set(providerCandidates)];
  const rewrittenPrompts = uniqueProviderPrompts.filter((prompt) => prompt !== submitted);
  const storedStatus = nonEmptyString(value?.provider_prompt_status);
  const providerPromptStatus = storedStatus || (
    uniqueProviderPrompts.length === 0
      ? "not_returned"
      : rewrittenPrompts.length
        ? "rewritten"
        : "echoed"
  );
  const provenance = {
    submitted,
    changed: Boolean((source && source !== submitted) || providerPromptStatus === "rewritten"),
    provider_prompt_status: providerPromptStatus,
  };
  if (source && source !== submitted) provenance.source = source;
  if (rewrittenPrompts.length) {
    provenance.provider_revised = rewrittenPrompts[0];
    if (rewrittenPrompts.length > 1) {
      provenance.provider_revised_prompts = rewrittenPrompts;
    }
  }
  Object.keys(provenance).forEach((key) => {
    if (provenance[key] === undefined || provenance[key] === null) delete provenance[key];
  });
  return provenance;
}

function legacyProviderResponseMetadata(responseMetadata) {
  if (!responseMetadata || typeof responseMetadata !== "object") return undefined;
  const metadata = { ...responseMetadata };
  delete metadata.revised_prompt;
  delete metadata.data_revised_prompts;
  Object.keys(metadata).forEach((key) => {
    if (metadata[key] === undefined || metadata[key] === null) delete metadata[key];
  });
  return Object.keys(metadata).length ? metadata : undefined;
}

function compactImageResultForResponse(result, args = {}) {
  if (args.verbose || result?.dry_run) return result;
  const includeProviderMetadata = Boolean(args.include_provider_metadata);
  const compact = { ...result };
  delete compact.revised_prompt_submitted;
  delete compact.revised_prompts_submitted;
  delete compact.provider_revised_prompt;
  delete compact.provider_revised_prompts;
  delete compact.provider_transport;
  delete compact.provider_base_url;
  delete compact.output_dir;
  delete compact.cache_dir;
  delete compact.batch_manifest;
  delete compact.child_manifests;
  delete compact.job_statuses;
  delete compact.notes;
  delete compact.timing;
  delete compact.timings;
  delete compact.request;
  if (!includeProviderMetadata) {
    delete compact.prompt_provenance;
    delete compact.prompt_provenances;
    delete compact.provider_response_metadata;
    delete compact.provider_response_metadata_items;
  }
  compact.image_metadata = compactImageMetadata(compact.image_metadata);
  compact.warnings = shouldExposeWarnings(compact) ? normalizedStringArray(compact.warnings) : undefined;
  Object.keys(compact).forEach((key) => {
    if (compact[key] === undefined) delete compact[key];
  });
  return compact;
}

function compactDalle3ImageResultForResponse(result, args = {}) {
  const compact = compactImageResultForResponse(result, args);
  if (args.verbose || result?.dry_run) return compact;
  delete compact.revised_prompt_source;
  delete compact.revised_prompts_source;
  return compact;
}

async function enrichResult(result, revisedPrompt, provider, promptResolution = null) {
  const displayImages = Array.isArray(result?.images)
    ? result.images.map(normalizeDisplayPath).filter(Boolean)
    : [];
  const enriched = {
    ...result,
    display_images: displayImages,
    display_manifests: Array.isArray(result?.manifests)
      ? result.manifests.map(normalizeDisplayPath).filter(Boolean)
      : [],
    display_manifest: normalizeDisplayPath(result?.manifest),
    request: sanitizeRequest(result.request),
    provider: provider.name,
    provider_transport: provider.transport,
    ...(provider.transportProfile ? { transport_profile: provider.transportProfile } : {}),
    ...(provider.promptProfile ? { prompt_profile: provider.promptProfile } : {}),
    provider_base_url: provider.baseUrl,
    provider_model: provider.model,
    revised_prompt_submitted: revisedPrompt,
    prompt_provenance: normalizePromptProvenance({}, {
      source: promptResolution?.sourcePrompt,
      submitted: revisedPrompt,
    }),
  };

  if (promptResolution) {
    Object.assign(enriched, {
      revised_prompt_source: promptResolution.sourcePrompt,
      source_prompt_chars: promptResolution.sourcePromptChars,
      submitted_prompt_chars: promptResolution.submittedPromptChars,
      prompt_policy: promptResolution.promptPolicy,
      prompt_policy_applied: promptResolution.promptPolicyApplied,
      prompt_preparation: promptResolution.promptPreparation,
    });
  }

  const manifestPath = nonEmptyString(result?.manifest);
  if (manifestPath) {
    try {
      const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
      enriched.request = sanitizeRequest(manifest.request);
      const legacyProviderRevisedPrompt =
        manifest.response_metadata?.revised_prompt ??
        manifest.response_metadata?.data_revised_prompts?.[0] ??
        null;
      enriched.prompt_provenance = normalizePromptProvenance(manifest.prompt_provenance, {
        source: promptResolution?.sourcePrompt,
        submitted: revisedPrompt,
        providerRevised: legacyProviderRevisedPrompt,
      });
      enriched.provider_revised_prompt = enriched.prompt_provenance.provider_revised ?? null;
      enriched.provider_revised_prompts = enriched.prompt_provenance.provider_revised_prompts;
      enriched.provider_response_metadata =
        manifest.provider_response_metadata ??
        legacyProviderResponseMetadata(manifest.response_metadata);
      if (!Array.isArray(enriched.image_metadata) && Array.isArray(manifest.image_metadata)) {
        enriched.image_metadata = manifest.image_metadata;
      }
      if (!Array.isArray(enriched.warnings) && Array.isArray(manifest.warnings)) {
        enriched.warnings = manifest.warnings;
      }
      if (!enriched.requested_size && manifest.requested_size) {
        enriched.requested_size = manifest.requested_size;
      }
    } catch {
      // Generated images remain usable if optional manifest enrichment fails.
    }
  }
  return enriched;
}

function positiveInteger(value, fallback) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

async function pathExists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function realpathIfExists(path) {
  try {
    return await realpath(path);
  } catch {
    return resolve(path);
  }
}

function isPathInside(childPath, parentPath) {
  const relation = relative(parentPath, childPath);
  return relation === "" || (!!relation && !relation.startsWith("..") && !isAbsolute(relation));
}

async function removeEmptyAncestors(startDir, boundaryDir) {
  const boundary = await realpathIfExists(boundaryDir);
  let current = await realpathIfExists(startDir);
  while (current !== boundary && isPathInside(current, boundary)) {
    let removed = false;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await rmdir(current);
        removed = true;
        break;
      } catch (error) {
        if (!['EBUSY', 'ENOTEMPTY', 'EPERM'].includes(error?.code) || attempt === 2) {
          break;
        }
        await new Promise((resolveDelay) => setTimeout(resolveDelay, 25 * (attempt + 1)));
      }
    }
    if (!removed) {
      try {
        const entries = await readdir(current);
        if (entries.length === 0) {
          await rm(current, { recursive: true, force: true });
          removed = true;
        }
      } catch {
        // Leave non-empty or externally locked directories untouched.
      }
    }
    if (!removed) {
      return;
    }
    current = dirname(current);
  }
}

async function validateLatestImagePaths(images, allowedRoots) {
  const roots = [];
  for (const root of allowedRoots) {
    const normalizedRoot = nonEmptyString(root);
    if (normalizedRoot) roots.push(await realpathIfExists(normalizedRoot));
  }
  if (!roots.length) return images;

  const validated = [];
  for (const image of images) {
    const imagePath = nonEmptyString(image);
    if (!imagePath) continue;
    const resolvedImage = await realpath(imagePath);
    if (!roots.some((root) => isPathInside(resolvedImage, root))) {
      throw new Error(
        `latest.json references an image outside the configured Fmage output/cache directories: ` +
          `${normalizeDisplayPath(imagePath)}`,
      );
    }
    validated.push(resolvedImage);
  }
  return validated;
}

async function moveFile(source, destination) {
  if (resolve(source) === resolve(destination)) return;
  try {
    await rename(source, destination);
  } catch (error) {
    if (!error || typeof error !== "object" || error.code !== "EXDEV") {
      throw error;
    }
    await copyFile(source, destination);
    await rm(source, { force: true });
  }
}

async function uniqueFlatOutputPath(outputDir, timestamp, sequence, extension) {
  const padded = String(sequence).padStart(3, "0");
  let suffix = "";
  let attempt = 0;
  while (true) {
    const candidate = join(outputDir, `${timestamp}-${padded}${suffix}${extension}`);
    if (!(await pathExists(candidate))) return candidate;
    attempt += 1;
    suffix = `-${attempt}`;
  }
}

async function updateImageManifest(result) {
  const manifestPath = nonEmptyString(result.manifest);
  if (!manifestPath) return;
  try {
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    manifest.images = result.images;
    manifest.image_metadata = result.image_metadata;
    await writeFile(manifestPath, JSON.stringify(manifest, null, 2), "utf8");
  } catch {
    // The output image paths remain authoritative even if manifest refresh fails.
  }
}

async function writeImageManifestSidecars(cacheDir, result) {
  if (!Array.isArray(result.images) || !result.images.length) return [];
  await mkdir(cacheDir, { recursive: true });

  let baseManifest = {};
  const sourceManifest = nonEmptyString(result.manifest);
  if (sourceManifest) {
    try {
      baseManifest = JSON.parse(await readFile(sourceManifest, "utf8"));
    } catch {
      baseManifest = {};
    }
  }

  const sidecars = [];
  for (const [index, image] of result.images.entries()) {
    const imagePath = nonEmptyString(image);
    if (!imagePath) continue;
    const imageName = basename(imagePath, extname(imagePath));
    const sidecarPath = join(cacheDir, `${imageName}.json`);
    const imageMetadata = Array.isArray(result.image_metadata) ? result.image_metadata[index] : undefined;
    const manifest = {
      ...baseManifest,
      command: result.command ?? baseManifest.command,
      provider: result.provider ?? baseManifest.provider,
      provider_transport: result.provider_transport ?? baseManifest.provider_transport,
      provider_model: result.provider_model ?? baseManifest.provider_model,
      request: sanitizeRequestCompact(result.request ?? baseManifest.request),
      requested_size: result.requested_size ?? baseManifest.requested_size,
      prompt_provenance:
        result.prompt_provenance ??
        baseManifest.prompt_provenance ??
        normalizePromptProvenance({}, {
          source: result.revised_prompt_source ?? baseManifest.revised_prompt_source,
          submitted: result.revised_prompt_submitted ?? baseManifest.revised_prompt_submitted,
          providerRevised: result.provider_revised_prompt ?? baseManifest.provider_revised_prompt,
        }),
      provider_response_metadata:
        result.provider_response_metadata ??
        baseManifest.provider_response_metadata ??
        legacyProviderResponseMetadata(baseManifest.response_metadata),
      images: [imagePath],
      image_metadata: imageMetadata ? [imageMetadata] : [],
      warnings: Array.isArray(result.warnings) ? result.warnings : baseManifest.warnings,
      notes: Array.isArray(result.notes) ? result.notes : baseManifest.notes,
      timing: result.timing ?? baseManifest.timing,
    };
    delete manifest.response_metadata;
    delete manifest.revised_prompt_submitted;
    delete manifest.provider_revised_prompt;
    delete manifest.revised_prompt_source;
    if (result.prompt_policy) {
      Object.assign(manifest, {
        source_prompt_chars: result.source_prompt_chars,
        submitted_prompt_chars: result.submitted_prompt_chars,
        prompt_policy: result.prompt_policy,
        prompt_policy_applied: result.prompt_policy_applied,
        prompt_preparation: result.prompt_preparation,
      });
    }
    await writeFile(sidecarPath, JSON.stringify(manifest, null, 2), "utf8");
    sidecars.push(sidecarPath);
  }
  return sidecars;
}

function latestRequestSummary(result) {
  const summary = { ...(sanitizeRequestCompact(result?.request) ?? {}) };
  delete summary.prompt;
  delete summary.model;
  return summary;
}

async function writeLatestState(root, result) {
  if (!Array.isArray(result.images) || !result.images.length) return;
  await mkdir(root, { recursive: true });
  await writeFile(
    join(root, "latest.json"),
    JSON.stringify(
      {
        schema_version: 1,
        source_provider: result.provider,
        provider_transport: result.provider_transport,
        provider_model: result.provider_model,
        manifest: result.manifest,
        manifests: result.manifests,
        images: result.images,
        request_summary: latestRequestSummary(result),
        updated_at: nonEmptyString(result.updated_at) || isoNow(),
      },
      null,
      2,
    ),
    "utf8",
  );
}

async function writeGlobalLatestState(result) {
  return writeLatestState(configuredGlobalCacheRoot(), result);
}

async function removeProviderLatestState(root) {
  const providerRoot = nonEmptyString(root);
  if (!providerRoot || resolve(providerRoot) === resolve(configuredGlobalCacheRoot())) return;
  await rm(join(providerRoot, "latest.json"), { force: true });
}

async function publishResultImages(result, args, provider) {
  if (args.dry_run || !Array.isArray(result.images) || !result.images.length) return result;

  const outputDir = finalOutputRoot(args, provider);
  const cacheDir = transportOutputRoot({}, provider);
  const requestCacheDir = transportOutputRoot(args, provider);
  const cleanupBoundary = nonEmptyString(args._transport_output_dir)
    ? dirname(requestCacheDir)
    : requestCacheDir;
  const timestamp = nonEmptyString(args.output_timestamp) || timestampForPath();
  const firstSequence = positiveInteger(args.output_sequence, 1);
  const publishedImages = [];
  const cleanupDirectories = new Set();

  await mkdir(outputDir, { recursive: true });

  for (const [index, image] of result.images.entries()) {
    const source = nonEmptyString(image);
    if (!source) continue;
    const extension = extname(source) || `.${nonEmptyString(args.output_format) || "png"}`;
    const resolvedSource = resolve(source);
    const destination =
      dirname(resolvedSource) === resolve(outputDir)
        ? resolvedSource
        : await uniqueFlatOutputPath(outputDir, timestamp, firstSequence + index, extension);
    cleanupDirectories.add(dirname(resolvedSource));
    await moveFile(source, destination);
    publishedImages.push(destination);
  }

  result.images = publishedImages;
  result.display_images = publishedImages.map(normalizeDisplayPath).filter(Boolean);
  result.output_dir = outputDir;
  result.cache_dir = cacheDir;
  const originalManifest = nonEmptyString(result.manifest);

  if (Array.isArray(result.image_metadata)) {
    result.image_metadata = result.image_metadata.map((item, index) =>
      item && typeof item === "object" && publishedImages[index]
        ? { ...item, path: publishedImages[index] }
        : item,
    );
  }

  await updateImageManifest(result);
  const sidecarManifests = await writeImageManifestSidecars(cacheDir, result);
  if (sidecarManifests.length) {
    result.manifests = sidecarManifests;
    result.display_manifests = sidecarManifests.map(normalizeDisplayPath).filter(Boolean);
    result.manifest = sidecarManifests[0];
    result.display_manifest = normalizeDisplayPath(sidecarManifests[0]);
    if (originalManifest && !sidecarManifests.some((item) => resolve(item) === resolve(originalManifest))) {
      await rm(originalManifest, { force: true });
      await removeEmptyAncestors(dirname(originalManifest), cleanupBoundary);
    }
  }
  await writeGlobalLatestState(result);
  await removeProviderLatestState(cacheDir);
  for (const directory of Array.from(cleanupDirectories).sort((left, right) => right.length - left.length)) {
    await removeEmptyAncestors(directory, cleanupBoundary);
  }
  return result;
}

function requestedImageCount(args) {
  const count = Number(args.n ?? 1);
  if (!Number.isInteger(count) || count < 1 || count > 10) {
    throw new Error("The n argument must be an integer between 1 and 10.");
  }
  return count;
}

function batchReturnWhen(args) {
  const value = nonEmptyString(args.return_when) || "completed";
  if (!["submitted", "completed"].includes(value)) {
    throw new Error('return_when must be "submitted" or "completed".');
  }
  return value;
}

function batchJobs(args) {
  const jobs = Array.isArray(args.jobs) ? args.jobs : [];
  if (!jobs.length) throw new Error("jobs must contain at least one image job.");
  if (jobs.length > 10) throw new Error("jobs may contain at most 10 image jobs.");
  return jobs.map((job, index) => {
    if (!job || typeof job !== "object" || Array.isArray(job)) {
      throw new Error(`jobs[${index}] must be an object.`);
    }
    const prompt = nonEmptyString(job.prompt);
    if (!prompt) {
      throw new Error(`jobs[${index}].prompt must contain one complete Codex-revised image prompt.`);
    }
    return { ...job, prompt };
  });
}

function dalle3BatchJobs(args) {
  const jobs = Array.isArray(args.jobs) ? args.jobs : [];
  if (!jobs.length) throw new Error("jobs must contain at least one image job.");
  if (jobs.length > 10) throw new Error("jobs may contain at most 10 image jobs.");
  return jobs.map((job, index) => {
    if (!job || typeof job !== "object" || Array.isArray(job)) {
      throw new Error(`jobs[${index}] must be an object.`);
    }
    if (!nonEmptyString(job.prompt) && !nonEmptyString(job.prompt_check_id)) {
      throw new Error(
        `jobs[${index}] must contain an initial prompt or a staged prompt_check_id.`,
      );
    }
    return { ...job };
  });
}

function jobArgsFromBatchArgs(args, job) {
  const merged = { ...args, ...job };
  delete merged.jobs;
  delete merged.return_when;
  delete merged.embed_images;
  delete merged.verbose;
  delete merged.include_provider_metadata;
  delete merged.n;
  return merged;
}

function configuredImageRoots(config) {
  const roots = [configuredGlobalCacheRoot()];
  if (!config?.providers || typeof config.providers !== "object") return roots;
  for (const [name, raw] of Object.entries(config.providers)) {
    if (!raw || typeof raw !== "object") continue;
    const provider = { name, raw, config };
    roots.push(finalOutputRoot({}, provider), transportOutputRoot({}, provider));
  }
  return Array.from(new Set(roots.map((root) => resolve(root))));
}

async function loadLatestImages(config) {
  const root = configuredGlobalCacheRoot();
  const latestPath = join(root, "latest.json");
  let latest;
  try {
    latest = JSON.parse(stripJsonBom(await readFile(latestPath, "utf8")));
  } catch (error) {
    throw new Error(`No global Fmage latest state found at ${normalizeDisplayPath(latestPath)}: ${errorMessage(error)}`);
  }

  const images = Array.isArray(latest?.images)
    ? latest.images.map(nonEmptyString).filter(Boolean)
    : [];
  if (!images.length) {
    throw new Error(`latest.json exists but contains no image paths: ${normalizeDisplayPath(latestPath)}`);
  }
  return validateLatestImagePaths(images, configuredImageRoots(config));
}

async function mapAllSettled(count, mapper, onSettle = null) {
  const results = new Array(count);
  await Promise.all(
    Array.from({ length: count }, async (_, index) => {
      try {
        results[index] = {
          status: "fulfilled",
          value: await mapper(index),
        };
      } catch (error) {
        results[index] = {
          status: "rejected",
          reason: error,
        };
      }
      if (onSettle) {
        await onSettle(index, results[index], results);
      }
    }),
  );
  return results;
}

function timeoutResult(ms, value) {
  let timer = null;
  const promise = new Promise((resolveTimeout) => {
    timer = setTimeout(() => {
      timer = null;
      resolveTimeout(value);
    }, ms);
  });
  return {
    promise,
    cancel: () => {
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
}

function updateTaskProgressFromSettled(task, settledResults) {
  const fulfilled = settledResults.filter((item) => item?.status === "fulfilled").map((item) => item.value);
  const completed = fulfilled.filter((result) => !isPendingResult(result));
  const pending = fulfilled.filter(isPendingResult);
  const failed = settledResults.filter((item) => item?.status === "rejected");
  task.completed_count = completed.length;
  task.pending_count = pending.length;
  task.failed_count = failed.length;
  task.images = completed.flatMap((result) => (Array.isArray(result.images) ? result.images : []));
  task.image_metadata = completed.flatMap((result) =>
    Array.isArray(result.image_metadata) ? result.image_metadata : [],
  );
  task.child_manifests = completed
    .map((result) => nonEmptyString(result.manifest))
    .filter(Boolean);
  task.manifests = completed
    .flatMap((result) =>
      Array.isArray(result.manifests)
        ? result.manifests
        : [nonEmptyString(result.manifest)].filter(Boolean),
    )
    .filter(Boolean);
  task.manifest = task.manifests[0];
  task.warnings = [
    ...completed.flatMap((result) => (Array.isArray(result.warnings) ? result.warnings : [])),
    ...settledResults
      .map((item, index) => (item?.status === "rejected" ? `Subrequest ${index + 1} failed: ${compactFailureMessage(item.reason)}` : null))
      .filter(Boolean),
  ];
  task.job_statuses = settledResults
    .map((item, index) => {
      if (!item) return null;
      if (item.status === "fulfilled") {
        if (isPendingResult(item.value)) {
          return {
            job_index: index + 1,
            status: item.value.pending_expired ? "pending_expired" : "pending",
            remote_task_id: item.value.remote_task_id,
            remote_status: item.value.remote_status,
            timing: item.value.timing,
          };
        }
        return {
          job_index: index + 1,
          status: "completed",
          images: Array.isArray(item.value.images) ? item.value.images : [],
          image_metadata: Array.isArray(item.value.image_metadata) ? item.value.image_metadata : [],
          manifest: item.value.manifest,
          timing: item.value.timing,
        };
      }
      return {
        job_index: index + 1,
        status: "failed",
        error: compactFailureMessage(item.reason),
        timing: item.reason?.timing,
      };
    })
    .filter(Boolean);
}

async function writeBatchManifest(root, batchRoot, result) {
  const createdAt = new Date().toISOString();
  const manifestPath = join(batchRoot, "manifest.json");
  const manifest = {
    command: result.command,
    status: result.status,
    created_at: createdAt,
    provider: result.provider,
    provider_transport: result.provider_transport,
    provider_model: result.provider_model,
    requested_count: result.requested_count,
    completed_count: result.completed_count,
    pending_count: result.pending_count,
    failed_count: result.failed_count,
    orchestration_count: result.orchestration_count,
    request: sanitizeRequestCompact(result.request),
    requested_size: result.requested_size,
    prompt_provenance: result.prompt_provenance,
    prompt_provenances: result.prompt_provenances,
    source_prompt_char_counts: result.source_prompt_char_counts,
    submitted_prompt_char_counts: result.submitted_prompt_char_counts,
    prompt_policies_applied: result.prompt_policies_applied,
    prompt_preparations: result.prompt_preparations,
    provider_response_metadata: result.provider_response_metadata,
    provider_response_metadata_items: result.provider_response_metadata_items,
    images: result.images,
    image_metadata: result.image_metadata,
    manifests: result.manifests,
    child_manifests: result.child_manifests,
    timings: result.timings,
    job_statuses: result.job_statuses,
    notes: result.notes,
    warnings: result.warnings,
  };
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2), "utf8");
  await writeGlobalLatestState({ ...result, manifest: manifestPath, updated_at: createdAt });
  await removeProviderLatestState(root);
  return manifestPath;
}

async function combineBatchResults({
  command,
  args,
  prompt,
  jobs,
  provider,
  count,
  root,
  batchRoot,
  settledResults,
}) {
  const promptResolutions = args._dalle3_prompt_policy && Array.isArray(jobs)
    ? jobs.map((job) => job._prompt_resolution ?? resolveProviderPrompt(job, provider))
    : null;
  const fulfilled = settledResults
    .filter((item) => item?.status === "fulfilled")
    .map((item) => item.value);
  const successes = fulfilled.filter((result) => !isPendingResult(result));
  const pending = fulfilled.filter(isPendingResult);
  const failures = settledResults
    .map((item, index) => (item?.status === "rejected" ? { index, reason: item.reason } : null))
    .filter(Boolean);

  if (!successes.length && !pending.length) {
    const failureText = failures
      .map((failure) => `request_${failure.index + 1}: ${compactFailureMessage(failure.reason)}`)
      .join("; ");
    throw new Error(`All ${count} image subrequests failed. ${failureText}`);
  }

  const images = successes.flatMap((result) => (Array.isArray(result.images) ? result.images : []));
  const imageMetadata = successes.flatMap((result) =>
    Array.isArray(result.image_metadata) ? result.image_metadata : [],
  );
  const childManifests = successes
    .map((result) => nonEmptyString(result.manifest))
    .filter(Boolean);
  const notes = [
    `batch_fanout_${count}_provider_requests`,
    ...successes.flatMap((result) => (Array.isArray(result.notes) ? result.notes : [])),
  ];
  const warnings = [
    ...successes.flatMap((result) => (Array.isArray(result.warnings) ? result.warnings : [])),
    ...failures.map((failure) => `Subrequest ${failure.index + 1} failed: ${compactFailureMessage(failure.reason)}`),
  ];
  const request = Array.isArray(jobs)
    ? {
        model: provider.model,
        jobs: jobs.map((job, index) =>
          sanitizeRequest({
            model: provider.model,
            prompt: promptResolutions?.[index]?.submittedPrompt ?? job.prompt,
            size: job.size,
            aspect: job.aspect,
            resolution: job.resolution,
            image_size: job.image_size,
            quality: job.quality,
            moderation: job.moderation,
            background: job.background,
            output_format: job.output_format,
            output_mime_type: job.output_mime_type,
            response_format: job.response_format,
            parameters: job.parameters,
            image_config: job.image_config,
          }),
        ),
      }
    : { ...(successes[0]?.request ?? {}) };
  const jobStatuses = settledResults
    .map((item, index) => {
      if (!item) return null;
      if (item.status === "fulfilled") {
        if (isPendingResult(item.value)) {
          return {
            job_index: index + 1,
            status: item.value.pending_expired ? "pending_expired" : "pending",
            remote_task_id: item.value.remote_task_id,
            remote_status: item.value.remote_status,
            timing: item.value.timing,
          };
        }
        return {
          job_index: index + 1,
          status: "completed",
          images: Array.isArray(item.value.images) ? item.value.images : [],
          image_metadata: Array.isArray(item.value.image_metadata) ? item.value.image_metadata : [],
          manifest: item.value.manifest,
          timing: item.value.timing,
        };
      }
      return {
        job_index: index + 1,
        status: "failed",
        error: compactFailureMessage(item.reason),
        timing: item.reason?.timing,
      };
    })
    .filter(Boolean);
  const promptProvenances = settledResults.map((item, index) => {
    const resolution = promptResolutions?.[index];
    const jobPrompt = resolution?.submittedPrompt ?? jobs?.[index]?.prompt ?? prompt;
    const sourcePrompt = resolution?.sourcePrompt ?? jobs?.[index]?.prompt ?? prompt;
    const childResult = item?.status === "fulfilled" ? item.value : null;
    return normalizePromptProvenance(childResult?.prompt_provenance, {
      source: sourcePrompt,
      submitted: jobPrompt,
      providerRevised: childResult?.provider_revised_prompt,
      providerRevisedPrompts: childResult?.provider_revised_prompts,
    });
  });
  const providerResponseMetadataItems = settledResults
    .map((item, index) => {
      const metadata = item?.status === "fulfilled" ? item.value?.provider_response_metadata : null;
      return metadata && typeof metadata === "object"
        ? { job_index: index + 1, metadata }
        : null;
    })
    .filter(Boolean);

  const result = {
    command,
    dry_run: Boolean(args.dry_run),
    images,
    image_metadata: imageMetadata,
    requested_size: successes[0]?.requested_size ?? pending[0]?.requested_size,
    notes,
    warnings,
    child_manifests: childManifests,
    manifests: childManifests,
    requested_count: count,
    completed_count: successes.length,
    pending_count: pending.length,
    failed_count: failures.length,
    orchestration_count: count,
    request,
    provider: provider.name,
    provider_transport: provider.transport,
    ...(provider.transportProfile ? { transport_profile: provider.transportProfile } : {}),
    ...(provider.promptProfile ? { prompt_profile: provider.promptProfile } : {}),
    provider_base_url: provider.baseUrl,
    provider_model: provider.model,
    revised_prompt_submitted: prompt,
    revised_prompts_submitted: Array.isArray(jobs) ? jobs.map((job) => job.prompt) : undefined,
    provider_revised_prompts: successes
      .map((item) => item.provider_revised_prompt)
      .filter((value) => value !== null && value !== undefined),
    job_statuses: jobStatuses,
    timings: {
      provider_request_started_at: jobStatuses
        .map((item) => item.timing?.provider_request_started_at ?? item.timing?.mcp_request_started_at)
        .filter(Boolean)
        .sort()[0],
      provider_response_completed_at: jobStatuses
        .map((item) => item.timing?.provider_response_completed_at ?? item.timing?.mcp_request_finished_at)
        .filter(Boolean)
        .sort()
        .at(-1),
    },
    provider_revised_prompt: successes
      .map((item) => item.provider_revised_prompt)
      .find((value) => value !== null && value !== undefined) ?? null,
    prompt_provenance: promptProvenances.length === 1 ? promptProvenances[0] : undefined,
    prompt_provenances: promptProvenances.length > 1 ? promptProvenances : undefined,
    provider_response_metadata:
      providerResponseMetadataItems.length === 1
        ? providerResponseMetadataItems[0].metadata
        : undefined,
    provider_response_metadata_items:
      providerResponseMetadataItems.length > 1
        ? providerResponseMetadataItems
        : undefined,
  };

  if (promptResolutions) {
    Object.assign(result, {
      revised_prompt_source: prompt,
      revised_prompt_submitted: prompt ? promptResolutions[0]?.submittedPrompt : undefined,
      revised_prompts_source: promptResolutions.map((item) => item.sourcePrompt),
      revised_prompts_submitted: promptResolutions.map((item) => item.submittedPrompt),
      source_prompt_char_counts: promptResolutions.map((item) => item.sourcePromptChars),
      submitted_prompt_char_counts: promptResolutions.map((item) => item.submittedPromptChars),
      prompt_policies_applied: promptResolutions.map((item) => item.promptPolicyApplied),
      prompt_preparations: promptResolutions.map((item) => item.promptPreparation),
    });
  }

  if (!args.dry_run) {
    result.manifest = childManifests[0];
    result.display_manifest = normalizeDisplayPath(result.manifest);
    result.display_manifests = childManifests.map(normalizeDisplayPath).filter(Boolean);
    await writeGlobalLatestState(result);
    await removeProviderLatestState(root);
  }
  result.display_images = images.map(normalizeDisplayPath).filter(Boolean);
  return result;
}

async function runImageCommand(command, args) {
  args = enforceQualityPolicy(args);
  const provider = await resolveProvider(args.provider, !args.dry_run);
  if (isDalle3PromptPolicyProvider(provider)) {
    const routed = routeDalle3ImageArgs(args, provider);
    if (!routed.ready) return routed.result;
    args = {
      ...routed.args,
      provider: provider.name,
      _dalle3_prompt_policy: true,
      _dalle3_prompt_check_required: true,
    };
  } else if (nonEmptyString(args.prompt_check_id)) {
    throw new Error(
      `prompt_check_id can be used only with a provider configured with prompt_profile ` +
        `"${PROMPT_PROFILE_DALLE3}".`,
    );
  }
  const count = requestedImageCount(args);
  if (count > 1) {
    return submitBatchImageTask(command, args, count);
  }
  return runSingleImageCommand(command, args, provider);
}

async function runBatchImageCommand(command, args) {
  const provider = await resolveProvider(args.provider, !args.dry_run);
  const jobs = isDalle3PromptPolicyProvider(provider) ? dalle3BatchJobs(args) : batchJobs(args);
  if (!isDalle3PromptPolicyProvider(provider)) {
    if (jobs.some((job) => nonEmptyString(job.prompt_check_id))) {
      throw new Error(
        `prompt_check_id can be used only with a provider configured with prompt_profile ` +
          `"${PROMPT_PROFILE_DALLE3}".`,
      );
    }
    return submitBatchJobs(command, { ...args, provider: provider.name }, jobs);
  }

  const routedJobs = jobs.map((job) => routeDalle3ImageArgs(job, provider, { consume: false }));
  if (routedJobs.some((item) => !item.ready)) {
    return dalle3BatchPreparationResult(routedJobs, provider.name);
  }
  for (const item of routedJobs) {
    if (item.stagedId) dalle3PromptStages.delete(item.stagedId);
  }
  return submitBatchJobs(
    command,
    {
      ...args,
      provider: provider.name,
      _dalle3_prompt_policy: true,
      _dalle3_prompt_check_required: true,
    },
    routedJobs.map((item) => item.args),
  );
}

function routeDalle3ImageArgs(args, provider, { consume = true } = {}) {
  if (!isDalle3PromptPolicyProvider(provider)) {
    throw new Error(`Provider "${provider?.name ?? ""}" does not use prompt_profile "${PROMPT_PROFILE_DALLE3}".`);
  }
  if (nonEmptyString(args.provider_prompt)) {
    throw new Error(
      `Provider "${provider.name}" uses prompt_profile "${PROMPT_PROFILE_DALLE3}"; do not provide ` +
        `provider_prompt to the base image tool. Submit the complete source prompt first, then ` +
        `continue through ${TOOL_PREPARE_PROMPT_DALLE3} only if preparation is required.`,
    );
  }

  const promptCheckId = nonEmptyString(args.prompt_check_id);
  if (promptCheckId) {
    if (nonEmptyString(args.prompt) || nonEmptyString(args.provider_prompt)) {
      throw new Error(
        "Use prompt_check_id by itself; do not resend prompt or provider_prompt.",
      );
    }
    const { id, stage } = getDalle3PromptStage(promptCheckId, "ready");
    if (stage.promptResolution.providerName !== provider.name) {
      throw new Error(
        `This prompt_check_id belongs to provider "${stage.promptResolution.providerName}", not ` +
          `"${provider.name}". No provider request has been sent.`,
      );
    }
    if (JSON.stringify(stage.promptResolution.promptPolicy) !== JSON.stringify(provider.promptPolicy)) {
      dalle3PromptStages.delete(id);
      throw new Error(
        `The ${provider.name} prompt policy changed after preparation. ` +
          "No provider request has been sent; prepare the prompt again.",
      );
    }
    if (consume) dalle3PromptStages.delete(id);
    return {
      ready: true,
      args: {
        ...args,
        prompt: stage.promptResolution.sourcePrompt,
        _prompt_resolution: stage.promptResolution,
      },
      stagedId: id,
    };
  }

  const prompt = nonEmptyString(args.prompt);
  if (!prompt) {
    throw new Error(
      `Provide the complete unrestricted prompt for the initial ${provider.name} call, ` +
        `or provide prompt_check_id after an over-limit preparation.`,
    );
  }
  if (unicodeCharacterCount(prompt) <= provider.promptPolicy.max_chars) {
    return { ready: true, args };
  }
  return {
    ready: false,
    result: prepareDalle3ProviderPrompt({ prompt }, provider),
  };
}

function dalle3BatchPreparationResult(routedJobs, providerName) {
  return {
    status: "prompt_preparation_required",
    ready: false,
    provider_request_sent: false,
    provider: providerName,
    jobs: routedJobs.map((item, index) => ({
      job_index: index + 1,
      ready: item.ready,
      ...(item.ready
        ? { status: "ready_for_submission" }
        : item.result),
    })),
    next_action:
      `No provider requests were sent. Continue only the jobs with ready=false through ` +
      `${TOOL_PREPARE_PROMPT_DALLE3}, then call the same base batch tool with prompt_check_id for those jobs.`,
  };
}

function isDalle3PreparationResult(result) {
  return result?.ready === false && result?.provider_request_sent === false;
}

function dalle3PreparationText(result) {
  if (Array.isArray(result?.jobs)) {
    return (
      `Prompt preparation status: ${result.status}. ready=false. ` +
      `Provider request sent: false. ${result.next_action}`
    );
  }
  return (
    `Prompt preparation status: ${result.status}. ready=${result.ready}. ` +
    `Source characters: ${result.source_prompt_chars}. Provider candidate characters: ` +
    `${result.provider_prompt_chars ?? "not supplied"}. Provider request sent: false. ` +
    result.next_action
  );
}

async function submitBatchImageTask(command, args, count) {
  const prompt = nonEmptyString(args.prompt);
  if (!prompt) throw new Error("The prompt argument must contain one complete Codex-revised image prompt.");
  const jobs = Array.from({ length: count }, () =>
    args._dalle3_prompt_policy
      ? {
          prompt,
          _prompt_resolution: args._prompt_resolution,
        }
      : { prompt },
  );
  return submitBatchJobs(command, { ...args, jobs, return_when: args.return_when ?? "submitted" }, jobs, prompt);
}

async function submitBatchJobs(command, args, jobs, legacyPrompt = null) {
  const returnWhen = args.dry_run ? "completed" : batchReturnWhen(args);
  const provider = await resolveProvider(args.provider, !args.dry_run);
  if (isDalle3PromptPolicyProvider(provider) && !args._dalle3_prompt_policy) {
    args = { ...args, provider: provider.name, _dalle3_prompt_policy: true };
  }
  const root = transportOutputRoot(args, provider);
  const outputDir = finalOutputRoot(args, provider);
  const taskId = createTaskId(`${command}_batch`);
  const batchRoot = join(root, taskId);
  if (!args.dry_run) {
    await mkdir(batchRoot, { recursive: true });
  }

  const needsLatestImages =
    command === "edit" && (args.use_latest || jobs.some((job) => Boolean(job.use_latest)));
  const latestImages = needsLatestImages ? await loadLatestImages(provider.config) : [];
  const normalizedJobs = jobs.map((job) => {
    const jobArgs = enforceQualityPolicy(jobArgsFromBatchArgs(args, job));
    if (command === "edit" && latestImages.length && (args.use_latest || jobArgs.use_latest)) {
      const images = Array.isArray(jobArgs.images) ? jobArgs.images : [];
      jobArgs.images = [...images, ...latestImages];
      jobArgs.use_latest = false;
    }
    if (args._dalle3_prompt_policy) {
      jobArgs._prompt_resolution = resolveProviderPrompt(jobArgs, provider);
    }
    return jobArgs;
  });
  const submittedDate = new Date();
  const submittedAt = submittedDate.toISOString();
  const outputTimestamp = timestampForPath(submittedDate);
  const taskRequest = {
    model: provider.model,
    jobs: normalizedJobs.map((job) =>
      sanitizeRequest({
        model: provider.model,
        prompt: job._prompt_resolution?.submittedPrompt ?? job.prompt,
        size: job.size,
        aspect: job.aspect,
        resolution: job.resolution,
        image_size: job.image_size,
        quality: job.quality,
        moderation: job.moderation,
        background: job.background,
        output_format: job.output_format,
        output_mime_type: job.output_mime_type,
        response_format: job.response_format,
        parameters: job.parameters,
        image_config: job.image_config,
      }),
    ),
  };

  if (needsLatestImages) {
    taskRequest.use_latest_resolved_count = latestImages.length;
  }

  const task = {
    task_id: taskId,
    status: args.dry_run ? "completed" : "submitted",
    command: `${command}_batch`,
    submitted_at: submittedAt,
    updated_at: submittedAt,
    provider: provider.name,
    provider_transport: provider.transport,
    ...(provider.transportProfile ? { transport_profile: provider.transportProfile } : {}),
    ...(provider.promptProfile ? { prompt_profile: provider.promptProfile } : {}),
    provider_base_url: provider.baseUrl,
    provider_model: provider.model,
    requested_count: normalizedJobs.length,
    completed_count: 0,
    pending_count: 0,
    failed_count: 0,
    images: [],
    image_metadata: [],
    child_manifests: [],
    warnings: [],
    notes: [`batch_submitted_${normalizedJobs.length}_provider_requests`],
    revised_prompt_submitted: legacyPrompt,
    revised_prompts_submitted: normalizedJobs.map((job) => job.prompt),
    output_dir: outputDir,
    cache_dir: batchRoot,
    task_dir: dirname(taskStatePath(taskId)),
    request: taskRequest,
    orchestration_count: normalizedJobs.length,
    return_when: returnWhen,
    timings: {
      task_submitted_at: submittedAt,
    },
  };
  if (args._dalle3_prompt_policy) {
    Object.assign(task, {
      revised_prompt_source: legacyPrompt,
      revised_prompt_submitted: legacyPrompt
        ? normalizedJobs[0]?._prompt_resolution.submittedPrompt
        : undefined,
      revised_prompts_source: normalizedJobs.map((job) => job._prompt_resolution.sourcePrompt),
      revised_prompts_submitted: normalizedJobs.map((job) => job._prompt_resolution.submittedPrompt),
      source_prompt_char_counts: normalizedJobs.map((job) => job._prompt_resolution.sourcePromptChars),
      submitted_prompt_char_counts: normalizedJobs.map((job) => job._prompt_resolution.submittedPromptChars),
      prompt_policies_applied: normalizedJobs.map((job) => job._prompt_resolution.promptPolicyApplied),
      prompt_preparations: normalizedJobs.map((job) => job._prompt_resolution.promptPreparation),
    });
  }
  await writeTaskState(task);

  const execute = async () => {
    task.status = "running";
    task.started_at = isoNow();
    task.updated_at = task.started_at;
    task.timings.task_started_at = task.started_at;
    await writeTaskState(task);

    const settledResults = await mapAllSettled(
      normalizedJobs.length,
      async (index) => {
        const requestOutputDir = join(batchRoot, `request_${String(index + 1).padStart(2, "0")}`);
        const jobStartedAt = isoNow();
        try {
          const result = await runSingleImageCommand(command, {
            ...normalizedJobs[index],
            _transport_output_dir: requestOutputDir,
            _pending_total_timeout: PENDING_TOTAL_TIMEOUT_SECONDS,
            _pending_fast_window: PENDING_POLL_FAST_WINDOW_SECONDS,
            _pending_fast_interval: PENDING_POLL_FAST_INTERVAL_SECONDS,
            _pending_slow_interval: PENDING_POLL_SLOW_INTERVAL_SECONDS,
            output_timestamp: outputTimestamp,
            output_sequence: index + 1,
          }, provider);
          const jobFinishedAt = isoNow();
          result.job_index = index + 1;
          result.timing = {
            ...result.timing,
            mcp_request_started_at: jobStartedAt,
            mcp_request_finished_at: jobFinishedAt,
          };
          return result;
        } catch (error) {
          if (error && typeof error === "object") {
            error.timing = {
              mcp_request_started_at: jobStartedAt,
              mcp_request_finished_at: isoNow(),
            };
          }
          throw error;
        }
      },
      async (_index, _settled, currentResults) => {
        updateTaskProgressFromSettled(task, currentResults);
        task.updated_at = isoNow();
        await writeTaskState(task);
      },
    );

    try {
      const combined = await combineBatchResults({
        command: `${command}_batch`,
        args,
        prompt: legacyPrompt,
        jobs: normalizedJobs,
        provider,
        count: normalizedJobs.length,
        root,
        batchRoot,
        settledResults,
      });
      const completedAt = isoNow();
      Object.assign(task, combined, {
        task_id: taskId,
        status: batchStatusFromCounts(combined.completed_count, combined.pending_count, combined.failed_count),
        requested_count: normalizedJobs.length,
        completed_at: completedAt,
        updated_at: completedAt,
        timings: {
          ...task.timings,
          ...combined.timings,
          task_completed_at: completedAt,
        },
      });
    } catch (error) {
      const completedAt = isoNow();
      Object.assign(task, {
        status: "failed",
        error: errorMessage(error),
        completed_at: completedAt,
        updated_at: completedAt,
        timings: {
          ...task.timings,
          task_completed_at: completedAt,
        },
      });
    }
    await writeTaskState(task);
    return task;
  };

  if (returnWhen === "completed") {
    const executePromise = execute().catch(async (error) => {
      const completedAt = isoNow();
      Object.assign(task, {
        status: "failed",
        error: errorMessage(error),
        completed_at: completedAt,
        updated_at: completedAt,
        timings: {
          ...task.timings,
          task_completed_at: completedAt,
        },
      });
      await writeTaskState(task);
      return task;
    });
    const foregroundTimeout = timeoutResult(BATCH_FOREGROUND_WAIT_SECONDS * 1000, false);
    const finished = await Promise.race([
      executePromise.then(() => true),
      foregroundTimeout.promise,
    ]);
    foregroundTimeout.cancel();
    if (!finished) {
      if (task.completed_count > 0) {
        task.status = "partial";
      }
      task.updated_at = isoNow();
      await writeTaskState(task);
      return normalizeTaskForResponse(task, {
        verbose: Boolean(args.verbose || args.dry_run),
        includeProviderMetadata: Boolean(args.include_provider_metadata),
      });
    }
    return normalizeTaskForResponse(task, {
      verbose: Boolean(args.verbose || args.dry_run),
      includeProviderMetadata: Boolean(args.include_provider_metadata),
    });
  }

  execute().catch(async (error) => {
    const completedAt = isoNow();
    Object.assign(task, {
      status: "failed",
      error: errorMessage(error),
      completed_at: completedAt,
      updated_at: completedAt,
      timings: {
        ...task.timings,
        task_completed_at: completedAt,
      },
    });
    await writeTaskState(task);
  });

  return normalizeTaskForResponse(task, {
    verbose: Boolean(args.verbose),
    includeProviderMetadata: Boolean(args.include_provider_metadata),
  });
}

async function runSingleImageCommand(command, args, resolvedProvider = null) {
  const prompt = nonEmptyString(args.prompt);
  if (!prompt) throw new Error("The prompt argument must contain one complete Codex-revised image prompt.");

  const singleStartedAt = isoNow();
  const provider = resolvedProvider ?? (await resolveProvider(args.provider, !args.dry_run));
  args = {
    ...args,
    output_timestamp: nonEmptyString(args.output_timestamp) || timestampForPath(),
    output_sequence: positiveInteger(args.output_sequence, 1),
  };
  if (isDalle3PromptPolicyProvider(provider) && !args._dalle3_prompt_policy) {
    args = { ...args, provider: provider.name, _dalle3_prompt_policy: true };
  }
  const promptResolution = args._dalle3_prompt_policy ? resolveProviderPrompt(args, provider) : null;
  if (promptResolution?.promptPreparation) {
    promptResolution.promptPreparation = {
      ...promptResolution.promptPreparation,
      image_call_started_at: singleStartedAt,
    };
  }
  const submittedPrompt = promptResolution?.submittedPrompt ?? prompt;
  const scriptPath = providerTransportScript(provider);
  const tempDir = await mkdtemp(join(tmpdir(), "fmage-"));
  const promptFile = join(tempDir, "revised-prompt.txt");
  await writeFile(promptFile, submittedPrompt, "utf8");

  try {
    const transportArguments =
      provider.transportProfile === TRANSPORT_PROFILE_808
        ? openaiImages808Arguments(args, promptFile, provider)
        : commonArguments(args, promptFile, provider);
    const argv = [scriptPath, command, ...transportArguments];
    if (command === "edit") {
      const images = Array.isArray(args.images) ? [...args.images] : [];
      if (args.use_latest) {
        images.push(...(await loadLatestImages(provider.config)));
      }
      for (const image of images) {
        const imagePath = nonEmptyString(image);
        if (imagePath) appendOption(argv, "--image", imagePath);
      }
      if (images.length === 0) {
        throw new Error("Editing requires at least one image path/URL or use_latest=true.");
      }
    }

    const helperTimeoutSeconds =
      provider.transportProfile === TRANSPORT_PROFILE_808
        ? openaiImages808PendingTimeoutSeconds(args, provider)
        : args.timeout;
    const helperEnvironment = {
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      ...(provider.apiKey ? { FMAGE_ACTIVE_API_KEY: provider.apiKey } : {}),
    };
    if (!args.dry_run) {
      Object.assign(helperEnvironment, {
        FMAGE_DIRECT_OUTPUT_DIR: finalOutputRoot(args, provider),
        FMAGE_OUTPUT_TIMESTAMP: args.output_timestamp,
        FMAGE_OUTPUT_SEQUENCE: String(args.output_sequence),
      });
    }
    const result = await runProcess(
      pythonCommand(),
      argv,
      helperEnvironment,
      { timeoutSeconds: helperTimeoutSeconds },
    );
    const enriched = await enrichResult(result, submittedPrompt, provider, promptResolution);
    if (args._quality_policy_warning) {
      enriched.warnings = [
        ...normalizedStringArray(enriched.warnings),
        args._quality_policy_warning,
      ];
    }
    enriched.timing = {
      ...enriched.timing,
      mcp_single_started_at: singleStartedAt,
      mcp_single_finished_at: isoNow(),
    };
    return publishResultImages(enriched, args, provider);
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

function revisedPromptProperty(editing = false) {
  return {
    type: "string",
    description: editing
      ? "One complete provider-appropriate image prompt revised by the active Codex model. Identify each input image by index and role, state change-only and keep-unchanged invariants, and quote required text verbatim."
      : "One complete provider-appropriate image prompt revised by the active Codex model. Shape it for the image use case, preserve the requested composition and style, and quote required text verbatim.",
  };
}

function configuredDalle3PolicyContext(requestedProvider = null) {
  try {
    const config = readConfigForPaths();
    if (!config?.providers || typeof config.providers !== "object") return null;
    const activeProviders = configuredActiveProviders(config);
    const requested = nonEmptyString(requestedProvider);
    const candidates = requested ? activeProviders.filter((name) => name === requested) : activeProviders;
    const providerName = candidates.find((name) => {
      const raw = config.providers[name];
      return (
        raw &&
        typeof raw === "object" &&
        nonEmptyString(raw.prompt_profile) === PROMPT_PROFILE_DALLE3
      );
    });
    if (!providerName) return null;
    const raw = config.providers[providerName];
    if (!raw || typeof raw !== "object") return null;
    const configuredTransport = nonEmptyString(raw.transport);
    const transport = configuredTransport;
    const transportProfile = normalizeTransportProfile(raw.transport_profile);
    const policy = normalizePromptPolicy(
      raw.prompt_policy == null ? DALLE3_DEFAULT_PROMPT_POLICY : raw.prompt_policy,
      `providers.${providerName}.prompt_policy`,
    );
    if (!policy) return null;
    return {
      providerName,
      name: providerName,
      transport,
      transportProfile,
      promptProfile: PROMPT_PROFILE_DALLE3,
      policy,
      isDefault: activeProviders[0] === providerName,
      hasOtherActiveProviders: activeProviders.some((name) => name !== providerName),
    };
  } catch {
    return null;
  }
}

function configuredProviderRoutingGuidance() {
  const context = configuredDalle3PolicyContext();
  if (!context) return "";
  return (
    " The selected provider may apply a prompt profile automatically. Keep the complete source prompt " +
    "unchanged before provider adaptation. If this tool returns ready=false with " +
    "provider_request_sent=false, follow its next_action through prepare_prompt_dalle3, then call this " +
    "same image tool with prompt_check_id only."
  );
}

function dalle3ProviderPromptProperty() {
  return {
    type: "string",
    minLength: 1,
    description:
      `Stage 2 transport candidate for prompt_profile "${PROMPT_PROFILE_DALLE3}", used only with prompt_session_id after the initial image tool reports an over-limit source. ` +
      "Follow the latest preparation result's next_action exactly. Never replace or rewrite the staged source value, and never truncate or reduce the transport candidate to a summary. Preserve exact required text, names, counts, identities, spatial relationships, composition, edit invariants, camera, materials, lighting, style, and key avoid constraints.",
  };
}

function commonProperties(editing = false) {
  const properties = {
    prompt: revisedPromptProperty(editing),
    provider: {
      type: "string",
      description: "Active provider name or unique shorthand; omit for default.",
    },
    size: {
      type: "string",
      description: "WIDTHxHEIGHT.",
    },
    aspect: {
      type: "string",
      description: "Aspect ratio, such as 1:1, 3:4, or 16:9.",
    },
    resolution: {
      type: "string",
      description:
        "Provider-supported resolution tier, such as 512px, 1k, 2k, 3k, or 4k. Banana models are validated against their model-specific capability list.",
    },
    thinking_level: {
      type: "string",
      enum: ["minimal", "high"],
      description:
        "Optional EzAI Nano Banana 2 thinking level. Nano Banana Pro does not support this parameter.",
    },
    quality: {
      type: "string",
      enum: ["low", "medium", "high", "auto"],
      description: "Delivery tier; omit for the model default (high for gpt-image-2, medium otherwise).",
    },
    quality_user_requested: {
      type: "boolean",
      description: "True only when the user explicitly requested a non-medium tier.",
    },
    moderation: {
      type: "string",
      enum: ["low", "auto"],
      description: "Optional moderation level.",
    },
    background: {
      type: "string",
      enum: ["auto", "opaque"],
      description: "Optional background mode.",
    },
    output_format: {
      type: "string",
      enum: ["png", "jpeg", "webp"],
      description: "Optional output format.",
    },
    output_compression: {
      type: "integer",
      minimum: 0,
      maximum: 100,
      description: "JPEG/WebP compression from 0 to 100.",
    },
    response_format: {
      type: "string",
      description: "Optional JSON transport response format.",
    },
    output_dir: {
      type: "string",
      description: "Output directory override.",
    },
    timeout: {
      type: "integer",
      minimum: 1,
      description: "Optional timeout in seconds.",
    },
    dry_run: {
      type: "boolean",
      description: "Validate without a provider call.",
    },
    verbose: {
      type: "boolean",
      description: "Return full prompts/request details.",
    },
    include_provider_metadata: {
      type: "boolean",
      description:
        "Return sanitized provider response metadata and prompt provenance without enabling all verbose fields.",
    },
    embed_images: {
      type: "string",
      enum: ["auto", "never"],
      description: "Embed image bytes when auto; default is never.",
    },
  };
  if (configuredDalle3PolicyContext()) {
    properties.prompt_check_id = {
      type: "string",
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description:
        `Short-lived one-time readiness ID returned by ${TOOL_PREPARE_PROMPT_DALLE3}. ` +
        "Use it instead of prompt after an over-limit prompt-profile preparation.",
    };
  }
  return properties;
}

function jobProperties(editing = false) {
  const properties = { ...commonProperties(editing) };
  delete properties.provider;
  delete properties.output_dir;
  delete properties.timeout;
  delete properties.dry_run;
  delete properties.verbose;
  delete properties.include_provider_metadata;
  delete properties.embed_images;
  if (editing) {
    properties.images = {
      type: "array",
      items: { type: "string" },
      description: "Input image paths or URLs; identify each image by index and role in the prompt.",
    };
    properties.use_latest = {
      type: "boolean",
      description: "Use latest output when no reference is supplied.",
    };
  }
  return properties;
}

function batchProperties(editing = false) {
  const properties = { ...commonProperties(editing) };
  delete properties.prompt;
  delete properties.prompt_check_id;
  const supportsPromptProfile = Boolean(configuredDalle3PolicyContext());
  properties.jobs = {
    type: "array",
    minItems: 1,
    maxItems: 10,
    description: "Independent one-image jobs.",
    items: {
      type: "object",
      properties: jobProperties(editing),
      ...(supportsPromptProfile
        ? { anyOf: [{ required: ["prompt"] }, { required: ["prompt_check_id"] }] }
        : { required: ["prompt"] }),
      additionalProperties: false,
    },
  };
  properties.return_when = {
    type: "string",
    enum: ["submitted", "completed"],
    description: "completed waits; submitted returns task_id.",
  };
  properties.verbose = {
    type: "boolean",
    description: "Return full task state.",
  };
  properties.include_provider_metadata = {
    type: "boolean",
    description: "Return sanitized provider response metadata and prompt provenance.",
  };
  return properties;
}

function toolDefinitions() {
  const dalle3Context = configuredDalle3PolicyContext();
  const standardImageToolNames = [TOOL_GENERATE, TOOL_GENERATE_BATCH, TOOL_EDIT, TOOL_EDIT_BATCH];
  const imageToolNames = [...standardImageToolNames];
  const promptProfileHint = dalle3Context ? configuredProviderRoutingGuidance() : "";
  const tools = [
    {
      name: TOOL_REGRESS,
      title: "Fmage 图片退步",
      description:
        "Immediately apply the fixed local Fmage image-regression pipeline to one local image. " +
        "No provider request, prompt, shell command, preview, or preflight is needed.",
      inputSchema: {
        type: "object",
        properties: {
          image: {
            type: "string",
            minLength: 1,
            description: "Absolute path of the local input image.",
          },
        },
        required: ["image"],
        additionalProperties: false,
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    },
    {
      name: TOOL_TRACE_PLAN,
      title: "Trace Fmage Image Plan",
      description: "Optional read-only trace for troubleshooting; no provider call.",
      inputSchema: {
        type: "object",
        properties: {
          image_job_plan: {
            type: "object",
            description: "Image orchestration plan.",
            additionalProperties: true,
          },
          image_tool: {
            type: "string",
            enum: imageToolNames,
            description: "Planned image tool.",
          },
          call_arguments_preview: {
            type: "object",
            description: "Non-secret preview of image tool arguments.",
            additionalProperties: true,
          },
          revised_prompts: {
            type: "array",
            items: { type: "string" },
            description: "Revised prompts in request order.",
          },
          reference_images: {
            type: "array",
            items: { type: "string" },
            description: "Reference image paths or URLs.",
          },
          notes: {
            type: "array",
            items: { type: "string" },
            description: "Optional non-secret notes.",
          },
        },
        required: ["image_job_plan", "image_tool", "revised_prompts"],
        additionalProperties: false,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    {
      name: TOOL_GENERATE,
      title: "Generate Image with Fmage",
      description: `Generate one image from a complete revised prompt.${promptProfileHint}`,
      inputSchema: {
        type: "object",
        properties: commonProperties(false),
        ...(dalle3Context
          ? { anyOf: [{ required: ["prompt"] }, { required: ["prompt_check_id"] }] }
          : { required: ["prompt"] }),
        additionalProperties: false,
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    {
      name: TOOL_GENERATE_BATCH,
      title: "Generate Image Batch with Fmage",
      description: `Generate multiple independent images in one batch call.${promptProfileHint}`,
      inputSchema: {
        type: "object",
        properties: batchProperties(false),
        required: ["jobs"],
        additionalProperties: false,
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    {
      name: TOOL_EDIT,
      title: "Edit Image with Fmage",
      description: `Edit reference images with one complete revised prompt.${promptProfileHint}`,
      inputSchema: {
        type: "object",
        properties: {
          ...commonProperties(true),
          images: {
            type: "array",
            items: { type: "string" },
            description: "Input image paths or URLs; identify each image by index and role in the prompt.",
          },
          use_latest: {
            type: "boolean",
            description: "Use latest output when no reference is supplied.",
          },
        },
        ...(dalle3Context
          ? { anyOf: [{ required: ["prompt"] }, { required: ["prompt_check_id"] }] }
          : { required: ["prompt"] }),
        additionalProperties: false,
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    {
      name: TOOL_EDIT_BATCH,
      title: "Edit Image Batch with Fmage",
      description: `Edit multiple independent image jobs in one batch call.${promptProfileHint}`,
      inputSchema: {
        type: "object",
        properties: batchProperties(true),
        required: ["jobs"],
        additionalProperties: false,
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    {
      name: TOOL_TASK_STATUS,
      title: "Get Fmage Image Task Status",
      description: "Read compact async task status.",
      inputSchema: {
        type: "object",
        properties: {
          task_id: {
            type: "string",
            description: "Task id.",
          },
          verbose: {
            type: "boolean",
            description: "Return full task state.",
          },
          include_provider_metadata: {
            type: "boolean",
            description: "Return sanitized provider response metadata and prompt provenance.",
          },
          embed_images: {
            type: "string",
            enum: ["auto", "never"],
            description: "Embed image bytes only when set to auto; default is never.",
          },
        },
        required: ["task_id"],
        additionalProperties: false,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    {
      name: TOOL_STATUS,
      title: "Get Fmage Provider Status",
      description: "Show the active provider and non-secret configuration.",
      inputSchema: {
        type: "object",
        properties: {
          provider: {
            type: "string",
            description: "Active provider full name or unique active shorthand; omit for default.",
          },
        },
        additionalProperties: false,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
  ];

  if (dalle3Context) {
    const { providerName } = dalle3Context;
    tools.push({
      name: TOOL_PREPARE_PROMPT_DALLE3,
      title: `Prepare Prompt for Fmage (${providerName})`,
      description:
        `After an image tool using the ${providerName} prompt profile returns ready=false, validate its Stage 2 ` +
        "provider_prompt with prompt_session_id; it sends no provider request. When ready=true, call " +
        "the same base image tool with prompt_check_id only.",
      inputSchema: {
        type: "object",
        properties: {
          prompt_session_id: {
            type: "string",
            minLength: 64,
            maxLength: 64,
            pattern: "^[0-9a-f]{64}$",
            description:
              "Short-lived session ID returned when an initial image tool or this preparation tool reports ready=false.",
          },
          provider_prompt: dalle3ProviderPromptProperty(),
        },
        required: ["prompt_session_id", "provider_prompt"],
        additionalProperties: false,
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    });
  }

  return tools;
}

function resultText(result, options = {}) {
  const verbose = Boolean(options.verbose);
  const includeProviderMetadata = Boolean(options.includeProviderMetadata || verbose);
  const images = Array.isArray(result.images) ? result.images : [];
  const displayImages =
    Array.isArray(result.display_images) && result.display_images.length
      ? result.display_images
      : images.map(normalizeDisplayPath).filter(Boolean);
  const request = result.request ?? {};
  const isTask = Boolean(result.task_id);
  const lines = [
    isTask
      ? `Fmage task ${result.status}.`
      : result.dry_run
        ? "Fmage dry-run request prepared."
        : "Fmage request completed.",
  ];
  if (result.provider || result.provider_model) {
    const modelSuffix = result.provider_model ? ` (${result.provider_model})` : "";
    lines.push(`Provider: ${result.provider ?? "unknown"}${modelSuffix}`);
  }
  if (isTask) {
    lines.push(`Task id: ${result.task_id}`);
    const pendingCount = result.pending_count ? `, ${result.pending_count} pending` : "";
    lines.push(
      `Progress: ${result.completed_count ?? 0}/${result.requested_count ?? "?"} completed${pendingCount}, ${result.failed_count ?? 0} failed`,
    );
  }
  if (result.revised_prompt_submitted) {
    lines.push(`Revised prompt submitted:\n${result.revised_prompt_submitted}`);
  }
  const submittedPrompts = normalizedStringArray(result.revised_prompts_submitted);
  if (submittedPrompts.length) {
    lines.push(`Revised prompts submitted:\n${numberedBlock(submittedPrompts)}`);
  }
  if (request.size || result.requested_size) {
    lines.push(`Request size: ${request.size ?? result.requested_size}`);
  }
  const imageSizeLines = Array.isArray(result.image_metadata)
    ? result.image_metadata
        .map((item, index) => {
          if (
            !item ||
            typeof item !== "object" ||
            typeof item.width !== "number" ||
            typeof item.height !== "number"
          ) {
            return null;
          }
          return `image_${index + 1}: ${item.width}x${item.height}`;
        })
        .filter(Boolean)
    : [];
  if (imageSizeLines.length) {
    lines.push(`Actual image size${imageSizeLines.length === 1 ? "" : "s"}:\n${imageSizeLines.join("\n")}`);
  }
  if (shouldExposeWarnings(result, { verbose })) {
    lines.push(`Warnings:\n${normalizedStringArray(result.warnings).join("\n")}`);
  }
  if (verbose && Array.isArray(result.notes) && result.notes.length) {
    lines.push(`Notes:\n${result.notes.join("\n")}`);
  }
  const timings = result.timings ?? result.timing;
  if (verbose && timings && typeof timings === "object") {
    const timingLines = [
      "task_submitted_at",
      "task_started_at",
      "task_completed_at",
      "provider_request_started_at",
      "provider_response_completed_at",
      "download_completed_at",
      "manifest_written_at",
    ]
      .map((key) => (timings[key] ? `${key}: ${timings[key]}` : null))
      .filter(Boolean);
    if (timingLines.length) {
      lines.push(`Timings:\n${timingLines.join("\n")}`);
    }
  }
  if (displayImages.length) {
    lines.push(
      `${result.dry_run ? "Reference images" : "Saved images"}:\n${displayImages.join("\n")}`,
    );
  }
  const displayManifests =
    Array.isArray(result.display_manifests) && result.display_manifests.length
      ? result.display_manifests
      : [];
  if (displayManifests.length) {
    lines.push(`Manifest${displayManifests.length === 1 ? "" : "s"}:\n${displayManifests.join("\n")}`);
  } else if (result.display_manifest || result.manifest) {
    lines.push(`Manifest: ${result.display_manifest ?? normalizeDisplayPath(result.manifest)}`);
  }
  if (result.display_state_path) {
    lines.push(`Task state: ${result.display_state_path}`);
  }
  if (includeProviderMetadata) {
    const provenance = normalizePromptProvenance(result.prompt_provenance, {
      submitted: result.revised_prompt_submitted,
      providerRevised: result.provider_revised_prompt,
      providerRevisedPrompts: result.provider_revised_prompts,
    });
    if (provenance.provider_prompt_status) {
      lines.push(`Provider prompt status: ${provenance.provider_prompt_status}`);
    }
    if (provenance.provider_revised) {
      lines.push(`Provider revised prompt:\n${provenance.provider_revised}`);
    }
    const promptProvenances = Array.isArray(result.prompt_provenances)
      ? result.prompt_provenances
      : [];
    if (promptProvenances.length) {
      const statuses = promptProvenances.map((item, index) => {
        const normalized = normalizePromptProvenance(item);
        return `job_${index + 1}: ${normalized.provider_prompt_status ?? "not_returned"}`;
      });
      lines.push(`Provider prompt statuses:\n${statuses.join("\n")}`);
    }
    if (result.provider_response_metadata && typeof result.provider_response_metadata === "object") {
      lines.push(
        `Provider response metadata:\n${JSON.stringify(result.provider_response_metadata, null, 2)}`,
      );
    }
    if (
      Array.isArray(result.provider_response_metadata_items) &&
      result.provider_response_metadata_items.length
    ) {
      lines.push(
        `Provider response metadata by job:\n${JSON.stringify(result.provider_response_metadata_items, null, 2)}`,
      );
    }
  }
  return lines.join("\n\n");
}

function shouldEmbedImages(value, defaultValue = false) {
  const mode = nonEmptyString(value);
  if (mode === "never") return false;
  if (mode === "auto") return true;
  return defaultValue;
}

async function imageContent(result, options = {}) {
  const content = [{ type: "text", text: resultText(result, options) }];
  if (!options.embedImages || result.dry_run || !Array.isArray(result.images)) return content;

  for (const value of result.images) {
    const imagePath = nonEmptyString(value);
    const mimeType = imageMimeType(imagePath);
    if (!imagePath || !mimeType || /^https?:\/\//i.test(imagePath)) continue;

    try {
      const image = await readFile(imagePath);
      if (image.byteLength <= MAX_EMBEDDED_IMAGE_BYTES) {
        content.push({
          type: "image",
          data: image.toString("base64"),
          mimeType,
        });
      }
    } catch {
      // The normalized display path remains available when native embedding is unavailable.
    }
  }

  return content;
}

async function providerStatus(requestedProvider) {
  const provider = await resolveProvider(requestedProvider, false);
  const activeProviders = configuredActiveProviders(provider.config);
  const status = {
    config_path: CONFIG_PATH,
    active_provider: provider.config.active_provider,
    active_providers: activeProviders,
    default_provider: activeProviders[0],
    selected_provider: provider.name,
    transport: provider.transport,
    ...(provider.transportProfile ? { transport_profile: provider.transportProfile } : {}),
    ...(provider.promptProfile ? { prompt_profile: provider.promptProfile } : {}),
    base_url: provider.baseUrl,
    model: provider.model,
    ...(provider.dalle3PromptProfile
      ? {
          prompt_policy: provider.promptPolicy,
        }
      : {}),
    api_key_configured: provider.apiKeyConfigured,
    api_key_source: provider.apiKeySource,
    available_providers: Object.keys(provider.config.providers),
    output_dir: finalOutputRoot({}, provider),
    cache_dir: transportOutputRoot({}, provider),
    task_dir: taskStoreRoot(),
    ...(provider.openaiImages808
      ? {
          response_format: provider.openaiImages808.responseFormat,
          timeout_seconds: provider.openaiImages808.timeoutSeconds,
          remote_async: {
            enabled: true,
            submission_query: { async: "true" },
            status_path: "/images/tasks/{task_id}",
          },
        }
      : {}),
    ...(provider.ezaiBanana
      ? {
          response_format: provider.ezaiBanana.responseFormat,
          edit_input_modes: provider.ezaiBanana.editInputModes,
        }
      : {}),
  };
  return status;
}

async function handleToolCall(id, params) {
  if (params?.name === TOOL_REGRESS) {
    const result = await runImageRegression(params.arguments ?? {});
    sendResult(id, {
      content: [{ type: "text", text: regressionResultText(result) }],
      structuredContent: result,
    });
    return;
  }

  if (params?.name === TOOL_TRACE_PLAN) {
    const trace = {
      trace_type: "image_job_plan",
      created_at: isoNow(),
      ...(params.arguments ?? {}),
    };
    sendResult(id, {
      content: [{ type: "text", text: planTraceText(trace) }],
      structuredContent: trace,
    });
    return;
  }

  if (params?.name === TOOL_GENERATE) {
    const result = await runImageCommand("generate", params.arguments ?? {});
    if (isDalle3PreparationResult(result)) {
      sendResult(id, {
        content: [{ type: "text", text: dalle3PreparationText(result) }],
        structuredContent: result,
      });
      return;
    }
    const responseResult = compactDalle3ImageResultForResponse(result, params.arguments ?? {});
    sendResult(id, {
      content: await imageContent(responseResult, {
        embedImages: shouldEmbedImages(params.arguments?.embed_images, false),
        verbose: Boolean(params.arguments?.verbose),
        includeProviderMetadata: Boolean(params.arguments?.include_provider_metadata),
      }),
      structuredContent: responseResult,
    });
    return;
  }

  if (params?.name === TOOL_GENERATE_BATCH) {
    const result = await runBatchImageCommand("generate", params.arguments ?? {});
    if (isDalle3PreparationResult(result)) {
      sendResult(id, {
        content: [{ type: "text", text: dalle3PreparationText(result) }],
        structuredContent: result,
      });
      return;
    }
    const responseResult = compactDalle3ImageResultForResponse(result, params.arguments ?? {});
    sendResult(id, {
      content: await imageContent(responseResult, {
        embedImages: shouldEmbedImages(params.arguments?.embed_images, false),
        verbose: Boolean(params.arguments?.verbose),
        includeProviderMetadata: Boolean(params.arguments?.include_provider_metadata),
      }),
      structuredContent: responseResult,
    });
    return;
  }

  if (params?.name === TOOL_PREPARE_PROMPT_DALLE3) {
    const result = prepareDalle3ProviderPrompt(params.arguments ?? {});
    sendResult(id, {
      content: [{ type: "text", text: dalle3PreparationText(result) }],
      structuredContent: result,
    });
    return;
  }

  if (params?.name === TOOL_EDIT) {
    const result = await runImageCommand("edit", params.arguments ?? {});
    if (isDalle3PreparationResult(result)) {
      sendResult(id, {
        content: [{ type: "text", text: dalle3PreparationText(result) }],
        structuredContent: result,
      });
      return;
    }
    const responseResult = compactDalle3ImageResultForResponse(result, params.arguments ?? {});
    sendResult(id, {
      content: await imageContent(responseResult, {
        embedImages: shouldEmbedImages(params.arguments?.embed_images, false),
        verbose: Boolean(params.arguments?.verbose),
        includeProviderMetadata: Boolean(params.arguments?.include_provider_metadata),
      }),
      structuredContent: responseResult,
    });
    return;
  }

  if (params?.name === TOOL_EDIT_BATCH) {
    const result = await runBatchImageCommand("edit", params.arguments ?? {});
    if (isDalle3PreparationResult(result)) {
      sendResult(id, {
        content: [{ type: "text", text: dalle3PreparationText(result) }],
        structuredContent: result,
      });
      return;
    }
    const responseResult = compactDalle3ImageResultForResponse(result, params.arguments ?? {});
    sendResult(id, {
      content: await imageContent(responseResult, {
        embedImages: shouldEmbedImages(params.arguments?.embed_images, false),
        verbose: Boolean(params.arguments?.verbose),
        includeProviderMetadata: Boolean(params.arguments?.include_provider_metadata),
      }),
      structuredContent: responseResult,
    });
    return;
  }

  if (params?.name === TOOL_TASK_STATUS) {
    const result = await readTaskStateWithOptions(params.arguments?.task_id, {
      verbose: Boolean(params.arguments?.verbose),
      includeProviderMetadata: Boolean(params.arguments?.include_provider_metadata),
    });
    sendResult(id, {
      content: await imageContent(result, {
        embedImages: shouldEmbedImages(params.arguments?.embed_images, false),
        verbose: Boolean(params.arguments?.verbose),
        includeProviderMetadata: Boolean(params.arguments?.include_provider_metadata),
      }),
      structuredContent: result,
    });
    return;
  }

  if (params?.name === TOOL_STATUS) {
    const status = await providerStatus(params.arguments?.provider);
    sendResult(id, {
      content: [
        {
          type: "text",
          text:
            `Active providers: ${status.active_providers.join(", ")}\n` +
            `Default provider: ${status.default_provider ?? ""}\n` +
            `Selected provider: ${status.selected_provider}\n` +
            `Model: ${status.model}\n` +
            (status.prompt_policy ? `Prompt policy: ${JSON.stringify(status.prompt_policy)}\n` : "") +
            `Base URL: ${status.base_url}\n` +
            `API key configured: ${status.api_key_configured}\n` +
            `Config: ${status.config_path}`,
        },
      ],
      structuredContent: status,
    });
    return;
  }

  sendError(id, JsonRpcError.INVALID_PARAMS, `Unknown tool: ${params?.name ?? ""}`);
}

async function handleRequest(message) {
  const { id, method, params } = message;

  if (method === "initialize") {
    sendResult(id, {
      protocolVersion: params?.protocolVersion ?? "2025-11-25",
      capabilities: { tools: {} },
      serverInfo: {
        name: SERVER_NAME,
        version: SERVER_VERSION,
      },
      instructions: BASE_INITIALIZE_INSTRUCTIONS + configuredProviderRoutingGuidance(),
    });
    return;
  }

  if (method === "ping") {
    sendResult(id, {});
    return;
  }

  if (method === "tools/list") {
    sendResult(id, { tools: toolDefinitions() });
    return;
  }

  if (method === "tools/call") {
    try {
      await handleToolCall(id, params);
    } catch (error) {
      sendError(
        id,
        JsonRpcError.INTERNAL_ERROR,
        error instanceof Error ? error.message : String(error),
      );
    }
    return;
  }

  if (id !== undefined) {
    sendError(id, JsonRpcError.METHOD_NOT_FOUND, `Method not found: ${method}`);
  }
}

const lines = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

lines.on("line", (line) => {
  if (!line.trim()) return;

  let message;
  try {
    message = JSON.parse(line);
  } catch {
    return;
  }

  handleRequest(message).catch((error) => {
    if (message.id !== undefined) {
      sendError(
        message.id,
        JsonRpcError.INTERNAL_ERROR,
        error instanceof Error ? error.message : String(error),
      );
    }
  });
});
