import { useEffect, useRef, useState } from "react";
import Markdown from "./Markdown.jsx";

const SAMPLES = [
  "What is the best route from Kollam to Thiruvananthapuram right now?",
  "Why is the Kochi to Thiruvananthapuram corridor blocked?",
  "Which locations near Thiruvananthapuram have active incidents?",
  "What is the impact of the Kollam incident on downstream deliveries?",
];

export default function Chat({ messages, onSend, running }) {
  const [draft, setDraft] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, running]);

  const submit = () => {
    const text = draft.trim();
    if (!text || running) return;
    setDraft("");
    onSend(text);
  };

  const onKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <section className="panel">
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            Ask about routes, incidents, delays or the impact of a disruption.
            Every answer is checked against the road network before it reaches
            you.
          </div>
        )}

        {messages.map((message, index) => (
          <div
            key={index}
            className={`msg ${message.role} ${message.blocked ? "blocked" : ""}`}
          >
            {message.role === "assistant" ? (
              <Markdown>{message.content}</Markdown>
            ) : (
              message.content
            )}

            {message.role === "assistant" && message.meta && (
              <div className="msg-foot">
                {message.meta.confidence !== undefined && (
                  <span>
                    confidence {(message.meta.confidence * 100).toFixed(0)}%
                  </span>
                )}
                {message.meta.sources !== undefined && (
                  <span>· {message.meta.sources} sources</span>
                )}
                {message.meta.latency && (
                  <span>· {Math.round(message.meta.latency)} ms</span>
                )}
                {message.meta.langsmith && (
                  <a
                    href={message.meta.langsmith}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: "var(--accent)" }}
                  >
                    · LangSmith trace
                  </a>
                )}
              </div>
            )}
          </div>
        ))}

        {running && (
          <div className="msg assistant">
            <span className="spin" /> agents working…
          </div>
        )}
        <div ref={endRef} />
      </div>

      {messages.length === 0 && (
        <div className="samples">
          {SAMPLES.map((sample) => (
            <button key={sample} onClick={() => onSend(sample)} disabled={running}>
              {sample}
            </button>
          ))}
        </div>
      )}

      <div className="composer">
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask LogiPilot…  (Enter to send, Shift+Enter for a new line)"
          rows={1}
        />
        <button className="send" onClick={submit} disabled={running || !draft.trim()}>
          Send
        </button>
      </div>
    </section>
  );
}
