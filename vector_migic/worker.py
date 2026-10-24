"""A separate owned process keeps tracing cancellable and the UI responsive."""
def run_engine(source, destination, options, messages):
    try:
        try:
            from . import engine
        except ImportError:
            import engine
        def progress(*args):
            if len(args) == 1 and isinstance(args[0], dict):
                value = args[0]
            else:
                value = {'progress': args[0], 'message': args[1] if len(args) > 1 else ''}
            messages.put({'kind': 'progress', **value})
        value = engine.vectorize(source, destination, options, progress)
        messages.put({'kind': 'done', 'metrics': value})
    except Exception as exc:
        messages.put({'kind': 'error', 'message': str(exc)[:1200]})
