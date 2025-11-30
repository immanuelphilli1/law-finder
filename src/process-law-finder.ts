/**
 * Batch workflow that walks the legacy “LAW FINDER” HTML corpus, extracts plain text,
 * and asks an OpenRouter-hosted LLM (via the AI SDK) to produce a structured JSON payload
 * per case for downstream retrieval/RAG indexing.
 */
import path from 'node:path';
import { parseArgs } from 'node:util';
import { promises as fs } from 'node:fs';
import { config as loadEnv } from 'dotenv';
import { htmlToText } from 'html-to-text';
import { glob } from 'glob';
import pLimit from 'p-limit';
import { createOpenAI } from '@ai-sdk/openai';
import { generateObject } from 'ai';
import { z } from 'zod';

loadEnv();

/**
 * Contract we expect every LLM response to honor. Using Zod ensures the model output
 * is validated/coerced before writing to disk, which keeps the downstream JSON uniform.
 */
const CaseExtractionSchema = z.object({
  summary: z.string().min(40, 'summary should be descriptive'),
  legalKeywords: z.array(z.string().min(2)).min(5).max(30),
  topics: z.array(z.string()).min(1).max(10),
  verdict: z.string().min(3),
  winningSide: z.string().min(3),
  prosecutionType: z.enum(['state', 'private', 'civil', 'unknown']),
  judges: z
    .array(
      z.object({
        name: z.string(),
        role: z.string().optional()
      })
    )
    .max(15),
  jurisdiction: z.object({
    court: z.string().optional(),
    country: z.string().optional()
  }),
  caseType: z.string().optional(),
  trialDate: z.string().optional(),
  citations: z.array(z.string()).max(10).optional(),
  rationaleHighlights: z.array(z.string()).max(8).optional(),
  confidenceNotes: z.string().optional()
});

type CaseExtraction = z.infer<typeof CaseExtractionSchema>;

const MAX_CHARS = Number(process.env.LAW_FINDER_MAX_CHARS ?? 15000);
const DEFAULT_CONCURRENCY = Number(process.env.LAW_FINDER_CONCURRENCY ?? 2);
const DEFAULT_MODEL = process.env.OPENROUTER_MODEL ?? 'qwen/qwen3-235b-a22b-2507';
const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY;

if (!OPENROUTER_API_KEY) {
  console.error('Missing OPENROUTER_API_KEY. Set it in your environment or .env file.');
  process.exit(1);
}

const openrouter = createOpenAI({
  apiKey: OPENROUTER_API_KEY,
  baseURL: 'https://openrouter.ai/api/v1',
  headers: {
    'HTTP-Referer': process.env.OPENROUTER_REFERRER ?? 'https://law-finder-ai.local',
    'X-Title': process.env.OPENROUTER_TITLE ?? 'Law Finder AI'
  }
});

const { values } = parseArgs({
  options: {
    input: { type: 'string', short: 'i' },
    output: { type: 'string', short: 'o' },
    limit: { type: 'string', short: 'l' },
    concurrency: { type: 'string', short: 'c' },
    model: { type: 'string', short: 'm' },
    dryRun: { type: 'boolean', short: 'd' }
  }
});

const inputDir = values.input ?? process.env.LAW_FINDER_INPUT ?? path.resolve('LAW FINDER');
const outputDir = values.output ?? process.env.LAW_FINDER_OUTPUT ?? path.resolve('law-finder-json');
const modelName = values.model ?? DEFAULT_MODEL;
const concurrency = Number(values.concurrency ?? DEFAULT_CONCURRENCY);
const fileLimit = values.limit ? Number(values.limit) : undefined;
const dryRun = Boolean(values.dryRun);

async function ensureDir(dirPath: string) {
  await fs.mkdir(dirPath, { recursive: true });
}

/**
 * Extract case title from HTML body content. The <title> tag often contains "pages.gif"
 * as a template default, so we look for case names in the body (typically in bold/centered text).
 * Pattern: "PLAINTIFF v. DEFENDANT" or "PLAINTIFF v DEFENDANT" (with optional date in brackets)
 */
