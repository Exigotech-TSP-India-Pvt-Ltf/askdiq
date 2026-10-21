import type { PageTour } from "../types";

const CONTENT = '[data-tour="analytics-content"]';
const HOUR_FLOW = '[data-tour="analytics-row-hour-flow"]';
const TOPICS_QUALITY = '[data-tour="analytics-row-topics-quality"]';
const DROPOFF_PEOPLE = '[data-tour="analytics-row-dropoff-people"]';

export const analyticsTour: PageTour = {
  id: "analytics",
  title: "Analytics",
  steps: [
    {
      title: "Welcome to Analytics",
      body: "Analytics looks at chat traffic one conversation and one question at a time: what people ask, how each question was answered and where conversations drop. Analysts use it to decide what content to add; support uses it to read failing conversations.",
      placement: "center",
    },
    {
      tab: "overview",
      target: '[data-tour="analytics-tabs"]',
      title: "Sections",
      body: "Overview shows everything on one page; Trend, Flow, Topics, Drop-off and Conversations each focus one area. Guide has definitions of every number.",
      placement: "bottom",
    },
    {
      target: '[data-tour="analytics-filters"]',
      title: "Filters",
      body: "Search questions, issues and people, or open Filters to pick a user type, outcome or rating. Each active filter shows as a chip on the tab row: click a chip to change it, or its × to remove it. Clicking a chart adds a chip too; Clear all resets them (not the date range, which is the filter at the end of the row).",
      placement: "bottom",
    },
    {
      target: '[data-tour="analytics-pending"]',
      title: "Pending classification",
      body: "Every number comes from turn labels written by a background classifier. While turns are still waiting, this note shows the backlog and the charts include labelled turns only, refreshing as more are labelled.",
      placement: "bottom",
    },
    {
      target: '[data-tour="analytics-kpis"]',
      title: "Summary KPIs",
      body: "Conversations in scope; substantive questions (excluding greetings and closings); handled %, the share of those questions answered well; served %, the share of conversations where every question was handled; and probable people, a pseudonymous count with no PII.",
      placement: "bottom",
    },
    {
      target: `${CONTENT} > :first-child`,
      title: "Daily volume",
      body: "Substantive questions and conversations per day, in your time zone. Click a day to focus every chart on it; click again to clear.",
      placement: "bottom",
    },
    {
      target: `${HOUR_FLOW} > :nth-child(1)`,
      title: "Questions by hour",
      body: "A 24-hour clock of when people ask questions, in your time zone. Use it to plan support cover and content releases.",
      placement: "right",
    },
    {
      target: `${HOUR_FLOW} > :nth-child(2)`,
      title: "User type → outcome",
      body: "Flows from the kind of user to Served (every question handled), Partly served or Not served (none handled). Click a band to filter by that pair.",
      placement: "left",
    },
    {
      target: `${TOPICS_QUALITY} > :nth-child(1)`,
      title: "Question topics",
      body: "Tile size is how often a topic is asked; colour runs from red (0% handled) to teal (100% handled). Large red tiles are your biggest content gaps; click one to filter.",
      placement: "right",
    },
    {
      target: `${TOPICS_QUALITY} > :nth-child(2)`,
      title: "Answer quality",
      body: "How questions were answered: correctly, correct refusal, out of scope, partly, no-info reply, clarifying, off-target or false action claim. Click a slice to filter.",
      placement: "left",
    },
    {
      target: `${DROPOFF_PEOPLE} > :nth-child(1)`,
      title: "Where conversations dropped",
      body: "For Partly and Not served conversations, the issue behind the last unhandled question. Click an issue, then View conversations to read them.",
      placement: "right",
    },
    {
      target: `${DROPOFF_PEOPLE} > :nth-child(2)`,
      title: "People",
      body: "One bubble per probable person, sized by conversations and coloured by their usual outcome. Click one to see only their conversations.",
      placement: "left",
    },
    {
      target: '[data-tour="analytics-conversations"]',
      title: "Conversations table",
      body: "Every conversation matching your filters with its user type, outcome, rating, issue and question count. Sort by any column and expand a row to see each question's labels.",
      placement: "top",
    },
    {
      title: "That's Analytics",
      body: "Filter down, find the red topics and drop-off issues, then read those conversations to decide what to fix. For health and latency see Governance; for tokens and cost see Usage; taxonomy labels live in Settings.",
      placement: "center",
    },
  ],
};
