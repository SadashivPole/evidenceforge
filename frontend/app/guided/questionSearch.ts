/**
 * Deterministic, workspace-local question discovery.
 *
 * This module is deliberately free of network calls, model inference, and
 * vector search. It normalizes words and ranks REAL questionnaire questions
 * supplied by the authorized browsing endpoints. The caller is responsible for
 * passing only questions loaded from the selected workspace.
 */

export type QuestionSearchEntry = {
  id: string;
  ordinal: number;
  source_row: number;
  sheet_name: string;
  section_path: string[];
  question_text: string;
  source_question_id: string | null;
  stable_question_id: string;
  workspaceId: string;
  workspaceName: string;
  questionnaireId: string;
  questionnaireName: string;
  versionId: string;
  versionNumber: number;
  versionSourceFilename: string;
};

export type QuestionSearchMatch = {
  entry: QuestionSearchEntry;
  score: number;
  matchedTerms: string[];
  exactPhraseMatch: boolean;
};

export type QuestionSearchOutcome = {
  status: "NO_MATCHES" | "AMBIGUOUS" | "SINGLE";
  matches: QuestionSearchMatch[];
};

const STOP_WORDS = new Set([
  "a",
  "about",
  "all",
  "an",
  "and",
  "any",
  "are",
  "as",
  "at",
  "be",
  "been",
  "by",
  "can",
  "could",
  "did",
  "do",
  "does",
  "for",
  "from",
  "has",
  "have",
  "how",
  "if",
  "in",
  "is",
  "it",
  "its",
  "may",
  "of",
  "on",
  "or",
  "our",
  "shall",
  "should",
  "that",
  "the",
  "their",
  "there",
  "these",
  "this",
  "to",
  "was",
  "we",
  "were",
  "what",
  "when",
  "where",
  "which",
  "who",
  "why",
  "will",
  "with",
  "would",
  "you",
  "your",
]);

// Small, transparent word-normalization table. This is lexical normalization,
// not semantic understanding and not an LLM. It only helps ordinary word forms
// such as "encrypted"/"encryption" meet on one canonical term.
const TERM_ALIASES: Record<string, string> = {
  accessed: "access",
  accesses: "access",
  accessing: "access",
  assessed: "assess",
  assessment: "assess",
  assessments: "assess",
  authenticated: "authenticate",
  authentication: "authenticate",
  authorize: "authorize",
  authorized: "authorize",
  authorization: "authorize",
  backups: "backup",
  controlled: "control",
  controlling: "control",
  controls: "control",
  encrypt: "encrypt",
  encrypted: "encrypt",
  encrypting: "encrypt",
  encryption: "encrypt",
  encrypts: "encrypt",
  incidents: "incident",
  logged: "log",
  logging: "log",
  logs: "log",
  policies: "policy",
  retain: "retention",
  retained: "retention",
  retention: "retention",
  rotate: "rotate",
  rotated: "rotate",
  rotates: "rotate",
  rotating: "rotate",
  rotation: "rotate",
  reviewed: "review",
  reviewing: "review",
  reviews: "review",
};

const MIN_MATCH_SCORE = 2.4;
const AMBIGUITY_RATIO = 0.86;
const AMBIGUITY_ABSOLUTE_GAP = 1.2;

