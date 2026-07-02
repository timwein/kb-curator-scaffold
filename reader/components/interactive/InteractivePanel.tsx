import RatingWidget from "./RatingWidget";
import PageChat from "./PageChat";

export default function InteractivePanel({
  pagePath,
  initialScore,
  pageTitle,
  canRate,
  canChat,
}: {
  pagePath: string;
  initialScore: number | null;
  pageTitle: string;
  canRate: boolean;
  canChat: boolean;
}) {
  if (!canRate && !canChat) return null;
  return (
    <div className="mt-10 border-t border-[var(--border)] pt-8">
      {canRate && (
        <div className="mb-6 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
          <RatingWidget pagePath={pagePath} initialScore={initialScore} />
        </div>
      )}
      {canChat && <PageChat pagePath={pagePath} pageTitle={pageTitle} />}
    </div>
  );
}
