import datetime
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from checkout.models import LiveTransaction
from django.shortcuts import render, redirect
from django.contrib import messages
from checkout.models import Product
from decimal import Decimal
from .models import DailyProductSales
from .apps import ProjectsmartshelfConfig
from .ml_models.utils import prepare_input_for_tomorrow, predict_prices_batch

# Create your views here.
def home(request):
    return render(request, "home.html")

def analytics_dashboard(request):
    # Get simulated date from request, fallback to real today if None
    simulated_today = getattr(request, 'simulated_date', None)
    if simulated_today is None:
        simulated_today = timezone.now().date()
    
    yesterday = simulated_today - datetime.timedelta(days=1)
    
    recent_summaries = DailyProductSales.objects.all().order_by('-date')[:20]
    yesterday_top = DailyProductSales.objects.filter(date=yesterday).order_by('-units_sold')[:10]
    
    # Category data (unchanged)
    from django.db.models import Sum
    category_data = DailyProductSales.objects.filter(
        date=yesterday
    ).values('category').annotate(
        total_units=Sum('units_sold'),
        total_revenue=Sum('total_revenue')
    ).order_by('-total_units')
    
    context = {
        'today': simulated_today,
        'yesterday': yesterday,
        'recent_summaries': recent_summaries,
        'yesterday_top': yesterday_top,
        'category_data': category_data,
        'total_summaries': DailyProductSales.objects.count(),
    }
    return render(request, 'dashboard.html', context)

@csrf_exempt
def generate_summary_api(request):
    """API endpoint to generate daily summary"""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            target_date_str = data.get("date")

            if target_date_str:
                target_date = datetime.datetime.strptime(
                    target_date_str, "%Y-%m-%d"
                ).date()
            else:
                # Default to yesterday
                target_date = timezone.now().date() - datetime.timedelta(days=1)

            # Generate summary
            records = DailyProductSales.generate_daily_summary(target_date)

            return JsonResponse(
                {
                    "success": True,
                    "date": target_date_str or str(target_date),
                    "records_created": len(records),
                    "message": f"Generated {len(records)} daily summaries for {target_date}",
                }
            )

        except ValueError as e:
            return JsonResponse(
                {"success": False, "error": f"Invalid date format: {str(e)}"}
            )
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    return JsonResponse({"success": False, "error": "Invalid request method"})


def sales_report_api(request):
    """API endpoint to get sales data"""
    # Get date parameters
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    category = request.GET.get("category")
    product_name = request.GET.get("product_name")

    # Build queryset
    queryset = DailyProductSales.objects.all()

    # Apply filters
    if start_date_str:
        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        queryset = queryset.filter(date__gte=start_date)

    if end_date_str:
        end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
        queryset = queryset.filter(date__lte=end_date)

    if category:
        queryset = queryset.filter(category=category)

    if product_name:
        queryset = queryset.filter(product_name__icontains=product_name)

    # Get aggregated data
    from django.db.models import Sum, Count, Avg

    summary = queryset.aggregate(
        total_units=Sum("units_sold"),
        total_revenue=Sum("total_revenue"),
        avg_price=Avg("current_price"),
        total_days=Count("date", distinct=True),
        total_products=Count("product_name", distinct=True),
    )

    # Get daily breakdown
    daily_data = (
        queryset.values("date")
        .annotate(daily_units=Sum("units_sold"), daily_revenue=Sum("total_revenue"))
        .order_by("date")
    )

    # Get product breakdown
    product_data = (
        queryset.values("product_name", "category")
        .annotate(
            total_units=Sum("units_sold"),
            total_revenue=Sum("total_revenue"),
            avg_price=Avg("current_price"),
        )
        .order_by("-total_units")
    )

    return JsonResponse(
        {
            "success": True,
            "summary": summary,
            "daily_data": list(daily_data),
            "product_data": list(product_data),
            "record_count": queryset.count(),
        }
    )


def product_trend_api(request, product_id=None):
    """API endpoint to get product trend data"""
    from checkout.models import Product

    if product_id:
        try:
            product = Product.objects.get(id=product_id)
            product_name = product.name
        except Product.DoesNotExist:
            return JsonResponse({"success": False, "error": "Product not found"})
    else:
        product_name = request.GET.get("product_name")
        if not product_name:
            return JsonResponse(
                {"success": False, "error": "Product name or ID required"}
            )

    # Get sales data for this product
    sales_data = DailyProductSales.objects.filter(product_name=product_name).order_by(
        "date"
    )

    # Format for charting
    chart_data = {"labels": [], "units_sold": [], "revenue": [], "prices": []}

    for sale in sales_data:
        chart_data["labels"].append(str(sale.date))
        chart_data["units_sold"].append(float(sale.units_sold))
        chart_data["revenue"].append(float(sale.total_revenue))
        chart_data["prices"].append(float(sale.current_price))

    # Calculate statistics
    if sales_data.exists():
        total_units = sum(chart_data["units_sold"])
        total_revenue = sum(chart_data["revenue"])
        avg_daily_units = total_units / len(chart_data["units_sold"])

        return JsonResponse(
            {
                "success": True,
                "product_name": product_name,
                "chart_data": chart_data,
                "stats": {
                    "total_units": total_units,
                    "total_revenue": total_revenue,
                    "avg_daily_units": avg_daily_units,
                    "sales_days": len(chart_data["labels"]),
                    "current_price": float(sales_data.last().current_price),
                },
                "sales_data": list(
                    sales_data.values(
                        "date", "units_sold", "total_revenue", "current_price"
                    )
                ),
            }
        )
    else:
        return JsonResponse(
            {"success": False, "error": "No sales data found for this product"}
        )

