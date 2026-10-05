import type { Metadata } from "next";
import { AuthProvider } from "@/lib/auth-context";
import "./globals.css";

export const metadata: Metadata = {
  title: "DeployIQ | Governed Security & Compliance Assistant",
  description:
    "Ask DeployIQ about your security posture, Essential Eight and Zero Trust delivery, and governance — grounded in your own knowledge base, with every answer traceable.",
  icons: {
    icon: "/exigotech-mark.png",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
