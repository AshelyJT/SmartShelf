# context_processors.py
def simulated_date(request):
    """Add simulated_date to template context if it exists."""
    return {
        'simulated_date': getattr(request, 'simulated_date', None)
    }