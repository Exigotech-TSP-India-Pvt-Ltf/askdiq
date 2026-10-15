import Link from "next/link";
import { BrandedStatusPage } from "@/components/shell/BrandedStatusPage";
import { primaryButton, secondaryButton } from "@/components/ui/styles";

export default function NotFound() {
  return (
    <BrandedStatusPage
      code="Error 404"
      title="Page not found"
      message="The page you are looking for does not exist or has moved."
    >
      <Link href="/admin" className={primaryButton}>
        Go to the dashboard
      </Link>
      <Link href="/chat" className={secondaryButton}>
        Open chat
      </Link>
    </BrandedStatusPage>
  );
}
