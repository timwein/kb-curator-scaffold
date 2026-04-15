import type { KbPage } from "./types";

/**
 * A page gets the interactive footer (rating + chat) iff it has a `user_score`
 * frontmatter field AND its kind is not `runlog`. This captures blog analyses,
 * tweet analyses, and syntheses; excludes run-logs, daily READMEs, and topics.
 */
export function canInteract(page: KbPage): boolean {
  return page.canRate; // canRate and canChat are always equal; single source of truth.
}
