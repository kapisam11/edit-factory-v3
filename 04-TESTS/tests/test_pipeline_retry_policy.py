from ai_video_factory.pipeline import Pipeline, PipelineContext, PipelineStage


class FlakyStage(PipelineStage):
    name = "flaky"
    max_retries = 2

    def __init__(self):
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        if self.calls < 2:
            raise TimeoutError("temporary")
        return ctx


class DeterministicFailureStage(PipelineStage):
    name = "deterministic"
    max_retries = 2

    def __init__(self):
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        raise ValueError("bad input")


def test_transient_errors_are_retried():
    stage = FlakyStage()
    Pipeline([stage], verbose=False).run(PipelineContext("test"))
    assert stage.calls == 2


def test_deterministic_errors_are_not_retried():
    stage = DeterministicFailureStage()
    try:
        Pipeline([stage], verbose=False).run(PipelineContext("test"))
    except ValueError:
        pass
    assert stage.calls == 1
