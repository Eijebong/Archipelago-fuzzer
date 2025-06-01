from fuzz import GenOutcome, BaseHook

class FuzzException(Exception):
    pass

def raise_fuzz_exception(*args, **kwargs):
    raise FuzzException("Caught usage of deprecated Utils.get_settings")


class Hook(BaseHook):
    def setup_worker(self, _args):
        import Utils
        Utils.get_settings = raise_fuzz_exception

    def reclassify_outcome(self, outcome, exception):
        if isinstance(exception, FuzzException):
            return GenOutcome.Failure
        return GenOutcome.Success
