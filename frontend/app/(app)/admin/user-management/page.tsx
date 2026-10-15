"use client";

import { Suspense } from "react";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { UserManagementPage } from "@/components/admin/user-management/UserManagementPage";

export default function UserManagementRoute() {
  return (
    <Suspense fallback={<SkeletonCard label="Loading User Management" className="m-8" />}>
      <UserManagementPage />
    </Suspense>
  );
}
