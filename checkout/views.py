from django.shortcuts import render
import datetime

# Create your views here.
def home(request):
    return render(request, "landing_page.html")


from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from .models import Product, LiveTransaction

def checkout(request):
    """Main till interface - shows 5 products"""
    # Get 5 products to display (or all if less than 5 exist)
    products = Product.objects.all()[:5]
   
    # Get recent transactions for display
    recent_transactions = LiveTransaction.objects.all().order_by('-created_at')[:10]
   
    return render(request, 'checkout.html', {
        'products': products,
        'recent_transactions': recent_transactions,
    })


@csrf_exempt
def process_transaction(request):
    """
    API endpoint to process a transaction
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            product_id = data.get('product_id')
            quantity = int(data.get('quantity', 1))
            product = Product.objects.get(id=product_id)

            # Get simulated date from request (set by middleware)
            simulated_date = getattr(request, 'simulated_date', None)
            if simulated_date is None:
                transaction_date = datetime.date.today()
                print(f"DEBUG [process_transaction]: No simulated date, using real date: {transaction_date}")
            else:
                transaction_date = simulated_date
                print(f"DEBUG [process_transaction]: Using simulated date: {transaction_date}")

            transaction = LiveTransaction.objects.create(
                product=product,
                quantity=quantity,
                date=transaction_date  # force this date
            )
            print(f"DEBUG [process_transaction]: Transaction created with date {transaction.date}")

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
            print(f"DEBUG [process_transaction] ERROR: {e}")
            return JsonResponse({'success': False, 'error': str(e)})

    return JsonResponse({'success': False, 'error': 'Invalid request method'})

def get_products(request):
    """Get the 5 products for display"""
    products = Product.objects.all()[:5]
   
    product_list = []
    for product in products:
        product_list.append({
            'id': product.id,
            'name': product.name,
            'category': product.category,
            'price': float(product.price),
        })
   
    return JsonResponse({
        'success': True,
        'products': product_list
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