function extractTitle(html: string, plainText?: string): string | undefined {
  // First, try extracting from plain text if provided (more reliable than HTML parsing)
  if (plainText) {
    // Look for case title pattern in plain text: "NAME v. NAME" or "NAME v NAME"
    // Usually appears early in the document, often in all caps
    const textLines = plainText.split('\n').slice(0, 50); // Check first 50 lines
    for (const line of textLines) {
      const trimmed = line.trim();
      // Pattern: All caps or mixed case with " v. " or " v " followed by name
      if (
        trimmed.length > 15 &&
        trimmed.length < 200 &&
        (trimmed.includes(' v. ') || trimmed.includes(' v ')) &&
        !trimmed.includes('GHANA LAW FINDER') &&
        !trimmed.includes('HOME') &&
        !trimmed.includes('pages.gif')
      ) {
        // Remove date brackets and clean up
        const cleaned = trimmed.replace(/\s*\[[^\]]+\]\s*$/, '').trim();
        if (cleaned.length > 10) {
          return cleaned;
        }
      }
    }
  }

  // Fallback: Look for case title patterns in HTML
  const caseTitlePatterns = [
    // Pattern with "v." or "v" followed by name, optionally with date in brackets
    /<[^>]*>(?:<[^>]*>)*([A-Z][A-Z\s&.,'-]+(?:\s+v\.?\s+[A-Z][A-Z\s&.,'-]+)+)(?:\s*\[[^\]]+\])?/i,
    // Pattern in bold tags
    /<b[^>]*>([A-Z][A-Z\s&.,'-]+(?:\s+v\.?\s+[A-Z][A-Z\s&.,'-]+)+)/i,
    // Pattern in font tags with bold styling
    /<font[^>]*font-weight:\s*700[^>]*>([A-Z][A-Z\s&.,'-]+(?:\s+v\.?\s+[A-Z][A-Z\s&.,'-]+)+)/i
  ];

  for (const pattern of caseTitlePatterns) {
    const match = html.match(pattern);
    if (match && match[1]) {
      const candidate = cleanWhitespace(match[1]);
      // Filter out common false positives
      if (
        candidate.length > 10 &&
        !candidate.includes('pages.gif') &&
        !candidate.includes('GHANA LAW FINDER') &&
        !candidate.includes('HOME') &&
        (candidate.includes(' v. ') || candidate.includes(' v '))
      ) {
        // Remove date brackets if present in the match
        return candidate.replace(/\s*\[[^\]]+\]\s*$/, '').trim();
      }
    }
  }

  return undefined;
}

/**
 * Extract trial/decision date from HTML. Dates appear in various formats:
 * - In brackets: [30/1/2003], [15/01/2004]
 * - As text: "February 25, 1956", "15TH JAN., 2004", "27th June, 2002"
 * - As "DATE - 15TH JAN., 2004"
 */
function extractDate(html: string): string | undefined {
  // Pattern 1: Date in brackets near case title: [30/1/2003], [15/01/2004]
  const bracketDateMatch = html.match(/\[(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})\]/);
  if (bracketDateMatch && bracketDateMatch[1]) {
    return bracketDateMatch[1];
  }

  // Pattern 2: "DATE - 15TH JAN., 2004" or "DATE: 15TH JAN., 2004"
  const dateLabelMatch = html.match(/DATE\s*[:\-]\s*([A-Z0-9\s,\.]+(?:19|20)\d{2})/i);
  if (dateLabelMatch && dateLabelMatch[1]) {
    return cleanWhitespace(dateLabelMatch[1]);
  }

  // Pattern 3: Full date text like "February 25, 1956" or "27th June, 2002"
  const fullDateMatch = html.match(/(?:January|February|March|April|May|June|July|August|September|October|November|December)[\s\d,]+(?:19|20)\d{2}/i);
  if (fullDateMatch) {
    return cleanWhitespace(fullDateMatch[0]);
  }

  // Pattern 4: Ordinal date like "27th June, 2002" or "15TH JAN., 2004"
  const ordinalDateMatch = html.match(/\d{1,2}(?:st|nd|rd|th|ST|ND|RD|TH)\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\s,]+(?:19|20)\d{2}/i);
  if (ordinalDateMatch) {
    return cleanWhitespace(ordinalDateMatch[0]);
  }

  return undefined;
}

function cleanWhitespace(input: string): string {
  return input.replace(/\s+/g, ' ').replace(/&nbsp;/gi, ' ').trim();
}

