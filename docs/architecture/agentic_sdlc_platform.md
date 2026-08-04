flowchart TB

    USER[Product Owner / Engineer]

    subgraph INTERACTION["1. Interaction Layer"]
        CLI[CLI]
        UI[Execution Dashboard]
        API[FastAPI Control API]
    end

    subgraph CONTROL["2. Governance and Control Layer"]
        APPROVAL[Human Approval Gateway]
        POLICY[Policy and Guardrail Engine]
        AUDIT[Audit and Decision Log]
        RBAC[Identity and Access Control]
    end

    subgraph ORCHESTRATION["3. Agentic Orchestration Layer"]
        ORCH[LangGraph SDLC Orchestrator]
        STATE[Shared Agent State]
        DAG[Dependency Graph]
        CHECKPOINT[Checkpoint Store]
        REPLAN[Dynamic Re-planner]
        RETRY[Retry / Rollback / Safe Stop]
    end

    subgraph AGENTS["4. Specialized Engineering Agents"]
        REQ[Requirement Agent]
        PLAN[Planning Agent]
        ARCH[Architecture Agent]
        CODE[Coding Agent]
        TEST[Testing Agent]
        SEC[Security Agent]
        DOC[Documentation Agent]
        RELEASE[Release Readiness Agent]
    end

    subgraph CODEGEN["5. Code Generation Engine"]
        CODEPLAN[Code Generation Plan]
        REGISTRY[Prompt Registry]
        PROMPTS[Versioned Prompt Library]
        GENERATOR[Generic Code Generator]
        DEP[Dependency Resolver]
        VALIDATOR[Deterministic Code Validator]
        BUILDER[Safe Project Builder]
        IMPACT[Brownfield Impact Analyzer]
    end

    subgraph AI["6. AI Model Layer"]
        CLIENT[LLM Abstraction]
        LLM[ChatGPT / OpenAI Model]
    end

    subgraph MEMORY["7. Memory and Persistence"]
        EXECSTORE[Execution State Store]
        ARTIFACTSTORE[Artifact Store]
        DECISIONSTORE[Decision Lineage]
        CONTEXT[Project Context Store]
    end

    subgraph OUTPUT["8. Engineering Output"]
        PROJECT[Generated URL Shortener]
        SOURCE[Source Code]
        TESTS[Unit and Integration Tests]
        DOCS[README / ADR / API Documentation]
        DEPLOY[Docker / CI-CD / Deployment Files]
        REPORT[Engineering Summary and Risk Report]
    end

    subgraph OBS["9. Observability Layer"]
        METRICS[Execution Metrics]
        TRACES[Agent and LLM Traces]
        LOGS[Structured Logs]
        DASH[Operational Dashboard]
    end

    USER --> INTERACTION
    INTERACTION --> CONTROL
    CONTROL --> ORCH

    ORCH --> STATE
    ORCH --> DAG
    ORCH --> CHECKPOINT
    ORCH --> REPLAN
    ORCH --> RETRY

    ORCH --> REQ
    REQ --> PLAN
    PLAN --> ARCH
    ARCH --> APPROVAL
    APPROVAL --> CODE
    CODE --> TEST
    TEST --> SEC
    SEC --> DOC
    DOC --> RELEASE
    RELEASE --> APPROVAL

    CODE --> CODEPLAN
    CODEPLAN --> DEP
    DEP --> GENERATOR
    REGISTRY --> GENERATOR
    PROMPTS --> REGISTRY
    IMPACT --> CODEPLAN
    GENERATOR --> VALIDATOR
    VALIDATOR --> BUILDER

    GENERATOR --> CLIENT
    CLIENT --> LLM

    STATE --> EXECSTORE
    ORCH --> DECISIONSTORE
    BUILDER --> ARTIFACTSTORE
    IMPACT --> CONTEXT

    BUILDER --> PROJECT
    PROJECT --> SOURCE
    PROJECT --> TESTS
    PROJECT --> DOCS
    PROJECT --> DEPLOY
    RELEASE --> REPORT

    ORCH --> METRICS
    ORCH --> TRACES
    ORCH --> LOGS
    METRICS --> DASH
    TRACES --> DASH
    LOGS --> DASH