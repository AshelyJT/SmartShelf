import csv
import datetime
import json
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from checkout.models import Product
from decimal import Decimal
from .models import DailyProductSales, PriceHistory
from .apps import ProjectsmartshelfConfig
from .ml_models.utils import prepare_input_for_tomorrow, predict_prices_batch
from .utils import send_price_update


def login_view(request):
    error_message = ""
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.is_staff:
                login(request, user)
                next_url = (
                    request.POST.get("next") or
                    request.GET.get("next") or
                    reverse(settings.LOGIN_REDIRECT_URL)
                )
                return redirect(next_url)
            else:
                error_message = "You do not have staff privileges!"
        else:
            error_message = "Invalid username or password. Please try again."
    return render(request, "staff_login.html", {"error": error_message})

def staff_logout_view(request):
    logout(request)
    return redirect(reverse('staff_login'))

@staff_member_required(login_url='staff_login')
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

@staff_member_required(login_url='staff_login')
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

@staff_member_required(login_url='staff_login')
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

@staff_member_required(login_url='staff_login')
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

@staff_member_required(login_url='staff_login')
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

@staff_member_required(login_url='staff_login')
def pricing_dashboard(request):
    # Get simulated date (if any) or use real today
    simulated_today = getattr(request, 'simulated_date', None)
    if simulated_today is None:
        simulated_today = timezone.now().date()

    # Run AI Batch Prediction using simulated date as base
    raw_results = predict_prices_batch(base_date=simulated_today)

    final_report = []
    for product, raw_ai_price in raw_results:
        current_price = product.price if product.price else Decimal('0.00')
        expiry_date = product.expiry_date
        ai_price = Decimal(str(raw_ai_price)).quantize(Decimal('0.01'))

        # Determine price trend
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

        # Calculate days until expiry (using simulated today)
        if product.expiry_date:
            days_until_expiry = (product.expiry_date - simulated_today).days
            if days_until_expiry < 0:
                days_until_expiry = 0  # or keep negative to show expired
        else:
            days_until_expiry = 30  # default as in prediction

        final_report.append({
            'product': product,
            'current_price': current_price,
            'current_date': simulated_today,
            'expiry_date': expiry_date,
            'ai_price': ai_price,
            'status': status,
            'icon': trend_icon,
            'css_class': css_class,
            'days_until_expiry': days_until_expiry,
        })

    # Handle "Apply All" button
    if request.method == "POST" and "apply_all" in request.POST:
        update_count = 0
        for item in final_report:
            product = item['product']
            new_price = item['ai_price']
            old_price = product.price
            if new_price != old_price:
                PriceHistory.objects.create(
                    product=product,
                    changed_by=request.user,
                    old_price=old_price,
                    new_price=new_price
                )
                product.price = new_price
                product.save()
                # MQTT push
                send_price_update(product.lcd_id, product.name, str(new_price))
                update_count += 1
        messages.success(request, f"Successfully updated prices for {update_count} products!")
        return redirect('pricing_dashboard')

    return render(request, 'pricing_dashboard.html', {'report_data': final_report, 'current_date': simulated_today,})

@staff_member_required(login_url='staff_login')
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

@staff_member_required(login_url='staff_login')
def reset_date(request):
    if request.method == 'POST':
        if 'simulated_date' in request.session:
            del request.session['simulated_date']
        return JsonResponse({'success': True})
    return JsonResponse({'success': False})

@staff_member_required(login_url='staff_login')
def apply_single_price(request):
    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        new_price = request.POST.get('new_price')
        try:
            product = Product.objects.get(id=product_id)
            new_price_decimal = Decimal(new_price).quantize(Decimal('0.01'))
            if product.price != new_price_decimal:
                # Log history
                PriceHistory.objects.create(
                    product=product,
                    changed_by=request.user,
                    old_price=product.price,
                    new_price=new_price_decimal
                )
                product.price = new_price_decimal
                product.save()
                # MQTT push to ESP32
                send_price_update(product.lcd_id, product.name, str(new_price_decimal))
                messages.success(request, f"Price updated for {product.name}.")
            else:
                messages.info(request, f"Price for {product.name} unchanged.")
        except Product.DoesNotExist:
            messages.error(request, "Product not found.")
        except Exception as e:
            messages.error(request, f"Error: {e}")
        return redirect('pricing_dashboard')
    return redirect('pricing_dashboard')

