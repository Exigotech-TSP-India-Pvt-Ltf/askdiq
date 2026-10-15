"use client";

import React from "react";
import { FeedbackWorkspace } from "@/components/admin/feedback/FeedbackWorkspace";
import { SkeletonText } from "@/components/ui/Skeleton";

export default function FeedbackPage() {
  return (
    <React.Suspense
      fallback={
        <div className="p-8">
          <SkeletonText lines={3} label="Loading feedback" />
        </div>
      }
    >
      <FeedbackWorkspace />
    </React.Suspense>
  );
}
