import type { KbPage } from "./types";

/**
 * A page shows the interactive footer when either the rating widget or chat is
 * available. Chat is enabled on every analysis kind (blog, tweet, podcast,
 * synthesis). Rating is enabled on the same set minus `slot: manual` pages
 * (the user's personal URL submissions — no agent feedback loop to close).
 */
export function canInteract(page: KbPage): boolean {
  return page.canChat || page.canRate;
}
