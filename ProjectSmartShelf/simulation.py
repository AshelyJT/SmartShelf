import datetime

from django.contrib.sessions.models import Session
from django.utils import timezone

from .models import SimulationState


ACTIVE_DAY_KEY = 'active_day'


def parse_simulated_date(value):
    if not value:
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def get_session_simulated_date(request):
    return parse_simulated_date(request.session.get('simulated_date'))


def set_active_simulated_date(request, active_date):
    active_date = parse_simulated_date(active_date)
    if not active_date:
        return None

    if request is not None:
        request.session['simulated_date'] = active_date.strftime('%Y-%m-%d')
        request.simulated_date = active_date

    SimulationState.objects.update_or_create(
        key=ACTIVE_DAY_KEY,
        defaults={'current_date': active_date},
    )
    return active_date


def clear_active_simulated_date(request=None):
    if request is not None:
        request.session.pop('simulated_date', None)
        request.simulated_date = None

    SimulationState.objects.filter(key=ACTIVE_DAY_KEY).delete()


def get_stored_simulated_date():
    state = SimulationState.objects.filter(key=ACTIVE_DAY_KEY).first()
    return state.current_date if state else None


def get_session_table_simulated_date():
    sessions = Session.objects.filter(expire_date__gte=timezone.now()).order_by('-expire_date')
    for session in sessions:
        active_date = parse_simulated_date(session.get_decoded().get('simulated_date'))
        if active_date:
            return active_date
    return None


def get_active_simulated_date(request=None):
    if request is not None:
        session_date = get_session_simulated_date(request)
        if session_date:
            set_active_simulated_date(request, session_date)
            return session_date

    stored_date = get_stored_simulated_date()
    if stored_date:
        if request is not None:
            request.simulated_date = stored_date
        return stored_date

    session_table_date = get_session_table_simulated_date()
    if session_table_date:
        set_active_simulated_date(request, session_table_date)
        return session_table_date

    if request is not None:
        request.simulated_date = None
    return None


def get_active_day(request=None):
    return get_active_simulated_date(request) or timezone.now().date()
