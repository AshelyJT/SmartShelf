from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, login, logout
from django.urls import reverse_lazy, reverse
from django.conf import settings
import json
from ProjectSmartShelf.simulation import get_active_day, get_active_simulated_date
from .models import Product, LiveTransaction

# Create your views here.
def get_checkout_date(request):
    simulated_date = get_active_simulated_date(request)
    current_date = simulated_date if simulated_date else get_active_day(request)
    return current_date, simulated_date is not None

def format_checkout_date(current_date):
    return f"{current_date.strftime('%A, %B')} {current_date.day}, {current_date.year}"

def get_checkout_date_payload(request):
    current_date, is_simulated_date = get_checkout_date(request)
    return {
        'current_date': current_date.strftime('%Y-%m-%d'),
        'current_date_label': format_checkout_date(current_date),
        'is_simulated_date': is_simulated_date,
        'date_source': 'Simulated active day' if is_simulated_date else 'Dashboard active day',
    }

def home(request):
    return render(request, "landing_page.html")

def checkout_login_view(request):
    error_message = ""
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.is_staff==False:
                login(request, user)
                next_url = (
                    request.POST.get("next") or
                    request.GET.get("next") or
                    reverse(settings.CHECKOUT_LOGIN_REDIRECT_URL)
                )
                return redirect(next_url)
            else:
                error_message = "You do not have checkout privileges!"
        else:
            error_message = "Invalid username or password. Please try again."
    return render(request, "checkout_login.html", {"error": error_message})

def checkout_logout_view(request):
    logout(request)
    return redirect(reverse('checkout_login'))

def checkout(request):
    """Main till interface."""
    products = Product.objects.all()
    date_payload = get_checkout_date_payload(request)
   
    # Get recent transactions for display
    recent_transactions = LiveTransaction.objects.all().order_by('-created_at')[:10]
   
    return render(request, 'checkout.html', {
        'products': products,
        'recent_transactions': recent_transactions,
        'current_checkout_date': date_payload['current_date'],
        'current_checkout_date_label': date_payload['current_date_label'],
        'is_simulated_date': date_payload['is_simulated_date'],
        'checkout_date_source': date_payload['date_source'],
    })

@csrf_exempt
def process_transaction(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            product_id = data.get('product_id')
            quantity = int(data.get('quantity', 1))
            product = Product.objects.get(id=product_id)

            # Check inventory
            if product.inventory_level < quantity:
                return JsonResponse({
                    'success': False,
                    'error': f'Insufficient inventory. Available: {product.inventory_level}'
                })

            # Use the same active day shown on the dashboard.
            transaction_date, _ = get_checkout_date(request)

            # Create transaction
            transaction = LiveTransaction.objects.create(
                product=product,
                quantity=quantity,
                date=transaction_date
            )

            # Deduct inventory
            product.inventory_level -= quantity
            product.save()

            return JsonResponse({
                'success': True,
                'transaction': {
                    'id': transaction.transaction_id,
                    'product': product.name,
                    'quantity': quantity,
                    'unit_price': float(product.price),
                    'total': float(transaction.total_amount),
                    'category': product.category,
                    'day_of_week': transaction.day_of_week,
                    'time': transaction.time.strftime('%H:%M:%S'),
                    'date': transaction.date.strftime('%Y-%m-%d'),
                }
            })

        except Product.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Product not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    return JsonResponse({'success': False, 'error': 'Invalid request method'})


def get_products(request):
    products = Product.objects.all()
    product_list = []
    for product in products:
        product_list.append({
            'id': product.id,
            'name': product.name,
            'category': product.category,
            'price': float(product.price),
            'inventory': product.inventory_level,  # send inventory level
        })
    return JsonResponse({
        'success': True,
        'products': product_list,
        'date': get_checkout_date_payload(request),
    })

def recent_transactions(request):
    """Get recent transactions for display"""
    transactions = LiveTransaction.objects.all().order_by('-created_at')[:10]
   
    transaction_list = []
    for trans in transactions:
        transaction_list.append({
            'product': trans.product.name,
            'quantity': trans.quantity,
            'unit_price': float(trans.unit_price),
            'total': float(trans.total_amount),
            'time': trans.time.strftime('%H:%M'),
            'date': trans.date.strftime('%Y-%m-%d'),
        })
   
    return JsonResponse({
        'success': True,
        'transactions': transaction_list
    })

