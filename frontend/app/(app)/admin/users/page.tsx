"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { UserCheck, Users as UsersIcon, Activity } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";

type User = {
  id: string;
  ip_address?: string | null;
  first_seen?: string | null;
  last_active?: string | null;
  status?: string;
  sessions?: number;
  conversations?: number;
  queries?: number;
  feedback_count?: number;
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

export default function UsersPage() {
  const router = useRouter();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(1);
  const pageSize = 10;

  useEffect(() => {
    async function loadUsers() {
      try {
        const response = await fetch("http://localhost:8000/admin/users", {
          headers: {
            "X-API-Key": "dep-iq-key",
          },
        });

        if (!response.ok) {
          setUsers([]);
          return;
        }

        const data = await response.json();

        setUsers(Array.isArray(data) ? data : data.users || data.items || []);
      } catch {
        setUsers([]);
      } finally {
        setLoading(false);
      }
    }

    loadUsers();
  }, []);

  const activeCount = useMemo(
    () => users.filter((u) => (u.status || "Active").toLowerCase() === "active").length,
    [users]
  );
  const totalQueries = useMemo(
    () => users.reduce((sum, u) => sum + (u.queries ?? 0), 0),
    [users]
  );

  const filtered = useMemo(
    () =>
      users.filter((u) => {
        if (statusFilter !== "all") {
          const isActive = (u.status || "Active").toLowerCase() === "active";
          if (statusFilter === "active" && !isActive) return false;
          if (statusFilter === "inactive" && isActive) return false;
        }
        if (!search.trim()) return true;
        return (u.ip_address ?? "").toLowerCase().includes(search.trim().toLowerCase());
      }),
    [users, search, statusFilter]
  );

  useEffect(() => setPage(1), [search, statusFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  return (
    <div>
      <header className="flex h-16 shrink-0 items-center border-b border-slate-200 bg-white px-8">
        <div>
          <h1 className="text-sm font-semibold text-slate-900">Users</h1>
          <p className="text-xs text-slate-500">
            Monitor user activity, engagement and access history.
          </p>
        </div>
      </header>

      <div className="p-8">
      <div className="mb-6 grid grid-cols-1 gap-5 sm:grid-cols-3">
        <HeroStat label="Total Users" value={users.length} icon={UsersIcon} accent="bg-purple-500" />
        <HeroStat label="Active Users" value={activeCount} icon={UserCheck} accent="bg-emerald-500" />
        <HeroStat label="Total Queries" value={totalQueries} icon={Activity} accent="bg-indigo-500" />
      </div>

      <div className="overflow-hidden rounded-xl border border-purple-100 bg-white shadow-sm">
        <div className="p-4 pb-0">
          <TableToolbar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search by IP address…"
            filterOptions={[
              { label: "All", value: "all" },
              { label: "Active", value: "active" },
              { label: "Inactive", value: "inactive" },
            ]}
            filterValue={statusFilter}
            onFilterChange={setStatusFilter}
            countLabel={`${filtered.length} users`}
          />
        </div>

        <div className="max-h-[60vh] overflow-y-auto overflow-x-auto">
          <table className="w-full text-left">
            <thead className="sticky top-0 z-10">
              <tr>
                {[
                  "IP Address",
                  "First Seen",
                  "Last Active",
                  "Status",
                  "Sessions",
                  "Conversations",
                  "Queries",
                  "Feedback",
                ].map((header) => (
                  <th
                    key={header}
                    className="border-b border-purple-100 bg-purple-50 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500"
                  >
                    {header}
                  </th>
                ))}
              </tr>
            </thead>

            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={8} className="px-5 py-10 text-center text-sm text-slate-500">
                    Loading users...
                  </td>
                </tr>
              ) : paged.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-5 py-10 text-center text-sm text-slate-500">
                    No users found.
                  </td>
                </tr>
              ) : (
                paged.map((user) => {
                  const isActive = (user.status || "Active").toLowerCase() === "active";
                  return (
                    <tr
                      key={user.id}
                      onClick={() => router.push(`/admin/users/${user.id}`)}
                      className="cursor-pointer border-b border-purple-50 odd:bg-white even:bg-purple-50/20 transition-colors hover:bg-purple-50/60"
                    >
                      <td className="px-5 py-4 font-mono text-xs text-slate-500">
                        {user.ip_address || "—"}
                      </td>

                      <td className="px-5 py-4 text-sm text-slate-600">{formatDate(user.first_seen)}</td>

                      <td className="px-5 py-4 text-sm text-slate-600">{formatDate(user.last_active)}</td>

                      <td className="px-5 py-4">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                            isActive ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"
                          }`}
                        >
                          <span
                            className={`h-1.5 w-1.5 rounded-full ${isActive ? "bg-emerald-500" : "bg-slate-400"}`}
                          />
                          {user.status || "Active"}
                        </span>
                      </td>

                      <td className="px-5 py-4 text-sm text-slate-600">{user.sessions ?? 0}</td>
                      <td className="px-5 py-4 text-sm text-slate-600">{user.conversations ?? 0}</td>
                      <td className="px-5 py-4">
                        <span className="inline-flex items-center rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
                          {user.queries ?? 0}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-sm text-slate-600">{user.feedback_count ?? 0}</td>
                    </tr>
                  );
                })
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

function formatDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}
