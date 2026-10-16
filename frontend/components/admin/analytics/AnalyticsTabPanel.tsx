"use client";

import type React from "react";
import { m } from "motion/react";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import {
  DropoffCard,
  FlowCard,
  HourCard,
  PeopleCard,
  QualityCard,
  TopicsCard,
  TrendCard,
  type CardProps,
} from "./AnalyticsCards";
import { ConversationsTable } from "./ConversationsTable";
import { GuideTab } from "./GuideTab";
import type { AnalyticsTabId } from "./url-state";

const GRID = "grid grid-cols-1 gap-4 lg:grid-cols-2";

/**
 * One tab's content. Overview mirrors the reference HTML's full page in its
 * order: trend; hour + flow; topics + quality; drop-off + people; the
 * conversations table. The other tabs focus one section.
 */
export function AnalyticsTabPanel({ tab, ...card }: CardProps & { tab: AnalyticsTabId }) {
  const variants = useMotionVariants(fadeUp);
  let content: React.ReactNode;
  switch (tab) {
    case "guide":
      content = <GuideTab onShow={(t) => card.update({ tab: t })} />;
      break;
    case "trend":
      content = (
        <>
          <TrendCard {...card} />
          <div className={GRID}>
            <HourCard {...card} />
            <QualityCard {...card} />
          </div>
        </>
      );
      break;
    case "flow":
      content = (
        <div className={GRID}>
          <FlowCard {...card} />
          <QualityCard {...card} />
        </div>
      );
      break;
    case "topics":
      content = (
        <div className={GRID}>
          <TopicsCard {...card} />
          <QualityCard {...card} />
        </div>
      );
      break;
    case "dropoff":
      content = (
        <div className={GRID}>
          <DropoffCard {...card} />
          <PeopleCard {...card} />
        </div>
      );
      break;
    case "conversations":
      content = <ConversationsTable state={card.state} filters={card.filters} update={card.update} />;
      break;
    default:
      content = (
        <>
          <TrendCard {...card} />
          <div className={GRID}>
            <HourCard {...card} />
            <FlowCard {...card} />
          </div>
          <div className={GRID}>
            <TopicsCard {...card} />
            <QualityCard {...card} />
          </div>
          <div className={GRID}>
            <DropoffCard {...card} />
            <PeopleCard {...card} />
          </div>
          <ConversationsTable state={card.state} filters={card.filters} update={card.update} />
        </>
      );
  }
  return (
    <m.div key={tab} variants={variants} initial="hidden" animate="visible" className="flex flex-col gap-4">
      {content}
    </m.div>
  );
}
