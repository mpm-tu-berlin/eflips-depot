from depot import DepotEvaluation
from depot.api.django_simba.output import to_simba, InputForSimba
from test_eflips_whole_stack import TestDepotEvaluation


class TestApiDjangoSImbaOutput(TestDepotEvaluation):
    def test_output_format(self, depot_evaluation):
        output_for_simba = to_simba(depot_evaluation)
        assert isinstance(output_for_simba, list)
        for item in output_for_simba:
            assert isinstance(item, InputForSimba)
