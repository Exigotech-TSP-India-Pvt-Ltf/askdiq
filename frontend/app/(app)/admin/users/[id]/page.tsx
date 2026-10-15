"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState, useMemo } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { TableCard } from "@/components/admin/TableCard";
import { formatNumber } from "@/components/admin/TableFormatters";

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
  const router = useRouter();
  const params = useParams();
  const id = params.id as string;

  const { token } = useAuth();
  const [user, setUser] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(true);

  // Conversations table state
  const [convSearch, setConvSearch] = useState("");
  const [convPage, setConvPage] = useState(1);
  const [convPageSize, setConvPageSize] = useState(10);

  const filteredConversations = useMemo(() => {
    if (!user?.conversations_data) return [];
    if (!convSearch.trim()) return user.conversations_data;
    const needle = convSearch.toLowerCase();
    return user.conversations_data.filter(
      (c) =>
        (c.title || "").toLowerCase().includes(needle) ||
        c.id.toLowerCase().includes(needle)
    );
  }, [user?.conversations_data, convSearch]);

  const pagedConversations = useMemo(() => {
    const start = (convPage - 1) * convPageSize;
    return filteredConversations.slice(start, start + convPageSize);
  }, [filteredConversations, convPage, convPageSize]);

  const convColumns: ColumnDef<Conversation>[] = [
    {
      key: "title",
      header: "Conversation Title & ID",
      render: (c) => (
        <div className="flex flex-col">
          <span className="font-medium text-brand-600 dark:text-brand-400 hover:underline">
            {c.title || "Untitled Conversation"}
          </span>
          <span className="font-mono text-[10px] text-slate-400 dark:text-slate-500">{c.id}</span>
        </div>
      ),
    },
    {
      key: "created_at",
      header: "Created",
      render: (c) => (
        <span className="text-xs text-slate-600 dark:text-slate-400">
          {formatDate(c.created_at)}
        </span>
      ),
    },
    {
      key: "last_activity_at",
      header: "Last Activity",
      render: (c) => (
        <span className="text-xs text-slate-600 dark:text-slate-400">
          {formatDate(c.last_activity_at)}
        </span>
      ),
    },
    {
      key: "message_count",
      header: "Messages",
      sortable: true,
      align: "right",
      render: (c) => formatNumber(c.message_count ?? 0),
    },
  ];

  useEffect(() => {
    if (!id || !token) return;
    const authToken = token;

    async function loadUser() {
      try {
        const data = await api.adminGet<UserDetail>(authToken, `/users/${id}`);
        setUser(data);
      } catch {
        setUser(null);
      } finally {
        setLoading(false);
      }
    }

    loadUser();
  }, [id, token]);

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
    <div className="p-8 min-h-screen bg-[#f4fbfb] dark:bg-dark-bg text-ink dark:text-slate-100">
      <div className="mb-6">
        <Link
          href="/admin/users"
          className="text-sm font-medium text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300"
        >
          ← Back to Users
        </Link>
      </div>

      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-white">
          {user.ip_address || `User ${user.id.slice(0, 8)}`}
        </h1>

        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
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
      <div className="mb-6 rounded-xl border border-slate-200 dark:border-dark-border bg-white dark:bg-dark-card p-6 shadow-sm">
        <h2 className="text-base font-semibold text-slate-900 dark:text-white">
          Activity
        </h2>

        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Last Active
          </p>

          <p className="mt-1 text-sm text-slate-700 dark:text-slate-200">
            {formatDate(user.last_active)}
          </p>
        </div>
      </div>

      {/* Conversations */}
      <TableCard
        title="Conversations"
        description="Conversations associated with this client IP address."
        toolbar={
          <TableToolbar
            search={convSearch}
            onSearchChange={(v) => {
              setConvSearch(v);
              setConvPage(1);
            }}
            searchPlaceholder="Search conversations…"
          />
        }
        pagination={
          <Pagination
            page={convPage}
            pageCount={Math.ceil(filteredConversations.length / convPageSize)}
            totalItems={filteredConversations.length}
            pageSize={convPageSize}
            onPageChange={setConvPage}
            onPageSizeChange={setConvPageSize}
          />
        }
      >
        <DataTable
          columns={convColumns}
          data={pagedConversations}
          keyField="id"
          isLoading={false}
          onRowClick={(c) => router.push(`/admin/conversations/${c.id}`)}
          emptyMessage="No conversations found for this user."
        />
      </TableCard>
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
    <div className="rounded-xl border border-slate-200 dark:border-dark-border bg-white dark:bg-dark-card p-5 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </p>

      <p className="mt-2 break-words text-sm font-medium text-slate-900 dark:text-white">
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
    <div className="rounded-xl border border-slate-200 dark:border-dark-border bg-white dark:bg-dark-card p-5 shadow-sm">
      <p className="text-sm text-slate-500 dark:text-slate-400">
        {label}
      </p>

      <p className="mt-2 text-2xl font-semibold text-slate-900 dark:text-white">
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