@staff_member_required(login_url='staff_login')
def recommendations_page(request):
    # Get simulated date (or real today)
    simulated_today = getattr(request, 'simulated_date', None)
    if simulated_today is None:
        simulated_today = timezone.now().date()

    # Run AI batch prediction (reuse existing function)
    raw_results = predict_prices_batch(base_date=simulated_today)

    recommendations = []
    for product, raw_ai_price in raw_results:
        current_price = product.price if product.price else Decimal('0.00')
        ai_price = Decimal(str(raw_ai_price)).quantize(Decimal('0.01'))
        expiry_date = product.expiry_date
        days_until_expiry = None
        if expiry_date:
            days_until_expiry = (expiry_date - simulated_today).days

        # Determine recommendation type
        rec_type = None
        reason = ""
        urgency = "low"  # low, medium, high

        # Price change recommendation
        price_diff_pct = 0
        if current_price > 0:
            price_diff_pct = ((ai_price - current_price) / current_price) * 100

        # Expiry-based recommendations
        if days_until_expiry is not None and days_until_expiry <= 7:
            if days_until_expiry <= 2:
                urgency = "high"
                rec_type = "clearance"
                reason = f"Expires in {days_until_expiry} days – urgent markdown"
            else:
                urgency = "medium"
                rec_type = "markdown"
                reason = f"Expires in {days_until_expiry} days – suggest discount"
        # Price increase if AI price > current by > 5%
        elif ai_price > current_price and price_diff_pct > 5:
            rec_type = "price_increase"
            reason = f"AI suggests +{price_diff_pct:.0f}% price increase"
            urgency = "medium"
        # Price decrease if AI price < current by > 5%
        elif ai_price < current_price and price_diff_pct < -5:
            rec_type = "price_decrease"
            reason = f"AI suggests {price_diff_pct:.0f}% price decrease"
            urgency = "medium"
        # Restock recommendation based on sales velocity (placeholder)
        # For now, simple: if product has no sales in last 3 days? Use dummy logic.
        # We'll add a simple check: if total units sold in recent summaries < 5, suggest restock.
        else:
            # Check sales volume from DailyProductSales (last 7 days)
            from .models import DailyProductSales
            recent_sales = DailyProductSales.objects.filter(
                product_name=product.name,
                date__gte=simulated_today - datetime.timedelta(days=7)
            ).aggregate(total=Sum('units_sold'))['total'] or 0
            if recent_sales < 5 and current_price > 0:
                rec_type = "restock"
                reason = f"Low sales velocity ({recent_sales} units in 7 days) – review stock"
                urgency = "low"
            else:
                continue  # no recommendation

        recommendations.append({
            'product': product,
            'current_price': current_price,
            'ai_price': ai_price,
            'days_until_expiry': days_until_expiry,
            'rec_type': rec_type,
            'reason': reason,
            'urgency': urgency,
        })

    # Sort by urgency: high -> medium -> low
    urgency_order = {'high': 0, 'medium': 1, 'low': 2}
    recommendations.sort(key=lambda x: urgency_order.get(x['urgency'], 3))

    context = {
        'recommendations': recommendations,
        'current_date': simulated_today,
    }
    return render(request, 'recommendations.html', context)


@staff_member_required(login_url='staff_login')
def price_history_view(request):
    history_list = PriceHistory.objects.select_related('product', 'changed_by').all()

    # Search query (text search on product name or username)
    search_query = request.GET.get('search', '')
    if search_query:
        history_list = history_list.filter(
            Q(product__name__icontains=search_query) |
            Q(changed_by__username__icontains=search_query)
        )

    # Advanced filters
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    product_id = request.GET.get('product_id')
    user_id = request.GET.get('user_id')

    if start_date:
        start_date_obj = parse_date(start_date)
        if start_date_obj:
            history_list = history_list.filter(changed_at__date__gte=start_date_obj)
    if end_date:
        end_date_obj = parse_date(end_date)
        if end_date_obj:
            history_list = history_list.filter(changed_at__date__lte=end_date_obj)
    if product_id:
        history_list = history_list.filter(product_id=product_id)
    if user_id:
        history_list = history_list.filter(changed_by_id=user_id)

    # Pagination
    paginator = Paginator(history_list, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Add price difference
    for history in page_obj:
        history.price_diff = history.new_price - history.old_price

    # Get lists for filter dropdowns
    products = Product.objects.all().order_by('name')
    staff_users = User.objects.filter(is_staff=True).order_by('username')

    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'start_date': start_date,
        'end_date': end_date,
        'selected_product': product_id,
        'selected_user': user_id,
        'products': products,
        'staff_users': staff_users,
    }
    return render(request, 'price_history.html', context)


@staff_member_required(login_url='staff_login')
def export_price_history_csv(request):
    # Apply same filters as the main view (duplicate logic)
    history_list = PriceHistory.objects.select_related('product', 'changed_by').all()

    search_query = request.GET.get('search', '')
    if search_query:
        history_list = history_list.filter(
            Q(product__name__icontains=search_query) |
            Q(changed_by__username__icontains=search_query)
        )

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    product_id = request.GET.get('product_id')
    user_id = request.GET.get('user_id')

    if start_date:
        start_date_obj = parse_date(start_date)
        if start_date_obj:
            history_list = history_list.filter(changed_at__date__gte=start_date_obj)
    if end_date:
        end_date_obj = parse_date(end_date)
        if end_date_obj:
            history_list = history_list.filter(changed_at__date__lte=end_date_obj)
    if product_id:
        history_list = history_list.filter(product_id=product_id)
    if user_id:
        history_list = history_list.filter(changed_by_id=user_id)

    # Create CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="price_history.csv"'
    writer = csv.writer(response)
    writer.writerow(['Product', 'Changed By', 'Date & Time', 'Old Price', 'New Price', 'Price Change'])

    for history in history_list.order_by('-changed_at'):
        diff = history.new_price - history.old_price
        writer.writerow([
            history.product.name,
            history.changed_by.get_full_name() if history.changed_by else 'System',
            history.changed_at.strftime('%Y-%m-%d %H:%M:%S'),
            f"${history.old_price:.2f}",
            f"${history.new_price:.2f}",
            f"{'+' if diff > 0 else ''}{diff:.2f}"
        ])

    return response


