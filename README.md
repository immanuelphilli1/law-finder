# Law Finder AI

Workflow for summarising historical Ghanaian legal decisions into structured JSON suitable for a RAG index. Built with the TypeScript AI SDK and OpenRouter models.

## Prerequisites

- Node.js 18+
- An OpenRouter API key with sufficient credits
- Downloaded HTML corpus inside `LAW FINDER/` (already present)

## Setup

1. Install dependencies (first run may ask to authenticate the npm proxy):
   ```bash
   cd /Users/immanuelphillips/Downloads/law-finder-ai
   npm install
   ```
2. Copy the sample environment file and fill in your secrets:
   ```bash
   cp env.example .env
   ```
   Required variables:
   - `OPENROUTER_API_KEY`: your OpenRouter key
   - `OPENROUTER_MODEL`: e.g. `openrouter/anthropic/claude-3.5-sonnet`
   - `LAW_FINDER_INPUT`: absolute path to the HTML root (defaults to `LAW FINDER`)
   - `LAW_FINDER_OUTPUT`: where JSON files will be written
   - Optional tuning knobs: `LAW_FINDER_MAX_CHARS`, `LAW_FINDER_CONCURRENCY`

## Running the workflow

Use the bundled `tsx` runner to process every `.htm/.html` file:

```bash
npm run start -- \
  --input "/Users/immanuelphillips/Downloads/law-finder-ai/LAW FINDER" \
  --output "/Users/immanuelphillips/Downloads/law-finder-ai/law-finder-json" \
  --model "qwen/qwen3-235b-a22b-2507" \
  --concurrency 2
```

Key flags:
- `--limit N` – dry test on the first N files
- `--dryRun` – log which files would run without calling the LLM
- `--concurrency` – number of parallel LLM calls (default 2)

Outputs are mirrored into `law-finder-json/` with one JSON file per HTML source, ready to feed into your vector store or search index.

## JSON structure

Each result contains:
- `caseTitle`
- `summary`
- `legalKeywords`
- `topics`
- `verdict`
- `winningSide`
- `prosecutionType` (state/private/civil/unknown)
- `judges` list with optional roles
- `jurisdiction` metadata
- `citations`, `rationaleHighlights`, `confidenceNotes`
- `metadata` (source path, model, processed timestamp, raw text length)

## Next steps

- Add caching/checkpointing if you want resumable runs
- Feed the JSON into an embedding or graph store for your RAG engine
- Extend `CaseExtractionSchema` if more attributes are needed (parties, statutes, etc.)
# law-finder
