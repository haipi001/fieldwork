# Frontend Routes / Components

## Routes

```text
/new
/runs/:runId
/findings
/findings/:findingId
/reports
/reports/:findingId
/settings
```

## Core components

- `ModeSwitch`
- `TargetInput`
- `ScopeCard`
- `StartAnalysisButton`
- `RunTimeline`
- `RunStats`
- `AdvancedActivityDrawer`
- `FindingCard`
- `VerificationBadge`
- `EvidenceDrawer`
- `ReportPlatformPicker`
- `CompletenessChecklist`
- `ExportButton`
- `ToolHealthPanel`

## 设计原则

- 默认中文 UI。
- 技术术语二级显示。
- 不要求用户看命令行。
- 每个状态有“系统正在做什么 / 为什么”。
- 只在必要时弹人工确认。
- VERIFIED 和 Candidate 视觉严格分开。
