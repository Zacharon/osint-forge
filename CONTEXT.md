# OSINT Forge domain language

**Mission supervisor**: The existing MissionSupervisor is the single owner of mission orchestration, governance gates, persistence and resumption.

**Mission status**: Operational progress of an OSINT mission. Completed and awaiting_review do not establish kernel approval.

**Kernel lifecycle state**: A review state in the shared execution contract. A normalized alias describes a recorded state; it does not authenticate a reviewer or grant authority.

**Ledger projection**: A read-only interpretation of validated ledger records and artifact bytes, retaining each original record and raw lifecycle alias.

**Plan readiness**: Metadata that a plan exists, separate from a review decision or authorization to execute.
