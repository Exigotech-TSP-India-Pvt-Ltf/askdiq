"use client";

import { Suspense } from "react";
import { MascotLoader } from "@/components/ui/MascotLoader";
import { UserManagementPage } from "@/components/admin/user-management/UserManagementPage";

export default function UserManagementRoute() {
  return (
    <Suspense fallback={<MascotLoader variant="page" label="Loading User Management" />}>
      <UserManagementPage />
    </Suspense>
  );
}
