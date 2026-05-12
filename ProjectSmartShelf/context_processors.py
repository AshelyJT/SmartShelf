# context_processors.py
from django.utils import timezone


def simulated_date(request):
    """Add shared sidebar/date context to templates."""
    active_date = getattr(request, 'simulated_date', None)
    context = {
        'simulated_date': active_date,
        'recommendations_count': 0,
    }

    cached_count = getattr(request, '_recommendations_count', None)
    if cached_count is not None:
        context['recommendations_count'] = cached_count
        return context

    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and user.is_staff):
        return context

    try:
        from .recommendations import recommendation_count
        context['recommendations_count'] = recommendation_count(active_date or timezone.localdate())
    except Exception:
        context['recommendations_count'] = 0

    return context
