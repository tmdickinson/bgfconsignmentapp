from django.contrib.auth import logout
from django.shortcuts import redirect


class SellerAccessMiddleware:
    """Ends seller sessions once their logins stop, 30 days after Post-sale."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and user.seller and user.seller.login_expired:
            logout(request)
            return redirect("accounts:login")
        return self.get_response(request)
