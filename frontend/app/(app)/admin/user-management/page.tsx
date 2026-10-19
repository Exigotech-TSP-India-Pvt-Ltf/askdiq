"use client";

import { Suspense } from "react";
import { PageShellSkeleton } from "@/components/ui/PageShellSkeleton";
import { UserManagementPage } from "@/components/admin/user-management/UserManagementPage";

export default function UserManagementRoute() {
  return (
    <Suspense fallback={<PageShellSkeleton label="Loading User Management" />}>
      <UserManagementPage />
    </Suspense>
  );
}
