"""Azure Insights: free Microsoft Foundry / Azure data for the admin console.

Background jobs poll free Azure APIs (Retail Prices, Monitor metrics, Cost
Management, Cognitive Services management, Resource Health), store every
result with its history in Postgres, refresh the console snapshots and
announce them on the live stream. Pages never call Azure. See README.md.
"""
