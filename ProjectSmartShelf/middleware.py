from .simulation import get_active_simulated_date, get_session_simulated_date


class SimulatedDateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.session.get('simulated_date') and not get_session_simulated_date(request):
            del request.session['simulated_date']

        request.simulated_date = get_active_simulated_date(request)
        return self.get_response(request)
