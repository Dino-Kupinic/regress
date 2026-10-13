"""A shared, estimate-based allowance for one batch or evaluation."""

from dataclasses import dataclass
from math import isfinite

from regress.errors import ProjectError, TotalBudgetReached
from regress.pricing import format_usd


@dataclass
class TotalBudget:
    limit: float
    spent: float = 0.0
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.limit) or not 0 < self.limit <= 10_000:
            raise ProjectError("max_total_cost must be finite, above 0 and at most 10000 USD.")

    def check(self, upcoming: float) -> None:
        if self.stop_reason is None and self.spent + upcoming > self.limit:
            self.stop_reason = (
                f"The next model call would take the operation past its total budget of {format_usd(self.limit)} "
                f"({format_usd(self.spent)} spent, about {format_usd(upcoming)} more)."
            )
        if self.stop_reason:
            raise TotalBudgetReached(self.stop_reason)

    def exhausted(self) -> bool:
        if self.stop_reason is None and self.spent >= self.limit:
            self.stop_reason = (
                f"The total budget of {format_usd(self.limit)} has been spent ({format_usd(self.spent)})."
            )
        return self.stop_reason is not None
