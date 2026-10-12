"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

type Message = {
  id: string;
  role: string;
  content: string;
  created_at?: string;
  grounded?: boolean | null;
  ip_address?: string | null;
};

type Conversation = {
  id: string;
  title?: string;
  created_at?: string;
  last_activity_at?: string;
  message_count?: number;
};

type UserDetail = {
  id: string;
  ip_address?: string | null;
  first_seen?: string | null;
  last_active?: string | null;
  status?: string;
  sessions?: number;
  conversations?: number;
  queries?: number;
  feedback_count?: number;
  conversations_data?: Conversation[];
};

export default function UserDetailPage() {
  const params = useParams();
  const id = params.id as string;

  const [user, setUser] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;

    async function loadUser() {
      try {
        const response = await fetch(
          `http://localhost:8000/admin/users/${id}`,
          {
            headers: {
              "X-API-Key": "dep-iq-key",
            },
          }
        );

        if (!response.ok) {
          setUser(null);
          return;
        }

        const data = await response.json();
        setUser(data);
      } catch {
        setUser(null);
      } finally {
        setLoading(false);
      }
    }

    loadUser();
  }, [id]);

  if (loading) {
    return (
      <div className="p-8">
        <p className="text-sm text-slate-500">
          Loading user...
        </p>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="p-8">
        <Link
          href="/admin/users"
          className="text-sm font-medium text-purple-700 hover:text-purple-900"
        >
          ← Back to Users
        </Link>

        <div className="mt-8 rounded-xl border border-slate-200 bg-white p-10 text-center">
          <h1 className="text-lg font-semibold text-slate-900">
            User not found
          </h1>

          <p className="mt-2 text-sm text-slate-500">
            The requested user could not be found.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="mb-6">
        <Link
          href="/admin/users"
          className="text-sm font-medium text-purple-700 hover:text-purple-900"
        >
          ← Back to Users
        </Link>
      </div>

      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-slate-900">
          {user.ip_address || `User ${user.id.slice(0, 8)}`}
        </h1>

        <p className="mt-1 text-sm text-slate-500">
          User details and activity
        </p>
      </div>

      {/* User information */}
      <div className="mb-6 grid grid-cols-1 gap-5 md:grid-cols-3">
        <InfoCard
          label="IP Address"
          value={user.ip_address || "—"}
        />

        <InfoCard
          label="Status"
          value={user.status || "Active"}
        />

        <InfoCard
          label="First Seen"
          value={formatDate(user.first_seen)}
        />
      </div>

      {/* Activity statistics */}
      <div className="mb-8 grid grid-cols-2 gap-5 lg:grid-cols-4">
        <StatCard
          label="Sessions"
          value={user.sessions ?? 0}
        />

        <StatCard
          label="Conversations"
          value={user.conversations ?? 0}
        />

        <StatCard
          label="Queries"
          value={user.queries ?? 0}
        />

        <StatCard
          label="Feedback"
          value={user.feedback_count ?? 0}
        />
      </div>

      {/* Last activity */}
      <div className="mb-6 rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="text-base font-semibold text-slate-900">
          Activity
        </h2>

        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Last Active
          </p>

          <p className="mt-1 text-sm text-slate-700">
            {formatDate(user.last_active)}
          </p>
        </div>
      </div>

      {/* Conversations */}
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-6 py-4">
          <h2 className="text-base font-semibold text-slate-900">
            Conversations
          </h2>

          <p className="mt-1 text-sm text-slate-500">
            Conversations associated with this user.
          </p>
        </div>

        {user.conversations_data &&
        user.conversations_data.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="border-b border-slate-200 bg-slate-50">
                <tr>
                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Conversation
                  </th>

                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Created
                  </th>

                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Last Activity
                  </th>

                  <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Messages
                  </th>
                </tr>
              </thead>

              <tbody>
                {user.conversations_data.map((conversation) => (
                  <tr
                    key={conversation.id}
                    className="border-b border-slate-100 last:border-0"
                  >
                    <td className="px-5 py-4">
                      <Link
                        href={`/admin/conversations/${conversation.id}`}
                        className="font-medium text-purple-700 hover:text-purple-900"
                      >
                        {conversation.title || "Untitled Conversation"}
                      </Link>
                    </td>

                    <td className="px-5 py-4 text-sm text-slate-600">
                      {formatDate(conversation.created_at)}
                    </td>

                    <td className="px-5 py-4 text-sm text-slate-600">
                      {formatDate(conversation.last_activity_at)}
                    </td>

                    <td className="px-5 py-4 text-sm text-slate-600">
                      {conversation.message_count ?? 0}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-8 text-center text-sm text-slate-500">
            No conversations found for this user.
          </div>
        )}
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
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </p>

      <p className="mt-2 break-words text-sm font-medium text-slate-900">
        {value}
      </p>
    </div>
  );
}

function StatCard({
  label,
  value,
}: {
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <p className="text-sm text-slate-500">
        {label}
      </p>

      <p className="mt-2 text-2xl font-semibold text-slate-900">
        {value}
      </p>
    </div>
  );
}

function formatDate(value?: string | null) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "—";
  }

  return date.toLocaleString();
}