@staff_member_required(login_url='staff_login')
def manual_pricing_view(request):
    products = Product.objects.all().order_by('name')
    search_query = request.GET.get('search', '')
    category_filter = request.GET.get('category', '')

    if search_query:
        products = products.filter(name__icontains=search_query)
    if category_filter:
        products = products.filter(category=category_filter)

    # Get unique categories for filter dropdown
    categories = Product.objects.values_list('category', flat=True).distinct().order_by('category')

    # Handle single product price override (via POST from the table)
    if request.method == 'POST' and 'product_id' in request.POST:
        product_id = request.POST.get('product_id')
        try:
            product = Product.objects.get(id=product_id)
            old_price = product.price

            new_price_raw = request.POST.get('new_price', '').strip()
            discount_percent_raw = request.POST.get('discount_percent', '').strip()
            discount_fixed_raw = request.POST.get('discount_fixed', '').strip()

            new_price_decimal = None

            # Priority: new_price > discount_percent > discount_fixed
            if new_price_raw:
                try:
                    new_price_decimal = Decimal(new_price_raw).quantize(Decimal('0.01'))
                except:
                    messages.error(request, f"Invalid new price format: {new_price_raw}")
                    return redirect('manual_pricing')
            elif discount_percent_raw:
                try:
                    percent = Decimal(discount_percent_raw)
                    new_price_decimal = old_price * (1 - percent / 100)
                    new_price_decimal = new_price_decimal.quantize(Decimal('0.01'))
                except:
                    messages.error(request, f"Invalid discount percentage: {discount_percent_raw}")
                    return redirect('manual_pricing')
            elif discount_fixed_raw:
                try:
                    fixed = Decimal(discount_fixed_raw)
                    new_price_decimal = old_price - fixed
                    new_price_decimal = max(new_price_decimal, Decimal('0.00')).quantize(Decimal('0.01'))
                except:
                    messages.error(request, f"Invalid fixed discount amount: {discount_fixed_raw}")
                    return redirect('manual_pricing')
            else:
                messages.error(request, "No valid price change provided.")
                return redirect('manual_pricing')

            if old_price != new_price_decimal:
                PriceHistory.objects.create(
                    product=product,
                    changed_by=request.user,
                    old_price=old_price,
                    new_price=new_price_decimal
                )
                product.price = new_price_decimal
                product.save()
                send_price_update(product.lcd_id, product.name, str(new_price_decimal))
                messages.success(request, f"Price for {product.name} updated from ${old_price} to ${new_price_decimal}.")
            else:
                messages.info(request, f"Price for {product.name} unchanged.")

        except Product.DoesNotExist:
            messages.error(request, "Product not found.")
        except Exception as e:
            messages.error(request, f"Unexpected error: {e}")
        return redirect('manual_pricing')

    # Handle bulk discount by category
    if request.method == 'POST' and 'bulk_category' in request.POST:
        category = request.POST.get('bulk_category')
        discount_type = request.POST.get('discount_type')  # 'percentage' or 'fixed'
        discount_value = request.POST.get('discount_value')
        try:
            discount_value = Decimal(discount_value)
            products_in_cat = Product.objects.filter(category=category)
            updated_count = 0
            for prod in products_in_cat:
                old_price = prod.price
                if discount_type == 'percentage':
                    new_price = old_price * (1 - discount_value / 100)
                else:  # fixed amount
                    new_price = old_price - discount_value
                # Ensure price doesn't go below zero
                new_price = max(new_price, Decimal('0.00'))
                new_price = new_price.quantize(Decimal('0.01'))
                if old_price != new_price:
                    PriceHistory.objects.create(
                        product=prod,
                        changed_by=request.user,
                        old_price=old_price,
                        new_price=new_price
                    )
                    prod.price = new_price
                    prod.save()
                    send_price_update(prod.lcd_id, prod.name, str(new_price))
                    updated_count += 1
            messages.success(request, f"Bulk discount applied to {updated_count} products in '{category}' category.")
        except Exception as e:
            messages.error(request, f"Error applying bulk discount: {e}")
        return redirect('manual_pricing')

    context = {
        'products': products,
        'search_query': search_query,
        'category_filter': category_filter,
        'categories': categories,
    }
    return render(request, 'manual_pricing.html', context)

