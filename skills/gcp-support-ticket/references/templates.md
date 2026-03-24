# GCP Support Ticket Templates

Use these templates as a baseline for formatting GCP support tickets. Fill out all relevant variables before submission.

## Standard Issue Template

```markdown
**Time**: [YYYY-MM-DDTHH:MM:SS+00:00 to YYYY-MM-DDTHH:MM:SS+00:00 or "Ongoing since YYYY-MM-DDTHH:MM:SS+00:00"]
**Product/Feature**: [e.g., Compute Engine REST API, Cloud Storage Transfer API]
**Method/Mechanism**: [e.g., REST API, gcloud CLI, Cloud Console URL]
**Location**: [Region and Zone, e.g., us-east1-b]
**Identifiers**:
- Project ID(s): [Alphanumeric Project ID]
- Other IDs (Instance/Job/IP): [e.g., IP 218.239.8.9 connecting from corporate gateway 56.56.56.56]
**Problem Type**: [Intermittent | Transient | Consistent]

**Issue Summary**:
[Detailed description: "I started an operation... Expected outcome vs Actual outcome..."]

**Reproduction Steps (if Consistent)**:
1. 
2. 

**Artifacts Attached**: [e.g., Screenshot of UI, pcap file, HAR trace, stack trace snippets]
```

## Long-Running or Difficult Issue Template

For issues that persist or involve multiple hypotheses, keep a living summary document.

```markdown
**Current State Summary**:
[High-level summary of where the issue stands today. What is broken, what is working.]

**Hypotheses**:
- Hypothesis 1: [Description]
  - Test/Tool: [How we intend to test this]
  - Status: [Pending / Disproven / Confirmed]
- Hypothesis 2: [Description]
  - Test/Tool: [How we intend to test this]
  - Status: [Pending / Disproven / Confirmed]

**Relevant Cases / Internal Bugs**:
- Case [Number]
- Bug [Number]
```

## High Priority (P1) Production Outage Template

```markdown
**Time**: [YYYY-MM-DDTHH:MM:SS+00:00]
**Product**: [Product Name]
**Location**: [Region and Zone]
**Identifiers**: [Project ID, App ID]

**Business Impact**:
[Detail the critical impact: "Production application X is completely unusable by our end users, causing a significant rate of user-facing errors..."]

**P1 Justification**:
[Brief description of why this is P1, e.g., "This issue blocks a critical security fix" or "Revenue is actively being lost as checkout is offline."]

**Incident Commander Contact**:
[Name, Email, Phone Number]

**Conference Bridge (if Meet is not possible)**:
[Link to Zoom/Teams/Webex]
```