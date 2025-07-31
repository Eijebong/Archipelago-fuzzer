import os
import yappi
from fuzz import BaseHook, OUT_DIR

class Hook(BaseHook):
    def before_generate(self, _args):
        yappi.start(builtins=True, profile_threads=True)

    def after_generate(self, _mw, _output_dir):
        yappi.stop()
        yappi.get_func_stats().save(os.path.join(OUT_DIR, 'profile', f'profile_{os.getpid()}.prof'))

    def setup_main(self, args):
        os.makedirs(os.path.join(OUT_DIR, 'profile'))

    def finalize(self):
        aggregated_stats = yappi.YFuncStats([os.path.join(OUT_DIR, 'profile', f) for f in os.listdir(os.path.join(OUT_DIR, 'profile')) if f.startswith('profile_')])
        aggregated_stats.strip_dirs()
        aggregated_stats.save(os.path.join(OUT_DIR, 'full.prof'), "callgrind")
