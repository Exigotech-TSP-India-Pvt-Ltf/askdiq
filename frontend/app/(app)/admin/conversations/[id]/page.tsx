"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";

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

  const [conversation, setConversation] =
    useState<Conversation | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadConversation() {
      try {
        const id = params.id as string;

        const response = await fetch(
          `http://localhost:8000/admin/conversations/${id}`,
          {
            headers: {
              "X-API-Key": "dep-iq-key",
            },
          }
        );

        if (!response.ok) {
          const errorText = await response.text();

          throw new Error(
            `Failed to load conversation (${response.status})${
              errorText ? `: ${errorText}` : ""
            }`
          );
        }

        const data = await response.json();

        console.log("Conversation detail response:", data);

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

    if (params.id) {
      loadConversation();
    }
  }, [params.id]);

  if (loading) {
    return (
      <div className="p-6">
        <div className="text-sm text-gray-500">
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
          className="mb-6 text-sm text-purple-600 hover:text-purple-800"
        >
          ← Back to conversations
        </button>

        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600">
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
          className="mb-4 text-sm text-purple-600 hover:text-purple-800"
        >
          ← Back to conversations
        </button>

        <div>
          <h1 className="text-2xl font-semibold text-gray-900">
            {conversation.title || "Untitled Conversation"}
          </h1>

          <p className="mt-1 text-sm text-gray-500">
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
      <div className="rounded-xl border border-gray-200 bg-white">
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-base font-semibold text-gray-900">
            Conversation
          </h2>

          <p className="mt-1 text-sm text-gray-500">
            {messages.length} messages
          </p>
        </div>

        <div className="space-y-5 p-6">
          {messages.length === 0 ? (
            <div className="py-10 text-center text-sm text-gray-500">
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
                      ? "max-w-[75%] rounded-2xl rounded-br-md bg-purple-600 px-5 py-4 text-white"
                      : "max-w-[75%] rounded-2xl rounded-bl-md border border-gray-200 bg-gray-50 px-5 py-4 text-gray-900"
                  }
                >
                  {/* Message Header */}
                  <div className="mb-2 flex items-center justify-between gap-6">
                    <span
                      className={
                        message.role === "user"
                          ? "text-xs font-semibold uppercase text-purple-100"
                          : "text-xs font-semibold uppercase text-gray-500"
                      }
                    >
                      {message.role}
                    </span>

                    <span
                      className={
                        message.role === "user"
                          ? "text-xs text-purple-100"
                          : "text-xs text-gray-400"
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
                          ? "mt-3 border-t border-purple-400/40 pt-2 text-xs text-purple-100"
                          : "mt-3 border-t border-gray-200 pt-2 text-xs text-gray-400"
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
    <div className="rounded-xl border border-gray-200 bg-white px-5 py-4">
      <div className="text-xs font-medium uppercase tracking-wide text-gray-500">
        {label}
      </div>

      <div className="mt-2 break-all text-sm font-medium text-gray-900">
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