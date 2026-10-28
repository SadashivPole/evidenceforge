/**
 * Deterministic next-question recommendation.
 *
 * Recommendations use only real questionnaire questions, the current
 * question's wording, evidence excerpts actually returned by grounding, and
 * saved response status supplied by the authorized response-list endpoint.
 * There is no model call and no generated question text.
 */

import { meaningfulTerms, type QuestionSearchEntry } from "./questionSearch";

export type QuestionResponseSummary = {
  questionId: string;
  status: string;
  answer: string | null;
};

export type NextQuestionBasis = "UNFINISHED" | "SHARED" | "NEXT";

export type NextQuestionSuggestion = {
  question: QuestionSearchEntry;
  basis: NextQuestionBasis;
  label: string;
  detail: string;
  sharedTerms: string[];
};

export type SuggestNextQuestionInput = {
  current: QuestionSearchEntry;
  questions: QuestionSearchEntry[];
  responses: QuestionResponseSummary[];
  responseStatusAvailable: boolean;
  evidenceTexts: string[];
};

function readableStatus(status: string): string {
  return status
    .toLowerCase()
    .split("_")
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

/**
 * Prefer the first later question that the real response list shows as
 * unfinished. If response status is unavailable, or all later questions are
 * approved, fall back to transparent lexical overlap with the current
 * question/evidence and finally to questionnaire order.
 */
export function suggestNextQuestion(
  input: SuggestNextQuestionInput,
): NextQuestionSuggestion | null {
  const later = input.questions
    .filter(
      (question) =>
        question.workspaceId === input.current.workspaceId &&
        question.questionnaireId === input.current.questionnaireId &&
        question.versionId === input.current.versionId &&
        question.id !== input.current.id &&
        question.ordinal > input.current.ordinal,
    )
    .sort((left, right) => left.ordinal - right.ordinal);
  if (!later.length) return null;

  const responsesById = new Map(
    input.responses.map((response) => [response.questionId, response]),
  );

  if (input.responseStatusAvailable) {
    const unfinished = later.find((question) => {
      const response = responsesById.get(question.id);
      return (
        !response ||
        response.status !== "APPROVED" ||
        !(response.answer ?? "").trim()
      );
    });
    if (unfinished) {
      const response = responsesById.get(unfinished.id);
      return {
        question: unfinished,
        basis: "UNFINISHED",
        label: response
          ? "Unfinished response in this version"
          : "No saved response in this version",
        detail: response
          ? `The authorized response list reports “${readableStatus(response.status)}” for this question.`
          : "The authorized response list has no saved revision for this question.",
        sharedTerms: [],
      };
    }
  }

  const contextTerms = new Set(
    meaningfulTerms(
      [input.current.question_text, ...input.evidenceTexts].join(" "),
    ),
  );
  let best: { question: QuestionSearchEntry; shared: string[] } | null = null;
  for (const question of later) {
    const shared = meaningfulTerms(question.question_text).filter((term) =>
      contextTerms.has(term),
    );
    if (shared.length < 2) continue;
    if (!best || shared.length > best.shared.length) {
      best = { question, shared };
    }
  }
  if (best) {
    return {
      question: best.question,
      basis: "SHARED",
      label: "Suggested based on shared terms/evidence",
      detail: `Shares ${best.shared
        .slice(0, 4)
        .join(", ")} with the current question or retrieved evidence.`,
      sharedTerms: best.shared,
    };
  }

  const next = later[0];
  return {
    question: next,
    basis: "NEXT",
    label: input.responseStatusAvailable
      ? "Next in questionnaire"
      : "Next in questionnaire (status unavailable)",
    detail: input.responseStatusAvailable
      ? "Later questions already have approved saved responses; this is the next question by imported order."
      : "Saved-response status could not be loaded, so this is the next imported question by order.",
    sharedTerms: [],
  };
}
