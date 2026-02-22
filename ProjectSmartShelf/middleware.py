import datetime
from django.utils import timezone

class SimulatedDateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        simulated_date_str = request.session.get('simulated_date')
        if simulated_date_str:
            try:
                simulated_date = datetime.datetime.strptime(simulated_date_str, '%Y-%m-%d').date()
                request.simulated_date = simulated_date
                print(f"DEBUG: Set request.simulated_date = {simulated_date}")
            except ValueError:
                # Invalid date – remove it
                del request.session['simulated_date']
                request.simulated_date = None
                print("DEBUG: Invalid date removed")
        else:
            request.simulated_date = None
            print("DEBUG: No simulated date in session")

        response = self.get_response(request)
        return response