def predict_tomorrow_price(request):
    predicted_price = None

    if request.method == "POST":
        # A. Get data from User (or Database)
        p_name = request.POST.get("product_name") # e.g. "Milk"
        p_cat = request.POST.get("category")      # e.g. "Dairy"
        inventory = float(request.POST.get("inventory"))
        expiry = request.POST.get("expiry_date")  # "2025-12-31"
       
        # B. Get Sales History (Crucial!)
        # In a real app, query your database: Sales.objects.filter(product=p_name)...
        # For this example, we ask the user:
        sales_today = float(request.POST.get("sales_today"))
        sales_yesterday = float(request.POST.get("sales_yesterday"))

        # C. Prepare Data
        input_df = prepare_input_for_tomorrow(
            p_name, p_cat, inventory, expiry, sales_today, sales_yesterday
        )

        # D. Predict
        model = ProjectsmartshelfConfig.model
        if model:
            # xgboost usually returns a numpy array like [12.50]
            result = model.predict(input_df)
            predicted_price = round(result[0], 2)

    return render(request, "predict_price.html", {"price": predicted_price})


def pricing_dashboard(request):
    # Get simulated date from request (if available), otherwise use real today
    simulated_today = getattr(request, 'simulated_date', None)
    if simulated_today is None:
        simulated_today = timezone.now().date()

    # 1. Run the AI Batch Prediction using the simulated date as base
    raw_results = predict_prices_batch(base_date=simulated_today)

    # 2. Process Data for Display
    final_report = []
    for product, raw_ai_price in raw_results:
        current_price = product.price if product.price else Decimal('0.00')
        ai_price = Decimal(str(raw_ai_price)).quantize(Decimal('0.01'))

        if ai_price > current_price:
            status = "Increase"
            trend_icon = "⬆"
            css_class = "trend-up"
        elif ai_price < current_price:
            status = "Decrease"
            trend_icon = "⬇"
            css_class = "trend-down"
        else:
            status = "Keep"
            trend_icon = "➡"
            css_class = "trend-flat"

        final_report.append({
            'product': product,
            'current_price': current_price,
            'ai_price': ai_price,
            'status': status,
            'icon': trend_icon,
            'css_class': css_class
        })

    # 3. Handle "Apply All" Button Click
    if request.method == "POST" and "apply_all" in request.POST:
        update_count = 0
        for item in final_report:
            product = item['product']
            new_price = item['ai_price']
            if new_price != product.price:
                product.price = new_price
                product.save()
                update_count += 1
        messages.success(request, f"Successfully updated prices for {update_count} products!")
        return redirect('pricing_dashboard')

    return render(request, 'pricing_dashboard.html', {'report_data': final_report})

@csrf_exempt
def close_day_advance(request):
    if request.method == 'POST':
        try:
            # Get the current simulated date from session
            simulated_date_str = request.session.get('simulated_date')
            if simulated_date_str:
                current_date = datetime.datetime.strptime(simulated_date_str, '%Y-%m-%d').date()
            else:
                # No simulated date set yet – use the latest transaction date or today
                from checkout.models import LiveTransaction
                latest_trans = LiveTransaction.objects.order_by('-date').first()
                if latest_trans:
                    current_date = latest_trans.date
                else:
                    current_date = timezone.now().date()
                # Set the simulated date to this starting point
                request.session['simulated_date'] = current_date.strftime('%Y-%m-%d')

            print(f"DEBUG: Closing day for {current_date}")

            # Generate summary for that date (will include zeros for unsold products)
            records = DailyProductSales.generate_daily_summary(current_date)

            # Advance to the next day
            next_date = current_date + datetime.timedelta(days=1)
            request.session['simulated_date'] = next_date.strftime('%Y-%m-%d')
            print(f"DEBUG: Advanced to {next_date}")

            return JsonResponse({
                'success': True,
                'closed_date': str(current_date),
                'next_date': str(next_date),
                'records_created': len(records),
                'message': f'Closed {current_date}, now at {next_date}.'
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid method'})

@csrf_exempt
def reset_date(request):
    if request.method == 'POST':
        if 'simulated_date' in request.session:
            del request.session['simulated_date']
        return JsonResponse({'success': True})
    return JsonResponse({'success': False})