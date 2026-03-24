# GCP Technical Support Best Practices

This document outlines the core requirements for writing an effective Google Cloud Platform (GCP) support case to minimize resolution time.

## Required Information for Every Ticket

1. **Time**: Use ISO 8601 format (e.g., `2017-09-08T15:13:06+00:00`). State exact start time, end time, and duration. Avoid relative times like "yesterday".
2. **Product**: Specify the exact product, feature, and mechanism (REST API, gcloud CLI, Cloud Console UI). Include URLs or API endpoints.
3. **Location**: Provide the exact region and zone (e.g., `us-east1-b`).
4. **Identifiers**: Provide exact alphanumeric IDs. 
   - Projects: Project ID (not project name).
   - Compute: Instance IDs.
   - Network: IP addresses (specify context: external, internal, load balancer, VPN endpoint).
   - BigQuery: Job IDs or table names.
5. **Problem Type**:
   - **Intermittent**: Fails randomly (e.g., DNS resolution failure). Identify bottlenecks.
   - **Transient**: Fails momentarily (e.g., network latency spikes). TCP usually handles this.
   - **Consistent**: Fails completely (e.g., website down). Provide exact reproduction steps.
6. **Useful Artifacts**: Attach screenshots, browser trace (HAR), tcpdump, log snippets, or example stack traces.

## Case Priority (Severity Levels)

- **P1 (Critical Impact)**: Service unusable in production. Critical business impact (revenue loss, data integrity). Requires immediate attention. Expect 24x7 "follow-the-sun" engagement and joining a Google Meet bridge within 15-30 mins.
- **P2 (High Impact)**: Service use severely impaired. Infrastructure degraded in production. Moderate business impact.
- **P3 (Medium Impact)**: Service use partially impaired. No user-visible impact, limited scope.
- **P4 (Low Impact)**: Service fully usable. Used for consultative tickets.

## Routing and Escalation

- **Routing**: Explicitly state if you need the ticket routed to a specific time zone (e.g., "Please route this case to the Pacific time zone (GMT-8)").
- **Escalation**: Escalate only when business impact increases or the resolution process breaks down (no updates/stuck). Do not escalate immediately after a priority change.
- **Follow-the-Sun**: P1 cases follow the sun by default. You can request this for P1 issues.

## Reporting Specific Issues

### Networking Issues
- Provide packet flow diagrams (source to destination, intermediate hops like VPN, proxies, NAT).
- Identify endpoints by exact IP (external or RFC 1918 private) and context.
- Provide Path Analysis: Use MTR or tcptraceroute.
- Provide Packet Capture: `pcap` via tcpdump or Wireshark from both endpoints simultaneously.

### Google Cloud Console (UI) Issues
- URLs of impacted console pages.
- IDs of impacted projects.
- Impacted browsers and any extensions/firewalls in use.
- Include browser trace information (HAR file).

### Production Outages
- If an application stops serving traffic to users.
- Immediately create a case including time, product, identifiers, and location.
- Assign an "Incident Commander" from your team to manage communication and triage parallel hypotheses.