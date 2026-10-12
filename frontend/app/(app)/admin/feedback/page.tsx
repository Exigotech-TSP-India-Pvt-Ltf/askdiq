"use client";

import { useEffect, useState } from "react";

type Feedback = {
  id: string;
  rating?: number;
  comment?: string;
  ip_address?: string;
  conversation_id?: string;
  message_id?: string;
  created_at?: string;
};

export default function FeedbackPage() {
  const [feedback, setFeedback] = useState<Feedback[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/admin/feedback")
      .then((response) =>
        response.ok ? response.json() : []
      )
      .then((data) => {
        setFeedback(
          Array.isArray(data)
            ? data
            : data.feedback || data.items || []
        );
      })
      .catch(() => setFeedback([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <header className="flex h-16 shrink-0 items-center border-b border-slate-200 bg-white px-8">
        <div>
          <h1 className="text-sm font-semibold text-slate-900">Feedback</h1>
          <p className="text-xs text-slate-500">
            User ratings and comments for conversations and responses.
          </p>
        </div>
      </header>

      <div className="p-8">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
        <Metric title="Total Feedback" value={feedback.length} />

        <Metric
          title="Positive"
          value={feedback.filter((x) => (x.rating || 0) >= 4).length}
        />

        <Metric
          title="Negative"
          value={feedback.filter((x) => (x.rating || 0) <= 2).length}
        />
      </div>

      <div className="mt-8 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-left">
          <thead className="border-b border-slate-200 bg-slate-50">
            <tr>
              {[
                "IP Address",
                "Rating",
                "Comment",
                "Conversation",
                "Message",
                "Created",
              ].map((item) => (
                <th
                  key={item}
                  className="px-5 py-3 text-xs font-semibold uppercase text-slate-500"
                >
                  {item}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {loading ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-5 py-10 text-center text-sm text-slate-500"
                >
                  Loading feedback...
                </td>
              </tr>
            ) : feedback.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-5 py-10 text-center text-sm text-slate-500"
                >
                  No feedback available.
                </td>
              </tr>
            ) : (
              feedback.map((item) => (
                <tr
                  key={item.id}
                  className="border-b border-slate-100"
                >
                  <td className="px-5 py-4 font-mono text-xs text-slate-500">
                    {item.ip_address || "—"}
                  </td>

                  <td className="px-5 py-4">
                    <span className="font-semibold text-purple-700">
                      {item.rating ?? "—"}/5
                    </span>
                  </td>

                  <td className="max-w-md px-5 py-4 text-sm text-slate-600">
                    {item.comment || "—"}
                  </td>

                  <td className="px-5 py-4 text-xs text-slate-500">
                    {item.conversation_id || "—"}
                  </td>

                  <td className="px-5 py-4 text-xs text-slate-500">
                    {item.message_id || "—"}
                  </td>

                  <td className="px-5 py-4 text-sm text-slate-500">
                    {formatDate(item.created_at)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      </div>
    </div>
  );
}

function Metric({
  title,
  value,
}: {
  title: string;
  value: number;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <p className="text-sm text-slate-500">{title}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-900">
        {value}
      </p>
    </div>
  );
}

function formatDate(value?: string) {
  if (!value) return "—";

  return new Date(value).toLocaleString();
}