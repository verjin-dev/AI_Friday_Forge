const BASE = "/api";

async function getJSON(path) {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

export const getHealth = () => getJSON("/health");
export const getRoles = () => getJSON("/roles");
export const getGraphOverview = () => getJSON("/graph/overview?limit=150");
export const getGraphSchema = () => getJSON("/graph/schema");
export const getMetrics = () => getJSON("/observability/metrics");
export const getConstraintCatalogue = () => getJSON("/constraints/catalogue");
export const getTools = (role) =>
  getJSON(`/tools${role ? `?role=${encodeURIComponent(role)}` : ""}`);

/**
 * Stream the agent timeline over SSE.
 *
 * EventSource only does GET, and the workflow needs a POST body, so this reads
 * the response stream manually and re-assembles `data:` frames.
 */
export async function streamChat(
  { message, sessionId, role, history },
  onEvent,
  signal
) {
  const response = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      role,
      history,
      stream: true,
    }),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Stream failed: ${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);

      const payload = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("");

      if (payload) {
        try {
          onEvent(JSON.parse(payload));
        } catch {
          // A partial frame is not fatal — the next read completes it.
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}
