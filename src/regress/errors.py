class RegressError(Exception):
    """An expected failure with a message meant for the user."""


class ProjectError(RegressError):
    """The target project is missing something Regress needs."""


class ToolError(RegressError):
    """An external tool (Vitest, Stryker) failed in a way we cannot recover from."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


class LLMError(RegressError):
    """The model call failed or returned something unusable."""


class CandidateRejected(RegressError):
    """The model could not produce a valid test file within the allowed attempts."""

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems
