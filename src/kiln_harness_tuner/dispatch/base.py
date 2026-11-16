"""The AgentDispatcher protocol.

One typed method per agent — no `agent:` string, no `payload: dict`, no switch
statement re-deriving what the type system already knew. The reference
implementation is ClaudeCliDispatcher; another host implements the same
protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from kiln_harness_tuner.schemas import (
    AgentResult,
    AnalysisInput,
    BaselineReviewInput,
    BaselineReviewResult,
    BatchPlan,
    BatchPlanInput,
    DescribeEvalInput,
    EvalBuildInput,
    EvalBuildReport,
    EvalDescription,
    ExperimentResult,
    GeneralityReviewInput,
    GeneralityReviewResult,
    IntegrityReviewInput,
    IntegrityReviewResult,
    ProbeInput,
    ProbeResult,
    PromotionDecision,
    PromotionInput,
    RcBuildInput,
    RcBuildResult,
    ReportInput,
    ReportMarkdown,
    Usage,
)


@runtime_checkable
class AgentDispatcher(Protocol):
    async def preflight(self) -> Usage:
        """Once per run, before any paid work: prove the agents' tool lane
        (the loop gateway's `ping` operation through its MCP server).
        Raises on failure — a broken lane must never start a run."""
        ...

    async def batch_plan(self, req: BatchPlanInput) -> AgentResult[BatchPlan]: ...

    async def describe_eval(
        self, req: DescribeEvalInput
    ) -> AgentResult[EvalDescription]:
        """One eval's criterion in prose a planner can act
        on. Dispatched only for new and drifted evals — a one-time cost per
        eval, never paid again on a warm run."""
        ...

    async def eval_build(self, req: EvalBuildInput) -> AgentResult[EvalBuildReport]: ...

    async def create_experiment_rc(
        self, req: RcBuildInput
    ) -> AgentResult[RcBuildResult]: ...

    async def analyze_experiment_results(
        self, req: AnalysisInput
    ) -> AgentResult[ExperimentResult]: ...

    async def promote_best(
        self, req: PromotionInput
    ) -> AgentResult[PromotionDecision]: ...

    async def probe(self, req: ProbeInput) -> AgentResult[ProbeResult]:
        """One yes/no question about a mechanism, answered by a
        short session that forks an ephemeral run config and reads a few
        train traces — never a judge, never a measurement."""
        ...

    async def report_writer(self, req: ReportInput) -> AgentResult[ReportMarkdown]: ...

    async def integrity_review(
        self, req: IntegrityReviewInput
    ) -> AgentResult[IntegrityReviewResult]:
        """At the promotion gate, a read-only session with no
        tools reviews a candidate's changed instructions against the evals'
        criteria for paraphrased copying or eval-specific gaming."""
        ...

    async def generality_review(
        self, req: GeneralityReviewInput
    ) -> AgentResult[GeneralityReviewResult]:
        """Before the train measurement, a session with no tools reads a
        built run config's instructions beside its parent's and names the
        added text that memorises the train set or games the measurement
        (`kiln_harness_tuner.generality`)."""
        ...

    async def baseline_review(
        self, req: BaselineReviewInput
    ) -> AgentResult[BaselineReviewResult]:
        """Once for each baseline, before the planner plans against it, a
        read-only session reads the smell documents, the baseline's harness
        and a sample of its train traces, and names the smells it finds
        (`smells.run.ensure_baseline_reviewed`). Code writes the issues."""
        ...

    # The agent returns markdown; report.py (code) writes the file.