function normalizeText(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function canonicalTerm(raw: string): string {
  const alias = TERM_ALIASES[raw];
  if (alias) return alias;

  let term = raw;
  if (term.length > 4 && term.endsWith("ies")) {
    term = `${term.slice(0, -3)}y`;
  } else if (term.length > 4 && term.endsWith("sses")) {
    term = term.slice(0, -2);
  } else if (term.length > 3 && term.endsWith("s") && !term.endsWith("ss")) {
    term = term.slice(0, -1);
  }
  if (term.length > 5 && term.endsWith("ed")) {
    term = term.slice(0, -2);
  } else if (term.length > 6 && term.endsWith("ing")) {
    term = term.slice(0, -3);
  }

  return TERM_ALIASES[term] ?? term;
}

/** Return unique, lowercased, stop-word-free terms from a piece of text. */
export function meaningfulTerms(value: string): string[] {
  return [
    ...new Set(
      normalizeText(value)
        .split(/\s+/)
        .filter(Boolean)
        .filter((term) => term.length > 1 && !STOP_WORDS.has(term))
        .map(canonicalTerm),
    ),
  ];
}

/** Terms shared by two strings, after the same deterministic normalization. */
export function sharedMeaningfulTerms(left: string, right: string): string[] {
  const rightTerms = new Set(meaningfulTerms(right));
  return meaningfulTerms(left).filter((term) => rightTerms.has(term));
}

function termDocumentFrequency(
  entries: QuestionSearchEntry[],
): Map<string, number> {
  const frequency = new Map<string, number>();
  for (const entry of entries) {
    const terms = new Set(
      meaningfulTerms(
        [
          entry.question_text,
          entry.section_path.join(" "),
          entry.sheet_name,
        ].join(" "),
      ),
    );
    for (const term of terms) {
      frequency.set(term, (frequency.get(term) ?? 0) + 1);
    }
  }
  return frequency;
}

function inverseDocumentFrequency(
  term: string,
  totalDocuments: number,
  frequency: Map<string, number>,
): number {
  const containing = frequency.get(term) ?? 0;
  return 1 + Math.log((totalDocuments + 1) / (containing + 1));
}

/**
 * Rank real question records against a user's wording. A non-exact candidate
 * must share at least two meaningful terms with the query (or one term when the
 * query itself has only one meaningful term). This prevents arbitrary mapping
 * of an unrelated question while still allowing ordinary paraphrases such as
 * "Do you encrypt backups?" → "Are backups encrypted?".
 */
export function findQuestionMatches(
  query: string,
  entries: QuestionSearchEntry[],
  limit = 6,
): QuestionSearchMatch[] {
  const queryTerms = meaningfulTerms(query);
  if (!queryTerms.length || !entries.length) return [];

  const normalizedQuery = normalizeText(query);
  const frequency = termDocumentFrequency(entries);
  const totalDocuments = entries.length;
  const requiredSharedTerms = queryTerms.length === 1 ? 1 : 2;
  const matches: QuestionSearchMatch[] = [];

  for (const entry of entries) {
    const normalizedQuestion = normalizeText(entry.question_text);
    const questionTerms = new Set(meaningfulTerms(entry.question_text));
    const contextTerms = new Set(
      meaningfulTerms([...entry.section_path, entry.sheet_name].join(" ")),
    );
    const matchedQuestionTerms = queryTerms.filter((term) =>
      questionTerms.has(term),
    );
    const matchedContextTerms = queryTerms.filter(
      (term) => contextTerms.has(term) && !questionTerms.has(term),
    );
    const exactPhraseMatch =
      normalizedQuery.length >= 3 &&
      (normalizedQuestion.includes(normalizedQuery) ||
        (normalizedQuestion.length >= 3 &&
          normalizedQuery.includes(normalizedQuestion)));

    if (
      !exactPhraseMatch &&
      matchedQuestionTerms.length < requiredSharedTerms
    ) {
      continue;
    }

    let score = 0;
    for (const term of matchedQuestionTerms) {
      score += 2.2 * inverseDocumentFrequency(term, totalDocuments, frequency);
    }
    for (const term of matchedContextTerms) {
      score += 0.45 * inverseDocumentFrequency(term, totalDocuments, frequency);
    }
    score += (matchedQuestionTerms.length / queryTerms.length) * 1.5;
    if (exactPhraseMatch) score += 6;
    if (score < MIN_MATCH_SCORE) continue;

    matches.push({
      entry,
      score,
      matchedTerms: [...matchedQuestionTerms, ...matchedContextTerms],
      exactPhraseMatch,
    });
  }

  return matches
    .sort(
      (left, right) =>
        right.score - left.score ||
        left.entry.ordinal - right.entry.ordinal ||
        left.entry.question_text.localeCompare(right.entry.question_text),
    )
    .slice(0, limit);
}

/**
 * Classify a ranked match list. A close runner-up is shown as an explicit
 * ambiguity instead of being silently selected.
 */
export function searchQuestions(
  query: string,
  entries: QuestionSearchEntry[],
  limit = 6,
): QuestionSearchOutcome {
  const matches = findQuestionMatches(query, entries, limit);
  if (!matches.length) return { status: "NO_MATCHES", matches: [] };

  const [top, runnerUp] = matches;
  const ambiguous =
    runnerUp !== undefined &&
    (runnerUp.score >= top.score * AMBIGUITY_RATIO ||
      top.score - runnerUp.score < AMBIGUITY_ABSOLUTE_GAP);

  return {
    status: ambiguous ? "AMBIGUOUS" : "SINGLE",
    matches,
  };
}
