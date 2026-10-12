"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { MessagesSquare, Users, Clock } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";

type Conversation = {
  id: string;
  title?: string;
  created_at?: string;
  last_activity_at?: string;
  message_count?: number;
  created_ip?: string | null;
};

function HeroStat({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: string | number;
  icon: React.ElementType;
  accent: string;
}) {
  return (
    <div className="group relative overflow-hidden rounded-xl border border-purple-100 bg-white p-5 shadow-sm transition-shadow hover:shadow-md">
      <div
        className={`absolute -right-4 -top-4 h-20 w-20 rounded-full opacity-10 transition-transform group-hover:scale-110 ${accent}`}
      />
      <div className="flex items-start justify-between">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        <div className={`rounded-lg p-2 ${accent} bg-opacity-10`}>
          <Icon className={`h-4 w-4 ${accent.replace("bg-", "text-")}`} />
        </div>
      </div>
      <p className="mt-3 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

export default function ConversationsPage() {
  const router = useRouter();

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [ipFilter, setIpFilter] = useState("all");
  const [page, setPage] = useState(1);
  const pageSize = 10;

  useEffect(() => {
    async function loadConversations() {
      try {
        const response = await fetch(
          "http://localhost:8000/admin/conversations",
          {
            headers: {
              "X-API-Key": "dep-iq-key",
            },
          }
        );

        if (!response.ok) {
          setConversations([]);
          return;
        }

        const data = await response.json();

        setConversations(
          Array.isArray(data)
            ? data
            : data.conversations || data.items || []
        );
      } catch {
        setConversations([]);
      } finally {
        setLoading(false);
      }
    }

    loadConversations();
  }, []);

  const totalMessages = useMemo(
    () => conversations.reduce((sum, c) => sum + (c.message_count ?? 0), 0),
    [conversations]
  );
  const uniqueIps = useMemo(
    () => new Set(conversations.map((c) => c.created_ip).filter(Boolean)).size,
    [conversations]
  );

  const ipOptions = useMemo(
    () => [
      { label: "All", value: "all" },
      ...Array.from(new Set(conversations.map((c) => c.created_ip).filter(Boolean) as string[])).map(
        (ip) => ({ label: ip, value: ip })
      ),
    ],
    [conversations]
  );

  const filtered = useMemo(
    () =>
      conversations.filter((c) => {
        if (ipFilter !== "all" && c.created_ip !== ipFilter) return false;
        if (!search.trim()) return true;
        const haystack = `${c.title ?? ""} ${c.created_ip ?? ""} ${c.id}`.toLowerCase();
        return haystack.includes(search.trim().toLowerCase());
      }),
    [conversations, search, ipFilter]
  );

  useEffect(() => setPage(1), [search, ipFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  return (
    <div>
      <header className="flex h-16 shrink-0 items-center border-b border-slate-200 bg-white px-8">
        <div>
          <h1 className="text-sm font-semibold text-slate-900">Conversation Logs</h1>
          <p className="text-xs text-slate-500">View conversations across all users.</p>
        </div>
      </header>

      <div className="p-8">
      <div className="mb-6 grid grid-cols-1 gap-5 sm:grid-cols-3">
        <HeroStat label="Conversations" value={conversations.length} icon={MessagesSquare} accent="bg-purple-500" />
        <HeroStat label="Unique IPs" value={uniqueIps} icon={Users} accent="bg-indigo-500" />
        <HeroStat label="Total Messages" value={totalMessages} icon={Clock} accent="bg-fuchsia-500" />
      </div>

      <div className="overflow-hidden rounded-xl border border-purple-100 bg-white shadow-sm">
        <div className="p-4 pb-0">
          <TableToolbar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search by title, IP or ID…"
            filterOptions={ipOptions}
            filterValue={ipFilter}
            onFilterChange={setIpFilter}
            countLabel={`${filtered.length} conversations`}
          />
        </div>

        <div className="max-h-[60vh] overflow-y-auto overflow-x-auto">
          <table className="w-full text-left">
            <thead className="sticky top-0 z-10">
              <tr>
                {["Conversation", "IP Address", "Created", "Last Activity", "Messages"].map(
                  (item) => (
                    <th
                      key={item}
                      className="border-b border-purple-100 bg-purple-50 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500"
                    >
                      {item}
                    </th>
                  )
                )}
              </tr>
            </thead>

            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={5} className="px-5 py-10 text-center text-sm text-slate-500">
                    Loading conversations...
                  </td>
                </tr>
              ) : paged.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-5 py-10 text-center text-sm text-slate-500">
                    No conversations found.
                  </td>
                </tr>
              ) : (
                paged.map((conversation) => (
                  <tr
                    key={conversation.id}
                    onClick={() => router.push(`/admin/conversations/${conversation.id}`)}
                    className="cursor-pointer border-b border-purple-50 odd:bg-white even:bg-purple-50/20 transition-colors hover:bg-purple-50/60"
                  >
                    <td className="px-5 py-4">
                      <p className="font-medium text-slate-900 hover:text-purple-600">
                        {conversation.title || "Untitled Conversation"}
                      </p>
                      <p className="mt-1 font-mono text-[11px] text-slate-400" title={conversation.id}>
                        {conversation.id.length > 20 ? `${conversation.id.slice(0, 20)}…` : conversation.id}
                      </p>
                    </td>

                    <td className="px-5 py-4 font-mono text-xs text-slate-500">
                      {conversation.created_ip || "—"}
                    </td>

                    <td className="px-5 py-4 text-sm text-slate-600">
                      {formatDate(conversation.created_at)}
                    </td>

                    <td className="px-5 py-4 text-sm text-slate-600">
                      {formatDate(conversation.last_activity_at)}
                    </td>

                    <td className="px-5 py-4">
                      <span className="inline-flex items-center rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
                        {conversation.message_count ?? 0}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <Pagination
          page={page}
          pageCount={pageCount}
          onPageChange={setPage}
          totalItems={filtered.length}
          pageSize={pageSize}
        />
      </div>
      </div>
    </div>
  );
}

function formatDate(value?: string) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}
