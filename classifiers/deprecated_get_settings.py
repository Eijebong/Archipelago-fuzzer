from fuzz import GenOutcome

class FuzzException(Exception):
    pass

def raise_fuzz_exception(*args, **kwargs):
    raise FuzzException("Caught usage of deprecated Utils.get_settings")


class Classifier:
    def setup(self, _args):
        import Utils
        Utils.get_settings = raise_fuzz_exception

    def classify(self, outcome, exception):
        if isinstance(exception, FuzzException):
            return GenOutcome.Failure
        return GenOutcome.Success
