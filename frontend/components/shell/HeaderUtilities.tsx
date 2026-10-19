"use client";

import React from "react";
import { NotificationBell, type NotificationItem } from "@/components/shell/NotificationBell";
import { ProfileMenu } from "@/components/shell/ProfileMenu";
import { RoleBadge } from "@/components/shell/RoleBadge";
import { TourLauncher, type TourLauncherVariant } from "@/components/tour/TourLauncher";
import { useAuth } from "@/lib/auth-context";
import { useShellNotifications } from "@/lib/use-shell-notifications";

interface HeaderUtilitiesProps {
  notifications?: NotificationItem[];
  roleBadge?: React.ReactNode;
  /** Which tour launcher slot this header is (the mobile bar does not hide the floating one). */
  launcherVariant?: Exclude<TourLauncherVariant, "floating">;
}

/**
 * Tour launcher, notification bell and profile menu shared by every header
 * (the go-to-page palette trigger lives at the top of the sidebar).
 * Defaults: the signed-in user's role badge and the shell notification feed.
 */
export function HeaderUtilities({ notifications, roleBadge, launcherVariant = "header" }: HeaderUtilitiesProps) {
  const { role } = useAuth();
  const feed = useShellNotifications();
  return (
    <div className="flex items-center gap-2">
      <TourLauncher variant={launcherVariant} />
      <NotificationBell items={notifications ?? feed} />
      <ProfileMenu roleBadge={roleBadge ?? (role ? <RoleBadge role={role} /> : undefined)} />
    </div>
  );
}
