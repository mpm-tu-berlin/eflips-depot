from depot import DepotEvaluation
from test_eflips_whole_stack import TestDepotEvaluation


class TestApiDjangoSImbaOutput(TestDepotEvaluation):
    def test_output_format(self, depot_evaluation):
        assert isinstance(depot_evaluation, DepotEvaluation)
