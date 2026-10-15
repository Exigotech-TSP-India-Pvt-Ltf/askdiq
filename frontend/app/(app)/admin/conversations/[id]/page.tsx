"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

type Message = {
  id: string;
  turn_index: number;
  role: string;
  content: string;
  created_at?: string | null;
  request_id?: string | null;
  grounded?: boolean | null;
  guardrail_flags?: string[] | null;
  ip_address?: string | null;
};

type Conversation = {
  id: string;
  title?: string | null;
  created_at?: string | null;
  last_activity_at?: string | null;
  expires_at?: string | null;
  created_ip?: string | null;
  messages: Message[];
};

export default function ConversationDetailPage() {
  const params = useParams();
  const router = useRouter();

  const { token } = useAuth();
  const [conversation, setConversation] =
    useState<Conversation | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!params.id || !token) return;
    const authToken = token;

    async function loadConversation() {
      try {
        const id = params.id as string;
        const data = await api.adminGet<any>(authToken, `/conversations/${id}`);

        // Backend returns:
        // {
        //   conversation: {...},
        //   messages: [...]
        // }
        setConversation({
          ...data.conversation,
          messages: data.messages || [],
        });
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load conversation"
        );
      } finally {
        setLoading(false);
      }
    }

    loadConversation();
  }, [params.id, token]);

  if (loading) {
    return (
      <div className="p-6">
        <div className="text-sm text-fg-subtle">
          Loading conversation...
        </div>
      </div>
    );
  }

  if (error || !conversation) {
    return (
      <div className="p-6">
        <button
          onClick={() => router.back()}
          className="mb-6 text-sm text-accent-fg hover:text-brand-800 dark:hover:text-brand-200"
        >
          ← Back to conversations
        </button>

        <div className="rounded-xl border border-status-danger/30 bg-status-danger-bg p-4 text-sm text-status-danger-fg">
          {error || "Conversation not found"}
        </div>
      </div>
    );
  }

  const messages = conversation.messages || [];

  return (
    <div className="p-6">
      {/* Header */}
      <div className="mb-6">
        <button
          onClick={() => router.back()}
          className="mb-4 text-sm text-accent-fg hover:text-brand-800 dark:hover:text-brand-200"
        >
          ← Back to conversations
        </button>

        <div>
          <h1 className="text-2xl font-semibold text-fg">
            {conversation.title || "Untitled Conversation"}
          </h1>

          <p className="mt-1 text-sm text-fg-subtle">
            Conversation ID: {conversation.id}
          </p>
        </div>
      </div>

      {/* Conversation Information */}
      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-4">
        <InfoCard
          label="IP Address"
          value={conversation.created_ip || "Not available"}
        />

        <InfoCard
          label="Created"
          value={formatDate(conversation.created_at)}
        />

        <InfoCard
          label="Last Activity"
          value={formatDate(conversation.last_activity_at)}
        />

        <InfoCard
          label="Messages"
          value={String(conversation.messages?.length ?? 0)}
        />
      </div>

      {/* Conversation Messages */}
      <div className="rounded-xl border border-line bg-surface-card">
        <div className="border-b border-line px-6 py-4">
          <h2 className="text-base font-semibold text-fg">
            Conversation
          </h2>

          <p className="mt-1 text-sm text-fg-subtle">
            {messages.length} messages
          </p>
        </div>

        <div className="space-y-5 p-6">
          {messages.length === 0 ? (
            <div className="py-10 text-center text-sm text-fg-subtle">
              No messages found.
            </div>
          ) : (
            messages.map((message) => (
              <div
                key={message.id}
                className={
                  message.role === "user"
                    ? "flex justify-end"
                    : "flex justify-start"
                }
              >
                <div
                  className={
                    message.role === "user"
                      ? "max-w-[75%] rounded-2xl rounded-br-md bg-brand-600 px-5 py-4 text-white"
                      : "max-w-[75%] rounded-2xl rounded-bl-md border border-line bg-surface-muted px-5 py-4 text-fg"
                  }
                >
                  {/* Message Header */}
                  <div className="mb-2 flex items-center justify-between gap-6">
                    <span
                      className={
                        message.role === "user"
                          ? "text-xs font-semibold uppercase text-brand-50"
                          : "text-xs font-semibold uppercase text-fg-subtle"
                      }
                    >
                      {message.role}
                    </span>

                    <span
                      className={
                        message.role === "user"
                          ? "text-xs text-brand-50"
                          : "text-xs text-fg-subtle"
                      }
                    >
                      {formatDate(message.created_at)}
                    </span>
                  </div>

                  {/* Message Content */}
                  <div className="whitespace-pre-wrap text-sm leading-6">
                    {message.content}
                  </div>

                  {/* Message Metadata */}
                  {(message.ip_address ||
                    message.grounded !== null &&
                      message.grounded !== undefined) && (
                    <div
                      className={
                        message.role === "user"
                          ? "mt-3 border-t border-white/30 pt-2 text-xs text-brand-50"
                          : "mt-3 border-t border-line pt-2 text-xs text-fg-subtle"
                      }
                    >
                      {message.ip_address && (
                        <span>
                          IP: {message.ip_address}
                        </span>
                      )}

                      {message.grounded !== null &&
                        message.grounded !== undefined && (
                          <span className="ml-4">
                            Grounded:{" "}
                            {message.grounded ? "Yes" : "No"}
                          </span>
                        )}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function InfoCard({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface-card px-5 py-4">
      <div className="text-xs font-medium uppercase tracking-wide text-fg-subtle">
        {label}
      </div>

      <div className="mt-2 break-all text-sm font-medium text-fg">
        {value}
      </div>
    </div>
  );
}

function formatDate(value?: string | null) {
  if (!value) {
    return "—";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "—";
  }

  return date.toLocaleString();
}