function sanitizeText(html: string): string {
  return htmlToText(html, {
    wordwrap: 120,
    selectors: [
      { selector: 'a', options: { ignoreHref: true } },
      { selector: 'img', format: 'skip' },
      { selector: 'script', format: 'skip' },
      { selector: 'style', format: 'skip' }
    ]
  });
}

/**
 * Ingest a single HTML file: strip markup, call the LLM, and persist the normalized JSON.
 */
async function processFile(htmlPath: string, options: { relativePath: string }): Promise<void> {
  const raw = await fs.readFile(htmlPath, 'utf8');
  const plainText = sanitizeText(raw);
  // Extract title from both HTML and plain text (plain text is more reliable)
  const extractedTitle = extractTitle(raw, plainText);
  const extractedDate = extractDate(raw);
  // Fallback to filename if title extraction failed
  const caseTitle = extractedTitle ?? path.basename(htmlPath, path.extname(htmlPath));
  const truncated = plainText.length > MAX_CHARS ? `${plainText.slice(0, MAX_CHARS)}\n[TRUNCATED]` : plainText;
  const userPrompt = String.raw`Summarize and extract insights from the following Ghanaian legal decision. Keep factual accuracy high.

Source file: ${options.relativePath}
Case title: ${caseTitle}${extractedDate ? `\nTrial/Decision date: ${extractedDate}` : ''}

Document:
"""
${truncated}
"""`;

  if (dryRun) {
    console.log(`[dry-run] Would process ${options.relativePath}`);
    return;
  }

  const { object } = await generateObject({
    model: openrouter(modelName),
    schema: CaseExtractionSchema,
    messages: [
      {
        role: 'system',
        content: `You are a senior legal analyst helping build a retrieval dataset. Extract precise, citation-ready information. Always answer with the structured schema fields. Pay special attention to extracting trial dates, decision dates, and hearing dates from the document text.`
      },
      {
        role: 'user',
        content: userPrompt
      }
    ]
  });

  const enriched = buildOutputPayload({
    extraction: object,
    caseTitle,
    extractedDate,
    sourcePath: options.relativePath,
    plainText
  });

  const outPath = path.join(outputDir, `${options.relativePath.replace(/[\\/]/g, '__').replace(/\.[^.]+$/, '')}.json`);
  await ensureDir(path.dirname(outPath));
  await fs.writeFile(outPath, JSON.stringify(enriched, null, 2), 'utf8');
  console.log(`Saved ${outPath}`);
}

function buildOutputPayload(params: {
  extraction: CaseExtraction;
  caseTitle: string;
  extractedDate?: string;
  sourcePath: string;
  plainText: string;
}) {
  const { extraction, caseTitle, extractedDate, sourcePath, plainText } = params;
  // Prefer LLM-extracted date, but fall back to regex-extracted date if LLM didn't find one
  const trialDate = extraction.trialDate || extractedDate || null;
  return {
    caseTitle,
    summary: extraction.summary,
    legalKeywords: extraction.legalKeywords,
    topics: extraction.topics,
    verdict: extraction.verdict,
    winningSide: extraction.winningSide,
    prosecutionType: extraction.prosecutionType,
    judges: extraction.judges,
    jurisdiction: extraction.jurisdiction,
    caseType: extraction.caseType,
    trialDate,
    citations: extraction.citations ?? [],
    rationaleHighlights: extraction.rationaleHighlights ?? [],
    confidenceNotes: extraction.confidenceNotes ?? null,
    metadata: {
      sourcePath,
      model: modelName,
      processedAt: new Date().toISOString(),
      plainTextLength: plainText.length
    }
  };
}

/**
 * Entry point – globs all `.htm*` files, enforces concurrency, and runs the pipeline.
 */
async function main() {
  await ensureDir(outputDir);
  const pattern = path.join(inputDir, '**', '*.htm*');
  const files = await glob(pattern, { nodir: true, absolute: true });

  if (!files.length) {
    console.warn(`No HTML files found under ${inputDir}`);
    return;
  }

  const limitedFiles = typeof fileLimit === 'number' ? files.slice(0, fileLimit) : files;
  const limiter = pLimit(Math.max(1, concurrency));

  await Promise.all(
    limitedFiles.map((filePath) =>
      limiter(() => {
        const relativePath = path.relative(inputDir, filePath);
        return processFile(filePath, { relativePath });
      })
    )
  );
}

main().catch((error) => {
  console.error('Processing failed:', error);
  process.exitCode = 1;
});
