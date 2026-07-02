import { loadManifest } from "@/lib/manifest";
import PageShell from "@/components/layout/PageShell";
import AnalyzeForm from "@/components/admin/AnalyzeForm";

export const dynamic = "force-static";

export const metadata = {
  title: "Analyze a URL · KB Reader",
  robots: { index: false, follow: false },
};

export default async function AnalyzeUrlPage() {
  const manifest = await loadManifest();

  return (
    <PageShell manifest={manifest}>
      <div className="mb-3 text-sm text-[var(--muted)]">Admin</div>
      <h1 className="mb-2 text-3xl font-bold">Analyze a URL</h1>
      <p className="mb-8 text-[var(--muted)]">
        Submit a URL and the kb-blog-curator agent will fetch it, analyze it
        following the standard blog-analysis format, and commit a new page to
        the KB. This runs the same workflow as the scheduled slots — it just
        skips discovery and ranking and analyzes exactly this one URL.
      </p>

      <AnalyzeForm />

      <hr className="my-10 border-[var(--border)]" />

      <div className="text-sm text-[var(--muted)]">
        <h2 className="mb-2 text-base font-semibold text-[var(--foreground)]">
          How it works
        </h2>
        <ol className="ml-4 list-decimal space-y-1">
          <li>
            This form triggers the <code>kb-blog-curator</code> GitHub Actions
            workflow with your URL as an input.
          </li>
          <li>
            The agent fetches the page, writes a full analysis (TLDR, talking
            points, steelman, etc.) using the same prompt as scheduled runs.
          </li>
          <li>
            It commits the markdown to{" "}
            <code>YYYY/MM/DD/blog-&lt;slug&gt;.md</code> (today&rsquo;s UTC
            date) with <code>slot: manual</code> in the frontmatter.
          </li>
          <li>
            The GitHub → reader webhook revalidates the manifest and the new
            page shows up in the sidebar.
          </li>
        </ol>
      </div>
    </PageShell>
  );
}
