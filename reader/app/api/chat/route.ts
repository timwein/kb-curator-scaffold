import { NextRequest, NextResponse } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import { verifyAccess } from "@/lib/auth/verifyAccess";
import { allow } from "@/lib/rate-limit";
import { loadPageByRelPath } from "@/lib/kb/scan";

export const runtime = "nodejs";

const ALLOWED_MODELS = new Set([
  "claude-opus-4-6",
  "claude-sonnet-4-6",
]);

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

interface ChatBody {
  pagePath: string;
  model?: string;
  messages: ChatMessage[];
}

function validPagePath(path: string): boolean {
  if (path.includes("..") || path.startsWith("/")) return false;
  if (!path.endsWith(".md")) return false;
  return (
    /^\d{4}\/\d{2}\/\d{2}\//.test(path) ||
    path.startsWith("topics/") ||
    path.startsWith("syntheses/")
  );
}

function buildSystemPrompt(title: string, body: string): string {
  return `You are helping the user think through an analysis page in their personal knowledge base. The full page content appears below. Use it as the primary source for your answers, but you may draw on general knowledge to expand, connect, or pressure-test the argument. Be direct and concise; prefer substance over hedging.

Page title: ${title}

---
${body}
---`;
}

function sseFormat(payload: object): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

export async function POST(req: NextRequest) {
  const identity = await verifyAccess(req);
  if (!identity) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const gate = allow(`chat:${identity.email}`);
  if (!gate.ok) {
    return NextResponse.json(
      { error: "rate limited", retryAfter: gate.retryAfter },
      { status: 429 },
    );
  }

  let body: ChatBody;
  try {
    body = (await req.json()) as ChatBody;
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  const { pagePath, messages } = body;
  const model = body.model || "claude-opus-4-6";

  if (!ALLOWED_MODELS.has(model)) {
    return NextResponse.json({ error: "model not allowed" }, { status: 400 });
  }
  if (!validPagePath(pagePath)) {
    return NextResponse.json({ error: "invalid pagePath" }, { status: 400 });
  }
  if (!Array.isArray(messages) || messages.length === 0) {
    return NextResponse.json({ error: "messages required" }, { status: 400 });
  }
  for (const m of messages) {
    if (
      (m.role !== "user" && m.role !== "assistant") ||
      typeof m.content !== "string"
    ) {
      return NextResponse.json({ error: "bad message" }, { status: 400 });
    }
  }

  const page = await loadPageByRelPath(pagePath);
  if (!page) {
    return NextResponse.json({ error: "page not found" }, { status: 404 });
  }
  if (!page.canChat) {
    return NextResponse.json({ error: "page not chat-enabled" }, { status: 400 });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "server misconfigured (ANTHROPIC_API_KEY)" },
      { status: 500 },
    );
  }
  const anthropic = new Anthropic({ apiKey });

  const system = buildSystemPrompt(page.title, page.body);

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        const streamResp = anthropic.messages.stream({
          model,
          max_tokens: 4096,
          system,
          messages,
        });
        for await (const event of streamResp) {
          if (event.type === "content_block_delta") {
            const delta = event.delta;
            if (delta.type === "text_delta") {
              controller.enqueue(
                encoder.encode(sseFormat({ type: "delta", text: delta.text })),
              );
            }
          }
        }
        controller.enqueue(encoder.encode("data: [DONE]\n\n"));
      } catch (err) {
        console.error("[chat] upstream error:", err);
        controller.enqueue(
          encoder.encode(
            sseFormat({ type: "error", message: "upstream failure" }),
          ),
        );
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
